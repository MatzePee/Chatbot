"""Real SQLite transactions; disposable files and synthetic values only."""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from app import db as chat
from autoposter.db import Base
from autoposter.models import Character
from autoposter.services import appconfig
from autoposter.workspaces import character_session


@pytest_asyncio.fixture(params=['attached-delete', 'same-file-wal'])
async def stores(tmp_path, request):
    # Supported layouts only; never change live DBs or deployment pragmas.
    if request.param == 'attached-delete':
        chat._conn.execute('PRAGMA journal_mode=DELETE')
        path = tmp_path / 'post.db'
    else:
        path = chat.DB_PATH
    engine = create_async_engine('sqlite+aiosqlite:///' + str(path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from sqlalchemy.ext.asyncio import AsyncSession
    async with AsyncSession(engine, expire_on_commit=False) as session:
        a, b = Character(name='Synthetic A'), Character(name='Synthetic B')
        session.add_all([a, b])
        await session.commit()
        owners = a.id, b.id
    chat.set_setting('openrouter_api_key', 'synthetic-before')
    try:
        yield engine, owners
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mixed_settings_rollback_is_invisible_to_other_readers(stores):
    engine, (a, b) = stores
    async with character_session(engine, a) as session:
        await appconfig.set_many(session, {
            'openrouter_api_key': 'synthetic-uncommitted', 'auto_plan_days': 91,
        })
        # The caller owns the commit. Separate synchronous readers must not see it.
        assert chat.get_setting('openrouter_api_key') == 'synthetic-before'
        assert (await appconfig.get_all(session, use_cache=False))['openrouter_api_key'] == 'synthetic-uncommitted'
        await session.rollback()
        assert (await appconfig.get_all(session))['auto_plan_days'] == 14
        assert appconfig.current('openrouter_api_key') == 'synthetic-before'
    async with character_session(engine, a) as session:
        values = await appconfig.get_all(session)
        assert values['auto_plan_days'] == 14
        assert values['openrouter_api_key'] == 'synthetic-before'


@pytest.mark.asyncio
async def test_clear_secret_rolls_back_with_character_changes(stores):
    engine, (a, b) = stores
    async with character_session(engine, a) as session:
        await appconfig.clear_secret(session, 'openrouter_api_key')
        assert chat.technical_setting('openrouter_api_key') == (True, 'synthetic-before')
        await appconfig.set_many(session, {'auto_plan_days': 83})
        assert (await appconfig.get_all(session))['openrouter_api_key'] == ''
        await session.rollback()
    assert chat.technical_setting('openrouter_api_key') == (True, 'synthetic-before')
    async with character_session(engine, a) as session:
        assert (await appconfig.get_all(session))['auto_plan_days'] == 14


@pytest.mark.asyncio
@pytest.mark.parametrize('second_has_technical', [True, False, 'clear'])
async def test_concurrent_mixed_updates_merge_after_writer_lock(stores, second_has_technical):
    import asyncio
    from sqlalchemy import select
    from autoposter.models import AppSetting
    engine, (a, b) = stores
    async with character_session(engine, a) as session:
        await appconfig.set_many(session, {'auto_plan_days': 14, 'inventory_warn_days': 3,
                                           'x_client_secret': 'synthetic-clear-me'})
    loaded, first_written, second_attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def first():
        await loaded.wait()
        async with character_session(engine, a) as session:
            await appconfig.set_many(session, {'git_branch': 'synthetic-first', 'auto_plan_days': 45})
            first_written.set()
            await second_attempted.wait()

    async def second():
        async with character_session(engine, a) as session:
            # Keep a stale identity-map object, as real long-lived callers can.
            stale = (await session.execute(select(AppSetting).where(AppSetting.key == 'integrations'))).scalar_one()
            assert stale.value['auto_plan_days'] == 14
            loaded.set()
            await first_written.wait()
            from sqlalchemy import event
            def writer_attempt(conn, cursor, statement, parameters, context, executemany):
                if statement.lstrip().upper().startswith(('BEGIN IMMEDIATE', 'UPDATE', 'INSERT')):
                    second_attempted.set()
            event.listen(engine.sync_engine, 'before_cursor_execute', writer_attempt)
            if second_has_technical == 'clear':
                await appconfig.clear_secret(session, 'x_client_secret')
            else:
                patch = {'inventory_warn_days': 9}
                if second_has_technical is True:
                    patch['git_user_name'] = 'synthetic-second'
                await appconfig.set_many(session, patch)
            assert stale is not None

    await asyncio.wait_for(asyncio.gather(first(), second()), timeout=15)
    async with character_session(engine, a) as session:
        values = await appconfig.get_all(session, use_cache=False)
        assert values['auto_plan_days'] == 45
        assert values['git_branch'] == 'synthetic-first'
        if second_has_technical == 'clear':
            assert values['x_client_secret'] == ''
            assert values['inventory_warn_days'] == 3
        else:
            assert values['inventory_warn_days'] == 9
        if second_has_technical is True:
            assert values['git_user_name'] == 'synthetic-second'


@pytest.mark.asyncio
async def test_commit_preserves_encryption_masking_clear_and_owner_isolation(stores):
    import json
    from autoposter.crypto import decrypt
    engine, (a, b) = stores
    async with character_session(engine, a) as session:
        result = await appconfig.set_many(session, {
            'openrouter_api_key': 'synthetic-committed',
            'x_client_secret': 'synthetic-owner-secret', 'auto_plan_days': 73,
        })
        assert result['openrouter_api_key'] == 'synthetic-committed'
        assert appconfig.mask(result)['openrouter_api_key'] == '********'
    assert chat.get_setting('openrouter_api_key') == 'synthetic-committed'
    raw = chat._conn.execute('SELECT value FROM settings WHERE key=?', ('openrouter_api_key',)).fetchone()[0]
    assert 'synthetic-committed' not in raw
    assert decrypt(json.loads(raw)['ciphertext']) == 'synthetic-committed'
    async with character_session(engine, b) as session:
        values = await appconfig.get_all(session)
        assert values['openrouter_api_key'] == 'synthetic-committed'
        assert values['auto_plan_days'] == 14
        assert values['x_client_secret'] == ''
    async with character_session(engine, a) as session:
        for placeholder in ('', None, '********'):
            result = await appconfig.set_many(session, {'openrouter_api_key': placeholder})
            assert result['openrouter_api_key'] == 'synthetic-committed'
        assert result['x_client_secret'] == 'synthetic-owner-secret'
        await appconfig.clear_secret(session, 'openrouter_api_key')
        assert (await appconfig.get_all(session))['openrouter_api_key'] == ''
    assert chat.technical_setting('openrouter_api_key') == (True, '')
    async with character_session(engine, b) as session:
        assert (await appconfig.get_all(session))['openrouter_api_key'] == ''
    await engine.dispose()
    assert chat.technical_setting('openrouter_api_key') == (True, '')


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['save', 'clear'])
async def test_unsupported_memory_layout_rejected_before_autoflush(db, monkeypatch, operation):
    from sqlalchemy import event, inspect
    from autoposter.workspaces import control_context
    from autoposter.services.settings_transaction import SettingsTransactionUnsupported
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    chat.set_setting('openrouter_api_key', 'synthetic-before')
    pending = Character(name='must-not-flush')
    db.add(pending)
    writes = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE')):
            writes.append(statement)
    event.listen(db.bind.sync_engine, 'before_cursor_execute', capture)
    with control_context(), pytest.raises(SettingsTransactionUnsupported):
        if operation == 'save':
            await appconfig.set_many(db, {'git_branch': 'rejected', 'auto_plan_days': 99})
        else:
            await appconfig.clear_secret(db, 'openrouter_api_key')
    assert writes == []
    assert inspect(pending).pending
    assert chat.technical_setting('openrouter_api_key') == (True, 'synthetic-before')
    await db.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['save', 'clear', 'git'])
async def test_unsupported_layout_is_explicit_http_conflict(db, monkeypatch, route):
    from fastapi import HTTPException
    from autoposter.api.dashboard import save_integrations, clear_integration_secret
    from autoposter.api.system import save_git_settings, GitSettingsIn
    from autoposter.workspaces import control_context
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    with control_context(), pytest.raises(HTTPException) as caught:
        if route == 'save':
            await save_integrations({'git_branch': 'synthetic', 'auto_plan_days': 30}, db, None)
        elif route == 'clear':
            await clear_integration_secret({'field': 'openrouter_api_key'}, db, None)
        else:
            await save_git_settings(GitSettingsIn(git_branch='synthetic'), db, None)
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_first_enlistment_inside_savepoint_rejected_without_writes(stores):
    from autoposter.services.settings_transaction import SettingsTransactionUnsupported
    engine, (a, b) = stores
    async with character_session(engine, a) as session:
        async with session.begin_nested():
            with pytest.raises(SettingsTransactionUnsupported):
                await appconfig.set_many(session, {'git_branch': 'rejected', 'auto_plan_days': 94})
        await session.rollback()
    assert chat.get_setting('git_branch') != 'rejected'
    async with character_session(engine, a) as session:
        assert (await appconfig.get_all(session))['auto_plan_days'] == 14


@pytest.mark.asyncio
async def test_large_attached_write_reads_own_transaction_without_second_connection(stores):
    engine, (a, b) = stores
    schema = 'main' if str(engine.url.database) == chat.DB_PATH else 'technical_config'
    async with character_session(engine, a) as session:
        await appconfig.set_many(session, {'git_branch': 'synthetic'})
        connection = await session.connection()
        await connection.exec_driver_sql(f'PRAGMA {schema}.cache_size=5')
        chat._conn.execute('PRAGMA busy_timeout=10')
        large_value = 'synthetic-' * 16000
        values = await appconfig.set_many(session, {'git_commit_default': large_value, 'auto_plan_days': 88})
        assert values['git_commit_default'] == large_value
        await session.rollback()
    assert chat.get_setting('git_commit_default') != large_value


@pytest.mark.asyncio
@pytest.mark.parametrize('unsafe', ['main-wal', 'technical-wal', 'sync-off'])
async def test_cross_file_unsafe_durability_rejected_before_dml(tmp_path, monkeypatch, unsafe):
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import AsyncSession
    from autoposter.workspaces import control_context
    from autoposter.services.settings_transaction import SettingsTransactionUnsupported
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    if unsafe != 'technical-wal':
        chat._conn.execute('PRAGMA journal_mode=DELETE')
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'unsafe.db'))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    writes = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE')):
            writes.append(statement)
    event.listen(engine.sync_engine, 'before_cursor_execute', capture)
    try:
        async with AsyncSession(engine) as session:
            connection = await session.connection()
            if unsafe == 'main-wal':
                await connection.exec_driver_sql('PRAGMA journal_mode=WAL')
            if unsafe == 'sync-off':
                await connection.exec_driver_sql('PRAGMA synchronous=OFF')
            with control_context(), pytest.raises(SettingsTransactionUnsupported):
                await appconfig.set_many(session, {'git_branch': 'rejected', 'auto_plan_days': 99})
            assert writes == []
            await session.rollback()
        assert chat.get_setting('git_branch') != 'rejected'
    finally:
        await engine.dispose()

