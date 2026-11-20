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


def test_berlin_calendar_age_across_dst():
    # 23:30 UTC is already the next Berlin calendar day.
    then=datetime(2026,10,17,23,30,tzinfo=timezone.utc)
    assert top_posts.age_info(then,NOW)==(14,'red')



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
async def test_batch_request_is_read_only_and_uses_public_lifetime_views():
    adapter=XAdapter()
    with respx.mock() as mock:
        route=mock.get(adapter.base+'/2/tweets').respond(200,json={'data':[]})
        await adapter.fetch_metrics_batch(Credentials('fake'),['123','456'])
        req=route.calls.last.request
        assert dict(req.url.params)=={'ids':'123,456','tweet.fields':'public_metrics,author_id'}


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


async def manual_x_fixture(db, monkeypatch, *, returned_id='42', author_id='42'):
    """Real adapter, exact transport allowlist; no mocked collector/verify method."""
    import httpx
    import autoposter.adapters.x as x_module
    channel = await make_channel(db, 'One')
    _, post = await published_image(db, channel)
    row = ChannelCredential(platform='x', external_user_id='42', access_token_enc='fake')
    db.add(row)
    await db.flush()
    channel.credential_id = row.id
    await db.commit()
    monkeypatch.setattr(credentials, 'to_credentials', lambda row: Credentials('synthetic', external_user_id=row.external_user_id))
    requests = []
    def transport(request):
        requests.append(request)
        assert request.method == 'GET'
        assert request.headers['authorization'] == 'Bearer synthetic'
        if request.url.path == '/2/users/me':
            assert dict(request.url.params) == {'user.fields': 'username,name,verified'}
            return httpx.Response(200, json={'data': {'id': returned_id, 'username': channel.handle.lstrip('@'), 'name': 'Fixture'}})
        assert request.url.path == '/2/tweets', str(request.url)
        assert request.url.params['tweet.fields'] == 'public_metrics,author_id'
        return httpx.Response(200, json={'data': [
            {'id': external, 'author_id': author_id, 'public_metrics': {'impression_count': 5000}}
            for external in request.url.params['ids'].split(',')]})
    monkeypatch.setattr(x_module, 'http_client', lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    return channel, post, requests


@pytest.mark.asyncio
async def test_manual_collection_rejects_changed_bound_account_id_before_post_reads(db, monkeypatch):
    _, post, requests = await manual_x_fixture(db, monkeypatch, returned_id='99', author_id='99')
    token = await x_post_metrics.claim(db, None, NOW)
    result = await x_post_metrics.collect(db, None, token, NOW)
    assert [r.url.path for r in requests] == ['/2/users/me']
    assert result['updated'] == 0 and result['errors']
    await db.refresh(post)
    assert post.metrics['impression_count'] == 1000


@pytest.mark.asyncio
async def test_manual_wrong_post_author_is_excluded_from_cached_ranking(db, monkeypatch):
    _, post, requests = await manual_x_fixture(db, monkeypatch, author_id='999')
    token = await x_post_metrics.claim(db, None, NOW)
    result = await x_post_metrics.collect(db, None, token, NOW)
    assert result['unavailable'] == 1
    await db.refresh(post)
    assert post.metrics['x_account_mismatch'] is True
    assert not (await top_posts.overview(db, NOW))['items']
    assert [r.url.path for r in requests] == ['/2/users/me', '/2/tweets']


@pytest.mark.asyncio
@pytest.mark.parametrize('now', [datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc),
                                datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)])
@pytest.mark.parametrize('days', [30, 180])
async def test_manual_scope_filters_publication_instant_across_dst_and_retains_old_cache(db, monkeypatch, now, days):
    channel, old, requests = await manual_x_fixture(db, monkeypatch)
    cutoff = now - timedelta(days=days)
    old.published_at = cutoff - timedelta(microseconds=1)
    inside = Post(channel_id=channel.id, status='published', external_post_id='202', published_at=cutoff)
    outside = Post(channel_id=channel.id, status='published', external_post_id='203', published_at=cutoff-timedelta(seconds=1), metrics={'impression_count': 1200})
    db.add_all([inside, outside])
    await db.commit()
    token = await x_post_metrics.claim(db, days, now)
    result = await x_post_metrics.collect(db, days, token, now)
    assert result['updated'] == 1 and not result['errors']
    assert requests[-1].url.params['ids'] == '202'
    await db.refresh(old); await db.refresh(outside)
    assert old.metrics['impression_count'] == 1000
    assert outside.metrics['impression_count'] == 1200
    cached = await top_posts.overview(db, now)
    assert {i['views'] for i in cached['items']} == {1000, 1200, 5000}


@pytest.mark.asyncio
async def test_manual_all_includes_old_posts_but_requires_matching_claim_and_read_get_never_collects(db, monkeypatch):
    from autoposter import jobs
    from autoposter.api.top_posts import router
    from autoposter.db import get_db
    from autoposter.deps import current_user
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    _, post, requests = await manual_x_fixture(db, monkeypatch)
    post.published_at = NOW-timedelta(days=1000)
    await db.commit()
    with pytest.raises(ValueError, match='matching manual'):
        await x_post_metrics.collect(db, None, 'not-claimed', NOW)
    assert requests == []
    assert await jobs.collect_x_post_metrics() == {'skipped': True, 'reason': 'manual_only'}
    app=FastAPI(); app.include_router(router)
    async def session(): yield db
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=None)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/analytics/top-posts-x')).status_code == 200
    assert requests == []
    token = await x_post_metrics.claim(db, None, NOW)
    with pytest.raises(ValueError, match='matching manual'):
        await x_post_metrics.collect(db, 30, token, NOW)
    assert requests == []
    assert (await x_post_metrics.collect(db, None, token, NOW))['updated'] == 1
    assert requests[-1].url.params['ids'] == '101'
    state = (await db.get(AppSetting, top_posts.COLLECTION_KEY, populate_existing=True)).value
    assert state['active'] is None and state['manual']['all']['status'] == 'ok'
    assert state['manual']['all']['triggered_at'] == NOW.isoformat()


@pytest.mark.asyncio
async def test_manual_claim_serializes_independent_sessions_and_expired_completion_cannot_clear_new_claim(tmp_path):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from autoposter.db import Base
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'claims.db'))
    try:
        async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
        maker=async_sessionmaker(engine,expire_on_commit=False)
        async def attempt(days):
            async with maker() as session:
                try: return await x_post_metrics.claim(session,days,NOW)
                except HTTPException as exc: return exc.status_code
        first, second=await asyncio.gather(attempt(30),attempt(180))
        assert [first,second].count(409)==1
        winner=first if first!=409 else second
        async with maker() as session:
            state=(await session.get(AppSetting,top_posts.COLLECTION_KEY)).value
            assert state['active']['token']==winner
            assert len(state['manual'])==1
            later=await x_post_metrics.claim(session,None,NOW+timedelta(minutes=46))
            await x_post_metrics.fail(session,winner)
            state=(await session.get(AppSetting,top_posts.COLLECTION_KEY,populate_existing=True)).value
            assert state['active']['token']==later
            await x_post_metrics.fail(session,later)
            state=(await session.get(AppSetting,top_posts.COLLECTION_KEY,populate_existing=True)).value
            assert state['active'] is None and state['manual']['all']['status']=='error'
    finally: await engine.dispose()


@pytest.mark.asyncio
async def test_manual_endpoint_real_auth_roles_scope_and_background_claim(db, monkeypatch):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from autoposter.api import top_posts as endpoint
    from autoposter.db import get_db
    from autoposter.config import settings
    from autoposter.models import User
    from autoposter.security import create_token
    monkeypatch.setattr(settings,'auth_enabled',True)
    app=FastAPI(); app.include_router(endpoint.router)
    async def session(): yield db
    app.dependency_overrides[get_db]=session
    background=AsyncMock()
    monkeypatch.setattr(endpoint,'_collect_in_background',background)
    users={}
    for role in ['viewer','editor','admin']:
        user=User(email=role+'@fixture.invalid',password_hash='unused',role=role)
        db.add(user); await db.flush(); users[role]=user
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.post('/analytics/top-posts-x/collection/all')).status_code==401
        for role in ['viewer','editor','admin']:
            headers={'Authorization':'Bearer '+create_token(str(users[role].id))}
            before = await db.get(AppSetting,top_posts.COLLECTION_KEY,populate_existing=True)
            before_value = dict(before.value) if before else None
            response=await client.post('/analytics/top-posts-x/collection/invalid',headers=headers)
            assert response.status_code==(403 if role=='viewer' else 422)
            after = await db.get(AppSetting,top_posts.COLLECTION_KEY,populate_existing=True)
            assert (after.value if after else None) == before_value
            if role=='viewer':
                assert (await client.post('/analytics/top-posts-x/collection/all',headers=headers)).status_code==403
                assert (await client.get('/analytics/top-posts-x',headers=headers)).status_code==200
            else:
                for scope,days in [('all',None),('180',180),('30',30)]:
                    response=await client.post('/analytics/top-posts-x/collection/'+scope,headers=headers)
                    assert response.status_code==202,response.text
                    state=(await db.get(AppSetting,top_posts.COLLECTION_KEY,populate_existing=True)).value
                    token=state['active']['token']
                    assert background.call_args.args==(days,token)
                    assert (await client.post('/analytics/top-posts-x/collection/all',headers=headers)).status_code==409
                    await x_post_metrics.fail(db,token)
        assert background.await_count==6



def test_neither_scheduler_registers_paid_automatic_x_collection():
    from autoposter.scheduler import default_jobs
    from autoposter.celery_app import celery_app
    assert 'collect_x_post_metrics' not in {job.name for job in default_jobs()}
    for entry in celery_app.conf.beat_schedule.values():
        assert entry['task'] != 'autoposter.collect_x_post_metrics'
        assert entry.get('kwargs',{}).get('job_name') != 'collect_x_post_metrics'
