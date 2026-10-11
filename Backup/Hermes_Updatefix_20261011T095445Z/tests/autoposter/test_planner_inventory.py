"""Slot-Berechnung, Asset-Auswahl und Reichweiten-Formel."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from autoposter.models import PostingPolicy
from autoposter.schemas import AssignmentBatch
from autoposter.services import assignment, inventory, media as media_service, planner
from tests.autoposter.test_duplicates import make_channel, make_image


@pytest.mark.asyncio
async def test_slots_respektieren_zeitfenster_und_abstand(db):
    channel = await make_channel(db, "Slots", "x")
    policy = PostingPolicy(
        posts_per_day=2,
        min_gap_minutes=240,
        jitter_minutes=0,
        allowed_time_windows=[{"dow": [0, 1, 2, 3, 4, 5, 6], "from": "10:00", "to": "20:00"}],
    )
    start = datetime.now(timezone.utc)
    slots = planner.candidate_slots(channel, policy, start, days=3)
    assert slots
    free = planner.free_slots(slots, [], policy.min_gap_minutes, [])
    for earlier, later in zip(free, free[1:]):
        assert (later - earlier).total_seconds() >= policy.min_gap_minutes * 60


@pytest.mark.asyncio
async def test_planer_greift_nur_auf_zugeordnete_bilder_zu(db):
    channel = await make_channel(db, "Strict", "x")
    frei = await media_service.ingest(db, filename="frei.jpg", data=make_image(31))
    zugeordnet = await media_service.ingest(db, filename="zu.jpg", data=make_image(32))
    await assignment.assign(
        db, AssignmentBatch(asset_ids=[zugeordnet.id], channel_ids=[channel.id])
    )

    picked, _ = await planner.pick_assets(db, channel, channel.policy, count=5)
    ids = [a.id for a in picked]
    assert zugeordnet.id in ids
    assert frei.id not in ids


@pytest.mark.asyncio
async def test_leerer_pool_erzeugt_luecke_statt_zufallsbild(db):
    channel = await make_channel(db, "Leer", "x")
    await media_service.ingest(db, filename="ignoriert.jpg", data=make_image(33))
    result = await planner.fill_calendar(db, channel_ids=[channel.id], days=2, dry_run=True)
    assert result.slots == [] or all(not s.media_asset_ids for s in result.slots)
    assert result.gaps


@pytest.mark.asyncio
async def test_reichweite_wird_pro_kanal_ohne_doppelabzug_gerechnet(db):
    x = await make_channel(db, "RX", "x")
    fanvue = await make_channel(db, "RF", "fanvue")
    assets = [
        await media_service.ingest(db, filename=f"r{i}.jpg", data=make_image(40 + i))
        for i in range(10)
    ]
    await assignment.assign(
        db,
        AssignmentBatch(asset_ids=[a.id for a in assets], channel_ids=[x.id, fanvue.id]),
    )

    overview = await inventory.overview(db)
    per_channel = {c.channel_name: c for c in overview.channels}

    # Beide Kanäle sehen den vollen Pool — das Bild wird nicht aufgeteilt.
    assert per_channel["RX"].available == 10
    assert per_channel["RF"].available == 10
    assert per_channel["RX"].days_left and per_channel["RX"].days_left > 0
    assert overview.unassigned_assets == 0


@pytest.mark.asyncio
async def test_reichweite_meldet_rot_bei_wenig_material(db):
    channel = await make_channel(db, "Knapp", "x")
    asset = await media_service.ingest(db, filename="knapp.jpg", data=make_image(60))
    await assignment.assign(db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[channel.id]))
    overview = await inventory.overview(db)
    entry = next(c for c in overview.channels if c.channel_name == "Knapp")
    assert entry.traffic_light == "red"
