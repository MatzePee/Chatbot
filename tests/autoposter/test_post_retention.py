from datetime import datetime, timedelta, timezone
import pytest
from autoposter.services import post_retention as retention
from autoposter.config import settings
from autoposter.adapters.base import Credentials
from autoposter.adapters.fanvue import FanvueAdapter

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
UUID = '123e4567-e89b-12d3-a456-426614174000'

def post(**changes):
    value = {'uuid': UUID, 'publishedAt': (NOW-timedelta(days=31)).isoformat(), 'publishAt': None, 'expiresAt': None, 'price': 0, 'isPinned': False}
    value.update(changes)
    return value

@pytest.mark.parametrize('change,ppv,expected', [
    ({},False,True), ({'price':0},False,True), ({'price':300},False,False),
    ({'price':300},True,True), ({'price':None},True,True),
    ({'price':'300'},True,False), ({'price':-1},True,False),
    ({'isPinned':True},False,False), ({'isPinned':True},True,False),
    ({'isPinned':None},False,False), ({'isPinned':0},True,False),
    ({'publishedAt':(NOW-timedelta(days=30)).isoformat()},True,False),
    ({'publishedAt':None},True,False),
    ({'publishAt':(NOW+timedelta(days=1)).isoformat()},True,False),
    ({'expiresAt':(NOW-timedelta(seconds=1)).isoformat()},True,False),
])
def test_exact_age_and_safe_price(change, ppv, expected):
    assert retention.eligible(post(**change), NOW, 30, ppv) is expected

def test_missing_or_ambiguous_price_is_retained():
    value=post();value.pop('price')
    assert retention.eligible(value,NOW,30,True) is False
    assert retention.eligible(post(price=None),NOW,30,False) is True
    assert retention.eligible(post(price=float('nan')),NOW,30,True) is False

def test_missing_or_ambiguous_pinned_flag_is_retained():
    value=post(); value.pop('isPinned')
    assert retention.eligible(value, NOW, 30, False) is False
    assert retention.eligible(post(isPinned='false'), NOW, 30, True) is False


@pytest.mark.asyncio
async def test_preview_paginates_all_account_posts_and_never_deletes(db, monkeypatch):
    monkeypatch.setattr(retention, 'accounts', lambda db: fake_accounts())
    async def creds(db, ch): return Credentials('synthetic', external_user_id='account-a', scopes=['read:post','write:post'])
    monkeypatch.setattr(retention.credentials, 'get_valid_credentials', creds)
    calls=[]
    async def request(self, cred, method, path, **kw):
        calls.append((method,path))
        if path == '/users/me': return {'uuid':'account-a'}
        if path == '/v1/posts':
            if 'cursor' not in kw['params']:
                return {'data':[post(),post(uuid='123e4567-e89b-12d3-a456-426614174001', price=300),
                                post(uuid='123e4567-e89b-12d3-a456-426614174002', isPinned=True)], 'nextCursor':'next'}
            return {'data':[post()], 'nextCursor':None}
        return post()
    async def fake_accounts(): return [(object(),'account-a')]
    monkeypatch.setattr(FanvueAdapter,'retention_request',request)
    result=await retention.preview(db, days=30, delete_ppv=False, now=NOW)
    assert result['accounts'][0]['candidates'] == [UUID]
    assert all(method=='GET' for method,path in calls)

@pytest.mark.asyncio
async def test_disabled_run_never_calls_fanvue(db, monkeypatch):
    async def forbidden(db): raise AssertionError('no Fanvue requests when disabled')
    monkeypatch.setattr(retention,'accounts',forbidden)
    monkeypatch.setattr(settings,'post_retention_active',False,raising=False)
    assert (await retention.run(db,days=30,delete_ppv=False,manual=True))['status']=='disabled'

@pytest.mark.asyncio
async def test_delete_rechecks_price_and_age_and_verifies(db, monkeypatch):
    monkeypatch.setattr(settings,'post_retention_active',True,raising=False)
    monkeypatch.setattr(settings,'dry_run',False)
    async def fake_accounts(db): return [(object(),'account-a')]
    monkeypatch.setattr(retention,'accounts',fake_accounts)
    async def creds(db, ch): return Credentials('synthetic',external_user_id='account-a',scopes=['read:post','write:post'])
    monkeypatch.setattr(retention.credentials,'get_valid_credentials',creds)
    first=post(); second=post(uuid='123e4567-e89b-12d3-a456-426614174001')
    third=post(uuid='123e4567-e89b-12d3-a456-426614174002')
    fourth=post(uuid='123e4567-e89b-12d3-a456-426614174003', isPinned=True)
    deleted=set(); calls=[]
    async def request(self, cred, method, path, **kw):
        calls.append((method,path))
        if path=='/users/me': return {'uuid':'account-a'}
        if path=='/v1/posts': return {'data':[first,second,third,fourth], 'nextCursor':None}
        if method=='DELETE': deleted.add(path)
        elif method=='GET':
            if path.endswith(first['uuid']): return post(price=300) # changed between list and precheck
            if path.endswith(third['uuid']): return post(uuid=third['uuid'],isPinned=True) # newly pinned
            if path in deleted: return None # 404 confirmed by adapter
            return second
        return None
    monkeypatch.setattr(FanvueAdapter,'retention_request',request)
    result=await retention.run(db,days=30,delete_ppv=False,manual=True,now=NOW)
    assert result['accounts'][0]['deleted']==1
    assert ('DELETE','/v1/posts/'+first['uuid']) not in calls
    assert ('DELETE','/v1/posts/'+third['uuid']) not in calls
    assert not any(path.endswith(fourth['uuid']) for _,path in calls)
    assert result['accounts'][0]['skipped'] == 2
    assert ('DELETE','/v1/posts/'+second['uuid']) in calls
    assert ('GET','/v1/posts/'+second['uuid']) in calls
