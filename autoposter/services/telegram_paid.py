"""Telegram Stars sale units, immutable planning snapshots and explicit reapproval."""
import random
import uuid
from datetime import date, datetime
from sqlalchemy import select
from fastapi import HTTPException
from autoposter.models import Channel, MediaAsset, MediaSet, MediaSetItem, MediaAssignment, Post


def is_paid(channel):
    return channel.platform == 'telegram' and channel.telegram_mode == 'paid'


def on_day(channel, day):
    if channel.platform != 'telegram':
        return True
    from zoneinfo import ZoneInfo
    created = getattr(channel, 'created_at', None)
    start = channel.telegram_start_date or (created.astimezone(ZoneInfo(channel.timezone or 'UTC')).date() if created else day)
    return day >= start and (day - start).days % max(1, channel.telegram_interval_days or 1) == 0


async def spec(db, channel, asset_ids, set_id=None):
    ids = [str(i) for i in asset_ids]
    if set_id:
        unit = (await db.execute(select(MediaSet).where(MediaSet.id == uuid.UUID(str(set_id))).execution_options(populate_existing=True))).scalar_one_or_none()
        if not unit:
            raise ValueError('Das Pay-Set wurde gelöscht.')
        members = list((await db.execute(select(MediaSetItem.media_asset_id).where(
            MediaSetItem.media_set_id == unit.id).order_by(MediaSetItem.position, MediaSetItem.id))).scalars())
        expected = [str(i) for i in members]
        if not 2 <= len(expected) <= 10:
            raise ValueError('Pay-Sets müssen 2 bis 10 Medien enthalten; sie werden nie aufgeteilt.')
        if ids != expected:
            raise ValueError('Set-Inhalt oder Reihenfolge geändert. Set und Preis erneut übernehmen.')
        assignment_filter = MediaAssignment.media_set_id == unit.id
    else:
        if len(ids) != 1:
            raise ValueError('Pay-Post benötigt ein bepreistes Einzelmedium oder ein vollständiges Set.')
        unit = (await db.execute(select(MediaAsset).where(MediaAsset.id == uuid.UUID(ids[0])).execution_options(populate_existing=True))).scalar_one_or_none()
        assignment_filter = MediaAssignment.media_asset_id == uuid.UUID(ids[0])
    if not unit or type(unit.telegram_price_stars) is not int or not 1 <= unit.telegram_price_stars <= 25000:
        raise ValueError('Telegram-Sternepreis fehlt (1 bis 25000 Sterne).')
    assigned = (await db.execute(select(MediaAssignment.id).where(
        MediaAssignment.channel_id == channel.id, assignment_filter))).first()
    if not assigned:
        raise ValueError('Medium bzw. Set ist diesem Pay-Kanal nicht zugeordnet.')
    assets = list((await db.execute(select(MediaAsset).where(MediaAsset.id.in_([uuid.UUID(i) for i in ids])).execution_options(populate_existing=True))).scalars())
    if len(assets) != len(ids) or len(set(ids)) != len(ids) or any(a.mime not in ('image/jpeg','image/png','video/mp4') or a.status != 'ready' for a in assets):
        raise ValueError('Pay-Inhalt benötigt vollständig verfügbare JPEG-/PNG-Bilder oder MP4-Videos.')
    if sum(a.bytes or 0 for a in assets) > 100_000_000 or any((a.bytes or 0) > (50_000_000 if a.mime == 'video/mp4' else 10_000_000) for a in assets):
        raise ValueError('Telegram Pay: Foto maximal 10 MB, Video maximal 50 MB und Set insgesamt maximal 100 MB.')
    return {'kind': 'set' if set_id else 'video' if assets[0].mime == 'video/mp4' else 'image', 'id': str(unit.id),
            'stars': unit.telegram_price_stars, 'asset_ids': ids}


async def prepare(db, channel, post):
    if not is_paid(channel):
        post.price_stars = None
        return
    try:
        snapshot = await spec(db, channel, post.media_asset_ids or [], post.media_set_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    post.price_stars = snapshot['stars']
    meta = dict(post.generation_meta or {})
    meta['telegram_paid'] = snapshot
    meta.pop('telegram_paid_stale', None)
    post.generation_meta = meta


async def validate(db, channel, post):
    if not is_paid(channel):
        return 'Sternepreis nur im Telegram-Pay-Kanal erlaubt.' if post.price_stars else None
    try:
        current = await spec(db, channel, post.media_asset_ids or [], post.media_set_id)
        if (post.generation_meta or {}).get('telegram_paid_stale') or current != (post.generation_meta or {}).get('telegram_paid') or post.price_stars != current['stars']:
            return 'Pay-Preis oder Set geändert. Im Post „Set und Preis übernehmen“ und erneut freigeben.'
    except ValueError as exc:
        return str(exc)
    return None


async def invalidate(db, *, asset_id=None, set_id=None, include_sets=False):
    posts = (await db.execute(select(Post).join(Channel, Channel.id == Post.channel_id).where(
        Channel.platform == 'telegram', Channel.telegram_mode == 'paid',
        Post.status.in_(['draft', 'needs_review', 'scheduled', 'failed'])))).scalars().all()
    for post in posts:
        # Individual prices never affect the separately priced set.
        hit = (set_id and str(post.media_set_id) == str(set_id)) or (asset_id and (include_sets or not post.media_set_id) and str(asset_id) in (post.media_asset_ids or []))
        if hit:
            post.status = 'needs_review'
            post.generation_meta = {**(post.generation_meta or {}), 'telegram_paid_stale': True}
            post.error_message = 'Pay-Preis oder Set geändert. Bitte Set und Preis erneut übernehmen und freigeben.'
    await db.flush()


async def units(db, channel, policy, *, exclude=None, tags_any=None, prefer_avoid=None):
    from autoposter.services import assignment, planner
    pool = await assignment.channel_pool(db, channel.id, include_used=False)
    eligible, _ = await planner.pick_assets(db, channel, policy, count=len(pool),
        exclude=exclude, tags_any=tags_any, keep_set_order=False)
    available = {a.id: a for a in eligible if a.mime in ('image/jpeg','image/png','video/mp4')}
    assigned = (await db.execute(select(MediaAssignment).where(MediaAssignment.channel_id == channel.id))).scalars().all()
    results = []
    for row in assigned:
        ids, set_id, title = [], None, ''
        if row.media_set_id and channel.telegram_content != 'images':
            unit = await db.get(MediaSet, row.media_set_id)
            if not unit: continue
            ids = list((await db.execute(select(MediaSetItem.media_asset_id).where(
                MediaSetItem.media_set_id == unit.id).order_by(MediaSetItem.position, MediaSetItem.id))).scalars())
            set_id, title = unit.id, unit.name
        elif row.media_asset_id and channel.telegram_content != 'sets':
            unit = available.get(row.media_asset_id)
            if not unit: continue
            ids, title = [unit.id], getattr(unit, 'filename', '') or 'Einzelmedium'
        else:
            continue
        if not ids or any(i not in available for i in ids): continue
        try:
            snapshot = await spec(db, channel, ids, set_id)
        except ValueError:
            continue
        assets = [available[i] for i in ids]
        results.append({'assets': assets, 'set_id': set_id, 'title': title, **snapshot})
    random.shuffle(results)
    results.sort(key=lambda u: (any(a.id in (prefer_avoid or set()) for a in u['assets']), any(a.scheduled_channel_ids for a in u['assets'])))
    return results


async def pick(db, channel, policy, **kwargs):
    choices = await units(db, channel, policy, **kwargs)
    if not choices:
        return [], None, 'Kein freies Pay-Angebot: bepreiste Medien oder Sets (2–10 Medien) ausdrücklich dem Pay-Kanal zuordnen.'
    unit = choices[0]
    return unit['assets'], unit['set_id'], ''


async def sibling_times(db, channel):
    if channel.platform != 'telegram' or not channel.credential_id:
        return []
    from autoposter.services import lifecycle
    return list((await db.execute(select(Post.scheduled_at).join(Channel, Channel.id == Post.channel_id).where(
        Channel.credential_id == channel.credential_id, Channel.id != channel.id,
        Post.scheduled_at.isnot(None), Post.status.in_(lifecycle.ACTIVE_SCHEDULE_STATES)))).scalars())
