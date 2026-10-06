"""Reconcile dashboard warnings with current state, preserving their history."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.models import AppSetting, Channel, Notification, Post, PostStatus, XImageComment
from autoposter.services import credentials, inventory, notify, planner


def kind(note):
    if note.entity == "post" and note.title == "Bild veröffentlicht · X-Kommentar prüfen":
        return "comment"
    for prefix, value in (
        ("Bildvorrat knapp:", "inventory"), ("Token-Problem:", "token"),
        ("Neu-Autorisierung nötig:", "token"), ("Kalenderlücke:", "calendar"),
        ("Preflight fehlgeschlagen:", "post"), ("Veröffentlichung fehlgeschlagen:", "post"),
    ):
        if note.title.startswith(prefix):
            expected = "post" if value == "post" else "channel"
            return value if note.entity == expected else None
    return None


def is_preflight_failure(post):
    error = post.error_message or ""
    return bool((post.generation_meta or {}).get("preflight_failure")
                or "Kanalstatus:" in error or " lief bereits auf diesem Kanal" in error)


async def failure_superseded(db, post):
    """An old validation failure is resolved when its media was subsequently sent."""
    if post.status != PostStatus.failed.value or not is_preflight_failure(post):
        return False
    asset_ids = {str(uuid.UUID(str(asset_id))) for asset_id in (post.media_asset_ids or [])}
    if not asset_ids:
        return False
    failed_at = post.last_attempt_at or post.scheduled_at or post.created_at
    if not failed_at:
        return False
    published = (await db.execute(select(Post).where(
        Post.channel_id == post.channel_id,
        Post.status == PostStatus.published.value,
        Post.published_at > failed_at,
    ))).scalars().all()
    return any(asset_ids == {str(uuid.UUID(str(asset_id))) for asset_id in (later.media_asset_ids or [])}
               for later in published)


async def current_failure(db, post):
    """Recheck validation failures without retrying or changing a historical post."""
    preflight = is_preflight_failure(post)
    if not preflight:
        return post.error_message, False
    blocking = [issue for issue in await planner.preflight(db, post) if issue["level"] == "error"]
    if blocking:
        return "; ".join(issue["message"] for issue in blocking), False
    return "Die Prüfung ist jetzt erfolgreich. Dieser Post wurde damals nicht gesendet und kann im Kalender erneut freigegeben werden.", True


async def recheck_channel_failures(db, channel_id):
    notes = (await db.execute(select(Notification).where(Notification.is_read.is_(False), Notification.entity == "post"))).scalars().all()
    for note in notes:
        if kind(note) != "post":
            continue
        try:
            post = await db.get(Post, uuid.UUID(note.entity_id))
        except (ValueError, TypeError):
            continue
        if post is None or post.channel_id != channel_id:
            continue
        message, ready = await current_failure(db, post)
        if ready or post.status != PostStatus.failed.value or await failure_superseded(db, post):
            note.is_read = True
        else:
            note.body = message
        db.add(note)
    await db.flush()


async def check(db: AsyncSession) -> dict:
    now = datetime.now(timezone.utc)
    low = {str(e.channel_id): e for e in await inventory.low_inventory_channels(db)}
    notes = list((await db.execute(select(Notification).where(
        Notification.is_read.is_(False)
    ).order_by(Notification.created_at.desc(), Notification.id))).scalars().all())
    channels = {str(c.id): c for c in (await db.execute(select(Channel))).scalars().all()}
    seen, token_ok, gaps = set(), {}, {}
    result = {"checked": len(notes), "resolved": 0, "updated": 0, "created": 0, "errors": []}

    for note in notes:
        category = kind(note)
        if not category:
            continue  # An event with no verifiable condition must not be dismissed.
        key = (category, note.entity_id)
        active = True
        try:
            if category == "inventory":
                active = note.entity_id in low
            elif category in ("token", "calendar"):
                channel = channels.get(note.entity_id)
                active = bool(channel and channel.is_active)
                if active and category == "token":
                    if note.entity_id not in token_ok:
                        health = channel.health
                        if channel.platform == "fanvue":
                            from autoposter.services.chatbot_fanvue import health as shared_health
                            health = shared_health(channel)
                        expiry = await credentials.token_expiry(db, channel)
                        token_ok[note.entity_id] = credentials.is_connected(channel) and health == "ok" and (expiry is None or expiry > now)
                    active = not token_ok[note.entity_id]
                elif active:
                    if note.entity_id not in gaps:
                        plan = await planner.fill_calendar(db, channel_ids=[channel.id], days=14, dry_run=True, generate_text=False)
                        gaps[note.entity_id] = plan.gaps
                    active = bool(gaps[note.entity_id])
                    if active:
                        note.body = gaps[note.entity_id][0]["reason"]
            elif category == "comment":
                comment = await db.get(XImageComment, uuid.UUID(note.entity_id))
                active = bool(comment and comment.state != "sent")
            else:
                post = await db.get(Post, uuid.UUID(note.entity_id))
                active = bool(post and post.status == PostStatus.failed.value)
                if active:
                    active = not await failure_superseded(db, post)
                if active:
                    note.body, ready = await current_failure(db, post)
                    active = not ready
        except Exception as exc:
            result["errors"].append({"id": str(note.id), "error": str(exc)[:300]})
            continue
        if not active or key in seen:
            note.is_read = True
            result["resolved"] += 1
        else:
            seen.add(key)
            result["updated"] += 1
            if category == "inventory":
                entry = low[note.entity_id]
                note.title = f"Bildvorrat knapp: {entry.channel_name}"
                note.body = inventory_body(entry)
        db.add(note)

    for channel_id, entry in low.items():
        if ("inventory", channel_id) not in seen:
            await notify.push(db, level="warning", title=f"Bildvorrat knapp: {entry.channel_name}",
                              body=inventory_body(entry), entity="channel", entity_id=channel_id, external=False)
            result["created"] += 1
    stamp = await db.get(AppSetting, "problems_check")
    if stamp is None:
        stamp = AppSetting(key="problems_check")
    stamp.value = {"checked_at": now.isoformat()}
    db.add(stamp)
    await db.flush()
    return result


def inventory_body(entry):
    return (f"Noch {entry.available} Bilder, reicht ca. {entry.days_left} Tage "
            f"(bis {entry.empty_on.date() if entry.empty_on else '?'}).")


async def check_if_due(db: AsyncSession):
    stamp = await db.get(AppSetting, "problems_check")
    raw = (stamp.value or {}).get("checked_at", "") if stamp else ""
    try:
        previous = datetime.fromisoformat(raw) if raw else None
        if previous and previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        previous = None
    if previous is None or (datetime.now(timezone.utc) - previous).total_seconds() >= 3600:
        return await check(db)
    return None