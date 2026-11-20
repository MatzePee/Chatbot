"""No provider requests: Buffer/FTPS contract and safety regression tests."""
import io
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from autoposter.adapters import buffer
from autoposter.adapters.base import AdapterError, Credentials, MediaRef
from autoposter.models import Channel, Post
from autoposter.services import buffer_channels, buffer_storage
from autoposter.services.media_rules import combination_issue


def test_instagram_combinations():
    photo=SimpleNamespace(mime='image/jpeg')
    video=SimpleNamespace(mime='video/mp4')
    assert combination_issue('instagram',[photo]*10) is None
    assert combination_issue('instagram',[video]) is None
    assert combination_issue('instagram',[])
    assert combination_issue('instagram',[photo]*11)
    assert combination_issue('instagram',[photo,video])


def test_webspace_confines_paths_and_requires_https():
    config=dict(ftp_host='ftp.example.com',ftp_user='user',ftp_directory='/',public_base_url='https://media.example.com')
    assert buffer_storage.normalize(config)['ftp_directory']=='/'
    for url in ['http://media.example.com','https://user:pass@media.example.com','https://media.example.com/../files','https://media.example.com/?token=x']:
        with pytest.raises(AdapterError):buffer_storage.normalize({**config,'public_base_url':url})
    for path in ['../file.jpg','mp-creatorstudio/a/b/file.jpg',f'mp-creatorstudio/{uuid.uuid4()}/{uuid.uuid4()}/../file.jpg']:
        with pytest.raises(AdapterError):buffer_storage.managed_path(path)
    path=f'mp-creatorstudio/{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}.jpg'
    assert buffer_storage.managed_path(path)==path


def test_instagram_jpeg_requires_a_deliberate_valid_crop():
    data=io.BytesIO();Image.new('RGB',(200,1000),'blue').save(data,'PNG')
    with pytest.raises(AdapterError,match='Bildeditor'):
        buffer_storage.jpeg(data.getvalue())
    data=io.BytesIO();Image.new('RGB',(600,800),'blue').save(data,'PNG')
    with Image.open(io.BytesIO(buffer_storage.jpeg(data.getvalue()))) as image:
        assert image.format=='JPEG' and image.size==(600,800)


@pytest.mark.parametrize('state',['preparing','uploading','pending','queued','uncertain','remote_error'])
def test_pending_buffer_posts_cannot_be_edited_or_deleted(state):
    post=Post(generation_meta={'buffer_delivery':{'state':state}})
    assert buffer_channels.locked(post)
    with pytest.raises(Exception) as caught:buffer_channels.editable(post,deleting=True)
    assert caught.value.status_code==409


@pytest.mark.asyncio
async def test_buffer_uses_automatic_share_now_and_never_retries_ambiguous_create(monkeypatch):
    from autoposter.services import runtime
    monkeypatch.setattr(runtime,'assert_external_write_allowed',AsyncMock())
    calls=[]
    def handle(request):
        body=json.loads(request.content);calls.append(body)
        if 'channel(input' in body['query']:
            return httpx.Response(200,json={'data':{'channel':{'id':'ig','name':'sally_in_nylons','service':'instagram','isDisconnected':False,'isLocked':False}}})
        payload=body['variables']['input']
        assert payload['mode']=='shareNow' and payload['schedulingType']=='automatic'
        assert payload['metadata']['instagram']['type']=='reel'
        assert payload['assets']==[{'video':{'url':'https://media.example.com/video.mp4'}}]
        raise httpx.ReadTimeout('simulated',request=request)
    client=httpx.AsyncClient
    monkeypatch.setattr(buffer.httpx,'AsyncClient',lambda **kw:client(transport=httpx.MockTransport(handle),**kw))
    with pytest.raises(buffer.BufferUncertain):
        await buffer.BufferAdapter().publish(Credentials('synthetic',external_user_id='ig'),Channel(handle='@sally_in_nylons'),Post(body_text='Caption',media_asset_ids=['v']),[MediaRef('v','https://media.example.com/video.mp4',mime='video/mp4')])
    assert len(calls)==2  # one verification query, exactly one create attempt


@pytest.mark.asyncio
async def test_graphql_read_allowlist_rejects_mutation_without_write_flag():
    with pytest.raises(AdapterError):
        await buffer._request('synthetic','mutation { deletePost(input: {}) { __typename } }')
