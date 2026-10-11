from datetime import datetime, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from autoposter.api.posts import bulk_generate
from autoposter.models import Post
from autoposter.schemas import AssignmentBatch, PostBulkGenerate
from autoposter.services import assignment, media as media_service, planner
from autoposter.services.llm import LlmResult
from tests.autoposter.test_duplicates import make_channel, make_image


def generated_text():
    return LlmResult(variants=[{
        'text': 'A moment to remember.', 'hashtags': [],
        'translation_de': 'Ein Moment zum Erinnern.',
    }], model='test-model', cost_usd=0, raw={})


@pytest.mark.asyncio
@pytest.mark.parametrize('platform,kind', [('x', 'text'), ('x', 'image'), ('fanvue', 'image')])
@pytest.mark.parametrize('month', [1, 7])
async def test_fill_generates_and_persists_posts_with_each_channels_local_time(db, monkeypatch, platform, kind, month):
    # Exercise the real non-dry-run fill path; only slots and the external LLM are fixed.
    slot = datetime(2027, month, 15, 23, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(planner, 'typed_slots', lambda *args: [(slot, kind)])
    generated = AsyncMock(return_value=generated_text())
    monkeypatch.setattr(planner.llm, 'generate_post_text', generated)
    channel_zones = {}
    for index, zone in enumerate(['Europe/Berlin', 'America/New_York', '']):
        channel = await make_channel(db, f'Zone {index}', platform)
        channel.timezone = zone
        channel.policy.auto_approve = True
        channel_zones[channel.id] = zone or 'UTC'
        if kind == 'image':
            asset = await media_service.ingest(db, filename=f'zone-{index}.jpg', data=make_image(81 + index))
            asset.ai_description = 'A red dress against a white wall.'
            await assignment.assign(db, AssignmentBatch(asset_ids=[asset.id], channel_ids=[channel.id]))
    await db.flush()

    result = await planner.fill_calendar(db, channel_ids=list(channel_zones), dry_run=False)
    assert len(result.created_post_ids) == 3
    assert not result.gaps
    assert generated.await_count == 3
    for call in generated.await_args_list:
        args = call.kwargs
        expected_zone = channel_zones[args['channel_id']]
        assert args['when_local'].tzinfo.key == expected_zone
        assert args['when_local'].isoformat() == slot.astimezone(ZoneInfo(expected_zone)).isoformat()
        assert args['has_image'] is (kind == 'image')

    await db.flush()
    posts = (await db.execute(select(Post).where(Post.id.in_(result.created_post_ids)))).scalars().all()
    for post in posts:
        assert post.body_text == 'A moment to remember.'
        assert post.body_text_variants[0]['translation_de'] == 'Ein Moment zum Erinnern.'
        assert post.scheduled_at == slot
        assert post.status == 'scheduled'
        assert 'error' not in post.generation_meta
        assert bool(post.media_asset_ids) is (kind == 'image')


@pytest.mark.asyncio
async def test_bulk_regeneration_repairs_empty_posts_without_replanning(db, monkeypatch):
    channel = await make_channel(db, 'Repair', 'x')
    slot = datetime(2027, 7, 15, 12, tzinfo=timezone.utc)
    broken = Post(channel_id=channel.id, body_text='', type='text_only', status='needs_review',
                  scheduled_at=slot, generation_meta={'error': "Textgenerierung fehlgeschlagen: name 'tz' is not defined"})
    good = Post(channel_id=channel.id, body_text='Keep existing text', status='scheduled', scheduled_at=slot)
    db.add_all([broken, good])
    await db.flush()
    broken_id = broken.id
    monkeypatch.setattr(planner.llm, 'generate_post_text', AsyncMock(return_value=generated_text()))
    result = await bulk_generate(PostBulkGenerate(post_ids=[broken.id]), db, None)
    assert result['generated'] == 1 and result['failed'] == 0
    await db.refresh(broken)
    await db.refresh(good)
    assert broken.id == broken_id and broken.scheduled_at == slot
    assert broken.body_text == 'A moment to remember.'
    assert broken.status == 'needs_review'
    assert 'error' not in broken.generation_meta
    assert good.body_text == 'Keep existing text' and good.status == 'scheduled'
    assert len((await db.execute(select(Post))).scalars().all()) == 2
