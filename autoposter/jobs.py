"""Hintergrundjobs als reine async-Funktionen.

Diese Ebene kennt weder Celery noch den eingebauten Scheduler. Beide rufen
dieselben Funktionen auf – dadurch verhält sich der portable Betrieb ohne Redis
exakt wie der Betrieb mit Worker und Beat.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from autoposter.config import settings
from autoposter.db import session_scope
from autoposter.models import Channel, ChannelHealth, MediaAsset, MediaStatus, Post, PostStatus
from autoposter.services import (
    appconfig,
    credentials as cred_service,
    inventory,
    library,
    lifecycle,
    media as media_service,
    notify,
    planner,
    publisher,
)
from autoposter.services.llm import llm

logger = logging.getLogger("autoposter.jobs")


# --------------------------------------------------------------------------- #
# Veröffentlichung
# --------------------------------------------------------------------------- #
async def publish_one(post_id: uuid.UUID) -> Dict[str, Any]:
    """Einen einzelnen Post veröffentlichen (inkl. Thread-Aufteilung)."""
    async with session_scope() as db:
        post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
        if not post:
            return {"ok": False, "message": "Post nicht gefunden"}
        channel = (
            await db.execute(select(Channel).where(Channel.id == post.channel_id))
        ).scalar_one_or_none()
        if channel:
            await publisher.split_thread_if_needed(db, post, channel)
        # Falls ein Dispatcher den Post bereits auf 'publishing' gesetzt hat.
        if post.status == PostStatus.publishing.value:
            post.status = PostStatus.scheduled.value
            db.add(post)
            await db.flush()
        ok, message = await publisher.publish_post(db, post_id)
        return {"ok": ok, "message": message}


async def dispatch_due_posts(*, inline: bool = True, limit: int = 50) -> Dict[str, Any]:
    """Fällige Posts einsammeln.

    inline=True  -> direkt veröffentlichen (eingebauter Scheduler)
    inline=False -> nur markieren, die IDs zurückgeben (Celery reiht sie ein)
    """
    if settings.global_pause:
        return {"paused": True, "dispatched": 0, "post_ids": []}

    async with session_scope() as db:
        from autoposter.services.image_comments import deliver_pending
        await deliver_pending(db)
        posts = await publisher.due_posts(db, limit=limit)
        post_ids = [p.id for p in posts]
        for post in posts:
            post.status = PostStatus.publishing.value
            db.add(post)
        await db.flush()

    if not inline:
        return {"dispatched": len(post_ids), "post_ids": [str(p) for p in post_ids]}

    results = []
    for post_id in post_ids:
        try:
            results.append(await publish_one(post_id))
        except Exception as exc:  # pragma: no cover - defensiv
            logger.exception("Veröffentlichung von %s fehlgeschlagen", post_id)
            results.append({"ok": False, "message": str(exc)})
    return {
        "dispatched": len(post_ids),
        "published": sum(1 for r in results if r.get("ok")),
        "failed": sum(1 for r in results if not r.get("ok")),
    }


async def retry_failed(hours: int = 2) -> Dict[str, Any]:
    async with session_scope() as db:
        return {"rescheduled": await publisher.retry_failed(db, hours)}


# --------------------------------------------------------------------------- #
# Planung
# --------------------------------------------------------------------------- #
async def generate_plan(days: int = 14) -> Dict[str, Any]:
    """Kalenderlücken nach den Kanal-Regeln schließen.

    Standardmäßig abgeschaltet. Wer seinen Kalender selbst plant, wird sonst
    davon überrascht, dass stündlich Posts über zwei Wochen hinweg von allein
    dazukommen – und zwar nach den Kanal-Regeln, nicht nach den Vorgaben, die
    er im Planungsdialog gemacht hat.
    """
    if settings.global_pause:
        return {"paused": True}
    async with session_scope() as db:
        if not await appconfig.get(db, "auto_plan_enabled", False):
            return {"skipped": "automatisches Nachplanen ist ausgeschaltet"}

        # Der Scheduler ruft stündlich auf, die eigentliche Taktung steht hier.
        # So lässt sie sich zur Laufzeit ändern, ohne den Dienst neu zu starten.
        hours = int(await appconfig.get(db, "auto_plan_interval_hours", 24) or 24)
        last_raw = str(await appconfig.get(db, "auto_plan_last_run", "") or "")
        if last_raw:
            try:
                last = datetime.fromisoformat(last_raw)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - last < timedelta(hours=max(1, hours)):
                    return {"skipped": f"nächster Lauf frühestens {hours} h nach dem letzten"}
            except (ValueError, TypeError):
                pass  # unlesbarer Zeitstempel -> jetzt laufen und neu setzen

        channel_ids = list((await db.execute(
            select(Channel.id).where(
                Channel.is_active.is_(True), Channel.auto_plan_enabled.is_(True)
            )
        )).scalars().all())
        # Eine leere Auswahl darf nicht als „alle Kanäle“ beim Planer ankommen.
        if not channel_ids:
            return {"skipped": "kein Kanal für automatisches Nachplanen eingeschaltet"}

        await appconfig.set_many(
            db, {"auto_plan_last_run": datetime.now(timezone.utc).isoformat()}
        )
        days = int(await appconfig.get(db, "auto_plan_days", days) or days)
        result = await planner.fill_calendar(db, channel_ids=channel_ids, days=days, dry_run=False)
        for gap in result.gaps[:10]:
            await notify.push(
                db,
                level="warning",
                title=f"Kalenderlücke: {gap['channel_name']}",
                body=gap["reason"],
                entity="channel",
                entity_id=gap["channel_id"],
                external=False,
            )
        return {"created": len(result.created_post_ids), "gaps": len(result.gaps)}


async def update_check_cycle() -> Dict[str, Any]:
    """Nach neuen Versionen sehen. Die Drosselung sitzt in updater.is_due()."""
    from autoposter.services import updater

    async with session_scope() as db:
        try:
            return await updater.cycle(db)
        except Exception as exc:  # noqa: BLE001 – nie den Zyklus abbrechen
            logger.warning("Update-Prüfung fehlgeschlagen: %s", exc)
            return {"error": str(exc)[:200]}


# --------------------------------------------------------------------------- #
# Medien
# --------------------------------------------------------------------------- #
async def describe_assets(limit: int = 20) -> Dict[str, Any]:
    """Fehlende KI-Bildbeschreibungen nachziehen (Basis für Captions und Alt-Texte)."""
    if not settings.openrouter_api_key:
        return {"skipped": "kein OPENROUTER_API_KEY"}
    async with session_scope() as db:
        assets = (
            await db.execute(
                select(MediaAsset)
                .where(
                    MediaAsset.ai_description == "",
                    MediaAsset.status == MediaStatus.ready.value,
                )
                .limit(limit)
            )
        ).scalars().all()
        # Dieselbe Funktion, die auch beim Generieren einspringt – ein Weg,
        # ein Verhalten.
        done = await media_service.ensure_descriptions(db, assets)
        await db.flush()
    library.invalidate_counts()
    return {"described": done}


# --------------------------------------------------------------------------- #
# Kennzahlen & Betrieb
# --------------------------------------------------------------------------- #
async def refresh_metrics(hours: int = 72) -> Dict[str, Any]:
    from autoposter.adapters import get_adapter

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    updated = 0
    async with session_scope() as db:
        posts = (
            await db.execute(
                select(Post).where(
                    Post.status == PostStatus.published.value,
                    Post.published_at >= since,
                    Post.external_post_id.isnot(None),
                )
            )
        ).scalars().all()
        for post in posts:
            if str(post.external_post_id).startswith("dryrun-"):
                continue
            channel = (
                await db.execute(select(Channel).where(Channel.id == post.channel_id))
            ).scalar_one_or_none()
            if not channel or not cred_service.is_connected(channel):
                continue
            try:
                cred = await cred_service.get_valid_credentials(db, channel)
                metrics = await get_adapter(channel.platform).fetch_metrics(
                    cred, post.external_post_id
                )
            except Exception:
                continue
            if metrics:
                post.metrics = metrics
                db.add(post)
                updated += 1
    return {"updated": updated}


async def inventory_check() -> Dict[str, Any]:
    async with session_scope() as db:
        low = await inventory.low_inventory_channels(db)
        for entry in low:
            await notify.push(
                db,
                level="warning",
                title=f"Bildvorrat knapp: {entry.channel_name}",
                body=(
                    f"Noch {entry.available} Bilder, reicht ca. {entry.days_left} Tage "
                    f"(bis {entry.empty_on.date() if entry.empty_on else '?'})."
                ),
                entity="channel",
                entity_id=str(entry.channel_id),
            )
        return {"warned": len(low)}


async def nightly_maintenance() -> Dict[str, Any]:
    async with session_scope() as db:
        refreshed = await lifecycle.refresh_all(db)
    library.invalidate_counts()
    return {"counters_refreshed": refreshed}


async def token_health() -> Dict[str, Any]:
    checked = 0
    async with session_scope() as db:
        channels = (
            await db.execute(select(Channel).where(Channel.is_active.is_(True)))
        ).scalars().all()
        for channel in channels:
            if not cred_service.is_connected(channel):
                continue
            try:
                await cred_service.get_valid_credentials(db, channel)
                if channel.health == ChannelHealth.error.value:
                    channel.health = ChannelHealth.ok.value
                    channel.health_note = ""
                    db.add(channel)
                checked += 1
            except Exception as exc:
                await notify.push(
                    db,
                    level="error",
                    title=f"Token-Problem: {channel.display_name}",
                    body=str(exc)[:400],
                    entity="channel",
                    entity_id=str(channel.id),
                )
    return {"checked": checked}
