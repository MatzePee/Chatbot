"""Opt-in Fanvue feed retention. Never alter local AutoPost history or media."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, update
from autoposter.adapters.base import AdapterError
from autoposter.adapters.fanvue import FanvueAdapter
from autoposter.config import settings
from autoposter.models import AppSetting
from autoposter.services import appconfig, credentials, visibility_aging

accounts = visibility_aging.accounts
stamp = visibility_aging.timestamp
ZONE = ZoneInfo('Europe/Berlin')
PREFIX = 'fanvue_retention:'
LEASE = timedelta(minutes=5)
# About 92 requests/minute, leaving headroom in Fanvue's shared 200/min bucket.
REQUEST_GAP_SECONDS = 0.65


def eligible(post, now, days, delete_ppv):
    """Fail closed: only unambiguously published, old, free/paid posts."""
    if not isinstance(post, dict):
        return False
    # Fail closed if the provider omits or ambiguously encodes pin state.
    # This predicate is used both for preview/list scan and the final GET
    # immediately before DELETE, protecting posts pinned in between.
    if post.get('isPinned') is not False:
        return False
    published = stamp(post.get('publishedAt'))
    if not published or published >= now - timedelta(days=days) or published > now:
        return False
    scheduled = post.get('publishAt')
    if scheduled is not None and (stamp(scheduled) is None or stamp(scheduled) > now):
        return False
    expires = post.get('expiresAt')
    if expires is not None and (stamp(expires) is None or stamp(expires) <= now):
        return False
    if 'price' not in post:
        return False
    price = post['price']
    if price is None:
        price = 0  # Fanvue explicitly represents a free post as null.
    if type(price) not in (int, float) or price < 0 or price != price:
        return False
    if price > 0 and not delete_ppv:
        return False
    return True


def valid_uuid(raw):
    try:
        return str(uuid.UUID(raw)) == raw
    except (TypeError, ValueError, AttributeError):
        return False


async def scan(request, now, days, delete_ppv):
    """List every published post on the provider, independent of local history."""
    candidates = []
    seen, cursors = set(), set()
    cursor = None
    scanned = 0
    while True:
        params = {'size': 50, 'includeUnpublished': 'false'}
        if cursor:
            params['cursor'] = cursor
        page = await request('GET', '/v1/posts', params=params)
        if not isinstance(page, dict) or not isinstance(page.get('data'), list) or 'nextCursor' not in page:
            raise AdapterError('Ungültige Fanvue-Postliste; kein Löschen aus dieser Seite')
        for post in page['data']:
            raw = post.get('uuid') if isinstance(post, dict) else None
            if not valid_uuid(raw):
                raise AdapterError('Ungültige Fanvue-Post-ID; kein Löschen aus dieser Seite')
            if raw in seen:
                continue
            seen.add(raw)
            scanned += 1
            if eligible(post, now, days, delete_ppv):
                candidates.append(raw)
        cursor = page['nextCursor']
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise AdapterError('Ungültige oder wiederholte Fanvue-Pagination')
        cursors.add(cursor)
    return scanned, candidates


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
    if stamp(value.get('lease_until')) and stamp(value['lease_until']) > now:
        await db.commit()
        return None, 'running'
    if not manual and value.get('completed_slot') == now.astimezone(ZONE).date().isoformat():
        await db.commit()
        return None, 'already_done'
    token = str(uuid.uuid4())
    value.update(token=token, lease_until=(now+LEASE).isoformat(), started_at=now.isoformat(), status='running', deleted=0, skipped=0, scanned=0, errors=[])
    row.value = value
    await db.commit()
    return token, value


async def heartbeat(db, account, token, clock):
    key = PREFIX + account
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key).execution_options(populate_existing=True))).scalar_one()
    value = dict(row.value or {})
    now = clock()
    if value.get('token') != token or not stamp(value.get('lease_until')) or stamp(value['lease_until']) <= now:
        await db.rollback()
        raise AdapterError('Löschlauf hat seine Sperre verloren; abgebrochen')
    value['lease_until'] = (now+LEASE).isoformat()
    changed = await db.execute(update(AppSetting).where(AppSetting.key == key, AppSetting.value['token'].as_string() == token).values(value=value).execution_options(synchronize_session=False))
    await db.commit()
    if changed.rowcount != 1:
        raise AdapterError('Löschlauf hat seine Sperre verloren; abgebrochen')


async def process(db, *, days, delete_ppv, execute, now=None, manual=True):
    if type(days) is not int or not 1 <= days <= 36500 or type(delete_ppv) is not bool:
        raise ValueError('Alter (1–36500 Tage) und PPV-Auswahl ungültig')
    if execute and not settings.post_retention_active:
        return {'status':'disabled', 'accounts':[]}
    if execute and (settings.dry_run or settings.global_pause):
        return {'status':'dry_run' if settings.dry_run else 'paused', 'accounts':[]}
    clock = (lambda: now) if now else (lambda: datetime.now(timezone.utc))
    moment = clock()
    targets = await accounts(db)
    await db.commit()
    reports = []
    seen_accounts = set()
    for channel, account in targets:
        if not account:
            reports.append({'status':'error','error':'Fanvue-Konto oder Kanalbindung fehlt'})
            continue
        if account in seen_accounts:
            continue
        seen_accounts.add(account)
        token = None
        if execute:
            token, reason = await claim(db, account, clock(), manual)
            if token is None:
                reports.append({'account_id':account,'status':reason})
                continue
        report = {'account_id':account,'status':'ok','scanned':0,'candidates':[], 'deleted':0,'skipped':0,'errors':[]}
        adapter = FanvueAdapter()
        next_request_at = asyncio.get_running_loop().time()
        async def request(method, path, **kwargs):
            nonlocal next_request_at
            if execute:
                if not settings.post_retention_active or settings.global_pause or settings.dry_run:
                    raise AdapterError('Löschfunktion oder Notfall-Stopp aktiv; Lauf abgebrochen')
                await heartbeat(db, account, token, clock)
                loop = asyncio.get_running_loop()
                delay = next_request_at - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                if settings.global_pause or settings.dry_run or not settings.post_retention_active:
                    raise AdapterError('Löschfunktion oder Notfall-Stopp aktiv; Lauf abgebrochen')
                # The delay is per account and includes GET, DELETE and readback.
                next_request_at = loop.time() + REQUEST_GAP_SECONDS
            return await adapter.retention_request(cred, method, path, **kwargs)
        try:
            try:
                cred = await credentials.get_valid_credentials(db, channel)
            except Exception:
                raise AdapterError('Fanvue-Verbindung nicht verfügbar') from None
            await db.commit()
            if cred.external_user_id != account or not {'read:post','write:post'}.issubset(set(cred.scopes)):
                raise AdapterError('Fanvue-Konto oder erforderliche Post-Berechtigungen stimmen nicht')
            identity = await request('GET', '/users/me')
            if not isinstance(identity, dict) or identity.get('uuid') != account:
                raise AdapterError('Fanvue-Kontoidentität stimmt nicht überein')
            report['scanned'], candidates = await scan(request, moment, days, delete_ppv)
            report['candidates'] = candidates if not execute else []
            if execute:
                for raw in candidates:
                    path = '/v1/posts/' + raw
                    stage = 'precheck'
                    try:
                        current = await request('GET', path)
                        if current is None:
                            report['skipped'] += 1
                            continue
                        if not isinstance(current, dict) or current.get('uuid') != raw:
                            raise AdapterError('Fanvue Post-Identität stimmt nicht überein')
                        if not eligible(current, clock(), days, delete_ppv):
                            report['skipped'] += 1
                            continue
                        stage = 'delete'
                        await request('DELETE', path)
                        stage = 'postcheck'
                        if await request('GET', path) is not None:
                            raise AdapterError('Löschung nicht bestätigt')
                        report['deleted'] += 1
                    except AdapterError as exc:
                        report['errors'].append({'post_uuid':raw,'stage':stage,'error':str(exc)})
                        if exc.status in {401,403,410,429} or 'Sperre' in str(exc) or 'abgebrochen' in str(exc):
                            raise
        except Exception as exc:
            message=str(exc) if isinstance(exc, AdapterError) else 'Interner Fehler; Serverprotokoll prüfen'
            # A fatal per-post error is already recorded with the post and phase.
            if not report['errors'] or report['errors'][-1].get('error') != message:
                report['errors'].append({'error':message})
        if report['errors']:
            report['status'] = 'error'
        if execute:
            finished=clock()
            saved={**report,'token':token,'lease_until':None,'finished_at':finished.isoformat()}
            # Automatic retention attempts at most once per Berlin calendar day,
            # even on partial provider failure; a later manual run remains possible.
            if not manual or not report['errors']:
                saved['completed_slot']=moment.astimezone(ZONE).date().isoformat()
            changed=await db.execute(update(AppSetting).where(AppSetting.key == PREFIX+account,AppSetting.value['token'].as_string() == token).values(value=saved).execution_options(synchronize_session=False))
            await db.commit()
            if changed.rowcount != 1:
                report['status']='error';report['errors'].append({'error':'Laufstatus konnte nicht gesichert werden'})
        reports.append(report)
    return {'status':'error' if any(x['status']=='error' for x in reports) else 'ok', 'accounts':reports}


async def preview(db, *, days, delete_ppv, now=None):
    return await process(db, days=days, delete_ppv=delete_ppv, execute=False, now=now)


async def run(db, *, days, delete_ppv, manual=True, now=None):
    return await process(db, days=days, delete_ppv=delete_ppv, execute=True, now=now, manual=manual)


async def scheduled(db):
    if not settings.post_retention_active or not settings.post_retention_automatic_active:
        return {'status':'disabled'}
    values = await appconfig.get_all(db, use_cache=False)
    if values['post_retention_automatic'] is not True:
        return {'status':'disabled'}
    return await run(db, days=values['post_retention_days'],delete_ppv=values['post_retention_delete_ppv'],manual=False)


async def status(db):
    rows=(await db.execute(select(AppSetting).where(AppSetting.key.startswith(PREFIX, autoescape=True)))).scalars().all()
    last = await db.get(AppSetting, 'fanvue_retention_last_run')
    return {'active': settings.post_retention_active, 'automatic_active': settings.post_retention_automatic_active, 'last_run': last.value if last else None, 'accounts':[
        {'account_id': row.key[len(PREFIX):], **{k:v for k,v in (row.value or {}).items() if k not in {'token','lease_until'}}}
        for row in rows]}
