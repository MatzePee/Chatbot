"""Real API routes, disposable DB, no lifespan, all Bot API requests mocked."""
import json
import subprocess
import uuid
import httpx
import pytest
from fastapi import FastAPI
from autoposter.api import channels, posts, media as media_api
from autoposter.db import get_db
from autoposter.deps import current_user, require_admin, require_editor
from autoposter.config import settings
from autoposter.models import User, Channel, Post
from autoposter.services import media
from .test_telegram import no_network
from .test_telegram_publish import image_bytes

@pytest.mark.asyncio
@pytest.mark.parametrize('kind,method',[('text','sendMessage'),('photo','sendPhoto'),('video','sendVideo'),('mixed','sendMediaGroup')])
async def test_full_setup_manual_post_status_and_independent_queue(db,monkeypatch,tmp_path,kind,method):
    monkeypatch.setattr(settings,'dry_run',False)
    from autoposter import jobs
    def forbidden(*args,**kwargs):raise AssertionError('Unrelated scheduler job executed')
    for name in ['publish_tick','plan_tick','metrics_tick','visibility_aging_tick']:
        if hasattr(jobs,name):monkeypatch.setattr(jobs,name,forbidden)
    app=FastAPI();app.include_router(channels.router,prefix='/api/v1');app.include_router(posts.router,prefix='/api/v1')
    app.include_router(media_api.router,prefix='/api/v1')
    user=User(id=uuid.uuid4(),email='synthetic@test.invalid',password_hash='synthetic-not-used',role='admin')
    db.add(user);await db.commit()
    untouched=[]
    for platform in ('x','fanvue'):
        other=Channel(platform=platform,display_name='Untouched '+platform);db.add(other);await db.flush()
        item=Post(channel_id=other.id,type='text_only',status='scheduled',body_text='Never send '+platform);db.add(item);untouched.append(item)
    await db.commit()
    async def database():
        yield db
        await db.commit()
    app.dependency_overrides[get_db]=database
    from contextlib import asynccontextmanager
    monkeypatch.setattr(media_api,'write_session',asynccontextmanager(database))
    for dep in (current_user,require_admin,require_editor):app.dependency_overrides[dep]=lambda:user
    seen=[]
    def handler(req):
        assert req.method=='POST' and req.url.host=='api.telegram.org'
        name=req.url.path.rsplit('/',1)[1];seen.append(name)
        result={'getMe':{'id':123,'is_bot':True,'username':'sample_bot'},'getChat':{'id':-100456,'type':'channel','username':'sample_channel'},'getChatMember':{'status':'administrator','can_post_messages':True,'user':{'id':123}}}.get(name)
        if name.startswith('send'):
            assert name==method
            result={'message_id':30,'chat':{'id':-100456}}
            if name=='sendMediaGroup':result=[result,{'message_id':31,'chat':{'id':-100456}}]
        assert result is not None
        return httpx.Response(200,json={'ok':True,'result':result})
    monkeypatch.setattr('autoposter.adapters.telegram.http_client',lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic') as client:
        response=await client.post('/api/v1/channels',json={'platform':'telegram','display_name':'Own Telegram','handle':'@sample_channel'})
        assert response.status_code==201,response.text
        channel_id=response.json()['id']
        assert not response.json()['is_connected'] and not response.json()['is_active']
        response=await client.put(f'/api/v1/channels/{channel_id}/telegram',json={'bot_token':'123:synthetic','destination':'@sample_channel'})
        assert response.status_code==200 and '123:synthetic' not in response.text
        response=await client.post(f'/api/v1/channels/{channel_id}/test')
        assert response.json()['ok'] and seen==['getMe','getChat','getChatMember']
        response=await client.patch(f'/api/v1/channels/{channel_id}',json={'is_active':True})
        assert response.status_code==200
        asset_ids=[]
        if kind in ('photo','mixed'):
            response=await client.post('/api/v1/media/upload',files={'files':('p.jpg',image_bytes(),'image/jpeg')})
            assert response.status_code==201 and response.json()['errors']==[],response.text
            asset_ids.append(response.json()['created'][0]['id'])
        if kind in ('video','mixed'):
            movie=tmp_path/'fixture.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=32x32:d=0.1','-c:v','libx264','-pix_fmt','yuv420p',str(movie)],check=True)
            response=await client.post('/api/v1/media/upload',files={'files':('v.mp4',movie.read_bytes(),'video/mp4')})
            assert response.status_code==201 and response.json()['errors']==[],response.text
            video_id=response.json()['created'][0]['id'];asset_ids.append(video_id)
            response=await client.get(f'/api/v1/media/{video_id}/file')
            assert response.status_code==200 and response.headers['content-type']=='video/mp4' and response.content[4:8]==b'ftyp'
            response=await client.get(f'/api/v1/media/{video_id}/thumb')
            assert response.status_code==200 and response.headers['content-type']=='image/jpeg'
        await db.commit()
        response=await client.post('/api/v1/posts',json={'channel_id':channel_id,'type':'text_only' if not asset_ids else 'image_set','body_text':'Own Telegram post','media_asset_ids':asset_ids,'status':'draft'})
        assert response.status_code==201,response.text
        post_id=response.json()['id']
        response=await client.post(f'/api/v1/posts/{post_id}/publish')
        assert response.status_code==200 and response.json()['ok'],response.text
        response=await client.get('/api/v1/posts',params={'channel_ids':channel_id})
        row=response.json()[0]
        assert row['audience']==''
        assert row['status']=='published' and row['external_post_id']==('30,31' if kind=='mixed' else '30')
        assert row['external_url']=='https://t.me/sample_channel/30'
        await client.post(f'/api/v1/posts/{post_id}/publish')
        assert seen==['getMe','getChat','getChatMember',method]
        for item in untouched:
            await db.refresh(item)
            assert item.status=='scheduled' and not item.external_post_id
