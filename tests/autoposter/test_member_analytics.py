from datetime import datetime, timezone
import pytest
import httpx
from autoposter.adapters import Credentials, AdapterError
from autoposter.adapters.telegram import TelegramAdapter

@pytest.mark.asyncio
async def test_member_count_validates_binding_and_zero(monkeypatch):
    import autoposter.adapters.telegram as module
    calls=[]
    def handler(request):
        calls.append(request.url.path.rsplit('/',1)[-1])
        return httpx.Response(200,json={'ok':True,'result':{'id':-1009876543210,'type':'channel','username':'example'} if calls[-1]=='getChat' else 0})
    monkeypatch.setattr(module,'http_client',lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    cred=Credentials('123:synthetic',raw_meta={'destination':'@example','chat_id':'-1009876543210'})
    assert await TelegramAdapter().fetch_members(cred)==0
    assert calls==['getChat','getChatMemberCount']

from autoposter.services import followers, credentials
from autoposter.models import ChannelCredential, AppSetting
from tests.autoposter.test_duplicates import make_channel

@pytest.mark.asyncio
async def test_telegram_collection_history_and_isolation(db,monkeypatch):
    channel=await make_channel(db,'Telegram');channel.platform='telegram';channel.handle='@example'
    row=ChannelCredential(platform='telegram',external_user_id='123',raw_meta={'destination':'@example','chat_id':'-1009876543210'})
    db.add(row);await db.flush();channel.credential_id=row.id;channel.last_verified_at=datetime.now(timezone.utc)
    monkeypatch.setattr(credentials,'to_credentials',lambda r:Credentials('123:synthetic',raw_meta=r.raw_meta))
    calls=[]
    async def fetch(self,cred):calls.append(1);return 0
    monkeypatch.setattr(TelegramAdapter,'fetch_members',fetch)
    now=datetime(2026,10,25,5,tzinfo=timezone.utc)
    assert await followers.collect(db,now,platform='telegram',daily_only=True)==1
    await db.commit();db.expire_all()
    assert await followers.collect(db,now,platform='telegram',daily_only=True)==0
    result=(await followers.overview(db,now,platform='telegram'))[0]
    assert result['account_id']=='-1009876543210'
    assert result['current']==0
    assert len(result['history'])==30
    assert result['history'][-1]=={'date':'2026-10-25','count':0}
    assert all(p['count'] is None for p in result['history'][:-1])
    assert await db.get(AppSetting,'x_followers:-1009876543210') is None
    channel.handle='@different';await db.flush()
    changed=(await followers.overview(db,now,platform='telegram'))[0]
    assert changed['current'] is None
    assert 'verbunden' in changed['status']
    assert await followers.collect(db,now,platform='telegram')==0
    assert calls==[1]

@pytest.mark.asyncio
async def test_daily_job_includes_telegram_without_provider_writes(db,monkeypatch):
    from contextlib import asynccontextmanager
    from autoposter import jobs
    calls=[]
    @asynccontextmanager
    async def scope():
        yield db
    async def collect(db,now=None,**kwargs):
        calls.append(kwargs);return 0
    monkeypatch.setattr(jobs,'session_scope',scope)
    monkeypatch.setattr(followers,'collect',collect)
    await jobs.collect_daily_followers(datetime(2026,10,25,5,tzinfo=timezone.utc))
    assert calls==[{'daily_only':True},{'daily_only':True,'platform':'telegram'}]

@pytest.mark.asyncio
async def test_telegram_analytics_route_is_cached_read_only(db,monkeypatch):
    from autoposter.api import dashboard
    calls=[]
    async def overview(db,**kwargs):calls.append(kwargs);return []
    monkeypatch.setattr(followers,'overview',overview)
    endpoint=next((r.endpoint for r in dashboard.router.routes if r.path.endswith('/analytics/telegram-members')),None)
    assert endpoint is not None
    assert await endpoint(db=db,_=object())==[]
    assert calls==[{'platform':'telegram'}]

@pytest.mark.asyncio
@pytest.mark.parametrize('result',[True,False,-1,'42',None,{},[42],1.5])
async def test_invalid_member_count_shapes_are_rejected(monkeypatch,result):
    async def call(self,cred,method,payload,files=None):
        return {'id':-1009876543210,'type':'channel'} if method=='getChat' else result
    monkeypatch.setattr(TelegramAdapter,'_call',call)
    with pytest.raises(AdapterError,match='ungültige'):
        await TelegramAdapter().fetch_members(Credentials('123:synthetic',raw_meta={'destination':'-1009876543210','chat_id':'-1009876543210'}))

@pytest.mark.asyncio
@pytest.mark.parametrize('chat',[{'id':-100999,'type':'channel'},{'id':-1009876543210,'type':'supergroup'},{'id':True,'type':'channel'},{'id':'-1009876543210','type':'channel'},None])
async def test_wrong_chat_never_requests_count(monkeypatch,chat):
    calls=[]
    async def call(self,cred,method,payload,files=None):calls.append(method);return chat
    monkeypatch.setattr(TelegramAdapter,'_call',call)
    with pytest.raises(AdapterError):
        await TelegramAdapter().fetch_members(Credentials('123:synthetic',raw_meta={'destination':'-1009876543210','chat_id':'-1009876543210'}))
    assert calls==['getChat']

@pytest.mark.asyncio
@pytest.mark.parametrize('code',[400,401,403,429,500])
async def test_api_ok_false_and_secret_sanitization(monkeypatch,code):
    import autoposter.adapters.telegram as module
    def handler(request):
        assert request.method=='POST'
        assert request.url.path.endswith('/getChat')
        import json
        assert json.loads(request.content)=={'chat_id':'-1009876543210'}
        return httpx.Response(200,json={'ok':False,'error_code':code,'description':'https://api.telegram.org/bot123:synthetic/secret'})
    monkeypatch.setattr(module,'http_client',lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(AdapterError) as error:
        await TelegramAdapter().fetch_members(Credentials('123:synthetic',raw_meta={'destination':'-1009876543210','chat_id':'-1009876543210'}))
    assert 'synthetic' not in str(error.value)
    assert str(code) in str(error.value)

async def telegram_account(db,monkeypatch):
    channel=await make_channel(db,'Telegram');channel.platform='telegram';channel.handle='@example';channel.last_verified_at=datetime.now(timezone.utc)
    row=ChannelCredential(platform='telegram',external_user_id='123',raw_meta={'destination':'@example','chat_id':'-1009876543210'})
    db.add(row);await db.flush();channel.credential_id=row.id;await db.commit()
    monkeypatch.setattr(credentials,'to_credentials',lambda r:Credentials('123:synthetic',raw_meta=r.raw_meta))
    return channel,row

@pytest.mark.asyncio
@pytest.mark.parametrize('six',[datetime(2026,3,29,4,tzinfo=timezone.utc),datetime(2026,10,25,5,tzinfo=timezone.utc)])
async def test_telegram_dst_throttle_aliases_failure_preserves_history(db,monkeypatch,six):
    from datetime import timedelta
    channel,row=await telegram_account(db,monkeypatch)
    alias=await make_channel(db,'Alias');alias.platform='telegram';alias.handle=channel.handle;alias.credential_id=row.id;alias.last_verified_at=channel.last_verified_at
    calls=[]
    async def fetch(self,cred):calls.append(1);return 77
    monkeypatch.setattr(TelegramAdapter,'fetch_members',fetch)
    assert await followers.collect(db,six-timedelta(seconds=1),platform='telegram')==1
    assert await followers.collect(db,six,platform='telegram',daily_only=True)==1
    await db.commit();db.expire_all()
    assert await followers.collect(db,six+timedelta(minutes=1),platform='telegram')==0
    async def failed(self,cred):calls.append(1);raise RuntimeError('secret')
    monkeypatch.setattr(TelegramAdapter,'fetch_members',failed)
    assert await followers.collect(db,six+timedelta(hours=6),platform='telegram')==0
    await db.commit();db.expire_all()
    items=await followers.overview(db,six+timedelta(hours=6),platform='telegram')
    assert len(items)==2
    assert all(i['current']==77 and 'nicht verfügbar' in i['status'] for i in items)
    assert len(calls)==3
    assert 'secret' not in str(items)

@pytest.mark.asyncio
async def test_telegram_committed_claim_other_session_and_slow_response(db,monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    from datetime import timedelta
    await telegram_account(db,monkeypatch)
    before=datetime(2026,10,25,4,59,tzinfo=timezone.utc)
    calls=[]
    async def fetch(self,cred):
        calls.append(1)
        if len(calls)==1:
            async with AsyncSession(db.bind,expire_on_commit=False) as other:
                assert (await other.get(AppSetting,'telegram_members:-1009876543210')).value['attempted_at']==before.isoformat()
                assert await followers.collect(other,before,platform='telegram')==0
                assert await followers.collect(other,before+timedelta(minutes=1),platform='telegram',daily_only=True)==1
                await other.commit()
            return 1
        return 2
    monkeypatch.setattr(TelegramAdapter,'fetch_members',fetch)
    await followers.collect(db,before,platform='telegram');await db.commit();db.expire_all()
    item=(await followers.overview(db,before+timedelta(minutes=1),platform='telegram'))[0]
    assert item['current']==2
    assert item['daily_collection']['last_slot']=='2026-10-25'
    assert len(calls)==2

@pytest.mark.asyncio
async def test_calendar_history_exact_baselines_and_original_x_data(db):
    from datetime import timedelta
    now=datetime(2028,3,1,0,tzinfo=timezone.utc)
    channel=await make_channel(db,'Original X');channel.platform='x'
    cred=ChannelCredential(platform='x',external_user_id='-1009876543210');db.add(cred);await db.flush();channel.credential_id=cred.id
    data={'days':{'2028-03-01':50,'2028-02-29':40,'2028-02-23':30,'2028-01-31':20},'sampled_at':now.isoformat(),'status':'OK'}
    db.add(AppSetting(key='x_followers:-1009876543210',value=data));await db.flush()
    item=(await followers.overview(db,now))[0]
    assert len(item['history'])==30
    assert item['history'][0]['date']=='2028-02-01'
    assert item['history'][-2]=={'date':'2028-02-29','count':40}
    assert item['days30']=={'count':20,'growth':30}
    assert item['days7']=={'count':30,'growth':20}
    assert item['yesterday']=={'count':40,'growth':10}
    assert (await db.get(AppSetting,'x_followers:-1009876543210')).value==data
    assert await followers.overview(db,now,platform='telegram')==[]

@pytest.mark.asyncio
async def test_file_sqlite_concurrent_regular_daily_claim_one_request(tmp_path,monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from autoposter.db import Base
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'metrics.db'))
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    maker=async_sessionmaker(engine,expire_on_commit=False)
    async with maker() as setup:await telegram_account(setup,monkeypatch)
    calls=[]
    async def fetch(self,cred):calls.append(1);await asyncio.sleep(0.05);return 9
    monkeypatch.setattr(TelegramAdapter,'fetch_members',fetch)
    now=datetime(2026,10,25,5,tzinfo=timezone.utc)
    async def run(daily):
        async with maker() as session:
            result=await followers.collect(session,now,platform='telegram',daily_only=daily)
            await session.commit();return result
    assert sorted(await asyncio.gather(run(False),run(True)))==[0,1]
    assert calls==[1]
    async with maker() as check:
        item=(await followers.overview(check,now,platform='telegram'))[0]
        assert item['current']==9 and item['daily_collection']['last_slot']=='2026-10-25'
    await engine.dispose()

@pytest.mark.asyncio
async def test_regular_metrics_job_collects_telegram_from_existing_shared_path(db,monkeypatch):
    from contextlib import asynccontextmanager
    from autoposter import jobs
    calls=[]
    @asynccontextmanager
    async def scope():yield db
    async def collect(db,**kwargs):calls.append(kwargs);return 0
    monkeypatch.setattr(jobs,'session_scope',scope)
    monkeypatch.setattr(followers,'collect',collect)
    assert await jobs.refresh_metrics()=={'updated':0,'follower_accounts':0}
    assert calls==[{}, {'platform':'telegram'}]

@pytest.mark.asyncio
async def test_permission_failure_has_safe_explicit_code_without_fake_zero(db,monkeypatch):
    await telegram_account(db,monkeypatch)
    async def fetch(self,cred):raise AdapterError('https://api.telegram.org/bot123:synthetic/private',status=403)
    monkeypatch.setattr(TelegramAdapter,'fetch_members',fetch)
    now=datetime(2026,10,25,5,tzinfo=timezone.utc)
    assert await followers.collect(db,now,platform='telegram')==0
    await db.commit();db.expire_all()
    item=(await followers.overview(db,now,platform='telegram'))[0]
    assert item['current'] is None and item['sampled_at'] is None
    assert '403' in item['status'] and 'synthetic' not in str(item)

@pytest.mark.asyncio
async def test_same_bot_different_channels_never_share_history(db,monkeypatch):
    first,row=await telegram_account(db,monkeypatch)
    second=await make_channel(db,'Second channel');second.platform='telegram';second.handle='-1008888888888';second.last_verified_at=first.last_verified_at
    cred=ChannelCredential(platform='telegram',external_user_id='123',raw_meta={'destination':second.handle,'chat_id':second.handle});db.add(cred);await db.flush();second.credential_id=cred.id
    async def fetch(self,cred):return 23 if cred.raw_meta['chat_id']=='-1009876543210' else 91
    monkeypatch.setattr(TelegramAdapter,'fetch_members',fetch)
    now=datetime(2026,10,25,5,tzinfo=timezone.utc)
    assert await followers.collect(db,now,platform='telegram')==2
    await db.commit();db.expire_all()
    result=await followers.overview(db,now,platform='telegram')
    assert {i['account_id']:i['current'] for i in result}=={'-1009876543210':23,'-1008888888888':91}
    assert all(i['history'][-1]['count']==i['current'] for i in result)
