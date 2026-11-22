"""Cached audience totals. Same-platform accounts deduped; missing days never estimated."""
from datetime import datetime, timedelta, timezone
from autoposter.services.followers import ZONE

PLATFORMS = ('fanvue', 'x', 'instagram')

def valid(value):
    return type(value) is int and value >= 0

def aggregate(accounts, now=None):
    today = (now or datetime.now(timezone.utc)).astimezone(ZONE).date()
    unique = {}
    for account in accounts:
        key = (account['platform'], account.get('account_id') or 'unbound:' + account['channel_id'])
        # Keep the newest sample when several posting channels use one account.
        previous = unique.get(key)
        if previous is None or (account.get('sampled_at') or '') > (previous.get('sampled_at') or ''):
            unique[key] = account
    rows = list(unique.values())
    measured = [a for a in rows if valid(a.get('current'))]
    current = sum(a['current'] for a in measured) if measured else None
    result = {'name':'Fanvue, X und Instagram gesamt', 'current':current, 'accounts':len(rows),
              'measured':len(measured), 'complete':bool(rows) and len(measured)==len(rows),
              'stale':any(a.get('stale', True) for a in rows), 'timezone':'Europe/Berlin'}
    for label in ('yesterday','days7','days30'):
        complete = result['complete'] and not result['stale'] and all(valid((a.get(label) or {}).get('count')) for a in rows)
        baseline = sum(a[label]['count'] for a in rows) if complete else None
        growth = current-baseline if baseline is not None else None
        result[label] = {'count':baseline,'growth':growth,'percent':growth/baseline*100 if baseline else None}
    history = [{p['date']:p['count'] for p in a.get('history',[])} for a in rows]
    result['history'] = []
    for offset in range(29,-1,-1):
        day = (today-timedelta(days=offset)).isoformat()
        count = sum(h[day] for h in history) if history and all(valid(h.get(day)) for h in history) else None
        result['history'].append({'date':day,'count':count})
    return result

async def overview(db, now=None):
    from autoposter.services import followers
    platforms = []
    all_accounts = []
    for platform in PLATFORMS:
        accounts = await followers.overview(db, now=now, platform=platform)
        all_accounts.extend(accounts)
        platforms.append({'platform':platform, **aggregate(accounts,now)})
    return {'total':aggregate(all_accounts,now), 'platforms':platforms}
