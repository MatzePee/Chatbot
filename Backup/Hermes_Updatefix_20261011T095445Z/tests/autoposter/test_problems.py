from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from autoposter.models import AppSetting, MediaAsset, MediaAssignment, Notification, Post
from autoposter.services import problems
from tests.autoposter.test_duplicates import make_channel


async def notes(db):
    return list((await db.execute(select(Notification).where(Notification.is_read.is_(False)))).scalars().all())


@pytest.mark.asyncio
async def test_inventory_updates_deduplicates_and_resolves(db):
    channel = await make_channel(db, 'Inventory check')
    for number in (9, 19, 10):
        db.add(Notification(title=f'Bildvorrat knapp: {channel.display_name}', body=f'Noch {number} Bilder',
                            entity='channel', entity_id=str(channel.id), level='warning'))
    await db.flush()
    result = await problems.check(db)
    active = await notes(db)
    assert len(active) == 1
    assert 'Noch 0 Bilder' in active[0].body
    assert result['resolved'] == 2
    assert not (await problems.check(db))['created']

    assets = [MediaAsset(filename=f'{i}.jpg', storage_path=f'{i}.jpg', sha256=f'{i:064x}', status='ready') for i in range(40)]
    db.add_all(assets)
    await db.flush()
    db.add_all([MediaAssignment(media_asset_id=a.id, channel_id=channel.id) for a in assets])
    await db.flush()
    assert (await problems.check(db))['resolved'] == 1
    assert not await notes(db)
    assert len((await db.execute(select(Notification))).scalars().all()) == 3  # history preserved


@pytest.mark.asyncio
async def test_new_warning_and_deleted_channel(db):
    channel = await make_channel(db, 'New low channel')
    assert (await problems.check(db))['created'] == 1
    channel.is_active = False
    await db.flush()
    await problems.check(db)
    assert not await notes(db)


@pytest.mark.asyncio
async def test_post_failures_only_disappear_after_resolution(db):
    channel = await make_channel(db, 'Posts')
    failed = Post(channel_id=channel.id, status='failed', error_message='Still broken')
    published = Post(channel_id=channel.id, status='published')
    db.add_all([failed, published]); await db.flush()
    for post in (failed, published):
        db.add(Notification(title='Veröffentlichung fehlgeschlagen: Posts', entity='post', entity_id=str(post.id)))
    db.add(Notification(title='Unknown condition', entity='system'))
    await db.flush()
    await problems.check(db)
    active = await notes(db)
    assert any(n.entity_id == str(failed.id) and n.body == 'Still broken' for n in active)
    assert not any(n.entity_id == str(published.id) for n in active)
    assert any(n.title == 'Unknown condition' for n in active)


@pytest.mark.asyncio
async def test_token_and_calendar_state(db, monkeypatch):
    channel = await make_channel(db, 'Token and calendar')
    channel.health = 'ok'
    monkeypatch.setattr(problems.credentials, 'is_connected', lambda c: True)
    async def expiry(*args): return datetime.now(timezone.utc) + timedelta(hours=2)
    async def plan(*args, **kwargs):
        assert kwargs['dry_run'] is True and kwargs['generate_text'] is False
        return SimpleNamespace(gaps=[])
    monkeypatch.setattr(problems.credentials, 'token_expiry', expiry)
    monkeypatch.setattr(problems.planner, 'fill_calendar', plan)
    for prefix in ('Token-Problem:', 'Neu-Autorisierung nötig:', 'Kalenderlücke:'):
        db.add(Notification(title=prefix + ' Test', entity='channel', entity_id=str(channel.id)))
    await db.flush()
    await problems.check(db)
    assert not any(n.title.startswith(('Token-Problem:', 'Neu-Autorisierung nötig:', 'Kalenderlücke:')) for n in await notes(db))


@pytest.mark.asyncio
async def test_hourly_check_is_persistent(db, monkeypatch):
    await problems.check(db)
    assert await problems.check_if_due(db) is None
    stamp = await db.get(AppSetting, 'problems_check')
    stamp.value = {'checked_at': (datetime.now(timezone.utc) - timedelta(minutes=61)).isoformat()}
    await db.flush()
    assert await problems.check_if_due(db) is not None
    assert await problems.check_if_due(db) is None


@pytest.mark.asyncio
async def test_manual_check_endpoint(db):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from autoposter.api.dashboard import router
    from autoposter.db import get_db
    from autoposter.deps import require_editor
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_editor] = lambda: None
    await make_channel(db, 'Manual')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/problems/check')
    assert response.status_code == 200
    assert response.json()['created'] == 1
    assert await db.get(AppSetting, 'problems_check') is not None

@pytest.mark.asyncio
async def test_recovered_preflight_is_not_reported_as_current_connection_error(db):
    from autoposter.api.dashboard import dashboard
    channel = await make_channel(db, 'Recovered X')
    channel.health = 'ok'
    post = Post(channel_id=channel.id, type='text_only', body_text='A valid post', status='failed', error_message='Kanalstatus: needs_reauth')
    db.add(post); await db.flush()
    note = Notification(title='Preflight fehlgeschlagen: Recovered X', body=post.error_message, entity='post', entity_id=str(post.id), level='error')
    db.add(note); await db.flush()
    await problems.recheck_channel_failures(db, channel.id)
    assert note.is_read is True
    data = await dashboard(db=db, _=None)
    failure = next(item for item in data.recent_failures if item.id == post.id)
    assert failure.retry_ready is True
    assert 'needs_reauth' not in failure.error_message
    assert not any('Preflight fehlgeschlagen' in item['title'] for item in data.notifications)
    assert post.status == 'failed' and post.error_message == 'Kanalstatus: needs_reauth'  # no automatic resend or history rewriting


@pytest.mark.asyncio
async def test_preflight_check_keeps_current_blockers(db):
    channel = await make_channel(db, 'Still blocked')
    channel.health = 'needs_reauth'
    post = Post(channel_id=channel.id, type='text_only', body_text='A valid post', status='failed', error_message='Kanalstatus: needs_reauth')
    db.add(post); await db.flush()
    note = Notification(title='Preflight fehlgeschlagen: Still blocked', entity='post', entity_id=str(post.id), body=post.error_message)
    db.add(note); await db.flush()
    await problems.check(db)
    assert note.is_read is False
    assert 'needs_reauth' in note.body
    channel.health = 'ok'
    post.body_text = ''
    post.generation_meta = {'preflight_failure': True}
    await db.flush()
    await problems.check(db)
    assert note.is_read is False
    assert 'needs_reauth' not in note.body  # new validation error replaces obsolete channel error


@pytest.mark.asyncio
async def test_later_publication_resolves_old_validation_failure_only_on_same_channel(db):
    from autoposter.api.dashboard import dashboard
    from tests.autoposter.test_media_reuse import setup_used
    a, b, asset = await setup_used(db)
    now = datetime.now(timezone.utc)
    failed = Post(channel_id=a.id, status='failed', body_text='Old attempt',
                  media_asset_ids=[str(asset.id)], last_attempt_at=now - timedelta(days=5),
                  error_message='Kanalstatus: needs_reauth')
    db.add(failed); await db.flush()
    note = Notification(title='Preflight fehlgeschlagen: Test', body=failed.error_message,
                        entity='post', entity_id=str(failed.id))
    db.add(note)
    later = Post(channel_id=b.id, status='published', published_at=now,
                 media_asset_ids=[str(asset.id)])
    db.add(later); await db.flush()
    assert not await problems.failure_superseded(db, failed)
    later.channel_id = a.id
    later.published_at = now - timedelta(days=6)
    await db.flush()
    assert not await problems.failure_superseded(db, failed)
    later.published_at = now
    await db.flush()
    assert await problems.failure_superseded(db, failed)
    await problems.check(db)
    assert note.is_read
    data = await dashboard(db=db, _=None)
    assert not any(item.id == failed.id for item in data.recent_failures)
    assert failed.status == 'failed' and failed.error_message == 'Kanalstatus: needs_reauth'
    # A new intentional attempt after the publication still needs permission.
    failed.last_attempt_at = now + timedelta(seconds=1)
    await db.flush()
    assert not await problems.failure_superseded(db, failed)
    assert not (await problems.current_failure(db, failed))[1]


@pytest.mark.asyncio
async def test_reuse_release_rechecks_legacy_duplicate_warning(db):
    from autoposter.api.assignments import reuse_asset
    from tests.autoposter.test_media_reuse import setup_used
    a, b, asset = await setup_used(db)
    failed = Post(channel_id=a.id, status='failed', body_text='Another post',
                  media_asset_ids=[str(asset.id)], error_message=asset.filename + ' lief bereits auf diesem Kanal')
    db.add(failed); await db.flush()
    note = Notification(title='Preflight fehlgeschlagen: Test', entity='post',
                        entity_id=str(failed.id), body=failed.error_message)
    db.add(note); await db.flush()
    assert not (await problems.current_failure(db, failed))[1]
    await reuse_asset(a.id, asset.id, db=db, user=SimpleNamespace(id=None))
    assert note.is_read
    assert (await problems.current_failure(db, failed))[1]
