import asyncio
from uuid import uuid4
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from autoposter import db as database, models
from autoposter.workspaces import current_character, character_context, CharacterBoundaryError


@pytest.mark.asyncio
async def test_job_dispatch_leases_and_status_are_owned_and_persistent(tmp_path, monkeypatch):
    from autoposter import character_dispatch as dispatch
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'jobs.db'))
    async with engine.begin() as connection:
        await connection.run_sync(database.Base.metadata.create_all)
        for owner in (a := uuid4(), b := uuid4()):
            await connection.execute(models.Character.__table__.insert().values(id=owner, name=str(owner)))
    monkeypatch.setattr(database, 'engine', engine)
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    entered = asyncio.Event(); release = asyncio.Event(); observed = []
    async def slow():
        observed.append(current_character()); entered.set(); await release.wait()
        return {'ok': True}
    first = asyncio.create_task(dispatch.invoke(a, 'synthetic', slow))
    await asyncio.wait_for(entered.wait(), 3)
    try:
        assert (await dispatch.invoke(a, 'synthetic', slow))['status'] == 'busy'
        assert await dispatch.invoke(b, 'synthetic', lambda: asyncio.sleep(0, result={'owner': str(current_character())})) == {'owner':str(b)}
        with character_context(b):
            assert (await dispatch.job_status('synthetic'))['result'] == {'owner':str(b)}
            from autoposter import scheduler
            from autoposter.api import dashboard
            scheduler_instance = scheduler.Scheduler([scheduler.Job('synthetic', slow, 60)])
            scheduler_instance.jobs[0].last_result = {'private':str(a)}
            monkeypatch.setattr(scheduler, 'scheduler', scheduler_instance)
            monkeypatch.setattr(scheduler.settings, 'scheduler_mode', 'inprocess')
            status = await dashboard.jobs_status(None)
            assert str(a) not in str(status)
            assert status['jobs'][0]['last_result'] == {'owner':str(b)}
        release.set(); assert await first == {'ok': True}
        with character_context(a):
            assert (await dispatch.job_status('synthetic'))['result'] == {'ok':True}
        assert observed == [a]
        with pytest.raises(CharacterBoundaryError):
            await dispatch.invoke(uuid4(), 'synthetic', slow)
        with pytest.raises((CharacterBoundaryError, ValueError, TypeError)):
            await dispatch.invoke(None, 'synthetic', slow)
    finally:
        release.set()
        await first
        await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_primes_sync_configuration_from_legacy_technical_store(tmp_path, monkeypatch):
    from autoposter import character_dispatch as dispatch
    from autoposter.services import appconfig
    from autoposter.crypto import encrypt
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'snapshot.db'))
    a, b = uuid4(), uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(database.Base.metadata.create_all)
        for owner in (a, b):
            await connection.execute(models.Character.__table__.insert().values(id=owner, name=str(owner)))
        await connection.execute(models.AppSetting.__table__.insert().values(key='integrations', value={
            'openrouter_api_key': encrypt('synthetic-legacy-shared'),
            'x_client_secret': encrypt('synthetic-legacy-private'),
        }))
    monkeypatch.setattr(database, 'engine', engine)
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    async def provider_mock():
        return (appconfig.current('openrouter_api_key'), appconfig.current('x_client_secret'))
    try:
        for owner in (a, b):
            assert await dispatch.invoke(owner, 'synthetic-config', provider_mock) == ('synthetic-legacy-shared', None)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_inprocess_tick_and_manual_run_carry_explicit_owners(monkeypatch):
    from autoposter import character_dispatch as dispatch, scheduler
    a, b = uuid4(), uuid4()
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    async def owners(): return [a, b]
    calls = []
    async def invoked(owner, name, function, *args, **kwargs):
        calls.append((owner, name)); return {'ok':True}
    monkeypatch.setattr(dispatch, 'active_ids', owners)
    monkeypatch.setattr(dispatch, 'invoke', invoked)
    job = scheduler.Job('synthetic', lambda: asyncio.sleep(0, result={}), 60)
    await scheduler.dispatch_tick(job)
    assert calls == [(a, 'synthetic'), (b, 'synthetic')]
    calls.clear()
    with character_context(b):
        assert await scheduler.Scheduler([job]).run_now('synthetic') == {'ok':True}
    assert calls == [(b, 'synthetic')]


def test_celery_unowned_payload_is_rejected_before_job(monkeypatch):
    from autoposter import tasks
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    observed = []
    monkeypatch.setattr(tasks, 'run_async', lambda coro: observed.append(coro))
    with pytest.raises(CharacterBoundaryError):
        tasks.generate_plan.run()
    assert observed == []
