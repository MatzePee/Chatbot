import pytest
from fastapi import HTTPException
from sqlalchemy import text
from autoposter.api.media import delete_asset
from autoposter.models import MediaAsset, MediaAssignment, Post
from autoposter.schemas import AssignmentBatch
from autoposter.services import assignment, media, lifecycle, reuse
from tests.autoposter.test_duplicates import make_channel, make_image


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['draft', 'needs_review', 'approved', 'scheduled', 'publishing', 'failed'])
async def test_deleting_image_in_open_post_is_blocked(db, status):
    channel = await make_channel(db, 'Delete guard')
    asset = await media.ingest(db, filename='protected.jpg', data=make_image(61))
    post = Post(channel_id=channel.id, status=status, media_asset_ids=[str(asset.id)])
    db.add(post); await db.flush()
    with pytest.raises(HTTPException) as error:
        await delete_asset(asset.id, db=db, _=None)
    assert error.value.status_code == 409
    assert await db.get(MediaAsset, asset.id) is asset
    assert post.media_asset_ids == [str(asset.id)]


@pytest.mark.asyncio
async def test_deletion_removes_assignment_and_reuse_preserves_published_post(db):
    from sqlalchemy import select
    await db.execute(text('PRAGMA foreign_keys=ON'))
    channel = await make_channel(db, 'Delete completed')
    asset = await media.ingest(db, filename='deleted.jpg', data=make_image(62))
    await assignment.assign(db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[channel.id]))
    published = Post(channel_id=channel.id, status='published', media_asset_ids=[str(asset.id)])
    db.add(published); await db.flush()
    await lifecycle.record_usage(db, asset_ids=[asset.id], channel_id=channel.id, post_id=published.id)
    await reuse.release(db, asset.id, channel.id)
    await delete_asset(asset.id, db=db, _=None)
    assert await db.get(MediaAsset, asset.id) is None
    assert not (await db.execute(select(MediaAssignment))).scalars().all()
    assert not await reuse.allowed(db, channel.id)
    assert await db.get(Post, published.id) is published
    assert published.status == 'published'