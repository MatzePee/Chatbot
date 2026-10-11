"""X v2 media contract; all requests are intercepted, no real posts or uploads.

References: docs.x.com/x-api/media/{initialize-media-upload,
append-media-upload,finalize-media-upload,create-media-metadata}.
"""
import json
from email.parser import BytesParser
from email.policy import default
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from autoposter.adapters import x
from autoposter.adapters.base import AdapterError, Credentials
from autoposter.models import Channel, MediaAsset, Post


def asset(mime='image/jpeg', alt='Bildbeschreibung'):
    return MediaAsset(id='test-image', mime=mime, caption_hint=alt)


def setup_upload(mock, adapter, processing=None):
    base = f'{adapter.base}/2/media/upload'
    init = mock.post(base + '/initialize').respond(200, json={'data': {'id': '12345'}})
    append = mock.post(base + '/12345/append').respond(200, json={'data': {}})
    final = mock.post(base + '/12345/finalize').respond(200, json={'data': {
        'id': '12345', **({'processing_info': processing} if processing else {})}})
    return init, append, final


@pytest.mark.asyncio
@pytest.mark.parametrize('mime,category', [('image/jpeg', 'tweet_image'), ('image/png', 'tweet_image'),
                                           ('image/webp', 'tweet_image'), ('image/gif', 'tweet_gif')])
async def test_image_upload_chunks_metadata_and_post(monkeypatch, mime, category):
    adapter = x.XAdapter()
    monkeypatch.setattr(x, 'CHUNK_SIZE', 4)
    image = b'\xff\xd8\x00\x01\x02\x03\x04\xff\xd9'
    with respx.mock() as mock:
        init, append, final = setup_upload(mock, adapter)
        meta = mock.post(f'{adapter.base}/2/media/metadata').respond(200, json={'data': {'id': '12345'}})
        tweet = mock.post(f'{adapter.base}/2/tweets').respond(201, json={'data': {'id': '67890'}})
        media = await adapter.upload_media(Credentials('fake-token'), asset(mime), image)
        assert json.loads(init.calls.last.request.content) == {
            'total_bytes': len(image), 'media_type': mime, 'media_category': category}
        assert init.calls.last.request.headers['content-type'] == 'application/json'
        chunks = []
        for index, call in enumerate(append.calls):
            request = call.request
            message = BytesParser(policy=default).parsebytes(
                b'Content-Type: ' + request.headers['content-type'].encode() + b'\r\n\r\n' + request.content)
            fields = {part.get_param('name', header='content-disposition'): part.get_payload(decode=True)
                      for part in message.iter_parts()}
            assert set(fields) == {'media', 'segment_index'}
            assert fields['segment_index'] == str(index).encode()
            chunks.append(fields['media'])
        assert len(chunks) == 3 and b''.join(chunks) == image
        assert final.calls.last.request.content == b''
        assert json.loads(meta.calls.last.request.content) == {
            'id': '12345', 'metadata': {'alt_text': {'text': 'Bildbeschreibung'}}}
        assert not tweet.called
        result = await adapter.publish(Credentials('fake-token'), Channel(handle='@test'),
                                       Post(body_text='Bildpost', hashtags=[]), [media])
        assert json.loads(tweet.calls.last.request.content) == {'text': 'Bildpost', 'media': {'media_ids': ['12345']}}
        assert result.external_post_id == '67890'
        assert [call.request.url.path for call in mock.calls] == [
            '/2/media/upload/initialize', *(['/2/media/upload/12345/append'] * 3),
            '/2/media/upload/12345/finalize', '/2/media/metadata', '/2/tweets']
        assert all(not call.request.url.query for call in mock.calls)
        assert all(call.request.headers['authorization'] == 'Bearer fake-token' for call in mock.calls)


@pytest.mark.asyncio
async def test_processing_waits_until_ready(monkeypatch):
    adapter = x.XAdapter()
    sleep = AsyncMock()
    monkeypatch.setattr(x.asyncio, 'sleep', sleep)
    with respx.mock() as mock:
        setup_upload(mock, adapter, {'state': 'pending', 'check_after_secs': 0})
        status = mock.get(f'{adapter.base}/2/media/upload', params={'command': 'STATUS', 'media_id': '12345'})
        status.side_effect = [httpx.Response(200, json={'data': {'processing_info': {'state': 'in_progress', 'check_after_secs': 2}}}),
                              httpx.Response(200, json={'data': {'processing_info': {'state': 'succeeded'}}})]
        result = await adapter.upload_media(Credentials('fake'), asset(alt=''), b'image')
        assert result.external_id == '12345' and status.call_count == 2
        assert [call.args[0] for call in sleep.await_args_list] == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['initialize', 'append', 'finalize'])
async def test_upload_error_stops_following_steps(stage):
    adapter = x.XAdapter()
    with respx.mock(assert_all_called=False) as mock:
        routes = setup_upload(mock, adapter)
        index = ['initialize', 'append', 'finalize'].index(stage)
        routes[index].respond(400, json={'detail': 'Invalid Request'})
        with pytest.raises(AdapterError) as exc:
            await adapter.upload_media(Credentials('fake'), asset(), b'image')
        assert exc.value.status == 400
        assert len(mock.calls) == index + 1
        assert all(not route.called for route in routes[index + 1:])


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['failed', 'pending'])
async def test_processing_failure_or_timeout_does_not_return_media(monkeypatch, state):
    adapter = x.XAdapter()
    monkeypatch.setattr(x.asyncio, 'sleep', AsyncMock())
    with respx.mock() as mock:
        setup_upload(mock, adapter, {'state': state, 'check_after_secs': 301})
        with pytest.raises(AdapterError) as exc:
            await adapter.upload_media(Credentials('fake'), asset(), b'image')
        assert exc.value.retryable is (state == 'pending')
        assert len(mock.calls) == 3


@pytest.mark.asyncio
async def test_missing_upload_id_stops_before_append():
    adapter = x.XAdapter()
    with respx.mock() as mock:
        mock.post(f'{adapter.base}/2/media/upload/initialize').respond(200, json={'data': {}})
        with pytest.raises(AdapterError, match='keine media_id'):
            await adapter.upload_media(Credentials('fake'), asset(), b'image')
        assert len(mock.calls) == 1
