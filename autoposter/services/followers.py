"""Read-only audience history, keyed by immutable platform account/channel ID.

Original x_followers keys and daily values remain untouched; Telegram adds
telegram_members keys in existing AppSetting JSON storage without migrations.
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

def x_account_matches(channel, username):
    expected = (channel.handle or '').strip().lstrip('@').casefold()
    actual = (username or '').strip().lstrip('@').casefold()
    return not expected or bool(actual and actual == expected)


def account_binding(channel, cred, platform):
    if not cred or cred.platform != platform:
        return None
    if platform == 'x':
        if channel.verified_account and not x_account_matches(channel, channel.verified_account):
            return None
        return cred.external_user_id
    import re
    meta = cred.raw_meta or {}
    chat = meta.get('chat_id')
    if (channel.last_verified_at and meta.get('destination') == channel.handle
            and isinstance(chat, str) and re.fullmatch(r'-100[0-9]+', chat)):
        return chat
    return None


def cache_key(platform, account):
    return ('x_followers:' if platform == 'x' else 'telegram_members:') + account


async def overview(db, now=None, *, platform='x'):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZONE).date()
    result = []
    channels = (await db.execute(select(Channel).where(Channel.platform == platform).order_by(Channel.display_name))).scalars().all()
    for channel in channels:
        cred = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
        account = account_binding(channel, cred, platform)
        row = await db.get(AppSetting, cache_key(platform, account)) if account else None
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
                'status': ('Falsches X-Konto verbunden – unter Einstellungen → Kanäle mit dem richtigen Konto neu verbinden' if platform == 'x' and cred and not account else 'Nicht verbunden – Zielkanal und Zugang prüfen' if platform == 'telegram' and not account else value.get('status', 'Noch keine Messung')), 'stale': latest != today.isoformat()}
        for label, horizon in [('yesterday', 1), ('days7', 7), ('days30', 30)]:
            count = days.get((today - timedelta(days=horizon)).isoformat())
            item[label] = {'count': count, 'growth': current - count if current is not None and count is not None and latest == today.isoformat() else None}
        item['platform'] = platform
        item['history'] = [{'date': (today - timedelta(days=offset)).isoformat(), 'count': days.get((today - timedelta(days=offset)).isoformat())} for offset in range(29, -1, -1)]
        result.append(item)
    return result

async def collect(db, now=None, *, daily_only=False, platform='x', force=False):
    """Owns its session transactions: commit claims before read-only HTTP.

    First poll at/after 06:00 catches up today's slot, never earlier days.
    Metrics and daily jobs share the claim, so either can satisfy the slot.
    Even failures/crashes consume it; ordinary six-hour retries remain allowed.
    """
    from autoposter.adapters.x import XAdapter
    now = now or datetime.now(timezone.utc)
    channels = (await db.execute(select(Channel).where(Channel.platform == platform, Channel.is_active.is_(True) if not force else True))).scalars().all()
    done = 0
    for channel in channels:
        credrow = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
        account = account_binding(channel, credrow, platform)
        if not account:
            continue
        key = cache_key(platform, account)
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
        if daily_only and not daily_due and not force:
            await db.commit()
            continue
        if not force and not daily_due and raw and now - datetime.fromisoformat(raw) < timedelta(hours=6):
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
            if platform == 'x' and hasattr(cred, 'raw_meta'):
                cred.raw_meta = {**(cred.raw_meta or {}), 'expected_x_username': channel.handle}
            if platform == 'telegram':
                from autoposter.adapters.telegram import TelegramAdapter
                count = await TelegramAdapter().fetch_members(cred)
            else:
                count = await XAdapter().fetch_followers(cred)
            days = dict(value.get('days', {}))
            days[now.astimezone(ZONE).date().isoformat()] = count
            cutoff = (now.astimezone(ZONE).date() - timedelta(days=400)).isoformat()
            value.update(days={k:v for k,v in days.items() if k >= cutoff}, sampled_at=now.isoformat(), status='OK')
            done += 1
        except Exception as exc:
            # Never persist provider response/token-bearing exception text.
            value['status'] = ('Messung nicht verfügbar – Telegram-Zielkanal, Privatsphäre, Berechtigung oder Token prüfen' if platform == 'telegram' else 'Messung nicht verfügbar – X-Tarif, Berechtigung oder Token prüfen')
            if platform == 'telegram':
                from autoposter.adapters import AdapterError
                code = getattr(exc, 'status', None) if isinstance(exc, AdapterError) else None
                if type(code) is int and 100 <= code <= 599:
                    value['status'] += f' (API {code})'
        # A slow pre-06:00 response must not erase a newer daily claim/result.
        await db.execute(update(AppSetting).where(
            AppSetting.key == key,
            AppSetting.value['attempted_at'].as_string() == now.isoformat(),
        ).values(value=value).execution_options(synchronize_session=False))
        db.expire(row)
        await db.flush()
    return done
