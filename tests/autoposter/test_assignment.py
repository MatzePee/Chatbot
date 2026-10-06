"""Tests für die Drag-&-Drop-Zuordnung."""
from __future__ import annotations

import pytest

from autoposter.models import MediaAssignment
from autoposter.schemas import AssignmentBatch, AssignmentRemove
from autoposter.services import assignment, media as media_service
from tests.autoposter.test_duplicates import make_channel, make_image
from sqlalchemy import select


@pytest.mark.asyncio
async def test_drop_auf_zwei_kanaele_erzeugt_zwei_zuordnungen(db):
    x = await make_channel(db, "X", "x")
    fanvue = await make_channel(db, "FV", "fanvue")
    asset = await media_service.ingest(db, filename="a.jpg", data=make_image(11))

    result = await assignment.assign(
        db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[x.id, fanvue.id])
    )
    assert result.created == 2

    rows = (
        await db.execute(
            select(MediaAssignment).where(MediaAssignment.media_asset_id == asset.id)
        )
    ).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_erneutes_ablegen_erzeugt_keine_dublette(db):
    x = await make_channel(db, "X2", "x")
    asset = await media_service.ingest(db, filename="b.jpg", data=make_image(12))

    first = await assignment.assign(db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[x.id]))
    second = await assignment.assign(db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[x.id]))

    assert first.created == 1
    assert second.created == 0
    assert second.skipped == 1


@pytest.mark.asyncio
async def test_entfernen_laesst_andere_kanaele_unberuehrt(db):
    x = await make_channel(db, "X3", "x")
    fanvue = await make_channel(db, "FV3", "fanvue")
    asset = await media_service.ingest(db, filename="c.jpg", data=make_image(13))

    await assignment.assign(
        db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[x.id, fanvue.id])
    )
    await assignment.unassign(
        db, AssignmentRemove(asset_ids=[asset.id], channel_ids=[x.id])
    )
    await db.refresh(asset)

    assert str(fanvue.id) in asset.assigned_channel_ids
    assert str(x.id) not in asset.assigned_channel_ids


@pytest.mark.asyncio
async def test_move_entfernt_alte_zuordnungen(db):
    a = await make_channel(db, "A9", "x")
    b = await make_channel(db, "B9", "x")
    asset = await media_service.ingest(db, filename="d.jpg", data=make_image(14))

    await assignment.assign(db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[a.id]))
    await assignment.assign(
        db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[b.id], mode="move")
    )
    await db.refresh(asset)

    assert asset.assigned_channel_ids == [str(b.id)]


@pytest.mark.asyncio
async def test_nsfw_inkompatibles_asset_wird_abgelehnt(db):
    sfw_channel = await make_channel(db, "Clean", "x", nsfw="sfw")
    asset = await media_service.ingest(
        db, filename="e.jpg", data=make_image(15), nsfw_level="explicit"
    )
    result = await assignment.assign(
        db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[sfw_channel.id])
    )
    assert result.created == 0
    assert result.rejected and "NSFW" in result.rejected[0]["reason"]


@pytest.mark.asyncio
async def test_reihenfolge_steuert_verplanung(db):
    channel = await make_channel(db, "Order", "x")
    assets = [
        await media_service.ingest(db, filename=f"o{i}.jpg", data=make_image(20 + i))
        for i in range(3)
    ]
    await assignment.assign(
        db, AssignmentBatch(asset_ids=[a.id for a in assets], channel_ids=[channel.id])
    )
    reversed_ids = [a.id for a in reversed(assets)]
    await assignment.reorder(db, channel.id, reversed_ids)

    pool = await assignment.channel_pool(db, channel.id)
    assert [a.id for a in pool] == reversed_ids


@pytest.mark.asyncio
async def test_board_liefert_vollstaendigen_kanalbestand(db):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from autoposter.api.assignments import router
    from autoposter.db import get_db
    from autoposter.deps import current_user
    from autoposter.models import MediaAsset, MediaStatus

    channel = await make_channel(db, "Full board", "x")
    # More than both the former default (60) and former maximum (500).
    assets = [
        MediaAsset(
            filename=f"board-{i}.jpg", storage_path=f"board-{i}.jpg",
            sha256=f"{i:064x}", status=MediaStatus.ready.value,
            assigned_channel_ids=[str(channel.id)],
            scheduled_channel_ids=[str(channel.id)] if i % 3 == 1 else [],
            used_channel_ids=[str(channel.id)] if i % 3 == 2 else [],
        )
        for i in range(501)
    ]
    db.add_all(assets)
    await db.flush()
    db.add_all([
        MediaAssignment(media_asset_id=a.id, channel_id=channel.id, priority=i)
        for i, a in enumerate(assets)
    ])
    await db.flush()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/assignments/board")
        assert response.status_code == 200
        result = response.json()
        limited_response = await client.get("/assignments/board?limit_per_channel=60")
        assert limited_response.status_code == 200
        limited = limited_response.json()

    column = result["columns"][0]
    assert column["count"] == 501
    assert len(column["assets"]) == 501
    assert {a["id"] for a in column["assets"]} == {str(a.id) for a in assets}
    assert column["assets"][1]["scheduled_channel_ids"] == [str(channel.id)]
    assert column["assets"][2]["used_channel_ids"] == [str(channel.id)]

    assert limited["columns"][0]["count"] == 501
    assert len(limited["columns"][0]["assets"]) == 60
