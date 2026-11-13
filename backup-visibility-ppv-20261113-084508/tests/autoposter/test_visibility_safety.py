"""Synthetic-only safety, lease and calendar-slot regressions."""
import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from autoposter.db import Base
from autoposter.models import AppSetting, Channel
from autoposter.services import visibility_aging as aging, appconfig, credentials
from autoposter.config import settings
from autoposter.adapters.fanvue import FanvueAdapter
from autoposter.adapters.base import Credentials, AdapterError
from tests.autoposter.test_duplicates import make_channel

NOW=datetime(2026,10,18,12,tzinfo=timezone.utc)
POST='123e4567-e89b-12d3-a456-426614174000'

@pytest_asyncio.fixture
async def setup(db, monkeypatch):
    channel=await make_channel(db,'Safety');channel.platform='fanvue'
    await appconfig.set_many(db, {'visibility_aging_enabled':True,'visibility_aging_days':7})
    await db.commit()
    monkeypatch.setattr(settings,'dry_run',False)
    async def accounts(session):
        ch=(await session.execute(select(Channel).where(Channel.platform=='fanvue'))).scalars().first()
        return [(ch,'a'),(ch,'a'),(ch,'b')]
    async def creds(session, channel):
        return Credentials('synthetic',external_user_id='a',scopes=['read:post','write:post'])
    monkeypatch.setattr(aging,'accounts',accounts)
    monkeypatch.setattr(credentials,'get_valid_credentials',creds)
    return channel

@pytest.mark.asyncio
async def test_credential_error_never_persists_provider_secrets(db, setup, monkeypatch):
    async def failed(*a):raise AdapterError('Bearer SECRET synthetic raw provider body',status=401)
    monkeypatch.setattr(credentials,'get_valid_credentials',failed)
    await aging.run(db,now=NOW,manual=True)
    report=await aging.status(db)
    assert 'SECRET' not in str(report)
    assert 'Zugangsdaten' in str(report)

@pytest.mark.parametrize('boundary', [datetime(2026,3,28,23,tzinfo=timezone.utc),datetime(2026,3,29,22,tzinfo=timezone.utc),datetime(2026,10,24,22,tzinfo=timezone.utc),datetime(2026,10,25,23,tzinfo=timezone.utc)])
@pytest.mark.asyncio
async def test_berlin_midnight_slot_once_and_crash_recovery(db, setup, boundary):
    token,_=await aging.claim(db,'calendar',boundary-timedelta(seconds=1),False)
    assert token
    row=await db.get(AppSetting,aging.PREFIX+'calendar')
    value=dict(row.value);value.update(lease_until=None,completed_slot=(boundary-timedelta(seconds=1)).astimezone(aging.ZONE).date().isoformat());row.value=value;await db.commit()
    token,_=await aging.claim(db,'calendar',boundary,False)
    assert token, 'UTC time must not replace Berlin calendar midnight'
    assert (await aging.claim(db,'calendar',boundary+timedelta(seconds=1),True))[1]=='running'
    later=boundary+timedelta(minutes=6)
    new_token,_=await aging.claim(db,'calendar',later,False)
    assert new_token and new_token!=token
    with pytest.raises(AdapterError,match='Sperre'):
        await aging.heartbeat(db,'calendar',token,lambda:later)
    assert (await aging.status(db))['accounts'][0]['changed'] == 0

@pytest.mark.asyncio
async def test_separate_sessions_concurrent_manual_nightly_claim(tmp_path):
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'lease.db'))
    async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
    maker=async_sessionmaker(engine,expire_on_commit=False)
    async def run(manual):
        async with maker() as session:return await aging.claim(session,'same-account',NOW,manual)
    try:
        results=await asyncio.gather(run(True),run(False))
        assert sum(bool(r[0]) for r in results)==1
        assert [r[1] for r in results if r[0] is None]==['running']
        async with maker() as session:
            assert (await aging.claim(session,'other-account',NOW,False))[0]
    finally:await engine.dispose()

@pytest.mark.asyncio
async def test_failure_retry_scope_isolation_readback_and_cursor_cycle(db, setup, monkeypatch):
    calls=[]
    async def request(self,cred,method,path,**kw):
        calls.append((method,path))
        if path=='/users/me':return {'uuid':'a'}
        if path=='/v1/posts':return {'data':[{'uuid':POST,'audience':'followers-and-subscribers','publishedAt':'2020-01-01T00:00:00Z','publishAt':None}], 'nextCursor':'cycle'}
        return {'uuid':POST,'audience':'followers-and-subscribers','publishedAt':'2020-01-01T00:00:00Z','publishAt':None}
    monkeypatch.setattr(FanvueAdapter,'visibility_request',request)
    result=await aging.run(db,now=NOW,manual=True)
    assert len(result['accounts'])==2
    first,second=result['accounts']
    assert first['status']=='error' and first['changed']==0
    assert 'bestätigt' in str(first) and 'Cursor' in str(first)
    assert second['status']=='error' and 'anderen Konto' in str(second)
    assert sum(m=='PATCH' for m,p in calls)==1
    assert (await aging.run(db,now=NOW+timedelta(minutes=1)))['accounts'][0]['status']=='retry_wait'
    assert (await aging.claim(db,'a',NOW+timedelta(minutes=15),False))[0]

@pytest.mark.asyncio
async def test_pause_during_walk_prevents_next_mutation(db, setup, monkeypatch):
    calls=[]
    async def request(self,cred,method,path,**kw):
        calls.append(method)
        if path=='/users/me':return {'uuid':'a'}
        if path=='/v1/posts':
            monkeypatch.setattr(settings,'global_pause',True)
            return {'data':[{'uuid':POST,'audience':'followers-and-subscribers','publishedAt':'2020-01-01T00:00:00Z'}],'nextCursor':None}
        return {'uuid':POST,'audience':'followers-and-subscribers','publishedAt':'2020-01-01T00:00:00Z'}
    monkeypatch.setattr(FanvueAdapter,'visibility_request',request)
    await aging.run(db,now=NOW,manual=True)
    assert 'PATCH' not in calls
    assert 'Notfall-Stopp' in str(await aging.status(db))

@pytest.mark.asyncio
async def test_failed_manual_after_success_does_not_hide_retry(db, setup, monkeypatch):
    token,value=await aging.claim(db,'a',NOW,False)
    row=await db.get(AppSetting,aging.PREFIX+'a')
    row.value={**value,'lease_until':None,'completed_slot':NOW.astimezone(aging.ZONE).date().isoformat()};await db.commit()
    async def fail(*a):raise AdapterError('synthetic')
    monkeypatch.setattr(credentials,'get_valid_credentials',fail)
    await aging.run(db,now=NOW+timedelta(minutes=1),manual=True)
    assert (await aging.claim(db,'a',NOW+timedelta(minutes=2),False))[1]=='retry_wait'
    assert (await aging.claim(db,'a',NOW+timedelta(minutes=16),False))[0]

@pytest.mark.asyncio
async def test_manual_crash_after_completed_day_is_recoverable(db, setup):
    token,value=await aging.claim(db,'recover',NOW,False)
    row=await db.get(AppSetting,aging.PREFIX+'recover')
    row.value={**value,'lease_until':None,'completed_slot':NOW.astimezone(aging.ZONE).date().isoformat()};await db.commit()
    assert (await aging.claim(db,'recover',NOW+timedelta(minutes=1),True))[0]
    assert (await aging.claim(db,'recover',NOW+timedelta(minutes=7),False))[0]

@pytest.mark.asyncio
async def test_disabled_dryrun_pause_never_contact_provider(db, setup, monkeypatch):
    async def fail(*a,**kw):raise AssertionError('Provider traffic forbidden')
    monkeypatch.setattr(FanvueAdapter,'visibility_request',fail)
    await appconfig.set_many(db,{'visibility_aging_enabled':False});await db.commit()
    assert (await aging.run(db,now=NOW))['status']=='disabled'
    monkeypatch.setattr(settings,'dry_run',True)
    assert (await aging.run(db,now=NOW,manual=True))['status']=='dry_run'
    monkeypatch.setattr(settings,'global_pause',True)
    assert (await aging.run(db,now=NOW,manual=True))['status']=='paused'

def test_expired_post_is_not_currently_published():
    assert not aging.eligible({'audience':'followers-and-subscribers','publishedAt':'2020-01-01T00:00:00Z','expiresAt':'2026-01-01T00:00:00Z'}, NOW, 7)


@pytest.mark.parametrize('published,scheduled,audience,expected',[
 ('2026-10-11T12:00:00Z',None,'followers-and-subscribers',True),
 ('2026-10-11T12:00:01Z',None,'followers-and-subscribers',False),
 ('2026-10-11T14:00:00+02:00',None,'followers-and-subscribers',True),
 ('2026-10-11T12:00:00',None,'followers-and-subscribers',False),
 (None,None,'followers-and-subscribers',False),
 ('2020-01-01T00:00:00Z','2027-01-01T00:00:00Z','followers-and-subscribers',False),
 ('2020-01-01T00:00:00Z',None,'subscribers',False),
])
def test_actual_publication_required(published,scheduled,audience,expected):
    assert aging.eligible({'publishedAt':published,'publishAt':scheduled,'audience':audience,'createdAt':'2000-01-01T00:00:00Z'},NOW,7) is expected
