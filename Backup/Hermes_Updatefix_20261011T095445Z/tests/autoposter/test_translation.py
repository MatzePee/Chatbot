import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import select

from app.preview import PreviewMiddleware
from autoposter.api.posts import router
from autoposter.db import get_db
from autoposter.deps import require_editor
from autoposter.models import LlmUsage, Post
from autoposter.services import llm as service
from tests.autoposter.test_duplicates import make_channel


@pytest.mark.asyncio
async def test_translation_uses_existing_model_budget_and_leaves_post_unchanged(db, monkeypatch):
    channel = await make_channel(db, 'Translation test', 'x')
    post = Post(channel_id=channel.id, body_text='Original', status='scheduled')
    db.add(post)
    await db.flush()
    values = {'openrouter_api_key': 'fake', 'openrouter_model_text': 'test-model',
              'openrouter_model_fallback': 'fallback', 'openrouter_monthly_budget_usd': 10,
              'public_base_url': 'https://example.test'}
    monkeypatch.setattr(service, 'cfg', values.__getitem__)
    with respx.mock() as mock:
        request = mock.post(service.llm.base + '/chat/completions').respond(200, json={
            'choices': [{'message': {'content': 'Die Küste wurde still.'}, 'finish_reason': 'stop'}],
            'usage': {'total_cost': .001}})
        result = await service.llm.translate_german(db, 'The coast went quiet.')
        assert result == 'Die Küste wurde still.'
        sent = json.loads(request.calls.last.request.content)
        assert sent['model'] == 'test-model'
        assert sent['messages'][1] == {'role': 'user', 'content': 'The coast went quiet.'}
        assert 'Original' not in json.dumps(sent)
        assert post.body_text == 'Original' and post.status == 'scheduled'
        usage = (await db.execute(select(LlmUsage))).scalar_one()
        assert usage.task == 'translate_de' and float(usage.cost_usd) == pytest.approx(.001)
        values['openrouter_monthly_budget_usd'] = 0
        with pytest.raises(service.BudgetExceeded):
            await service.llm.translate_german(db, 'Hello')
        assert request.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('content,finish', [('', 'stop'), ('Nur ein Teil', 'length')])
async def test_empty_or_truncated_translation_is_not_shown_as_success(db, monkeypatch, content, finish):
    monkeypatch.setattr(service.llm, '_call', AsyncMock(return_value={
        'choices': [{'message': {'content': content}, 'finish_reason': finish}]}))
    with pytest.raises(RuntimeError, match='vollständige Übersetzung'):
        await service.llm.translate_german(db, 'Hello')


@pytest.mark.asyncio
@pytest.mark.parametrize('prefix', ['/api/v1', '/autoposter/api/v1'])
async def test_translation_preview_allows_only_translation_and_validates_input(db, monkeypatch, prefix):
    monkeypatch.setenv('MP_PREVIEW', '1')
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    app.add_middleware(PreviewMiddleware)
    async def database():
        yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_editor] = lambda: None
    translate = AsyncMock(return_value='Hallo Welt')
    monkeypatch.setattr(service.llm, 'translate_german', translate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.post(prefix + '/posts/translate', json={'text': 'Hello world'})
        assert response.status_code == 200 and response.json() == {'translation': 'Hallo Welt'}
        for text in ['', '   ', 'x' * 5001]:
            assert (await client.post(prefix + '/posts/translate', json={'text': text})).status_code == 422
        assert translate.await_count == 1
        assert (await client.post(prefix + '/posts/id/publish')).status_code == 403
        assert (await client.patch(prefix + '/posts/id', json={'body_text': 'changed'})).status_code == 403
        translate.side_effect = service.BudgetExceeded('Budget ausgeschöpft')
        assert (await client.post(prefix + '/posts/translate', json={'text': 'Hello'})).status_code == 429
        translate.side_effect = RuntimeError('Dienst nicht erreichbar')
        response = await client.post(prefix + '/posts/translate', json={'text': 'Hello'})
        assert response.status_code == 502
        assert 'Dienst nicht erreichbar' in response.json()['detail']


@pytest.mark.asyncio
async def test_generation_returns_and_persists_german_translation(db, monkeypatch):
    from autoposter.api.posts import generate
    from autoposter.schemas import GenerateRequest
    channel = await make_channel(db, 'Generated translation', 'x')
    post = Post(channel_id=channel.id, body_text='', status='needs_review')
    db.add(post)
    await db.flush()
    call = AsyncMock(return_value={
        'choices': [{'message': {'content': json.dumps({'variants': [{
            'text': 'The coast went quiet.', 'hashtags': ['coast'],
            'translation_de': 'Die Küste wurde still.'}]})}, 'finish_reason': 'stop'}],
        '_model': 'test-model', '_cost': .001})
    monkeypatch.setattr(service.llm, '_call', call)
    result = await generate(GenerateRequest(post_id=post.id, variants=1), db=db, _=None)
    assert post.body_text == 'The coast went quiet.'
    assert post.body_text_variants[0]['translation_de'] == 'Die Küste wurde still.'
    assert result.variants[0]['translation_de'] == 'Die Küste wurde still.'
    assert call.await_count == 1
    assert 'translation_de' in call.call_args.kwargs['messages'][1]['content']
    await db.flush()
    await db.refresh(post)
    assert post.body_text_variants[0]['translation_de'] == 'Die Küste wurde still.'


@pytest.mark.asyncio
@pytest.mark.parametrize('fails', [False, True])
async def test_generation_adds_missing_translation_without_losing_post(db, monkeypatch, fails):
    channel = await make_channel(db, 'Fallback translation', 'x')
    monkeypatch.setattr(service.llm, '_call', AsyncMock(return_value={
        'choices': [{'message': {'content': json.dumps({'variants': [{
            'text': 'A quiet evening.', 'hashtags': []}]})}, 'finish_reason': 'stop'}]}))
    translate = AsyncMock(return_value='Ein ruhiger Abend.')
    if fails:
        translate.side_effect = RuntimeError('Übersetzung momentan nicht erreichbar')
    monkeypatch.setattr(service.llm, 'translate_german', translate)
    result = await service.llm.generate_post_text(db, persona=None, platform='x',
                                                 channel_id=channel.id, image_descriptions=[], variants=1)
    assert result.variants[0]['text'] == 'A quiet evening.'
    if fails:
        assert 'translation_error' in result.variants[0]
    else:
        assert result.variants[0]['translation_de'] == 'Ein ruhiger Abend.'
    translate.assert_awaited_once_with(db, 'A quiet evening.')
