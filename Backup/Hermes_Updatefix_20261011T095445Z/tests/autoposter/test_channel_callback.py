"""Channel callback editing uses the saved URL throughout OAuth, without live requests."""
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from autoposter.api import channels
from autoposter.adapters.base import Credentials
from autoposter.models import Channel
from autoposter.services import oauthapp
from autoposter.services.appconfig import _snapshot


@pytest.fixture
def callback_app(db):
    app = FastAPI()
    app.include_router(channels.router, prefix='/api/v1')
    async def session():
        yield db
    app.dependency_overrides[channels.get_db] = session
    for dependency in (channels.current_user, channels.require_admin, channels.require_editor):
        app.dependency_overrides[dependency] = lambda: None
    return app


@pytest.mark.asyncio
async def test_saved_callback_is_used_for_authorization_and_token_exchange(callback_app, db, monkeypatch):
    async with AsyncClient(transport=ASGITransport(app=callback_app), base_url='http://test') as client:
        created = await client.post('/api/v1/channels', json={
            'platform': 'x', 'display_name': 'Sabrina',
            'oauth_client_id': 'TEST-CLIENT', 'oauth_client_secret': 'TEST-SECRET',
        })
        assert created.status_code == 201, created.text
        channel_id = created.json()['id']
        channel = await db.get(Channel, UUID(channel_id))
        saved_secret = channel.oauth_client_secret_enc
        uri = 'http://192.0.2.24:8000/api/v1/oauth/x/callback'
        updated = await client.patch(f'/api/v1/channels/{channel_id}', json={'oauth_redirect_base': uri})
        assert updated.status_code == 200, updated.text
        assert updated.json()['redirect_uri'] == uri
        assert updated.json()['oauth_redirect_base'] == 'http://192.0.2.24:8000'
        assert channel.oauth_client_secret_enc == saved_secret

        started = await client.get('/api/v1/oauth/x/start', params={'channel_id': channel_id})
        assert started.status_code == 200, started.text
        auth = started.json()
        assert parse_qs(urlsplit(auth['authorize_url']).query)['redirect_uri'] == [uri]
        assert auth['redirect_uri'] == uri
        exchanged = []
        async def exchange(app, code, verifier):
            exchanged.append((app.redirect_uri('x'), app.client_id, app.client_secret))
            return Credentials(access_token='TEST-ACCESS', refresh_token='TEST-REFRESH')
        monkeypatch.setattr(channels.get_adapter('x'), 'exchange_code', exchange)
        result = await client.get('/api/v1/oauth/x/callback', params={'code': 'TEST-CODE', 'state': auth['state']})
        assert result.status_code == 200, result.text
        assert exchanged == [(uri, 'TEST-CLIENT', 'TEST-SECRET')]
        credential_id = channel.credential_id
        assert credential_id is not None

        # Changing the address never disconnects an already linked account.
        updated = await client.patch(f'/api/v1/channels/{channel_id}', json={'oauth_redirect_base': 'https://creator.example.test/studio/'})
        assert updated.json()['redirect_uri'] == 'https://creator.example.test/studio/api/v1/oauth/x/callback'
        assert channel.credential_id == credential_id
        assert channel.oauth_client_secret_enc == saved_secret
        monkeypatch.setitem(_snapshot, 'public_base_url', 'https://global.example.test')
        cleared = await client.patch(f'/api/v1/channels/{channel_id}', json={'oauth_redirect_base': ''})
        assert cleared.json()['redirect_uri'] == 'https://global.example.test/api/v1/oauth/x/callback'


@pytest.mark.parametrize('value', [
    'localhost:8000', 'javascript:alert(1)', 'https://user:pass@example.test',
    'https://example.test/#fragment', 'https://example.test/?token=value',
    'http://example.test:99999', 'http://exa mple.test',
    'https://example.test/api/v1/oauth/fanvue/callback',
])
@pytest.mark.asyncio
async def test_invalid_callback_is_rejected_before_creating_channel(callback_app, value):
    async with AsyncClient(transport=ASGITransport(app=callback_app), base_url='http://test') as client:
        result = await client.post('/api/v1/channels', json={
            'platform': 'x', 'display_name': 'Invalid', 'oauth_redirect_base': value,
        })
        assert result.status_code == 422
        assert (await client.get('/api/v1/channels')).json() == []


@pytest.mark.asyncio
async def test_fanvue_still_uses_shared_callback(callback_app):
    from app import db as chatbot_db
    uri = 'https://shared.example.test/oauth/callback'
    chatbot_db.set_setting('fanvue_redirect_uri', uri)
    async with AsyncClient(transport=ASGITransport(app=callback_app), base_url='http://test') as client:
        result = await client.post('/api/v1/channels', json={
            'platform': 'fanvue', 'display_name': 'Shared',
            'oauth_redirect_base': 'https://channel.example.test',
        })
        assert result.status_code == 201, result.text
        assert result.json()['redirect_uri'] == uri
        assert result.json()['connection_source'] == 'chatbot'
