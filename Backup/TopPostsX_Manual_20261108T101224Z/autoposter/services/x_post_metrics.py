"""Once per Berlin calendar day at/after 01:00: lifetime X public metrics.
Claims survive restarts. HTTP is performed without holding a DB writer lock.
"""
from datetime import datetime, timezone
from sqlalchemy import select, update
from autoposter.models import AppSetting, Channel, ChannelCredential, Post
from autoposter.services.top_posts import COLLECTION_KEY, ZONE
from autoposter.services import credentials, oauthapp
from autoposter.services.followers import x_account_matches
from autoposter.adapters.x import XAdapter


def due(now, state):
    local = now.astimezone(ZONE)
    return local.hour >= 1 and state.get('daily_slot') != local.date().isoformat()


async def collect(db, now=None):
    now = now or datetime.now(timezone.utc)
    # Shared by in-process and Celery workers, including concurrent invocations.
    if db.bind.dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    await db.execute(insert(AppSetting).values(key=COLLECTION_KEY,value={}).on_conflict_do_nothing(index_elements=['key']))
    await db.execute(update(AppSetting).where(AppSetting.key==COLLECTION_KEY).values(key=COLLECTION_KEY))
    row = (await db.execute(select(AppSetting).where(AppSetting.key==COLLECTION_KEY).with_for_update().execution_options(populate_existing=True))).scalar_one()
    state = dict(row.value or {})
    if not due(now,state):
        await db.commit()
        return {'skipped':True}
    stamp = now.isoformat()
    state.update(daily_slot=now.astimezone(ZONE).date().isoformat(), attempted_at=stamp, status='running', errors=[])
    row.value = dict(state)
    await db.commit()
    channels = (await db.execute(select(Channel).where(Channel.platform=='x'))).scalars().all()
    updated, unavailable, errors = 0, 0, []
    adapter = XAdapter()
    channel_ids = [channel.id for channel in channels]
    for channel_id in channel_ids:
        channel = await db.get(Channel,channel_id,populate_existing=True)
        channel_name = channel.display_name
        posts = (await db.execute(select(Post.id,Post.external_post_id).where(
            Post.channel_id==channel.id, Post.status=='published', Post.external_post_id.isnot(None)))).all()
        posts = [(pid,str(external)) for pid,external in posts if str(external).isdigit()]
        if not posts:
            continue
        if channel.verified_account and not x_account_matches(channel,channel.verified_account):
            errors.append(channel.display_name+': falsches X-Konto verbunden')
            continue
        credrow = await db.get(ChannelCredential,channel.credential_id) if channel.credential_id else None
        if not credrow or not credrow.access_token_enc or (credrow.expires_at and credrow.expires_at<=now):
            errors.append(channel.display_name+': Anmeldung prüfen')
            continue
        try:
            cred = credentials.to_credentials(credrow)
            await db.commit()
            info = await adapter.verify(oauthapp.for_channel(channel),cred)
            if not info.ok or not x_account_matches(channel,info.username):
                raise ValueError('account mismatch')
            cred.external_user_id = info.account_id
            by_external = {}
            for pid, external in posts:
                by_external.setdefault(external,[]).append(pid)
            ids = list(by_external)
            for offset in range(0,len(ids),100):
                chunk = ids[offset:offset+100]
                response = await adapter.fetch_metrics_batch(cred,chunk)
                measured = set()
                changes = []
                for item in response.get('data',[]):
                    external = str(item.get('id'))
                    if external not in by_external:
                        continue
                    measured.add(external)
                    if str(item.get('author_id'))!=str(cred.external_user_id):
                        changes.extend((pid,{'x_account_mismatch':True}) for pid in by_external[external])
                        unavailable += 1
                        continue
                    metrics = item.get('public_metrics') or {}
                    if type(metrics.get('impression_count')) is not int or metrics['impression_count']<0:
                        unavailable += 1
                        continue
                    changes.extend((pid,{**metrics,'x_metrics_sampled_at':stamp,'x_account_mismatch':False}) for pid in by_external[external])
                unavailable += len(set(chunk)-measured)
                # Read latest metrics only after HTTP; retain other metric fields.
                for pid, metrics in changes:
                    post = await db.get(Post,pid)
                    if post:
                        post.metrics = {**(post.metrics or {}),**metrics}
                        updated += 1
                await db.commit()
        except Exception as exc:
            await db.rollback()
            code = getattr(exc,'status',None)
            errors.append(channel_name+': Kennzahlen nicht verfügbar'+(f' (API {code})' if type(code) is int else ''))
    row = (await db.execute(select(AppSetting).where(AppSetting.key==COLLECTION_KEY).execution_options(populate_existing=True))).scalar_one()
    latest = dict(row.value or {})
    if latest.get('attempted_at') == stamp:
        latest.update(status='partial' if errors else 'ok', completed_at=datetime.now(timezone.utc).isoformat(),
                      updated=updated, unavailable=unavailable, errors=errors)
        if not errors:
            latest['last_success_at'] = latest['completed_at']
        row.value = latest
    await db.commit()
    return {'updated':updated,'unavailable':unavailable,'errors':errors}
