import pytest
from uuid import uuid4
from autoposter.workspaces import current_character


@pytest.mark.asyncio
async def test_poller_orchestrator_reconciles_new_archived_and_shutdown_owners(monkeypatch):
    from app.character_workers import CharacterPollers
    from app import poller
    from autoposter import character_dispatch
    a,b=uuid4(),uuid4(); active=[a];events=[]
    async def ids(): return list(active)
    monkeypatch.setattr(character_dispatch,'active_ids',ids)
    monkeypatch.setattr(poller,'start',lambda:events.append(('start',current_character())))
    monkeypatch.setattr(poller,'stop',lambda:events.append(('stop',current_character())))
    workers=CharacterPollers()
    await workers.reconcile()
    active.append(b);await workers.reconcile();await workers.reconcile()
    active.remove(a);await workers.reconcile()
    assert set(workers.owners)=={b}
    await workers.stop()
    assert events==[('start',a),('start',b),('stop',a),('stop',b)]
