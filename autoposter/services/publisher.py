"""Veröffentlichungs-Pipeline mit Idempotenz, Retry und Dry-Run."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.adapters import AdapterError, MediaRef, get_adapter
from autoposter.config import settings
from autoposter.models import (
    Channel,
    ChannelHealth,
    ExternalMediaRef,
    MediaAsset,
    Post,
    PostStatus,
    PostType,
    PublishAttempt,
    PostingPolicy,
)
from autoposter.services import credentials as cred_service
from autoposter.services import lifecycle, media as media_service, notify, planner, watermark


class PublishBlocked(Exception):
    """Fachlicher Abbruch ohne Retry."""


async def due_posts(db: AsyncSession, limit: int = 50) -> List[Post]:
    """Fällige Posts sperren, damit parallele Worker sie nicht doppelt greifen."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(Post)
        .where(Post.status == PostStatus.scheduled.value, Post.scheduled_at <= now)
        .order_by(Post.scheduled_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    try:
        return list((await db.execute(stmt)).scalars().all())
    except Exception:
        # SQLite kennt kein SKIP LOCKED
        fallback = (
            select(Post)
            .where(Post.status == PostStatus.scheduled.value, Post.scheduled_at <= now)
            .order_by(Post.scheduled_at)
            .limit(limit)
        )
        return list((await db.execute(fallback)).scalars().all())


async def _reset_quota_if_needed(db: AsyncSession, channel: Channel) -> None:
    now = datetime.now(timezone.utc)
    if not channel.quota_reset_at or channel.quota_reset_at <= now:
        channel.quota_used_today = 0
        channel.quota_reset_at = now + timedelta(days=1)
        db.add(channel)


async def _upload_all(
    db: AsyncSession, adapter, cred, channel: Channel, assets: Sequence[MediaAsset]
) -> List[MediaRef]:
    refs: List[MediaRef] = []
    now = datetime.now(timezone.utc)
    for asset in assets:
        cached = (
            await db.execute(
                select(ExternalMediaRef).where(
                    ExternalMediaRef.media_asset_id == asset.id,
                    ExternalMediaRef.channel_id == channel.id,
                )
            )
        ).scalar_one_or_none()
        if cached and (cached.expires_at is None or cached.expires_at > now):
            refs.append(MediaRef(asset_id=str(asset.id), external_id=cached.external_id))
            continue

        data = media_service.read_bytes(asset)
        # Wasserzeichen erst hier: Das Original bleibt unangetastet, und
        # dasselbe Bild kann auf zwei Kanälen mit verschiedenen Zeichen laufen.
        data = await asyncio.to_thread(watermark.apply_bytes, data, channel, mime=asset.mime)
        if channel.platform == 'x':
            ref = await adapter.upload_media(cred, asset, data, include_alt_text=channel.x_send_alt_text)
        else:
            ref = await adapter.upload_media(cred, asset, data)
        if cached:
            cached.external_id = ref.external_id
            cached.expires_at = ref.expires_at
            db.add(cached)
        else:
            db.add(
                ExternalMediaRef(
                    media_asset_id=asset.id,
                    channel_id=channel.id,
                    external_id=ref.external_id,
                    expires_at=ref.expires_at,
                )
            )
        await db.flush()
        refs.append(ref)
    return refs


async def split_thread_if_needed(db: AsyncSession, post: Post, channel: Channel) -> List[Post]:
    """Bildersets mit mehr Bildern als die Plattform erlaubt als Self-Thread zerlegen."""
    limits = get_adapter(channel.platform).limits()
    asset_ids = [str(a) for a in (post.media_asset_ids or [])]
    if len(asset_ids) <= limits.max_images_per_post:
        return [post]

    chunks = [
        asset_ids[i : i + limits.max_images_per_post]
        for i in range(0, len(asset_ids), limits.max_images_per_post)
    ]
    post.media_asset_ids = chunks[0]
    post.thread_position = 0
    db.add(post)
    children: List[Post] = []
    for index, chunk in enumerate(chunks[1:], start=1):
        child = Post(
            channel_id=post.channel_id,
            persona_id=post.persona_id,
            type=PostType.image_set.value,
            status=post.status,
            scheduled_at=post.scheduled_at,
            body_text="",
            media_asset_ids=chunk,
            alt_texts={k: v for k, v in (post.alt_texts or {}).items() if k in chunk},
            audience=post.audience,
            thread_parent_id=post.id,
            thread_position=index,
            generation_meta={"thread_of": str(post.id)},
        )
        db.add(child)
        children.append(child)
    await db.flush()
    return [post] + children


async def publish_post(
    db: AsyncSession, post_id: uuid.UUID, *, force: bool = False
) -> Tuple[bool, str]:
    """Einen Post veröffentlichen. Rückgabe: (erfolgreich, Meldung)."""
    post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
    if not post:
        return False, "Post nicht gefunden"

    # Idempotenz: bereits veröffentlicht -> nichts tun.
    if post.status == PostStatus.published.value and post.external_post_id:
        return True, "Bereits veröffentlicht"
    if post.status == PostStatus.publishing.value and not force:
        return False, "Wird bereits veröffentlicht"

    channel = (
        await db.execute(select(Channel).where(Channel.id == post.channel_id))
    ).scalar_one_or_none()
    if not channel:
        post.status = PostStatus.failed.value
        post.error_message = "Kanal fehlt"
        db.add(post)
        return False, post.error_message

    if settings.global_pause:
        return False, "Globaler Stopp aktiv"
    if not channel.is_active or channel.health == ChannelHealth.paused.value:
        return False, f"Kanal '{channel.display_name}' pausiert"

    issues = await planner.preflight(db, post)
    blocking = [i for i in issues if i["level"] == "error"]
    if blocking and not force:
        post.status = PostStatus.failed.value
        post.error_message = "; ".join(i["message"] for i in blocking)[:900]
        db.add(post)
        await notify.push(
            db,
            level="error",
            title=f"Preflight fehlgeschlagen: {channel.display_name}",
            body=post.error_message,
            entity="post",
            entity_id=str(post.id),
        )
        return False, post.error_message

    await _reset_quota_if_needed(db, channel)
    policy: PostingPolicy = channel.policy or PostingPolicy()
    if channel.quota_used_today >= policy.daily_api_quota and not force:
        post.error_message = "Tageskontingent des Kanals erschöpft"
        post.scheduled_at = datetime.now(timezone.utc) + timedelta(hours=6)
        db.add(post)
        return False, post.error_message

    post.status = PostStatus.publishing.value
    post.attempt_count += 1
    post.last_attempt_at = datetime.now(timezone.utc)
    db.add(post)
    await db.flush()

    attempt = PublishAttempt(post_id=post.id, dry_run=settings.dry_run)
    db.add(attempt)
    await db.flush()

    asset_ids = [uuid.UUID(str(a)) for a in (post.media_asset_ids or [])]
    assets = (
        list((await db.execute(select(MediaAsset).where(MediaAsset.id.in_(asset_ids)))).scalars().all())
        if asset_ids
        else []
    )
    order = {str(a): i for i, a in enumerate(post.media_asset_ids or [])}
    assets.sort(key=lambda a: order.get(str(a.id), 0))

    adapter = get_adapter(channel.platform)

    # ---------------- Dry-Run ---------------- #
    if settings.dry_run:
        post.status = PostStatus.published.value
        post.published_at = datetime.now(timezone.utc)
        post.external_post_id = f"dryrun-{post.id.hex[:12]}"
        post.external_url = ""
        post.error_message = ""
        attempt.outcome = "dry_run"
        attempt.http_status = 200
        attempt.finished_at = datetime.now(timezone.utc)
        attempt.response_excerpt = "Trockenlauf: es wurde nichts an die Plattform gesendet."
        db.add_all([post, attempt])
        await lifecycle.record_usage(
            db, asset_ids=[a.id for a in assets], channel_id=channel.id, post_id=post.id
        )
        channel.last_published_at = post.published_at
        db.add(channel)
        return True, "Trockenlauf erfolgreich"

    # ---------------- Echter Versand ---------------- #
    try:
        cred = await cred_service.get_valid_credentials(db, channel)
        refs = await _upload_all(db, adapter, cred, channel, assets)
        outcome = await adapter.publish(cred, channel, post, refs)
    except AdapterError as exc:
        attempt.finished_at = datetime.now(timezone.utc)
        attempt.http_status = exc.status
        attempt.outcome = "error"
        attempt.response_excerpt = str(exc)[:1000]
        db.add(attempt)

        if exc.needs_reauth:
            await cred_service.mark_reauth(db, channel, str(exc))
            await notify.push(
                db,
                level="error",
                title=f"Neu-Autorisierung nötig: {channel.display_name}",
                body=str(exc),
                entity="channel",
                entity_id=str(channel.id),
            )

        if exc.retryable and post.attempt_count < 5:
            delay = exc.retry_after or 60 * post.attempt_count
            post.status = PostStatus.scheduled.value
            post.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            post.error_message = f"Wiederholung in {delay}s: {exc}"[:900]
            db.add(post)
            return False, post.error_message

        post.status = PostStatus.failed.value
        post.error_message = str(exc)[:900]
        db.add(post)
        await notify.push(
            db,
            level="error",
            title=f"Veröffentlichung fehlgeschlagen: {channel.display_name}",
            body=post.error_message,
            entity="post",
            entity_id=str(post.id),
        )
        return False, post.error_message
    except Exception as exc:  # pragma: no cover - defensiv
        attempt.finished_at = datetime.now(timezone.utc)
        attempt.outcome = "error"
        attempt.response_excerpt = str(exc)[:1000]
        post.status = PostStatus.failed.value
        post.error_message = f"Unerwarteter Fehler: {exc}"[:900]
        db.add_all([post, attempt])
        return False, post.error_message

    post.status = PostStatus.published.value
    post.published_at = datetime.now(timezone.utc)
    post.external_post_id = outcome.external_post_id
    post.external_url = outcome.external_url
    post.error_message = ""
    attempt.finished_at = post.published_at
    attempt.http_status = outcome.http_status
    attempt.request_id = outcome.request_id
    attempt.outcome = "success"
    attempt.response_excerpt = str(outcome.raw)[:1000]

    channel.last_published_at = post.published_at
    channel.quota_used_today += 1
    db.add_all([post, attempt, channel])

    await lifecycle.record_usage(
        db, asset_ids=[a.id for a in assets], channel_id=channel.id, post_id=post.id
    )

    # Thread-Fortsetzungen unmittelbar anhängen
    children = (
        await db.execute(
            select(Post)
            .where(Post.thread_parent_id == post.id, Post.status != PostStatus.published.value)
            .order_by(Post.thread_position)
        )
    ).scalars().all()
    for child in children:
        child.generation_meta = {
            **(child.generation_meta or {}),
            "in_reply_to_tweet_id": outcome.external_post_id,
        }
        child.status = PostStatus.scheduled.value
        child.scheduled_at = datetime.now(timezone.utc)
        db.add(child)

    from autoposter.services import image_comments
    await db.commit()  # Image success must survive any later comment/config failure.
    comment = await image_comments.prepare(db, post, channel, assets)
    await db.commit()
    if comment:
        await image_comments.deliver(db, post.id)
    return True, "Veröffentlicht"


async def retry_failed(db: AsyncSession, hours: int = 2) -> int:
    """Fehlgeschlagene Posts erneut einplanen, sofern der Kanal das erlaubt."""
    posts = (
        await db.execute(select(Post).where(Post.status == PostStatus.failed.value))
    ).scalars().all()
    count = 0
    for post in posts:
        channel = (
            await db.execute(select(Channel).where(Channel.id == post.channel_id))
        ).scalar_one_or_none()
        policy = channel.policy if channel else None
        if not policy or not policy.auto_retry_on_fail or post.attempt_count >= 3:
            continue
        post.status = PostStatus.scheduled.value
        post.scheduled_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        db.add(post)
        count += 1
    return count
