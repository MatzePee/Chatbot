"""Upload-Post regressions. All provider responses are simulated."""
import json
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from autoposter.adapters import upload_post
from autoposter.adapters.base import AdapterError, Credentials, ExternalActionBlocked
from autoposter.adapters.buffer import BufferUncertain
from autoposter.models import Channel, Post


@pytest.mark.parametrize('payload,expected',[
    ({'status':'pending'},'sending'),
    ({'status':'completed','results':[{'platform':'instagram','success':True}]},'sent'),
    ({'status':'completed','results':[{'platform':'instagram','success':True,'skipped':True}]},'error'),
    ({'status':'completed','results':[{'platform':'facebook','success':True}]},'error'),
    ({'status':'failed','results':[{'platform':'instagram','success':False,'status':'retryable'}]},'sending'),
    ({'status':'completed','results':[]},'error'),
    ({'results':{'instagram':{'success':True,'url':'https://www.instagram.com/p/test/'}}},'sent'),
])
def test_success_requires_an_explicit_instagram_result(payload,expected):
    assert upload_post.normalized(payload,'request-id','profile')['status']==expected


def test_profile_identifier_is_distinct_from_instagram_handle():
    item=upload_post.account({'username':'customer-profile','social_accounts':{'instagram':{'username':'178999','handle':'sally_in_nylons'}}})
    assert item['profile']=='customer-profile' and item['account_id']=='178999' and item['handle']=='sally_in_nylons'


@pytest.mark.parametrize('value,expected',[
    ('2026-11-20T22:59:02.650Z','2026-11-20T22:59:02.650Z'),
    ({'$date':'2026-11-20T22:59:02.650Z'},'2026-11-20T22:59:02.650Z'),
    ({'$date':None},None),
    ({'$date':{'$numberLong':'invalid'}},None),
    ({},None),(None,None),(42,None),
])
def test_status_timestamps_accept_extended_json_without_crashing(value,expected):
    assert upload_post.timestamp_text(value)==expected


def test_real_async_status_shape_is_published_with_provider_timestamp():
    response={'request_id':'request','status':'completed','results':[{'platform':'instagram','success':True,'upload_timestamp':{'$date':'2026-11-20T22:59:02.650Z'},'post_url':'https://www.instagram.com/p/example/'}],'last_update':{'$date':'2026-11-20T22:59:02.650Z'}}
    normalized=upload_post.normalized(response,'request','default')
    assert normalized['status']=='sent'
    assert normalized['sentAt']=='2026-11-20T22:59:02.650Z'
    assert normalized['externalLink']=='https://www.instagram.com/p/example/'


@pytest.mark.asyncio
async def test_profile_list_without_id_and_single_profile_with_id(monkeypatch):
    async def get(token,path,**kwargs):
        profile={'username':'default','social_accounts':{'instagram':{'handle':'sally_in_nylons','display_name':'sally_in_nylons','reauth_required':False}}}
        if path=='/api/uploadposts/users':
            return {'success':True,'profiles':[profile]}
        assert path=='/api/uploadposts/users/default'
        profile['social_accounts']['instagram']['username']='178999'
        return {'success':True,'profile':profile}
    monkeypatch.setattr(upload_post,'request',get)
    cred=Credentials('synthetic',external_user_id='default',raw_meta={'destination':'@sally_in_nylons'})
    choices=await upload_post.profiles(cred)
    assert len(choices)==1 and choices[0]['profile']=='default' and choices[0]['handle']=='sally_in_nylons'
    assert choices[0]['account_id']==''
    verified=await upload_post.verify_account(cred)
    assert verified['account_id']=='178999'


def test_empty_or_display_only_social_account_is_not_a_connection():
    for social in ['',None,{}, {'display_name':'Unverified label'}]:
        assert upload_post.account({'username':'default','social_accounts':{'instagram':social}}) is None


@pytest.mark.asyncio
async def test_verification_still_requires_single_profile_identity(monkeypatch):
    async def get(*args,**kwargs):
        return {'success':True,'profile':{'username':'default','social_accounts':{'instagram':{'handle':'sally_in_nylons'}}}}
    monkeypatch.setattr(upload_post,'request',get)
    with pytest.raises(AdapterError):
        await upload_post.verify_account(Credentials('synthetic',external_user_id='default',raw_meta={'destination':'@sally_in_nylons'}))


@pytest.mark.asyncio
async def test_wrong_destination_prevents_upload(monkeypatch):
    async def get(*args,**kwargs):
        return {'profile':{'username':'profile','social_accounts':{'instagram':{'username':'178999','handle':'other_account'}}}}
    monkeypatch.setattr(upload_post,'request',get)
    with pytest.raises(AdapterError,match='Kanal-Handle'):
        await upload_post.verify_account(Credentials('synthetic',external_user_id='profile'),Channel(handle='@sally_in_nylons'))


@pytest.mark.asyncio
async def test_changed_numeric_instagram_account_rejected(monkeypatch):
    async def get(*args,**kwargs):
        return {'profile':{'username':'profile','social_accounts':{'instagram':{'username':'different-id','handle':'sally_in_nylons'}}}}
    monkeypatch.setattr(upload_post,'request',get)
    with pytest.raises(AdapterError,match='anderes Instagram-Konto'):
        await upload_post.verify_account(Credentials('synthetic',external_user_id='profile',raw_meta={'destination':'@sally_in_nylons','instagram_account_id':'original-id'}))


@pytest.mark.asyncio
async def test_uploads_files_with_durable_idempotency_key_not_public_urls(monkeypatch):
    from autoposter.services import runtime
    monkeypatch.setattr(runtime,'assert_external_write_allowed',AsyncMock())
    calls=[]
    async def handle(request):
        calls.append(request)
        if request.method=='GET':
            return httpx.Response(200,json={'profile':{'username':'profile','social_accounts':{'instagram':{'username':'178999','handle':'sally_in_nylons'}}}})
        body=await request.aread()
        assert request.url.path=='/api/upload_photos'
        assert request.headers['Authorization']=='Apikey synthetic'
        assert request.headers['Idempotency-Key']=='saved-request-id'
        assert b'name="photos[]"; filename="media-0.jpg"' in body
        assert b'\xff\xd8test-jpeg' in body and b'https://' not in body
        return httpx.Response(200,json={'success':True,'request_id':'saved-request-id'})
    original=httpx.AsyncClient
    monkeypatch.setattr(upload_post.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    post=Post(id=uuid.uuid4(),body_text='caption',media_asset_ids=['image'],generation_meta={'buffer_delivery':{'provider':'upload_post','request_id':'saved-request-id'}})
    ref=upload_post.UploadPostMediaRef('image','',mime='image/jpeg',data=b'\xff\xd8test-jpeg')
    result=await upload_post.UploadPostAdapter().publish(Credentials('synthetic',external_user_id='profile'),Channel(handle='@sally_in_nylons'),post,[ref])
    assert len(calls)==2 and result.raw['status']=='sending' and result.external_post_id=='saved-request-id'


@pytest.mark.asyncio
async def test_fresh_stop_blocks_before_http_request(monkeypatch):
    from autoposter.services import runtime
    monkeypatch.setattr(runtime,'assert_external_write_allowed',AsyncMock(side_effect=ExternalActionBlocked('stopped')))
    with pytest.raises(ExternalActionBlocked):
        await upload_post.request('synthetic','/api/upload_photos',files=[('photos[]',('test.jpg',b'test','image/jpeg'))])


@pytest.mark.asyncio
async def test_timeout_is_uncertain_and_no_automatic_retry(monkeypatch):
    from autoposter.services import runtime
    monkeypatch.setattr(runtime,'assert_external_write_allowed',AsyncMock())
    calls=[]
    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout('simulated',request=request)
    original=httpx.AsyncClient
    monkeypatch.setattr(upload_post.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(fail),**kwargs))
    with pytest.raises(BufferUncertain):
        await upload_post.request('synthetic','/api/upload_photos',files=[('photos[]',('test.jpg',b'test','image/jpeg'))],request_id='durable')
    assert len(calls)==1
