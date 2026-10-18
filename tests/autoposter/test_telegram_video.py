import subprocess
import pytest
from autoposter.services import media, assignment
from autoposter.models import Channel
from .test_telegram import no_network

@pytest.mark.asyncio
async def test_mp4_library_ingest_reuse_and_platform_scope(db,tmp_path):
    movie=tmp_path/'fixture.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=32x32:d=0.1','-c:v','libx264','-pix_fmt','yuv420p',str(movie)],check=True)
    asset=await media.ingest(db,filename='fixture.mp4',data=movie.read_bytes())
    assert asset.mime=='video/mp4' and asset.width==32 and asset.height==32
    assert media.read_bytes(asset)[4:8]==b'ftyp'
    assert media.thumb_path(asset).exists()
    assert assignment.check_compatibility(asset,Channel(platform='telegram',nsfw_level='sfw')) is None
    assert assignment.check_compatibility(asset,Channel(platform='x',nsfw_level='sfw'))
    assert assignment.check_compatibility(asset,Channel(platform='fanvue',nsfw_level='sfw'))
    from autoposter.services.appconfig import current
    from autoposter.services.llm import llm
    from unittest.mock import patch, AsyncMock
    describe=AsyncMock(return_value={'description':'Blue frame','alt_text':'Blue','tags':[]})
    with patch('autoposter.services.appconfig.current',return_value='synthetic'), patch.object(llm,'describe_image',describe):
        await media.ensure_descriptions(db,[asset])
    assert describe.await_args.args[2]=='image/jpeg'
    assert describe.await_args.args[1].startswith(b'\xff\xd8')
    assert asset.ai_description.startswith('Video-Standbild:')

@pytest.mark.asyncio
async def test_invalid_video_import_explains_supported_scope(db):
    with pytest.raises(ValueError,match='MP4|Video'):
        await media.ingest(db,filename='bad.mp4',data=b'not video')
