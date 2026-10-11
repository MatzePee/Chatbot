"""Idempotenz, Dry-Run und Preflight."""
from __future__ import annotations

import pytest

from autoposter.config import settings
from autoposter.models import Post, PostStatus, PostType
from autoposter.schemas import AssignmentBatch
from autoposter.services import assignment, media as media_service, planner, publisher
from tests.autoposter.test_duplicates import make_channel, make_image


@pytest.mark.asyncio
async def test_doppeltes_publish_erzeugt_nur_einen_post(db):
    settings.dry_run = True
    channel = await make_channel(db, "Idem", "x")
    asset = await media_service.ingest(db, filename="i.jpg", data=make_image(70))
    await assignment.assign(db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[channel.id]))

    post = Post(
        channel_id=channel.id,
        type=PostType.image_single.value,
        status=PostStatus.scheduled.value,
        body_text="Test",
        media_asset_ids=[str(asset.id)],
        alt_texts={str(asset.id): "Alt"},
    )
    db.add(post)
    await db.flush()

    ok_first, _ = await publisher.publish_post(db, post.id)
    ok_second, message = await publisher.publish_post(db, post.id)

    await db.refresh(asset)
    assert ok_first and ok_second
    assert "Bereits" in message
    assert asset.usage_count == 1


@pytest.mark.asyncio
async def test_dry_run_sendet_nichts_und_markiert_versuch(db):
    settings.dry_run = True
    channel = await make_channel(db, "Dry", "x")
    asset = await media_service.ingest(db, filename="d.jpg", data=make_image(71))
    await assignment.assign(db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[channel.id]))

    post = Post(
        channel_id=channel.id,
        status=PostStatus.scheduled.value,
        body_text="Trockenlauf",
        media_asset_ids=[str(asset.id)],
        alt_texts={str(asset.id): "Alt"},
    )
    db.add(post)
    await db.flush()

    ok, _ = await publisher.publish_post(db, post.id)
    await db.refresh(post)
    assert ok
    assert post.external_post_id.startswith("dryrun-")
    assert post.status == PostStatus.published.value


@pytest.mark.asyncio
async def test_preflight_meldet_zu_langen_text(db):
    channel = await make_channel(db, "Lang", "x")
    post = Post(
        channel_id=channel.id,
        type=PostType.text_only.value,
        status=PostStatus.draft.value,
        body_text="X" * 400,
    )
    db.add(post)
    await db.flush()
    issues = await planner.preflight(db, post)
    assert any(i["code"] == "too_long" for i in issues)


@pytest.mark.asyncio
async def test_preflight_warnt_bei_fehlendem_alt_text(db):
    channel = await make_channel(db, "Alt", "x")
    asset = await media_service.ingest(db, filename="alt.jpg", data=make_image(72))
    await assignment.assign(db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[channel.id]))
    post = Post(
        channel_id=channel.id,
        status=PostStatus.draft.value,
        body_text="Kurz",
        media_asset_ids=[str(asset.id)],
    )
    db.add(post)
    await db.flush()
    issues = await planner.preflight(db, post)
    assert any(i["code"] == "missing_alt" for i in issues)


@pytest.mark.asyncio
async def test_thread_split_bei_mehr_als_vier_bildern(db):
    channel = await make_channel(db, "Thread", "x")
    assets = [
        await media_service.ingest(db, filename=f"t{i}.jpg", data=make_image(80 + i))
        for i in range(6)
    ]
    post = Post(
        channel_id=channel.id,
        type=PostType.image_set.value,
        status=PostStatus.draft.value,
        body_text="Serie",
        media_asset_ids=[str(a.id) for a in assets],
    )
    db.add(post)
    await db.flush()

    chain = await publisher.split_thread_if_needed(db, post, channel)
    assert len(chain) == 2
    assert len(chain[0].media_asset_ids) == 4
    assert len(chain[1].media_asset_ids) == 2
    assert chain[1].thread_parent_id == post.id
