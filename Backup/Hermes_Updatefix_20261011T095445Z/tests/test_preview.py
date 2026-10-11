import asyncio
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from app import preview

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('method,url,allowed', [
    ('GET', 'https://api.fanvue.com/chats', True),
    ('GET', 'https://api.fanvue.com/chats/x/messages?markAsRead=false', True),
    ('GET', 'https://api.fanvue.com/chats/x/messages?markAsRead=true', False),
    ('POST', 'https://api.fanvue.com/chats/x/message', False),
    ('POST', 'https://api.fanvue.com/posts', False),
    ('POST', 'https://api.fanvue.com/posts/example/comments', False),
    ('POST', 'https://auth.fanvue.com/oauth2/token', False),
    ('POST', 'https://api.x.com/2/tweets', False),
    ('POST', 'https://openrouter.ai/api/v1/chat/completions', True),
    ('GET', 'https://openrouter.ai/api/v1/models', True),
    ('POST', 'https://openrouter.ai/api/v1/credits', False),
    ('POST', 'https://api.telegram.org/botEXAMPLE/sendMessage', False),
    ('GET', 'https://api.fanvue.com.attacker.example/chats', False),
    ('POST', 'http://api.fanvue.com/chats/x/message', False),
])
def test_outbound_policy(method, url, allowed):
    assert preview.request_allowed(httpx.Request(method, url)) is allowed


def test_transport_blocks_sync_async_writes_but_allows_reads():
    code = '''
import os, asyncio, httpx
os.environ['MP_PREVIEW'] = '1'
from app import preview
calls=[]
def sync(self, request):
    calls.append(request.method)
    return httpx.Response(200)
async def asynchronous(self, request):
    calls.append(request.method)
    return httpx.Response(200)
httpx.HTTPTransport.handle_request=sync
httpx.AsyncHTTPTransport.handle_async_request=asynchronous
preview.install_network_guard()
transport=httpx.HTTPTransport()
transport.handle_request(httpx.Request('GET','https://api.fanvue.com/chats'))
for url in ['https://api.fanvue.com/chats/a/message','https://auth.fanvue.com/oauth2/token']:
    try: transport.handle_request(httpx.Request('POST',url))
    except preview.PreviewNetworkBlocked: pass
    else: raise AssertionError('write reached transport')
async def check():
    async_transport=httpx.AsyncHTTPTransport()
    try: await async_transport.handle_async_request(httpx.Request('POST','https://api.x.com/2/tweets'))
    except preview.PreviewNetworkBlocked: pass
    else: raise AssertionError('async write reached transport')
    await async_transport.handle_async_request(httpx.Request('POST','https://openrouter.ai/api/v1/chat/completions'))
asyncio.run(check())
assert calls==['GET','POST'],calls
'''
    subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True)


def test_chatbot_never_refreshes_or_sends_and_forces_approval(monkeypatch):
    from app import fanvue, poller
    monkeypatch.setenv('MP_PREVIEW', '1')
    with pytest.raises(fanvue.NotAuthenticated):
        fanvue._refresh_token()
    with pytest.raises(preview.PreviewNetworkBlocked):
        fanvue.send_message('example-user', 'must not send')
    assert poller._effective_mode({'mode_override': 'auto'}) == 'approval'
    seen = {}
    def read_request(method, path, **kwargs):
        seen.update(kwargs['params'])
        return httpx.Response(200, json={'data': []})
    monkeypatch.setattr(fanvue, '_request', read_request)
    fanvue.list_messages('example-user', mark_as_read=True)
    assert seen['markAsRead'] == 'false'


def test_shadow_worker_never_uses_send_loop(monkeypatch):
    from app import poller
    import threading
    event = threading.Event()
    monkeypatch.setattr(poller, 'poll_cycle', event.set)
    monkeypatch.setattr(poller, '_loop', lambda: pytest.fail('production loop started'))
    preview.start_shadow_worker()
    assert event.wait(timeout=2)
    preview.stop_shadow_worker()
    assert not preview.status()['alive']


@pytest.mark.asyncio
async def test_connection_test_allowed_but_publish_stays_blocked(monkeypatch):
    monkeypatch.setenv('MP_PREVIEW', '1')
    from starlette.responses import JSONResponse
    async def endpoint(scope, receive, send):
        await JSONResponse({'ok': True})(scope, receive, send)
    app = preview.PreviewMiddleware(endpoint)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/api/v1/channels/11111111-1111-1111-1111-111111111111/test')).status_code == 200
        assert (await client.post('/api/v1/posts/11111111-1111-1111-1111-111111111111/publish')).status_code == 403
        assert (await client.get('/api/v1/oauth/fanvue/start')).status_code == 403
