"""Real sqlite partition tests, synthetic accounts and overlapping fan IDs."""
import uuid
import pytest
from app import db


def test_autochat_tokens_chats_and_drafts_are_character_owned(tmp_path, monkeypatch):
    from autoposter.workspaces import character_context
    a,b = uuid.uuid4(),uuid.uuid4()
    with character_context(a):
        db.init_db()
        assert db.get_tokens() is None
        db.save_tokens(access_token='synthetic-a',refresh_token='refresh-a',expires_at=1234,scope='read:chat')
        db.upsert_chat('same-fan',handle='alpha')
        db.get_conn().execute("INSERT INTO drafts(user_uuid,edited_text,status) VALUES('same-fan','Alpha private','pending')")
        db.get_conn().commit()
    with character_context(b):
        db.init_db()
        assert db.get_tokens() is None, 'New character must never inherit credentials'
        assert db.get_chat('same-fan') is None
        assert db.count_drafts('pending') == 0
        assert db.get_setting('bot_running') is False
        db.upsert_chat('same-fan',handle='beta')
    with character_context(a):
        assert db.get_tokens()['access_token'] == 'synthetic-a'
        assert db.get_chat('same-fan')['handle'] == 'alpha'
        assert db.count_drafts('pending') == 1
    assert db.get_tokens() is None
    assert db.get_chat('same-fan') is None


@pytest.mark.asyncio
async def test_fanvue_credentials_refuse_foreign_channel_before_refresh(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from autoposter.workspaces import character_context
    from autoposter.services.chatbot_fanvue import credentials
    from autoposter.adapters.base import AdapterError
    a,b=uuid.uuid4(),uuid.uuid4()
    with character_context(b):
        db.init_db()
        db.save_tokens(access_token='synthetic-beta',refresh_token='refresh-beta',expires_at=99999999999,account_uuid='shared-external-id')
        channel=SimpleNamespace(character_id=a,fanvue_account_uuid='shared-external-id',handle='same')
        import pytest
        with pytest.raises(AdapterError):await credentials(channel)


def test_autochat_database_has_no_unscoped_fallback_when_enabled(monkeypatch):
    import pytest
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    with pytest.raises(ValueError,match='Character'):db.get_tokens()


@pytest.mark.asyncio
async def test_two_character_refresh_uses_own_client_and_rotating_chain(monkeypatch):
    import asyncio, httpx
    from urllib.parse import parse_qs
    from types import SimpleNamespace
    from app import fanvue
    from autoposter.workspaces import character_context
    from autoposter.services.chatbot_fanvue import credentials
    a,b=uuid.uuid4(),uuid.uuid4();owners={a:'alpha',b:'beta'}
    for owner,label in owners.items():
        with character_context(owner):
            db.init_db();db.set_setting('fanvue_client_id','client-'+label)
            db.set_setting('fanvue_client_secret','synthetic-secret-'+label)
            db.save_tokens(access_token='expired-'+label,refresh_token='old-'+label,
                           expires_at=0,scope='read:chat',account_uuid='account-'+label)
    calls=[]
    def handler(request):
        assert request.method=='POST' and request.url.path=='/oauth2/token'
        body=parse_qs(request.content.decode());label=body['refresh_token'][0].removeprefix('old-')
        assert body['client_id']==['client-'+label]
        assert body['grant_type']==['refresh_token']
        calls.append(label)
        return httpx.Response(200,json={'access_token':'new-'+label,'refresh_token':'rotated-'+label,'expires_in':3600,'scope':'read:chat'})
    client=httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(fanvue.httpx,'post',client.post)
    async def run(owner,label):
        with character_context(owner):
            channel=SimpleNamespace(character_id=owner,fanvue_account_uuid='account-'+label,handle=label)
            result=await credentials(channel)
            assert db.get_tokens()['refresh_token']=='rotated-'+label
            return result.access_token,result.external_user_id
    async def both():return await asyncio.gather(*(run(o,n) for o,n in owners.items()))
    try:assert await both()==[('new-alpha','account-alpha'),('new-beta','account-beta')]
    finally:client.close()
    assert sorted(calls)==['alpha','beta']


def test_autochat_fanlist_cache_is_partitioned_even_with_same_provider_id(monkeypatch):
    from app import main
    from autoposter.workspaces import character_context
    a,b=uuid.uuid4(),uuid.uuid4()
    with character_context(a):
        assert main._cached('same-list',lambda:[{'uuid':'fan-a'}])==[{'uuid':'fan-a'}]
        main._draft_context_cache['same-fan']={'text':'Alpha private'}
        main._insights_cache['same-fan']={'amount':111}
    with character_context(b):
        assert main._cached('same-list',lambda:[{'uuid':'fan-b'}])==[{'uuid':'fan-b'}]
        assert main._draft_context_cache.get('same-fan') is None
        assert main._insights_cache.get('same-fan') is None
    with character_context(a):
        assert main._cached('same-list',lambda:[])==[{'uuid':'fan-a'}]


def test_chat_exports_and_export_progress_are_namespaced(tmp_path,monkeypatch):
    from app import main
    from autoposter.workspaces import character_context,current_character
    from pathlib import Path
    a,b=uuid.uuid4(),uuid.uuid4();paths={}
    monkeypatch.setattr(main,'EXPORT_DIR',str(tmp_path))
    monkeypatch.setattr(main.fanvue,'full_message_history',lambda user:[{'text':'private-'+str(current_character()),'sender':{'uuid':'fan'}}])
    with character_context(a):
        path,_=main._write_chat_export('same-fan','same-handle','Same Name','me')
        paths[a]=Path(path)
        main._export_state.update(running=True,done=7)
    with character_context(b):
        path,_=main._write_chat_export('same-fan','same-handle','Same Name','me')
        paths[b]=Path(path)
        assert main.export_status()['running'] is False
        assert main.export_status()['done']==0
    assert paths[a]!=paths[b]
    assert str(a) in str(paths[a]) and str(b) in str(paths[b])
    assert 'private-'+str(a) in paths[a].read_text()
    assert 'private-'+str(b) not in paths[a].read_text()
