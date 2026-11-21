import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from autoposter.services import llm
from autoposter.models import Persona
from autoposter.schemas import PersonaCreate, PersonaUpdate, PersonaOut
from autoposter.persona_schema import upgrade
from sqlalchemy import text

@pytest.mark.asyncio
@pytest.mark.parametrize('enabled',[True,False])
@pytest.mark.parametrize('pool',[[],['pooltag']])
@pytest.mark.parametrize('has_image',[True,False])
async def test_generated_list_respects_switch_and_caption_keeps_its_hashtags(monkeypatch,enabled,pool,has_image):
    persona=SimpleNamespace(id='persona',system_prompt='custom',media_system_prompt='custom',daily_rhythm='',model_override=None,temperature=.9,forbidden_topics=[],hashtag_pool=pool,generate_separate_hashtags=enabled)
    caption='My caption #captiontag'
    payload={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'variants':[{'text':caption,'hashtags':['a','b','c','d'],'translation_de':'Deutsche Lesehilfe'}]})}}]}
    client=llm.LlmClient.__new__(llm.LlmClient)
    client._call=AsyncMock(return_value=payload)
    monkeypatch.setattr(llm,'_recent_texts',AsyncMock(return_value=[]));monkeypatch.setattr(llm,'_few_shots',AsyncMock(return_value=[]))
    monkeypatch.setattr(llm,'build_system_prompt',lambda *a,**k:'custom prompt');monkeypatch.setattr(llm,'cfg',lambda key:'synthetic-model')
    result=await client.generate_post_text(None,persona=persona,platform='instagram',channel_id=None,image_descriptions=['A portrait'] if has_image else [],has_image=has_image,max_chars=2200)
    assert result.variants[0]['text']==caption
    assert result.variants[0]['hashtags']==(['a','b','c'] if enabled else [])
    messages=client._call.call_args.kwargs['messages'][1]['content']
    assert ('Setze hashtags auf []' in messages) is (not enabled)

@pytest.mark.asyncio
async def test_preference_persists_and_patch_omission_preserves_false(db):
    persona=Persona(**PersonaCreate(name='Test',generate_separate_hashtags=False).model_dump(),slug='test')
    db.add(persona);await db.commit();await db.refresh(persona)
    assert PersonaOut.model_validate(persona).generate_separate_hashtags is False
    assert 'generate_separate_hashtags' not in PersonaUpdate(name='Rename').model_dump(exclude_unset=True)
    assert PersonaCreate(name='Default').generate_separate_hashtags is True

