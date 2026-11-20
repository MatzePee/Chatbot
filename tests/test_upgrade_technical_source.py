"""Explicit technical-secret conflict choices through real staging/copy paths."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

@pytest.mark.parametrize('source', ['autochat','autopost'])
def test_real_assembled_upgrade_api_records_explicit_source(tmp_path,monkeypatch,source):
    from autoposter.main import app
    from autoposter.deps import require_admin
    from app import character_upgrade as upgrade,helper_readiness
    from app.setting_scope import GLOBAL_TECHNICAL_KEYS
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','0')
    monkeypatch.setattr(upgrade,'ROOT',tmp_path)
    monkeypatch.setattr(helper_readiness,'readiness',lambda root:{'ready':True})
    monkeypatch.setitem(app.dependency_overrides,require_admin,lambda:object())
    client=TestClient(app)
    result=client.post('/api/v1/system/character-upgrade',json={'name':'Cherry','confirmed':True,'technical_source':source})
    assert result.status_code==200,result.text
    expected={key:source for key in GLOBAL_TECHNICAL_KEYS}
    assert upgrade.status(tmp_path)['technical_sources']==expected
    assert client.get('/api/v1/system/character-upgrade').json()['technical_sources']==expected


def test_staging_source_choice_is_validated_and_immutable(tmp_path):
    from app import character_upgrade as upgrade
    with pytest.raises(ValueError):
        upgrade.stage(tmp_path,name='Cherry',confirmed=True,technical_sources={'fanvue_client_secret':'autochat'})
    assert not (tmp_path/'data/character-upgrade.json').exists()
    first=upgrade.stage(tmp_path,name='Cherry',confirmed=True,technical_sources={'github_token':'autochat'})
    with pytest.raises(ValueError):
        upgrade.stage(tmp_path,name='Cherry',confirmed=True,technical_sources={'github_token':'autopost'})
    assert upgrade.status(tmp_path)==first


@pytest.mark.parametrize('source',['autochat','autopost','none'])
def test_confirmed_copy_uses_explicit_source_and_keeps_originals(tmp_path,source):
    repo=Path(__file__).parents[1]
    env={**os.environ,'UPGRADE_ROOT':str(tmp_path),'CHOSEN_SOURCE':source,
         'MP_CHARACTERS_ENABLED':'0','MP_AUTOCHAT_DATA_DIR':str(tmp_path/'data'),
         'MP_AUTOPOSTER_DATABASE_URL':'sqlite+aiosqlite:///'+str(tmp_path/'data/autoposter/autoposter.db'),
         'MP_AUTOPOSTER_DATA_DIR':str(tmp_path/'data/autoposter'),
         'MP_AUTOPOSTER_MEDIA_ROOT':str(tmp_path/'data/autoposter/media'),
         'MP_AUTOPOSTER_SECRET_KEY':'synthetic-migration-key-not-a-real-secret-0123456789',
         'MP_AUTOPOSTER_SCHEDULER_MODE':'off'}
    code=r'''
import os,json,hashlib,sqlite3
from pathlib import Path
from sqlalchemy import create_engine
from autoposter.db import Base
from autoposter.models import AppSetting
from autoposter.crypto import encrypt
from app import db
root=Path(os.environ['UPGRADE_ROOT']);(root/'data/autoposter/media').mkdir(parents=True,exist_ok=True)
db.DATA_DIR=str(root/'data');db.DB_PATH=str(root/'data/bot.db');db._seed_from_env=lambda:None
db.init_db();db.set_setting('github_token','synthetic-chat-token');db._conn.close();db._conn=None
engine=create_engine('sqlite:///'+str(root/'data/autoposter/autoposter.db'));Base.metadata.create_all(engine)
with engine.begin() as c:c.execute(AppSetting.__table__.insert().values(key='integrations',value={'github_token':encrypt('synthetic-post-token')}))
engine.dispose()
paths=[root/'data/bot.db',root/'data/autoposter/autoposter.db']
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
from app.character_upgrade import stage,apply_stopped,status
choice=os.environ['CHOSEN_SOURCE']
stage(root,name='Cherry',confirmed=True,technical_sources={'github_token':choice} if choice!='none' else {})
if choice=='none':
    try:apply_stopped(root)
    except ValueError as exc:assert str(exc)=='Technical setting conflict: github_token'
    else:raise AssertionError('Conflict accepted without an explicit choice')
    assert not (root/'data/character-active.json').exists()
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items())
    print('NO_IMPLICIT_SOURCE_CHOICE');raise SystemExit(0)
apply_stopped(root)
state=status(root);assert state['phase']=='active'
active=root/'data/character-releases'/state['id']/'active'
from app.setting_scope import decode_technical_value
with sqlite3.connect(active/'bot.db') as c:value=decode_technical_value('github_token',json.loads(c.execute("select value from settings where key='github_token'").fetchone()[0]))
assert value==('synthetic-chat-token' if choice=='autochat' else 'synthetic-post-token')
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items())
print('EXPLICIT_SOURCE_COPY_VERIFIED')
'''
    result=subprocess.run([sys.executable,'-B','-c',code],cwd=repo,env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    assert ('NO_IMPLICIT_SOURCE_CHOICE' if source=='none' else 'EXPLICIT_SOURCE_COPY_VERIFIED') in result.stdout
