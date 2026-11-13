import httpx
import pytest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from autoposter.adapters.base import AdapterError, Credentials
from autoposter.adapters.fanvue import FanvueAdapter
from autoposter.services import post_retention as retention
from autoposter.config import settings

@pytest.mark.asyncio
async def test_retention_adapter_restricts_methods_and_confirms_404(monkeypatch):
    requests=[]
    async def handler(req):
        requests.append(req)
        if req.method == 'GET':
            return httpx.Response(404, json={'message':'gone'})
        return httpx.Response(204)
    @asynccontextmanager
    async def client(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            yield c
    import autoposter.adapters.fanvue as module
    monkeypatch.setattr(module,'http_client',client)
    monkeypatch.setattr(module,'cfg',lambda field:'2025-06-26')
    adapter=FanvueAdapter()
    cred=Credentials('synthetic')
    path='/v1/posts/123e4567-e89b-12d3-a456-426614174000'
    assert await adapter.retention_request(cred,'GET',path) is None
    assert await adapter.retention_request(cred,'DELETE',path) is None
    for method,bad in [('DELETE','/v1/posts'),('PATCH',path),('DELETE','/v1/posts/../../users/me')]:
        with pytest.raises(AdapterError): await adapter.retention_request(cred,method,bad)
    assert len(requests)==2

@pytest.mark.asyncio
async def test_schedule_and_manual_gate_defaults(db, monkeypatch):
    from autoposter.services import appconfig
    from autoposter import jobs
    monkeypatch.setattr(settings,'post_retention_active',False)
    assert (await jobs.delete_old_fanvue_posts())['status']=='disabled'
    assert (await retention.scheduled(db))['status']=='disabled'
    values=await appconfig.get_all(db, use_cache=False)
    assert values['post_retention_days']==30 and values['post_retention_delete_ppv'] is False
    assert values['post_retention_automatic'] is False
    for bad in [0, True, 1.2, '30']:
        with pytest.raises(ValueError): await appconfig.set_many(db,{'post_retention_days':bad})
    with pytest.raises(ValueError): await appconfig.set_many(db,{'post_retention_delete_ppv':'false'})
    with pytest.raises(ValueError): await appconfig.set_many(db,{'post_retention_automatic':1})

@pytest.mark.asyncio
async def test_api_run_requires_deployment_gate_even_with_confirmation(db, monkeypatch):
    from fastapi import BackgroundTasks, HTTPException
    from autoposter.api.dashboard import post_retention_run
    monkeypatch.setattr(settings,'post_retention_active',False)
    with pytest.raises(HTTPException) as exc:
        await post_retention_run({'days':30,'delete_ppv':False,'confirm':'FANVUE_POSTS_LOESCHEN'}, BackgroundTasks(),db,None)
    assert exc.value.status_code==409
    assert not BackgroundTasks().tasks

@pytest.mark.asyncio
async def test_preview_does_not_keep_candidates_after_invalid_cursor(db, monkeypatch):
    async def fake_accounts(db): return [(object(),'account-a')]
    monkeypatch.setattr(retention,'accounts',fake_accounts)
    async def creds(db,ch): return Credentials('synthetic',external_user_id='account-a',scopes=['read:post','write:post'])
    monkeypatch.setattr(retention.credentials,'get_valid_credentials',creds)
    count=0
    async def req(self,cred,method,path,**kw):
        nonlocal count
        assert method=='GET'
        if path=='/users/me': return {'uuid':'account-a'}
        count+=1
        return {'data':[], 'nextCursor':'again'}
    monkeypatch.setattr(FanvueAdapter,'retention_request',req)
    result=await retention.preview(db,days=30,delete_ppv=False)
    assert result['status']=='error'
    assert count==2

@pytest.mark.asyncio
async def test_automatic_requires_both_gates(db, monkeypatch):
    from autoposter.services import appconfig
    monkeypatch.setattr(settings,'post_retention_active',True)
    monkeypatch.setattr(settings,'post_retention_automatic_active',False)
    await appconfig.set_many(db,{'post_retention_automatic':True})
    async def forbidden(db): raise AssertionError('disabled scheduler must not make network requests')
    monkeypatch.setattr(retention,'accounts',forbidden)
    assert (await retention.scheduled(db))['status']=='disabled'
    monkeypatch.setattr(settings,'post_retention_automatic_active',True)
    await appconfig.set_many(db,{'post_retention_automatic':False})
    assert (await retention.scheduled(db))['status']=='disabled'


@pytest.mark.asyncio
async def test_saved_automatic_enabled_uses_saved_age_and_ppv_only_after_both_gates(db, monkeypatch):
    from autoposter.services import appconfig
    monkeypatch.setattr(settings,'post_retention_active',True)
    monkeypatch.setattr(settings,'post_retention_automatic_active',True)
    await appconfig.set_many(db,{'post_retention_days':60,'post_retention_delete_ppv':False,'post_retention_automatic':True})
    calls=[]
    async def fake_run(session, **kwargs):
        calls.append(kwargs)
        return {'status':'ok','accounts':[]}
    monkeypatch.setattr(retention,'run',fake_run)
    assert (await retention.scheduled(db))['status']=='ok'
    assert calls == [{'days':60,'delete_ppv':False,'manual':False}]

