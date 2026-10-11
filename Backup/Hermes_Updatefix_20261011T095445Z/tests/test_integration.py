"""Regression checks for update compatibility and independent namespaces."""
from pathlib import Path
import subprocess
import sys
import sqlite3
from tools.backup import backup

ROOT = Path(__file__).resolve().parents[1]


def test_backup_includes_wal_and_preserves_secrets(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    (tmp_path / '.env').write_text('SECRET_KEY=example-test-value\n')
    (data / 'autoposter.env').write_text('MP_AUTOPOSTER_SECRET_KEY=example-test-value\n')
    with sqlite3.connect(data / 'bot.db') as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE tokens (value TEXT)')
        db.execute("INSERT INTO tokens VALUES ('kept')")
        db.commit()
        snapshot = backup(tmp_path)
        with sqlite3.connect(snapshot / 'data/bot.db') as saved:
            assert saved.execute('SELECT value FROM tokens').fetchone() == ('kept',)
    assert (snapshot / '.env').read_bytes() == (tmp_path / '.env').read_bytes()
    assert (snapshot / 'data/autoposter.env').read_bytes() == (data / 'autoposter.env').read_bytes()


def test_configuration_namespaces_are_separate(tmp_path):
    import os
    env = {**os.environ, 'SECRET_KEY': 'chatbot-only', 'DATABASE_URL': 'broken://chatbot',
           'MP_AUTOPOSTER_DATABASE_URL': 'sqlite+aiosqlite:///:memory:',
           'MP_AUTOPOSTER_DATA_DIR': str(tmp_path), 'MP_AUTOPOSTER_SECRET_KEY': 'studio-only'}
    code = '''from autoposter.config import settings
assert settings.secret_key == 'studio-only'
assert settings.database_url == 'sqlite+aiosqlite:///:memory:'
'''
    subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, check=True)


def test_routes_and_git_update_authority(tmp_path):
    import os
    env = {**os.environ, 'MP_AUTOPOSTER_DATABASE_URL': 'sqlite+aiosqlite:///:memory:',
           'MP_AUTOPOSTER_DATA_DIR': str(tmp_path)}
    code = '''from app.main import app
paths = {r.path for r in app.routes}
assert {'/', '/settings', '/queue', '/upload', '/system/update', '/autoposter', '/api/v1/channels', '/api/v1/posts'} <= paths
assert '/api/v1/system/update' not in paths
assert app.title == 'MP CreatorStudio'
'''
    subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, check=True)


def test_full_shadow_startup_preserves_chatbot_and_blocks_writes(tmp_path):
    import os
    import textwrap
    env = {**os.environ, 'MP_PREVIEW': '1',
           'MP_AUTOPOSTER_DATABASE_URL': 'sqlite+aiosqlite:///' + str(tmp_path / 'studio.db'),
           'MP_AUTOPOSTER_DATA_DIR': str(tmp_path / 'studio'),
           'MP_AUTOPOSTER_MEDIA_ROOT': str(tmp_path / 'studio/media'),
           'MP_AUTOPOSTER_AUTH_ENABLED': 'false', 'TEST_ROOT': str(tmp_path)}
    code = '''
import os
from pathlib import Path
from app import db
root = Path(os.environ['TEST_ROOT'])
db.DATA_DIR = str(root / 'data')
db.DB_PATH = str(root / 'data/bot.db')
db.init_db()
db.set_setting('existing_api_key', 'keep-this-test-value')
from app.main import app
from fastapi.testclient import TestClient
with TestClient(app) as client:
    for path in ['/', '/settings', '/queue', '/reports', '/autoposter/', '/autoposter/js/app.js', '/api/v1/dashboard', '/api/v1/channels', '/api/v1/calendar/month?year=2026&month=9']:
        result = client.get(path)
        assert result.status_code == 200, (path, result.status_code, result.text[:150])
    runtime = client.get('/api/v1/settings').json()
    assert runtime['dry_run'] and runtime['global_pause']
    created = client.post('/api/v1/channels', json={'platform': 'x', 'display_name': 'Integration test', 'handle': '@test'})
    assert created.status_code == 403, created.text
    channels = client.get('/api/v1/channels').json()
    assert not any(c['display_name'] == 'Integration test' for c in channels)
    assert client.post('/queue/1/approve').status_code == 403
    assert client.post('/toggle-running').status_code == 403
    assert client.get('/oauth/start').status_code == 403
    assert client.get('/api/creatorpilot/health').json()['preview'] is True
    assert db.get_setting('existing_api_key') == 'keep-this-test-value'
assert (root / 'data/backups').exists()
'''
    subprocess.run([sys.executable, '-c', textwrap.dedent(code)], cwd=ROOT, env=env, check=True, capture_output=True, text=True)
