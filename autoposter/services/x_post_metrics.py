"""On-demand lifetime X public metrics for Top Posts X.

Only an authenticated user action may claim a collection. The claim and per-mode
trigger timestamps survive restarts; network requests never hold the DB writer lock.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update

from autoposter.models import AppSetting, Channel, ChannelCredential, Post
from autoposter.services.top_posts import COLLECTION_KEY
from autoposter.services import credentials, oauthapp
from autoposter.services.followers import x_account_matches
from autoposter.adapters.x import XAdapter

MODES = {None: 'all', 180: '180', 30: '30'}


async def _locked_state(db):
    # SQLite's no-op UPDATE takes the writer lock; PostgreSQL uses FOR UPDATE.
    if db.bind.dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    await db.execute(insert(AppSetting).values(key=COLLECTION_KEY, value={}).on_conflict_do_nothing(index_elements=['key']))
    await db.execute(update(AppSetting).where(AppSetting.key == COLLECTION_KEY).values(key=COLLECTION_KEY))
    row = (await db.execute(select(AppSetting).where(AppSetting.key == COLLECTION_KEY)
                            .with_for_update().execution_options(populate_existing=True))).scalar_one()
    return row, dict(row.value or {})


async def claim(db, days, now=None):
    if days not in MODES:
        raise ValueError('Only all, 180 or 30 days are supported')
    now = now or datetime.now(timezone.utc)
    row, state = await _locked_state(db)
    active = state.get('active') or {}
    if active:
        try:
            started = datetime.fromisoformat(active['started_at'])
        except (KeyError, TypeError, ValueError):
            started = now
        if now - started < timedelta(minutes=45):
            await db.commit()
            raise HTTPException(409, 'Eine X-Aktualisierung läuft bereits. Bitte später erneut versuchen.')
    token = uuid4().hex
    mode = MODES[days]
    manual = dict(state.get('manual') or {})
    manual[mode] = {'triggered_at': now.isoformat(), 'status': 'running'}
    state.update(active={'token': token, 'mode': mode, 'started_at': now.isoformat()},
                 manual=manual, attempted_at=now.isoformat(), status='running', errors=[])
    row.value = state
    await db.commit()
    return token


async def collect(db, days, token, now=None):
    """Run only for a previously claimed manual request; never schedule this."""
    if days not in MODES:
        raise ValueError('Only all, 180 or 30 days are supported')
    now = now or datetime.now(timezone.utc)
    row = await db.get(AppSetting, COLLECTION_KEY, populate_existing=True)
    active = (row.value or {}).get('active') or {} if row else {}
    if active.get('token') != token or active.get('mode') != MODES[days]:
        raise ValueError('No matching manual X collection claim')
    await db.commit()
    channels = (await db.execute(select(Channel).where(Channel.platform == 'x'))).scalars().all()
    updated, unavailable, errors = 0, 0, []
    adapter = XAdapter()
    for channel_id in [channel.id for channel in channels]:
        channel = await db.get(Channel, channel_id, populate_existing=True)
        channel_name = channel.display_name
        query = select(Post.id, Post.external_post_id).where(
            Post.channel_id == channel.id, Post.status == 'published', Post.external_post_id.isnot(None))
        if days is not None:
            query = query.where(Post.published_at >= now - timedelta(days=days))
        posts = (await db.execute(query)).all()
        posts = [(pid, str(external)) for pid, external in posts if str(external).isdigit()]
        if not posts:
            continue
        if channel.verified_account and not x_account_matches(channel, channel.verified_account):
            errors.append(channel.display_name + ': falsches X-Konto verbunden')
            continue
        credrow = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
        if not credrow or not credrow.access_token_enc or (credrow.expires_at and credrow.expires_at <= now):
            errors.append(channel.display_name + ': Anmeldung prüfen')
            continue
        try:
            cred = credentials.to_credentials(credrow)
            await db.commit()
            info = await adapter.verify(oauthapp.for_channel(channel), cred)
            if not info.ok or not x_account_matches(channel, info.username):
                raise ValueError('account mismatch')
            cred.external_user_id = info.account_id
            by_external = {}
            for pid, external in posts:
                by_external.setdefault(external, []).append(pid)
            ids = list(by_external)
            for offset in range(0, len(ids), 100):
                chunk = ids[offset:offset+100]
                response = await adapter.fetch_metrics_batch(cred, chunk)
                measured, changes = set(), []
                for item in response.get('data', []):
                    external = str(item.get('id'))
                    if external not in by_external:
                        continue
                    measured.add(external)
                    if str(item.get('author_id')) != str(cred.external_user_id):
                        changes.extend((pid, {'x_account_mismatch': True}) for pid in by_external[external])
                        unavailable += 1
                        continue
                    metrics = item.get('public_metrics') or {}
                    if type(metrics.get('impression_count')) is not int or metrics['impression_count'] < 0:
                        unavailable += 1
                        continue
                    changes.extend((pid, {**metrics, 'x_metrics_sampled_at': now.isoformat(),
                                           'x_account_mismatch': False}) for pid in by_external[external])
                unavailable += len(set(chunk)-measured)
                for pid, metrics in changes:
                    post = await db.get(Post, pid)
                    if post:
                        post.metrics = {**(post.metrics or {}), **metrics}
                        updated += 1
                await db.commit()
        except Exception as exc:
            await db.rollback()
            code = getattr(exc, 'status', None)
            errors.append(channel_name + ': Kennzahlen nicht verfügbar' + (f' (API {code})' if type(code) is int else ''))
    row, state = await _locked_state(db)
    if (state.get('active') or {}).get('token') == token:
        completed_at = datetime.now(timezone.utc).isoformat()
        mode = MODES[days]
        manual = dict(state.get('manual') or {})
        manual[mode] = {**manual[mode], 'completed_at': completed_at,
                        'status': 'partial' if errors else 'ok', 'updated': updated,
                        'unavailable': unavailable, 'errors': errors}
        state.update(manual=manual, active=None, status='partial' if errors else 'ok',
                     completed_at=completed_at, updated=updated, unavailable=unavailable, errors=errors)
        if not errors:
            state['last_success_at'] = completed_at
        row.value = state
    await db.commit()
    return {'updated': updated, 'unavailable': unavailable, 'errors': errors}


async def fail(db, token):
    """Release a claim if an unexpected failure aborts the background task."""
    row, state = await _locked_state(db)
    active = state.get('active') or {}
    if active.get('token') == token:
        mode = active['mode']
        manual = dict(state.get('manual') or {})
        manual[mode] = {**manual[mode], 'status': 'error', 'completed_at': datetime.now(timezone.utc).isoformat(),
                        'errors': ['Aktualisierung fehlgeschlagen. Bitte erneut versuchen.']}
        state.update(manual=manual, active=None, status='error', errors=manual[mode]['errors'])
        row.value = state
    await db.commit()
