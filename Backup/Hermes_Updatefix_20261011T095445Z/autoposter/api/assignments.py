"""Drag-&-Drop-Zuordnung Bild/Set -> Kanal."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db import get_db
from autoposter.deps import current_user, require_editor
from autoposter.models import AssignmentRule, Channel, MediaAsset, User
from autoposter.schemas import (
    AssignmentBatch,
    AssignmentRemove,
    AssignmentReorder,
    AssignmentResult,
    AssignmentRuleIn,
    AssignmentRuleOut,
    MediaOut,
)
from autoposter.services import assignment as service
from autoposter.services import inventory, library, lifecycle, reuse, media as media_service

router = APIRouter(prefix="/assignments", tags=["assignments"])


def _to_out(asset: MediaAsset, releases=None) -> MediaOut:
    out = MediaOut.model_validate(asset)
    out.thumb_url, out.url = media_service.public_urls(asset)
    out.reusable_channel_ids = (releases or {}).get(str(asset.id), [])
    return out


@router.post("/batch", response_model=AssignmentResult)
async def assign_batch(
    payload: AssignmentBatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_editor),
) -> AssignmentResult:
    """Ein Drop-Vorgang. Mehrere Kanäle gleichzeitig sind ausdrücklich erlaubt:
    dasselbe Bild darf auf X und auf Fanvue laufen."""
    if not payload.channel_ids:
        raise HTTPException(400, "Kein Zielkanal angegeben")
    result = await service.assign(db, payload, actor_id=user.id)
    library.invalidate_counts()
    return result


@router.post("/channel/{channel_id}/assets/{asset_id}/reuse", response_model=MediaOut)
async def reuse_asset(
    channel_id: uuid.UUID, asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_editor),
) -> MediaOut:
    channel = await db.get(Channel, channel_id)
    asset = await db.get(MediaAsset, asset_id)
    if not channel or not asset:
        raise HTTPException(404, "Bild oder Kanal nicht gefunden")
    await lifecycle.refresh_asset(db, asset)
    if not channel.is_active or asset.status != "ready":
        raise HTTPException(400, "Bild und Kanal müssen aktiv verfügbar sein")
    if str(channel_id) not in (asset.used_channel_ids or []):
        raise HTTPException(400, "Dieses Bild wurde auf dem Kanal noch nicht veröffentlicht")
    if str(channel_id) in (asset.scheduled_channel_ids or []):
        raise HTTPException(409, "Dieses Bild ist auf dem Kanal bereits erneut eingeplant")
    reason = service.check_compatibility(asset, channel)
    if reason:
        raise HTTPException(400, reason)
    result = await service.assign(db, AssignmentBatch(asset_ids=[asset_id], channel_ids=[channel_id]), actor_id=user.id)
    if result.rejected:
        raise HTTPException(400, result.rejected[0]["reason"])
    await reuse.release(db, asset_id, channel_id)
    from autoposter.services import problems
    await problems.recheck_channel_failures(db, channel_id)
    library.invalidate_counts()
    out = _to_out(asset)
    out.reusable_channel_ids = (await reuse.all_channels(db)).get(str(asset_id), [])
    return out


@router.post("/remove", response_model=AssignmentResult)
async def remove(
    payload: AssignmentRemove,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> AssignmentResult:
    result = await service.unassign(db, payload)
    library.invalidate_counts()
    return result


@router.post("/check-remove")
async def check_remove(
    payload: AssignmentRemove,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> Dict[str, Any]:
    """Vor dem Entfernen prüfen, ob geplante Posts betroffen wären."""
    posts = await service.dependent_posts(db, payload.asset_ids, payload.channel_ids)
    return {
        "affected_posts": [
            {
                "id": str(p.id),
                "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
                "status": p.status,
                "text": p.body_text[:120],
            }
            for p in posts
        ],
        "count": len(posts),
    }


@router.post("/reorder")
async def reorder(
    payload: AssignmentReorder,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> Dict[str, int]:
    """Reihenfolge in der Kanal-Spalte = Reihenfolge der automatischen Verplanung."""
    count = await service.reorder(db, payload.channel_id, payload.ordered_asset_ids)
    return {"reordered": count}


@router.get("/channel/{channel_id}", response_model=List[MediaOut])
async def channel_pool(
    channel_id: uuid.UUID,
    include_used: bool = Query(default=True),
    limit: int = Query(default=300, le=2000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> List[MediaOut]:
    assets = await service.channel_pool(db, channel_id, include_used=include_used)
    releases = await reuse.all_channels(db)
    return [_to_out(a, releases) for a in assets[:limit]]


@router.get("/board")
async def board(
    limit_per_channel: int | None = Query(default=None, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(current_user),
) -> Dict[str, Any]:
    """Vollständige Kanalbestände; nur auf ausdrücklichen Wunsch begrenzen."""
    channels = (
        await db.execute(
            select(Channel).where(Channel.is_active.is_(True)).order_by(Channel.display_name)
        )
    ).scalars().all()
    overview = await inventory.overview(db)
    inv = {str(c.channel_id): c for c in overview.channels}

    releases = await reuse.all_channels(db)
    columns = []
    for channel in channels:
        assets = await service.channel_pool(db, channel.id, include_used=True)
        entry = inv.get(str(channel.id))
        columns.append(
            {
                "channel": {
                    "id": str(channel.id),
                    "name": channel.display_name,
                    "handle": channel.handle,
                    "platform": channel.platform,
                    "color": channel.color,
                    "nsfw_level": channel.nsfw_level,
                    "health": channel.health,
                },
                "count": len(assets),
                "available": entry.available if entry else 0,
                "days_left": entry.days_left if entry else None,
                "traffic_light": entry.traffic_light if entry else "grey",
                "empty_on": entry.empty_on.isoformat() if entry and entry.empty_on else None,
                "assets": [_to_out(a, releases).model_dump(mode="json") for a in assets[:limit_per_channel]],
            }
        )
    return {"columns": columns, "unassigned": overview.unassigned_assets}


# --------------------------------------------------------------------------- #
# Regeln
# --------------------------------------------------------------------------- #
@router.get("/rules", response_model=List[AssignmentRuleOut])
async def list_rules(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> List[AssignmentRuleOut]:
    rows = (await db.execute(select(AssignmentRule))).scalars().all()
    return [AssignmentRuleOut.model_validate(r) for r in rows]


@router.post("/rules", response_model=AssignmentRuleOut, status_code=201)
async def create_rule(
    payload: AssignmentRuleIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> AssignmentRuleOut:
    rule = AssignmentRule(**payload.model_dump())
    db.add(rule)
    await db.flush()
    return AssignmentRuleOut.model_validate(rule)


@router.delete("/rules/{rule_id}", status_code=204, response_model=None)
async def delete_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> None:
    rule = (
        await db.execute(select(AssignmentRule).where(AssignmentRule.id == rule_id))
    ).scalar_one_or_none()
    if rule:
        await db.delete(rule)


@router.post("/rules/apply")
async def apply_rules(
    channel_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_editor),
) -> Dict[str, Any]:
    created, suggestions = await service.apply_rules(db, only_channel=channel_id)
    library.invalidate_counts()
    return {"auto_assigned": created, "suggestions": suggestions}
