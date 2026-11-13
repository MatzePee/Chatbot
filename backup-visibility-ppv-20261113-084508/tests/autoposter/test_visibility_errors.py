"""Synthetic HTTP regressions: no provider calls or production data."""
from contextlib import asynccontextmanager
import httpx
import pytest
from autoposter.adapters.base import Credentials, AdapterError
from autoposter.adapters.fanvue import FanvueAdapter

POST='123e4567-e89b-12d3-a456-426614174000'

@pytest.mark.asyncio
@pytest.mark.parametrize('status,hint', [(404,'Ressource nicht gefunden'),(401,'Token'),(403,'Berechtigung'),(410,'API-Version'),(422,'Anfrage abgelehnt')])
async def test_visibility_http_errors_are_specific_and_sanitized(monkeypatch,status,hint):
    import autoposter.adapters.fanvue as module
    calls=[]
    def handler(req):
        calls.append((req.method,req.url.path))
        return httpx.Response(status,json={'error':'Bearer SECRET raw provider body'})
    @asynccontextmanager
    async def client(**kw):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:yield c
    monkeypatch.setattr(module,'http_client',client)
    monkeypatch.setattr(module,'cfg',lambda key:'2025-06-26')
    with pytest.raises(AdapterError) as caught:
        await FanvueAdapter().visibility_request(Credentials('synthetic'),'GET','/v1/posts/'+POST)
    assert calls==[('GET','/v1/posts/'+POST)]
    assert caught.value.status==status
    text=str(caught.value)
    assert hint in text
    assert 'GET /v1/posts/'+POST in text
    assert 'SECRET' not in text and 'raw provider' not in text
    if status==404:assert 'Token' not in text and 'Scopes' not in text and 'API-Version' not in text
from tests.autoposter.test_visibility_safety import setup, NOW
from autoposter.services import visibility_aging as aging

@pytest.mark.asyncio
@pytest.mark.parametrize('stage,methods,state', [
    ('precheck',['GET'],'not_attempted'),
    ('patch',['GET','PATCH'],'update_failed'),
    ('postcheck',['GET','PATCH','GET'],'unconfirmed'),
])
async def test_missing_resource_stage_continues_without_false_success(db,setup,monkeypatch,stage,methods,state):
    import autoposter.adapters.fanvue as module
    posts=[dict(uuid=f'123e4567-e89b-12d3-a456-{i:012d}',audience='followers-and-subscribers',publishedAt='2020-01-01T00:00:00Z') for i in range(1,5)]
    posts[-1]['audience']='subscribers'
    calls=[]; counts={}; updated=set()
    def handler(req):
        path=req.url.path; calls.append((req.method,path))
        if path=='/users/me':return httpx.Response(200,json={'uuid':'a'})
        if path=='/v1/posts':return httpx.Response(200,json={'data':posts,'nextCursor':None})
        item=next(p for p in posts if path=='/v1/posts/'+p['uuid'])
        counts[item['uuid']]=counts.get(item['uuid'],0)+1
        n=counts[item['uuid']]
        if item is posts[0] and n==len(methods):
            return httpx.Response(404,json={'error':'SECRET'})
        # A post already changed by someone else must never receive another PATCH.
        if item is posts[2]:return httpx.Response(200,json={**item,'audience':'subscribers'})
        if req.method=='PATCH':
            assert req.content==b'{"audience":"subscribers"}'
            updated.add(item['uuid'])
        return httpx.Response(200,json={**item,'audience':'subscribers' if item['uuid'] in updated else item['audience']})
    @asynccontextmanager
    async def client(**kw):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:yield c
    monkeypatch.setattr(module,'http_client',client)
    monkeypatch.setattr(module,'cfg',lambda key:'2025-06-26')
    report=(await aging.run(db,now=NOW,manual=True))['accounts'][0]
    assert report['scanned']==4 and report['changed']==1 and report['skipped']==2
    assert len(report['errors'])==1
    error=report['errors'][0]
    assert error['stage']==stage and error['http_status']==404
    assert error['method']==methods[-1] and error['path']=='/v1/posts/'+posts[0]['uuid']
    assert error['update_state']==state
    assert [m for m,p in calls if p.endswith(posts[0]['uuid'])]==methods
    assert [m for m,p in calls if p.endswith(posts[1]['uuid'])]==['GET','PATCH','GET']
    assert not any(m=='PATCH' and p.endswith(posts[2]['uuid']) for m,p in calls)
    assert not any(p.endswith(posts[3]['uuid']) for m,p in calls)
    assert report['scanned']==report['changed']+report['skipped']+len(report['errors'])
    persisted=await aging.status(db)
    assert 'SECRET' not in str(persisted)
    assert persisted['accounts'][0]['errors'][0]['stage']==stage

@pytest.mark.asyncio
@pytest.mark.parametrize('status',[401,403])
async def test_auth_failure_aborts_further_posts(db,setup,monkeypatch,status):
    calls=[]
    posts=[dict(uuid=f'123e4567-e89b-12d3-a456-{i:012d}',audience='followers-and-subscribers',publishedAt='2020-01-01T00:00:00Z') for i in [1,2]]
    async def request(self,cred,method,path,**kw):
        calls.append((method,path))
        if path=='/users/me':return {'uuid':'a'}
        if path=='/v1/posts':return {'data':posts,'nextCursor':None}
        raise AdapterError('controlled authentication failure',status=status)
    monkeypatch.setattr(FanvueAdapter,'visibility_request',request)
    report=(await aging.run(db,now=NOW,manual=True))['accounts'][0]
    assert report['status']=='error' and report['changed']==0
    assert not any(m=='PATCH' for m,p in calls)
    assert not any(p.endswith(posts[1]['uuid']) for m,p in calls)
    assert report['errors'][0]['http_status']==status
    assert report['errors'][0]['stage']=='precheck'

@pytest.mark.asyncio
async def test_visibility_save_route_persists_only_explicit_fields(db,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from autoposter.api.dashboard import router
    from autoposter.db import get_db
    from autoposter.deps import current_user,require_admin
    from autoposter.services import appconfig
    from autoposter.models import AppSetting
    await appconfig.set_many(db,{'auto_plan_days':21,'openrouter_api_key':'synthetic-private-secret'})
    await db.commit()
    secret_before=(await db.get(AppSetting,appconfig.SETTINGS_KEY)).value['openrouter_api_key']
    app=FastAPI();app.include_router(router)
    async def database():yield db
    app.dependency_overrides[get_db]=database
    app.dependency_overrides[current_user]=lambda:object()
    app.dependency_overrides[require_admin]=lambda:object()
    with TestClient(app) as client:
        r=client.post('/settings/integrations',json={'visibility_aging_enabled':True,'visibility_aging_days':12})
        assert r.status_code==200
        r=client.get('/settings/integrations')
        assert r.json()['visibility_aging_days']==12 and r.json()['visibility_aging_enabled'] is True
        assert 'synthetic-private-secret' not in r.text
        assert r.json()['auto_plan_days']==21
    assert (await db.get(AppSetting,appconfig.SETTINGS_KEY)).value['openrouter_api_key']==secret_before
