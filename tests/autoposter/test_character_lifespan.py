"""Actual application lifespan, real file-backed synthetic installation."""
import os
import subprocess
import sys
import textwrap
import pytest


@pytest.mark.parametrize('fresh', [False, True])
@pytest.mark.parametrize('mock_workers', [False, True])
def test_normal_lifespan_two_migrated_characters_no_schema_or_switch_mutation(tmp_path, fresh, mock_workers):
    code = r'''
import asyncio, json, os, sqlite3
from pathlib import Path
from sqlalchemy import create_engine
from autoposter.db import Base
import autoposter.models as m
from tools.character_migrate import build_workspace_copy, LEGACY_ID
from app import db as chat
root = Path(os.environ['FIXTURE_ROOT'])
old = root / 'legacy.db'
sync = create_engine('sqlite:///' + str(old))
Base.metadata.create_all(sync)
with sync.begin() as c:
    c.execute(m.AppSetting.__table__.insert().values(key='runtime', value={'dry_run':False,'global_pause':False}))
sync.dispose()
chat.DATA_DIR = str(root / 'old-chat')
chat.DB_PATH = str(root / 'old-chat' / 'bot.db')
chat.init_db()
chat.set_setting('bot_running', False)
chat._conn.close(); chat._conn = None
media = root / 'old-media'; media.mkdir()
if os.environ.get('FRESH_INSTALL') == '1':
    from tools.character_migrate import initialize_empty
    result = initialize_empty(root / 'installation', name='Operator supplied')
else:
    result = build_workspace_copy(old, chat.DB_PATH, media, root / 'installation', name='Operator supplied')
installed = root / 'installation'
# The DB URI was selected before imports; install only our synthetic copy.
import shutil
shutil.copy2(installed / 'autoposter.db', root / 'active.db')
shutil.copytree(installed / 'characters', root / 'active-chat' / 'characters')
chat.DATA_DIR = str(root / 'active-chat')
chat.DB_PATH = str(root / 'active-chat' / 'bot.db')
with sqlite3.connect(root / 'active.db') as conn:
    conn.execute("INSERT INTO character(id,name,planning_enabled,chat_enabled,is_archived) VALUES(?, 'Second',0,0,0)", ('a'*32,))
    before = conn.execute("SELECT key,value FROM app_setting ORDER BY key").fetchall()
    schema = conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
from app.main import app
from app import deployment
from autoposter.config import settings
settings.scheduler_mode = 'off'
from tools import backup
backup.backup = lambda *args, **kwargs: None
# Disable effects, not the application startup/lifespan.
deployment.pending = lambda: os.environ.get('MOCK_WORKERS') != '1'
from app import poller
from autoposter.workspaces import current_character
workers = []
poller.start = lambda: workers.append(('start',str(current_character())))
poller.stop = lambda: workers.append(('stop',str(current_character())))
from fastapi.testclient import TestClient
with TestClient(app) as client:
    response = client.get('/api/v1/characters')
    assert response.status_code == 200, response.text
    assert len(response.json()) == 2
    for path in ('/health','/api/creatorpilot/health'):
        response = client.get(path)
        assert response.status_code == 200, response.text
    for owner in (LEGACY_ID, 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'):
        response = client.get('/autoposter/?character_id=' + owner)
        assert response.status_code == 200, response.text
if os.environ.get('MOCK_WORKERS') == '1':
    assert workers == [('start',LEGACY_ID),('start','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'),('stop',LEGACY_ID),('stop','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')], workers
else:
    assert not workers
with sqlite3.connect(root / 'active.db') as conn:
    assert conn.execute("SELECT key,value FROM app_setting ORDER BY key").fetchall() == before
    assert conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall() == schema
print('LIFESPAN_TWO_CHARACTERS_PRESERVED')
'''
    env = dict(os.environ, MOCK_WORKERS='1' if mock_workers else '0', FIXTURE_ROOT=str(tmp_path), FRESH_INSTALL='1' if fresh else '0', MP_CHARACTERS_ENABLED='1',
               MP_AUTOPOSTER_DATABASE_URL='sqlite+aiosqlite:///' + str(tmp_path / 'active.db'),
               MP_AUTOPOSTER_DATA_DIR=str(tmp_path / 'autoposter'),
               MP_AUTOPOSTER_MEDIA_ROOT=str(tmp_path / 'media'),
               MP_AUTOPOSTER_SCHEDULER_MODE='off', MP_AUTOPOSTER_AUTH_ENABLED='false')
    # Fixture construction is legacy-mode; activation occurs after its copy migration.
    code = code.replace('import asyncio, json, os, sqlite3', "import asyncio, json, os, sqlite3\nos.environ.pop('MP_CHARACTERS_ENABLED', None)")
    code = code.replace('from app.main import app', "os.environ['MP_CHARACTERS_ENABLED'] = '1'\nfrom app.main import app")
    result = subprocess.run([sys.executable, '-c', textwrap.dedent(code)], env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'LIFESPAN_TWO_CHARACTERS_PRESERVED' in result.stdout
