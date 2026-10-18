import asyncio
import pytest
import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from autoposter.db import Base
from autoposter.config import settings
from autoposter.models import Channel, Post, ChannelCredential
from autoposter.api.channels import disconnect, pause_channel, _to_out
from autoposter.services import publisher
from .test_telegram_pipeline import setup
from .test_telegram import no_network

@pytest.mark.asyncio
async def test_disconnect_removes_local_publication_secret_only(db):
    channel,post,assets=await setup(db)
    key=channel.credential_id
    await disconnect(channel.id,db,None)
    await db.flush()  # production request dependency commits after returning
    assert await db.get(ChannelCredential,key) is None
    assert channel.last_verified_at is None

@pytest.mark.asyncio
async def test_paused_verified_bot_stays_connected(db):
    channel,post,assets=await setup(db)
    await pause_channel(channel.id,True,db,None)
    assert (await _to_out(db,channel)).is_connected

@pytest.mark.asyncio
@pytest.mark.parametrize('old_preflight_failure',[False,True])
async def test_claim_survives_other_session_and_force_during_send(tmp_path,monkeypatch,old_preflight_failure):
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'synthetic.db'))
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    maker=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(settings,'dry_run',False)
    calls=[]
    async with maker() as first, maker() as stale:
        channel,post,_=await setup(first)
        if old_preflight_failure:post.generation_meta={'preflight_failure':True}
        await first.commit()
        stale_post = await stale.get(Post, post.id)  # loaded before the first sender claims
        async def handler(req):
            calls.append(req)
            assert len(calls) == 1, 'Stale session must not issue a duplicate send'
            result=await publisher.publish_post(stale,post.id,force=True)
            assert result[0] is False
            return httpx.Response(200,json={'ok':True,'result':{'message_id':4,'chat':{'id':-100456}}})
        monkeypatch.setattr('autoposter.adapters.telegram.http_client',lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        assert (await publisher.publish_post(first,post.id))[0]
        assert len(calls)==1
    await engine.dispose()

@pytest.mark.asyncio
async def test_unexpected_send_exception_cannot_leak_token_or_be_retried(db,monkeypatch):
    channel,post,_=await setup(db)
    monkeypatch.setattr(settings,'dry_run',False)
    from autoposter.adapters import get_adapter
    async def broken(*args):raise RuntimeError('https://api.telegram.org/bot123:synthetic/sendMessage')
    monkeypatch.setattr(get_adapter('telegram'),'publish',broken)
    assert (await publisher.publish_post(db,post.id))[0] is False
    assert '123:synthetic' not in post.error_message
    assert post.generation_meta['telegram_delivery']['state']=='uncertain'
