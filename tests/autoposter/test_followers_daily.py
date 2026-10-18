from datetime import datetime, timedelta, timezone
import pytest
import pytest_asyncio
from autoposter.services import followers, credentials
from autoposter.adapters.x import XAdapter
from autoposter.models import ChannelCredential, AppSetting
from tests.autoposter.test_duplicates import make_channel

@pytest_asyncio.fixture
async def account(db, monkeypatch):
    channel = await make_channel(db, 'Daily'); channel.platform = 'x'
    cred = ChannelCredential(platform='x', external_user_id='daily-id')
    db.add(cred); await db.flush(); channel.credential_id = cred.id; await db.flush()
    calls = []
    async def fetch(self, credential):
        calls.append(1); return 42
    monkeypatch.setattr(credentials, 'to_credentials', lambda row: object())
    monkeypatch.setattr(XAdapter, 'fetch_followers', fetch)
    return calls

@pytest.mark.asyncio
async def test_daily_at_six_bypasses_throttle_and_claims_day(db, account):
    before = datetime(2026, 10, 12, 3, 59, tzinfo=timezone.utc)
    assert await followers.collect(db, before) == 1
    assert await followers.collect(db, before, daily_only=True) == 0
    at_six = before + timedelta(minutes=1)
    assert await followers.collect(db, at_six, daily_only=True) == 1
    await db.commit(); db.expire_all()
    assert await followers.collect(db, at_six + timedelta(minutes=1), daily_only=True) == 0
    assert await followers.collect(db, at_six) == 0
    value = (await db.get(AppSetting, 'x_followers:daily-id')).value
    assert value['daily_slot'] == '2026-10-12'
    assert len(account) == 2

@pytest.mark.asyncio
async def test_claim_committed_before_request_and_overlapping_metrics_skip(db, account, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    now = datetime(2026, 10, 12, 4, tzinfo=timezone.utc)
    await db.commit()
    async def fetch(self, credential):
        async with AsyncSession(db.bind, expire_on_commit=False) as other:
            row = await other.get(AppSetting, 'x_followers:daily-id')
            assert row is not None, 'Claim must survive process termination before response'
            assert row.value['daily_slot'] == '2026-10-12'
            assert await followers.collect(other, now) == 0
        account.append(1)
        return 43
    monkeypatch.setattr(XAdapter, 'fetch_followers', fetch)
    assert await followers.collect(db, now, daily_only=True) == 1
    assert len(account) == 1

@pytest.mark.asyncio
async def test_daily_job_is_follower_only_in_both_schedulers(db, account, monkeypatch):
    from contextlib import asynccontextmanager
    from autoposter import jobs, scheduler, tasks
    from autoposter.celery_app import celery_app
    @asynccontextmanager
    async def scope():
        yield db
        await db.commit()
    monkeypatch.setattr(jobs, 'session_scope', scope)
    async def unexpected(*args, **kwargs):
        raise AssertionError('Daily job must not refresh posts or tokens')
    monkeypatch.setattr(jobs.cred_service, 'get_valid_credentials', unexpected)
    now = datetime(2026, 10, 12, 4, tzinfo=timezone.utc)
    result = await jobs.collect_daily_followers(now=now)
    assert result == {'follower_accounts': 1, 'schedule': '06:00 Europe/Berlin'}
    daily = next(j for j in scheduler.default_jobs() if j.name == 'collect_daily_followers')
    assert daily.interval_seconds == 60
    monkeypatch.setattr(jobs, 'collect_daily_followers', lambda: jobs.inventory_check())
    # Task delegates to exactly the same async job without starting workers.
    monkeypatch.setattr(tasks, 'run_async', lambda coro: (coro.close(), 'delegated')[1])
    assert tasks.collect_daily_followers.run() == 'delegated'
    entry = celery_app.conf.beat_schedule['collect-daily-followers']
    assert entry['task'] == 'autoposter.collect_daily_followers'
    assert entry['schedule'] == 60.0

@pytest.mark.asyncio
async def test_daily_status_is_persistent_and_dashboard_read_only(db, account, monkeypatch):
    now = datetime(2026, 10, 12, 4, tzinfo=timezone.utc)
    item = (await followers.overview(db, now))[0]
    assert item['daily_collection'] == {'time': '06:00', 'timezone': 'Europe/Berlin', 'last_slot': None, 'pending': True}
    assert account == []
    await followers.collect(db, now, daily_only=True)
    await db.commit(); db.expire_all()
    item = (await followers.overview(db, now))[0]
    assert item['daily_collection']['last_slot'] == '2026-10-12'
    assert item['daily_collection']['pending'] is False
    assert item['attempted_at'] == now.isoformat()
    assert account == [1]

@pytest.mark.asyncio
@pytest.mark.parametrize('day,hour', [(29,4), (25,5)])
async def test_dst_six_boundary(db, account, day, hour):
    month = 3 if day == 29 else 10
    six = datetime(2026, month, day, hour, tzinfo=timezone.utc)
    assert await followers.collect(db, six - timedelta(seconds=1), daily_only=True) == 0
    assert await followers.collect(db, six, daily_only=True) == 1
    await db.commit(); db.expire_all()
    assert await followers.collect(db, six + timedelta(minutes=30), daily_only=True) == 0
    assert account == [1]

@pytest.mark.asyncio
async def test_failure_slot_not_retried_but_regular_six_hour_retry_preserved(db, account, monkeypatch):
    now = datetime(2026, 10, 12, 4, tzinfo=timezone.utc)
    async def failed(self, credential):
        account.append(1)
        raise RuntimeError('secret')
    monkeypatch.setattr(XAdapter, 'fetch_followers', failed)
    assert await followers.collect(db, now) == 0
    await db.commit(); db.expire_all()
    assert await followers.collect(db, now + timedelta(minutes=1), daily_only=True) == 0
    assert await followers.collect(db, now + timedelta(hours=5, minutes=59)) == 0
    assert len(account) == 1
    assert await followers.collect(db, now + timedelta(hours=6)) == 0
    assert len(account) == 2
    value = (await db.get(AppSetting, 'x_followers:daily-id')).value
    assert 'secret' not in str(value)

@pytest.mark.asyncio
async def test_restart_catches_only_current_day_after_six(db, account):
    before = datetime(2026, 10, 12, 3, 59, tzinfo=timezone.utc)
    assert await followers.collect(db, before, daily_only=True) == 0
    late = before + timedelta(days=2, hours=8)
    assert await followers.collect(db, late, daily_only=True) == 1
    await db.commit(); db.expire_all()
    assert await followers.collect(db, late + timedelta(minutes=1), daily_only=True) == 0
    assert await followers.collect(db, late + timedelta(days=1), daily_only=True) == 1
    assert len(account) == 2

@pytest.mark.asyncio
async def test_slow_pre_six_response_cannot_erase_new_daily_claim(db, account, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    before = datetime(2026,10,12,3,59,tzinfo=timezone.utc)
    calls = []
    async def fetch(self, credential):
        calls.append(1)
        if len(calls) == 1:
            async with AsyncSession(db.bind, expire_on_commit=False) as other:
                assert await followers.collect(other, before + timedelta(minutes=1), daily_only=True) == 1
                await other.commit()
        return len(calls)
    monkeypatch.setattr(XAdapter, 'fetch_followers', fetch)
    assert await followers.collect(db, before) == 1
    await db.commit(); db.expire_all()
    assert await followers.collect(db, before + timedelta(minutes=2), daily_only=True) == 0
    assert len(calls) == 2

@pytest.mark.asyncio
async def test_simultaneous_daily_and_metrics_dedupe_file_database(db, account, tmp_path, monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from autoposter.db import Base
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'followers.db'))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as setup:
        channel = await make_channel(setup, 'Concurrent'); channel.platform = 'x'
        cred = ChannelCredential(platform='x', external_user_id='concurrent-id')
        setup.add(cred); await setup.flush(); channel.credential_id = cred.id
        await setup.commit()
    async def fetch(self, credential):
        account.append(1)
        await asyncio.sleep(.05)
        return 12
    monkeypatch.setattr(XAdapter, 'fetch_followers', fetch)
    async def run(daily):
        async with maker() as session:
            result = await followers.collect(session, datetime(2026,10,12,4,tzinfo=timezone.utc), daily_only=daily)
            await session.commit()
            return result
    try:
        assert sorted(await asyncio.gather(run(True), run(False))) == [0, 1]
        assert account == [1]
    finally:
        await engine.dispose()
