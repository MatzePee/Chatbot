from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import respx
from fastapi import HTTPException
from sqlalchemy import select

from autoposter.adapters.base import AccountInfo, Credentials
from autoposter.adapters.x import XAdapter
from autoposter.models import AppSetting, ChannelCredential, Post
from autoposter.services import top_posts, x_post_metrics, lifecycle, media, reuse, credentials
from tests.autoposter.test_duplicates import make_channel, make_image

NOW = datetime(2026,11,1,12,tzinfo=timezone.utc)


@pytest.mark.parametrize('days,band',[(0,'red'),(14,'red'),(15,'yellow'),(30,'yellow'),(31,'green'),(500,'green')])
def test_calendar_age_boundaries(days,band):
    assert top_posts.age_info(NOW-timedelta(days=days),NOW)==(days,band)


def test_berlin_calendar_boundaries_and_dst_schedule():
    # 23:30 UTC is already the next Berlin calendar day.
    then=datetime(2026,10,17,23,30,tzinfo=timezone.utc)
    assert top_posts.age_info(then,NOW)==(14,'red')
    assert not x_post_metrics.due(datetime(2026,11,1,23,59,tzinfo=timezone.utc),{})
    assert x_post_metrics.due(datetime(2026,11,2,0,0,tzinfo=timezone.utc),{})
    assert x_post_metrics.due(datetime(2026,7,1,23,0,tzinfo=timezone.utc),{})
    assert not x_post_metrics.due(datetime(2026,11,2,0,0,tzinfo=timezone.utc),{'daily_slot':'2026-11-02'})


@pytest.mark.asyncio
async def test_ranking_all_x_channels_lifetime_views_not_likes_top50(db):
    a=await make_channel(db,'One'); b=await make_channel(db,'Two'); other=await make_channel(db,'Fanvue','fanvue')
    for i in range(55):
        db.add(Post(channel_id=(a if i%2 else b).id,status='published',external_post_id=str(100+i),
                    published_at=NOW-timedelta(days=100+i),metrics={'impression_count':i,'like_count':1000-i}))
    db.add_all([Post(channel_id=other.id,status='published',external_post_id='999',published_at=NOW,metrics={'impression_count':999999}),
                Post(channel_id=a.id,status='draft',external_post_id='998',published_at=NOW,metrics={'impression_count':999999}),
                Post(channel_id=a.id,status='published',external_post_id='dryrun-1',published_at=NOW,metrics={'impression_count':999999}),
                Post(channel_id=a.id,status='published',external_post_id='997',published_at=NOW,metrics={'like_count':999999})])
    await db.flush()
    result=await top_posts.overview(db,NOW)
    assert len(result['items'])==50
    assert [item['views'] for item in result['items']]==list(range(54,4,-1))
    assert {item['channel_id'] for item in result['items']}=={str(a.id),str(b.id)}
    assert result['missing_views']==1
    assert all(item['age_band']=='green' for item in result['items'])


async def published_image(db,channel,index=1):
    asset=await media.ingest(db,filename=f'top-{index}.jpg',data=make_image(index+70))
    post=Post(channel_id=channel.id,status='published',external_post_id=str(100+index),
              published_at=NOW-timedelta(days=40),media_asset_ids=[str(asset.id)],metrics={'impression_count':1000})
    db.add(post);await db.flush()
    await lifecycle.record_usage(db,asset_ids=[asset.id],channel_id=channel.id,post_id=post.id)
    return asset,post


@pytest.mark.asyncio
async def test_release_is_channel_specific_and_disables_all_uses_of_same_image(db):
    a=await make_channel(db,'One');b=await make_channel(db,'Two')
    asset,post=await published_image(db,a)
    await media.archive(db,[asset.id],True)
    assert (await top_posts.overview(db,NOW))['items'][0]['can_reuse']
    await top_posts.release_post(db,post.id,SimpleNamespace(id=None))
    assert asset.status=='ready'
    assert str(asset.id) in await reuse.allowed(db,a.id)
    assert not await reuse.allowed(db,b.id)
    assert post.status=='published' and post.external_post_id=='101'
    item=(await top_posts.overview(db,NOW))['items'][0]
    assert not item['can_reuse'] and item['reuse_state']=='available'
    with pytest.raises(HTTPException) as error:
        await top_posts.release_post(db,post.id,SimpleNamespace(id=None))
    assert error.value.status_code==409


@pytest.mark.asyncio
async def test_scheduled_image_blocks_release_even_if_denormalized_flags_are_stale(db):
    channel=await make_channel(db,'One');asset,post=await published_image(db,channel)
    db.add(Post(channel_id=channel.id,status='scheduled',media_asset_ids=[str(asset.id)]));await db.flush()
    assert asset.scheduled_channel_ids==[]
    item=(await top_posts.overview(db,NOW))['items'][0]
    assert item['reuse_state']=='scheduled' and not item['can_reuse']
    with pytest.raises(HTTPException):
        await top_posts.release_post(db,post.id,SimpleNamespace(id=None))
    assert not await reuse.allowed(db,channel.id)


@pytest.mark.asyncio
async def test_daily_collector_uses_old_posts_and_persistent_claim(db,monkeypatch):
    channel=await make_channel(db,'One');asset,post=await published_image(db,channel)
    row=ChannelCredential(platform='x',external_user_id='author',access_token_enc='fake')
    db.add(row);await db.flush();channel.credential_id=row.id;await db.commit()
    monkeypatch.setattr(credentials,'to_credentials',lambda row:Credentials('fake',external_user_id='author'))
    monkeypatch.setattr(XAdapter,'verify',AsyncMock(return_value=AccountInfo(ok=True,account_id='author',username=channel.handle)))
    fetch=AsyncMock(return_value={'data':[{'id':post.external_post_id,'author_id':'author','public_metrics':{'impression_count':5000}}]})
    monkeypatch.setattr(XAdapter,'fetch_metrics_batch',fetch)
    before=datetime(2026,11,1,23,59,tzinfo=timezone.utc)
    assert (await x_post_metrics.collect(db,before))['skipped']
    at_one=before+timedelta(minutes=1)
    assert (await x_post_metrics.collect(db,at_one))['updated']==1
    assert fetch.await_count==1
    assert fetch.call_args.args[-1]==['101']
    assert (await x_post_metrics.collect(db,at_one))['skipped']
    await db.refresh(post)
    assert post.metrics['impression_count']==5000
    assert (await db.get(AppSetting,top_posts.COLLECTION_KEY)).value['status']=='ok'


@pytest.mark.asyncio
async def test_batch_request_is_read_only_and_uses_public_lifetime_views():
    adapter=XAdapter()
    with respx.mock() as mock:
        route=mock.get(adapter.base+'/2/tweets').respond(200,json={'data':[]})
        await adapter.fetch_metrics_batch(Credentials('fake'),['123','456'])
        req=route.calls.last.request
        assert dict(req.url.params)=={'ids':'123,456','tweet.fields':'public_metrics,author_id'}


@pytest.mark.asyncio
async def test_collector_rejects_metrics_from_wrong_post_author(db,monkeypatch):
    channel=await make_channel(db,'One');asset,post=await published_image(db,channel)
    row=ChannelCredential(platform='x',external_user_id='correct',access_token_enc='fake')
    db.add(row);await db.flush();channel.credential_id=row.id;await db.commit()
    monkeypatch.setattr(credentials,'to_credentials',lambda row:Credentials('fake',external_user_id='correct'))
    monkeypatch.setattr(XAdapter,'verify',AsyncMock(return_value=AccountInfo(ok=True,account_id='correct',username=channel.handle)))
    monkeypatch.setattr(XAdapter,'fetch_metrics_batch',AsyncMock(return_value={'data':[{'id':'101','author_id':'other','public_metrics':{'impression_count':999999}}]}))
    await x_post_metrics.collect(db,NOW)
    await db.refresh(post)
    assert post.metrics['x_account_mismatch']
    assert not (await top_posts.overview(db,NOW))['items']


@pytest.mark.asyncio
async def test_cross_channel_release_links_original_and_only_clears_target_upload_cache(db):
    from autoposter.models import ExternalMediaRef
    source=await make_channel(db,'Source');target=await make_channel(db,'Destination')
    asset,post=await published_image(db,source)
    original=media.read_bytes(asset)
    db.add_all([ExternalMediaRef(media_asset_id=asset.id,channel_id=source.id,external_id='source-upload'),
                ExternalMediaRef(media_asset_id=asset.id,channel_id=target.id,external_id='old-target-upload')])
    await db.flush()
    result=await top_posts.release_post(db,post.id,SimpleNamespace(id=None),target_channel_id=target.id)
    assert result['channel_id']==str(target.id)
    assert result['released_assets']==[str(asset.id)]
    assert str(asset.id) in await reuse.allowed(db,target.id)
    assert not await reuse.allowed(db,source.id)
    assert str(target.id) in asset.assigned_channel_ids
    assert post.channel_id==source.id and post.media_asset_ids==[str(asset.id)]
    assert media.read_bytes(asset)==original
    refs=(await db.execute(select(ExternalMediaRef))).scalars().all()
    assert [(r.channel_id,r.external_id) for r in refs]==[(source.id,'source-upload')]
    item=(await top_posts.overview(db,NOW))['items'][0]
    assert item['media'][0]['id']==str(asset.id)
    targets={t['channel_id']:t for t in item['targets']}
    assert targets[str(source.id)]['can_reuse']
    assert targets[str(target.id)]['reuse_state']=='available'


@pytest.mark.asyncio
async def test_planned_source_does_not_block_another_target_but_planned_target_does(db):
    source=await make_channel(db,'Source');target=await make_channel(db,'Destination')
    asset,post=await published_image(db,source)
    db.add(Post(channel_id=source.id,status='scheduled',media_asset_ids=[str(asset.id)]))
    await db.flush()
    item=(await top_posts.overview(db,NOW))['items'][0]
    targets={t['channel_id']:t for t in item['targets']}
    assert not targets[str(source.id)]['can_reuse']
    assert targets[str(target.id)]['can_reuse']
    db.add(Post(channel_id=target.id,status='draft',media_asset_ids=[str(asset.id)]));await db.flush()
    with pytest.raises(HTTPException):
        await top_posts.release_post(db,post.id,SimpleNamespace(id=None),target_channel_id=target.id)
    assert not await reuse.allowed(db,target.id)


@pytest.mark.asyncio
async def test_non_x_destinations_are_excluded_from_top_posts_x(db):
    source=await make_channel(db,'Source');target=await make_channel(db,'Telegram','telegram')
    asset,post=await published_image(db,source)
    with pytest.raises(HTTPException) as error:
        await top_posts.release_post(db,post.id,SimpleNamespace(id=None),target_channel_id=target.id)
    assert error.value.status_code==404
    assert not await reuse.allowed(db,target.id)
    item=(await top_posts.overview(db,NOW))['items'][0]
    assert all(option['platform']=='x' for option in item['targets'])
    assert str(target.id) not in {option['channel_id'] for option in item['targets']}


@pytest.mark.asyncio
async def test_fresh_upload_uses_destination_watermark_and_keeps_library_original(db):
    from io import BytesIO
    from PIL import Image
    from autoposter.adapters.base import MediaRef
    from autoposter.models import ExternalMediaRef
    from autoposter.services import publisher, watermark
    source=await make_channel(db,'Source');target=await make_channel(db,'Destination')
    asset,post=await published_image(db,source)
    original=media.read_bytes(asset)
    for channel,color in [(source,(255,0,0,255)),(target,(0,255,0,255))]:
        output=BytesIO();Image.new('RGBA',(80,80),color).save(output,format='PNG')
        watermark.store(channel.id,output.getvalue())
        channel.watermark_enabled=True;channel.watermark_anchor='top-left'
        channel.watermark_scale_pct=100;channel.watermark_margin_x_pct=0;channel.watermark_margin_y_pct=0
    db.add(ExternalMediaRef(media_asset_id=asset.id,channel_id=target.id,external_id='old-watermarked-upload'))
    await db.flush()
    await top_posts.release_post(db,post.id,SimpleNamespace(id=None),target_channel_id=target.id)
    upload=AsyncMock(return_value=MediaRef(asset_id=str(asset.id),external_id='fresh-upload'))
    await publisher._upload_all(db,SimpleNamespace(upload_media=upload),Credentials('fake'),target,[asset])
    upload.assert_awaited_once()
    rendered=upload.call_args.args[2]
    pixel=Image.open(BytesIO(rendered)).convert('RGB').getpixel((50,50))
    assert pixel[1]>220 and pixel[0]<35 and pixel[2]<35, pixel
    assert media.read_bytes(asset)==original


@pytest.mark.asyncio
async def test_cross_channel_api_accepts_explicit_target(db):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from autoposter.api.top_posts import router
    from autoposter.db import get_db
    from autoposter.deps import current_user, require_editor
    source=await make_channel(db,'Source');target=await make_channel(db,'Destination')
    asset,post=await published_image(db,source)
    app=FastAPI();app.include_router(router)
    async def session(): yield db
    app.dependency_overrides[get_db]=session
    app.dependency_overrides[current_user]=lambda:SimpleNamespace(id=None)
    app.dependency_overrides[require_editor]=lambda:SimpleNamespace(id=None)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post(f'/analytics/top-posts-x/{post.id}/reuse',params={'channel_id':str(target.id)})
    assert response.status_code==200,response.text
    assert response.json()['channel_id']==str(target.id)
    assert str(asset.id) in await reuse.allowed(db,target.id)


@pytest.mark.asyncio
async def test_channel_publication_history_uses_real_dates_and_deduplicates_usage(db):
    from autoposter.models import MediaChannelUsage
    a=await make_channel(db,'Source');b=await make_channel(db,'Other');c=await make_channel(db,'Imported');d=await make_channel(db,'Never')
    asset,original=await published_image(db,a)
    for external,days,status in [('201',20,'published'),('202',5,'published'),('dryrun-fake',1,'published'),('203',0,'draft')]:
        post=Post(channel_id=b.id,status=status,external_post_id=external,published_at=NOW-timedelta(days=days),
                  media_asset_ids=[str(asset.id)],external_url='https://x.com/i/status/'+external,
                  metrics={'impression_count': 8000 if days==20 else 123})
        db.add(post);await db.flush()
        await lifecycle.record_usage(db,asset_ids=[asset.id],channel_id=b.id,post_id=post.id)
    db.add(MediaChannelUsage(media_asset_id=asset.id,channel_id=c.id,used_at=NOW-timedelta(days=8)))
    await db.flush()
    item=next(row for row in (await top_posts.overview(db,NOW))['items'] if row['post_id']==str(original.id))
    targets={t['channel_id']:t for t in item['targets']}
    assert len(targets[str(a.id)]['publications'])==1
    history=targets[str(b.id)]['publications']
    assert [entry['age_days'] for entry in history]==[5,20]
    assert [entry['age_band'] for entry in history]==['red','yellow']
    assert targets[str(b.id)]['last_published_at']==(NOW-timedelta(days=5)).isoformat()
    assert [entry['views'] for entry in history]==[123,8000]
    assert targets[str(b.id)]['last_views']==123
    assert all(entry['asset_ids']==[str(asset.id)] for entry in history)
    assert targets[str(c.id)]['publications'][0]['age_days']==8
    assert targets[str(c.id)]['last_views'] is None
    assert not targets[str(d.id)]['publications']
    assert targets[str(d.id)]['last_published_at'] is None
