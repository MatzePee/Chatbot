"""Bibliothek: Filter, Sortierung, Keyset-Pagination, Zähler.

Ziel: auch bei 50.000+ Assets bleibt jede Abfrage unter 500 ms. Deshalb
- Filter laufen gegen die denormalisierten Spalten (lifecycle, *_channel_ids, usage_count),
- Pagination per Keyset (created_at, id) statt OFFSET,
- Zähler kommen aus einem kurzlebigen Cache statt aus Live-COUNT über den Gesamtbestand.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import Select, Text, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.models import Lifecycle, MediaAsset, MediaSetItem, MediaStatus
from autoposter.schemas import LifecycleCounts, MediaQuery
from autoposter.services.media import hamming

_counts_cache: Dict[str, Tuple[float, LifecycleCounts]] = {}
COUNTS_TTL_SECONDS = 60


# --------------------------------------------------------------------------- #
# Cursor
# --------------------------------------------------------------------------- #
def encode_cursor(asset: MediaAsset, sort: str) -> str:
    payload = {"s": sort, "id": str(asset.id)}
    if sort in ("created_desc", "created_asc"):
        payload["v"] = asset.created_at.isoformat()
    elif sort in ("used_desc", "used_asc"):
        payload["v"] = asset.last_used_at.isoformat() if asset.last_used_at else None
    elif sort in ("usage_desc", "usage_asc"):
        payload["v"] = asset.usage_count
    else:
        payload["v"] = asset.created_at.isoformat()
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(cursor: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        return None


def _json_contains(column, value: str):
    """Portables 'JSON-Liste enthält Wert'.

    Die Listenspalten sind auf beiden Dialekten JSON, ihre Textdarstellung lautet
    '["a", "b"]'. Ein LIKE auf '"wert"' trifft daher exakt ein Listenelement und
    niemals ein Teilstück eines anderen Werts.
    """
    return column.cast(Text).like(f'%"{value}"%')


# --------------------------------------------------------------------------- #
# Query-Bau
# --------------------------------------------------------------------------- #
def build_query(q: MediaQuery) -> Select:
    stmt = select(MediaAsset)
    conditions = []

    if not q.include_archived and Lifecycle.archived.value not in q.lifecycle:
        conditions.append(MediaAsset.status != MediaStatus.archived.value)

    if q.status:
        conditions.append(MediaAsset.status.in_(q.status))
    if q.lifecycle:
        conditions.append(MediaAsset.lifecycle.in_(q.lifecycle))
    if q.nsfw_level:
        conditions.append(MediaAsset.nsfw_level.in_(q.nsfw_level))

    if q.q:
        needle = f"%{q.q.lower()}%"
        conditions.append(
            or_(
                func.lower(MediaAsset.filename).like(needle),
                func.lower(MediaAsset.caption_hint).like(needle),
                func.lower(MediaAsset.ai_description).like(needle),
                func.lower(MediaAsset.source_note).like(needle),
                func.lower(MediaAsset.tags.cast(Text)).like(needle),
            )
        )

    for tag in q.tags_all:
        conditions.append(_json_contains(MediaAsset.tags, tag))
    if q.tags_any:
        conditions.append(or_(*[_json_contains(MediaAsset.tags, t) for t in q.tags_any]))
    for tag in q.tags_none:
        conditions.append(~_json_contains(MediaAsset.tags, tag))

    if q.unassigned:
        conditions.append(MediaAsset.lifecycle == Lifecycle.new.value)
    for channel_id in q.assigned_to:
        conditions.append(_json_contains(MediaAsset.assigned_channel_ids, str(channel_id)))
    for channel_id in q.used_on:
        conditions.append(_json_contains(MediaAsset.used_channel_ids, str(channel_id)))
    for channel_id in q.not_used_on:
        conditions.append(~_json_contains(MediaAsset.used_channel_ids, str(channel_id)))

    if q.set_id:
        stmt = stmt.join(MediaSetItem, MediaSetItem.media_asset_id == MediaAsset.id)
        conditions.append(MediaSetItem.media_set_id == q.set_id)

    if q.uploaded_after:
        conditions.append(MediaAsset.created_at >= q.uploaded_after)
    if q.uploaded_before:
        conditions.append(MediaAsset.created_at <= q.uploaded_before)
    if q.used_after:
        conditions.append(MediaAsset.last_used_at >= q.used_after)
    if q.used_before:
        conditions.append(MediaAsset.last_used_at <= q.used_before)
    if q.unused_since_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=q.unused_since_days)
        conditions.append(
            or_(MediaAsset.last_used_at.is_(None), MediaAsset.last_used_at < cutoff)
        )
    if q.min_usage is not None:
        conditions.append(MediaAsset.usage_count >= q.min_usage)
    if q.max_usage is not None:
        conditions.append(MediaAsset.usage_count <= q.max_usage)

    if conditions:
        stmt = stmt.where(and_(*conditions))
    return stmt


def apply_sort(stmt: Select, sort: str) -> Select:
    mapping = {
        "created_desc": (MediaAsset.created_at.desc(), MediaAsset.id.desc()),
        "created_asc": (MediaAsset.created_at.asc(), MediaAsset.id.asc()),
        "used_desc": (MediaAsset.last_used_at.desc().nullslast(), MediaAsset.id.desc()),
        "used_asc": (MediaAsset.last_used_at.asc().nullsfirst(), MediaAsset.id.asc()),
        "usage_desc": (MediaAsset.usage_count.desc(), MediaAsset.id.desc()),
        "usage_asc": (MediaAsset.usage_count.asc(), MediaAsset.id.asc()),
        "filename": (MediaAsset.filename.asc(), MediaAsset.id.asc()),
    }
    return stmt.order_by(*mapping.get(sort, mapping["created_desc"]))


def apply_cursor(stmt: Select, q: MediaQuery) -> Select:
    if not q.cursor:
        return stmt
    payload = decode_cursor(q.cursor)
    if not payload:
        return stmt
    value = payload.get("v")
    if q.sort == "created_desc" and value:
        stmt = stmt.where(MediaAsset.created_at < datetime.fromisoformat(value))
    elif q.sort == "created_asc" and value:
        stmt = stmt.where(MediaAsset.created_at > datetime.fromisoformat(value))
    elif q.sort == "usage_desc" and value is not None:
        stmt = stmt.where(MediaAsset.usage_count <= int(value))
    elif q.sort == "usage_asc" and value is not None:
        stmt = stmt.where(MediaAsset.usage_count >= int(value))
    elif q.sort in ("used_desc", "used_asc") and value:
        moment = datetime.fromisoformat(value)
        stmt = stmt.where(
            MediaAsset.last_used_at < moment
            if q.sort == "used_desc"
            else MediaAsset.last_used_at > moment
        )
    return stmt


async def search(db: AsyncSession, q: MediaQuery) -> Tuple[List[MediaAsset], Optional[str]]:
    if q.similar_to:
        return await _search_similar(db, q)

    stmt = apply_cursor(apply_sort(build_query(q), q.sort), q).limit(min(q.limit, 500) + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    next_cursor = None
    if len(rows) > q.limit:
        rows = rows[: q.limit]
        next_cursor = encode_cursor(rows[-1], q.sort)
    return rows, next_cursor


async def _search_similar(
    db: AsyncSession, q: MediaQuery
) -> Tuple[List[MediaAsset], Optional[str]]:
    reference = (
        await db.execute(select(MediaAsset).where(MediaAsset.id == q.similar_to))
    ).scalar_one_or_none()
    if not reference or not reference.phash:
        return [], None
    candidates = list((await db.execute(build_query(q))).scalars().all())
    scored = [
        (a, hamming(reference.phash, a.phash))
        for a in candidates
        if a.id != reference.id
    ]
    scored = [s for s in scored if s[1] <= q.similar_distance]
    scored.sort(key=lambda x: x[1])
    return [a for a, _ in scored[: q.limit]], None


# --------------------------------------------------------------------------- #
# Zähler
# --------------------------------------------------------------------------- #
async def lifecycle_counts(db: AsyncSession, use_cache: bool = True) -> LifecycleCounts:
    key = "global"
    if use_cache and key in _counts_cache:
        stamp, cached = _counts_cache[key]
        if time.time() - stamp < COUNTS_TTL_SECONDS:
            return cached

    rows = (
        await db.execute(
            select(MediaAsset.lifecycle, func.count(MediaAsset.id)).group_by(MediaAsset.lifecycle)
        )
    ).all()
    counts = LifecycleCounts()
    for state, amount in rows:
        if hasattr(counts, state):
            setattr(counts, state, int(amount))
        counts.total += int(amount)

    _counts_cache[key] = (time.time(), counts)
    return counts


def invalidate_counts() -> None:
    _counts_cache.clear()


async def tag_facets(db: AsyncSession, limit: int = 60) -> List[Dict[str, Any]]:
    assets = (
        await db.execute(
            select(MediaAsset.tags).where(MediaAsset.status != MediaStatus.archived.value)
        )
    ).scalars().all()
    counter: Dict[str, int] = {}
    for tags in assets:
        for tag in tags or []:
            counter[tag] = counter.get(tag, 0) + 1
    top = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"tag": t, "count": c} for t, c in top]


DEFAULT_SAVED_VIEWS: List[Dict[str, Any]] = [
    {"name": "Noch nie gepostet", "icon": "sparkles", "query": {"max_usage": 0}},
    {"name": "Nicht zugeordnet", "icon": "inbox", "query": {"unassigned": True}},
    {"name": "Eingeplant", "icon": "clock", "query": {"lifecycle": ["scheduled"]}},
    {
        "name": "Teilweise verbraucht",
        "icon": "layers",
        "query": {"lifecycle": ["partially_used"]},
    },
    {"name": "Seit 90 Tagen ungenutzt", "icon": "hourglass", "query": {"unused_since_days": 90}},
    {"name": "Archiv", "icon": "archive", "query": {"lifecycle": ["archived"], "include_archived": True}},
]
