"""Actual settings routes must keep role guards ahead of side effects."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_settings_mutations_preserve_real_admin_editor_guards(db, monkeypatch):
    from autoposter.api import dashboard, system, channels, media
    from autoposter.config import settings
    from autoposter.db import get_db
    from autoposter.models import User
    from autoposter.security import create_token
    from autoposter.services import appconfig
    monkeypatch.setattr(settings,'auth_enabled',True)
    app=FastAPI()
    for module in (dashboard,system,channels,media):app.include_router(module.router)
    async def session():yield db
    app.dependency_overrides[get_db]=session
    users={}
    for role in ('viewer','editor'):
        user=User(email=role+'@settings.invalid',password_hash='unused',role=role)
        db.add(user);await db.flush();users[role]=user
    await db.commit()
    # Every action below has an admin guard, including provider-read previews.
    actions=[('/settings/runtime',{}),('/settings/integrations',{'auto_plan_days':3}),
             ('/settings/integrations/clear',{'field':'openrouter_api_key'}),
             ('/settings/openrouter/test',{}),('/settings/visibility-aging/run',{}),
             ('/settings/ppv-restore/preview',{}),('/settings/ppv-restore/run',{}),
             ('/settings/post-retention/preview',{}),('/settings/post-retention/run',{}),
             ('/system/character-upgrade',{'name':'Cherry','confirmed':True}),
             ('/system/update',{}),('/system/restart',{}),('/system/publish',{}),
             ('/system/publish/settings',{})]
    before=(await appconfig.get_all(db,use_cache=False)).copy()
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        for path,payload in actions:
            assert (await client.post(path,json=payload)).status_code==401,path
            for role in users:
                headers={'Authorization':'Bearer '+create_token(str(users[role].id))}
                response=await client.post(path,json=payload,headers=headers)
                assert response.status_code==403,(role,path,response.text)
        assert (await client.post('/system/update-check',headers={'Authorization':'Bearer '+create_token(str(users['viewer'].id))})).status_code==403
    assert await appconfig.get_all(db,use_cache=False)==before
