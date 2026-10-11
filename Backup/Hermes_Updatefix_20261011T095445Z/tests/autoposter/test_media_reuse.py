from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from autoposter.api.assignments import reuse_asset
from autoposter.models import MediaChannelUsage, Post
from autoposter.schemas import AssignmentBatch
from autoposter.services import assignment, lifecycle, media, planner, reuse, inventory
from tests.autoposter.test_duplicates import make_channel, make_image


async def setup_used(db):
    a = await make_channel(db, 'Reuse A')
    b = await make_channel(db, 'Reuse B')
    asset = await media.ingest(db, filename='reuse.jpg', data=make_image(17))
    await assignment.assign(db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[a.id, b.id]))
    for channel in (a, b):
        await lifecycle.record_usage(db, asset_ids=[asset.id], channel_id=channel.id, post_id=None)
    return a, b, asset


@pytest.mark.asyncio
async def test_release_allows_one_further_use_on_one_channel_preserving_history(db):
    a, b, asset = await setup_used(db)
    assert not await assignment.channel_pool(db, a.id)
    result = await reuse_asset(a.id, asset.id, db=db, user=SimpleNamespace(id=None))
    assert result.reusable_channel_ids == [str(a.id)]
    assert result.usage_count == 2
    assert set(result.used_channel_ids) == {str(a.id), str(b.id)}
    await lifecycle.refresh_asset(db, asset)  # maintenance must not undo the permission
    assert [item.id for item in await assignment.channel_pool(db, a.id)] == [asset.id]
    assert not await assignment.channel_pool(db, b.id)
    picked, reason = await planner.pick_assets(db, a, a.policy, count=1)
    assert not reason and [item.id for item in picked] == [asset.id]  # bypass intentional reuse similarity block
    assert (await inventory.channel_inventory(db, a)).available == 1
    assert (await inventory.channel_inventory(db, b)).available == 0

    post = Post(channel_id=a.id, media_asset_ids=[str(asset.id)], body_text='A new post')
    db.add(post); await db.flush()
    assert not any(issue['code'] == 'already_used_here' for issue in await planner.preflight(db, post))
    await lifecycle.record_usage(db, asset_ids=[asset.id], channel_id=a.id, post_id=post.id)
    assert asset.usage_count == 3
    assert (await db.execute(select(func.count(MediaChannelUsage.id)))).scalar_one() == 3
    assert not await reuse.allowed(db, a.id)
    assert not await assignment.channel_pool(db, a.id)
    assert any(issue['code'] == 'already_used_here' for issue in await planner.preflight(db, post))


@pytest.mark.asyncio
async def test_release_does_not_allow_duplicate_active_plans(db):
    a, b, asset = await setup_used(db)
    post = Post(channel_id=a.id, status='scheduled', media_asset_ids=[str(asset.id)])
    db.add(post); await db.flush()
    with pytest.raises(HTTPException) as error:
        await reuse_asset(a.id, asset.id, db=db, user=SimpleNamespace(id=None))
    assert error.value.status_code == 409
    assert not await reuse.allowed(db, a.id)


@pytest.mark.asyncio
async def test_released_asset_is_not_selected_twice_while_planned(db):
    a, b, asset = await setup_used(db)
    await reuse_asset(a.id, asset.id, db=db, user=SimpleNamespace(id=None))
    post = Post(channel_id=a.id, status='scheduled', media_asset_ids=[str(asset.id)])
    db.add(post); await db.flush(); await lifecycle.refresh_asset(db, asset)
    picked, _ = await planner.pick_assets(db, a, a.policy, count=1)
    assert not picked
    assert str(asset.id) in await reuse.allowed(db, a.id)  # retained until success, so retries work


@pytest.mark.asyncio
async def test_reuse_api_and_matrix_status(db):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from autoposter.api.assignments import router as assignment_router
    from autoposter.api.media import router as media_router
    from autoposter.db import get_db
    from autoposter.deps import current_user, require_editor
    a, b, asset = await setup_used(db)
    app = FastAPI(); app.include_router(assignment_router); app.include_router(media_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: None
    app.dependency_overrides[require_editor] = lambda: SimpleNamespace(id=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(f'/assignments/channel/{a.id}/assets/{asset.id}/reuse')
        assert response.status_code == 200
        matrix = (await client.get('/media/matrix')).json()
        cells = matrix['rows'][0]['cells']
        assert cells[str(a.id)] == 'assigned' and cells[str(b.id)] == 'published'
        assert matrix['rows'][0]['asset']['usage_count'] == 2
        board = (await client.get('/assignments/board')).json()
        column = next(c for c in board['columns'] if c['channel']['id'] == str(a.id))
        assert column['assets'][0]['reusable_channel_ids'] == [str(a.id)]
        assert (await client.post(f'/assignments/channel/{a.id}/assets/{asset.id}/reuse')).status_code == 200
        assert len(await reuse.allowed(db, a.id)) == 1  # repeated click is idempotent

@pytest.mark.asyncio
async def test_removing_released_assignment_revokes_unused_permission(db):
    from autoposter.schemas import AssignmentRemove
    a, b, asset = await setup_used(db)
    await reuse_asset(a.id, asset.id, db=db, user=SimpleNamespace(id=None))
    await assignment.unassign(db, AssignmentRemove(asset_ids=[asset.id], channel_ids=[a.id]))
    assert not await reuse.allowed(db, a.id)
    assert asset.usage_count == 2
    assert set(asset.used_channel_ids) == {str(a.id), str(b.id)}


@pytest.mark.asyncio
async def test_release_survives_scheduling_and_publisher_consumes_only_after_success(db, monkeypatch):
    from autoposter.services import publisher
    a, b, asset = await setup_used(db)
    await reuse_asset(a.id, asset.id, db=db, user=SimpleNamespace(id=None))
    post = Post(channel_id=a.id, status='scheduled', body_text='A repeated image',
                media_asset_ids=[str(asset.id)])
    db.add(post); await db.flush()
    await lifecycle.refresh_asset(db, asset)
    monkeypatch.setattr(publisher.settings, 'dry_run', True)
    ok, message = await publisher.publish_post(db, post.id)
    assert ok, message
    assert post.status == 'published' and asset.usage_count == 3
    assert not await reuse.allowed(db, a.id)
    assert not await reuse.allowed(db, b.id)
