import pytest
import httpx
from fastapi import HTTPException
from autoposter.adapters import get_adapter, AdapterError
from autoposter.adapters.base import Credentials
from autoposter.models import Channel, Post, User, ChannelCredential
from autoposter.schemas import TelegramSetup
from autoposter.api.channels import save_telegram, test_channel as check_channel, oauth_start
from .test_telegram import no_network
from .test_telegram_pipeline import setup
from .test_telegram_publish import credential

@pytest.mark.asyncio
async def test_new_bot_token_resets_identity_and_does_not_reuse_old_binding(db):
    channel,_,_=await setup(db)
    await save_telegram(channel.id,TelegramSetup(bot_token='999:synthetic_new',destination='@sample_channel'),db,User(role='admin'))
    row=await db.get(ChannelCredential,channel.credential_id)
    assert row.external_user_id is None and row.raw_meta=={'destination':'@sample_channel'}
    assert channel.last_verified_at is None

@pytest.mark.asyncio
async def test_telegram_does_not_start_oauth_or_allow_viewer_connection_test(db):
    channel,_,_=await setup(db)
    with pytest.raises(HTTPException) as error:
        await oauth_start('telegram',channel.id,db,User(role='admin'))
    assert error.value.status_code==409
    with pytest.raises(HTTPException) as error:
        await check_channel(channel.id,db,User(role='viewer'))
    assert error.value.status_code==403

@pytest.mark.asyncio
@pytest.mark.parametrize('attribute,value',[('price_cents',300),('pin_after_publish',True),('thread_parent_id','unsupported-thread')])
async def test_reject_provider_specific_options_without_silently_dropping(monkeypatch,attribute,value):
    post=Post(body_text='Independent');setattr(post,attribute,value)
    async def forbidden(*args,**kwargs):raise AssertionError('No request permitted')
    monkeypatch.setattr(get_adapter('telegram'),'_call',forbidden)
    with pytest.raises(AdapterError):
        await get_adapter('telegram').publish(credential(),Channel(handle='@sample_channel'),post,[])

@pytest.mark.asyncio
async def test_private_channel_status_url_uses_canonical_chat_id(monkeypatch):
    cred=credential();cred.raw_meta['username']=''
    async def result(*args,**kwargs):return {'message_id':9,'chat':{'id':-100456}}
    monkeypatch.setattr(get_adapter('telegram'),'_call',result)
    outcome=await get_adapter('telegram').publish(cred,Channel(handle='@sample_channel'),Post(body_text='Private'),[])
    assert outcome.external_url=='https://t.me/c/456/9'
