"""Token alerts must describe the current credential check, not stale flags."""
from contextlib import asynccontextmanager
import pytest
from sqlalchemy import select
from autoposter import jobs
from autoposter.models import Channel,Notification


@pytest.mark.asyncio
async def test_connected_fanvue_recovers_stale_reauth_without_new_alert(db,monkeypatch):
    channel=Channel(platform='fanvue',display_name='Fanvue · Subscriber',handle='creator',
                    is_active=True,health='needs_reauth',health_note='Gemeinsame Fanvue-Verbindung')
    db.add(channel);await db.flush()
    @asynccontextmanager
    async def scope():yield db
    monkeypatch.setattr(jobs,'session_scope',scope)
    monkeypatch.setattr(jobs.cred_service,'is_connected',lambda c:True)
    checked=[]
    async def valid(session,current):checked.append(current.id);return object()
    monkeypatch.setattr(jobs.cred_service,'get_valid_credentials',valid)
    result=await jobs.token_health()
    assert result['checked']==1 and checked==[channel.id]
    assert channel.health=='ok' and channel.health_note==''
    assert (await db.execute(select(Notification))).scalars().all()==[]


@pytest.mark.asyncio
async def test_actual_credential_failure_still_creates_alert(db,monkeypatch):
    channel=Channel(platform='fanvue',display_name='Fanvue · Subscriber',handle='creator',
                    is_active=True,health='needs_reauth')
    db.add(channel);await db.flush()
    @asynccontextmanager
    async def scope():yield db
    monkeypatch.setattr(jobs,'session_scope',scope)
    monkeypatch.setattr(jobs.cred_service,'is_connected',lambda c:True)
    async def invalid(session,current):raise RuntimeError('auth rejected')
    monkeypatch.setattr(jobs.cred_service,'get_valid_credentials',invalid)
    result=await jobs.token_health()
    notes=(await db.execute(select(Notification))).scalars().all()
    assert result['checked']==0
    assert len(notes)==1 and notes[0].title=='Token-Problem: Fanvue · Subscriber'
    assert 'auth rejected' in notes[0].body
