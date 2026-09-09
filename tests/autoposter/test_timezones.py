"""Zeitstempel müssen immer mit UTC-Zeitzone aus der Datenbank kommen.

SQLite speichert keine Zeitzone. Ohne den UTCDateTime-Typ kommen naive Werte
zurück, und jeder Vergleich mit datetime.now(timezone.utc) wirft
"can't compare offset-naive and offset-aware datetimes" — genau daran ist der
Verbindungstest gescheitert.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from autoposter.crypto import encrypt
from autoposter.models import Channel, ChannelCredential, MediaAsset, Post, PostStatus, UTCDateTime
from autoposter.services import credentials as cred_service
from tests.autoposter.test_duplicates import make_channel, make_image
from autoposter.services import media as media_service

BERLIN = timezone(timedelta(hours=2))


def test_typ_normalisiert_beim_schreiben():
    t = UTCDateTime()
    aware = datetime(2026, 8, 7, 11, 6, tzinfo=timezone.utc)
    assert t.process_bind_param(None, None) is None
    assert t.process_bind_param(aware, None) == aware
    # Naive Eingaben gelten als UTC.
    assert t.process_bind_param(datetime(2026, 8, 7, 11, 6), None) == aware
    # Andere Zonen werden umgerechnet.
    assert t.process_bind_param(datetime(2026, 8, 7, 13, 6, tzinfo=BERLIN), None) == aware


def test_typ_heftet_beim_lesen_utc_an():
    t = UTCDateTime()
    naive = datetime(2026, 8, 7, 11, 6)
    back = t.process_result_value(naive, None)
    assert back.tzinfo is timezone.utc
    # Und der Vergleich, an dem es vorher scheiterte, funktioniert jetzt.
    assert back < datetime.now(timezone.utc) + timedelta(days=3650)
    assert t.process_result_value(None, None) is None


@pytest.mark.asyncio
async def test_token_ablauf_ist_vergleichbar(db):
    """Der konkrete Fall aus dem Verbindungstest."""
    credential = ChannelCredential(
        platform="x",
        access_token_enc=encrypt("token"),
        refresh_token_enc=encrypt("refresh"),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db.add(credential)
    await db.flush()

    loaded = (
        await db.execute(
            select(ChannelCredential).where(ChannelCredential.id == credential.id)
        )
    ).scalar_one()

    assert loaded.expires_at.tzinfo is not None, "Token-Ablauf ohne Zeitzone"
    # Genau diese Zeile stand in get_valid_credentials und ist vorher explodiert.
    assert loaded.expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_alle_zeitspalten_kommen_mit_zeitzone(db):
    channel = await make_channel(db, "TZ", "x")
    channel.last_verified_at = datetime.now(timezone.utc)
    channel.quota_reset_at = datetime.now(timezone.utc) + timedelta(days=1)
    db.add(channel)

    asset = await media_service.ingest(db, filename="tz.jpg", data=make_image(300))
    post = Post(
        channel_id=channel.id,
        status=PostStatus.scheduled.value,
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
        body_text="Zeitzonen",
    )
    db.add(post)
    await db.flush()

    fresh_channel = (
        await db.execute(select(Channel).where(Channel.id == channel.id))
    ).scalar_one()
    fresh_asset = (
        await db.execute(select(MediaAsset).where(MediaAsset.id == asset.id))
    ).scalar_one()
    fresh_post = (await db.execute(select(Post).where(Post.id == post.id))).scalar_one()

    now = datetime.now(timezone.utc)
    for obj, field in (
        (fresh_channel, "created_at"),
        (fresh_channel, "last_verified_at"),
        (fresh_channel, "quota_reset_at"),
        (fresh_asset, "created_at"),
        (fresh_post, "created_at"),
        (fresh_post, "scheduled_at"),
    ):
        value = getattr(obj, field)
        assert value is not None, f"{field} fehlt"
        assert value.tzinfo is not None, f"{type(obj).__name__}.{field} ohne Zeitzone"
        # Vergleich darf nicht werfen
        _ = value < now or value > now


@pytest.mark.asyncio
async def test_get_valid_credentials_ohne_refresh(db):
    """Gültiges Token: kein Refresh, kein Zeitzonenfehler."""
    credential = ChannelCredential(
        platform="x",
        access_token_enc=encrypt("live-token"),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=5),
    )
    db.add(credential)
    await db.flush()

    channel = await make_channel(db, "Cred", "x")
    channel.credential_id = credential.id
    db.add(channel)
    await db.flush()

    cred = await cred_service.get_valid_credentials(db, channel)
    assert cred.access_token == "live-token"
    assert cred.expires_at.tzinfo is not None
