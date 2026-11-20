"""Regression gates for the independent settings review findings."""
import json
import os
import sqlite3
import pytest
from tools.settings_consolidate import consolidate
from fastapi.testclient import TestClient
from app import main as appmain, db


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MP_PREVIEW", "0")
    monkeypatch.setattr(appmain.fanvue, "is_connected", lambda: False)
    return TestClient(appmain.app, follow_redirects=False)


def seed(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as c:
        c.execute('CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        c.execute('INSERT INTO settings VALUES (?,?)', ('openrouter_model', json.dumps('task/model')))


@pytest.mark.parametrize('alias', ['destination', 'other_owner'])
def test_promotion_refuses_hardlinked_database_aliases(tmp_path, capsys, alias):
    root=tmp_path/'active'; root.mkdir()
    source=root/'owners/a/bot.db';seed(source)
    destination=root/'bot.db'
    args=['--active-generation-root',str(root),'--destination','bot.db','--source','a=owners/a/bot.db']
    if alias=='destination':
        os.link(source,destination)
    else:
        seed(destination)
        other=root/'owners/b/bot.db';other.parent.mkdir(parents=True)
        os.link(source,other)
        args.extend(['--source','b=owners/b/bot.db'])
    before=source.read_bytes()
    sources={'a':'owners/a/bot.db'}
    if alias=='other_owner':sources['b']='owners/b/bot.db'
    with pytest.raises(ValueError,match='aliases'):
        consolidate(root,'bot.db',sources,apply=True,stopped_ack=True)
    assert source.read_bytes()==before


def test_common_telegram_checkbox_saves_checked_and_unchecked(client):
    db.set_setting('telegram_enabled', 0)
    assert client.post('/settings/shared/telegram', data={'telegram_enabled_present':'1','telegram_enabled':'1'}).status_code==303
    assert db.get_setting('telegram_enabled')==1
    assert client.post('/settings/shared/telegram', data={'telegram_enabled_present':'1'}).status_code==303
    assert db.get_setting('telegram_enabled')==0
    assert client.post('/settings',data={'telegram_enabled':'1'}).status_code==303
    assert db.get_setting('telegram_enabled')==0


def test_cost_coverage_and_budget_scope_are_explicit(client, tmp_path, monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from autoposter import db as postdb
    from autoposter.models import LlmUsage
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'costs.db'),poolclass=NullPool)
    async def initialize():
        async with engine.begin() as c:
            await c.run_sync(LlmUsage.__table__.create)
    asyncio.run(initialize())
    monkeypatch.setattr(postdb,'SessionLocal',async_sessionmaker(engine,expire_on_commit=False))
    response=client.get('/settings/shared/ai-usage')
    assert response.status_code==200
    assert response.json()['coverage']=='recorded_autopost_only'
    assert response.json()['budget_scope']=='per_character_autopost'
    page=client.get('/settings/shared').text
    assert 'AutoChat-Kosten sind nicht enthalten' in page
    assert 'kein gemeinsames Ausgabenlimit' in page
