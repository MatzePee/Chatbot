"""Active workspace update probe. Only disposable copies are started."""
from contextlib import closing
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import tempfile
from unittest.mock import patch



def snapshot_tables(connection):
    """Record existing columns as well as rows: additive schema is permitted."""
    from tools.release_smoke import fingerprint
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    result = {}
    for name in tables:
        quoted = name.replace('"', '""')
        columns = [row[1] for row in connection.execute('PRAGMA table_info("' + quoted + '")')]
        result[name] = (columns, fingerprint(connection, name, columns))
    return result


def assert_tables_preserved(connection, snapshot):
    from tools.release_smoke import fingerprint
    changed = [name for name, (columns, digest) in snapshot.items()
               if fingerprint(connection, name, columns) != digest]
    assert not changed, 'Existing workspace data changed in tables: ' + ', '.join(changed)


def run_active(source):
    from app.character_upgrade import _active_paths
    pointer = json.loads((source / 'data/character-active.json').read_text())
    active = _active_paths(source, pointer)
    from dotenv import load_dotenv
    load_dotenv(source / '.env', override=False)
    load_dotenv(source / 'data/autoposter.env', override=False)
    def blocked(*args, **kwargs):
        raise RuntimeError('Network disabled during release verification')
    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    with tempfile.TemporaryDirectory(prefix='workspace-smoke-') as temporary:
        copied = Path(temporary) / 'active'
        # Database copies use SQLite backup, never a naked live main-file copy.
        shutil.copytree(active, copied, ignore=lambda directory, names: [n for n in names if n.endswith(('.db', '.db-wal', '.db-shm', '.db-journal'))])
        for original in active.rglob('*.db'):
            if original.is_symlink():
                raise ValueError('Symlink database unsupported')
            target = copied / original.relative_to(active)
            target.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
        os.environ.update(MP_CHARACTERS_ENABLED='1', MP_PREVIEW='0',
                          MP_AUTOCHAT_DATA_DIR=str(copied),
                          MP_AUTOPOSTER_DATABASE_URL='sqlite+aiosqlite:///' + str(copied / 'autoposter.db'),
                          MP_AUTOPOSTER_DATA_DIR=str(copied), MP_AUTOPOSTER_MEDIA_ROOT=str(copied / 'media'),
                          MP_AUTOPOSTER_SCHEDULER_MODE='off', MP_AUTOPOSTER_AUTH_ENABLED='false',
                          MP_EXPORT_DIR=str(Path(temporary) / 'exports'))
        before = {}
        for database in copied.rglob('*.db'):
            with closing(sqlite3.connect(database)) as conn:
                before[database] = snapshot_tables(conn)
        media_before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (copied / 'media').rglob('*') if p.is_file()}
        from autoposter.crypto import decrypt
        from autoposter.services.appconfig import SECRET_FIELDS
        with closing(sqlite3.connect(copied / 'autoposter.db')) as conn:
            owners = conn.execute('SELECT id FROM character WHERE is_archived=0').fetchall()
            for access, refresh in conn.execute('SELECT access_token_enc,refresh_token_enc FROM channel_credential'):
                for value in (access, refresh):
                    if value:
                        assert decrypt(value), 'Stored account secret cannot be decrypted'
            for (value,) in conn.execute("SELECT value FROM app_setting WHERE key LIKE '%:integrations'"):
                for key, secret in json.loads(value).items():
                    if key in SECRET_FIELDS and secret:
                        assert decrypt(secret), 'Stored integration secret cannot be decrypted'
        from app import db, deployment
        from tools import backup
        from autoposter import scheduler
        async def no_jobs():
            return None
        async def no_problem_check(db):
            return None
        from autoposter.services import problems
        from fastapi.testclient import TestClient
        from app.main import app
        from uuid import UUID
        logging.disable(logging.CRITICAL)
        # Auth-free GETs may lazily flush a System app_user. Exercise the real
        # dependency/session and roll back request-only writes; startup and any
        # explicitly committed corruption remain covered by full fingerprints.
        from autoposter.db import get_db, SessionLocal
        async def read_probe_db():
            async with SessionLocal() as session:
                try:
                    yield session
                finally:
                    await session.rollback()
        with patch.dict(app.dependency_overrides, {get_db: read_probe_db}), patch.object(deployment, 'pending', lambda: True), patch.object(backup, 'backup', lambda *a, **k: None), patch.object(scheduler, 'start_if_enabled', no_jobs), patch.object(scheduler, 'stop_if_running', no_jobs), patch.object(problems, 'check_if_due', no_problem_check):
            with TestClient(app) as client:
                for (identity,) in owners:
                    owner = str(UUID(identity))
                    for path in ('/autoposter/', '/settings/shared?section=autochat', '/api/v1/dashboard', '/api/v1/settings/integrations'):
                        separator = '&' if '?' in path else '?'
                        response = client.get(path + separator + 'character_id=' + owner)
                        assert response.status_code == 200, 'Workspace route failed: ' + path
        for database, tables in before.items():
            with closing(sqlite3.connect(database)) as conn:
                assert_tables_preserved(conn, tables)
                assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in media_before.items()), 'Workspace media changed'
        for connection in db._character_connections.values():
            connection.close()
        db._character_connections.clear()
        if db._conn:
            db._conn.close()
            db._conn = None
        print(json.dumps({'ok': True, 'characters_verified': len(owners), 'databases_preserved': len(before), 'network_and_workers': 'disabled', 'installation_mode': 'characters'}))
