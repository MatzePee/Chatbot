"""Synthetic stopped-installation upgrade; never touches a real installation."""
import json
from pathlib import Path
import subprocess
import sys
import os

ROOT = Path(__file__).resolve().parents[1]


def test_staged_upgrade_requires_explicit_confirmation_and_does_not_migrate(tmp_path):
    from app import character_upgrade as upgrade
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/bot.db').write_bytes(b'not opened during staging')
    before = (tmp_path / 'data/bot.db').read_bytes()
    import pytest
    with pytest.raises(ValueError, match='confirmation'):
        upgrade.stage(tmp_path, name='Cherry', confirmed=False)
    result = upgrade.stage(tmp_path, name='Cherry', confirmed=True)
    assert result['phase'] == 'pending_restart'
    assert (tmp_path / 'data/bot.db').read_bytes() == before
    assert not (tmp_path / 'data/character-active.json').exists()
    assert upgrade.stage(tmp_path, name='Cherry', confirmed=True) == result
    assert upgrade.status(tmp_path)['name'] == 'Cherry'
    upgrade.cancel(tmp_path)
    assert upgrade.status(tmp_path)['phase'] == 'legacy'


import pytest


@pytest.mark.parametrize('existing_name', ['Cherry', 'Bestehender Charakter'])
@pytest.mark.parametrize('interrupted', [False, True])
def test_confirmed_restart_runs_copy_upgrade_before_real_app_import(tmp_path, existing_name, interrupted):
    code = r'''
import os
from pathlib import Path
from sqlalchemy import create_engine
from autoposter.db import Base
import autoposter.models
from app import db
root = Path(os.environ['UPGRADE_ROOT'])
(root/'data/autoposter/media').mkdir(parents=True,exist_ok=True)
db.DATA_DIR=str(root/'data'); db.DB_PATH=str(root/'data/bot.db'); db._seed_from_env=lambda:None
db.init_db(); db.set_setting('bot_running',False); db.set_setting('system_prompt','Cherry identity')
db._conn.close(); db._conn=None
engine=create_engine('sqlite:///'+str(root/'data/autoposter/autoposter.db'))
Base.metadata.create_all(engine); engine.dispose()
(root/'.env').write_text('PRIVATE_SENTINEL=retained\n')
(root/'exports').mkdir(); (root/'exports/history.md').write_text('synthetic conversation')
from app.character_upgrade import stage
stage(root,name=os.environ['EXISTING_NAME'],confirmed=True)
'''
    env = {**os.environ, 'UPGRADE_ROOT': str(tmp_path), 'EXISTING_NAME': existing_name,
           'MP_AUTOPOSTER_DATABASE_URL': 'sqlite+aiosqlite:///' + str(tmp_path / 'data/autoposter/autoposter.db'),
           'MP_AUTOPOSTER_DATA_DIR': str(tmp_path / 'data/autoposter'),
           'MP_AUTOPOSTER_MEDIA_ROOT': str(tmp_path / 'data/autoposter/media'),
           'MP_AUTOPOSTER_SCHEDULER_MODE': 'off', 'MP_AUTOPOSTER_AUTH_ENABLED': 'false'}
    seed = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=30)
    assert seed.returncode == 0, seed.stdout + seed.stderr
    if interrupted:
        fault = r'''
import os
from pathlib import Path
from app import character_upgrade as u
def interrupted_save(*args,**kwargs):
    raise RuntimeError('simulated interruption before activation pointer')
u._save=interrupted_save
u.apply_stopped(Path(os.environ['UPGRADE_ROOT']))
'''
        failed = subprocess.run([sys.executable, '-c', fault], env=env, capture_output=True, text=True, timeout=30)
        assert failed.returncode != 0 and 'simulated interruption' in failed.stderr
        assert not (tmp_path/'data/character-active.json').exists()
    before = {rel: (tmp_path / rel).read_bytes() for rel in ['data/bot.db', 'data/autoposter/autoposter.db', '.env', 'exports/history.md']}
    check = r'''
import os, sqlite3
from pathlib import Path
from app.character_upgrade import bootstrap, status
root=Path(os.environ['UPGRADE_ROOT'])
import subprocess
original_run=subprocess.run
def observed(*args,**kwargs):
    result=original_run(*args,**kwargs)
    if result.returncode: print(result.stderr.decode())
    return result
subprocess.run=observed
bootstrap(root)
assert status(root)['phase']=='active',status(root)
from app import db
assert db.DATA_DIR == os.environ['MP_AUTOCHAT_DATA_DIR']
from autoposter.config import settings
assert str(settings.media_root)==os.environ['MP_AUTOPOSTER_MEDIA_ROOT']
from autoposter.workspaces import character_context
from tools.character_migrate import LEGACY_ID
with character_context(LEGACY_ID):
    assert db.get_setting('system_prompt')=='Cherry identity'
    assert db.get_setting('bot_running') is False
with sqlite3.connect(settings.database_url.split('///',1)[1]) as conn:
    assert conn.execute('SELECT name,planning_enabled,chat_enabled FROM character').fetchall()==[(os.environ['EXISTING_NAME'],0,0)]
bootstrap(root)
from app import deployment, poller
from tools import backup
backup.backup=lambda *args,**kwargs:None
deployment.pending=lambda:False
poller.start=lambda:None
poller.stop=lambda:None
from app.main import app
from fastapi.testclient import TestClient
with TestClient(app) as client:
    for i in range(5):
        response=client.post('/api/v1/characters',json={'name':'Synthetic '+str(i)})
        assert response.status_code==201,response.text
        assert response.json()['planning_enabled'] is False
        assert response.json()['chat_enabled'] is False
    owners=client.get('/api/v1/characters').json()
    assert len(owners)==6,owners
    for owner in owners:
        identity=owner['id']
        assert client.get('/autoposter/?character_id='+identity).status_code==200
        assert client.get('/settings/shared?section=autochat&character_id='+identity).status_code==200
        with character_context(identity):
            assert db.get_tokens() is None
            assert db.get_setting('system_prompt')==('Cherry identity' if identity==LEGACY_ID else db.DEFAULT_SETTINGS['system_prompt'])
print('ACTIVATED_COPY_SIX_CHARACTERS_REAL_LIFESPAN')
'''
    result = subprocess.run([sys.executable, '-c', check], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ACTIVATED_COPY' in result.stdout
    assert all((tmp_path / rel).read_bytes() == data for rel, data in before.items())
    smoke = subprocess.run([sys.executable, 'tools/release_smoke.py', '--source', str(tmp_path)],
                           env=env, capture_output=True, text=True, timeout=60)
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
    assert '"characters_verified": 6' in smoke.stdout


def test_gui_stages_and_cancels_without_service_or_database_write(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from autoposter.api import system
    from autoposter.deps import require_admin
    from app import character_upgrade
    monkeypatch.setattr(character_upgrade, 'ROOT', tmp_path, raising=False)
    from app import helper_readiness
    monkeypatch.setattr(helper_readiness, 'readiness', lambda root: {'ready':True})
    api = FastAPI()
    api.include_router(system.router, prefix='/api/v1')
    api.dependency_overrides[require_admin] = lambda: object()
    with TestClient(api) as client:
        assert client.get('/api/v1/system/character-upgrade').json()['phase'] == 'legacy'
        bad = client.post('/api/v1/system/character-upgrade', json={'name':'Cherry','confirmed':False})
        assert bad.status_code == 422
        good = client.post('/api/v1/system/character-upgrade', json={'name':'Cherry','confirmed':True})
        assert good.status_code == 200,good.text
        assert good.json()['phase'] == 'pending_restart'
        assert not (tmp_path/'data/bot.db').exists()
        assert client.delete('/api/v1/system/character-upgrade').status_code == 200
        assert client.get('/api/v1/system/character-upgrade').json()['phase'] == 'legacy'


def test_software_update_maintenance_defers_pending_data_migration(tmp_path, monkeypatch):
    from app import character_upgrade as u
    u.stage(tmp_path, name='Cherry', confirmed=True)
    (tmp_path/'data/deployment-pending').touch()
    monkeypatch.setattr(u.subprocess, 'run', lambda *a,**k: pytest.fail('Migration during software update'))
    u.bootstrap(tmp_path)
    assert u.status(tmp_path)['phase']=='pending_restart'


def test_open_legacy_writer_refuses_before_copy(tmp_path, monkeypatch):
    import sqlite3
    from app import character_upgrade as u
    from autoposter.config import settings
    (tmp_path/'data/media').mkdir(parents=True)
    post=tmp_path/'data/post.db'
    chat=tmp_path/'data/bot.db'
    conn=sqlite3.connect(chat)
    conn.execute('CREATE TABLE marker(value TEXT)');conn.commit()
    post.write_bytes(chat.read_bytes())
    monkeypatch.setattr(settings,'database_url','sqlite+aiosqlite:///'+str(post))
    monkeypatch.setattr(settings,'media_root',tmp_path/'data/media')
    monkeypatch.setattr(settings,'data_dir',tmp_path/'data')
    monkeypatch.setattr(settings,'scheduler_mode','off')
    u.stage(tmp_path,name='Cherry',confirmed=True)
    try:
        with pytest.raises(ValueError,match='open by a process'):
            u.apply_stopped(tmp_path)
        assert not (tmp_path/'data/character-active.json').exists()
        assert not (tmp_path/'data/character-releases').exists()
    finally:
        conn.close()
