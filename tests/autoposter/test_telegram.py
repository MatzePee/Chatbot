import json
import httpx
import pytest
from autoposter.adapters import get_adapter, AdapterError
from autoposter.adapters.base import Credentials, OAuthApp
from autoposter.models import Channel, Post, PostingPolicy

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def blocked(*args, **kwargs):
        raise AssertionError('Outbound network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', blocked)

@pytest.mark.asyncio
async def test_telegram_read_only_identity_and_permission(monkeypatch):
    adapter = get_adapter('telegram')
    seen = []
    def handler(req):
        seen.append((req.method, req.url.path, json.loads(req.content)))
        method = req.url.path.rsplit('/', 1)[1]
        result = {'getMe': {'id': 123, 'is_bot': True, 'username': 'sample_bot'},
                  'getChat': {'id': -100456, 'type': 'channel', 'username': 'sample_channel'},
                  'getChatMember': {'status': 'administrator', 'can_post_messages': True, 'user': {'id':123}}}[method]
        return httpx.Response(200, json={'ok': True, 'result': result})
    monkeypatch.setattr('autoposter.adapters.telegram.http_client', lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    info = await adapter.verify(OAuthApp(''), Credentials('123:synthetic', raw_meta={'destination':'@sample_channel'}))
    assert info.ok and info.account_id == '123'
    assert info.raw['chat_id'] == '-100456'
    assert seen == [('POST', '/bot123:synthetic/getMe', {}),
                    ('POST', '/bot123:synthetic/getChat', {'chat_id':'@sample_channel'}),
                    ('POST', '/bot123:synthetic/getChatMember', {'chat_id':'-100456','user_id':123})]

@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['timeout','unexpected','bad_errorcode','http200error','rate','wrong_destination','permission'])
async def test_telegram_errors_are_sanitized_and_binding_checked(monkeypatch, mode):
    token = '123:synthetic'
    def handler(req):
        if mode == 'unexpected':
            raise RuntimeError(str(req.url))
        if mode == 'bad_errorcode':
            return httpx.Response(400, json={'ok':False,'error_code':str(req.url)})
        if mode == 'timeout':
            raise httpx.ReadTimeout(str(req.url), request=req)
        if mode == 'http200error':
            return httpx.Response(200, json={'ok':False, 'error_code':401,'description':str(req.url)})
        if mode == 'rate':
            return httpx.Response(429,json={'ok':False,'error_code':429,'parameters':{'retry_after':37}})
        method = req.url.path.rsplit('/',1)[1]
        result = {'getMe':{'id':123,'is_bot':True}, 'getChat':{'id':-100999 if mode=='wrong_destination' else -100456,'type':'channel'}, 'getChatMember':{'status':'administrator','can_post_messages':False,'user':{'id':123}}}[method]
        return httpx.Response(200,json={'ok':True,'result':result})
    monkeypatch.setattr('autoposter.adapters.telegram.http_client',lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(AdapterError) as error:
        await get_adapter('telegram').verify(OAuthApp(''), Credentials(token,raw_meta={'destination':'-100456'}))
    assert token not in str(error.value)
    if mode == 'rate':
        assert error.value.retry_after == 37
    if mode == 'timeout':
        assert not error.value.retryable

@pytest.mark.asyncio
async def test_independent_channel_secret_save_verify_disconnect(db, monkeypatch):
    from autoposter.api.channels import create_channel, update_channel, _to_out, test_channel, disconnect
    from autoposter.api.channels import save_telegram
    from autoposter.schemas import ChannelCreate, ChannelUpdate, TelegramSetup
    from autoposter.models import ChannelCredential, User
    from autoposter.services import credentials
    admin = User(role='admin')
    out = await create_channel(ChannelCreate(platform='telegram', display_name='My own queue', handle='@sample_channel'), db, admin)
    channel = await db.get(Channel, out.id)
    assert not out.is_connected and not channel.auto_plan_enabled and not channel.is_active
    assert not out.app_configured and not out.redirect_uri
    saved = await save_telegram(channel.id, TelegramSetup(bot_token='123:synthetic', destination='@sample_channel'), db, admin)
    assert saved.has_bot_token
    assert not saved.is_connected and '123:synthetic' not in saved.model_dump_json()
    row = await db.get(ChannelCredential, channel.credential_id)
    assert row.access_token_enc != '123:synthetic'
    encrypted = row.access_token_enc
    await save_telegram(channel.id, TelegramSetup(bot_token='********', destination='@sample_channel'), db, admin)
    assert row.access_token_enc == encrypted
    async def verify(app, cred):
        assert cred.access_token == '123:synthetic'
        from autoposter.adapters.base import AccountInfo
        return AccountInfo(True, account_id='123', username='sample_bot', raw={'chat_id':'-100456','username':'sample_channel'}, message='Read only')
    monkeypatch.setattr(get_adapter('telegram'), 'verify', verify)
    result = await test_channel(channel.id, db, admin)
    assert result['ok'] and result['chat_id'] == '-100456' and result['account_id'] == '123'
    assert (await _to_out(db, channel)).is_connected
    cred = await credentials.get_valid_credentials(db, channel)
    assert cred.raw_meta['chat_id'] == '-100456'
    await update_channel(channel.id, ChannelUpdate(handle='@different_channel'), db, admin)
    with pytest.raises(AdapterError):
        await credentials.get_valid_credentials(db, channel)
    await disconnect(channel.id, db, admin)
    assert not (await _to_out(db, channel)).is_connected
