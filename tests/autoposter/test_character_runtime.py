from uuid import uuid4
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from autoposter import db as database, models
from autoposter.api import api_router
from autoposter.config import settings
from autoposter.workspaces import WorkspaceAsyncSession
from app.character_http import CharacterMiddleware


@pytest.mark.asyncio
async def test_runtime_http_does_not_mutate_global_or_other_character(db, monkeypatch):
    a,b = models.Character(name='Alpha'),models.Character(name='Beta')
    db.add_all([a,b]); await db.commit()
    original = (settings.dry_run, settings.global_pause)
    monkeypatch.setattr(database, 'SessionLocal', async_sessionmaker(db.bind, class_=WorkspaceAsyncSession, expire_on_commit=False))
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    app = FastAPI(); app.include_router(api_router); app.add_middleware(CharacterMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(f'/api/v1/settings/runtime?character_id={a.id}', json={'dry_run':False,'global_pause':False})
        assert response.status_code == 200, response.text
        assert (settings.dry_run, settings.global_pause) == original
        beta = (await client.get(f'/api/v1/settings?character_id={b.id}')).json()
        assert beta['dry_run'] is True and beta['global_pause'] is True
        alpha = (await client.get(f'/api/v1/settings?character_id={a.id}')).json()
        assert alpha['dry_run'] is False and alpha['global_pause'] is False


@pytest.mark.asyncio
async def test_new_character_does_not_inherit_personal_x_credentials(db, monkeypatch):
    from autoposter.services import appconfig
    from autoposter.workspaces import character_context, character_session
    char = models.Character(name='Empty'); db.add(char); await db.commit()
    monkeypatch.setattr(settings, 'x_client_id', 'legacy-account-client')
    monkeypatch.setattr(settings, 'x_client_secret', 'synthetic-legacy-secret')
    with character_context(char.id):
        assert appconfig.current('x_client_id') in (None, '')
        assert appconfig.current('x_client_secret') in (None, '')
    async with character_session(db.bind, char.id) as scoped:
        values = await appconfig.get_all(scoped, use_cache=False)
        assert values['x_client_id'] in (None, '')
        assert values['x_client_secret'] in (None, '')
