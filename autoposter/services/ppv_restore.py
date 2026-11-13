"""Explicit manual restoration of paid subscriber-only Fanvue feed posts.

Never scheduled. Preview does not PATCH; each update rechecks the live post.
"""
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from autoposter.adapters.base import AdapterError
from autoposter.adapters.fanvue import FanvueAdapter
from autoposter.config import settings
from autoposter.models import AppSetting
from autoposter.services import credentials, visibility_aging

accounts = visibility_aging.accounts
stamp = visibility_aging.timestamp
PREFIX = 'fanvue_ppv_restore:'
LEASE = timedelta(minutes=5)


def eligible(post, now):
    if not isinstance(post, dict):
        return False
    price = post.get('price')
    if type(price) not in (int, float) or not 0 < price < float('inf'):
        return False
    published = stamp(post.get('publishedAt'))
    if published is None or published > now:
        return False
    scheduled = post.get('publishAt')
    if scheduled is not None and (stamp(scheduled) is None or stamp(scheduled) > now):
        return False
    expires = post.get('expiresAt')
    if expires is not None and (stamp(expires) is None or stamp(expires) <= now):
        return False
    return post.get('audience') == 'subscribers'


def valid_uuid(raw):
    try:
        return str(uuid.UUID(raw)) == raw
    except (ValueError, TypeError, AttributeError):
        return False


async def claim(db, account, now):
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
    until = stamp(value.get('lease_until'))
    if until and until > now:
        await db.commit()
        return None
    token = str(uuid.uuid4())
    row.value = {'token':token, 'lease_until':(now+LEASE).isoformat(), 'started_at':now.isoformat(), 'status':'running',
                 'scanned':0, 'candidates':0, 'changed':0, 'skipped':0, 'errors':[]}
    await db.commit()
    return token


async def heartbeat(db, account, token, now):
    key = PREFIX + account
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key).execution_options(populate_existing=True))).scalar_one()
    value = dict(row.value or {})
    lease = stamp(value.get('lease_until'))
    if value.get('token') != token or not lease or lease <= now:
        await db.rollback()
        raise AdapterError('PPV-Lauf hat seine Sperre verloren; abgebrochen')
    value['lease_until'] = (now+LEASE).isoformat()
    changed = await db.execute(update(AppSetting).where(AppSetting.key == key, AppSetting.value['token'].as_string() == token).values(value=value).execution_options(synchronize_session=False))
    await db.commit()
    if changed.rowcount != 1:
        raise AdapterError('PPV-Lauf hat seine Sperre verloren; abgebrochen')


async def process(db, *, execute, now=None):
    clock = (lambda: now) if now else (lambda: datetime.now(timezone.utc))
    if execute and (settings.global_pause or settings.dry_run):
        return {'status':'paused' if settings.global_pause else 'dry_run', 'accounts':[]}
    targets = await accounts(db)
    await db.commit()
    reports=[]; seen_accounts=set()
    for channel, account in targets:
        if not account:
            reports.append({'status':'error','error':'Fanvue-Konto oder Kanalbindung fehlt'})
            continue
        if account in seen_accounts:
            continue
        seen_accounts.add(account)
        token = await claim(db, account, clock()) if execute else None
        if execute and token is None:
            reports.append({'account_id':account,'status':'running'})
            continue
        report={'account_id':account,'status':'ok','scanned':0,'candidates':0,'changed':0,'skipped':0,'errors':[]}
        adapter=FanvueAdapter()
        async def request(method,path,**kwargs):
            if execute:
                if settings.global_pause or settings.dry_run:
                    raise AdapterError('Notfall-Stopp oder Trockenlauf aktiv; PPV-Lauf abgebrochen')
                await heartbeat(db,account,token,clock())
            return await adapter.visibility_request(cred,method,path,**kwargs)
        try:
            try:
                cred=await credentials.get_valid_credentials(db,channel)
            except Exception:
                raise AdapterError('Fanvue-Verbindung nicht verfügbar') from None
            await db.commit()
            if cred.external_user_id != account or not {'read:post','write:post'}.issubset(set(cred.scopes)):
                raise AdapterError('Fanvue-Konto oder Post-Berechtigungen stimmen nicht')
            identity=await request('GET','/users/me')
            if not isinstance(identity,dict) or identity.get('uuid') != account:
                raise AdapterError('Fanvue-Kontoidentität stimmt nicht überein')
            seen=set(); cursors=set(); cursor=None
            while True:
                params={'size':50,'includeUnpublished':'false'}
                if cursor: params['cursor']=cursor
                page=await request('GET','/v1/posts',params=params)
                if not isinstance(page,dict) or not isinstance(page.get('data'),list) or 'nextCursor' not in page:
                    raise AdapterError('Ungültige Fanvue-Postliste; Lauf abgebrochen')
                for item in page['data']:
                    raw=item.get('uuid') if isinstance(item,dict) else None
                    if not valid_uuid(raw):
                        raise AdapterError('Ungültige Fanvue-Post-ID; Lauf abgebrochen')
                    if raw in seen: continue
                    seen.add(raw); report['scanned']+=1
                    if not eligible(item,clock()): continue
                    report['candidates']+=1
                    if not execute: continue
                    path='/v1/posts/'+raw; stage='precheck'
                    try:
                        current=await request('GET',path)
                        if not isinstance(current,dict) or current.get('uuid') != raw:
                            raise AdapterError('Fanvue Post-Identität stimmt nicht überein')
                        if not eligible(current,clock()):
                            report['skipped']+=1
                            continue
                        stage='patch'
                        await request('PATCH',path,json={'audience':'followers-and-subscribers'})
                        stage='postcheck'
                        verified=await request('GET',path)
                        if not isinstance(verified,dict) or verified.get('uuid') != raw or verified.get('audience') != 'followers-and-subscribers' or verified.get('price') != current['price']:
                            raise AdapterError('PPV-Sichtbarkeit oder Preis nicht bestätigt')
                        report['changed']+=1
                    except AdapterError as exc:
                        report['errors'].append({'post_uuid':raw,'stage':stage,'error':str(exc)})
                        if exc.status in {401,403,410} or 'Sperre' in str(exc) or 'abgebrochen' in str(exc):
                            raise
                cursor=page['nextCursor']
                if cursor is None: break
                if not isinstance(cursor,str) or not cursor or cursor in cursors:
                    raise AdapterError('Ungültige oder wiederholte Fanvue-Pagination')
                cursors.add(cursor)
        except Exception as exc:
            report['errors'].append({'error':str(exc) if isinstance(exc,AdapterError) else 'Interner PPV-Fehler; Serverprotokoll prüfen'})
        if report['errors']: report['status']='error'
        if execute:
            saved={**report,'token':token,'lease_until':None,'finished_at':clock().isoformat()}
            changed=await db.execute(update(AppSetting).where(AppSetting.key == PREFIX+account,AppSetting.value['token'].as_string() == token).values(value=saved).execution_options(synchronize_session=False))
            await db.commit()
            if changed.rowcount != 1:
                report['status']='error'; report['errors'].append({'error':'Laufstatus konnte nicht gespeichert werden'})
        reports.append(report)
    return {'status':'error' if any(r['status']=='error' for r in reports) else 'ok','accounts':reports}


async def preview(db, *, now=None):
    return await process(db,execute=False,now=now)


async def run(db, *, now=None):
    return await process(db,execute=True,now=now)


async def status(db):
    rows=(await db.execute(select(AppSetting).where(AppSetting.key.startswith(PREFIX,autoescape=True)))).scalars().all()
    reports=[]
    for row in rows:
        value=dict(row.value or {})
        item={k:v for k,v in value.items() if k not in {'token','lease_until'}}
        if item.get('status')=='running' and (not stamp(value.get('lease_until')) or stamp(value['lease_until']) <= datetime.now(timezone.utc)):
            item['status']='interrupted'
        reports.append({'account_id':row.key[len(PREFIX):],**item})
    return {'accounts':reports}
