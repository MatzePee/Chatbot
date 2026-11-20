import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from autoposter.db import Base
from autoposter import models
from autoposter.workspaces import WorkspaceAsyncSession, character_context

@pytest.mark.asyncio
async def test_actual_claims_six_owners_and_heartbeats(db):
    from autoposter.services import x_post_metrics as x, visibility_aging as aging, post_retention as retention, ppv_restore as ppv
    now=datetime(2026, 11, 16, 23, 1, tzinfo=timezone.utc)
    owners=[models.Character(name=f'Owner {i}') for i in range(6)]
    db.add_all(owners);await db.commit()
    for owner in owners:
        with character_context(owner.id):
            async with WorkspaceAsyncSession(db.bind, expire_on_commit=False) as s:
                token=await x.claim(s,None,now)
                assert token
                await x.fail(s,token)
                for service in (aging,retention):
                    token,_=await service.claim(s,'same-account',now,False)
                    assert token
                    await service.heartbeat(s,'same-account',token,lambda: now)
                    assert (await service.claim(s,'same-account',now,False))[0] is None
                token=await ppv.claim(s,'same-account',now)
                assert token
                await ppv.heartbeat(s,'same-account',token,now)
                assert await ppv.claim(s,'same-account',now) is None

@pytest.mark.asyncio
async def test_actual_generate_plan_via_dispatch(db, monkeypatch):
    from contextlib import asynccontextmanager
    from autoposter import jobs
    from autoposter.character_dispatch import invoke
    from autoposter.services import appconfig
    owner=models.Character(name='Plan',planning_enabled=True)
    db.add(owner); await db.commit()
    @asynccontextmanager
    async def scope():
        async with WorkspaceAsyncSession(db.bind,expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except BaseException:
                await s.rollback(); raise
    monkeypatch.setattr(jobs,'session_scope',scope)
    monkeypatch.setattr('autoposter.db.engine',db.bind)
    monkeypatch.setattr('autoposter.db.SessionLocal',async_sessionmaker(db.bind,class_=WorkspaceAsyncSession,expire_on_commit=False))
    from tests.autoposter.test_planner_generation import generated_text
    from tests.autoposter.test_duplicates import make_channel
    generated=AsyncMock(return_value=generated_text())
    monkeypatch.setattr(jobs.planner.llm,'generate_post_text',generated)
    monkeypatch.setattr(jobs.planner,'typed_slots',lambda *a:[(datetime.now(timezone.utc)+timedelta(hours=1),'text')])
    with character_context(owner.id):
        async with scope() as s:
            s.add(models.AppSetting(key='runtime',value={'global_pause':False,'dry_run':False}))
            channel=await make_channel(s,'Plan')
            channel.auto_plan_enabled=True
            channel.policy.posts_per_day=1
            await appconfig.set_many(s,{'auto_plan_enabled':True})
        result=await invoke(owner.id,'generate_plan',jobs.generate_plan)
        assert result == {'created':1,'gaps':0}
        assert 'skipped' in await invoke(owner.id,'generate_plan',jobs.generate_plan)
        assert generated.await_count == 1

@pytest.mark.asyncio
async def test_actual_follower_claim_cas_and_throttle(db, monkeypatch):
    from autoposter.services import followers
    from autoposter.adapters.x import XAdapter
    monkeypatch.setattr(followers.credentials,'to_credentials',lambda c: SimpleNamespace(raw_meta={}))
    fetch=AsyncMock(return_value=42);monkeypatch.setattr(XAdapter,'fetch_followers',fetch)
    owner=models.Character(name='Followers');db.add(owner);await db.commit()
    now=datetime(2026,11,16,6,tzinfo=timezone.utc)
    with character_context(owner.id):
        async with WorkspaceAsyncSession(db.bind,expire_on_commit=False) as s:
            cred=models.ChannelCredential(platform='x',external_user_id='same-account');s.add(cred);await s.flush()
            s.add(models.Channel(platform='x',display_name='X',credential_id=cred.id,is_active=True));await s.commit()
            assert await followers.collect(s,now) == 1
            await s.commit()
            assert await followers.collect(s,now+timedelta(minutes=1)) == 0
            row=await s.get(models.AppSetting,'x_followers:same-account')
            assert row.value['days']['2026-11-16'] == 42
            assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_owned_coordination_refuses_ownerless_multicharacter_session(db,monkeypatch):
    from autoposter.services.owned_settings import locked_setting
    from autoposter.workspaces import CharacterBoundaryError
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    with pytest.raises(CharacterBoundaryError,match='context required'):
        await locked_setting(db,'auto_plan_daily_gate')


@pytest.mark.asyncio
async def test_file_sqlite_claim_race_and_rollback(tmp_path):
    from autoposter.services import x_post_metrics as x
    from autoposter.services.owned_settings import locked_setting, compare_and_set
    from autoposter.workspaces import CharacterBoundaryError
    from fastapi import HTTPException
    from sqlalchemy import text,update
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'claims.db'))
    async with engine.begin() as c:await c.run_sync(Base.metadata.create_all)
    maker=async_sessionmaker(engine,expire_on_commit=False,class_=WorkspaceAsyncSession)
    async with async_sessionmaker(engine,expire_on_commit=False)() as s:
        owner=models.Character(name='Race');s.add(owner);await s.commit();owner_id=owner.id
    now=datetime(2026,11,16,23,1,tzinfo=timezone.utc)
    async def claim():
        with character_context(owner_id):
            async with maker() as s:
                try:return await x.claim(s,None,now)
                except HTTPException as exc:
                    assert exc.status_code==409
                    return None
    try:
        results=await asyncio.gather(claim(),claim())
        assert sum(r is not None for r in results)==1
        with character_context(owner_id):
            async with maker() as s:
                row=await locked_setting(s,'auto_plan_daily_gate')
                row.value={'completed_day':'must-rollback'}
                await s.flush();await s.rollback()
            async with maker() as s:
                row=await locked_setting(s,'auto_plan_daily_gate')
                assert row.value=={}
                # A stale result cannot overwrite the durable current claim.
                result=await compare_and_set(s,'x_top_posts_collection','token','wrong',{'wrong':True})
                assert result.rowcount==0
                for operation in (lambda:s.execute(text('select 1')),
                                  lambda:s.execute(update(models.AppSetting).values(value={})),
                                  lambda:locked_setting(s,'integrations')):
                    with pytest.raises(CharacterBoundaryError):await operation()
                await s.rollback()
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_actual_lease_berlin_day_and_expiry(db):
    from autoposter.services import visibility_aging as aging,post_retention as retention
    owner=models.Character(name='Berlin');db.add(owner);await db.commit()
    now=datetime(2026,11,16,23,1,tzinfo=timezone.utc) # Nov 17 Berlin
    with character_context(owner.id):
        async with WorkspaceAsyncSession(db.bind,expire_on_commit=False) as s:
            for service in (aging,retention):
                token,_=await service.claim(s,'account',now,False)
                row=await s.get(models.AppSetting,service.PREFIX+'account')
                row.value={**row.value,'lease_until':None,'completed_slot':'2026-11-17'}
                await s.commit()
                assert (await service.claim(s,'account',now,False))[1]=='already_done'
                token,_=await service.claim(s,'account',now+timedelta(days=1),False)
                assert token
                replacement,_=await service.claim(s,'account',now+timedelta(days=1,minutes=6),False)
                assert replacement and replacement != token


@pytest.mark.asyncio
async def test_real_maintenance_processes_complete_owned_cas(db,monkeypatch):
    import uuid
    from app import db as chat
    from autoposter.services import visibility_aging as aging,post_retention as retention,ppv_restore as ppv,credentials,runtime
    from autoposter.adapters.fanvue import FanvueAdapter
    from autoposter.adapters.base import Credentials
    from autoposter.config import settings
    owner=models.Character(name='Maintenance');db.add(owner);await db.commit()
    account=str(uuid.uuid4())
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    monkeypatch.setattr(settings,'post_retention_active',True)
    async def creds(*a):return Credentials('synthetic',external_user_id=account,scopes=['read:post','write:post'])
    monkeypatch.setattr(credentials,'get_valid_credentials',creds)
    requests=[]
    async def request(self,cred,method,path,**kw):
        requests.append((method,path))
        assert method=='GET'
        return {'uuid':account} if path=='/users/me' else {'data':[],'nextCursor':None}
    monkeypatch.setattr(FanvueAdapter,'visibility_request',request)
    monkeypatch.setattr(FanvueAdapter,'retention_request',request)
    now=datetime(2026,11,16,23,1,tzinfo=timezone.utc)
    with character_context(owner.id):
        chat.save_tokens('synthetic','synthetic',9999999999,account_uuid=account)
        async with WorkspaceAsyncSession(db.bind,expire_on_commit=False) as s:
            s.add(models.Channel(platform='fanvue',display_name='Maintenance',fanvue_account_uuid=account,is_active=True))
            s.add(models.AppSetting(key='runtime',value={'global_pause':False,'dry_run':False}))
            await s.commit();await runtime.load(s)
            results=[await aging.run(s,now=now,manual=True),
                     await retention.process(s,days=7,delete_ppv=False,execute=True,now=now),
                     await ppv.process(s,execute=True,now=now)]
            assert all(r['status']=='ok' and r['accounts'][0]['status']=='ok' for r in results),results
            for service in (aging,retention,ppv):
                row=await s.get(models.AppSetting,service.PREFIX+account,populate_existing=True)
                assert row.value['lease_until'] is None
                assert row.value['finished_at']
    assert requests == [('GET','/users/me'),('GET','/v1/posts')]*3


