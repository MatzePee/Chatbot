"""Getrennte Zeitfenster, Versatz und Fanvue Free-/Sub-Posts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from autoposter.models import Audience, PostingPolicy
from autoposter.schemas import AssignmentBatch
from autoposter.services import assignment, media as media_service, planner
from tests.autoposter.test_duplicates import make_channel, make_image

TZ = ZoneInfo("Europe/Berlin")
START = datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc)


def policy(**kw) -> PostingPolicy:
    base = dict(
        posts_per_day=1,
        min_gap_minutes=120,
        jitter_minutes=0,
        stagger_minutes=0,
        text_only_ratio=0.0,
        free_post_ratio=0.3,
        allowed_time_windows=[{"dow": [0, 1, 2, 3, 4, 5, 6], "from": "09:00", "to": "21:00"}],
        image_time_windows=[],
        text_time_windows=[],
    )
    base.update(kw)
    return PostingPolicy(**base)


class FakeChannel:
    timezone = "Europe/Berlin"


def test_getrennte_zeitfenster_je_posttyp():
    """Bilder abends, Textposts morgens – die Fenster dürfen sich nicht mischen."""
    p = policy(
        image_time_windows=[{"dow": [0, 1, 2, 3, 4, 5, 6], "from": "18:00", "to": "22:00"}],
        text_time_windows=[{"dow": [0, 1, 2, 3, 4, 5, 6], "from": "07:00", "to": "09:00"}],
        text_only_ratio=0.5,
    )
    typed = planner.typed_slots(FakeChannel(), p, START, 3)
    image_hours = {s.astimezone(TZ).hour for s, kind in typed if kind == "image"}
    text_hours = {s.astimezone(TZ).hour for s, kind in typed if kind == "text"}

    assert image_hours, "es müssen Bild-Slots entstehen"
    assert text_hours, "es müssen Text-Slots entstehen"
    assert all(18 <= h <= 22 for h in image_hours)
    assert all(7 <= h <= 9 for h in text_hours)


def test_ohne_eigene_fenster_gilt_das_allgemeine():
    p = policy()
    assert planner.windows_for(p, "image") == p.allowed_time_windows
    assert planner.windows_for(p, "text") == p.allowed_time_windows


def test_versatz_verschiebt_alle_slots():
    """stagger_minutes sorgt dafür, dass zwei Kanäle nicht zur selben Minute posten."""
    windows = policy().allowed_time_windows
    a = planner.slots_from_windows(FakeChannel(), policy(), START, 2, windows, 1)
    b = planner.slots_from_windows(
        FakeChannel(), policy(stagger_minutes=45), START, 2, windows, 1
    )
    assert a and b
    assert {int((y - x).total_seconds() // 60) for x, y in zip(a, b)} == {45}


def test_mindestabstand_zu_anderen_kanaelen():
    base = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    slots = [base, base + timedelta(minutes=10), base + timedelta(minutes=60)]
    foreign = [base + timedelta(minutes=5)]

    accepted = planner.free_slots(
        slots, [], 0, [], foreign=foreign, cross_channel_gap_minutes=20
    )
    assert accepted == [base + timedelta(minutes=60)]

    # Ohne Cross-Gap bleibt alles erhalten.
    assert len(planner.free_slots(slots, [], 0, [], foreign=foreign)) == 3


@pytest.mark.parametrize(
    "ratio,expected_free",
    [(0.0, 0), (0.3, 3), (0.5, 5), (1.0, 10)],
)
def test_free_sub_verteilung(ratio, expected_free):
    """Die Verteilung ist deterministisch, damit das Verhältnis auch bei
    wenigen Posts stimmt (nicht zufällig gewürfelt)."""
    free_count = 0
    audiences = []
    for index in range(1, 11):
        if free_count < ratio * index:
            audiences.append(Audience.free.value)
            free_count += 1
        else:
            audiences.append(Audience.subscribers.value)
    assert audiences.count(Audience.free.value) == expected_free


def test_audience_werte_entsprechen_der_fanvue_api():
    assert Audience.free.value == "followers-and-subscribers"
    assert Audience.subscribers.value == "subscribers"


@pytest.mark.asyncio
async def test_planer_setzt_audience_bei_fanvue(db):
    channel = await make_channel(db, "FV Plan", "fanvue")
    channel.policy.free_post_ratio = 1.0  # alles als Free-Post
    channel.policy.posts_per_day = 2
    channel.default_audience = Audience.subscribers.value
    db.add(channel.policy)
    await db.flush()

    assets = [
        await media_service.ingest(db, filename=f"fv{i}.jpg", data=make_image(200 + i))
        for i in range(4)
    ]
    await assignment.assign(
        db, AssignmentBatch(asset_ids=[a.id for a in assets], channel_ids=[channel.id])
    )

    result = await planner.fill_calendar(db, channel_ids=[channel.id], days=3, dry_run=True)
    assert result.slots, "es müssen Slots geplant werden"
    assert all(s.audience == Audience.free.value for s in result.slots)
