"""Official Fanvue audience-only updates, shared by manual and Berlin-day jobs."""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, update
from autoposter.models import AppSetting, Channel
from autoposter.config import settings
from autoposter.adapters.base import AdapterError
from autoposter.adapters.fanvue import FanvueAdapter
from autoposter.services import appconfig, credentials, chatbot_fanvue

ZONE = ZoneInfo('Europe/Berlin')
PREFIX = 'fanvue_visibility:'
LEASE = timedelta(minutes=5)


def timestamp(raw):
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return value.astimezone(timezone.utc) if value.tzinfo is not None else None
    except ValueError:
        return None


def eligible(post, now, days):
    published = timestamp(post.get('publishedAt'))
    scheduled = post.get('publishAt')
    expires = post.get('expiresAt')
    if expires is not None and (timestamp(expires) is None or timestamp(expires) <= now):
        return False
    # Missing actual publication is never inferred from createdAt/local cache.
    if scheduled is not None and (timestamp(scheduled) is None or timestamp(scheduled) > now):
        return False
    return (post.get('audience') == 'followers-and-subscribers' and published is not None
            and published <= now - timedelta(days=days))


async def accounts(db):
    """Only active Fanvue channels bound to the current shared AutoChat account."""
    channels = (await db.execute(select(Channel).where(Channel.platform == 'fanvue', Channel.is_active.is_(True)))).scalars().all()
    tokens = chatbot_fanvue.chatbot_db.get_tokens()
    account = tokens['account_uuid'] if tokens else None
    return [(ch, account if account and chatbot_fanvue.account_matches(ch) else None) for ch in channels]


async def claim(db, account, now, manual):
    key = PREFIX + account
    if db.bind.dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    await db.execute(insert(AppSetting).values(key=key, value={}).on_conflict_do_nothing(index_elements=['key']))
    if db.bind.dialect.name == 'sqlite':
        await db.execute(update(AppSetting).where(AppSetting.key == key).values(key=key))
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key).with_for_update().execution_options(populate_existing=True))).scalar_one()
    value = dict(row.value or {})
    until = timestamp(value.get('lease_until'))
    today = now.astimezone(ZONE).date().isoformat()
    reason = None
    if until and until > now:
        reason = 'running'
    elif not manual and value.get('completed_slot') == today:
        reason = 'already_done'
    elif not manual and timestamp(value.get('retry_at')) and timestamp(value['retry_at']) > now:
        reason = 'retry_wait'
    if reason:
        await db.commit()
        return None, reason
    token = str(uuid.uuid4())
    value.pop('completed_slot', None)
    value.update(token=token, lease_until=(now + LEASE).isoformat(), started_at=now.isoformat(), status='running', manual=manual,
                 scanned=0, changed=0, skipped=0, errors=[], finished_at=None)
    row.value = value
    await db.commit()
    return token, value


async def heartbeat(db, account, token, clock):
    now = clock()
    stmt = update(AppSetting).where(AppSetting.key == PREFIX + account,
        AppSetting.value['token'].as_string() == token).values(
        # Keep status/counters and extend the lease through the owned row.
        value=None)
    row = (await db.execute(select(AppSetting).where(AppSetting.key == PREFIX + account).execution_options(populate_existing=True))).scalar_one()
    value = dict(row.value)
    if value.get('token') != token or timestamp(value.get('lease_until')) <= now:
        await db.rollback()
        raise AdapterError('Sichtbarkeitslauf hat seine Sperre verloren; abgebrochen')
    value['lease_until'] = (now + LEASE).isoformat()
    result = await db.execute(stmt.values(value=value).execution_options(synchronize_session=False))
    await db.commit()
    if result.rowcount != 1:
        raise AdapterError('Sichtbarkeitslauf hat seine Sperre verloren; abgebrochen')


async def status(db):
    rows = (await db.execute(select(AppSetting).where(AppSetting.key.startswith(PREFIX, autoescape=True)))).scalars().all()
    reports = []
    for row in rows:
        item = {**{k:v for k,v in (row.value or {}).items() if k not in {'token','lease_until'}}, 'account_id':row.key[len(PREFIX):]}
        if item.get('status') == 'running':
            until = timestamp((row.value or {}).get('lease_until'))
            if not until or until <= datetime.now(timezone.utc):
                item['status'] = 'interrupted'
        reports.append(item)
    last = await db.get(AppSetting, 'visibility_aging_last_run')
    return {'schedule':'00:00 Europe/Berlin', 'runtime_status':'paused' if settings.global_pause else 'dry_run' if settings.dry_run else 'ready', 'last_run':last.value if last else None, 'accounts':reports}


async def run(db, *, now=None, manual=False):
    fixed = now
    clock = (lambda: fixed) if fixed is not None else (lambda: datetime.now(timezone.utc))
    now = clock()
    values = await appconfig.get_all(db, use_cache=False)
    if not manual and not values['visibility_aging_enabled']:
        return {'status':'disabled', 'accounts':[]}
    days = values['visibility_aging_days']
    if type(days) is not int or not 1 <= days <= 36500:
        return {'status':'error', 'error':'Ungültiges gespeichertes Sichtbarkeitsalter', 'accounts':[]}
    if settings.global_pause or settings.dry_run:
        return {'status':'paused' if settings.global_pause else 'dry_run', 'accounts':[]}
    targets = await accounts(db)
    await db.commit()
    results=[]; seen_accounts=set()
    for channel, account in targets:
        if not account:
            results.append({'channel_id':str(channel.id), 'status':'error', 'error':'Fanvue-Konto fehlt oder Kanalbindung stimmt nicht'})
            continue
        if account in seen_accounts:
            continue
        seen_accounts.add(account)
        token, value = await claim(db, account, clock(), manual)
        if not token:
            results.append({'account_id':account, 'status':value})
            continue
        result = {'account_id':account, 'status':'ok', 'scanned':0, 'changed':0, 'skipped':0, 'errors':[]}
        adapter = FanvueAdapter()
        async def request(method, path, **kwargs):
            if settings.global_pause or settings.dry_run:
                raise AdapterError('Notfall-Stopp oder Trockenlauf aktiviert; Sichtbarkeitslauf abgebrochen')
            await heartbeat(db, account, token, clock)
            return await adapter.visibility_request(cred, method, path, **kwargs)
        try:
            try:
                cred = await credentials.get_valid_credentials(db, channel)
            except Exception:
                raise AdapterError('Fanvue-Zugangsdaten nicht verfügbar; AutoChat-Verbindung prüfen') from None
            await db.commit()
            if cred.external_user_id != account:
                raise AdapterError('Fanvue-Zugangsdaten gehören zu einem anderen Konto')
            if not {'read:post','write:post'}.issubset(set(cred.scopes)):
                raise AdapterError('Fanvue-Berechtigungen read:post und write:post fehlen; neu verbinden')
            identity = await request('GET', '/users/me')
            if identity.get('uuid') != account:
                raise AdapterError('Fanvue-Kontoidentität stimmt nicht überein')
            seen=set(); cursors=set(); cursor=None
            while True:
                params={'size':50, 'includeUnpublished':'false'}
                if cursor:
                    params['cursor']=cursor
                page = await request('GET', '/v1/posts', params=params)
                if not isinstance(page.get('data'), list) or 'nextCursor' not in page:
                    raise AdapterError('Fanvue lieferte eine ungültige Post-Seite')
                for item in page['data']:
                    raw = item.get('uuid') if isinstance(item, dict) else None
                    try:
                        if str(uuid.UUID(raw)) != raw:
                            raise ValueError()
                    except (ValueError, TypeError, AttributeError):
                        raise AdapterError('Fanvue lieferte eine ungültige Post-UUID') from None
                    if raw in seen:
                        continue
                    seen.add(raw); result['scanned'] += 1
                    if not eligible(item, now, days):
                        result['skipped'] += 1
                        continue
                    path='/v1/posts/' + raw
                    stage = 'precheck'
                    try:
                        current=await request('GET', path)
                        if current.get('uuid') != raw:
                            raise AdapterError('Fanvue Post-Identität stimmt nicht überein')
                        if not eligible(current, now, days):
                            result['skipped'] += 1
                            continue
                        stage = 'patch'
                        await request('PATCH', path, json={'audience':'subscribers'})
                        stage = 'postcheck'
                        verified=await request('GET', path)
                        if verified.get('uuid') != raw or verified.get('audience') != 'subscribers':
                            raise AdapterError('Fanvue Sichtbarkeitsänderung konnte nicht bestätigt werden')
                        result['changed'] += 1
                    except AdapterError as exc:
                        result['errors'].append({
                            'post_uuid':raw, 'error':str(exc), 'stage':stage,
                            'http_status':exc.status, 'method':'PATCH' if stage == 'patch' else 'GET',
                            'path':path,
                            'update_state':{'precheck':'not_attempted', 'patch':'update_failed', 'postcheck':'unconfirmed'}[stage],
                        })
                        if exc.status in {401,403,410} or 'Sperre' in str(exc):
                            raise
                cursor=page['nextCursor']
                if cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or cursor in cursors:
                    raise AdapterError('Fanvue Pagination wiederholt einen Cursor oder ist ungültig')
                cursors.add(cursor)
        except Exception as exc:
            # Only our controlled AdapterError messages can enter persisted status.
            result['errors'].append({'error':str(exc) if isinstance(exc, AdapterError) else 'Interner Sichtbarkeitsfehler; Serverprotokoll prüfen'})
        if result['errors']:
            result['status']='error'
        finished=clock()
        value.update(result, finished_at=finished.isoformat(), lease_until=None,
                     retry_at=(finished+timedelta(minutes=15)).isoformat() if result['errors'] else None)
        if not result['errors']:
            value['completed_slot']=now.astimezone(ZONE).date().isoformat()
        else:
            value.pop('completed_slot', None)
        await db.execute(update(AppSetting).where(AppSetting.key == PREFIX+account,
            AppSetting.value['token'].as_string() == token).values(value=value).execution_options(synchronize_session=False))
        await db.commit()
        results.append(result)
    return {'status':'error' if any(r['status']=='error' for r in results) else 'ok', 'schedule':'00:00 Europe/Berlin', 'accounts':results}
