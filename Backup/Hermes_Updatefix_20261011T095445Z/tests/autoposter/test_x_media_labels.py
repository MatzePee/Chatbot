import json
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
import respx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from autoposter import schema_sync
from autoposter.adapters import x
from autoposter.adapters.base import Credentials, MediaRef
from autoposter.api.channels import update_channel
from autoposter.db import Base
from autoposter.models import Channel, ExternalMediaRef, Post
from autoposter.schemas import ChannelUpdate
from autoposter.services import media as media_service, planner, publisher
from tests.autoposter.test_duplicates import make_channel, make_image
from tests.autoposter.test_x_media_upload import asset, setup_upload


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('with_media', [False, True])
async def test_official_ai_label_only_on_enabled_media_posts(enabled, with_media):
    adapter = x.XAdapter()
    with respx.mock() as mock:
        tweet = mock.post(f'{adapter.base}/2/tweets').respond(201, json={'data': {'id': '123'}})
        refs = [MediaRef(asset_id='asset', external_id='456')] if with_media else []
        await adapter.publish(Credentials('fake'), Channel(handle='test', x_made_with_ai=enabled),
                              Post(body_text='A post', hashtags=[]), refs)
        payload = json.loads(tweet.calls.last.request.content)
        assert payload.get('made_with_ai') is (True if enabled and with_media else None)
        assert ('media' in payload) is with_media


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_alt_description_is_only_uploaded_when_enabled(enabled):
    adapter = x.XAdapter()
    with respx.mock(assert_all_called=False) as mock:
        setup_upload(mock, adapter)
        meta = mock.post(f'{adapter.base}/2/media/metadata').respond(200, json={'data': {}})
        source = asset()
        await adapter.upload_media(Credentials('fake'), source, b'image', include_alt_text=enabled)
        assert meta.called is enabled
        assert source.caption_hint == 'Bildbeschreibung'


@pytest.mark.asyncio
async def test_channel_settings_preserve_posts_and_invalidate_only_its_upload_cache(db):
    channel = await make_channel(db, 'X', 'x')
    other = await make_channel(db, 'Other', 'x')
    image = await media_service.ingest(db, filename='label.jpg', data=make_image(51))
    db.add_all([ExternalMediaRef(media_asset_id=image.id, channel_id=c.id, external_id=str(c.id))
                for c in [channel, other]])
    post = Post(channel_id=channel.id, body_text='Existing', status='published', external_post_id='published')
    db.add(post)
    await db.flush()
    updated = await update_channel(channel.id, ChannelUpdate(x_send_alt_text=False, x_made_with_ai=True), db, None)
    assert updated.x_send_alt_text is False and updated.x_made_with_ai is True
    refs = (await db.execute(select(ExternalMediaRef))).scalars().all()
    assert [r.channel_id for r in refs] == [other.id]
    await db.refresh(post)
    assert post.body_text == 'Existing' and post.external_post_id == 'published'
    assert post.status == 'published'
    updated = await update_channel(channel.id, ChannelUpdate(display_name='Renamed'), db, None)
    assert updated.x_send_alt_text is False and updated.x_made_with_ai is True
    await db.refresh(other)
    assert other.x_send_alt_text is True and other.x_made_with_ai is False


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', ['x', 'fanvue'])
async def test_upload_pipeline_passes_x_preference_without_changing_image_or_fanvue(db, platform):
    channel = await make_channel(db, 'Upload', platform)
    channel.x_send_alt_text = False
    image = await media_service.ingest(db, filename='original.jpg', data=make_image(52))
    original = media_service.read_bytes(image)
    upload = AsyncMock(return_value=MediaRef(asset_id=str(image.id), external_id='123'))
    await publisher._upload_all(db, SimpleNamespace(upload_media=upload), Credentials('fake'), channel, [image])
    assert upload.call_args.args[2] == original  # No watermark means unchanged bytes.
    assert upload.call_args.kwargs == ({'include_alt_text': False} if platform == 'x' else {})
    post = Post(channel_id=channel.id, body_text='Post', media_asset_ids=[str(image.id)])
    issues = await planner.preflight(db, post)
    if platform == 'x':
        assert not any(issue['code'] == 'missing_alt' for issue in issues)


@pytest.mark.asyncio
async def test_additive_upgrade_keeps_existing_channel_settings(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'old.db'))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as db:
            db.add(Channel(platform='x', display_name='Existing', oauth_client_id='keep-client', auto_plan_enabled=False))
            await db.commit()
        async with engine.begin() as conn:
            for column in ('x_send_alt_text', 'x_made_with_ai'):
                await conn.execute(text(f'ALTER TABLE channel DROP COLUMN {column}'))
            before = dict((await conn.execute(text('SELECT * FROM channel'))).mappings().one())
        report = await schema_sync.sync(engine)
        assert set(report['added_columns']) == {'channel.x_send_alt_text', 'channel.x_made_with_ai'}
        assert not report['failed']
        async with engine.connect() as conn:
            after = dict((await conn.execute(text('SELECT * FROM channel'))).mappings().one())
        assert {k: after[k] for k in before} == before
        assert after['x_send_alt_text'] == 1 and after['x_made_with_ai'] == 0
    finally:
        await engine.dispose()
