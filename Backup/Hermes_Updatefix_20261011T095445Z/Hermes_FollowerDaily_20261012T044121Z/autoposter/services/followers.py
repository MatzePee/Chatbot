"""Read-only X follower history, keyed by immutable X user ID (not handle).

Existing AppSetting JSON storage makes this additive without schema migrations.
Dashboard GETs never query X; the existing metrics job collects at most every 6h.
Calendar-day baselines use Europe/Berlin and never substitute a nearby sample.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from autoposter.models import AppSetting, Channel, ChannelCredential
from autoposter.services import credentials

ZONE = ZoneInfo('Europe/Berlin')

async def overview(db, now=None):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZONE).date()
    result = []
    channels = (await db.execute(select(Channel).where(Channel.platform == 'x').order_by(Channel.display_name))).scalars().all()
    for channel in channels:
        cred = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
        account = cred.external_user_id if cred else None
        row = await db.get(AppSetting, 'x_followers:' + account) if account else None
        value = (row.value or {}) if row else {}
        days = value.get('days', {})
        sampled = value.get('sampled_at')
        latest = max(days) if days else None
        current = days.get(latest) if latest else None
        item = {'channel_id': str(channel.id), 'name': channel.display_name, 'account_id': account,
                'current': current, 'sampled_at': sampled, 'timezone': 'Europe/Berlin',
                'status': value.get('status', 'Noch keine Messung'), 'stale': latest != today.isoformat()}
        for label, horizon in [('yesterday', 1), ('days7', 7), ('days30', 30)]:
            count = days.get((today - timedelta(days=horizon)).isoformat())
            item[label] = {'count': count, 'growth': current - count if current is not None and count is not None and latest == today.isoformat() else None}
        result.append(item)
    return result

async def collect(db, now=None):
    from autoposter.adapters.x import XAdapter
    now = now or datetime.now(timezone.utc)
    channels = (await db.execute(select(Channel).where(Channel.platform == 'x', Channel.is_active.is_(True)))).scalars().all()
    done = 0
    for channel in channels:
        credrow = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
        if not credrow or not credrow.external_user_id:
            continue
        key = 'x_followers:' + credrow.external_user_id
        row = await db.get(AppSetting, key)
        value = dict(row.value or {}) if row else {}
        raw = value.get('attempted_at')
        if raw and now - datetime.fromisoformat(raw) < timedelta(hours=6):
            continue
        value['attempted_at'] = now.isoformat()
        try:
            # Do not rotate tokens or change channel health for optional analytics.
            if credrow.expires_at and credrow.expires_at <= now:
                raise ValueError('expired')
            cred = credentials.to_credentials(credrow)
            count = await XAdapter().fetch_followers(cred)
            days = dict(value.get('days', {}))
            days[now.astimezone(ZONE).date().isoformat()] = count
            cutoff = (now.astimezone(ZONE).date() - timedelta(days=400)).isoformat()
            value.update(days={k:v for k,v in days.items() if k >= cutoff}, sampled_at=now.isoformat(), status='OK')
            done += 1
        except Exception:
            # Never persist provider response/token-bearing exception text.
            value['status'] = 'Messung nicht verfügbar – X-Tarif, Berechtigung oder Token prüfen'
        if row is None:
            row = AppSetting(key=key)
        row.value = value
        db.add(row)
        await db.flush()
    return done
