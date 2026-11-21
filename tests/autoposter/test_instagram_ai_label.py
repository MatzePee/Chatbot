from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from autoposter.adapters import upload_post, buffer

@pytest.mark.asyncio
@pytest.mark.parametrize('enabled',[True,False])
@pytest.mark.parametrize('mime,count',[('image/jpeg',1),('image/jpeg',2),('video/mp4',1)])
async def test_instagram_disclosure_for_both_providers(monkeypatch,enabled,mime,count):
    channel=SimpleNamespace(handle='test',instagram_is_ai_generated=enabled)
    cred=SimpleNamespace(access_token='synthetic',external_user_id='profile')
    post=SimpleNamespace(id='post',media_asset_ids=list(range(count)),body_text='Caption',hashtags=[],generation_meta={'buffer_delivery':{'request_id':'request'}})
    media=[upload_post.UploadPostMediaRef(str(i),'https://example.invalid/image',mime=mime,data=b'test') for i in range(count)]
    monkeypatch.setattr(upload_post,'verify_account',AsyncMock(return_value={}))
    upload=AsyncMock(return_value={'request_id':'request','status':'pending'})
    monkeypatch.setattr(upload_post,'request',upload)
    await upload_post.UploadPostAdapter().publish(cred,channel,post,media)
    assert upload.call_args.kwargs['data']['is_ai_generated']==('true' if enabled else 'false')
    assert upload.call_args.args[1]==('/api/upload' if mime=='video/mp4' else '/api/upload_photos')
    monkeypatch.setattr(buffer,'read',AsyncMock(return_value={'channel':{'name':'test','service':'instagram'}}))
    send=AsyncMock(return_value={'createPost':{'__typename':'PostActionSuccess','post':{'id':'remote','channelId':'profile'}}})
    monkeypatch.setattr(buffer,'_request',send)
    await buffer.BufferAdapter().publish(cred,channel,post,media)
    assert send.call_args.args[2]['input']['metadata']['instagram']['isAiGenerated'] is enabled
