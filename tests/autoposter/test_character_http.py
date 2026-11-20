import uuid
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from autoposter import db as database, models
from autoposter.api import api_router


@pytest.mark.asyncio
async def test_character_url_scopes_real_views_api_and_fails_closed(db, monkeypatch):
    from app.character_http import CharacterMiddleware
    from autoposter.workspaces import WorkspaceAsyncSession
    a,b=models.Character(name='Alpha'),models.Character(name='Beta')
    db.add_all([a,b]);await db.commit()
    maker=async_sessionmaker(db.bind,class_=WorkspaceAsyncSession,expire_on_commit=False)
    monkeypatch.setattr(database,'SessionLocal',maker)
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    app=FastAPI();app.include_router(api_router);app.add_middleware(CharacterMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/api/v1/views')).status_code == 409
        assert (await client.get('/api/v1/views?character_id=invalid')).status_code == 400
        assert (await client.get(f'/api/v1/views?character_id={uuid.uuid4()}')).status_code == 404
        created=await client.post(f'/api/v1/views?character_id={a.id}',json={'name':'Alpha private','query':{}})
        assert created.status_code == 201,created.text
        vid=created.json()['id']
        rows=(await client.get(f'/api/v1/views?character_id={b.id}')).json()
        assert not any(row['id']==vid for row in rows)
        denied=await client.delete(f'/api/v1/views/{vid}?character_id={b.id}')
        assert denied.status_code == 404
        rows=(await client.get(f'/api/v1/views?character_id={a.id}')).json()
        assert any(row['id']==vid for row in rows)
        # An unscoped background DB access is not a legacy fallback in enabled mode.
        with pytest.raises(ValueError,match='Character'):
            maker()


@pytest.mark.asyncio
async def test_http_primes_sync_provider_config_without_legacy_account_fallback(db, monkeypatch):
    from app.character_http import CharacterMiddleware
    from autoposter.workspaces import WorkspaceAsyncSession
    from autoposter.services import appconfig
    from autoposter.crypto import encrypt
    owner = models.Character(name='Synthetic')
    db.add_all([owner, models.AppSetting(key='integrations', value={
        'openrouter_api_key': encrypt('synthetic-http-legacy'),
        'x_client_secret': encrypt('synthetic-private'),
    })])
    await db.commit()
    monkeypatch.setattr(database, 'SessionLocal', async_sessionmaker(db.bind, class_=WorkspaceAsyncSession, expire_on_commit=False))
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    app = FastAPI()
    app.add_middleware(CharacterMiddleware)
    @app.get('/synthetic-provider')
    def provider_mock():
        return {'technical': appconfig.current('openrouter_api_key'), 'account': appconfig.current('x_client_secret')}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get(f'/synthetic-provider?character_id={owner.id}')
        assert response.status_code == 200
        assert response.json() == {'technical': 'synthetic-http-legacy', 'account': None}


@pytest.mark.asyncio
async def test_character_creation_is_empty_disabled_and_no_persona_clone(db, monkeypatch):
    from app.character_http import CharacterMiddleware
    from autoposter.workspaces import WorkspaceAsyncSession, character_session
    from sqlalchemy import select,func
    maker=async_sessionmaker(db.bind,class_=WorkspaceAsyncSession,expire_on_commit=False)
    monkeypatch.setattr(database,'SessionLocal',maker)
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    app=FastAPI();app.include_router(api_router);app.add_middleware(CharacterMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/v1/characters',json={'name':'Cherry'})
        assert response.status_code==201,response.text
        char=response.json()
        assert char['name']=='Cherry'
        assert char['planning_enabled'] is False and char['chat_enabled'] is False
        assert char['id'] in [row['id'] for row in (await client.get('/api/v1/characters')).json()]
        async with character_session(db.bind,char['id']) as session:
            for model in (models.Channel,models.MediaAsset,models.MediaSet,models.SavedView,models.Persona,models.Post):
                assert await session.scalar(select(func.count()).select_from(model)) == 0
            runtime=(await session.get(models.AppSetting,'runtime')).value
            assert runtime=={'global_pause':True,'dry_run':True}
            from app import db as chat_db
            assert chat_db.get_tokens() is None
            assert chat_db.get_setting('bot_running') is False
        assert (await client.post('/api/v1/characters',json={'name':'  '})).status_code==422


@pytest.mark.asyncio
async def test_real_app_serves_character_switch_on_both_areas(db,monkeypatch):
    from autoposter.workspaces import WorkspaceAsyncSession
    from app.main import app
    from autoposter.config import settings
    monkeypatch.setattr(database,'SessionLocal',async_sessionmaker(db.bind,class_=WorkspaceAsyncSession,expire_on_commit=False))
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    char=models.Character(name='Synthetic');db.add(char);await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        mode=await client.get('/api/v1/characters/mode')
        assert mode.status_code==200 and mode.json()=={'enabled':True}
        chooser=await client.get('/characters')
        assert chooser.status_code==200 and 'character-workspace.js' in chooser.text
        for url in ('/','/autoposter/'):
            response=await client.get(url+f'?character_id={char.id}')
            assert response.status_code==200,response.text
            assert '/static/character-workspace.js' in response.text


@pytest.mark.asyncio
async def test_media_file_routes_authenticate_and_reject_cross_character_and_path_escape(db,tmp_path,monkeypatch):
    from app.character_http import CharacterMiddleware
    from autoposter.workspaces import WorkspaceAsyncSession,character_session
    from autoposter.config import settings
    monkeypatch.setattr(database,'SessionLocal',async_sessionmaker(db.bind,class_=WorkspaceAsyncSession,expire_on_commit=False))
    monkeypatch.setattr(settings,'media_root',tmp_path)
    monkeypatch.setattr(settings,'auth_enabled',True)
    asset=models.MediaAsset(filename='private.jpg',sha256='a'*64,storage_path='private.jpg')
    db.add(asset);await db.commit();(tmp_path/'private.jpg').write_bytes(b'private bytes')
    app=FastAPI();app.include_router(api_router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get(f'/api/v1/media/{asset.id}/file')).status_code==401
        assert (await client.get(f'/api/v1/media/{asset.id}/thumb')).status_code==401
    monkeypatch.setattr(settings,'auth_enabled',False)
    a,b=models.Character(name='Alpha'),models.Character(name='Beta');db.add_all([a,b]);await db.commit()
    async with character_session(db.bind,a.id) as session:
        own=models.MediaAsset(filename='own.jpg',sha256='b'*64,storage_path='own.jpg')
        unsafe=models.MediaAsset(filename='unsafe.jpg',sha256='c'*64,storage_path='../../../private.jpg')
        session.add_all([own,unsafe]);await session.commit();own_id,unsafe_id=own.id,unsafe.id
    directory=tmp_path/'characters'/str(a.id);directory.mkdir(parents=True,exist_ok=True)
    (directory/'own.jpg').write_bytes(b'Alpha private')
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    # Use a fresh ASGI object: Starlette disallows middleware changes after startup.
    app2=FastAPI();app2.include_router(api_router);app2.add_middleware(CharacterMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app2),base_url='http://test') as client:
        good=await client.get(f'/api/v1/media/{own_id}/file?character_id={a.id}')
        assert good.status_code==200 and good.content==b'Alpha private'
        assert 'private' in good.headers['cache-control']
        assert (await client.get(f'/api/v1/media/{own_id}/file?character_id={b.id}')).status_code==404
        assert (await client.get(f'/api/v1/media/{unsafe_id}/file?character_id={a.id}')).status_code==404


@pytest.mark.asyncio
async def test_character_form_redirect_retains_url_context(db,monkeypatch):
    from fastapi.responses import RedirectResponse
    from app.character_http import CharacterMiddleware
    from autoposter.workspaces import WorkspaceAsyncSession
    monkeypatch.setattr(database,'SessionLocal',async_sessionmaker(db.bind,class_=WorkspaceAsyncSession,expire_on_commit=False))
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    char=models.Character(name='Synthetic');db.add(char);await db.commit()
    app=FastAPI();app.add_middleware(CharacterMiddleware)
    @app.post('/save')
    def save():return RedirectResponse('/queue?msg=saved',303)
    @app.post('/external')
    def external():return RedirectResponse('https://provider.invalid/authorize?state=synthetic',303)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        result=await client.post(f'/save?character_id={char.id}')
        assert result.headers['location']==f'/queue?msg=saved&character_id={char.id}'
        external=await client.post(f'/external?character_id={char.id}')
        assert external.headers['location']=='https://provider.invalid/authorize?state=synthetic'


@pytest.mark.asyncio
async def test_form_redirect_retains_character_url_context(db,monkeypatch):
    from fastapi.responses import RedirectResponse
    from app.character_http import CharacterMiddleware
    from autoposter.workspaces import WorkspaceAsyncSession
    monkeypatch.setattr(database,'SessionLocal',async_sessionmaker(db.bind,class_=WorkspaceAsyncSession,expire_on_commit=False))
    char=models.Character(name='Alpha');db.add(char);await db.commit()
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    app=FastAPI();app.add_middleware(CharacterMiddleware)
    @app.post('/save')
    async def save():return RedirectResponse('/queue?msg=saved',303)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post(f'/save?character_id={char.id}')
        assert response.status_code==303
        assert f'character_id={char.id}' in response.headers['location']
        assert 'msg=saved' in response.headers['location']


@pytest.mark.asyncio
async def test_real_app_public_stylesheet_does_not_require_character_context(db, monkeypatch):
    from app.main import app
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/autoposter/app.css')
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/css')
        assert len(response.content) > 100
        for path in ('/autoposter/app.css/private', '/autoposter/api/v1/channels', '/autoposter/media-files/private'):
            assert (await client.get(path)).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['GET', 'HEAD'])
async def test_real_app_ownerless_entry_redirects_to_character_chooser(db, monkeypatch, method):
    from app.main import app
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.request(method, '/', follow_redirects=False)
        assert response.status_code == 307
        assert response.headers['location'] == '/characters'
        chooser = await client.get(response.headers['location'])
        assert chooser.status_code == 200
        assert 'character-workspace.js' in chooser.text
        assert (await client.get('/?character_id=')).status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path', [('POST', '/'), ('GET', '/chats'), ('GET', '/api/v1/media/private/thumb')])
async def test_entry_redirect_does_not_replace_owner_guards(db, monkeypatch, method, path):
    from app.main import app
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.request(method, path, follow_redirects=False)
        assert response.status_code == 409
        assert 'location' not in response.headers


@pytest.mark.asyncio
async def test_character_rename_preserves_identity_and_rejects_extra_fields(db, monkeypatch):
    from autoposter.workspaces import WorkspaceAsyncSession
    from autoposter.deps import require_admin
    first, second = models.Character(name='Alpha'), models.Character(name='Beta')
    db.add_all([first, second]); await db.commit()
    monkeypatch.setattr(database, 'SessionLocal', async_sessionmaker(db.bind, class_=WorkspaceAsyncSession, expire_on_commit=False))
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    app=FastAPI();app.include_router(api_router)
    from app.character_http import CharacterMiddleware
    app.add_middleware(CharacterMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response=await client.patch(f'/api/v1/characters/{first.id}',json={'name':'  Alpha neu  '})
        assert response.status_code==200
        assert response.json()['id']==str(first.id) and response.json()['name']=='Alpha neu'
        assert response.json()['planning_enabled'] is False and response.json()['chat_enabled'] is False
        listed=(await client.get('/api/v1/characters')).json()
        assert next(r for r in listed if r['id']==str(second.id))['name']=='Beta'
        for payload in ({'name':' '},{'name':'x'*121},{'name':'X','chat_enabled':True}):
            assert (await client.patch(f'/api/v1/characters/{first.id}',json=payload)).status_code==422
        assert (await client.patch(f'/api/v1/characters/{uuid.uuid4()}',json={'name':'X'})).status_code==404
        async def forbidden():
            from fastapi import HTTPException
            raise HTTPException(403,'Admin required')
        app.dependency_overrides[require_admin]=forbidden
        assert (await client.patch(f'/api/v1/characters/{first.id}',json={'name':'Blocked'})).status_code==403
