"""Boundary regression cases for persisted stops; no external requests."""
import sqlite3
import httpx
import pytest
from app import db, fanvue, openrouter, poller
from autoposter.services.runtime import autochat_automation
from autoposter.adapters.base import ExternalActionBlocked
from tests.test_pause_acceptance import _transport, _commit_running, _draft, six_owners


def test_paused_automation_preserves_read_only_post_batch(monkeypatch):
    db.set_setting('bot_running', False)
    sent,client=_transport(monkeypatch)
    try:
        with autochat_automation():
            assert fanvue.fan_insights_bulk(['synthetic-fan'])
            fanvue.list_messages('synthetic-fan', mark_as_read=False)
        assert len(sent)==2
    finally:
        client.close()


def test_read_receipt_get_is_not_a_read_only_bypass(monkeypatch):
    db.set_setting('bot_running', False)
    sent,client=_transport(monkeypatch)
    try:
        with autochat_automation(), pytest.raises(ExternalActionBlocked):
            fanvue.list_messages('synthetic-fan', mark_as_read=True)
        assert sent==[]
    finally:
        client.close()


@pytest.mark.parametrize('value', [False, None, 'true', 1, {}, []])
def test_invalid_persistent_master_never_sends_automatic_content(monkeypatch,value):
    db.set_setting('bot_running',value)
    sent,client=_transport(monkeypatch)
    try:
        with autochat_automation(), pytest.raises(ExternalActionBlocked):
            fanvue.send_message('synthetic-fan','synthetic')
        assert sent==[]
    finally:
        client.close()


def test_stale_chat_transaction_does_not_hide_committed_stop(monkeypatch):
    db.set_setting('bot_running', True)
    conn=db.get_conn()
    conn.execute('BEGIN')
    assert conn.execute("SELECT value FROM settings WHERE key='bot_running'").fetchone()[0]=='true'
    _commit_running(False)
    sent,client=_transport(monkeypatch)
    try:
        with autochat_automation(), pytest.raises(ExternalActionBlocked):
            fanvue.send_message('synthetic-fan','synthetic')
        assert sent==[]
    finally:
        conn.rollback()
        client.close()


def test_no_automatic_llm_retry_after_pause_committed_by_first_response(monkeypatch):
    db.set_setting('bot_running',True)
    calls=[]
    def handler(request):
        calls.append(request)
        _commit_running(False)
        return httpx.Response(200,json={'choices':[{'message':{'content':''}}]})
    client=httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(openrouter.httpx,'post',client.post)
    try:
        with autochat_automation(),pytest.raises(ExternalActionBlocked):
            openrouter.generate_retry([],model='synthetic',api_key='synthetic-secret')
        assert len(calls)==1
    finally:
        client.close()


def test_manual_claim_blocked_after_owner_archived_during_recheck(six_owners,monkeypatch):
    from autoposter import db as database
    from autoposter.workspaces import character_context
    owner=six_owners[0]
    sent,client=_transport(monkeypatch)
    def recheck(_):
        with sqlite3.connect(database.engine.url.database) as conn:
            conn.execute('UPDATE character SET is_archived=1 WHERE id=?',(owner.hex,))
        return None
    monkeypatch.setattr(poller,'_check_stale_before_send',recheck)
    try:
        with character_context(owner):
            db.init_db()
            draft=_draft('approved')
            assert not poller.send_draft_now(draft,'Operator edit')
            row=db.get_draft(draft)
            assert row['status']=='pending' and row['auto_send']==0
            assert row['edited_text']=='Operator edit'
            assert row['error'] and 'synthetic-secret' not in row['error']
        assert sent==[]
    finally:
        client.close()


def test_safety_reader_connections_closed_before_send(six_owners,monkeypatch):
    from autoposter.workspaces import character_context
    original=sqlite3.connect
    opened=[]
    closed=set()
    class Tracked(sqlite3.Connection):
        def close(self):
            closed.add(id(self))
            super().close()
    def connect(*args,**kwargs):
        conn=original(*args,**kwargs,factory=Tracked)
        opened.append(conn)
        return conn
    sent,client=_transport(monkeypatch)
    try:
        with character_context(six_owners[0]):
            db.init_db()
            monkeypatch.setattr(sqlite3,'connect',connect)
            fanvue.send_message('synthetic-fan','synthetic')
        assert len(sent)==1
        assert opened and all(id(conn) in closed for conn in opened)
    finally:
        for conn in opened:
            conn.close()
        client.close()
