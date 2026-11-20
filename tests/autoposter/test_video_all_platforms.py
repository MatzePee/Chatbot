"""Video publishing regression tests; providers are simulated, never contacted."""
import io
import json
import uuid
from unittest.mock import AsyncMock
import httpx
import pytest
from PIL import Image
from autoposter.adapters import x, fanvue, telegram
from autoposter.adapters.base import Credentials, AdapterError, MediaRef
from autoposter.models import Channel, MediaAsset, MediaAssignment, MediaSet, MediaSetItem, Post, PostingPolicy
from autoposter.services import assignment, telegram_paid


@pytest.mark.parametrize('platform', ['x', 'fanvue', 'telegram'])
def test_mp4_supported_without_relaxing_image_limits(platform):
    channel=Channel(platform=platform)
    assert assignment.check_compatibility(MediaAsset(mime='video/mp4',bytes=50_000_000,status='ready'),channel) is None
    assert assignment.check_compatibility(MediaAsset(mime='video/mp4',bytes=50_000_001,status='ready'),channel)
    if platform=='x':
        assert assignment.check_compatibility(MediaAsset(mime='image/jpeg',bytes=6_000_000,status='ready'),channel)


@pytest.mark.asyncio
async def test_x_video_category_processing_and_mixed_post_rejection(monkeypatch):
    adapter=x.XAdapter();requests=[]
    def handle(request):
        requests.append(request);path=request.url.path
        if path.endswith('/initialize'):
            assert json.loads(request.content)['media_category']=='tweet_video'
            return httpx.Response(200,json={'data':{'id':'video-id'}})
        if path.endswith('/append'):return httpx.Response(204)
        if path.endswith('/finalize'):return httpx.Response(200,json={'data':{'processing_info':{'state':'pending','check_after_secs':1}}})
        if path.endswith('/upload'):
            assert request.url.params['command']=='STATUS'
            return httpx.Response(200,json={'data':{'processing_info':{'state':'succeeded'}}})
        if path.endswith('/tweets'):
            assert json.loads(request.content)['media']['media_ids']==['video-id']
            return httpx.Response(201,json={'data':{'id':'post-id'}})
        raise AssertionError(path)
    monkeypatch.setattr(x,'http_client',lambda **kwargs:httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(x.asyncio,'sleep',AsyncMock())
    asset=MediaAsset(id=uuid.uuid4(),filename='clip.mp4',mime='video/mp4',caption_hint='Caption',nsfw_level='sfw')
    ref=await adapter.upload_media(Credentials('synthetic'),asset,b'synthetic-video')
    assert ref.mime=='video/mp4'
    await adapter.publish(Credentials('synthetic'),Channel(platform='x',handle='sample'),Post(body_text='Clip'),[ref])
    count=len(requests)
    with pytest.raises(AdapterError,match='allein'):
        await adapter.publish(Credentials('synthetic'),Channel(platform='x'),Post(body_text='bad'),[ref,MediaRef('image','image-id',mime='image/jpeg')])
    assert len(requests)==count
    assert not any(r.url.path.endswith('/metadata') for r in requests)


@pytest.mark.asyncio
async def test_fanvue_video_waits_for_ready(monkeypatch):
    adapter=fanvue.FanvueAdapter();seen=[];states=iter(['processing','ready']);data=b'original-video';client=httpx.AsyncClient
    def handle(request):
        seen.append(request);path=request.url.path
        if path.endswith('/uploads'):
            assert json.loads(request.content)['mediaType']=='video'
            return httpx.Response(200,json={'uploadId':'session','mediaUuid':'video-id'})
        if path.endswith('/parts/1/url'):return httpx.Response(200,text='https://storage.example/video')
        if request.url.host=='storage.example':
            assert request.content==data
            return httpx.Response(200,headers={'ETag':'"one"'})
        if path.endswith('/uploads/session'):return httpx.Response(200,json={'mediaUuid':'video-id'})
        if path.endswith('/media/video-id'):return httpx.Response(200,json={'data': {'status':next(states)}})
        raise AssertionError(path)
    monkeypatch.setattr(fanvue,'http_client',lambda **kwargs:client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(fanvue.httpx,'AsyncClient',lambda **kwargs:client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(fanvue.asyncio,'sleep',AsyncMock())
    ref=await adapter.upload_media(Credentials('synthetic'),MediaAsset(id=uuid.uuid4(),mime='video/mp4',filename='clip.mp4'),data)
    assert ref.external_id=='video-id' and ref.mime=='video/mp4'
    assert len([r for r in seen if r.url.path.endswith('/media/video-id')])==2


@pytest.mark.asyncio
async def test_paid_telegram_mixed_video_and_photo_use_one_stars_price():
    class Adapter(telegram.TelegramAdapter):
        async def verify(self,*args):
            from autoposter.adapters.base import AccountInfo
            return AccountInfo(True,raw={'can_send_paid_media':True})
        async def _call(self,cred,method,payload,files=None):
            assert method=='sendPaidMedia' and payload['star_count']=='300'
            items=json.loads(payload['media'])
            assert [i['type'] for i in items]==['video','photo'] and items[0]['supports_streaming'] is True
            assert files['media0'][1]==b'original-video'
            return {'message_id':12,'chat':{'id':-100123},'paid_media':{'star_count':300,'paid_media':[{'type':'video'},{'type':'photo'}]}}
    image=io.BytesIO();Image.new('RGB',(16,16),'blue').save(image,'JPEG')
    post=Post(id=uuid.uuid4(),body_text='Set',media_asset_ids=['v','p'],price_stars=300,generation_meta={'telegram_paid':{'stars':300,'asset_ids':['v','p']}})
    media=[telegram.TelegramMediaRef('v','',mime='video/mp4',data=b'original-video',filename='clip.mp4'),telegram.TelegramMediaRef('p','',mime='image/jpeg',data=image.getvalue(),filename='photo.jpg')]
    cred=Credentials('synthetic',external_user_id='42',raw_meta={'chat_id':'-100123','destination':'@sample_channel'})
    result=await Adapter().publish(cred,Channel(platform='telegram',telegram_mode='paid',handle='@sample_channel'),post,media)
    assert result.external_post_id=='12'


@pytest.mark.asyncio
async def test_paid_video_and_mixed_set_prices_are_independent(db):
    channel=Channel(platform='telegram',telegram_mode='paid',display_name='Pay',policy=PostingPolicy())
    assets=[MediaAsset(filename='clip.mp4',storage_path='clip',mime='video/mp4',status='ready',bytes=20,sha256=uuid.uuid4().hex*2,telegram_price_stars=100),MediaAsset(filename='photo.jpg',storage_path='photo',mime='image/jpeg',status='ready',bytes=20,sha256=uuid.uuid4().hex*2)]
    db.add_all([channel,*assets]);await db.flush()
    group=MediaSet(name='Video and photo',telegram_price_stars=450);db.add(group);await db.flush()
    db.add_all([MediaAssignment(channel_id=channel.id,media_asset_id=assets[0].id),MediaAssignment(channel_id=channel.id,media_set_id=group.id)])
    db.add_all([MediaSetItem(media_set_id=group.id,media_asset_id=a.id,position=i) for i,a in enumerate(assets)]);await db.flush()
    single=await telegram_paid.spec(db,channel,[assets[0].id]);bundle=await telegram_paid.spec(db,channel,[a.id for a in assets],group.id)
    assert single['kind']=='video' and single['stars']==100
    assert bundle['stars']==450 and bundle['asset_ids']==[str(a.id) for a in assets]
    assets[0].bytes=50_000_001;await db.flush()
    with pytest.raises(ValueError,match='50 MB'):await telegram_paid.spec(db,channel,[assets[0].id])
