from datetime import datetime, timedelta, timezone
import pytest
from autoposter.services import appconfig
import httpx
from autoposter.adapters.fanvue import FanvueAdapter
from autoposter.adapters.base import Credentials, AdapterError
from contextlib import asynccontextmanager
import json

@pytest.mark.asyncio
async def test_official_visibility_adapter_cursor_get_patch_and_retry(monkeypatch):
    requests = []
    retry = []
    async def handler(req):
        requests.append(req)
        assert req.headers['X-Fanvue-API-Version'] == '2025-06-26'
        assert req.headers['Authorization'] == 'Bearer synthetic'
        if req.url.path == '/v1/posts':
            assert dict(req.url.params) == {'size':'50', 'includeUnpublished':'false', 'cursor':'opaque'}
            return httpx.Response(200, json={'data':[], 'nextCursor':None})
        if req.method == 'PATCH':
            assert req.url.path == '/v1/posts/123e4567-e89b-12d3-a456-426614174000'
            assert json.loads(req.content) == {'audience':'subscribers'}
            if not retry:
                retry.append(1)
                return httpx.Response(429, headers={'Retry-After':'0'}, json={})
        return httpx.Response(200, json={'uuid':'123e4567-e89b-12d3-a456-426614174000','audience':'subscribers'})
    @asynccontextmanager
    async def client(**kw):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            yield c
    import autoposter.adapters.fanvue as module
    monkeypatch.setattr(module, 'http_client', client)
    monkeypatch.setattr(module, 'cfg', lambda field: '2025-06-26')
    adapter = FanvueAdapter(); cred = Credentials('synthetic')
    await adapter.visibility_request(cred, 'GET', '/v1/posts', params={'size':50,'includeUnpublished':'false','cursor':'opaque'})
    await adapter.visibility_request(cred, 'GET', '/v1/posts/123e4567-e89b-12d3-a456-426614174000')
    await adapter.visibility_request(cred, 'PATCH', '/v1/posts/123e4567-e89b-12d3-a456-426614174000', json={'audience':'subscribers'})
    assert [r.method for r in requests] == ['GET','GET','PATCH','PATCH']

@pytest.mark.asyncio
async def test_visibility_settings_defaults_validation_and_preservation(db):
    values = await appconfig.get_all(db, use_cache=False)
    assert values.get('visibility_aging_enabled') is False
    assert values.get('visibility_aging_days') == 7
    await appconfig.set_many(db, {'auto_plan_days': 21, 'visibility_aging_enabled': True, 'visibility_aging_days': 3})
    for bad in [0, -1, 1.5, True, '7', None]:
        with pytest.raises(ValueError):
            await appconfig.set_many(db, {'visibility_aging_days': bad})
    with pytest.raises(ValueError):
        await appconfig.set_many(db, {'visibility_aging_enabled': 'false'})
    values = await appconfig.get_all(db, use_cache=False)
    assert values['auto_plan_days'] == 21
    assert values['visibility_aging_days'] == 3

@pytest.mark.asyncio
async def test_published_cutoff_uses_actual_timestamp_and_rechecks(db, monkeypatch):
    from autoposter.services import visibility_aging as aging
    from tests.autoposter.test_duplicates import make_channel
    from autoposter.config import settings
    from autoposter.services import credentials
    now = datetime(2026,10,18,12,tzinfo=timezone.utc)
    channel = await make_channel(db, 'Aging'); channel.platform = 'fanvue'
    await appconfig.set_many(db, {'visibility_aging_enabled':True,'visibility_aging_days':7})
    await db.commit()
    monkeypatch.setattr(settings, 'dry_run', False)
    async def accounts(db): return [(channel, 'account-a')]
    monkeypatch.setattr(aging, 'accounts', accounts)
    async def creds(db, ch): return Credentials('synthetic', external_user_id='account-a', scopes=['read:post','write:post'])
    monkeypatch.setattr(credentials, 'get_valid_credentials', creds)
    def post(i, **kw):
        return dict(uuid=f'123e4567-e89b-12d3-a456-{i:012d}', audience='followers-and-subscribers', createdAt='2020-01-01T00:00:00Z', publishedAt=(now-timedelta(days=7)).isoformat(), publishAt=None, **kw)
    rows = [post(i) for i in range(1,8)]
    rows[1]['publishedAt'] = (now-timedelta(days=7)+timedelta(seconds=1)).isoformat()
    rows[2]['publishedAt'] = None
    rows[3]['publishedAt'] = (now+timedelta(days=1)).isoformat()
    rows[4]['publishAt'] = (now+timedelta(days=1)).isoformat()
    rows[5]['audience'] = 'subscribers'
    rows[6]['publishedAt'] = 'invalid'
    calls=[]; updated=set()
    async def request(self, cred, method, path, **kw):
        calls.append((method,path,kw))
        if path == '/users/me': return {'uuid':'account-a'}
        if path == '/v1/posts':
            assert kw['params']['includeUnpublished'] == 'false'
            if 'cursor' not in kw['params']:
                return {'data':rows, 'nextCursor':'page2'}
            return {'data':[rows[0]], 'nextCursor':None}
        item = next(r for r in rows if path.endswith(r['uuid']))
        if method == 'PATCH':
            assert kw == {'json':{'audience':'subscribers'}}
            updated.add(item['uuid'])
        return dict(item, audience='subscribers' if item['uuid'] in updated else item['audience'])
    monkeypatch.setattr(FanvueAdapter, 'visibility_request', request)
    result = await aging.run(db, now=now, manual=True)
    assert result['accounts'][0]['changed'] == 1
    assert len([c for c in calls if c[0]=='PATCH']) == 1
    assert len([c for c in calls if c[1].endswith(rows[0]['uuid']) and c[0]=='GET']) == 2
    assert (await aging.run(db, now=now))['accounts'][0]['status'] == 'already_done'

@pytest.mark.asyncio
async def test_visibility_job_and_authenticated_manual_routes(db, monkeypatch):
    from autoposter import jobs, scheduler, tasks
    from autoposter.celery_app import celery_app
    from autoposter.services import visibility_aging as aging
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from autoposter.api.dashboard import router
    from autoposter.db import get_db
    from autoposter.deps import require_admin, current_user
    @asynccontextmanager
    async def scope(): yield db
    monkeypatch.setattr(jobs, 'session_scope', scope)
    calls=[]
    async def run(session, **kw): calls.append(kw); return {'accounts':[],'status':'ok'}
    monkeypatch.setattr(aging, 'run', run)
    assert (await jobs.age_fanvue_visibility())['status'] == 'ok'
    from autoposter.models import AppSetting
    assert (await db.get(AppSetting, 'visibility_aging_last_run')).value['status'] == 'ok'
    assert next(j for j in scheduler.default_jobs() if j.name=='age_fanvue_visibility').interval_seconds == 60
    assert celery_app.conf.timezone == 'UTC'
    assert celery_app.conf.beat_schedule['age-fanvue-visibility']['schedule'] == 60.0
    monkeypatch.setattr(tasks, 'run_async', lambda c: (c.close(), 'delegated')[1])
    assert tasks.age_fanvue_visibility.run() == 'delegated'
    app=FastAPI(); app.include_router(router)
    async def database(): yield db
    app.dependency_overrides[get_db] = database
    async def deny():
        from fastapi import HTTPException
        raise HTTPException(403, 'denied')
    app.dependency_overrides[require_admin]=deny
    with TestClient(app) as client:
        assert client.post('/settings/visibility-aging/run').status_code == 403
    app.dependency_overrides[require_admin]=lambda: object()
    app.dependency_overrides[current_user]=lambda: object()
    async def job(**kw): calls.append(kw); return {'status':'ok'}
    monkeypatch.setattr(jobs, 'age_fanvue_visibility', job)
    from autoposter.config import settings as runtime
    monkeypatch.setattr(runtime, 'dry_run', False)
    with TestClient(app) as client:
        assert client.post('/settings/visibility-aging/run').status_code == 202
        from autoposter.config import settings as runtime
        monkeypatch.setattr(runtime, 'global_pause', True)
        assert client.post('/settings/visibility-aging/run').status_code == 409
        monkeypatch.setattr(runtime, 'global_pause', False)
        assert client.get('/settings/visibility-aging').status_code == 200
        assert client.post('/settings/integrations', json={'visibility_aging_days':0}).status_code == 422
    assert {'manual':True} in calls

@pytest.mark.asyncio
async def test_visibility_status_explains_pause_and_expired_leases(db, monkeypatch):
    from autoposter.services import visibility_aging as aging
    from autoposter.models import AppSetting
    from autoposter.config import settings
    monkeypatch.setattr(settings, 'dry_run', True)
    db.add(AppSetting(key=aging.PREFIX+'lost-worker', value={'status':'running','lease_until':'2000-01-01T00:00:00Z','token':'private'}))
    db.add(AppSetting(key='fanvueXvisibility:unrelated', value={'status':'private-unrelated-setting'}))
    await db.commit()
    report=await aging.status(db)
    assert 'private-unrelated-setting' not in str(report)
    assert report['runtime_status'] == 'dry_run'
    assert report['accounts'][0]['status'] == 'interrupted'
    assert 'token' not in report['accounts'][0]
