from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from fastapi import HTTPException

from app import db as chat_db
from autoposter.models import Channel, ContentPlan, ChannelCredential, Post, PostingPolicy
from autoposter.services import fanvue_channels as pairs, chatbot_fanvue as shared
from autoposter.api.channels import list_channels, update_channel, delete_channel
from autoposter.schemas import ChannelUpdate, AssignmentBatch, PlanRangeRequest
from autoposter.services import assignment, media, planner
from tests.autoposter.test_duplicates import make_channel, make_image


@pytest.fixture
def connection(monkeypatch):
    chat_db.set_setting('fanvue_client_id', 'TEST-CLIENT')
    chat_db.save_tokens('TEST-ACCESS', 'TEST-REFRESH', 2000000000,
                        account_uuid='account-a', account_handle='sabrina')
    monkeypatch.setattr(shared.fanvue, 'get_access_token', lambda: pytest.fail('No token refresh during local channel setup'))


@pytest.mark.asyncio
async def test_shared_connection_creates_two_targets_once_without_copying_tokens(db, connection):
    first = await list_channels(db=db, _=None)
    second = await list_channels(db=db, _=None)
    assert len(first) == len(second) == 2
    assert {c.id for c in first} == {c.id for c in second}
    assert {c.default_audience for c in first} == set(pairs.TARGETS)
    assert all(c.is_connected and c.handle == 'sabrina' for c in first)
    assert all(c.connection_source == 'chatbot' for c in first)
    assert len((await db.execute(select(ContentPlan))).scalars().all()) == 2
    assert len((await db.execute(select(PostingPolicy))).scalars().all()) == 2
    assert not (await db.execute(select(ChannelCredential))).scalars().all()
    assert all(not c.policy.auto_approve for c in first)


@pytest.mark.asyncio
async def test_unconfigured_installation_has_no_fanvue_channels(db):
    assert await pairs.ensure_channels(db) == []


@pytest.mark.asyncio
async def test_configured_but_unconnected_pair_is_bound_after_oauth(db):
    chat_db.set_setting('fanvue_client_id', 'TEST-CLIENT')
    first = await list_channels(db=db, _=None)
    assert len(first) == 2 and not any(c.is_connected for c in first)
    chat_db.save_tokens('TEST-A', 'TEST-R', 2000000000, account_uuid='first-account', account_handle='creator')
    second = await list_channels(db=db, _=None)
    assert {c.id for c in second} == {c.id for c in first}
    assert all(c.fanvue_account_uuid == 'first-account' and c.is_connected for c in second)


@pytest.mark.asyncio
async def test_existing_channel_and_its_posts_are_preserved(db, connection):
    legacy = await make_channel(db, 'Existing', 'fanvue')
    legacy.handle = 'sabrina'
    db.add(Post(channel_id=legacy.id, type='image_single', audience='followers-and-subscribers', body_text='Keep me'))
    await db.flush()
    rows = await list_channels(db=db, _=None)
    assert len(rows) == 3
    assert legacy.display_name == 'Existing' and not legacy.fanvue_audience
    post = (await db.execute(select(Post))).scalar_one()
    assert post.channel_id == legacy.id and post.audience == 'followers-and-subscribers'


@pytest.mark.asyncio
async def test_pause_and_account_binding_survive_sync(db, connection):
    first = await pairs.ensure_channels(db)
    first[0].is_active = False
    await db.flush()
    assert not (await pairs.ensure_channels(db))[0].is_active
    chat_db.save_tokens('TEST-NEW-A', 'TEST-NEW-R', 2000000000, account_uuid='account-b', account_handle='sabrina')
    second = await pairs.ensure_channels(db)
    assert len(set(c.id for c in first + second)) == 4
    assert not any(shared.account_matches(c) for c in first)
    assert all(shared.account_matches(c) for c in second)


@pytest.mark.asyncio
async def test_fixed_target_cannot_be_changed_or_deleted(db, connection):
    channel = (await pairs.ensure_channels(db))[0]
    output = await update_channel(channel.id, ChannelUpdate(default_audience='followers-and-subscribers',
                                  policy={'free_post_ratio': 1}), db=db, _=None)
    assert output.default_audience == 'subscribers' and output.policy.free_post_ratio == 0
    with pytest.raises(HTTPException) as error:
        await delete_channel(channel.id, db=db, _=None)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_both_planners_keep_images_and_audiences_separate(db, connection):
    channels = await pairs.ensure_channels(db)
    # Load relationships just as the channel list/planner does.
    channels = list((await db.execute(select(Channel))).scalars().all())
    by_id = {c.id: c for c in channels}
    pools = {}
    for n, channel in enumerate(channels):
        channel.policy.free_post_ratio = 1 if channel.fanvue_audience == 'subscribers' else 0  # contradictory legacy quota
        channel.policy.posts_per_day = 1
        channel.policy.phash_min_distance = 0
        channel.policy.min_gap_minutes = 30
        assets = [await media.ingest(db, filename=f'pair-{n}-{i}.jpg', data=make_image(710 + n * 10 + i)) for i in range(4)]
        pools[channel.id] = {str(a.id) for a in assets}
        await assignment.assign(db, AssignmentBatch(asset_ids=[a.id for a in assets], channel_ids=[channel.id]))
    await db.flush()
    start = datetime.now(timezone.utc) + timedelta(days=2)
    normal = await planner.fill_calendar(db, channel_ids=list(by_id), days=2, dry_run=True, generate_text=False)
    assert {s.channel_id for s in normal.slots} == set(by_id)
    requested = PlanRangeRequest(channel_ids=list(by_id), date_from=start.date(), date_to=start.date(),
                                 sub_posts_per_day=2, free_posts_per_day=1, generate_text=False, dry_run=True)
    ranged = await planner.plan_range(db, requested)
    for result in (normal, ranged):
        for slot in result.slots:
            assert slot.audience == by_id[slot.channel_id].fanvue_audience
            assert set(map(str, slot.media_asset_ids)) <= pools[slot.channel_id]
    for channel in channels:
        count = 2 if channel.fanvue_audience == 'subscribers' else 1
        assert sum(s.channel_id == channel.id for s in ranged.slots) == count


@pytest.mark.asyncio
async def test_publisher_rejects_wrong_audience_before_external_request(db, connection):
    from autoposter.adapters.fanvue import FanvueAdapter
    from autoposter.adapters.base import AdapterError, Credentials
    channel = (await pairs.ensure_channels(db))[0]
    post = Post(audience='followers-and-subscribers')
    with pytest.raises(AdapterError, match='Subscriber'):
        await FanvueAdapter().publish(Credentials(access_token='TEST'), channel, post, [])


@pytest.mark.asyncio
async def test_post_api_enforces_fixed_target(db, connection):
    from types import SimpleNamespace
    from autoposter.api.posts import create_post, update_post
    from autoposter.schemas import PostCreate, PostUpdate
    channel = (await pairs.ensure_channels(db))[0]
    user = SimpleNamespace(id=None)
    with pytest.raises(HTTPException) as error:
        await create_post(PostCreate(channel_id=channel.id, type='image_single', audience='followers-and-subscribers'), db=db, user=user)
    assert error.value.status_code == 422
    created = await create_post(PostCreate(channel_id=channel.id, type='image_single'), db=db, user=user)
    assert created.audience == 'subscribers'
    with pytest.raises(HTTPException) as error:
        await update_post(created.id, PostUpdate(audience='followers-and-subscribers'), db=db, user=user)
    assert error.value.status_code == 422
    assert (await db.get(Post, created.id)).audience == 'subscribers'


@pytest.mark.asyncio
async def test_concurrent_setup_does_not_duplicate_channels(tmp_path, connection):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from autoposter.db import Base
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'concurrent.db'))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async def sync():
            async with sessions() as session:
                await pairs.ensure_channels(session)
                await session.commit()
        await asyncio.gather(*(sync() for _ in range(4)))
        async with sessions() as session:
            for model in (Channel, PostingPolicy, ContentPlan):
                assert len((await session.execute(select(model))).scalars().all()) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_additive_schema_update_preserves_existing_channel_cells(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import text
    from autoposter.db import Base
    from autoposter import schema_sync
    from tools.release_smoke import fingerprint
    import sqlite3
    database = tmp_path / 'old-schema.db'
    engine = create_async_engine('sqlite+aiosqlite:///' + str(database))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await make_channel(session, 'Keep existing data', 'fanvue')
            await session.commit()
        async with engine.begin() as conn:
            await conn.execute(text('ALTER TABLE channel DROP COLUMN fanvue_audience'))
            await conn.execute(text('ALTER TABLE channel DROP COLUMN fanvue_account_uuid'))
        with sqlite3.connect(database) as conn:
            columns = [row[1] for row in conn.execute('PRAGMA table_info(channel)')]
            assert conn.execute('SELECT COUNT(*) FROM channel').fetchone()[0] == 1
            before = fingerprint(conn, 'channel', columns)
        report = await schema_sync.sync(engine)
        assert report['failed'] == []
        assert set(report['added_columns']) == {'channel.fanvue_audience', 'channel.fanvue_account_uuid'}
        with sqlite3.connect(database) as conn:
            assert fingerprint(conn, 'channel', columns) == before
    finally:
        await engine.dispose()
