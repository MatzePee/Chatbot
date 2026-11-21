from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from autoposter.adapters import upload_post
from autoposter.adapters.base import AdapterError
from autoposter.models import Channel, ChannelCredential, AppSetting
from autoposter.services import followers

@pytest.mark.asyncio
@pytest.mark.parametrize('count',[0,42,None,True,-1,'42'])
async def test_follower_response_is_strict_and_read_only(monkeypatch,count):
    verify=AsyncMock(return_value={});request=AsyncMock(return_value={'instagram':{'followers':count}})
    monkeypatch.setattr(upload_post,'verify_account',verify);monkeypatch.setattr(upload_post,'request',request)
    cred=SimpleNamespace(access_token='synthetic',external_user_id='my profile')
    if type(count) is int and count>=0:assert await upload_post.fetch_followers(cred,object())==count
    else:
        with pytest.raises(AdapterError):await upload_post.fetch_followers(cred,object())
    assert request.call_args.args[1]=='/api/analytics/my%20profile'
    assert request.call_args.kwargs=={'params':{'platforms':'instagram'}}
    verify.assert_awaited_once()

@pytest.mark.asyncio
async def test_count_history_throttle_and_failure_preserve_last_measurement(db,monkeypatch):
    now=datetime(2026,11,21,7,tzinfo=timezone.utc)
    row=ChannelCredential(platform='instagram',external_user_id='profile',raw_meta={'provider':'upload_post','destination':'@test','instagram_account_id':'17842'})
    db.add(row);await db.flush()
    channel=Channel(platform='instagram',display_name='Test',handle='@test',credential_id=row.id,verified_account='test',last_verified_at=now,is_active=True,health='ok')
    db.add(channel);await db.commit()
    monkeypatch.setattr(followers.credentials,'to_credentials',lambda _:SimpleNamespace(access_token='synthetic'))
    fetch=AsyncMock(return_value=123);monkeypatch.setattr(upload_post,'fetch_followers',fetch)
    assert await followers.collect(db,now=now,platform='instagram')==1
    assert await followers.collect(db,now=now+timedelta(minutes=1),platform='instagram')==0
    fetch.assert_awaited_once()
    result=(await followers.overview(db,now=now,platform='instagram'))[0]
    assert result['current']==123 and result['yesterday']['count'] is None
    fetch.side_effect=AdapterError('sensitive provider error')
    assert await followers.collect(db,now=now+timedelta(days=1),platform='instagram')==0
    result=(await followers.overview(db,now=now+timedelta(days=1),platform='instagram'))[0]
    assert result['current']==123 and result['stale'] and 'sensitive' not in result['status']
    assert channel.health=='ok'
    row.raw_meta={**row.raw_meta,'destination':'@different'}
    assert followers.account_binding(channel,row,'instagram') is None
    assert followers.cache_key('instagram','17842')=='instagram_followers:17842'
