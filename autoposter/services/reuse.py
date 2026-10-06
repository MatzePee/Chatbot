"""Explicit, channel-specific permission for one further use of a published asset."""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select
from autoposter.models import AppSetting

PREFIX = "media_reuse:"


async def allowed(db, channel_id):
    row = await db.get(AppSetting, PREFIX + str(channel_id))
    return set((row.value or {}).keys()) if row else set()


async def all_channels(db):
    rows = (await db.execute(select(AppSetting).where(AppSetting.key.startswith(PREFIX)))).scalars().all()
    result = {}
    for row in rows:
        for asset_id in (row.value or {}):
            result.setdefault(asset_id, []).append(row.key[len(PREFIX):])
    return result


async def release(db, asset_id, channel_id):
    key = PREFIX + str(channel_id)
    row = await db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
    row.value = {**(row.value or {}), str(asset_id): datetime.now(timezone.utc).isoformat()}
    db.add(row)
    await db.flush()


async def consume(db, asset_ids, channel_id):
    row = await db.get(AppSetting, PREFIX + str(channel_id))
    if row:
        consumed = {str(asset_id) for asset_id in asset_ids}
        row.value = {key: value for key, value in (row.value or {}).items() if key not in consumed}
        db.add(row)