"""External AutoPost alerts are reserved for a genuinely failed post."""
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from autoposter.models import Notification, Post
from autoposter.services import notify
from tests.autoposter.test_duplicates import make_channel

@pytest.mark.asyncio
@pytest.mark.parametrize('platform', ['fanvue', 'x', 'telegram'])
async def test_token_warning_never_sends_external_alert(db, monkeypatch, platform):
    channel = await make_channel(db, f'{platform} alerts', platform)
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(notify, '_fanout', send)
    for _ in range(2):
        await notify.push(db, level='error', title=f'Token-Problem: {platform}',
                          body='Verbindung prüfen', entity='channel', entity_id=str(channel.id))
    assert send.await_count == 0
    assert len((await db.execute(select(Notification))).scalars().all()) == 2

@pytest.mark.asyncio
@pytest.mark.parametrize('platform', ['fanvue', 'x', 'telegram'])
@pytest.mark.parametrize('status, expected', [('failed', 1), ('scheduled', 0), ('published', 0)])
async def test_external_alert_only_for_failed_post(db, monkeypatch, platform, status, expected):
    channel = await make_channel(db, f'{platform} send', platform)
    post = Post(channel_id=channel.id, status=status, body_text='synthetic')
    db.add(post)
    await db.flush()
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(notify, '_fanout', send)
    await notify.push(db, level='error', title=f'Veröffentlichung fehlgeschlagen: {platform}',
                      body='synthetic error', entity='post', entity_id=str(post.id))
    assert send.await_count == expected
