"""Offline copy conversion: synthetic files, real application factories."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


def wal_source(root):
    root.mkdir()
    # A crashed *synthetic* writer leaves committed frames in WAL; no connection
    # remains open, exactly the stopped-source case the converter must retain.
    script = '''
import os, sqlite3, sys
from pathlib import Path
from sqlalchemy import create_engine
from autoposter.db import Base
import autoposter.models
from app.db import SCHEMA
root=Path(sys.argv[1])
engine=create_engine('sqlite:///'+str(root/'post.db'))
Base.metadata.create_all(engine)
engine.dispose()
p=sqlite3.connect(root/'post.db');p.execute('PRAGMA journal_mode=WAL');p.execute('PRAGMA wal_autocheckpoint=0')
p.execute('INSERT INTO app_setting(key,value) VALUES(?,?)',('integrations','{"unknown_future":{"keep":true}}'));p.commit()
c=sqlite3.connect(root/'bot.db');c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA wal_autocheckpoint=0');c.executescript(SCHEMA)
c.execute('INSERT INTO settings VALUES(?,?)',('git_branch',json_dumps('wal-only')))
c.execute('INSERT INTO settings VALUES(?,?)',('__technical_cleared__:github_token','true'))
c.execute('INSERT INTO settings VALUES(?,?)',('github_token','""'))
c.execute('INSERT INTO settings VALUES(?,?)',('unknown_chat','{"keep":true}'));c.commit()
os._exit(0)
'''.replace("json_dumps('wal-only')", "'\"wal-only\"'")
    done = subprocess.run([sys.executable, '-c', script, str(root)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    (root / 'preserved-key.txt').write_bytes(b'synthetic-key-material\n')
    assert (root / 'bot.db-wal').stat().st_size > 0
    assert (root / 'post.db-wal').stat().st_size > 0
    return root


def hashes(root):
    import hashlib
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.mark.asyncio
async def test_default_wal_copy_then_normal_factories_mixed_commit_rollback(tmp_path, monkeypatch):
    from tools import sqlite_layout
    from app import db as chat
    from autoposter import db as post
    from autoposter.services import appconfig
    from autoposter.workspaces import control_context
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(control_context())
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    source = wal_source(tmp_path / 'input')
    before = hashes(source)
    output = tmp_path / 'output'
    receipt = sqlite_layout.convert_copy(source, output, autopost_db='post.db', autochat_db='bot.db', sources_stopped=True)
    assert hashes(source) == before
    assert (output / 'preserved-key.txt').read_bytes() == b'synthetic-key-material\n'
    assert sqlite_layout.convert_copy(source, output, autopost_db='post.db', autochat_db='bot.db', sources_stopped=True) == receipt
    monkeypatch.setattr(chat, '_conn', None)
    monkeypatch.setattr(chat, 'DATA_DIR', str(output))
    monkeypatch.setattr(chat, 'DB_PATH', str(output / 'bot.db'))
    monkeypatch.setattr(post.settings, 'database_url', 'sqlite+aiosqlite:///' + str(output / 'post.db'))
    engine = post.make_engine()
    monkeypatch.setattr(post, 'engine', engine)
    try:
        from autoposter.models import Character
        from autoposter.workspaces import character_context
        async with post.SessionLocal() as session:
            owner = Character(name='Synthetic layout owner')
            session.add(owner)
            await session.commit()
        assert chat.get_setting('unknown_chat') == {'keep': True}
        stack.enter_context(character_context(owner.id))
        assert chat.get_setting('git_branch') == 'wal-only'
        async with post.SessionLocal() as session:
            await appconfig.set_many(session, {'git_branch': 'committed', 'auto_plan_days': 38,
                                               'openrouter_api_key': 'synthetic-secret'})
            await session.commit()
        async with post.SessionLocal() as session:
            await appconfig.set_many(session, {'git_branch': 'rolled-back', 'auto_plan_days': 99})
            await appconfig.clear_secret(session, 'openrouter_api_key')
            assert chat.get_setting('git_branch') == 'committed'
            assert chat.get_setting('openrouter_api_key') == 'synthetic-secret'
            await session.rollback()
        await engine.dispose()
        chat._conn.close();chat._conn = None
        async with post.SessionLocal() as session:
            values = await appconfig.get_all(session, use_cache=False)
            assert values['git_branch'] == 'committed'
            assert values['auto_plan_days'] == 38
            assert values['openrouter_api_key'] == 'synthetic-secret'
            raw = (await session.connection())
            assert (await raw.exec_driver_sql('PRAGMA journal_mode')).scalar() == 'delete'
            assert (await raw.exec_driver_sql('PRAGMA synchronous')).scalar() == 2
            row = (await raw.exec_driver_sql("SELECT value FROM app_setting WHERE key='integrations'")).scalar()
            assert json.loads(row)['unknown_future'] == {'keep': True}
        assert chat.get_setting('git_branch') == 'committed'
        assert chat._technical_settings_conn().execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        assert chat._technical_settings_conn().execute('PRAGMA synchronous').fetchone()[0] == 2
        assert chat.technical_setting('github_token') == (True, '')
        assert 'synthetic-secret' not in (output / 'bot.db').read_bytes().decode('latin1')
        assert hashes(source) == before
        with pytest.raises(ValueError, match='changed'):
            sqlite_layout.convert_copy(source, output, autopost_db='post.db', autochat_db='bot.db', sources_stopped=True)
    finally:
        await engine.dispose()
        if chat._conn is not None:
            chat._conn.close();chat._conn = None
        stack.close()


@pytest.mark.asyncio
async def test_six_characters_existing_migration_layout_and_reopen(tmp_path, monkeypatch):
    from tools.character_migrate import build_workspace_copy, LEGACY_ID
    from tools.sqlite_layout import convert_copy
    from app import db as chat
    from autoposter import db as post
    from autoposter.api.characters import create_character, CharacterCreate
    from autoposter.services import appconfig
    from autoposter.workspaces import control_context, character_context
    from autoposter.models import AppSetting, Character
    from autoposter.crypto import encrypt, decrypt
    from sqlalchemy import select
    source = wal_source(tmp_path / 'legacy')
    media = tmp_path / 'media';media.mkdir()
    (media / 'keep.bin').write_bytes(b'synthetic media')
    # Add representative ciphertext and unknown values using the original key.
    ciphertext = encrypt('synthetic-preserved-account')
    with sqlite3.connect(source / 'post.db') as conn:
        conn.execute('UPDATE app_setting SET value=? WHERE key=?',
                     (json.dumps({'x_client_secret': ciphertext, 'unknown_future': {'keep': True}}), 'integrations'))
    migrated = tmp_path / 'migrated'
    migration = build_workspace_copy(source / 'post.db', source / 'bot.db', media, migrated, name='Synthetic existing')
    output = tmp_path / 'converted'
    import gc
    gc.collect()  # Migration's legacy sqlite context managers do not close handles.
    original_hashes = hashes(migrated)
    receipt = convert_copy(migrated, output, autopost_db='autoposter.db', autochat_db='bot.db', sources_stopped=True)
    assert hashes(migrated) == original_hashes
    assert convert_copy(migrated, output, autopost_db='autoposter.db', autochat_db='bot.db', sources_stopped=True) == receipt
    # Preserve the previous migration's strict, hash-checked reuse guarantee.
    reused = build_workspace_copy(source / 'post.db', source / 'bot.db', media, output, name='Synthetic existing')
    assert reused['character_id'] == migration['character_id'] == LEGACY_ID
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    monkeypatch.setattr(chat, '_conn', None)
    monkeypatch.setattr(chat, '_character_connections', {})
    monkeypatch.setattr(chat, 'DATA_DIR', str(output))
    monkeypatch.setattr(chat, 'DB_PATH', str(output / 'bot.db'))
    monkeypatch.setattr(post.settings, 'database_url', 'sqlite+aiosqlite:///' + str(output / 'autoposter.db'))
    engine = post.make_engine()
    monkeypatch.setattr(post, 'engine', engine)
    owners = [LEGACY_ID]
    try:
        with control_context():
            for index in range(1, 6):
                async with post.SessionLocal() as session:
                    char = await create_character(CharacterCreate(name=f'Synthetic {index}'), db=session, _=None)
                    await session.commit()
                    assert not char.planning_enabled and not char.chat_enabled and not char.is_archived
                    owners.append(str(char.id))
        assert len(set(owners)) == 6
        for index, owner in enumerate(owners):
            with character_context(owner):
                async with post.SessionLocal() as session:
                    values = await appconfig.get_all(session, use_cache=False)
                    if index:
                        assert values['x_client_secret'] == ''
                        assert values['auto_plan_days'] == 14
                        assert (await session.execute(select(AppSetting).where(AppSetting.key == 'integrations'))).scalar_one_or_none() is None
                        runtime = (await session.execute(select(AppSetting).where(AppSetting.key == 'runtime'))).scalar_one()
                        assert runtime.value == {'global_pause': True, 'dry_run': True}
                    else:
                        assert values['x_client_secret'] == 'synthetic-preserved-account'
                    await appconfig.set_many(session, {'git_branch': f'shared-{index}',
                        'auto_plan_days': 20 + index, 'x_client_secret': f'synthetic-owner-{index}'})
                    await session.commit()
                chat.set_setting('system_prompt', f'Synthetic content {index}')
                chat.set_setting('fanvue_client_secret', f'synthetic-chat-{index}')
                if index == 0:
                    assert chat.get_conn().execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
                async with post.SessionLocal() as session:
                    await appconfig.set_many(session, {'git_branch': 'not-committed', 'auto_plan_days': 99})
                    await appconfig.clear_secret(session, 'x_client_secret')
                    await appconfig.clear_secret(session, 'github_token')
                    await session.rollback()
        # Close every connection and reopen via the actual factories.
        await engine.dispose()
        for connection in chat._character_connections.values():
            connection.close()
        chat._character_connections.clear()
        if chat._conn is not None:
            chat._conn.close();chat._conn = None
        for index, owner in enumerate(owners):
            with character_context(owner):
                async with post.SessionLocal() as session:
                    values = await appconfig.get_all(session, use_cache=False)
                    assert values['auto_plan_days'] == 20 + index
                    assert values['x_client_secret'] == f'synthetic-owner-{index}'
                    assert values['git_branch'] == 'shared-5'
                    assert values['github_token'] == ''
                    row = (await session.execute(select(AppSetting).where(AppSetting.key == 'integrations'))).scalar_one()
                    assert decrypt(row.value['x_client_secret']) == f'synthetic-owner-{index}'
                    if index == 0:
                        assert row.value['unknown_future'] == {'keep': True}
                assert chat.get_setting('system_prompt') == f'Synthetic content {index}'
                assert chat.get_setting('fanvue_client_secret') == f'synthetic-chat-{index}'
        with sqlite3.connect(output / 'autoposter.db') as conn:
            assert conn.execute('SELECT count(*) FROM character').fetchone()[0] == 6
            assert conn.execute('SELECT count(*) FROM character WHERE planning_enabled OR chat_enabled').fetchone()[0] == 0
        assert hashes(migrated) == original_hashes
    finally:
        await engine.dispose()
        for connection in chat._character_connections.values():
            connection.close()
        chat._character_connections.clear()
        if chat._conn is not None:
            chat._conn.close();chat._conn = None


def test_changed_existing_migration_receipt_is_not_laundered(tmp_path):
    from tools.character_migrate import build_workspace_copy
    from tools.sqlite_layout import convert_copy
    import gc
    source = wal_source(tmp_path / 'source')
    media = tmp_path / 'media';media.mkdir()
    migrated = tmp_path / 'migrated'
    build_workspace_copy(source / 'post.db', source / 'bot.db', media, migrated, name='Synthetic')
    gc.collect()
    (migrated / 'media' / 'changed.bin').write_bytes(b'unrecorded mutation')
    with pytest.raises(ValueError, match='migration.*changed'):
        convert_copy(migrated, tmp_path / 'bad-output', autopost_db='autoposter.db', autochat_db='bot.db', sources_stopped=True)
    assert not (tmp_path / 'bad-output').exists()


@pytest.mark.parametrize('unsafe', ['no-ack', 'open-reader', 'open-writer', 'hot-journal', 'symlink', 'nested', 'path-escape', 'existing-output', 'mutation'])
def test_unsafe_source_or_destination_refused_without_source_sqlite_open(tmp_path, monkeypatch, unsafe):
    from tools import sqlite_layout as layout
    source = wal_source(tmp_path / 'source')
    output = tmp_path / 'output'
    ack = unsafe != 'no-ack'
    post = 'post.db'
    connection = None
    if unsafe in ('open-reader', 'open-writer'):
        connection = sqlite3.connect(source / 'bot.db')
        connection.execute('SELECT * FROM settings').fetchall()
        if unsafe == 'open-writer':
            connection.execute('BEGIN IMMEDIATE')
    elif unsafe == 'hot-journal':
        (source / 'bot.db-journal').write_bytes(b'unsafe-journal')
    elif unsafe == 'symlink':
        (source / 'linked-key').symlink_to(source / 'preserved-key.txt')
    elif unsafe == 'nested':
        output = source / 'output'
    elif unsafe == 'path-escape':
        post = '../source/post.db'
    elif unsafe == 'existing-output':
        output.mkdir();(output / 'precious').write_bytes(b'keep')
    elif unsafe == 'mutation':
        original = layout.shutil.copytree
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            (source / 'preserved-key.txt').write_bytes(b'intentional simulated concurrent write')
            return result
        monkeypatch.setattr(layout.shutil, 'copytree', changed)
    before = hashes(source)
    # The converter must never pass an input path to SQLite, even read-only:
    # mode=ro may create/change SHM; immutable=1 would ignore WAL contents.
    original_connect = layout.sqlite3.connect
    def guarded(path, *args, **kwargs):
        assert source not in Path(path).absolute().parents
        return original_connect(path, *args, **kwargs)
    monkeypatch.setattr(layout.sqlite3, 'connect', guarded)
    try:
        with pytest.raises(ValueError):
            layout.convert_copy(source, output, autopost_db=post, autochat_db='bot.db', sources_stopped=ack)
        if unsafe != 'mutation':
            assert hashes(source) == before
        if unsafe == 'existing-output':
            assert (output / 'precious').read_bytes() == b'keep'
        else:
            assert not output.exists()
        assert not list(tmp_path.glob('.sqlite-layout-*'))
    finally:
        if connection is not None:
            connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('tamper', ['wal', 'marker'])
async def test_normal_factories_fail_closed_on_tampered_converted_layout(tmp_path, monkeypatch, tamper):
    from tools.sqlite_layout import convert_copy
    from app import db as chat
    from autoposter import db as post
    source = wal_source(tmp_path / 'source')
    output = tmp_path / 'output'
    convert_copy(source, output, autopost_db='post.db', autochat_db='bot.db', sources_stopped=True)
    for name in ('bot.db', 'post.db'):
        connection = sqlite3.connect(output / name)
        try:
            if tamper == 'wal':
                connection.execute('PRAGMA journal_mode=WAL')
            else:
                connection.execute('UPDATE _settings_atomic_layout SET version=99');connection.commit()
        finally:
            connection.close()
    monkeypatch.setattr(chat, 'DATA_DIR', str(output))
    monkeypatch.setattr(chat, 'DB_PATH', str(output / 'bot.db'))
    monkeypatch.setattr(chat, '_conn', None)
    with pytest.raises(ValueError, match='layout|journal'):
        chat._connect()
    monkeypatch.setattr(post.settings, 'database_url', 'sqlite+aiosqlite:///' + str(output / 'post.db'))
    engine = post.make_engine()
    try:
        with pytest.raises(ValueError, match='layout|journal'):
            async with engine.connect():
                pass
    finally:
        await engine.dispose()


def test_cli_copy_preserves_ciphertext_and_key_bytes_without_decryption(tmp_path):
    from autoposter.crypto import encrypt
    from app.setting_scope import encode_technical_value
    from contextlib import closing
    source = wal_source(tmp_path / 'source')
    account = encrypt('synthetic-key-bound-account')
    envelope = json.dumps(encode_technical_value('openrouter_api_key', 'synthetic-key-bound-technical'))
    with closing(sqlite3.connect(source / 'bot.db')) as conn:
        conn.execute('INSERT INTO settings VALUES(?,?)', ('openrouter_api_key', envelope));conn.commit()
    with closing(sqlite3.connect(source / 'post.db')) as conn:
        conn.execute('UPDATE app_setting SET value=?', (json.dumps({'x_client_secret': account}),));conn.commit()
    before = hashes(source)
    output = tmp_path / 'cli-output'
    env = dict(os.environ, MP_AUTOPOSTER_ENCRYPTION_KEY='intentionally-unusable-key')
    command = [sys.executable, '-m', 'tools.sqlite_layout', '--source-root', str(source),
               '--destination', str(output), '--autopost-db', 'post.db', '--autochat-db', 'bot.db', '--sources-stopped']
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['activation_performed'] is False
    assert hashes(source) == before
    with closing(sqlite3.connect(output / 'bot.db')) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='openrouter_api_key'").fetchone()[0] == envelope
    with closing(sqlite3.connect(output / 'post.db')) as conn:
        assert json.loads(conn.execute('SELECT value FROM app_setting').fetchone()[0])['x_client_secret'] == account
    assert (output / 'preserved-key.txt').read_bytes() == (source / 'preserved-key.txt').read_bytes()
    assert 'synthetic-key-bound' not in result.stdout

