"""Run the actual release verifier in isolated processes with synthetic databases."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]

SEED = '''
import asyncio, hashlib, os
from pathlib import Path
from app import db
source=Path(os.environ['SMOKE_SOURCE'])
db.DATA_DIR=str(source/'data'); db.DB_PATH=str(source/'data/bot.db'); db._seed_from_env=lambda: None
db.init_db(); db._conn.close()
from autoposter.db import Base, engine, SessionLocal
import autoposter.models
from autoposter.models import AppSetting, Notification, MediaSet, Channel, MediaAsset
async def seed():
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        from autoposter.deps import get_system_user
        await get_system_user(session)
        session.add(AppSetting(key='runtime',value={'dry_run':True,'global_pause':True}))
        session.add(Notification(level='warning',title='Bildvorrat knapp: obsolete',body='preserve me',entity='channel',entity_id='missing',is_read=False))
        if os.environ['SMOKE_MODE'] in ('legacy', 'legacy_mutation'):
            session.add(MediaSet(name='Legacy set', nsfw_level='suggestive'))
            session.add(Channel(platform='x', display_name='Legacy channel', nsfw_level='suggestive'))
        if os.environ['SMOKE_MODE'] in ('media_mismatch', 'media_mutation', 'media_missing', 'media_match'):
            original=source/'data/autoposter/media/synthetic/original.jpg'
            original.parent.mkdir(parents=True,exist_ok=True)
            if os.environ['SMOKE_MODE']!='media_missing':
                original.write_bytes(b'pre-existing media with stale stored digest')
            digest=hashlib.sha256(original.read_bytes()).hexdigest() if os.environ['SMOKE_MODE']=='media_match' else '0'*64
            session.add(MediaAsset(filename='historical.jpg',storage_path='synthetic/original.jpg',sha256=digest,bytes=43))
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
    result=original_get(self,path,*args,**kwargs)
    if os.environ['SMOKE_MODE']=='media_mutation' and path=='/api/v1/dashboard':
        media=Path(os.environ['SMOKE_SOURCE'])/'data/autoposter/media/synthetic/original.jpg'
        media.write_bytes(b'media changed during update verification')
    return result
TestClient.get=get
mode=os.environ['SMOKE_MODE']
original_enter=TestClient.__enter__
def enter(self):
    from sqlalchemy import text
    if mode in ('mutation', 'legacy_mutation'):
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


@pytest.mark.parametrize('mode', ['normal', 'fresh', 'legacy'])
def test_existing_autopost_survives_smoke_dashboard_probe(tmp_path, mode):
    result = exercise(tmp_path, mode)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"ok": true' in result.stdout
    assert 'NORMAL_DASHBOARD_RECONCILIATION_PRESERVED' in result.stdout


@pytest.mark.parametrize('mode,expected', [('media_match', 0), ('media_mismatch', 1)])
def test_existing_media_is_reported_without_blocking_update(tmp_path, mode, expected):
    result = exercise(tmp_path, mode)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'"preexisting_media_mismatches": {expected}' in result.stdout


def test_smoke_still_rejects_missing_media(tmp_path):
    result = exercise(tmp_path, 'media_missing')
    assert result.returncode != 0
    assert 'Existing AutoPost media missing.' in result.stderr


def test_smoke_rejects_media_changed_during_verification(tmp_path):
    result = exercise(tmp_path, 'media_mutation')
    assert result.returncode != 0
    assert 'Existing AutoPost media changed.' in result.stderr, result.stdout + result.stderr


@pytest.mark.parametrize('mode', ['mutation', 'dashboard_mutation', 'legacy_mutation'])
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


def test_expected_migration_fingerprint_is_narrow_and_directional():
    import sqlite3
    from tools.release_smoke import fingerprint
    with sqlite3.connect(':memory:') as conn:
        for table in ('channel', 'media_asset', 'media_set', 'unrelated'):
            conn.execute(f'CREATE TABLE {table} (nsfw_level TEXT, label TEXT)')
            conn.execute(f"INSERT INTO {table} VALUES ('suggestive', 'keep')")
            expected = fingerprint(conn, table, expected_migrations=True)
            unchanged = fingerprint(conn, table)
            assert (expected != unchanged) == (table != 'unrelated')
            conn.execute(f"UPDATE {table} SET nsfw_level='explicit'")
            assert (fingerprint(conn, table) == expected) == (table != 'unrelated')
            conn.execute(f"UPDATE {table} SET label='changed'")
            assert fingerprint(conn, table) != expected
