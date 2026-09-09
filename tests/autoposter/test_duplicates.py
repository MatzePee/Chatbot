"""Kernregel: Duplikate werden PRO KANAL geprüft, nicht bestandsweit.

Ein Bild, das auf X-Profil A lief, muss auf Fanvue und auf X-Profil B weiterhin
uneingeschränkt verfügbar sein.
"""
from __future__ import annotations

import uuid
from io import BytesIO

import pytest
from PIL import Image

from autoposter.models import (
    Channel,
    ChannelHealth,
    Lifecycle,
    MediaAsset,
    MediaStatus,
    PostingPolicy,
)
from autoposter.schemas import AssignmentBatch
from autoposter.services import assignment, lifecycle, media as media_service, planner
from autoposter.services.lifecycle import compute_lifecycle


def make_image(seed: int = 0, size=(600, 800)) -> bytes:
    image = Image.new("RGB", size, (10 + seed * 7 % 240, 60, 120))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


async def make_channel(db, name: str, platform: str = "x", nsfw: str = "explicit") -> Channel:
    policy = PostingPolicy(posts_per_day=2, reuse_cooldown_days=0)
    db.add(policy)
    await db.flush()
    channel = Channel(
        platform=platform,
        display_name=name,
        handle=name.lower().replace(" ", ""),
        nsfw_level=nsfw,
        policy_id=policy.id,
        health=ChannelHealth.ok.value,
    )
    db.add(channel)
    await db.flush()
    await db.refresh(channel)
    return channel


@pytest.mark.asyncio
async def test_bild_von_x_bleibt_fuer_fanvue_verfuegbar(db):
    x_a = await make_channel(db, "X Profil A", "x")
    x_b = await make_channel(db, "X Profil B", "x")
    fanvue = await make_channel(db, "Fanvue", "fanvue")

    asset = await media_service.ingest(db, filename="a.jpg", data=make_image(1))
    await assignment.assign(
        db,
        AssignmentBatch(
            asset_ids=[asset.id], channel_ids=[x_a.id, x_b.id, fanvue.id]
        ),
    )
    await db.refresh(asset)
    assert len(asset.assigned_channel_ids) == 3

    # Auf X-Profil A veröffentlicht.
    await lifecycle.record_usage(
        db, asset_ids=[asset.id], channel_id=x_a.id, post_id=None
    )
    await db.refresh(asset)

    assert str(x_a.id) in asset.used_channel_ids
    assert asset.lifecycle == Lifecycle.partially_used.value

    # Für die beiden anderen Kanäle muss das Bild weiterhin wählbar sein.
    pool_b = await assignment.channel_pool(db, x_b.id)
    pool_fanvue = await assignment.channel_pool(db, fanvue.id)
    pool_a = await assignment.channel_pool(db, x_a.id)

    assert asset.id in [a.id for a in pool_b]
    assert asset.id in [a.id for a in pool_fanvue]
    assert asset.id not in [a.id for a in pool_a]


@pytest.mark.asyncio
async def test_lifecycle_wird_fully_used_erst_wenn_alle_kanaele_bedient_sind(db):
    x_a = await make_channel(db, "A", "x")
    fanvue = await make_channel(db, "F", "fanvue")
    asset = await media_service.ingest(db, filename="b.jpg", data=make_image(2))

    await assignment.assign(
        db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[x_a.id, fanvue.id])
    )
    await db.refresh(asset)
    assert asset.lifecycle == Lifecycle.assigned.value

    await lifecycle.record_usage(db, asset_ids=[asset.id], channel_id=x_a.id, post_id=None)
    await db.refresh(asset)
    assert asset.lifecycle == Lifecycle.partially_used.value

    await lifecycle.record_usage(db, asset_ids=[asset.id], channel_id=fanvue.id, post_id=None)
    await db.refresh(asset)
    assert asset.lifecycle == Lifecycle.fully_used.value
    assert asset.usage_count == 2


@pytest.mark.asyncio
async def test_gleicher_kanal_ohne_cooldown_nicht_erneut(db):
    channel = await make_channel(db, "Solo", "x")
    asset = await media_service.ingest(db, filename="c.jpg", data=make_image(3))
    await assignment.assign(db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[channel.id]))
    await lifecycle.record_usage(db, asset_ids=[asset.id], channel_id=channel.id, post_id=None)

    pool = await assignment.channel_pool(db, channel.id)
    assert pool == []

    picked, reason = await planner.pick_assets(
        db, channel, channel.policy, count=1
    )
    assert picked == []
    assert reason


@pytest.mark.asyncio
async def test_upload_duplikat_wird_bestandsweit_verhindert(db):
    data = make_image(9)
    first = await media_service.ingest(db, filename="x.jpg", data=data)
    with pytest.raises(media_service.DuplicateUpload) as exc:
        await media_service.ingest(db, filename="x_kopie.jpg", data=data)
    assert exc.value.existing.id == first.id


@pytest.mark.asyncio
async def test_phash_pruefung_nur_gegen_eigenen_kanal(db):
    x_a = await make_channel(db, "A2", "x")
    fanvue = await make_channel(db, "F2", "fanvue")

    original = await media_service.ingest(db, filename="o.jpg", data=make_image(5))
    aehnlich = await media_service.ingest(db, filename="s.jpg", data=make_image(5, (601, 800)))

    await assignment.assign(
        db,
        AssignmentBatch(
            asset_ids=[original.id, aehnlich.id], channel_ids=[x_a.id, fanvue.id]
        ),
    )
    await lifecycle.record_usage(db, asset_ids=[original.id], channel_id=x_a.id, post_id=None)

    # Auf X: ähnliches Bild wird durch den phash-Filter blockiert.
    picked_x, _ = await planner.pick_assets(db, x_a, x_a.policy, count=1)
    # Auf Fanvue: keine Historie -> beide Bilder verfügbar.
    picked_f, _ = await planner.pick_assets(db, fanvue, fanvue.policy, count=1)

    assert picked_f, "Fanvue muss trotz X-Historie Bilder liefern"
    assert original.id in [a.id for a in await assignment.channel_pool(db, fanvue.id)]
    assert picked_x == [] or picked_x[0].id != original.id


def test_compute_lifecycle_matrix():
    assert compute_lifecycle(status="ready", assigned=[], used=[], scheduled=[]) == "new"
    assert compute_lifecycle(status="ready", assigned=["a"], used=[], scheduled=[]) == "assigned"
    assert (
        compute_lifecycle(status="ready", assigned=["a"], used=[], scheduled=["a"]) == "scheduled"
    )
    assert (
        compute_lifecycle(status="ready", assigned=["a", "b"], used=["a"], scheduled=[])
        == "partially_used"
    )
    assert (
        compute_lifecycle(status="ready", assigned=["a", "b"], used=["a", "b"], scheduled=[])
        == "fully_used"
    )
    assert compute_lifecycle(status="archived", assigned=["a"], used=[], scheduled=[]) == "archived"
