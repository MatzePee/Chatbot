"""Real HTTP request hook, synthetic durable databases and no external transport."""
import asyncio
import httpx
import pytest
from uuid import uuid4
from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine
from autoposter import db as database, models
from autoposter.workspaces import character_context
from autoposter.services import runtime
from autoposter.adapters.base import http_client, AdapterError


@pytest.mark.asyncio
async def test_paused_workspace_can_verify_connections_and_exchange_tokens(db, monkeypatch):
    from autoposter.config import settings
    owner = models.Character(name='Paused setup')
    db.add(owner)
    await db.commit()
    monkeypatch.setattr(database, 'engine', db.bind)
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    sent = []
    async def transport(request):
        sent.append(str(request.url))
        return httpx.Response(200, json={'ok': True})
    urls = [settings.x_oauth_token_url, settings.fanvue_oauth_token_url]
    urls += ['https://api.telegram.org/botsynthetic/' + method for method in
             ('getMe', 'getChat', 'getChatMember', 'getChatMemberCount')]
    with character_context(owner.id):
        async with http_client() as client:
            client._mounts = {}
            client._transport = httpx.MockTransport(transport)
            for url in urls:
                assert (await client.post(url, json={})).status_code == 200
    assert sent == urls


@pytest.mark.asyncio
async def test_fanvue_s3_part_is_blocked_when_pause_changes_during_url_lookup(db, monkeypatch):
    from autoposter.adapters.fanvue import FanvueAdapter
    from autoposter.adapters.base import Credentials
    owner = models.Character(name='Upload owner')
    db.add(owner)
    await db.commit()
    monkeypatch.setattr(database, 'engine', db.bind)
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    sent = []
    async def transport(request):
        sent.append((request.method, request.url.path))
        if request.method == 'POST' and request.url.path.endswith('/v0/media/uploads'):
            return httpx.Response(200, json={'uploadId': 'synthetic-upload'})
        if request.method == 'GET' and request.url.path.endswith('/parts/1/url'):
            async with db.bind.begin() as conn:
                await conn.execute(update(models.AppSetting.__table__).where(models.AppSetting.key == 'runtime').values(value={'global_pause': True, 'dry_run': False}))
            return httpx.Response(200, text='https://synthetic-s3.invalid/part')
        if request.method == 'PUT':
            return httpx.Response(200, headers={'ETag': 'synthetic'})
        if request.method == 'PATCH':
            return httpx.Response(200, json={'mediaUuid': str(uuid4())})
        return httpx.Response(200, json={'status': 'ready'})
    real_client = httpx.AsyncClient
    def offline_client(*args, **kwargs):
        return real_client(*args, **kwargs, transport=httpx.MockTransport(transport), trust_env=False)
    monkeypatch.setattr(httpx, 'AsyncClient', offline_client)
    with character_context(owner.id):
        async with db.bind.begin() as conn:
            await conn.execute(models.AppSetting.__table__.insert().values(key='runtime', value={'global_pause': False, 'dry_run': False}))
        asset = models.MediaAsset(id=uuid4(), filename='synthetic.jpg', mime='image/jpeg')
        with pytest.raises(AdapterError, match='Stopp'):
            await FanvueAdapter().upload_media(Credentials('synthetic'), asset, b'synthetic-bytes')
    assert not any(method in ('PUT', 'PATCH') for method, _ in sent)


@pytest.mark.asyncio
async def test_telegram_pause_is_definitely_blocked_not_uncertain_delivery(db, monkeypatch):
    from autoposter.adapters.telegram import TelegramAdapter, TelegramUncertain
    from autoposter.adapters.base import Credentials
    owner = models.Character(name='Paused')
    db.add(owner)
    await db.commit()
    monkeypatch.setattr(database, 'engine', db.bind)
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    with character_context(owner.id):
        with pytest.raises(AdapterError) as error:
            await TelegramAdapter()._call(Credentials('synthetic'), 'sendMessage', {'chat_id': '-1001', 'text': 'synthetic'})
    assert not isinstance(error.value, TelegramUncertain)
    assert 'Stopp' in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path', [('POST','/2/tweets'), ('PATCH','/posts/synthetic'),
                                        ('DELETE','/posts/synthetic'), ('PUT','/part')])
@pytest.mark.parametrize('saved,archived', [
    ({'global_pause': True, 'dry_run': False}, False),
    ({'global_pause': False, 'dry_run': True}, False),
    ({'global_pause': 'false', 'dry_run': False}, False),
    (None, False),
    ({'global_pause': False, 'dry_run': False}, True),
])
async def test_external_mutation_gate_fails_closed(db, monkeypatch, method, path, saved, archived):
    owner = models.Character(name='Synthetic', is_archived=archived)
    db.add(owner)
    await db.commit()
    monkeypatch.setattr(database, 'engine', db.bind)
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    sent = []
    async def transport(request):
        sent.append(request)
        return httpx.Response(200)
    with character_context(owner.id):
        if saved is not None:
            async with db.bind.begin() as conn:
                await conn.execute(models.AppSetting.__table__.insert().values(key='runtime', value=saved))
        async with http_client() as client:
            client._mounts = {}
            client._transport = httpx.MockTransport(transport)
            with pytest.raises(AdapterError):
                await client.request(method, 'https://synthetic.invalid' + path)
    assert sent == []


@pytest.mark.asyncio
async def test_committed_pause_stops_delayed_http_write_despite_old_snapshot(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'pause.db'))
    a, b = uuid4(), uuid4()
    async with engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
        for owner in (a, b):
            await conn.execute(models.Character.__table__.insert().values(id=owner, name=str(owner)))
            with character_context(owner):
                await conn.execute(models.AppSetting.__table__.insert().values(key='runtime', value={'global_pause': False, 'dry_run': False}))
    monkeypatch.setattr(database, 'engine', engine)
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    entered, release = asyncio.Event(), asyncio.Event()
    sent = []
    async def transport(request):
        sent.append(request.url.path)
        return httpx.Response(200, json={'ok': True})
    async def write(owner):
        with character_context(owner):
            async with database.SessionLocal() as session:
                await runtime.load(session)
            assert runtime.runtime.global_pause is False
            entered.set()
            await release.wait()
            async with http_client() as client:
                client._mounts = {}
                client._transport = httpx.MockTransport(transport)
                return await client.post('https://synthetic.invalid/2/tweets', json={'text': 'synthetic'})
    worker = asyncio.create_task(write(a))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        with character_context(a):
            async with engine.begin() as conn:
                await conn.execute(update(models.AppSetting.__table__).where(models.AppSetting.key == 'runtime').values(value={'global_pause': True, 'dry_run': False}))
        release.set()
        with pytest.raises(AdapterError, match='Stopp'):
            await worker
        assert sent == []
        response = await write(b)
        assert response.status_code == 200
        assert sent == ['/2/tweets']
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)
        await engine.dispose()
