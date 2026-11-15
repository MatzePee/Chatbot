from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text as sql_text
from autoposter.models import Post
from autoposter.services import planner
from tests.autoposter.test_planner_generation import generated_text


@pytest.mark.asyncio
@pytest.mark.parametrize('dry_run', [False, True])
async def test_remaining_capacity_is_shared_across_new_slots_and_repeat_random_runs(db, monkeypatch, dry_run):
    channel = await make_channel(db, 'Partial')
    channel.timezone = 'Europe/Berlin'
    channel.policy.posts_per_day = 2
    channel.policy.min_gap_minutes = 0
    # Published without a scheduled timestamp must still occupy today's quota.
    db.add(Post(channel_id=channel.id, status='published', published_at=moment(0)))
    other = await make_channel(db, 'Other')
    db.add(Post(channel_id=other.id, status='scheduled', scheduled_at=moment(12)))
    db.add(Post(channel_id=channel.id, status='failed', scheduled_at=moment(10)))
    await db.flush()
    text = AsyncMock(return_value=generated_text())
    monkeypatch.setattr(planner.llm, 'generate_post_text', text)
    monkeypatch.setattr(planner, 'typed_slots', lambda *a: [(moment(12), 'text'), (moment(18), 'text')])
    first = await planner.fill_calendar(db, channel_ids=[channel.id], dry_run=dry_run)
    assert len(first.slots) == 1
    assert len(first.created_post_ids) == (0 if dry_run else 1)
    monkeypatch.setattr(planner, 'typed_slots', lambda *a: [(moment(13), 'text'), (moment(19), 'text')])
    second = await planner.fill_calendar(db, channel_ids=[channel.id], dry_run=dry_run)
    assert len(second.slots) == (1 if dry_run else 0)
    assert text.await_count == (0 if dry_run else 1)
from tests.autoposter.test_duplicates import make_channel


import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from autoposter.db import Base


@pytest.mark.asyncio
async def test_concurrent_manual_and_automatic_fills_share_remaining_capacity(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'quota.db'))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        channel = await make_channel(db, 'Parallel')
        channel.policy.posts_per_day = 1
        channel.policy.min_gap_minutes = 0
        channel_id = channel.id
        await db.commit()
    draws = iter([12, 18])
    monkeypatch.setattr(planner, 'typed_slots', lambda *a: [(moment(next(draws)), 'text')])
    async def delayed(*a, **kw):
        await asyncio.sleep(0.05)
        return generated_text()
    text = AsyncMock(side_effect=delayed)
    monkeypatch.setattr(planner.llm, 'generate_post_text', text)
    async def fill():
        async with maker() as db:
            result = await planner.fill_calendar(db, channel_ids=[channel_id], dry_run=False)
            await db.commit()
            return result
    try:
        results = await asyncio.gather(fill(), fill())
        assert sum(len(r.created_post_ids) for r in results) == 1
        assert text.await_count == 1
        async with maker() as db:
            assert len((await db.execute(select(Post))).scalars().all()) == 1
    finally:
        await engine.dispose()


def moment(hour, day=15):
    return datetime(2027, 1, day, hour, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_full_day_is_not_topped_up_before_media_or_text_work(db, monkeypatch):
    channel = await make_channel(db, 'Full')
    channel.policy.posts_per_day = 2
    channel.policy.min_gap_minutes = 0
    db.add_all([Post(channel_id=channel.id, status=status, scheduled_at=moment(hour))
                for status, hour in [('scheduled', 9), ('needs_review', 15)]])
    await db.flush()
    before = [(p.id, p.status, p.scheduled_at) for p in (await db.execute(select(Post))).scalars()]
    monkeypatch.setattr(planner, 'typed_slots', lambda *a: [(moment(12), 'image'), (moment(18), 'image')])
    media = AsyncMock(return_value=([], 'No stock'))
    monkeypatch.setattr(planner, 'pick_assets', media)
    text = AsyncMock()
    monkeypatch.setattr(planner.llm, 'generate_post_text', text)
    before_channel = dict((await db.execute(sql_text('SELECT * FROM channel WHERE id=:id'), {'id': channel.id.hex})).mappings().one())
    result = await planner.fill_calendar(db, dry_run=False)
    after_channel = dict((await db.execute(sql_text('SELECT * FROM channel WHERE id=:id'), {'id': channel.id.hex})).mappings().one())
    assert after_channel == before_channel
    assert not result.gaps and not result.slots and not result.created_post_ids
    media.assert_not_awaited()
    text.assert_not_awaited()
    assert [(p.id, p.status, p.scheduled_at) for p in (await db.execute(select(Post))).scalars()] == before


@pytest.mark.asyncio
@pytest.mark.parametrize('status,counted', [
    ('draft', True), ('needs_review', True), ('approved', True),
    ('scheduled', True), ('publishing', True), ('published', True),
    ('failed', False), ('cancelled', False), ('skipped', False),
])
@pytest.mark.parametrize('month', [1, 7])
async def test_quota_uses_channel_local_day_all_active_states_and_actual_publication(db, monkeypatch, status, counted, month):
    channel = await make_channel(db, 'Local')
    channel.timezone = 'Europe/Berlin'
    channel.policy.posts_per_day = 1
    channel.policy.min_gap_minutes = 0
    # Both timestamps are Jan/July 15 locally, despite differing UTC dates.
    occupied = datetime(2027, month, 14, 23, 30, tzinfo=timezone.utc)
    candidate = datetime(2027, month, 15, 12, tzinfo=timezone.utc)
    db.add(Post(channel_id=channel.id, status=status,
        scheduled_at=moment(0, 13) if status == 'published' else occupied,
        published_at=occupied if status == 'published' else None))
    # Next Berlin day does not consume the candidate day's budget.
    db.add(Post(channel_id=channel.id, status='scheduled',
        scheduled_at=datetime(2027, month, 15, 23, 30, tzinfo=timezone.utc)))
    await db.flush()
    monkeypatch.setattr(planner, 'typed_slots', lambda *a: [(candidate, 'text')])
    result = await planner.fill_calendar(db, channel_ids=[channel.id], dry_run=True)
    assert len(result.slots) == (0 if counted else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('rate,expected', [(0.5, 1), (1.5, 2), (2.5, 2), (3.5, 4)])
async def test_multiple_windows_types_and_fractional_policy_share_one_daily_budget(db, monkeypatch, rate, expected):
    channel = await make_channel(db, 'Windows')
    channel.policy.posts_per_day = rate
    channel.policy.min_gap_minutes = 0
    monkeypatch.setattr(planner, 'typed_slots', lambda *a: [(moment(h), 'text') for h in range(9, 19)])
    result = await planner.fill_calendar(db, channel_ids=[channel.id], dry_run=True)
    assert len(result.slots) == expected
