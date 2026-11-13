"""Offline regression tests: manually restore paid subscriber-only feed posts."""
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
import json
import pytest
import httpx
from autoposter.adapters.fanvue import FanvueAdapter
from autoposter.adapters.base import Credentials

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
UUIDS = [f'123e4567-e89b-12d3-a456-{i:012d}' for i in range(1, 10)]


def post(i, **kw):
    result = dict(uuid=UUIDS[i], audience='subscribers', price=300,
                  publishedAt=(NOW-timedelta(days=1)).isoformat(), publishAt=None,
                  expiresAt=None)
    result.update(kw)
    return result


def test_paid_published_subscriber_only_eligibility():
    from autoposter.services.ppv_restore import eligible
    assert eligible(post(0), NOW)
    for item in [post(0, price=None), post(0, price=0), post(0, price=-1),
                 post(0, price=True), post(0, price='300'), post(0, price=float('nan')),
                 post(0, audience='followers-and-subscribers'), post(0, audience='followers'),
                 post(0, publishedAt=None), post(0, publishedAt=(NOW+timedelta(days=1)).isoformat()),
                 post(0, publishAt=(NOW+timedelta(days=1)).isoformat()),
                 post(0, expiresAt=(NOW-timedelta(seconds=1)).isoformat())]:
        assert not eligible(item, NOW)
    missing = post(0); del missing['price']
    assert not eligible(missing, NOW)


@pytest.mark.asyncio
async def test_preview_is_read_only_and_run_rechecks_price_audience_and_uuid(db, monkeypatch):
    from autoposter.services import ppv_restore as restore
    from autoposter.services import credentials
    from autoposter.config import settings
    from tests.autoposter.test_duplicates import make_channel
    ch = await make_channel(db, 'Restore'); ch.platform='fanvue'; await db.commit()
    async def accounts(_): return [(ch, 'account-a'), (ch, 'account-a')]
    async def creds(_db, _ch): return Credentials('synthetic', external_user_id='account-a', scopes=['read:post','write:post'])
    monkeypatch.setattr(restore, 'accounts', accounts)
    monkeypatch.setattr(credentials, 'get_valid_credentials', creds)
    monkeypatch.setattr(settings, 'global_pause', False)
    monkeypatch.setattr(settings, 'dry_run', False)
    rows = [post(i) for i in range(6)]
    rows[4]['price'] = None
    rows[5]['audience'] = 'followers-and-subscribers'
    calls=[]; changed=set()
    async def request(self, cred, method, path, **kw):
        calls.append((method,path,kw))
        if path == '/users/me': return {'uuid':'account-a'}
        if path == '/v1/posts':
            assert kw['params']['includeUnpublished'] == 'false'
            return {'data':rows[:3], 'nextCursor':'next'} if 'cursor' not in kw['params'] else {'data':rows[2:], 'nextCursor':None}
        item = next(row for row in rows if path.endswith(row['uuid']))
        if method == 'PATCH':
            assert kw == {'json':{'audience':'followers-and-subscribers'}}
            changed.add(item['uuid'])
        if item['uuid'] == UUIDS[1]: return dict(item, price=None)
        if item['uuid'] == UUIDS[2]: return dict(item, audience='followers-and-subscribers')
        if item['uuid'] == UUIDS[3]: return dict(item, uuid=UUIDS[7])
        return dict(item, audience='followers-and-subscribers' if item['uuid'] in changed else item['audience'])
    monkeypatch.setattr(FanvueAdapter, 'visibility_request', request)
    preview = await restore.preview(db, now=NOW)
    assert preview['accounts'][0]['candidates'] == 4
    assert not any(method == 'PATCH' for method,_,_ in calls)
    calls.clear()
    result = await restore.run(db, now=NOW)
    assert result['accounts'][0]['changed'] == 1
    assert result['accounts'][0]['skipped'] == 2
    assert result['accounts'][0]['status'] == 'error'  # wrong UUID safely aborts that item
    assert [path for method,path,_ in calls if method=='PATCH'] == ['/v1/posts/'+UUIDS[0]]
    assert len(result['accounts']) == 1


@pytest.mark.asyncio
async def test_restore_adapter_rejects_other_mutations(monkeypatch):
    from autoposter.adapters.base import AdapterError
    seen=[]
    async def handler(req):
        seen.append((req.method,req.url.path, json.loads(req.content) if req.content else None))
        return httpx.Response(200,json={'uuid':UUIDS[0], 'audience':'followers-and-subscribers'})
    @asynccontextmanager
    async def client(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c: yield c
    import autoposter.adapters.fanvue as module
    monkeypatch.setattr(module,'http_client',client)
    monkeypatch.setattr(module,'cfg',lambda field:'2025-06-26')
    adapter=FanvueAdapter(); cred=Credentials('synthetic')
    for payload in ({'audience':'followers-and-subscribers','price':0}, {'audience':'followers'}, {'audience':'subscribers','price':300}):
        with pytest.raises(AdapterError):
            await adapter.visibility_request(cred,'PATCH','/v1/posts/'+UUIDS[0],json=payload)
    await adapter.visibility_request(cred,'PATCH','/v1/posts/'+UUIDS[0],json={'audience':'followers-and-subscribers'})
    assert seen == [('PATCH','/v1/posts/'+UUIDS[0],{'audience':'followers-and-subscribers'})]


@pytest.mark.asyncio
async def test_restore_routes_admin_only_and_confirmation(db, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from autoposter.api.dashboard import router
    from autoposter.db import get_db
    from autoposter.deps import require_admin
    from autoposter.config import settings
    from autoposter.services import ppv_restore
    app=FastAPI(); app.include_router(router)
    async def database(): yield db
    app.dependency_overrides[get_db] = database
    async def deny(): raise HTTPException(403,'denied')
    app.dependency_overrides[require_admin] = deny
    with TestClient(app) as c:
        assert c.post('/settings/ppv-restore/preview').status_code == 403
        assert c.post('/settings/ppv-restore/run', json={'confirm':'PPV_FOLLOWER_SICHTBAR'}).status_code == 403
    app.dependency_overrides[require_admin] = lambda: object()
    async def preview(_db, **kw): return {'status':'ok','accounts':[{'account_id':'a','candidates':2,'scanned':3}]}
    monkeypatch.setattr(ppv_restore,'preview',preview)
    monkeypatch.setattr(settings,'dry_run',False)
    monkeypatch.setattr(settings,'global_pause',False)
    from autoposter import jobs
    calls=[]
    async def fake_job(): calls.append('started')
    monkeypatch.setattr(jobs,'restore_fanvue_ppv_followers',fake_job)
    with TestClient(app) as c:
        assert c.post('/settings/ppv-restore/preview').json()['accounts'][0]['candidates']==2
        assert c.post('/settings/ppv-restore/run',json={}).status_code == 422
        monkeypatch.setattr(settings,'global_pause',True)
        assert c.post('/settings/ppv-restore/run',json={'confirm':'PPV_FOLLOWER_SICHTBAR'}).status_code == 409
        monkeypatch.setattr(settings,'global_pause',False)
        assert c.post('/settings/ppv-restore/run',json={'confirm':'PPV_FOLLOWER_SICHTBAR'}).status_code == 202
    assert calls == ['started']
