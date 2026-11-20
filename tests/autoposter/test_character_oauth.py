from urllib.parse import urlsplit, parse_qs
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import select
from autoposter import db as database, models
from autoposter.api import api_router, channels
from autoposter.adapters.base import Credentials, AccountInfo
from autoposter.workspaces import WorkspaceAsyncSession, current_character, character_context, character_session
from app.character_http import CharacterMiddleware


@pytest.mark.asyncio
async def test_x_oauth_callback_recovers_original_owner_without_tab_context(db, monkeypatch):
    a,b = models.Character(name='Alpha'),models.Character(name='Beta')
    db.add_all([a,b]); await db.commit()
    monkeypatch.setattr(database,'SessionLocal',async_sessionmaker(db.bind,class_=WorkspaceAsyncSession,expire_on_commit=False))
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    async with character_session(db.bind,a.id) as session:
        channel = models.Channel(platform='x', display_name='Alpha', handle='@alpha', oauth_client_id='synthetic-client')
        session.add(channel); await session.flush(); channel_id = channel.id
    app=FastAPI(); app.include_router(api_router); app.add_middleware(CharacterMiddleware)
    calls=[]
    async def exchange(app, code, verifier):
        calls.append(current_character()); return Credentials('synthetic-access',refresh_token='synthetic-refresh')
    async def verify(app, credential): return AccountInfo(ok=True,account_id='alpha-account',username='alpha')
    monkeypatch.setattr(channels.get_adapter('x'),'exchange_code',exchange)
    monkeypatch.setattr(channels.get_adapter('x'),'verify',verify)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        started=await client.get(f'/api/v1/oauth/x/start?character_id={a.id}&channel_id={channel_id}')
        assert started.status_code==200,started.text
        state=started.json()['state']
        result=await client.get('/api/v1/oauth/x/callback',params={'state':state,'code':'synthetic-code','character_id':str(b.id)})
        assert result.status_code==200,result.text
        assert calls==[a.id]
        replay=await client.get('/api/v1/oauth/x/callback',params={'state':state,'code':'synthetic-code'})
        assert replay.status_code==400
        assert calls==[a.id]
    async with character_session(db.bind,a.id) as session:
        assert (await session.get(models.Channel,channel_id)).credential_id is not None
    async with character_session(db.bind,b.id) as session:
        assert list((await session.scalars(select(models.ChannelCredential))).all())==[]


@pytest.mark.asyncio
async def test_autochat_oauth_two_tabs_keep_original_verifier_and_owner(db, monkeypatch):
    from app import main, db as chat_db
    a,b=models.Character(name='Alpha'),models.Character(name='Beta')
    db.add_all([a,b]); await db.commit()
    monkeypatch.setattr(database,'SessionLocal',async_sessionmaker(db.bind,class_=WorkspaceAsyncSession,expire_on_commit=False))
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    for owner in (a.id,b.id):
        with character_context(owner):
            chat_db.set_setting('fanvue_client_id','client-'+str(owner))
            chat_db.set_setting('fanvue_redirect_uri','http://test/oauth/callback')
    observed=[]
    def exchange(code, verifier):
        observed.append((current_character(),verifier))
        chat_db.save_tokens(access_token='synthetic-access',refresh_token='synthetic-refresh',expires_at=99999999999)
    monkeypatch.setattr(main.fanvue,'exchange_code',exchange)
    async with AsyncClient(transport=ASGITransport(app=main.app),base_url='http://test') as client:
        first=await client.get('/oauth/start',params={'character_id':str(a.id)})
        state_a=parse_qs(urlsplit(first.headers['location']).query)['state'][0]
        second=await client.get('/oauth/start',params={'character_id':str(b.id)})
        state_b=parse_qs(urlsplit(second.headers['location']).query)['state'][0]
        assert state_a!=state_b
        async with character_session(db.bind,a.id) as session:
            verifier=(await session.get(models.OAuthState,state_a)).code_verifier
        result=await client.get('/oauth/callback',params={'code':'synthetic','state':state_a})
        assert result.status_code==200,result.text
        assert observed==[(a.id,verifier)]
        assert (await client.get('/oauth/callback',params={'code':'synthetic','state':state_a})).status_code==400
    with character_context(a.id): assert chat_db.get_tokens()['access_token']=='synthetic-access'
    with character_context(b.id): assert chat_db.get_tokens() is None
