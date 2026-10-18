import json
import pytest
import httpx
from autoposter.adapters import get_adapter
from autoposter.adapters.base import Credentials
from autoposter.models import Channel, Post
from autoposter.services import planner
from autoposter.services.llm import build_system_prompt
from .test_telegram import no_network
from .test_telegram_publish import credential

@pytest.mark.asyncio
async def test_telegram_includes_hashtags_without_truncation(monkeypatch):
    seen=[]
    def handler(req):
        seen.append(json.loads(req.content));return httpx.Response(200,json={'ok':True,'result':{'message_id':1,'chat':{'id':-100456}}})
    monkeypatch.setattr('autoposter.adapters.telegram.http_client',lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await get_adapter('telegram').publish(credential(),Channel(handle='@sample_channel'),Post(body_text='Own text',hashtags=['hello','#world']),[])
    assert seen[0]['text']=='Own text\n\n#hello #world'
    assert 'Telegram' in build_system_prompt(None,'telegram')
    assert '280' not in build_system_prompt(None,'telegram')

@pytest.mark.asyncio
async def test_telegram_generation_has_own_caption_budget(db,monkeypatch):
    from autoposter.api.posts import generate
    from autoposter.schemas import GenerateRequest
    from autoposter.services.llm import llm
    from .test_telegram_pipeline import setup
    channel,post,assets=await setup(db,True)
    # Existing description avoids any vision request.
    assets[0].ai_description='Synthetic blue image';db.add(assets[0]);await db.flush()
    seen=[]
    async def variants(*args,**kwargs):
        seen.append(kwargs)
        from types import SimpleNamespace
        return SimpleNamespace(variants=[{'text':'Own Telegram caption','hashtags':[]}],model='synthetic',cost_usd=0)
    monkeypatch.setattr(llm,'generate_post_text',variants)
    await generate(GenerateRequest(channel_id=channel.id,post_id=post.id),db,None)
    assert seen[0]['platform']=='telegram' and seen[0]['max_chars']==1024
