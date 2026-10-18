import logging
import uuid
from datetime import datetime, timezone, timedelta
import httpx
import pytest
from sqlalchemy import select
from autoposter.adapters import get_adapter, AdapterError
from autoposter.adapters.base import Credentials
from autoposter.config import settings
from autoposter.models import Channel, PostingPolicy, Post, ChannelCredential, ExternalMediaRef
from autoposter.services import publisher, planner, credentials, media
from .test_telegram import no_network
from .test_telegram_publish import image_bytes

async def setup(db, with_media=False):
    row=ChannelCredential(platform='telegram');credentials.store_credentials(row,Credentials('123:synthetic',external_user_id='123',raw_meta={'destination':'@sample_channel','chat_id':'-100456','username':'sample_channel'}))
    policy=PostingPolicy(auto_retry_on_fail=True)
    db.add_all([row,policy]);await db.flush()
    channel=Channel(platform='telegram',handle='@sample_channel',display_name='Independent',credential_id=row.id,health='ok',is_active=True,last_verified_at=datetime.now(timezone.utc),policy_id=policy.id)
    db.add(channel);await db.flush();await db.refresh(channel)
    assets=[]
    if with_media:assets=[await media.ingest(db,filename='synthetic.jpg',data=image_bytes())]
    post=Post(channel_id=channel.id,type='image_single' if assets else 'text_only',status='scheduled',scheduled_at=datetime.now(timezone.utc)-timedelta(seconds=1),body_text='Independent Telegram',media_asset_ids=[str(a.id) for a in assets])
    db.add(post);await db.flush()
    return channel,post,assets

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['success','timeout','partial'])
async def test_real_pipeline_stages_locally_claims_and_never_blind_retries(db,monkeypatch,mode):
    monkeypatch.setattr(settings,'dry_run',False)
    channel,post,assets=await setup(db,True)
    called=[]
    def handler(req):
        called.append(req)
        assert req.url.path.endswith('/sendPhoto')
        if mode=='timeout':raise httpx.ReadTimeout(str(req.url),request=req)
        return httpx.Response(200,json={'ok':True,'result':{'message_id':10,'chat':{'id':-100456 if mode=='success' else -100999}}})
    monkeypatch.setattr('autoposter.adapters.telegram.http_client',lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    ok,message=await publisher.publish_post(db,post.id)
    assert ok==(mode=='success')
    assert len(called)==1
    assert not list((await db.execute(select(ExternalMediaRef))).scalars())
    if mode=='success':
        assert post.external_post_id=='10' and post.generation_meta['telegram_delivery']['message_ids']==[10]
        assert assets[0].usage_count==1
    else:
        assert post.generation_meta['telegram_delivery']['state']=='uncertain'
        assert await publisher.retry_failed(db)==0
        ok,_=await publisher.publish_post(db,post.id,force=True)
        assert not ok
    await publisher.publish_post(db,post.id)
    assert len(called)==1

@pytest.mark.asyncio
async def test_preflight_caption_album_missing_assets_and_no_thread_split(db):
    channel,post,assets=await setup(db,True)
    post.body_text='x'*1025
    assert any(i['code']=='too_long' for i in await planner.preflight(db,post))
    from autoposter.models import MediaAsset
    extras=[MediaAsset(filename=f'synthetic{i}.jpg',storage_path='unused',sha256=uuid.uuid4().hex*2,mime='image/jpeg',bytes=10,width=16,height=16,status='ready') for i in range(10)]
    db.add_all(extras);await db.flush()
    original=[str(a.id) for a in assets+extras]
    post.media_asset_ids=original
    issues=await planner.preflight(db,post)
    assert any(i['code']=='telegram_album' and i['level']=='error' for i in issues)
    assert not any(i['code']=='thread_split' for i in issues)
    assert await publisher.split_thread_if_needed(db,post,channel)==[post]
    assert post.media_asset_ids==original
    post.media_asset_ids=[str(uuid.uuid4())]
    assert any(i['code']=='telegram_missing_media' for i in await planner.preflight(db,post))

@pytest.mark.asyncio
async def test_http_logs_never_contain_token(monkeypatch,caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr('autoposter.adapters.telegram.http_client',lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'ok':False,'error_code':401}))))
    with pytest.raises(AdapterError):
        await get_adapter('telegram')._call(Credentials('123:synthetic'),'getMe',{})
    assert '123:synthetic' not in caplog.text
