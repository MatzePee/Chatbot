"""Zuordnung Bild/Set -> Kanal (Drag & Drop).

Grundregeln:
- Ein Asset darf beliebig vielen Kanälen zugeordnet sein. Ein Bild, das auf X läuft,
  darf ohne Einschränkung auch auf Fanvue und auf einem zweiten X-Profil laufen.
- Nur manuell (oder per ausdrücklich aktivierter Regel) zugeordnete Assets werden
  automatisch verplant. Es wird nie aus dem Gesamtbestand gegriffen.
- `source = manual` gewinnt immer gegen `rule`.
"""
from __future__ import annotations

import uuid
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.adapters import get_adapter
from autoposter.models import (
    AssignmentRule,
    AssignmentSource,
    Channel,
    MediaAsset,
    MediaAssignment,
    MediaSet,
    MediaSetItem,
    MediaStatus,
    NsfwLevel,
    Post,
    PostStatus,
)
from autoposter.schemas import AssignmentBatch, AssignmentRemove, AssignmentResult
from autoposter.services import lifecycle


def nsfw_rank(value: str) -> int:
    return {"sfw": 0, "suggestive": 1, "explicit": 2}.get(value, 0)


def check_compatibility(asset: MediaAsset, channel: Channel) -> Optional[str]:
    """Gibt einen Ablehnungsgrund zurück oder None."""
    if asset.status == MediaStatus.failed.value:
        return "Asset ist fehlerhaft verarbeitet"
    if nsfw_rank(asset.nsfw_level) > nsfw_rank(channel.nsfw_level):
        return (
            f"NSFW-Level '{asset.nsfw_level}' ist für Kanal "
            f"'{channel.display_name}' ({channel.nsfw_level}) nicht erlaubt"
        )
    limits = get_adapter(channel.platform).limits()
    if asset.mime not in limits.mime_whitelist:
        return f"Dateityp {asset.mime} wird von {channel.platform} nicht unterstützt"
    if asset.bytes > limits.max_image_bytes:
        return f"Datei zu groß für {channel.platform}"
    return None


async def _load_channels(db: AsyncSession, ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, Channel]:
    rows = (await db.execute(select(Channel).where(Channel.id.in_(list(ids))))).scalars().all()
    return {c.id: c for c in rows}


async def _next_priority(db: AsyncSession, channel_id: uuid.UUID) -> int:
    value = (
        await db.execute(
            select(func.coalesce(func.max(MediaAssignment.priority), 0)).where(
                MediaAssignment.channel_id == channel_id
            )
        )
    ).scalar_one()
    return int(value) + 1


async def assign(
    db: AsyncSession, batch: AssignmentBatch, actor_id: Optional[uuid.UUID] = None
) -> AssignmentResult:
    result = AssignmentResult(affected_channels=list(batch.channel_ids))
    channels = await _load_channels(db, batch.channel_ids)
    if not channels:
        return result

    assets: List[MediaAsset] = []
    if batch.asset_ids:
        assets = list(
            (
                await db.execute(select(MediaAsset).where(MediaAsset.id.in_(batch.asset_ids)))
            ).scalars().all()
        )
    sets: List[MediaSet] = []
    if batch.set_ids:
        sets = list(
            (await db.execute(select(MediaSet).where(MediaSet.id.in_(batch.set_ids)))).scalars().all()
        )

    touched: Set[uuid.UUID] = set()

    for channel_id, channel in channels.items():
        priority = await _next_priority(db, channel_id)

        for asset in assets:
            reason = check_compatibility(asset, channel)
            if reason:
                result.rejected.append(
                    {"asset_id": str(asset.id), "channel_id": str(channel_id), "reason": reason}
                )
                continue
            existing = (
                await db.execute(
                    select(MediaAssignment).where(
                        MediaAssignment.media_asset_id == asset.id,
                        MediaAssignment.channel_id == channel_id,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                if existing.source != AssignmentSource.manual.value:
                    existing.source = AssignmentSource.manual.value
                    db.add(existing)
                result.skipped += 1
                continue
            db.add(
                MediaAssignment(
                    media_asset_id=asset.id,
                    channel_id=channel_id,
                    assigned_by=actor_id,
                    source=AssignmentSource.manual.value,
                    priority=priority,
                )
            )
            priority += 1
            result.created += 1
            touched.add(asset.id)

        for media_set in sets:
            existing = (
                await db.execute(
                    select(MediaAssignment).where(
                        MediaAssignment.media_set_id == media_set.id,
                        MediaAssignment.channel_id == channel_id,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                result.skipped += 1
            else:
                db.add(
                    MediaAssignment(
                        media_set_id=media_set.id,
                        channel_id=channel_id,
                        assigned_by=actor_id,
                        source=AssignmentSource.manual.value,
                        priority=priority,
                    )
                )
                priority += 1
                result.created += 1
            member_ids = (
                await db.execute(
                    select(MediaSetItem.media_asset_id).where(
                        MediaSetItem.media_set_id == media_set.id
                    )
                )
            ).scalars().all()
            touched.update(member_ids)

    await db.flush()

    if batch.mode == "move":
        remove_targets = batch.remove_from_channel_ids or None
        removed = await _remove_other_channels(
            db,
            asset_ids=[a.id for a in assets],
            set_ids=[s.id for s in sets],
            keep_channel_ids=list(channels.keys()),
            only_channel_ids=remove_targets,
        )
        result.removed += removed

    await lifecycle.refresh_assets(db, touched)
    return result


async def _remove_other_channels(
    db: AsyncSession,
    *,
    asset_ids: Sequence[uuid.UUID],
    set_ids: Sequence[uuid.UUID],
    keep_channel_ids: Sequence[uuid.UUID],
    only_channel_ids: Optional[Sequence[uuid.UUID]] = None,
) -> int:
    removed = 0
    if asset_ids:
        stmt = delete(MediaAssignment).where(
            MediaAssignment.media_asset_id.in_(list(asset_ids)),
            MediaAssignment.channel_id.notin_(list(keep_channel_ids)),
        )
        if only_channel_ids:
            stmt = stmt.where(MediaAssignment.channel_id.in_(list(only_channel_ids)))
        removed += (await db.execute(stmt)).rowcount or 0
    if set_ids:
        stmt = delete(MediaAssignment).where(
            MediaAssignment.media_set_id.in_(list(set_ids)),
            MediaAssignment.channel_id.notin_(list(keep_channel_ids)),
        )
        if only_channel_ids:
            stmt = stmt.where(MediaAssignment.channel_id.in_(list(only_channel_ids)))
        removed += (await db.execute(stmt)).rowcount or 0
    return removed


async def unassign(db: AsyncSession, payload: AssignmentRemove) -> AssignmentResult:
    result = AssignmentResult(affected_channels=list(payload.channel_ids))
    touched: Set[uuid.UUID] = set(payload.asset_ids)

    if payload.set_ids:
        member_ids = (
            await db.execute(
                select(MediaSetItem.media_asset_id).where(
                    MediaSetItem.media_set_id.in_(payload.set_ids)
                )
            )
        ).scalars().all()
        touched.update(member_ids)

    if payload.asset_ids:
        result.removed += (
            await db.execute(
                delete(MediaAssignment).where(
                    MediaAssignment.media_asset_id.in_(payload.asset_ids),
                    MediaAssignment.channel_id.in_(payload.channel_ids),
                )
            )
        ).rowcount or 0
    if payload.set_ids:
        result.removed += (
            await db.execute(
                delete(MediaAssignment).where(
                    MediaAssignment.media_set_id.in_(payload.set_ids),
                    MediaAssignment.channel_id.in_(payload.channel_ids),
                )
            )
        ).rowcount or 0

    if payload.cascade_posts and touched:
        await _cancel_dependent_posts(db, touched, payload.channel_ids)

    await db.flush()
    await lifecycle.refresh_assets(db, touched)
    return result


async def dependent_posts(
    db: AsyncSession, asset_ids: Iterable[uuid.UUID], channel_ids: Sequence[uuid.UUID]
) -> List[Post]:
    """Geplante (noch nicht veröffentlichte) Posts, die diese Assets nutzen."""
    targets = {str(a) for a in asset_ids}
    posts = (
        await db.execute(
            select(Post).where(
                Post.channel_id.in_(list(channel_ids)),
                Post.status.in_(lifecycle.ACTIVE_SCHEDULE_STATES),
            )
        )
    ).scalars().all()
    return [p for p in posts if targets & {str(a) for a in (p.media_asset_ids or [])}]


async def _cancel_dependent_posts(
    db: AsyncSession, asset_ids: Iterable[uuid.UUID], channel_ids: Sequence[uuid.UUID]
) -> int:
    posts = await dependent_posts(db, asset_ids, channel_ids)
    for post in posts:
        post.status = PostStatus.cancelled.value
        post.error_message = "Zuordnung des Bildes wurde entfernt"
        db.add(post)
    return len(posts)


async def reorder(
    db: AsyncSession, channel_id: uuid.UUID, ordered_asset_ids: Sequence[uuid.UUID]
) -> int:
    for position, asset_id in enumerate(ordered_asset_ids, start=1):
        row = (
            await db.execute(
                select(MediaAssignment).where(
                    MediaAssignment.media_asset_id == asset_id,
                    MediaAssignment.channel_id == channel_id,
                )
            )
        ).scalar_one_or_none()
        if row:
            row.priority = position
            db.add(row)
    await db.flush()
    return len(ordered_asset_ids)


async def channel_pool(
    db: AsyncSession, channel_id: uuid.UUID, *, include_used: bool = False
) -> List[MediaAsset]:
    """Alle einem Kanal zugeordneten Assets — direkt oder über ein Set."""
    direct = (
        await db.execute(
            select(MediaAssignment.media_asset_id, MediaAssignment.priority)
            .where(
                MediaAssignment.channel_id == channel_id,
                MediaAssignment.media_asset_id.isnot(None),
            )
        )
    ).all()
    priorities: Dict[uuid.UUID, int] = {row[0]: row[1] for row in direct}

    set_ids = (
        await db.execute(
            select(MediaAssignment.media_set_id).where(
                MediaAssignment.channel_id == channel_id,
                MediaAssignment.media_set_id.isnot(None),
            )
        )
    ).scalars().all()
    if set_ids:
        members = (
            await db.execute(
                select(MediaSetItem.media_asset_id, MediaSetItem.position).where(
                    MediaSetItem.media_set_id.in_(set_ids)
                )
            )
        ).all()
        for asset_id, position in members:
            priorities.setdefault(asset_id, 1000 + position)

    if not priorities:
        return []

    stmt = select(MediaAsset).where(
        MediaAsset.id.in_(list(priorities.keys())),
        MediaAsset.status == MediaStatus.ready.value,
    )
    assets = list((await db.execute(stmt)).scalars().all())
    if not include_used:
        assets = [a for a in assets if str(channel_id) not in (a.used_channel_ids or [])]
    assets.sort(key=lambda a: (priorities.get(a.id, 9999), a.created_at))
    return assets


# --------------------------------------------------------------------------- #
# Regeln (Vorschlagsmodus ist Standard)
# --------------------------------------------------------------------------- #
def matches(asset: MediaAsset, match: Dict[str, object]) -> bool:
    tags = set(asset.tags or [])
    any_tags = set(match.get("tags_any") or [])
    all_tags = set(match.get("tags_all") or [])
    none_tags = set(match.get("tags_none") or [])
    if any_tags and not (tags & any_tags):
        return False
    if all_tags and not all_tags.issubset(tags):
        return False
    if none_tags and (tags & none_tags):
        return False
    nsfw_max = match.get("nsfw_max")
    if nsfw_max and nsfw_rank(asset.nsfw_level) > nsfw_rank(str(nsfw_max)):
        return False
    return True


async def apply_rules(
    db: AsyncSession, *, only_channel: Optional[uuid.UUID] = None
) -> Tuple[int, List[Dict[str, str]]]:
    """auto_assign-Regeln anwenden, suggest-Regeln nur als Vorschlag zurückgeben."""
    stmt = select(AssignmentRule).where(AssignmentRule.is_active.is_(True))
    if only_channel:
        stmt = stmt.where(AssignmentRule.channel_id == only_channel)
    rules = list((await db.execute(stmt)).scalars().all())
    if not rules:
        return 0, []

    assets = list(
        (
            await db.execute(
                select(MediaAsset).where(MediaAsset.status == MediaStatus.ready.value)
            )
        ).scalars().all()
    )
    created = 0
    suggestions: List[Dict[str, str]] = []

    for rule in rules:
        candidates = [a for a in assets if matches(a, rule.match or {})]
        candidates = [
            a for a in candidates if str(rule.channel_id) not in (a.assigned_channel_ids or [])
        ]
        if not candidates:
            continue
        if rule.mode == "auto_assign":
            outcome = await assign(
                db,
                AssignmentBatch(
                    asset_ids=[a.id for a in candidates], channel_ids=[rule.channel_id]
                ),
            )
            created += outcome.created
        else:
            suggestions.extend(
                {
                    "asset_id": str(a.id),
                    "channel_id": str(rule.channel_id),
                    "rule": rule.name,
                }
                for a in candidates[:500]
            )
    return created, suggestions
