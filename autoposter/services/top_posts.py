"""X lifetime leaderboard and channel-specific one-time media reuse."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, update, delete
from autoposter.models import AppSetting, Channel, ExternalMediaRef, MediaAsset, MediaChannelUsage, Post, PostStatus
from autoposter.schemas import AssignmentBatch
from autoposter.services import assignment, lifecycle, library, media, reuse

ZONE = ZoneInfo('Europe/Berlin')
COLLECTION_KEY = 'x_top_posts_collection'


def views(metrics):
    value = (metrics or {}).get('impression_count')
    if (metrics or {}).get('x_account_mismatch') or isinstance(value, bool):
        return None
    try:
        count = int(value)
        return count if count >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def age_info(published_at, now):
    days = max(0, (now.astimezone(ZONE).date() - published_at.astimezone(ZONE).date()).days)
    return days, 'red' if days <= 14 else 'yellow' if days <= 30 else 'green'


async def queue_context(db):
    active = (await db.execute(select(Post.channel_id, Post.media_asset_ids).where(
        Post.status.in_(lifecycle.ACTIVE_SCHEDULE_STATES)))).all()
    scheduled = {(str(cid), str(aid)) for cid, ids in active for aid in (ids or [])}
    return scheduled, await reuse.all_channels(db)


def reuse_state(post, assets, channel, scheduled, released):
    ids = [str(value) for value in (post.media_asset_ids or [])]
    cid = str(channel.id)
    if not ids:
        return 'unavailable', 'Kein Bild zum erneuten Einplanen'
    if any((cid, aid) in scheduled for aid in ids):
        return 'scheduled', 'Bereits eingeplant'
    if any(aid in assets and assets[aid].status == 'ready' and
           (cid in released.get(aid, []) or
            (cid in (assets[aid].assigned_channel_ids or []) and
             cid not in (assets[aid].used_channel_ids or []))) for aid in ids):
        return 'available', 'Bereits frei zum Verplanen'
    from .followers import x_account_matches
    if channel.platform == 'x' and channel.verified_account and not x_account_matches(channel, channel.verified_account):
        return 'unavailable', 'X-Konto neu verbinden'
    health = channel.health
    if channel.platform == 'fanvue':
        from .chatbot_fanvue import health as fanvue_health
        health = fanvue_health(channel)
    if not channel.is_active or health != 'ok':
        return 'unavailable', 'Kanal nicht aktiv verfügbar'
    if any(aid not in assets or assets[aid].status not in ('ready','archived') for aid in ids):
        return 'unavailable', 'Bild nicht mehr verfügbar'
    if any(assignment.check_compatibility(assets[aid], channel) for aid in ids):
        return 'unavailable', 'Bild nicht kompatibel'
    return 'ready', 'Erneut einplanen'


async def publication_history(db, asset_ids):
    """Actual publications per library image and channel, never planning dates."""
    if not asset_ids:
        return []
    events = {}
    rows = (await db.execute(select(Post.id, Post.channel_id, Post.media_asset_ids,
                                   Post.published_at, Post.external_url, Post.external_post_id, Post.metrics).where(
        Post.status == 'published', Post.published_at.isnot(None)))).all()
    wanted = {str(aid) for aid in asset_ids}
    for pid, cid, aids, stamp, url, external, metrics in rows:
        if str(external or '').startswith('dryrun-'):
            continue
        matches = wanted & {str(aid) for aid in (aids or [])}
        if matches:
            events[(str(cid), str(pid))] = {'channel_id':str(cid), 'post_id':str(pid),
                'published_at':stamp, 'external_url':url or '', 'asset_ids':matches, 'views':views(metrics)}
    uses = (await db.execute(select(MediaChannelUsage, Post.status, Post.external_post_id, Post.metrics).outerjoin(
        Post, Post.id == MediaChannelUsage.post_id).where(MediaChannelUsage.media_asset_id.in_(asset_ids)))).all()
    for usage, status, external, metrics in uses:
        if (status is not None and status != 'published') or str(external or '').startswith('dryrun-'):
            continue
        key = (str(usage.channel_id), str(usage.post_id or usage.id))
        if key not in events:
            events[key] = {'channel_id':str(usage.channel_id), 'post_id':str(usage.post_id) if usage.post_id else None,
                           'published_at':usage.used_at, 'external_url':'', 'asset_ids':set(), 'views':views(metrics)}
        events[key]['asset_ids'].add(str(usage.media_asset_id))
    return sorted(events.values(), key=lambda item:item['published_at'], reverse=True)


async def overview(db, now=None):
    now = now or datetime.now(timezone.utc)
    posts = (await db.execute(select(Post).join(Channel).where(
        Channel.platform == 'x', Post.status == PostStatus.published.value,
        Post.published_at.isnot(None), Post.external_post_id.isnot(None)))).scalars().all()
    posts = [p for p in posts if str(p.external_post_id).isdigit()]
    from .followers import x_account_matches
    measured = [p for p in posts if views(p.metrics) is not None and
                (not p.channel.verified_account or x_account_matches(p.channel,p.channel.verified_account))]
    measured.sort(key=lambda p: (views(p.metrics), p.published_at, str(p.id)), reverse=True)
    unique, seen = [], set()
    for post in measured:
        key = (str(post.channel_id), str(post.external_post_id))
        if key not in seen:
            unique.append(post); seen.add(key)
    top = unique[:50]
    ids = {UUID(str(a)) for p in top for a in (p.media_asset_ids or [])}
    assets = {str(a.id):a for a in (await db.execute(select(MediaAsset).where(MediaAsset.id.in_(ids)))).scalars().all()} if ids else {}
    scheduled, released = await queue_context(db)
    targets = (await db.execute(select(Channel).where(Channel.platform == 'x').order_by(Channel.platform,Channel.display_name))).scalars().all()
    history = await publication_history(db, ids)
    items = []
    for rank, post in enumerate(top, 1):
        channel = post.channel
        state, label = reuse_state(post, assets, channel, scheduled, released)
        target_options = []
        for target in targets:
            target_state, target_label = reuse_state(post, assets, target, scheduled, released)
            post_asset_ids = {str(aid) for aid in (post.media_asset_ids or [])}
            publications = []
            for event in history:
                matched = post_asset_ids & event['asset_ids']
                if event['channel_id'] != str(target.id) or not matched:
                    continue
                event_days, event_band = age_info(event['published_at'], now)
                publications.append({'published_at':event['published_at'].isoformat(), 'post_id':event['post_id'],
                                     'external_url':event['external_url'], 'asset_ids':sorted(matched),
                                     'age_days':event_days, 'age_band':event_band, 'views':event['views']})
            target_options.append({'channel_id':str(target.id), 'name':target.display_name,
                                   'platform':target.platform, 'handle':target.handle,
                                   'watermark_enabled':bool(target.watermark_enabled),
                                   'reuse_state':target_state, 'reuse_label':target_label,
                                   'can_reuse':target_state=='ready', 'publications':publications,
                                   'last_published_at':publications[0]['published_at'] if publications else None,
                                   'last_views':publications[0]['views'] if publications else None})
        days, band = age_info(post.published_at, now)
        pictures = []
        for aid in post.media_asset_ids or []:
            asset = assets.get(str(aid))
            if asset:
                thumb, url = media.public_urls(asset)
                pictures.append({'id':str(asset.id), 'url':url, 'thumb_url':thumb, 'filename':asset.filename, 'mime':asset.mime})
        items.append({'rank':rank, 'post_id':str(post.id), 'channel_id':str(channel.id),
                      'channel_name':channel.display_name, 'channel_handle':channel.handle,
                      'published_at':post.published_at.isoformat(), 'age_days':days, 'age_band':band,
                      'views':views(post.metrics), 'text':post.body_text, 'media':pictures,
                      'external_url':post.external_url or 'https://x.com/i/status/'+str(post.external_post_id),
                      'reuse_state':state, 'reuse_label':label, 'can_reuse':state=='ready', 'targets':target_options})
    row = await db.get(AppSetting, COLLECTION_KEY)
    return {'items':items, 'total_posts':len(posts), 'measured_posts':len(unique),
            'missing_views':len(posts)-len(measured),
            'collection':dict(row.value or {}) if row else {}, 'timezone':'Europe/Berlin', 'refresh_time':'01:00'}


async def release_post(db, post_id, user, target_channel_id=None):
    post = await db.get(Post, post_id)
    if not post or post.status != 'published' or not str(post.external_post_id or '').isdigit():
        raise HTTPException(404,'Veröffentlichter X-Post nicht gefunden')
    source = await db.get(Channel, post.channel_id)
    if not source or source.platform != 'x':
        raise HTTPException(400,'Nur für X-Posts verfügbar')
    channel = await db.get(Channel, target_channel_id or post.channel_id)
    if not channel or channel.platform != 'x':
        raise HTTPException(404,'X-Zielkanal nicht gefunden')
    # Serialize release vs concurrent clicks without editing the historical post.
    await db.execute(update(Channel).where(Channel.id==channel.id).values(quota_used_today=Channel.quota_used_today))
    ids = {UUID(str(a)) for a in (post.media_asset_ids or [])}
    assets = {str(a.id):a for a in (await db.execute(select(MediaAsset).where(MediaAsset.id.in_(ids)))).scalars().all()} if ids else {}
    scheduled, released = await queue_context(db)
    state, label = reuse_state(post, assets, channel, scheduled, released)
    if state != 'ready':
        raise HTTPException(409,label)
    # An explicit reuse action also reactivates archived media.
    archived = [a.id for a in assets.values() if a.status=='archived']
    if archived:
        await media.archive(db, archived, False)
    result = await assignment.assign(db, AssignmentBatch(asset_ids=list(ids),channel_ids=[channel.id]), actor_id=user.id)
    if result.rejected:
        raise HTTPException(400,result.rejected[0]['reason'])
    for aid in ids:
        await reuse.release(db, aid, channel.id)
    # Force a fresh upload from the library original for this destination only.
    # Rendering happens later with the destination's current watermark settings.
    await db.execute(delete(ExternalMediaRef).where(
        ExternalMediaRef.channel_id==channel.id, ExternalMediaRef.media_asset_id.in_(ids)))
    await lifecycle.refresh_assets(db, ids)
    library.invalidate_counts()
    return {'ok':True,'channel_id':str(channel.id),'released_assets':[str(a) for a in ids]}
