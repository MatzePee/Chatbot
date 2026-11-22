from datetime import datetime, timezone
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import httpx
from autoposter.services.social_summary import aggregate
from autoposter.adapters import fanvue
from autoposter.adapters.base import AdapterError

NOW=datetime(2026,11,22,9,tzinfo=timezone.utc)

def account(platform,identity,current,baseline,**changes):
    result={'platform':platform,'account_id':identity,'channel_id':identity,'current':current,'sampled_at':'2026-11-22T08:00:00Z','stale':False,
            'history':[{'date':'2026-11-22','count':current}],**{key:{'count':baseline} for key in ('yesterday','days7','days30')}}
    result.update(changes);return result


def test_total_dedupes_fanvue_channels_and_keeps_platform_accounts_distinct():
    rows=[account('fanvue','same',100,90),account('fanvue','same',100,90),account('x','same',200,180),account('instagram','insta',0,0)]
    result=aggregate(rows,NOW)
    assert result['accounts']==3 and result['current']==300 and result['complete']
    assert result['yesterday']['growth']==30
    assert result['yesterday']['percent']==pytest.approx(30/270*100)
    assert result['history'][-1]['count']==300
    assert result['history'][-2]['count'] is None


@pytest.mark.parametrize('change',[{'current':None},{'stale':True},{'yesterday':{'count':None}}])
def test_incomplete_comparisons_never_produce_fake_growth(change):
    result=aggregate([account('fanvue','a',100,90),{**account('x','b',200,190),**change}],NOW)
    assert result['yesterday']['growth'] is None
    assert result['yesterday']['percent'] is None


def test_missing_counts_are_visible_partial_and_zero_is_a_valid_measurement():
    result=aggregate([account('x','a',5,4),account('instagram','b',None,None)],NOW)
    assert result['current']==5 and not result['complete'] and result['measured']==1
    assert aggregate([account('x','a',0,0)],NOW)['current']==0
    assert aggregate([account('x','a',0,0)],NOW)['yesterday']['growth']==0
    assert aggregate([],NOW)['current'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('identity,count,ok',[('owner',42,True),('owner',0,True),('other',42,False),('owner',None,False),('owner',True,False),('owner',-1,False)])
async def test_fanvue_count_checks_identity_and_uses_only_followers(monkeypatch,identity,count,ok):
    client=SimpleNamespace(get=AsyncMock(return_value=httpx.Response(200,json={'uuid':identity,'fanCounts':{'followersCount':count,'subscribersCount':99}},request=httpx.Request('GET','https://example.invalid/users/me'))))
    @asynccontextmanager
    async def http_client(**kw):yield client
    monkeypatch.setattr(fanvue,'http_client',http_client)
    adapter=fanvue.FanvueAdapter.__new__(fanvue.FanvueAdapter);adapter.base='https://example.invalid';adapter._headers=lambda cred:{}
    cred=SimpleNamespace(external_user_id='owner')
    if ok:assert await adapter.fetch_followers(cred)==count
    else:
        with pytest.raises(AdapterError):await adapter.fetch_followers(cred)
    assert client.get.call_args.args[0]=='https://example.invalid/users/me'


@pytest.mark.asyncio
async def test_fanvue_shared_connection_collected_once_for_two_posting_channels(db,monkeypatch):
    import sys, types
    from autoposter.models import Channel
    from autoposter.services import followers
    shared=types.ModuleType('autoposter.services.chatbot_fanvue')
    shared.chatbot_db=SimpleNamespace(get_tokens=lambda:{'account_uuid':'owner'})
    shared.connected=lambda channel:True
    shared.credentials=AsyncMock(return_value=SimpleNamespace(external_user_id='owner'))
    monkeypatch.setitem(sys.modules,shared.__name__,shared)
    fetch=AsyncMock(return_value=50)
    monkeypatch.setattr(fanvue,'FanvueAdapter',lambda:SimpleNamespace(fetch_followers=fetch))
    db.add_all([Channel(platform='fanvue',display_name='Followers',handle='@test',is_active=True),Channel(platform='fanvue',display_name='Subscribers',handle='@test',is_active=True)])
    await db.commit()
    assert await followers.collect(db,now=NOW,platform='fanvue',force=True)==1
    fetch.assert_awaited_once()
    accounts=await followers.overview(db,now=NOW,platform='fanvue')
    assert len(accounts)==1 and accounts[0]['current']==50
