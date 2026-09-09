"""Lebenszyklus- und Zählerpflege für Medien.

Die denormalisierten Felder auf `media_asset` (lifecycle, *_channel_ids, usage_count,
last_used_at) sind die Grundlage dafür, dass die Bibliothek auch bei 50.000+ Bildern
in Millisekunden filtert. Sie werden hier nach jeder relevanten Änderung neu berechnet
— nicht bei jedem Lesezugriff.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Set

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.models import (
    Lifecycle,
    MediaAsset,
    MediaAssignment,
    MediaChannelUsage,
    MediaSetItem,
    MediaStatus,
    Post,
    PostStatus,
)

ACTIVE_SCHEDULE_STATES = (
    PostStatus.draft.value,
    PostStatus.needs_review.value,
    PostStatus.approved.value,
    PostStatus.scheduled.value,
    PostStatus.publishing.value,
)


async def _assigned_channels(db: AsyncSession, asset_id: uuid.UUID) -> Set[str]:
    """Direkte Zuordnungen plus solche, die über ein Set vererbt werden."""
    direct = await db.execute(
        select(MediaAssignment.channel_id).where(MediaAssignment.media_asset_id == asset_id)
    )
    channels = {str(c) for c in direct.scalars().all()}

    set_ids = (
        await db.execute(
            select(MediaSetItem.media_set_id).where(MediaSetItem.media_asset_id == asset_id)
        )
    ).scalars().all()
    if set_ids:
        inherited = await db.execute(
            select(MediaAssignment.channel_id).where(MediaAssignment.media_set_id.in_(set_ids))
        )
        channels |= {str(c) for c in inherited.scalars().all()}
    return channels


async def _used_channels(db: AsyncSession, asset_id: uuid.UUID) -> Set[str]:
    rows = await db.execute(
        select(MediaChannelUsage.channel_id).where(MediaChannelUsage.media_asset_id == asset_id)
    )
    return {str(c) for c in rows.scalars().all()}


async def _scheduled_channels(db: AsyncSession, asset_id: uuid.UUID) -> Set[str]:
    """Posts, in denen das Asset steckt und die noch nicht veröffentlicht sind."""
    rows = await db.execute(
        select(Post.channel_id, Post.media_asset_ids).where(
            Post.status.in_(ACTIVE_SCHEDULE_STATES)
        )
    )
    target = str(asset_id)
    return {
        str(channel_id)
        for channel_id, asset_ids in rows.all()
        if asset_ids and target in [str(a) for a in asset_ids]
    }


def compute_lifecycle(
    *,
    status: str,
    assigned: Sequence[str],
    used: Sequence[str],
    scheduled: Sequence[str],
) -> str:
    if status == MediaStatus.archived.value:
        return Lifecycle.archived.value
    assigned_set, used_set = set(assigned), set(used)
    if not assigned_set:
        return Lifecycle.new.value
    if used_set >= assigned_set:
        return Lifecycle.fully_used.value
    if used_set:
        return Lifecycle.partially_used.value
    if scheduled:
        return Lifecycle.scheduled.value
    return Lifecycle.assigned.value


async def refresh_asset(db: AsyncSession, asset: MediaAsset) -> MediaAsset:
    """Alle denormalisierten Felder eines Assets neu berechnen."""
    assigned = await _assigned_channels(db, asset.id)
    used = await _used_channels(db, asset.id)
    scheduled = await _scheduled_channels(db, asset.id)

    usage_rows = (
        await db.execute(
            select(
                func.count(MediaChannelUsage.id),
                func.min(MediaChannelUsage.used_at),
                func.max(MediaChannelUsage.used_at),
            ).where(MediaChannelUsage.media_asset_id == asset.id)
        )
    ).one()

    asset.assigned_channel_ids = sorted(assigned)
    asset.used_channel_ids = sorted(used)
    asset.scheduled_channel_ids = sorted(scheduled)
    asset.usage_count = int(usage_rows[0] or 0)
    asset.first_used_at = usage_rows[1]
    asset.last_used_at = usage_rows[2]
    asset.lifecycle = compute_lifecycle(
        status=asset.status,
        assigned=asset.assigned_channel_ids,
        used=asset.used_channel_ids,
        scheduled=asset.scheduled_channel_ids,
    )
    db.add(asset)
    return asset


async def refresh_assets(db: AsyncSession, asset_ids: Iterable[uuid.UUID]) -> int:
    ids: List[uuid.UUID] = list({a for a in asset_ids})
    if not ids:
        return 0
    assets = (
        await db.execute(select(MediaAsset).where(MediaAsset.id.in_(ids)))
    ).scalars().all()
    for asset in assets:
        await refresh_asset(db, asset)
    await db.flush()
    return len(assets)


async def refresh_all(db: AsyncSession, batch_size: int = 200) -> int:
    """Vollständige Neuberechnung — für Wartung und als Test-Referenz.

    Wird je Stapel festgeschrieben. Auf SQLite gäbe eine einzige große
    Transaktion sonst minutenlang die Schreibsperre nicht frei und parallele
    Zugriffe (etwa ein Upload) scheiterten mit "database is locked".
    """
    total = 0
    offset = 0
    while True:
        assets = (
            await db.execute(
                select(MediaAsset).order_by(MediaAsset.created_at).offset(offset).limit(batch_size)
            )
        ).scalars().all()
        if not assets:
            break
        for asset in assets:
            await refresh_asset(db, asset)
        await db.commit()
        total += len(assets)
        offset += batch_size
    return total


async def record_usage(
    db: AsyncSession,
    *,
    asset_ids: Sequence[uuid.UUID],
    channel_id: uuid.UUID,
    post_id: Optional[uuid.UUID],
    used_at: Optional[datetime] = None,
) -> None:
    """Nach erfolgreicher Veröffentlichung aufrufen."""
    for asset_id in asset_ids:
        entry = MediaChannelUsage(
            media_asset_id=asset_id,
            channel_id=channel_id,
            post_id=post_id,
        )
        if used_at:
            entry.used_at = used_at
        db.add(entry)
    await db.flush()
    await refresh_assets(db, asset_ids)
