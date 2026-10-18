from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from autoposter.models import Notification, Post
from autoposter.api.dashboard import mark_read, dashboard
from autoposter.services import problems
from tests.autoposter.test_duplicates import make_channel

@pytest.mark.asyncio
async def test_ack_inventory_survives_checks_until_condition_resolves(db):
    channel = await make_channel(db, 'Ack')
    await problems.check(db)
    note = (await db.execute(select(Notification))).scalars().first()
    stamp = note.created_at
    await mark_read(ids=[note.id], db=db, _=None)
    await db.commit()
    await problems.check(db)
    assert not (await db.execute(select(Notification).where(Notification.is_read.is_(False)))).scalars().all()
    assert note.created_at == stamp
    channel.is_active = False
    await db.flush(); await problems.check(db)
    channel.is_active = True
    await db.flush(); await problems.check(db)
    assert len((await db.execute(select(Notification).where(Notification.is_read.is_(False)))).scalars().all()) == 1

@pytest.mark.asyncio
async def test_follower_history_exact_days_and_account_isolation(db):
    from autoposter.services import followers
    channel = await make_channel(db, 'Followers')
    channel.platform = 'x'
    from autoposter.models import ChannelCredential, AppSetting
    cred = ChannelCredential(platform='x', external_user_id='account-a')
    db.add(cred); await db.flush(); channel.credential_id = cred.id; await db.flush()
    db.add(AppSetting(key='x_followers:account-a', value={'days': {'2026-10-11':100,'2026-10-10':98,'2026-10-04':90,'2026-09-11':110},'sampled_at':'2026-10-11T08:00:00+00:00'}))
    await db.flush()
    result = await followers.overview(db, now=datetime(2026,10,11,12,tzinfo=timezone.utc))
    item = result[0]
    assert item['current'] == 100
    assert item['yesterday'] == {'count':98,'growth':2}
    assert item['days7'] == {'count':90,'growth':10}
    assert item['days30'] == {'count':110,'growth':-10}
    cred.external_user_id = 'account-b'; await db.flush()
    item = (await followers.overview(db))[0]
    assert item['current'] is None and item['days7']['growth'] is None

@pytest.mark.asyncio
async def test_collection_cached_persistent_and_failure_safe(db, monkeypatch):
    from autoposter.services import followers, credentials
    from autoposter.adapters.x import XAdapter
    from autoposter.models import ChannelCredential, AppSetting
    channel = await make_channel(db, 'Collector'); channel.platform = 'x'
    cred = ChannelCredential(platform='x', external_user_id='stable-id')
    db.add(cred); await db.flush(); channel.credential_id = cred.id; await db.flush()
    calls = []
    async def fetch(self, credential):
        calls.append(1); return 42
    monkeypatch.setattr(credentials, 'to_credentials', lambda row: object())
    monkeypatch.setattr(XAdapter, 'fetch_followers', fetch)
    now = datetime(2026,10,11,12,tzinfo=timezone.utc)
    assert await followers.collect(db, now) == 1
    await db.commit()
    assert await followers.collect(db, now) == 0
    assert calls == [1]
    assert (await followers.overview(db, now))[0]['current'] == 42
    async def failed(self, credential): raise RuntimeError('secret-provider-response')
    monkeypatch.setattr(XAdapter, 'fetch_followers', failed)
    from datetime import timedelta
    assert await followers.collect(db, now + timedelta(hours=7)) == 0
    value = (await db.get(AppSetting, 'x_followers:stable-id')).value
    assert 'secret' not in str(value) and value['days']['2026-10-11'] == 42

@pytest.mark.asyncio
async def test_followers_endpoint_read_only_and_requires_auth(db, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from autoposter.api.dashboard import router
    from autoposter.db import get_db
    from autoposter.deps import current_user
    from autoposter.config import settings
    monkeypatch.setattr(settings, "auth_enabled", True)
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/analytics/x-followers')).status_code in (401,403)
        app.dependency_overrides[current_user] = lambda: None
        assert (await client.get('/analytics/x-followers')).json() == []

def test_dashboard_ui_problem_times_ack_and_followers():
    from pathlib import Path
    source = Path('autoposter/ui/js/pages/dashboard.js').read_text()
    assert 'problemTime(n.created_at)' in source
    assert 'problemTime(p.last_attempt_at || p.created_at)' in source
    assert 'api.markRead([n.id])' in source
    assert 'api.ackFailure(p.id)' in source
    assert 'api.xFollowers()' in source
    assert 'Нет' not in source

@pytest.mark.asyncio
async def test_failed_post_ack_hides_only_same_attempt(db):
    from autoposter.api.dashboard import acknowledge_failure
    from datetime import timedelta
    channel = await make_channel(db, 'Failed')
    post = Post(channel_id=channel.id, status='failed', error_message='broken', last_attempt_at=datetime.now(timezone.utc))
    db.add(post); await db.flush()
    await acknowledge_failure(post_id=post.id, db=db, _=None)
    await db.commit()
    assert not (await dashboard(db=db, _=None)).recent_failures
    assert post.status == 'failed'
    post.last_attempt_at += timedelta(minutes=1); await db.flush()
    assert len((await dashboard(db=db, _=None)).recent_failures) == 1

@pytest.mark.asyncio
async def test_ack_identical_events_stay_hidden_changed_message_reappears(db):
    from autoposter.services import notify
    first = await notify.push(db, level='warning', title='Token-Problem: X', body='expired', entity='channel', entity_id='same', external=False)
    await mark_read(ids=[first.id], db=db, _=None); await db.commit()
    repeated = await notify.push(db, level='warning', title=first.title, body='expired', entity='channel', entity_id='same', external=False)
    assert repeated.is_read is True
    changed = await notify.push(db, level='warning', title=first.title, body='new error', entity='channel', entity_id='same', external=False)
    assert not changed.is_read

@pytest.mark.asyncio
async def test_x_follower_adapter_uses_only_get_and_checks_account(monkeypatch):
    import httpx
    from contextlib import asynccontextmanager
    from autoposter.adapters import x
    from autoposter.adapters.base import Credentials
    seen=[]
    def handler(request):
        seen.append(request)
        assert request.method == 'GET'
        assert request.url.path == '/2/users/me'
        assert dict(request.url.params) == {'user.fields': 'public_metrics'}
        return httpx.Response(200, json={'data':{'id':'42','public_metrics':{'followers_count':123}}})
    @asynccontextmanager
    async def client():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as value: yield value
    monkeypatch.setattr(x, 'http_client', client)
    assert await x.XAdapter().fetch_followers(Credentials(access_token='test', external_user_id='42')) == 123
    with pytest.raises(ValueError, match='mismatch'):
        await x.XAdapter().fetch_followers(Credentials(access_token='test', external_user_id='other'))
    assert len(seen) == 2
