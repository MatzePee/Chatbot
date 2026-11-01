"""Read-only X follower history, keyed by immutable X user ID (not handle).

Existing AppSetting JSON storage makes this additive without schema migrations.
Dashboard GETs never query X. Metrics attempts are throttled to 6h, except
for one persistent daily slot at/after 06:00 Europe/Berlin.
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
                'attempted_at': value.get('attempted_at'),
                'daily_collection': {'time': '06:00', 'timezone': 'Europe/Berlin',
                                     'last_slot': value.get('daily_slot'),
                                     'pending': bool(channel.is_active and account and now.astimezone(ZONE).hour >= 6 and value.get('daily_slot') != today.isoformat())},
                'status': value.get('status', 'Noch keine Messung'), 'stale': latest != today.isoformat()}
        for label, horizon in [('yesterday', 1), ('days7', 7), ('days30', 30)]:
            count = days.get((today - timedelta(days=horizon)).isoformat())
            item[label] = {'count': count, 'growth': current - count if current is not None and count is not None and latest == today.isoformat() else None}
        result.append(item)
    return result

async def collect(db, now=None, *, daily_only=False):
    """Owns its session transactions: commit claims before read-only HTTP.

    First poll at/after 06:00 catches up today's slot, never earlier days.
    Metrics and daily jobs share the claim, so either can satisfy the slot.
    Even failures/crashes consume it; ordinary six-hour retries remain allowed.
    """
    from autoposter.adapters.x import XAdapter
    now = now or datetime.now(timezone.utc)
    channels = (await db.execute(select(Channel).where(Channel.platform == 'x', Channel.is_active.is_(True)))).scalars().all()
    done = 0
    for channel in channels:
        credrow = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
        if not credrow or not credrow.external_user_id:
            continue
        key = 'x_followers:' + credrow.external_user_id
        # Atomic account lock in both supported DBs. SQLite's no-op write takes
        # its writer lock; PostgreSQL uses a row lock. Read only AFTER locking.
        from sqlalchemy import update
        if db.bind.dialect.name == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        await db.execute(insert(AppSetting).values(key=key, value={}).on_conflict_do_nothing(index_elements=['key']))
        if db.bind.dialect.name == 'sqlite':
            await db.execute(update(AppSetting).where(AppSetting.key == key).values(key=key))
        row = (await db.execute(select(AppSetting).where(AppSetting.key == key).with_for_update().execution_options(populate_existing=True))).scalar_one()
        value = dict(row.value or {})
        local = now.astimezone(ZONE)
        slot = local.date().isoformat()
        daily_due = local.hour >= 6 and value.get('daily_slot') != slot
        raw = value.get('attempted_at')
        if daily_only and not daily_due:
            await db.commit()
            continue
        if not daily_due and raw and now - datetime.fromisoformat(raw) < timedelta(hours=6):
            await db.commit()
            continue
        if daily_due:
            value['daily_slot'] = slot
        value['attempted_at'] = now.isoformat()
        value['status'] = 'Messung begonnen – Ergebnis ausstehend'
        row.value = dict(value)
        await db.commit()  # Durable at-most-once attempt; no DB lock during HTTP.
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
        # A slow pre-06:00 response must not erase a newer daily claim/result.
        await db.execute(update(AppSetting).where(
            AppSetting.key == key,
            AppSetting.value['attempted_at'].as_string() == now.isoformat(),
        ).values(value=value).execution_options(synchronize_session=False))
        db.expire(row)
        await db.flush()
    return done
