import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from autoposter.db import Base
from autoposter.models import Persona, PersonaExample
from autoposter.schemas import PersonaCreate, PersonaUpdate, PersonaExampleBulk
from autoposter.api.channels import create_persona, update_persona, add_examples
from autoposter.services import llm as service
from autoposter import schema_sync


@pytest.mark.asyncio
async def test_separate_prompt_settings_and_examples_keep_existing_text_data(db):
    p = await create_persona(PersonaCreate(name='Test', system_prompt='Text rules', daily_rhythm='Original rhythm'), db, None)
    updated = await update_persona(p.id, PersonaUpdate(media_system_prompt='Image rules'), db, None)
    assert updated.system_prompt == 'Text rules' and updated.daily_rhythm == 'Original rhythm'
    assert updated.media_system_prompt == 'Image rules'
    first = await add_examples(p.id, PersonaExampleBulk(text='Same example', image_description=''), db, None)
    second = await add_examples(p.id, PersonaExampleBulk(text='Same example', image_description='Bildpost'), db, None)
    assert len(first) == len(second) == 1
    assert await add_examples(p.id, PersonaExampleBulk(text='Same example', image_description='Bildpost'), db, None) == []
    assert len((await db.execute(select(PersonaExample))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_examples_never_fall_back_to_the_other_post_type(db):
    persona = Persona(name='Example', slug='example')
    db.add(persona)
    await db.flush()
    db.add(PersonaExample(persona_id=persona.id, platform='x', text='Text only example', image_description='', rating=1))
    await db.flush()
    assert await service._few_shots(db, persona.id, 'x', with_image=True) == []
    assert await service._few_shots(db, persona.id, 'x', with_image=False) == ['Text only example']
    db.add(PersonaExample(persona_id=persona.id, platform='x', text='Image only example', image_description='Image', rating=1))
    await db.flush()
    assert await service._few_shots(db, persona.id, 'x', with_image=True) == ['Image only example']
    assert await service._few_shots(db, persona.id, 'fanvue', with_image=True) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', ['x', 'fanvue'])
@pytest.mark.parametrize('has_image,descriptions', [(False, []), (True, ['Rotes Kleid vor einer weißen Wand.']), (True, [])])
async def test_generation_selects_correct_prompt_examples_model_and_rhythm(db, monkeypatch, platform, has_image, descriptions):
    persona = Persona(name='Test', slug='test', system_prompt='ONLY TEXT RULES', media_system_prompt='ONLY MEDIA RULES',
                      daily_rhythm='PRIVATE RHYTHM', language='en')
    db.add(persona)
    await db.flush()
    for image, value in [('', 'TEXT EXAMPLE'), ('Image', 'MEDIA EXAMPLE')]:
        db.add(PersonaExample(persona_id=persona.id, platform=platform, text=value, image_description=image, rating=1))
    await db.flush()
    rhythm = Mock(return_value='INJECTED DAILY ACTIVITY')
    monkeypatch.setattr(service.rhythm, 'prompt_fragment', rhythm)
    call = AsyncMock(return_value={'choices': [{'message': {'content': json.dumps({'variants': [{
        'text': 'A red dress against a white wall.', 'hashtags': [], 'translation_de': 'Ein rotes Kleid vor einer weißen Wand.'}]})},
        'finish_reason': 'stop'}]})
    monkeypatch.setattr(service.llm, '_call', call)
    monkeypatch.setattr(service, 'cfg', lambda key: {'openrouter_model_caption': 'IMAGE MODEL', 'openrouter_model_text': 'TEXT MODEL'}[key])
    await service.llm.generate_post_text(db, persona=persona, platform=platform, channel_id=None,
        image_descriptions=descriptions, has_image=has_image, theme='DAY THEME', when_local=datetime.now(timezone.utc), variants=1)
    args = call.call_args.kwargs
    prompt = '\n'.join(m['content'] for m in args['messages'])
    assert args['model'] == ('IMAGE MODEL' if has_image else 'TEXT MODEL')
    assert ('ONLY MEDIA RULES' in prompt) is has_image
    assert ('ONLY TEXT RULES' in prompt) is not has_image
    assert ('MEDIA EXAMPLE' in prompt) is has_image
    assert ('TEXT EXAMPLE' in prompt) is not has_image
    assert ('INJECTED DAILY ACTIVITY' in prompt) is not has_image
    assert ('DAY THEME' in prompt) is not has_image
    assert rhythm.call_count == (0 if has_image else 1)
    if has_image:
        assert 'Verwende keine Bezüge zur Tageszeit' in prompt
        assert 'Uhrzeit als Einstieg' not in prompt


@pytest.mark.asyncio
async def test_additive_schema_upgrade_preserves_all_previous_persona_values(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'old.db'))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine)
        async with sessions() as session:
            session.add(Persona(name='Existing', slug='existing', system_prompt='Keep my original prompt',
                                daily_rhythm='Keep rhythm', bio='Keep biography', tone_guidelines='Keep tone'))
            await session.commit()
        async with engine.begin() as conn:
            await conn.execute(text('ALTER TABLE persona DROP COLUMN media_system_prompt'))
            before = (await conn.execute(text('SELECT * FROM persona'))).mappings().all()
        report = await schema_sync.sync(engine)
        assert report['added_columns'] == ['persona.media_system_prompt']
        async with engine.connect() as conn:
            after = (await conn.execute(text('SELECT * FROM persona'))).mappings().all()
        assert [{k: row[k] for k in before[0]} for row in after] == [dict(row) for row in before]
        assert after[0]['media_system_prompt'] == ''
    finally:
        await engine.dispose()
