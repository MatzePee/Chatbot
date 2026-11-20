"""Celery payloads carry immutable owners; Beat only dispatches control-plane ticks."""
from __future__ import annotations
import os
import uuid
from celery import shared_task
from autoposter import jobs
from autoposter.db import run_async
from autoposter.workspaces import CharacterBoundaryError


def _run(name, function, *args, character_id=None, **kwargs):
    if os.environ.get('MP_CHARACTERS_ENABLED') == '1':
        if character_id is None:
            raise CharacterBoundaryError('Explicit character ID required in Celery payload')
        owner = uuid.UUID(str(character_id))
        from autoposter.character_dispatch import invoke
        return run_async(invoke(owner, name, function, *args, **kwargs))
    return run_async(function(*args, **kwargs))


@shared_task(name='autoposter.fanout')
def fanout(job_name):
    names = {'dispatch_due_posts','generate_plan','describe_assets','refresh_metrics',
             'delete_old_fanvue_posts','age_fanvue_visibility','collect_daily_followers',
             'inventory_check','nightly_maintenance','token_health'}
    if job_name not in names:
        raise ValueError('Unknown scheduled job')
    from autoposter.character_dispatch import active_ids
    owners = run_async(active_ids())
    task = globals()[job_name]
    for owner in owners:
        task.delay(character_id=str(owner))
    return {'characters':len(owners)}


@shared_task(name='autoposter.dispatch_due_posts')
def dispatch_due_posts(*, character_id=None):
    async def dispatch():
        result = await jobs.dispatch_due_posts(inline=False)
        for post_id in result.get('post_ids', []):
            if os.environ.get('MP_CHARACTERS_ENABLED') == '1':
                publish_post.delay(post_id, character_id=str(character_id))
            else:
                publish_post.delay(post_id)
        return {'dispatched':result.get('dispatched', 0)}
    return _run('dispatch_due_posts', dispatch, character_id=character_id)


@shared_task(name='autoposter.publish_post', bind=True, max_retries=5, acks_late=True)
def publish_post(self, post_id, *, character_id=None):
    return _run('publish_post:' + str(uuid.UUID(post_id)), jobs.publish_one,
                uuid.UUID(post_id), character_id=character_id)


@shared_task(name='autoposter.retry_failed')
def retry_failed(*, character_id=None):
    return _run('retry_failed', jobs.retry_failed, character_id=character_id)


@shared_task(name='autoposter.generate_plan')
def generate_plan(days=14, *, character_id=None):
    return _run('generate_plan', jobs.generate_plan, days, character_id=character_id)


@shared_task(name='autoposter.describe_assets')
def describe_assets(limit=20, *, character_id=None):
    return _run('describe_assets', jobs.describe_assets, limit, character_id=character_id)


@shared_task(name='autoposter.refresh_metrics')
def refresh_metrics(hours=72, *, character_id=None):
    return _run('refresh_metrics', jobs.refresh_metrics, hours, character_id=character_id)


@shared_task(name='autoposter.delete_old_fanvue_posts')
def delete_old_fanvue_posts(*, character_id=None):
    return _run('delete_old_fanvue_posts', jobs.delete_old_fanvue_posts, character_id=character_id)


@shared_task(name='autoposter.age_fanvue_visibility')
def age_fanvue_visibility(*, character_id=None):
    return _run('age_fanvue_visibility', jobs.age_fanvue_visibility, character_id=character_id)


@shared_task(name='autoposter.collect_daily_followers')
def collect_daily_followers(*, character_id=None):
    return _run('collect_daily_followers', jobs.collect_daily_followers, character_id=character_id)


@shared_task(name='autoposter.inventory_check')
def inventory_check(*, character_id=None):
    return _run('inventory_check', jobs.inventory_check, character_id=character_id)


@shared_task(name='autoposter.nightly_maintenance')
def nightly_maintenance(*, character_id=None):
    return _run('nightly_maintenance', jobs.nightly_maintenance, character_id=character_id)


@shared_task(name='autoposter.token_health')
def token_health(*, character_id=None):
    return _run('token_health', jobs.token_health, character_id=character_id)


@shared_task(name='autoposter.collect_x_post_metrics')
def collect_x_post_metrics(*, character_id=None):
    return _run('collect_x_post_metrics', jobs.collect_x_post_metrics, character_id=character_id)
