import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from pydantic import ValidationError

from autoposter.adapters.base import Credentials, PublishResult
from autoposter.adapters.x import XAdapter
from autoposter.config import settings
from autoposter.models import Post, XImageComment
from autoposter.services import image_comments as comments, publisher
from tests.autoposter.test_duplicates import make_channel, make_image
from autoposter.services import media as media_service


@pytest.mark.parametrize('payload', [
    {'enabled': True},
    {'enabled': True, 'text': 'Hi', 'url': 'javascript:alert(1)'},
    {'url': 'https://user:pass@example.com'},
    {'url': 'https://example.com/a b'},
    {'text': '🐈' * 140, 'url': 'https://example.com'},
])
def test_invalid_settings(payload):
    with pytest.raises(ValidationError):
        comments.CommentSettings(**payload)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv('MP_PREVIEW', '0')
    monkeypatch.setattr(settings, 'dry_run', False)
    monkeypatch.setattr(settings, 'global_pause', False)


async def sample(db, platform='x'):
    channel = await make_channel(db, 'Comment test', platform)
    post = Post(channel_id=channel.id, status='published', external_post_id='123', media_asset_ids=[])
    db.add(post)
    await db.flush()
    await comments.save_settings(db, comments.CommentSettings(enabled=True, text='Mehr von mir', url='https://example.com'))
    return post, channel


@pytest.mark.asyncio
async def test_reply_request_format():
    with respx.mock() as mock:
        adapter = XAdapter()
        mock.post(f'{adapter.base}/2/tweets').mock(return_value=httpx.Response(201, json={'data': {'id': '456'}}))
        result = await adapter.create_comment(Credentials(access_token='test'), '123', 'Hi\nhttps://example.com')
        assert result == '456'
        body = json.loads(mock.calls.last.request.content)
        assert body == {'text': 'Hi\nhttps://example.com', 'reply': {'in_reply_to_tweet_id': '123'}}
        assert 'media' not in body


@pytest.mark.asyncio
async def test_comment_sent_once(db, live, monkeypatch):
    post, channel = await sample(db)
    comment = await comments.prepare(db, post, channel, [SimpleNamespace(mime='image/jpeg')])
    await db.commit()
    send = AsyncMock(return_value='456')
    monkeypatch.setattr(comments, 'get_adapter', lambda _: SimpleNamespace(create_comment=send))
    monkeypatch.setattr(comments.credentials, 'get_valid_credentials', AsyncMock(return_value=Credentials('test')))
    await comments.deliver(db, post.id)
    await comments.deliver(db, post.id)
    assert send.await_count == 1
    assert comment.state == 'sent'
    assert post.generation_meta['x_image_comment']['external_id'] == '456'
    assert post.status == 'published'


@pytest.mark.asyncio
async def test_timeout_never_retries_or_republishes_image(db, live, monkeypatch):
    post, channel = await sample(db)
    await comments.prepare(db, post, channel, [SimpleNamespace(mime='image/jpeg')])
    await db.commit()
    send = AsyncMock(side_effect=httpx.ReadTimeout('timeout'))
    monkeypatch.setattr(comments, 'get_adapter', lambda _: SimpleNamespace(create_comment=send))
    monkeypatch.setattr(comments.credentials, 'get_valid_credentials', AsyncMock(return_value=Credentials('test')))
    await comments.deliver(db, post.id)
    await comments.deliver_pending(db)
    ok, message = await publisher.publish_post(db, post.id)
    assert ok and 'Bereits' in message
    assert post.status == 'published'
    assert post.generation_meta['x_image_comment']['state'] == 'unknown'
    assert send.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('platform,mime,preview,dry', [
    ('fanvue', 'image/jpeg', False, False),
    ('x', 'video/mp4', False, False),
    ('x', 'image/jpeg', True, False),
    ('x', 'image/jpeg', False, True),
])
async def test_exclusions(db, live, monkeypatch, platform, mime, preview, dry):
    post, channel = await sample(db, platform)
    monkeypatch.setenv('MP_PREVIEW', '1' if preview else '0')
    monkeypatch.setattr(settings, 'dry_run', dry)
    assert await comments.prepare(db, post, channel, [SimpleNamespace(mime=mime)]) is None
    assert await db.get(XImageComment, post.id) is None


@pytest.mark.asyncio
async def test_disabling_cancels_pending(db, live):
    post, channel = await sample(db)
    item = await comments.prepare(db, post, channel, [SimpleNamespace(mime='image/jpeg')])
    await comments.save_settings(db, comments.CommentSettings(enabled=False))
    await db.commit()
    await comments.deliver(db, post.id)
    assert item.state == 'cancelled'


@pytest.mark.asyncio
async def test_publisher_integrates_comment_after_image_success(db, live, monkeypatch):
    channel = await make_channel(db, 'Publish')
    asset = await media_service.ingest(db, filename='i.jpg', data=make_image(7))
    post = Post(channel_id=channel.id, status='scheduled', body_text='Image', media_asset_ids=[str(asset.id)])
    db.add(post)
    await db.flush()
    await comments.save_settings(db, comments.CommentSettings(enabled=True, text='Link', url='https://example.com'))
    send = AsyncMock(side_effect=httpx.ReadTimeout('uncertain reply'))
    adapter = SimpleNamespace(publish=AsyncMock(return_value=PublishResult(external_post_id='789')), create_comment=send)
    monkeypatch.setattr(publisher, 'get_adapter', lambda _: adapter)
    monkeypatch.setattr(comments, 'get_adapter', lambda _: adapter)
    monkeypatch.setattr(publisher, '_upload_all', AsyncMock(return_value=[]))
    monkeypatch.setattr(publisher.planner, 'preflight', AsyncMock(return_value=[]))
    monkeypatch.setattr(publisher.cred_service, 'get_valid_credentials', AsyncMock(return_value=Credentials('test')))
    ok, _ = await publisher.publish_post(db, post.id)
    assert ok
    await publisher.publish_post(db, post.id)
    assert adapter.publish.await_count == 1
    assert send.await_count == 1
    assert post.status == 'published'
    assert post.generation_meta['x_image_comment']['state'] == 'unknown'


@pytest.mark.asyncio
async def test_settings_save_in_preview_does_not_enable_sending(db, monkeypatch):
    from fastapi import FastAPI
    from autoposter.api.dashboard import router
    from autoposter.db import get_db
    from autoposter.deps import current_user, require_admin
    from app.preview import PreviewMiddleware
    monkeypatch.setenv('MP_PREVIEW', '1')
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.add_middleware(PreviewMiddleware)
    async def database():
        yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_user] = lambda: None
    app.dependency_overrides[require_admin] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        r = await client.post('/api/v1/settings/image-comment', json={'enabled': True, 'text': 'Mehr', 'url': 'https://example.com'})
        assert r.status_code == 200
        assert (await client.get('/api/v1/settings/image-comment')).json()['text'] == 'Mehr'
        assert (await client.post('/api/v1/settings/runtime', json={'dry_run': False})).status_code == 403
        assert (await client.post('/api/v1/posts/example/publish', json={})).status_code == 403
        assert (await client.post('/api/v1/settings/image-comment', json={'enabled': True, 'url': 'bad'})).status_code == 422
