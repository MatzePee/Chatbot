"""Persisted AutoChat stop at real send transport; synthetic file DBs only."""
import sqlite3
import time
import httpx
import pytest
from app import db, fanvue, poller


def _draft(status='pending'):
    return db.create_draft(user_uuid='synthetic-fan', status=status,
                           generated_text='Synthetic reply', auto_send=1,
                           scheduled_send_at=time.time()-1)


def _commit_running(value):
    # Independent connection models a stop saved by another request/process.
    with sqlite3.connect(db.get_conn().execute('PRAGMA database_list').fetchone()[2]) as conn:
        import json
        conn.execute('UPDATE settings SET value=? WHERE key=?',
                     (json.dumps(value), 'bot_running'))


def _transport(monkeypatch):
    sent=[]
    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={'messageUuid':'synthetic-message'})
    client=httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(fanvue.httpx, 'request', client.request)
    monkeypatch.setattr(fanvue, '_headers', lambda: {'Authorization':'Bearer synthetic-secret'})
    monkeypatch.setattr(fanvue, 'is_connected', lambda: True)
    monkeypatch.setattr(poller, '_within_active_hours', lambda: True)
    monkeypatch.setattr(poller.time, 'sleep', lambda _: None)
    return sent,client


def test_auto_pause_committed_after_claim_before_transport(monkeypatch):
    db.set_setting('bot_running', True)
    draft=_draft()
    sent,client=_transport(monkeypatch)
    def recheck(_):
        _commit_running(False)
        return None
    monkeypatch.setattr(poller, '_check_stale_before_send', recheck)
    try:
        poller.send_due_cycle()
        row=db.get_draft(draft)
        assert sent == [], 'pause committed after claim must block real HTTP send'
        assert row['status']=='pending' and row['auto_send']==0
        assert row['scheduled_send_at'] is None
        assert row['error'] and 'synthetic-secret' not in row['error']
    finally:
        client.close()


def test_manual_approval_remains_intentional_when_automation_paused(monkeypatch):
    db.set_setting('bot_running', False)
    db.set_setting('draft_recheck_enabled', False)
    draft=_draft('approved')
    sent,client=_transport(monkeypatch)
    try:
        assert poller.send_draft_now(draft, 'Operator approved')
        assert len(sent)==1
        assert db.get_draft(draft)['status']=='sent'
    finally:
        client.close()


@pytest.mark.parametrize('call', ['generate_retry','analyze_image','analyze_incoming_image',
                                  'classify_message','confirm_ppv','consolidate_memory'])
def test_automatic_llm_stop_blocks_all_real_content_functions(monkeypatch, call):
    from app import openrouter
    from autoposter.services.runtime import autochat_automation
    from autoposter.adapters.base import ExternalActionBlocked
    db.set_setting('bot_running', True)
    db.set_setting('openrouter_api_key', 'synthetic-secret')
    _commit_running(False)
    sent,client=_transport(monkeypatch)
    monkeypatch.setattr(openrouter.httpx, 'post', client.post)
    args={
        'generate_retry': ([{'role':'user','content':'synthetic'}],),
        'analyze_image': ('https://synthetic.invalid/image',),
        'analyze_incoming_image': ('https://synthetic.invalid/image',),
        'classify_message': ('synthetic',),
        'confirm_ppv': ([], 'me', 'synthetic'),
        'consolidate_memory': ({}, [{'text':'synthetic','role':'user'}]),
    }
    try:
        with autochat_automation(), pytest.raises(ExternalActionBlocked):
            getattr(openrouter, call)(*args[call])
        assert sent == []
    finally:
        client.close()


def test_pause_during_credential_resolution_stops_transport(monkeypatch):
    db.set_setting('bot_running', True)
    draft=_draft()
    db.set_setting('draft_recheck_enabled', False)
    sent,client=_transport(monkeypatch)
    def headers():
        _commit_running(False)
        return {'Authorization':'Bearer synthetic-secret'}
    monkeypatch.setattr(fanvue, '_headers', headers)
    try:
        poller.send_due_cycle()
        assert sent == []
        assert db.get_draft(draft)['status']=='pending'
    finally:
        client.close()


@pytest.mark.parametrize('status', ['pending','approved'])
def test_unresolved_claim_is_released_for_manual_review(status):
    draft=_draft(status)
    assert db.claim_manual_draft(draft)
    db.finish_auto_claim(draft)
    assert db.get_draft(draft)['auto_send']==0
    assert db.claim_manual_draft(draft), 'manual retry must not remain permanently claimed'


@pytest.fixture
def six_owners(tmp_path, monkeypatch):
    import asyncio
    from uuid import uuid4
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import create_async_engine
    from autoposter import db as database, models
    path=tmp_path/'autopost.db'
    sync=create_engine('sqlite:///'+str(path))
    database.Base.metadata.create_all(sync)
    owners=[uuid4() for _ in range(6)]
    with sync.begin() as conn:
        conn.execute(models.Character.__table__.insert(),
                     [{'id':o, 'name':f'Synthetic {i}'} for i,o in enumerate(owners)])
    engine=create_async_engine('sqlite+aiosqlite:///'+str(path))
    monkeypatch.setattr(database, 'engine', engine)
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    try:
        yield owners
    finally:
        asyncio.run(engine.dispose())
        sync.dispose()


def test_six_character_real_send_and_llm_isolation(six_owners, monkeypatch):
    from app import openrouter
    from autoposter.workspaces import character_context
    from autoposter.services.runtime import autochat_automation
    from autoposter.adapters.base import ExternalActionBlocked
    sent,client=_transport(monkeypatch)
    def completion(*args, **kwargs):
        sent.append('llm')
        return httpx.Response(200, json={'choices':[{'message':{'content':'Synthetic text'}}]})
    monkeypatch.setattr(openrouter.httpx, 'post', completion)
    drafts={}
    try:
        for owner in six_owners:
            with character_context(owner):
                db.init_db()
                db.set_setting('bot_running', True)
                db.set_setting('draft_recheck_enabled', False)
                db.set_setting('openrouter_api_key', 'synthetic-secret')
                drafts[owner]=_draft()
        # Stop committed independently after every character has queued work.
        with character_context(six_owners[0]):
            _commit_running(False)
        for i,owner in enumerate(six_owners):
            with character_context(owner):
                before=len(sent)
                poller.send_due_cycle()
                assert db.get_draft(drafts[owner])['status']==('pending' if i==0 else 'sent')
                with autochat_automation():
                    if i==0:
                        with pytest.raises(ExternalActionBlocked):
                            openrouter.generate([], api_key='synthetic-secret')
                    else:
                        assert openrouter.generate([], api_key='synthetic-secret')=='Synthetic text'
                assert len(sent)-before==(0 if i==0 else 2)
    finally:
        client.close()


@pytest.mark.parametrize('owner_kind', ['missing','unknown','archived'])
def test_manual_content_clients_fail_closed_for_invalid_owner(six_owners, monkeypatch, owner_kind):
    from contextlib import nullcontext
    from uuid import uuid4
    from app import openrouter
    from autoposter import db as database
    from autoposter.workspaces import character_context
    from autoposter.adapters.base import ExternalActionBlocked
    owner=six_owners[0] if owner_kind=='archived' else uuid4()
    if owner_kind=='archived':
        with sqlite3.connect(database.engine.url.database) as conn:
            conn.execute('UPDATE character SET is_archived=1 WHERE id=?',(owner.hex,))
    sent,client=_transport(monkeypatch)
    monkeypatch.setattr(openrouter.httpx, 'post', client.post)
    context=nullcontext() if owner_kind=='missing' else character_context(owner)
    try:
        with context:
            with pytest.raises(ExternalActionBlocked) as error:
                fanvue.send_message('synthetic-fan','Synthetic reply')
            assert 'synthetic-secret' not in str(error.value)
            # Explicit settings avoid any unrelated missing-key failure.
            if owner_kind!='missing':
                db.init_db()
            with pytest.raises((ExternalActionBlocked, ValueError)):
                openrouter.generate([], api_key='synthetic-secret', model='synthetic')
        assert sent == []
    finally:
        client.close()
