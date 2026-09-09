"""Bestands-Reichweite: Wie lange reicht der zugeordnete Bildvorrat je Kanal?

Wichtig: Ein Asset, das mehreren Kanälen zugeordnet ist, wird NICHT aufgeteilt und
NICHT doppelt abgezogen — dasselbe Bild darf auf X und auf Fanvue laufen. Konkurrenz
um denselben Pool entsteht nur, wenn Kanäle über `exclusive_pool_group` explizit
zusammengefasst wurden.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config import settings
from autoposter.models import (
    Channel,
    Lifecycle,
    MediaAsset,
    MediaStatus,
    Post,
    PostStatus,
    PostingPolicy,
)
from autoposter.schemas import ChannelInventory, InventoryOverview
from autoposter.services import assignment


def traffic_light(days_left: Optional[float]) -> str:
    if days_left is None:
        return "grey"
    if days_left >= 30:
        return "green"
    if days_left >= 10:
        return "amber"
    return "red"


async def _avg_images_per_post(db: AsyncSession, channel_id: uuid.UUID) -> float:
    posts = (
        await db.execute(
            select(Post.media_asset_ids)
            .where(Post.channel_id == channel_id, Post.status == PostStatus.published.value)
            .order_by(Post.published_at.desc())
            .limit(50)
        )
    ).scalars().all()
    counts = [len(p) for p in posts if p]
    if not counts:
        return 1.0
    return max(1.0, sum(counts) / len(counts))


async def channel_inventory(db: AsyncSession, channel: Channel) -> ChannelInventory:
    policy: Optional[PostingPolicy] = channel.policy
    posts_per_day = policy.posts_per_day if policy else 1.0
    text_ratio = policy.text_only_ratio if policy else 0.0

    pool = await assignment.channel_pool(db, channel.id, include_used=True)
    assigned_total = len(pool)

    used_ids = {a.id for a in pool if str(channel.id) in (a.used_channel_ids or [])}
    scheduled_ids = {a.id for a in pool if str(channel.id) in (a.scheduled_channel_ids or [])}

    cooldown_days = policy.reuse_cooldown_days if policy else 0
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=cooldown_days) if cooldown_days else None
    )

    available = 0
    for asset in pool:
        if asset.status != MediaStatus.ready.value:
            continue
        if assignment.nsfw_rank(asset.nsfw_level) > assignment.nsfw_rank(channel.nsfw_level):
            continue
        if asset.id in used_ids:
            if not cutoff or not asset.last_used_at or asset.last_used_at > cutoff:
                continue
        available += 1

    avg_images = await _avg_images_per_post(db, channel.id)
    consumption = max(0.01, posts_per_day * (1.0 - text_ratio) * avg_images)
    days_left = available / consumption if consumption else None
    empty_on = (
        datetime.now(timezone.utc) + timedelta(days=days_left) if days_left is not None else None
    )

    shares: List[uuid.UUID] = []
    if policy and policy.exclusive_pool_group:
        rows = (
            await db.execute(
                select(Channel.id)
                .join(PostingPolicy, Channel.policy_id == PostingPolicy.id)
                .where(
                    PostingPolicy.exclusive_pool_group == policy.exclusive_pool_group,
                    Channel.id != channel.id,
                )
            )
        ).scalars().all()
        shares = list(rows)

    return ChannelInventory(
        channel_id=channel.id,
        channel_name=channel.display_name,
        platform=channel.platform,
        color=channel.color,
        assigned_total=assigned_total,
        available=available,
        used=len(used_ids),
        scheduled=len(scheduled_ids),
        consumption_per_day=round(consumption, 2),
        days_left=round(days_left, 1) if days_left is not None else None,
        empty_on=empty_on,
        traffic_light=traffic_light(days_left),
        shares_pool_with=shares,
    )


async def overview(db: AsyncSession) -> InventoryOverview:
    channels = (
        await db.execute(select(Channel).where(Channel.is_active.is_(True)))
    ).scalars().all()
    items = [await channel_inventory(db, c) for c in channels]

    unassigned = int(
        (
            await db.execute(
                select(func.count(MediaAsset.id)).where(
                    MediaAsset.lifecycle == Lifecycle.new.value,
                    MediaAsset.status == MediaStatus.ready.value,
                )
            )
        ).scalar_one()
    )
    total = int(
        (
            await db.execute(
                select(func.count(MediaAsset.id)).where(
                    MediaAsset.status != MediaStatus.archived.value
                )
            )
        ).scalar_one()
    )

    # Kanäle mit gemeinsamem Pool: Reichweite anteilig neu rechnen.
    groups: Dict[str, List[ChannelInventory]] = {}
    for item in items:
        if item.shares_pool_with:
            channel = next(c for c in channels if c.id == item.channel_id)
            group = channel.policy.exclusive_pool_group if channel.policy else None
            if group:
                groups.setdefault(group, []).append(item)
    for members in groups.values():
        total_consumption = sum(m.consumption_per_day for m in members) or 1.0
        shared_pool = max((m.available for m in members), default=0)
        for member in members:
            member.days_left = round(shared_pool / total_consumption, 1)
            member.empty_on = datetime.now(timezone.utc) + timedelta(days=member.days_left)
            member.traffic_light = traffic_light(member.days_left)

    return InventoryOverview(
        channels=items,
        unassigned_assets=unassigned,
        total_assets=total,
        generated_at=datetime.now(timezone.utc),
    )


async def low_inventory_channels(db: AsyncSession) -> List[ChannelInventory]:
    data = await overview(db)
    return [
        c
        for c in data.channels
        if c.days_left is not None and c.days_left < settings.inventory_warn_days
    ]
