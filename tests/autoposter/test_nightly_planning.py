from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from autoposter import jobs
from autoposter.schemas import PlanFillResult
from autoposter.services import appconfig
from tests.autoposter.test_duplicates import make_channel


import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from autoposter.db import Base
from autoposter.models import AppSetting


@pytest.mark.asyncio
async def test_two_workers_share_persistent_gate(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'nightly.db'))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        await make_channel(db, 'Concurrent')
        await appconfig.set_many(db, {'auto_plan_enabled': True})
        await db.commit()
    @asynccontextmanager
    async def session():
        async with maker() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise
    monkeypatch.setattr(jobs, 'session_scope', session)
    monkeypatch.setattr(jobs.settings, 'global_pause', False)
    async def delayed(*a, **kw):
        await asyncio.sleep(0.05)
        return PlanFillResult()
    fill = AsyncMock(side_effect=delayed)
    monkeypatch.setattr(jobs.planner, 'fill_calendar', fill)
    try:
        results = await asyncio.gather(jobs.generate_plan(), jobs.generate_plan())
        assert fill.await_count == 1
        assert sum('skipped' in r for r in results) == 1
        # Fresh process cache and sessions still observe the completed day.
        appconfig.invalidate()
        assert 'skipped' in await jobs.generate_plan()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_nightly_transaction_does_not_consume_day_or_keep_partial_posts(tmp_path, monkeypatch):
    from autoposter.models import Post
    from sqlalchemy import select
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'retry.db'))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        channel = await make_channel(db, 'Retry')
        channel_id = channel.id
        await appconfig.set_many(db, {'auto_plan_enabled': True})
        await db.commit()
    @asynccontextmanager
    async def session():
        async with maker() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise
    monkeypatch.setattr(jobs, 'session_scope', session)
    monkeypatch.setattr(jobs.settings, 'global_pause', False)
    async def broken(db, **kw):
        db.add(Post(channel_id=channel_id, status='needs_review', body_text='Partial synthetic'))
        await db.flush()
        raise RuntimeError('synthetic planner failure')
    fill = AsyncMock(side_effect=broken)
    monkeypatch.setattr(jobs.planner, 'fill_calendar', fill)
    try:
        with pytest.raises(RuntimeError, match='synthetic planner failure'):
            await jobs.generate_plan()
        async with maker() as db:
            assert not (await db.execute(select(Post))).scalars().all()
            assert not await appconfig.get(db, 'auto_plan_last_run', '')
        fill.side_effect = None
        fill.return_value = PlanFillResult()
        assert await jobs.generate_plan() == {'created': 0, 'gaps': 0}
        assert 'skipped' in await jobs.generate_plan()
        assert fill.await_count == 2
    finally:
        await engine.dispose()


def test_both_schedulers_poll_nightly_gate_every_minute_without_other_changes():
    from autoposter.scheduler import default_jobs
    from autoposter.celery_app import celery_app
    schedule = {j.name: (j.interval_seconds, j.initial_delay) for j in default_jobs()}
    assert schedule.pop('generate_plan') == (60, 60)
    assert schedule == {
        'dispatch_due_posts': (60, 10), 'refresh_metrics': (1800, 300),
        'age_fanvue_visibility': (60, 5), 'delete_old_fanvue_posts': (3600, 120),
        'collect_daily_followers': (60, 5), 'describe_assets': (900, 120),
        'inventory_check': (3600, 180), 'nightly_maintenance': (86400, 600),
        'token_health': (900, 90),
    }
    assert celery_app.conf.beat_schedule['generate-plan']['schedule'] == 60.0
    assert celery_app.conf.timezone == 'UTC'


@pytest.fixture(autouse=True)
def isolate_cache(monkeypatch):
    monkeypatch.setattr(appconfig, '_snapshot', {})
    monkeypatch.setattr(appconfig, '_cache', None)
    monkeypatch.setattr(appconfig, '_cache_time', 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize('previous,current', [
    ('2027-01-14T22:59:00+00:00', '2027-01-14T23:00:00+00:00'),
    ('2027-03-27T23:00:00+00:00', '2027-03-28T22:00:00+00:00'),
    ('2027-10-30T22:00:00+00:00', '2027-10-31T23:00:00+00:00'),
])
async def test_midnight_daily_gate_not_rolling_interval_and_persists(db, monkeypatch, previous, current):
    channel = await make_channel(db, 'Nightly')
    await appconfig.set_many(db, {'auto_plan_enabled': True, 'auto_plan_days': 4,
        'auto_plan_interval_hours': 168, 'auto_plan_last_run': previous})
    await db.commit()
    @asynccontextmanager
    async def session():
        yield db
        await db.commit()
    monkeypatch.setattr(jobs, 'session_scope', session)
    monkeypatch.setattr(jobs.settings, 'global_pause', False)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(current)
    monkeypatch.setattr(jobs, 'datetime', Clock)
    fill = AsyncMock(return_value=PlanFillResult())
    monkeypatch.setattr(jobs.planner, 'fill_calendar', fill)
    assert await jobs.generate_plan() == {'created': 0, 'gaps': 0}
    fill.assert_awaited_once_with(db, channel_ids=[channel.id], days=4, dry_run=False)
    # Process cache reset does not reset the persistent daily gate.
    appconfig._snapshot = {}; appconfig._cache = None; appconfig._cache_time = 0
    assert 'skipped' in await jobs.generate_plan()
    assert fill.await_count == 1
    assert await appconfig.get(db, 'auto_plan_interval_hours') == 168
