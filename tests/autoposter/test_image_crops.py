import io
import uuid

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select

from autoposter.models import MediaAsset, MediaImageEdit
from autoposter.schemas import ImageCropSave
from autoposter.services import image_edits, media


def fixture_image(size=(600,1000),fmt='JPEG'):
    output=io.BytesIO();Image.new('RGB',size,'navy').save(output,fmt);return output.getvalue()


@pytest.mark.parametrize('size,valid',[((600,1000),False),((600,800),True),((800,1000),True),((800,800),True),((1910,1000),True),((2000,1000),False)])
def test_instagram_format_range(size,valid):
    assert image_edits.instagram_ok(*size)==valid


def test_crop_is_explicit_and_bounds_checked():
    original=fixture_image()
    payload=ImageCropSave(x=0,y=.125,width=1,height=.75)
    result,w,h=image_edits.render(original,payload.model_dump(),'image/jpeg')
    assert (w,h)==(600,750)
    with Image.open(io.BytesIO(original)) as image:assert image.size==(600,1000)
    with pytest.raises(HTTPException):
        image_edits.render(original,ImageCropSave(x=.9,width=.5).model_dump(),'image/jpeg')


@pytest.mark.asyncio
async def test_variant_scopes_preserve_original_and_reset(db,tmp_path,monkeypatch):
    monkeypatch.setattr(media,'media_root',lambda:tmp_path)
    monkeypatch.setattr(media,'_assert_asset_owner',lambda asset:None)
    original=fixture_image();path=tmp_path/'original.jpg';path.write_bytes(original)
    asset=MediaAsset(filename='original.jpg',storage_path='original.jpg',sha256='c'*64,mime='image/jpeg',width=600,height=1000,bytes=len(original))
    db.add(asset);await db.flush();await db.refresh(asset,['image_edits'])
    await image_edits.save(db,asset,ImageCropSave(scope='instagram',y=.125,height=.75,expected_version=''))
    with Image.open(io.BytesIO(media.read_bytes(asset,platform='instagram'))) as image:assert image.size==(600,750)
    assert media.read_bytes(asset)==original and path.read_bytes()==original
    await image_edits.save(db,asset,ImageCropSave(scope='all',y=.2,height=.6,expected_version=''))
    assert set(image_edits.variants(asset))=={'all'}
    assert media.read_bytes(asset)==media.read_bytes(asset,platform='instagram')
    await image_edits.save(db,asset,ImageCropSave(scope='all',reset=True))
    assert not image_edits.variants(asset) and media.read_bytes(asset)==original


@pytest.mark.asyncio
async def test_stale_edit_does_not_overwrite_a_new_crop(db,tmp_path,monkeypatch):
    monkeypatch.setattr(media,'media_root',lambda:tmp_path)
    monkeypatch.setattr(media,'_assert_asset_owner',lambda asset:None)
    original=fixture_image();(tmp_path/'original.jpg').write_bytes(original)
    asset=MediaAsset(filename='original.jpg',storage_path='original.jpg',sha256='d'*64,mime='image/jpeg',width=600,height=1000,bytes=len(original))
    db.add(asset);await db.flush();await db.refresh(asset,['image_edits'])
    await image_edits.save(db,asset,ImageCropSave(scope='instagram',height=.8,expected_version=''))
    previous=image_edits.selected(asset,'instagram').id
    with pytest.raises(HTTPException) as caught:
        await image_edits.save(db,asset,ImageCropSave(scope='instagram',height=.8,expected_version=''))
    assert caught.value.status_code==409
    assert image_edits.selected(asset,'instagram').id==previous


def test_rotation_mirror_and_straightening_preserve_file_type():
    for mime,fmt in [('image/jpeg','JPEG'),('image/png','PNG'),('image/webp','WEBP')]:
        result,w,h=image_edits.render(fixture_image(fmt=fmt),ImageCropSave(rotation=90,flip_x=True,straighten=2,width=.8,height=.8).model_dump(),mime)
        with Image.open(io.BytesIO(result)) as image:
            assert image.format==fmt and image.size==(w,h) and w>h
