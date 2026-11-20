"""Synthetic worker concurrency: never contacts providers."""
import threading
import uuid
import pytest
from autoposter.workspaces import character_context, current_character, CharacterBoundaryError


def test_poller_workers_status_and_stop_are_independent(monkeypatch):
    from app import poller
    a, b = uuid.uuid4(), uuid.uuid4()
    entered = {a: threading.Event(), b: threading.Event()}
    observed = []
    def loop():
        owner = current_character()
        observed.append(owner)
        entered[owner].set()
        poller._worker_state().stop.wait(5)
    monkeypatch.setattr(poller, '_loop', loop)
    monkeypatch.setattr(poller, '_watchdog_loop', lambda: poller._worker_state().stop.wait(5))
    monkeypatch.setattr(poller.memory, 'reset_stale_backfills', lambda: 0)
    monkeypatch.setattr(poller.db, 'log', lambda *args: None)
    try:
        for owner in (a, b):
            with character_context(owner):
                poller.start()
                assert entered[owner].wait(2)
                assert poller.status()['alive']
        with character_context(a):
            poller._status['last_error'] = 'alpha only'
            poller.stop()
            assert not poller.status()['alive']
        with character_context(b):
            assert poller.status()['alive']
            assert poller.status()['last_error'] is None
        assert observed == [a, b]
    finally:
        for owner in (a, b):
            with character_context(owner):
                poller.stop()


def test_unowned_worker_state_fails_closed(monkeypatch):
    from app import poller
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    with pytest.raises(CharacterBoundaryError):
        poller.status()
    with pytest.raises(CharacterBoundaryError):
        poller.import_status()


def test_purchase_import_is_owned_and_runs_independently(monkeypatch):
    from app import poller
    a, b = uuid.uuid4(), uuid.uuid4()
    entered = {a: threading.Event(), b: threading.Event()}
    release = threading.Event()
    done = {a: threading.Event(), b: threading.Event()}
    def worker(*args):
        owner = current_character()
        entered[owner].set()
        release.wait(5)
        poller._import_state.update(running=False, offers=str(owner))
        done[owner].set()
    monkeypatch.setattr(poller, '_purchase_import_worker', worker)
    try:
        for owner in (a, b):
            with character_context(owner):
                assert poller.start_purchase_import([], check_api=False)
                assert entered[owner].wait(2)
                assert not poller.start_purchase_import([], check_api=False)
        release.set()
        for owner in (a, b):
            assert done[owner].wait(2)
            with character_context(owner):
                assert poller.import_status()['offers'] == str(owner)
    finally:
        release.set()


def test_fanlist_inflight_does_not_block_other_owner(monkeypatch):
    from app import main
    a, b = uuid.uuid4(), uuid.uuid4()
    entered = {a: threading.Event(), b: threading.Event()}
    release = threading.Event()
    done = {a: threading.Event(), b: threading.Event()}
    def loader():
        owner = current_character()
        entered[owner].set()
        release.wait(5)
        return []
    monkeypatch.setattr(main, '_cache_store', lambda *args: done[current_character()].set())
    try:
        for owner in (a, b):
            with character_context(owner):
                main._cache_refresh_async('same-list', loader)
                assert entered[owner].wait(2)
        release.set()
        assert all(event.wait(2) for event in done.values())
    finally:
        release.set()


def test_memory_backfill_queue_worker_and_status_are_owned(monkeypatch):
    from app import memory
    a, b = uuid.uuid4(), uuid.uuid4()
    entered = {a: threading.Event(), b: threading.Event()}
    release = threading.Event()
    done = {a: threading.Event(), b: threading.Event()}
    def backfill(user_uuid, name, auto=False):
        owner = current_character()
        entered[owner].set()
        release.wait(5)
        memory._backfill_state.update(running=False, name=str(owner))
        done[owner].set()
    monkeypatch.setattr(memory, '_backfill_one', backfill)
    monkeypatch.setattr(memory.db, 'get_setting', lambda key, default=None: 'synthetic' if key == 'openrouter_api_key' else default)
    monkeypatch.setattr(memory.db, 'update_chat', lambda *args, **kwargs: None)
    monkeypatch.setattr(memory.db, 'log', lambda *args: None)
    try:
        for owner in (a, b):
            with character_context(owner):
                assert memory.start_backfill('same-fan', str(owner))[0]
                assert entered[owner].wait(2)
        release.set()
        for owner in (a, b):
            assert done[owner].wait(2)
            with character_context(owner):
                assert memory.backfill_status()['name'] == str(owner)
    finally:
        release.set()


def test_prio2_throttle_is_per_character(monkeypatch):
    from app import poller
    a, b = uuid.uuid4(), uuid.uuid4()
    observed = []
    monkeypatch.setattr(poller.db, 'get_setting', lambda key, default=None: 'same-list' if key == 'prio2_custom_list_id' else default)
    monkeypatch.setattr(poller.db, 'log', lambda *args: None)
    monkeypatch.setattr(poller, 'poll_cycle', lambda **kwargs: observed.append(current_character()))
    for owner in (a, b):
        with character_context(owner):
            poller._prio2_cycle_if_due()
            poller._prio2_cycle_if_due()
    assert observed == [a, b]


def test_fanvue_media_index_cache_is_character_owned(monkeypatch):
    from app import fanvue
    a,b=uuid.uuid4(),uuid.uuid4()
    monkeypatch.setattr(fanvue,'list_vault_folders',lambda:[{'name':'PPV '+str(current_character())}])
    monkeypatch.setattr(fanvue,'folder_media_uuids',lambda name:{str(current_character())})
    for owner in (a,b,a):
        with character_context(owner):
            assert fanvue.media_folder_index()=={'PPV '+str(owner):{str(owner)}}


def test_memory_retention_clock_is_character_owned(monkeypatch):
    from app import memory
    a,b=uuid.uuid4(),uuid.uuid4();observed=[]
    monkeypatch.setattr(memory.db,'get_setting',lambda key,default=None:True if key=='memory_consolidate_enabled' else default)
    monkeypatch.setattr(memory.db,'purge_old_messages',lambda *args:observed.append(current_character()) or 0)
    monkeypatch.setattr(memory.db,'memory_candidates',lambda *args:[])
    for owner in (a,b,a):
        with character_context(owner): memory.cycle()
    assert observed==[a,b]
