from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from autoposter import jobs, schema_sync
from autoposter.api.channels import update_channel
from autoposter.db import Base
from autoposter.models import Channel, Post
from autoposter.schemas import ChannelUpdate, PlanFillResult
from autoposter.services import appconfig, planner
from tests.autoposter.test_duplicates import make_channel


@pytest.fixture(autouse=True)
def isolate_settings_cache(monkeypatch):
    # set_many refreshes process-wide snapshots; keep temporary DB settings local.
    monkeypatch.setattr(appconfig, '_snapshot', {})
    monkeypatch.setattr(appconfig, '_cache', None)
    monkeypatch.setattr(appconfig, '_cache_time', 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,selected,expected', [
    (False, True, False), (True, False, False), (True, True, True),
])
async def test_automatic_job_honors_master_channel_and_pause(db, monkeypatch, enabled, selected, expected):
    included = await make_channel(db, 'Selected', 'x')
    included.auto_plan_enabled = selected
    excluded = await make_channel(db, 'Manual only', 'fanvue')
    excluded.auto_plan_enabled = False
    paused = await make_channel(db, 'Paused', 'x')
    paused.is_active = False
    await appconfig.set_many(db, {'auto_plan_enabled': enabled, 'auto_plan_days': 7})
    await db.flush()

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(jobs, 'session_scope', session)
    monkeypatch.setattr(jobs.settings, 'global_pause', False)
    fill = AsyncMock(return_value=PlanFillResult())
    monkeypatch.setattr(jobs.planner, 'fill_calendar', fill)
    result = await jobs.generate_plan()
    if expected:
        fill.assert_awaited_once_with(db, channel_ids=[included.id], days=7, dry_run=False)
        assert result == {'created': 0, 'gaps': 0}
    else:
        fill.assert_not_awaited()
        assert 'skipped' in result
        assert not await appconfig.get(db, 'auto_plan_last_run', '')


@pytest.mark.asyncio
async def test_channel_toggle_persists_without_changing_posts_activity_or_other_channels(db):
    channel = await make_channel(db, 'One', 'x')
    other = await make_channel(db, 'Two', 'x')
    post = Post(channel_id=channel.id, body_text='Already planned', status='scheduled',
                scheduled_at=datetime(2027, 1, 1, tzinfo=timezone.utc))
    db.add(post)
    await db.flush()
    before = dict((await db.execute(text('SELECT * FROM post'))).mappings().one())
    updated = await update_channel(channel.id, ChannelUpdate(auto_plan_enabled=False), db, None)
    assert updated.auto_plan_enabled is False and updated.is_active is True
    # A later unrelated channel edit must not silently restore the default.
    updated = await update_channel(channel.id, ChannelUpdate(display_name='Renamed'), db, None)
    assert updated.auto_plan_enabled is False
    await db.refresh(other)
    assert other.auto_plan_enabled is True
    assert dict((await db.execute(text('SELECT * FROM post'))).mappings().one()) == before
    updated = await update_channel(channel.id, ChannelUpdate(auto_plan_enabled=True), db, None)
    assert updated.auto_plan_enabled is True


@pytest.mark.asyncio
async def test_manual_fill_still_considers_channels_with_autofill_disabled(db):
    channel = await make_channel(db, 'Manual', 'x')
    channel.auto_plan_enabled = False
    await db.flush()
    result = await planner.fill_calendar(db, channel_ids=[channel.id], days=2, dry_run=True)
    # No image stock: the planner must report gaps for this channel instead of skipping it.
    assert result.gaps
    assert {gap['channel_id'] for gap in result.gaps} == {str(channel.id)}


@pytest.mark.asyncio
async def test_additive_upgrade_preserves_existing_channel_values(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'old.db'))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as db:
            db.add(Channel(platform='x', display_name='Existing', handle='existing',
                           oauth_redirect_base='https://server.example', is_active=False))
            await db.commit()
        async with engine.begin() as conn:
            await conn.execute(text('ALTER TABLE channel DROP COLUMN auto_plan_enabled'))
            before = (await conn.execute(text('SELECT * FROM channel'))).mappings().all()
        report = await schema_sync.sync(engine)
        assert report['added_columns'] == ['channel.auto_plan_enabled']
        assert not report['failed']
        async with engine.connect() as conn:
            after = (await conn.execute(text('SELECT * FROM channel'))).mappings().all()
        assert [{k: row[k] for k in before[0]} for row in after] == [dict(row) for row in before]
        assert after[0]['auto_plan_enabled'] == 1
        assert (await schema_sync.sync(engine))['added_columns'] == []
    finally:
        await engine.dispose()
