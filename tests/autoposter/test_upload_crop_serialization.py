import uuid
import pytest
from sqlalchemy import inspect
from autoposter.schemas import MediaOut
from autoposter.services.media import PreparedImage, store_prepared
from autoposter.models import MediaAsset, MediaImageEdit


@pytest.mark.asyncio
async def test_new_upload_serializes_crop_metadata_before_commit_and_after_detach(db):
    prepared = PreparedImage(asset_id=uuid.uuid4(), filename='portrait.png', storage_path='test/portrait.png',
        sha256='f'*64, phash='0'*16, width=600, height=1000, bytes=100, mime='image/png')
    asset = await store_prepared(db, prepared)
    assert 'image_edits' not in inspect(asset).unloaded
    output = MediaOut.model_validate(asset)
    assert output.image_crops['variants'] == {}
    assert output.image_crops['instagram']['valid'] is False
    await db.commit()
    db.expunge(asset)
    assert MediaOut.model_validate(asset).image_crops == output.image_crops


@pytest.mark.asyncio
async def test_existing_upload_keeps_saved_crop_when_loaded(db):
    asset = MediaAsset(filename='portrait.png', storage_path='test/portrait.png',sha256='e'*64,
        width=600,height=1000,bytes=100,mime='image/png',image_edits=[])
    db.add(asset);await db.flush()
    db.add(MediaImageEdit(media_asset_id=asset.id,scope='instagram',storage_path='test/crop.png',width=600,height=750,settings={}))
    await db.commit();identity=asset.id;db.expunge_all()
    loaded=await db.get(MediaAsset,identity)
    output=MediaOut.model_validate(loaded)
    assert output.image_crops['instagram']['valid'] is True
    assert output.image_crops['variants']['instagram']['height']==750
