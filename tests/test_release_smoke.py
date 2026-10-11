"""Run the actual release verifier in isolated processes with synthetic databases."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]

SEED = '''
import asyncio, os
from pathlib import Path
from app import db
source=Path(os.environ['SMOKE_SOURCE'])
db.DATA_DIR=str(source/'data'); db.DB_PATH=str(source/'data/bot.db'); db._seed_from_env=lambda: None
db.init_db(); db._conn.close()
from autoposter.db import Base, engine, SessionLocal
import autoposter.models
from autoposter.models import AppSetting, Notification
async def seed():
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        from autoposter.deps import get_system_user
        await get_system_user(session)
        session.add(AppSetting(key='runtime',value={'dry_run':True,'global_pause':True}))
        session.add(Notification(level='warning',title='Bildvorrat knapp: obsolete',body='preserve me',entity='channel',entity_id='missing',is_read=False))
        await session.commit()
    await engine.dispose()
asyncio.run(seed())
if os.environ['SMOKE_MODE']!='fresh':
    (source/'data/autoposter/.creatorpilot-initialized').touch()
'''

VERIFY = '''
import os, sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
from tools.release_smoke import run, fingerprint
original_get=TestClient.get
saved=[]
def get(self,path,*args,**kwargs):
    from autoposter.services import problems
    if not saved: saved.append(problems.check_if_due)
    if os.environ['SMOKE_MODE']=='route_error' and path=='/api/v1/dashboard':
        raise RuntimeError('synthetic dashboard error')
    return original_get(self,path,*args,**kwargs)
TestClient.get=get
mode=os.environ['SMOKE_MODE']
original_enter=TestClient.__enter__
def enter(self):
    from sqlalchemy import text
    if mode=='mutation':
        from autoposter import schema_sync
        original_sync=schema_sync.sync
        async def mutate(engine):
            result=await original_sync(engine)
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE app_setting SET value='{}' WHERE key='runtime'"))
            return result
        schema_sync.sync=mutate
    elif mode=='dashboard_mutation':
        from autoposter.services import inventory
        original_overview=inventory.overview
        async def mutate(db):
            await db.execute(text("UPDATE app_setting SET value='{}' WHERE key='runtime'"))
            return await original_overview(db)
        inventory.overview=mutate
    return original_enter(self)
TestClient.__enter__=enter
try:
    run(Path(os.environ['SMOKE_SOURCE']))
finally:
    from autoposter.services import problems
    assert saved and problems.check_if_due is saved[0], 'Dashboard reconciliation was not restored'
# Verify normal dashboard requests still persist reconciliation after the smoke.
import asyncio
from autoposter.db import Base, engine, SessionLocal
from autoposter.models import AppSetting, Notification
from autoposter.api.dashboard import dashboard
async def normal():
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        db.add(Notification(level='warning',title='Bildvorrat knapp: obsolete',body='normal request',entity='channel',entity_id='missing',is_read=False))
        await db.commit()
        await dashboard(db,None)
        await db.commit()
        assert await db.get(AppSetting,'problems_check') is not None
        from sqlalchemy import select
        note=(await db.execute(select(Notification))).scalars().one()
        assert note.is_read
    await engine.dispose()
# run() has removed the disposable directory, so give the engine a new fixture.
Path(engine.url.database).parent.mkdir(parents=True,exist_ok=True)
asyncio.run(normal())
print('NORMAL_DASHBOARD_RECONCILIATION_PRESERVED')
'''


def exercise(tmp_path, mode):
    source = tmp_path / 'source'
    (source / 'data/autoposter').mkdir(parents=True)
    env = {**os.environ, 'SMOKE_SOURCE': str(source), 'SMOKE_MODE': mode,
           'MP_PREVIEW': '0', 'MP_AUTOPOSTER_SECRET_KEY': 'synthetic-smoke-test-only',
           'MP_AUTOPOSTER_DATABASE_URL': 'sqlite+aiosqlite:///' + str(source / 'data/autoposter/autoposter.db'),
           'MP_AUTOPOSTER_DATA_DIR': str(source / 'data/autoposter'),
           'MP_AUTOPOSTER_MEDIA_ROOT': str(source / 'data/autoposter/media'),
           'MP_AUTOPOSTER_SCHEDULER_MODE': 'off', 'MP_AUTOPOSTER_AUTH_ENABLED': 'false'}
    seed = subprocess.run([sys.executable, '-c', SEED], cwd=ROOT, env=env, capture_output=True, text=True)
    assert seed.returncode == 0, seed.stdout + seed.stderr
    # Let run() select its disposable database before importing any AutoPost modules.
    for key in ('MP_AUTOPOSTER_DATABASE_URL', 'MP_AUTOPOSTER_DATA_DIR', 'MP_AUTOPOSTER_MEDIA_ROOT'):
        env.pop(key)
    return subprocess.run([sys.executable, '-c', VERIFY], cwd=ROOT, env=env, capture_output=True, text=True)


@pytest.mark.parametrize('mode', ['normal', 'fresh'])
def test_existing_autopost_survives_smoke_dashboard_probe(tmp_path, mode):
    result = exercise(tmp_path, mode)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"ok": true' in result.stdout
    assert 'NORMAL_DASHBOARD_RECONCILIATION_PRESERVED' in result.stdout


@pytest.mark.parametrize('mode', ['mutation', 'dashboard_mutation'])
def test_smoke_still_rejects_preexisting_data_mutation(tmp_path, mode):
    result = exercise(tmp_path, mode)
    assert result.returncode != 0
    assert 'Existing AutoPost data changed.' in result.stderr, result.stdout + result.stderr
    assert 'Dashboard reconciliation was not restored' not in result.stderr


def test_dashboard_override_is_restored_after_request_failure(tmp_path):
    result = exercise(tmp_path, 'route_error')
    assert result.returncode != 0
    assert 'synthetic dashboard error' in result.stderr
    assert 'Dashboard reconciliation was not restored' not in result.stderr
