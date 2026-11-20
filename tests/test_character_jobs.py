import asyncio,uuid
import pytest
from autoposter.workspaces import character_context,current_character,CharacterBoundaryError
from autoposter.services import runs


@pytest.mark.asyncio
async def test_background_run_captures_owner_and_foreign_tab_cannot_read_or_cancel():
    a,b=uuid.uuid4(),uuid.uuid4();release=asyncio.Event();observed=[]
    async def work(run):
        await release.wait();observed.append(current_character())
        return {'owner':str(current_character())}
    with character_context(a):
        run=runs.create('Alpha private work');runs.launch(run,work)
        assert run.character_id==a
        with pytest.raises(AttributeError):run.character_id=b
    with character_context(b):
        assert runs.get(run.id) is None
        assert run not in runs.active()
        assert runs.cancel(run.id) is False
        with pytest.raises(CharacterBoundaryError):runs.launch(run,work)
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert observed==[a]
    with character_context(a):
        assert runs.get(run.id).result=={'owner':str(a)}


def test_unscoped_background_run_rejected_in_character_mode(monkeypatch):
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    with pytest.raises(CharacterBoundaryError):runs.create('must name workspace')


def test_thread_captures_character_before_other_tab_switches():
    import threading
    from app.character_tasks import CharacterThread
    a,b=uuid.uuid4(),uuid.uuid4();release=threading.Event();observed=[]
    def work():
        assert release.wait(5)
        observed.append(current_character())
    with character_context(a):
        worker=CharacterThread(target=work,daemon=True)
        assert worker.character_id==a
        with pytest.raises(AttributeError):worker.character_id=b
        worker.start()
    with character_context(b):
        release.set();worker.join(5)
    assert not worker.is_alive() and observed==[a]


def test_thread_without_owner_rejected_when_enabled(monkeypatch):
    from app.character_tasks import CharacterThread
    monkeypatch.setenv('MP_CHARACTERS_ENABLED','1')
    with pytest.raises(CharacterBoundaryError):CharacterThread(target=lambda:None)


def test_real_fanlist_background_refresh_retains_request_owner(monkeypatch):
    import threading
    from app import main
    a,b=uuid.uuid4(),uuid.uuid4();done=threading.Event();observed=[]
    original=main._cache_store
    def stored(key,data):
        try:original(key,data)
        finally:done.set()
    monkeypatch.setattr(main,'_cache_store',stored)
    with character_context(a):
        main._cache_refresh_async('owned-refresh',lambda:observed.append(current_character()) or [{'uuid':'private-a'}])
    with character_context(b):
        assert done.wait(5)
        assert main._list_cache.get('owned-refresh') is None
    assert observed==[a]
    with character_context(a):
        assert main._list_cache['owned-refresh']['data']==[{'uuid':'private-a'}]


def test_group_export_worker_carries_explicit_character(monkeypatch):
    import threading
    from app import main
    a=uuid.uuid4();done=threading.Event();observed=[]
    monkeypatch.setattr(main.fanvue,'is_connected',lambda:True)
    monkeypatch.setattr(main.fanvue,'account_uuid',lambda:'synthetic-account')
    monkeypatch.setattr(main,'_fetch_group_members',lambda **kwargs:[{'uuid':'fan'}])
    def worker(*args):observed.append(current_character());done.set()
    monkeypatch.setattr(main,'_export_group_worker',worker)
    with character_context(a):main.chat_export_all()
    assert done.wait(5) and observed==[a]
