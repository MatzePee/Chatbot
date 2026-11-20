"""Real HTTP/database acceptance; synthetic data, no lifespan/providers."""
import json
from uuid import UUID
import os
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func
from autoposter import db as post, models
from autoposter.workspaces import character_context
from app import db as chat


@pytest.fixture
async def installation(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.main import app
    from autoposter.config import settings
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    monkeypatch.setenv('MP_PREVIEW', '0')
    monkeypatch.setattr(settings, 'auth_enabled', False)
    monkeypatch.setattr(settings, 'media_root', tmp_path / 'media')
    monkeypatch.setattr(post.settings, 'database_url', 'sqlite+aiosqlite:///' + chat.DB_PATH)
    engine = post.make_engine()
    monkeypatch.setattr(post, 'engine', engine)
    monkeypatch.setattr(chat, '_character_connections', {})
    async with engine.begin() as conn:
        await conn.run_sync(post.Base.metadata.create_all)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://offline.test') as client:
        owners=[]
        for name in ['Ash', 'Birch', 'Cherry', 'Dune', 'Elm', 'Fern']:
            r=await client.post('/api/v1/characters', json={'name':name})
            assert r.status_code==201, r.text
            assert not r.json()['planning_enabled'] and not r.json()['chat_enabled']
            owners.append(r.json())
        yield client, owners, engine
    await engine.dispose()
    for conn in chat._character_connections.values():
        conn.close()
    chat._character_connections.clear()


@pytest.mark.asyncio
async def test_six_media_upload_sets_references_and_file_boundaries(installation):
    import io
    from PIL import Image
    client, owners, engine=installation
    raw=io.BytesIO(); Image.new('RGB',(32,32),'navy').save(raw,format='PNG')
    assets=[]; sets=[]
    for owner in owners:
        oid=owner['id']
        r=await client.post('/api/v1/media/upload?character_id='+oid,files={'files':('same.png',raw.getvalue(),'image/png')})
        assert r.status_code==201,r.text
        assert r.json()['summary']['created']==1,r.text
        asset=r.json()['created'][0];assets.append(asset)
        r=await client.post('/api/v1/sets?character_id='+oid,json={'name':'Same set','asset_ids':[asset['id']]})
        assert r.status_code==201,r.text
        sets.append(r.json())
    assert len({a['id'] for a in assets})==6
    for i, owner in enumerate(owners):
        oid=owner['id']; foreign=assets[(i+1)%6]['id']
        for suffix in ['', '/file', '/thumb']:
            r=await client.get(f'/api/v1/media/{foreign}{suffix}?character_id={oid}')
            assert r.status_code==404,r.text
        for suffix in ['/file','/thumb']:
            r=await client.get(f"/api/v1/media/{assets[i]['id']}{suffix}?character_id={oid}")
            assert r.status_code==200,r.text
            assert 'private' in r.headers['cache-control']
        r=await client.patch(f"/api/v1/sets/{sets[(i+1)%6]['id']}?character_id={oid}",json={'name':'Forbidden'})
        assert r.status_code==404,r.text
        r=await client.post('/api/v1/sets?character_id='+oid,json={'name':'Forbidden reference','asset_ids':[foreign]})
        assert r.status_code in [403,404,422],r.text
        r=await client.get('/api/v1/sets?character_id='+oid)
        assert [s['id'] for s in r.json()]==[sets[i]['id']]


@pytest.mark.asyncio
async def test_delayed_real_upload_keeps_original_request_owner(installation,monkeypatch):
    import asyncio, threading, io
    from PIL import Image
    from autoposter.services import media
    client,owners,engine=installation
    started=threading.Event(); release=threading.Event(); original=media.prepare_image
    def delayed(*args):
        started.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(media,'prepare_image',delayed)
    raw=io.BytesIO();Image.new('RGB',(32,32),'green').save(raw,format='PNG')
    a,b=[o['id'] for o in owners[:2]]
    upload=asyncio.create_task(client.post('/api/v1/media/upload?character_id='+a,files={'files':('delayed.png',raw.getvalue(),'image/png')}))
    assert await asyncio.to_thread(started.wait,5)
    r=await client.post('/api/v1/views?character_id='+b,json={'name':'Other tab','query':{}})
    assert r.status_code==201,r.text
    release.set();r=await upload
    assert r.status_code==201,r.text
    assert r.json()['summary']['created']==1,r.text
    asset=r.json()['created'][0]['id']
    assert (await client.get(f'/api/v1/media/{asset}/file?character_id={a}')).status_code==200
    assert (await client.get(f'/api/v1/media/{asset}/file?character_id={b}')).status_code==404


@pytest.mark.asyncio
async def test_six_telegram_channel_credentials_are_not_notification_settings(installation):
    from autoposter.services import credentials
    client,owners,engine=installation
    channels=[]
    chat.set_setting('telegram_bot_token','synthetic-global-notification')
    for i,owner in enumerate(owners):
        oid=owner['id']
        r=await client.post('/api/v1/channels?character_id='+oid,json={'platform':'telegram','display_name':owner['name']+' Telegram','handle':'@synthetic_channel'})
        assert r.status_code==201,r.text
        c=r.json();channels.append(c)
        assert not c['auto_plan_enabled'] and not c['is_active']
        for token in [f'{i+100}:synthetic_token', '', '********', '••••••••']:
            r=await client.put(f"/api/v1/channels/{c['id']}/telegram?character_id={oid}",json={'bot_token':token,'destination':'@synthetic_channel'})
            assert r.status_code==200,r.text
            assert 'synthetic_token' not in r.text
        with character_context(oid):
            async with post.SessionLocal() as session:
                row=await session.get(models.Channel, UUID(c['id']))
                cred=await session.get(models.ChannelCredential,row.credential_id)
                assert credentials.to_credentials(cred).access_token==f'{i+100}:synthetic_token'
    first=owners[0]['id']
    assert (await client.post(f"/api/v1/channels/{channels[0]['id']}/disconnect?character_id={first}")).status_code==204
    for i,owner in enumerate(owners):
        oid=owner['id']
        with character_context(oid):
            assert chat.get_setting('telegram_bot_token')=='synthetic-global-notification'
            async with post.SessionLocal() as session:
                c=await session.get(models.Channel,UUID(channels[i]['id']))
                assert bool(c.credential_id)==bool(i)
        foreign=channels[(i+1)%6]['id']
        assert (await client.put(f'/api/v1/channels/{foreign}/telegram?character_id={oid}',json={'bot_token':'999:synthetic_forbidden','destination':'@synthetic_channel'})).status_code==404


@pytest.mark.asyncio
async def test_six_x_channel_app_secret_retains_masks_and_explicit_owner_clear(installation):
    from autoposter.crypto import decrypt
    client,owners,engine=installation
    channels=[]
    for i,owner in enumerate(owners):
        oid=owner['id']
        r=await client.post('/api/v1/channels?character_id='+oid,json={'platform':'x','display_name':owner['name']+' X','handle':'@synthetic','oauth_client_id':f'synthetic-client-{i}','oauth_client_secret':f'synthetic-channel-secret-{i}'})
        assert r.status_code==201,r.text
        channels.append(r.json())
        for mask in ['', '********','••••••••','●●●●']:
            r=await client.patch(f"/api/v1/channels/{channels[-1]['id']}?character_id={oid}",json={'oauth_client_secret':mask})
            assert r.status_code==200,r.text
        with character_context(oid):
            async with post.SessionLocal() as session:
                row=await session.get(models.Channel,UUID(channels[-1]['id']))
                assert decrypt(row.oauth_client_secret_enc)==f'synthetic-channel-secret-{i}'
    first=owners[0]['id']
    r=await client.patch(f"/api/v1/channels/{channels[0]['id']}?character_id={first}",json={'oauth_clear_client_secret':True})
    assert r.status_code==200,r.text
    assert r.json()['has_client_secret'] is False
    for i,owner in enumerate(owners):
        with character_context(owner['id']):
            async with post.SessionLocal() as session:
                row=await session.get(models.Channel,UUID(channels[i]['id']))
                assert bool(row.oauth_client_secret_enc)==bool(i)
    assert (await client.patch(f"/api/v1/channels/{channels[1]['id']}?character_id={first}",json={'oauth_clear_client_secret':True})).status_code==404


@pytest.mark.asyncio
async def test_six_queued_jobs_preserve_owner_and_hide_ids_from_other_tabs(installation):
    import asyncio
    from autoposter.services import runs
    from autoposter.workspaces import current_character
    client,owners,engine=installation
    release=asyncio.Event();jobs=[];observed=[]
    async def work(run):
        await release.wait();observed.append(str(current_character()))
        return {'owner':str(current_character())}
    for owner in owners:
        with character_context(owner['id']):
            job=runs.create('Synthetic queued work');runs.launch(job,work);jobs.append(job)
    for i,owner in enumerate(owners):
        with character_context(owner['id']):
            assert runs.get(jobs[i].id) is jobs[i]
            for j,job in enumerate(jobs):
                if i!=j:
                    assert runs.get(job.id) is None
                    assert not runs.cancel(job.id)
    release.set()
    for _ in range(10):await asyncio.sleep(0)
    assert sorted(observed)==sorted(o['id'] for o in owners)
    for i,owner in enumerate(owners):
        with character_context(owner['id']):assert jobs[i].result=={'owner':owner['id']}


@pytest.mark.asyncio
async def test_media_no_legacy_path_fallback(installation):
    from autoposter.config import settings
    client,owners,engine=installation
    oid=owners[0]['id']
    settings.media_root.mkdir(parents=True,exist_ok=True)
    (settings.media_root/'legacy-private.jpg').write_bytes(b'legacy protected')
    with character_context(oid):
        async with post.SessionLocal() as session:
            asset=models.MediaAsset(filename='legacy-private.jpg',sha256='f'*64,storage_path='legacy-private.jpg')
            session.add(asset);await session.commit();aid=asset.id
    for suffix in ['file','thumb']:
        r=await client.get(f'/api/v1/media/{aid}/{suffix}?character_id={oid}')
        assert r.status_code==404,r.text
        assert b'legacy protected' not in r.content


@pytest.mark.parametrize('escape',['namespace','owner','file'])
def test_export_symlinks_cannot_write_foreign_workspace(tmp_path,monkeypatch,escape):
    from app import main
    from autoposter.workspaces import CharacterBoundaryError
    from uuid import uuid4
    owner=uuid4();base=tmp_path/'exports';outside=tmp_path/'foreign';outside.mkdir()
    monkeypatch.setattr(main,'EXPORT_DIR',str(base))
    monkeypatch.setattr(main.fanvue,'full_message_history',lambda fan:[{'text':'Synthetic','sender':{'uuid':fan}}])
    base.mkdir();namespace=base/'characters'
    if escape=='namespace':namespace.symlink_to(outside,target_is_directory=True)
    else:
        namespace.mkdir()
        if escape=='owner':(namespace/str(owner)).symlink_to(outside,target_is_directory=True)
        else:
            (namespace/str(owner)).mkdir()
            (outside/'protected.md').write_text('protected')
            (namespace/str(owner)/'same-handle.md').symlink_to(outside/'protected.md')
    with character_context(owner),pytest.raises(CharacterBoundaryError):
        main._write_chat_export('synthetic-fan','same-handle','Synthetic','')
    if escape=='file':assert (outside/'protected.md').read_text()=='protected'
    else:assert list(outside.iterdir())==[]


@pytest.mark.asyncio
async def test_six_export_direct_request_rejects_foreign_fan_before_provider(installation,monkeypatch,tmp_path):
    from app import main
    client,owners,engine=installation
    called=[]
    monkeypatch.setattr(main.fanvue,'is_connected',lambda:False)
    monkeypatch.setattr(main,'EXPORT_DIR',str(tmp_path/'exports'))
    def history(fan):
        called.append(fan)
        return [{'text':'Synthetic local test','sender':{'uuid':fan}}]
    monkeypatch.setattr(main.fanvue,'full_message_history',history)
    for i,owner in enumerate(owners):
        with character_context(owner['id']):chat.upsert_chat(f'synthetic-fan-{i}',handle='same-handle')
    for i,owner in enumerate(owners):
        oid=owner['id'];foreign=f'synthetic-fan-{(i+1)%6}'
        before=len(called)
        r=await client.post(f'/chats/{foreign}/export?character_id={oid}')
        assert r.status_code==404,r.text
        assert len(called)==before
        r=await client.post(f'/chats/synthetic-fan-{i}/export?character_id={oid}')
        assert r.status_code==303,r.text
        assert (tmp_path/'exports'/'characters'/oid/'same-handle.md').exists()


@pytest.mark.asyncio
async def test_export_synthetic_ui_fixtures_from_actual_six_create_routes(installation):
    client,owners,engine=installation
    out=Path(os.environ.get('SIX_RENDER_OUTPUT','/tmp/six-character-render'))
    out.mkdir(parents=True,exist_ok=True)
    for i,owner in enumerate(owners):
        oid=owner['id']
        assert (await client.post('/settings/shared/fanvue?character_id='+oid,data={'fanvue_client_id':f'synthetic-app-{i}','fanvue_client_secret':f'synthetic-secret-{i}'})).status_code==303
        for section in ['shared','autochat','autopost','channels']:
            r=await client.get(f'/settings/shared?section={section}&character_id={oid}')
            assert r.status_code==200,r.text
            (out/f'{oid}-{section}.html').write_text(r.text)
        r=await client.get('/api/v1/settings/integrations?character_id='+oid)
        assert r.status_code==200,r.text
        (out/f'{oid}-integrations.json').write_text(r.text)
        for platform in ['x','telegram']:
            r=await client.post('/api/v1/channels?character_id='+oid,json={'platform':platform,'display_name':owner['name']+' '+platform,'handle':'@synthetic_channel','oauth_client_id':('synthetic-app-'+str(i) if platform=='x' else ''),'oauth_client_secret':('synthetic-private-'+str(i) if platform=='x' else '')})
            assert r.status_code==201,r.text
        r=await client.get('/api/v1/channels?character_id='+oid)
        assert r.status_code==200,r.text
        for channel in r.json():
            assert channel['auto_plan_enabled'] is False, 'New character channels must not enable automatic planning'
        (out/f'{oid}-channels.json').write_text(r.text)
    (out/'owners.json').write_text(json.dumps(owners,indent=2))


@pytest.mark.asyncio
async def test_six_created_owners_settings_get_never_embeds_protected_secrets(installation):
    client, owners, engine = installation
    for i, owner in enumerate(owners):
        oid=owner['id']
        with character_context(oid):
            chat.set_setting('openrouter_api_key', 'synthetic-private-technical')
            chat.set_setting('vision_api_key', 'synthetic-private-vision')
            chat.set_setting('telegram_bot_token', 'synthetic-private-notification')
        r=await client.post('/settings/shared/fanvue?character_id='+oid, data={
            'fanvue_client_id':f'synthetic-app-{i}', 'fanvue_client_secret':f'synthetic-private-fanvue-{i}'})
        assert r.status_code==303, r.text
        for section in ['shared', 'autochat']:
            r=await client.get(f'/settings/shared?section={section}&character_id={oid}')
            assert r.status_code==200, r.text
            assert 'synthetic-private-' not in r.text, 'Settings HTML must not return plaintext credentials'


@pytest.mark.asyncio
async def test_six_create_and_settings_blank_mask_clear_reopen(installation):
    client, owners, engine=installation
    for i, owner in enumerate(owners):
        oid=owner['id']
        with character_context(oid):
            async with post.SessionLocal() as session:
                for model in [models.Channel,models.MediaAsset,models.MediaSet,models.Persona,models.Post]:
                    assert await session.scalar(select(func.count()).select_from(model))==0
            assert chat.get_tokens() is None
        for url,payload in [('/settings/shared/fanvue',{'fanvue_client_id':f'synthetic-app-{i}','fanvue_client_secret':f'synthetic-fv-{i}'}),
                            ('/api/v1/settings/integrations',{'x_client_id':f'synthetic-x-{i}','x_client_secret':f'synthetic-xs-{i}','auto_plan_days':i+10})]:
            r=await client.post(url+'?character_id='+oid, **({'json':payload} if url.startswith('/api') else {'data':payload}))
            assert r.status_code in [200,303],r.text
        for mask in ['', '********', '••••••••']:
            assert (await client.post('/settings/shared/fanvue?character_id='+oid,data={'fanvue_client_secret':mask})).status_code==303
            assert (await client.post('/api/v1/settings/integrations?character_id='+oid,json={'x_client_secret':mask})).status_code==200
    await engine.dispose()
    for conn in chat._character_connections.values():conn.close()
    chat._character_connections.clear()
    for i, owner in enumerate(owners):
        oid=owner['id']
        with character_context(oid):
            assert chat.get_setting('fanvue_client_secret')==f'synthetic-fv-{i}'
        r=await client.get('/api/v1/settings/integrations?character_id='+oid)
        assert r.status_code==200,r.text
        assert r.json()['x_client_id']==f'synthetic-x-{i}'
        assert r.json()['auto_plan_days']==i+10
        assert 'synthetic-xs-' not in r.text
    first=owners[0]['id']
    assert (await client.post('/settings/shared/fanvue?character_id='+first,data={'clear_fanvue_client_secret':'on'})).status_code==303
    assert (await client.post('/api/v1/settings/integrations/clear?character_id='+first,json={'field':'x_client_secret'})).status_code==200
    from autoposter.services import appconfig
    for i,owner in enumerate(owners):
        with character_context(owner['id']):
            assert chat.get_setting('fanvue_client_secret')==('' if i==0 else f'synthetic-fv-{i}')
            async with post.SessionLocal() as session:
                assert (await appconfig.get_all(session,use_cache=False))['x_client_secret']==('' if i==0 else f'synthetic-xs-{i}')
