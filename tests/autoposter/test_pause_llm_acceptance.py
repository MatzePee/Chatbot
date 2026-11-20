"""Real AutoPost LLM clients with file SQLite, synthetic responses, no network."""
import httpx
import pytest
from uuid import uuid4
from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine
from autoposter import db as database, models
from autoposter.workspaces import character_context
from autoposter.services import llm, runtime
from autoposter.adapters.base import ExternalActionBlocked


@pytest.fixture
async def llm_store(tmp_path, monkeypatch):
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'llm.db'))
    owners=[uuid4() for _ in range(6)]
    async with engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        await conn.execute(models.Character.__table__.insert(),
                           [{'id':o,'name':f'Synthetic {i}'} for i,o in enumerate(owners)])
        for owner in owners:
            with character_context(owner):
                await conn.execute(models.AppSetting.__table__.insert().values(
                    key='runtime',value={'global_pause':False,'dry_run':False}))
    monkeypatch.setattr(database,'engine',engine)
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    old_cfg=llm.cfg
    monkeypatch.setattr(llm,'cfg',lambda key: {'openrouter_api_key':'synthetic-secret',
        'openrouter_monthly_budget_usd':100,'openrouter_model_fallback':'synthetic-fallback',
        'openrouter_model_text':'synthetic'}.get(key,old_cfg(key)))
    try:
        yield engine,owners
    finally:
        await engine.dispose()


async def _save(engine, owner, pause=False, dry=False):
    with character_context(owner):
        async with engine.begin() as conn:
            await conn.execute(update(models.AppSetting.__table__).where(
                models.AppSetting.key=='runtime').values(value={'global_pause':pause,'dry_run':dry}))


def _client(monkeypatch, handler):
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs: original(
        **kwargs,transport=httpx.MockTransport(handler),trust_env=False))


@pytest.mark.asyncio
async def test_llm_rejects_unknown_owner_before_content_transport(llm_store, monkeypatch):
    engine,_=llm_store
    sent=[]
    async def handler(request):
        sent.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    _client(monkeypatch,handler)
    with character_context(uuid4()):
        async with database.SessionLocal() as session:
            with pytest.raises(ExternalActionBlocked):
                await llm.LlmClient()._call(session,task='text',model='synthetic',messages=[])
    assert sent==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('first_response', ['fallback','reasoning'])
async def test_automatic_planning_pause_committed_before_next_llm_request(llm_store,monkeypatch,first_response):
    engine,owners=llm_store
    owner=owners[0]
    sent=[]
    async def handler(request):
        sent.append(request)
        await _save(engine,owner,pause=True)
        if first_response=='reasoning':
            return httpx.Response(400,text='reasoning effort unsupported')
        return httpx.Response(503,text='synthetic failure')
    _client(monkeypatch,handler)
    with character_context(owner):
        async with database.SessionLocal() as session:
            await runtime.load(session)
            assert runtime.runtime.global_pause is False
            async with runtime.autopost_automation():
                with pytest.raises(ExternalActionBlocked):
                    await llm.LlmClient()._call(session,task='text',model='synthetic',
                                              messages=[],no_reasoning=True)
    assert len(sent)==1, 'no reasoning retry or fallback after committed stop'


@pytest.mark.asyncio
async def test_six_owner_llm_pause_isolation_and_dry_run_processing(llm_store,monkeypatch):
    engine,owners=llm_store
    sent=[]
    async def handler(request):
        sent.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    _client(monkeypatch,handler)
    await _save(engine,owners[0],pause=True)
    await _save(engine,owners[1],dry=True)
    for i,owner in enumerate(owners):
        with character_context(owner):
            async with database.SessionLocal() as session:
                async with runtime.autopost_automation():
                    if i==0:
                        with pytest.raises(ExternalActionBlocked):
                            await llm.LlmClient()._call(session,task='text',model='synthetic',messages=[])
                    else:
                        await llm.LlmClient()._call(session,task='text',model='synthetic',messages=[])
    assert len(sent)==5


@pytest.mark.asyncio
async def test_publication_pause_preserves_manual_tools_and_explicit_setup(llm_store,monkeypatch):
    engine,owners=llm_store
    await _save(engine,owners[0],pause=True,dry=True)
    sent=[]
    async def handler(request):
        sent.append(request.url.path)
        if request.method=='GET':
            return httpx.Response(200,json={'data':[]})
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    _client(monkeypatch,handler)
    with character_context(owners[0]):
        async with database.SessionLocal() as session:
            await llm.LlmClient()._call(session,task='translate_de',model='synthetic',messages=[])
            result=await llm.test_connection(session)
            assert result['ok']
            await llm.list_models(session,refresh=True)
    assert len(sent)==3
