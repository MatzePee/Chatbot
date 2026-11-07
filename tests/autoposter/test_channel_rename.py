"""Renaming a channel is metadata-only, with no provider traffic."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from autoposter.api import channels
from autoposter.models import Channel
from tests.autoposter.test_duplicates import make_channel

@pytest.mark.asyncio
@pytest.mark.parametrize('platform', ['fanvue', 'x', 'telegram'])
async def test_rename_preserves_channel_connection_and_settings(db, platform):
    channel = await make_channel(db, f'Old {platform}', platform)
    channel.handle = '@fixed-account'
    channel.is_active = True
    channel.auto_plan_enabled = False
    channel.health = 'ok'
    await db.flush()
    app = FastAPI()
    app.include_router(channels.router, prefix='/api/v1')
    async def session(): yield db
    app.dependency_overrides[channels.get_db] = session
    for dependency in (channels.current_user, channels.require_editor):
        app.dependency_overrides[dependency] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.patch(f'/api/v1/channels/{channel.id}', json={'display_name': f'New {platform}'})
    assert response.status_code == 200, response.text
    assert response.json()['display_name'] == f'New {platform}'
    await db.refresh(channel)
    assert channel.display_name == f'New {platform}'
    assert channel.handle == '@fixed-account'
    assert channel.is_active is True
    assert channel.auto_plan_enabled is False
    assert channel.health == 'ok'
