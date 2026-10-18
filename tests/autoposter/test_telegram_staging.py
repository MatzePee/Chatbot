import uuid
from types import SimpleNamespace
import pytest
from autoposter.adapters import AdapterError, get_adapter
from autoposter.models import Channel, MediaAsset
from autoposter.services import publisher, media, watermark
from .test_telegram import no_network
from .test_telegram_publish import credential

@pytest.mark.asyncio
async def test_oversized_album_is_rejected_before_loading_files(db,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Oversized album must not be loaded')
    monkeypatch.setattr(media,'read_bytes',forbidden)
    assets=[MediaAsset(id=uuid.uuid4(),mime='video/mp4',bytes=49_000_000) for _ in range(3)]
    with pytest.raises(AdapterError,match='100 MB'):
        await publisher._upload_all(db,get_adapter('telegram'),credential(),Channel(platform='telegram'),assets)

@pytest.mark.asyncio
async def test_video_does_not_enter_photo_watermark_processor(db,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Video must not enter PIL watermark path')
    monkeypatch.setattr(watermark,'apply_bytes',forbidden)
    monkeypatch.setattr(media,'read_bytes',lambda asset:b'synthetic-video')
    asset=MediaAsset(id=uuid.uuid4(),mime='video/mp4',bytes=15)
    refs=await publisher._upload_all(db,get_adapter('telegram'),credential(),Channel(platform='telegram'),[asset])
    assert refs[0].data==b'synthetic-video'
