"""Telegram paid sales: independent prices, whole sets and immutable approval."""
import uuid
from datetime import date
import pytest
from sqlalchemy import select
from autoposter.models import Channel, PostingPolicy, MediaAsset, MediaAssignment, MediaSet, MediaSetItem, Post
from autoposter.services import telegram_paid, lifecycle
from autoposter.paid_media_schema import upgrade_sqlite


@pytest.mark.asyncio
async def test_sale_units_require_explicit_assignment_and_independent_prices(db):
    channel = Channel(platform='telegram', display_name='Pay', telegram_mode='paid', telegram_content='mixed', policy=PostingPolicy())
    assets = [MediaAsset(filename=f'{i}.jpg', storage_path=str(i), sha256=uuid.uuid4().hex * 2,
                         mime='image/jpeg', status='ready', telegram_price_stars=50 if i == 0 else None) for i in range(5)]
    db.add_all([channel, *assets]); await db.flush()
    media_set = MediaSet(name='Manual set', telegram_price_stars=150)
    db.add(media_set); await db.flush()
    db.add_all([MediaAssignment(channel_id=channel.id, media_asset_id=a.id) for a in assets[:2]])
    db.add(MediaAssignment(channel_id=channel.id, media_set_id=media_set.id))
    db.add_all([MediaSetItem(media_set_id=media_set.id, media_asset_id=a.id, position=i) for i,a in enumerate(assets[2:])])
    await db.flush(); await lifecycle.refresh_assets(db, [a.id for a in assets])
    offers = await telegram_paid.units(db, channel, channel.policy)
    assert sorted(u['stars'] for u in offers) == [50,150]
    sale = next(u for u in offers if u['kind'] == 'set')
    assert sale['asset_ids'] == [str(a.id) for a in assets[2:]]
    assert all(a.telegram_price_stars is None for a in assets[2:])
    channel.telegram_content = 'images'
    assert len(await telegram_paid.units(db,channel,channel.policy)) == 1
    channel.telegram_content = 'sets'
    assert len(await telegram_paid.units(db,channel,channel.policy)) == 1


@pytest.mark.asyncio
async def test_price_change_requires_new_review_and_preserves_published_history(db):
    channel=Channel(platform='telegram',display_name='Pay',telegram_mode='paid',policy=PostingPolicy())
    asset=MediaAsset(filename='a.jpg',storage_path='a',sha256=uuid.uuid4().hex*2,mime='image/jpeg',status='ready',telegram_price_stars=100)
    db.add_all([channel,asset]);await db.flush()
    db.add(MediaAssignment(channel_id=channel.id,media_asset_id=asset.id));await db.flush()
    post=Post(channel_id=channel.id,status='scheduled',media_asset_ids=[str(asset.id)])
    await telegram_paid.prepare(db,channel,post);db.add(post);await db.flush()
    history=Post(channel_id=channel.id,status='published',price_stars=100,media_asset_ids=[str(asset.id)])
    db.add(history);await db.flush()
    asset.telegram_price_stars=200
    await telegram_paid.invalidate(db,asset_id=asset.id)
    assert post.status=='needs_review' and post.price_stars==100
    assert await telegram_paid.validate(db,channel,post)
    assert history.status=='published' and history.price_stars==100
    await telegram_paid.prepare(db,channel,post)
    assert post.price_stars==200 and await telegram_paid.validate(db,channel,post) is None
    asset.telegram_price_stars=None
    assert await telegram_paid.validate(db,channel,post)


def test_cadence_has_real_three_day_intervals():
    channel=Channel(platform='telegram',telegram_interval_days=3,telegram_start_date=date(2026,11,20))
    assert [d for d in range(20,28) if telegram_paid.on_day(channel,date(2026,11,d))]==[20,23,26]
    assert not telegram_paid.on_day(channel,date(2026,11,19))


def test_additive_schema_preserves_existing_rows_and_is_idempotent():
    import sqlite3
    with sqlite3.connect(':memory:') as conn:
        conn.executescript("CREATE TABLE channel(id TEXT); INSERT INTO channel VALUES('old'); CREATE TABLE post(id TEXT);")
        upgrade_sqlite(conn);upgrade_sqlite(conn)
        assert conn.execute('SELECT id,telegram_mode,telegram_interval_days FROM channel').fetchone()==('old','free',1)
