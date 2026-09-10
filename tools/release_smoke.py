"""Exercise a production startup against disposable copies, without network or workers."""
from pathlib import Path
import argparse
import hashlib
import json
import logging
import os
import shutil
import socket
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fingerprint(connection, table, columns=None):
    digest = hashlib.sha256()
    projection = ', '.join('"' + c.replace('"', '""') + '"' for c in columns) if columns else '*'
    for row in connection.execute('SELECT ' + projection + ' FROM "' + table.replace('"', '""') + '" ORDER BY rowid'):
        digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def run(source: Path):
    assert source.resolve() != ROOT.resolve(), 'Use a separate candidate checkout.'
    with tempfile.TemporaryDirectory(prefix='creatorstudio-smoke-') as temp:
        root = Path(temp)
        (root / 'data').mkdir()
        for name, target in [('data/bot.db', root / 'data/bot.db'),
                             ('data/autoposter/autoposter.db', root / 'studio/autoposter.db')]:
            original = source / name
            if original.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(original.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
                    src.backup(dst)
        assert (root / 'data/bot.db').exists(), 'Existing AutoChat database missing.'
        from dotenv import load_dotenv
        load_dotenv(source / '.env', override=False)
        load_dotenv(source / 'data/autoposter.env', override=False)
        os.environ.update(MP_PREVIEW='0', MP_AUTOPOSTER_DATABASE_URL='sqlite+aiosqlite:///' + str(root / 'studio/autoposter.db'),
                          MP_AUTOPOSTER_DATA_DIR=str(root / 'studio'), MP_AUTOPOSTER_MEDIA_ROOT=str(root / 'studio/media'),
                          MP_AUTOPOSTER_SCHEDULER_MODE='off', MP_AUTOPOSTER_AUTH_ENABLED='false')
        # Existing AutoPost runtime, if present, must not be treated as a new install.
        if (source / 'data/autoposter/.creatorpilot-initialized').exists():
            (root / 'studio/.creatorpilot-initialized').touch()
        # Read and decrypt existing private AutoPost data without contacting providers.
        studio_before = {}
        studio_columns = {}
        credential_count = 0
        if (root / 'studio/autoposter.db').exists():
            from autoposter.crypto import decrypt
            from autoposter.services.appconfig import SECRET_FIELDS
            with sqlite3.connect(root / 'studio/autoposter.db') as conn:
                names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                studio_columns = {name: [r[1] for r in conn.execute('PRAGMA table_info("' + name.replace('"', '""') + '")')] for name in names}
                studio_before = {name: fingerprint(conn, name, studio_columns[name]) for name in names}
                for access, refresh in conn.execute('SELECT access_token_enc,refresh_token_enc FROM channel_credential'):
                    for value in (access, refresh):
                        if value:
                            assert decrypt(value)
                            credential_count += 1
                for (value,) in conn.execute('SELECT oauth_client_secret_enc FROM channel WHERE oauth_client_secret_enc IS NOT NULL'):
                    if value:
                        assert decrypt(value)
                        credential_count += 1
                for (value,) in conn.execute("SELECT value FROM app_setting WHERE key='integrations'"):
                    for key, secret in json.loads(value).items():
                        if key in SECRET_FIELDS and secret:
                            assert decrypt(secret)
                            credential_count += 1
                for path, digest in conn.execute('SELECT storage_path,sha256 FROM media_asset'):
                    media = source / 'data/autoposter/media' / path
                    assert media.is_file(), 'Existing AutoPost media missing.'
                    assert hashlib.sha256(media.read_bytes()).hexdigest() == digest, 'AutoPost media checksum mismatch.'
        def blocked(*args, **kwargs):
            raise RuntimeError('Network disabled during release verification')
        socket.create_connection = blocked
        socket.socket.connect = blocked
        socket.socket.connect_ex = blocked
        logging.disable(logging.CRITICAL)
        from app import db, poller
        db._conn = None
        db.DATA_DIR = str(root / 'data')
        db.DB_PATH = str(root / 'data/bot.db')
        db._seed_from_env = lambda: None
        original_settings = db.all_settings()
        with sqlite3.connect(db.DB_PATH) as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            before = {name: fingerprint(conn, name) for name in tables}
        started = []
        poller.start = lambda: started.append(True)
        poller.stop = lambda: None
        from autoposter import scheduler
        async def no_jobs():
            return None
        scheduler.start_if_enabled = no_jobs
        scheduler.stop_if_running = no_jobs
        from app.main import app
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            for path in ['/health', '/api/creatorpilot/health', '/settings/shared', '/system', '/autoposter/', '/api/v1/dashboard']:
                result = client.get(path)
                assert result.status_code == 200, f'Startup route failed: {path} ({result.status_code})'
            assert started == [True], 'Production AutoChat worker would not start.'
            assert db.all_settings() == original_settings, 'Existing AutoChat settings changed.'
            with sqlite3.connect(db.DB_PATH) as conn:
                assert all(fingerprint(conn, name) == digest for name, digest in before.items()), 'Existing AutoChat data changed.'
                assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            if not (source / 'data/autoposter/.creatorpilot-initialized').exists():
                runtime = client.get('/api/v1/settings').json()
                assert runtime['global_pause'] and runtime['dry_run'], 'Fresh AutoPost must start paused.'
            elif studio_before:
                with sqlite3.connect(root / 'studio/autoposter.db') as conn:
                    # Additive columns are allowed; every pre-existing cell and
                    # row must still match, including row counts and ordering.
                    assert all(fingerprint(conn, name, studio_columns[name]) == digest for name, digest in studio_before.items()), 'Existing AutoPost data changed.'
                    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            result = {'ok': True, 'python': sys.version.split()[0], 'existing_tables_preserved': len(before),
                      'autopost_tables_preserved': len(studio_before), 'autopost_secrets_verified': credential_count,
                      'autochat_running_preserved': db.get_setting('bot_running'), 'workers_and_network': 'disabled for verification'}
        if db._conn:
            db._conn.close()
            db._conn = None
        print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    run(parser.parse_args().source.resolve())
