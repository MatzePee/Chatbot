"""Regression for independent review L2: actual jobs, not service substitutes."""
import uuid
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from autoposter import models, jobs, db as database
from autoposter.db import Base
from autoposter.workspaces import WorkspaceAsyncSession, character_context
from autoposter.character_dispatch import invoke, job_status
from autoposter.services import appconfig

@pytest.mark.asyncio
@pytest.mark.parametrize('job', ['age_fanvue_visibility', 'delete_old_fanvue_posts'])
async def test_actual_maintenance_dispatch_records_result_for_six_owners(tmp_path, monkeypatch, job):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'jobs.db'))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=WorkspaceAsyncSession, expire_on_commit=False)
    owners = [uuid.uuid4() for _ in range(6)]
    async with async_sessionmaker(engine)() as session:
        session.add_all([models.Character(id=owner, name=f'Owner {index}')
                         for index, owner in enumerate(owners)])
        await session.commit()
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    monkeypatch.setenv('MP_AUTOCHAT_DATA_DIR', str(tmp_path / 'chat'))
    from app import db as chat
    monkeypatch.setattr(chat, 'DATA_DIR', str(tmp_path / 'chat'))
    monkeypatch.setattr(database, 'engine', engine)
    monkeypatch.setattr(database, 'SessionLocal', factory)
    from autoposter.config import settings
    monkeypatch.setattr(settings, 'post_retention_active', True)
    monkeypatch.setattr(settings, 'post_retention_automatic_active', True)
    key = 'visibility_aging_last_run' if job == 'age_fanvue_visibility' else 'fanvue_retention_last_run'
    try:
        for index, owner in enumerate(owners):
            with character_context(owner):
                async with database.session_scope() as session:
                    session.add(models.AppSetting(key='runtime', value={'global_pause': False, 'dry_run': False}))
                    await appconfig.set_many(session, {'visibility_aging_enabled': True,
                                                      'post_retention_automatic': True})
                # Another owner's result must not have leaked into this workspace.
                async with factory() as session:
                    assert await session.get(models.AppSetting, key) is None
            result = await invoke(owner, job, getattr(jobs, job))
            assert result['status'] == 'ok'
            with character_context(owner):
                assert (await job_status(job))['status'] == 'complete'
                async with factory() as session:
                    row = await session.get(models.AppSetting, key)
                    assert row.value['status'] == 'ok'
                    assert row.value['recorded_at']
        for owner in owners:
            with character_context(owner):
                async with factory() as session:
                    assert (await session.get(models.AppSetting, key)).value['status'] == 'ok'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_job_result_writer_rejects_context_and_keys_and_rolls_back(tmp_path, monkeypatch):
    from autoposter.services.owned_settings import record_job_result
    from autoposter.workspaces import CharacterBoundaryError
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'results.db'))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=WorkspaceAsyncSession, expire_on_commit=False)
    owner, other = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setenv('MP_CHARACTERS_ENABLED', '1')
    try:
        with character_context(owner):
            async with factory() as session:
                with pytest.raises(CharacterBoundaryError):
                    await record_job_result(session, 'arbitrary_key', {'bad': True})
                with pytest.raises(CharacterBoundaryError):
                    await record_job_result(session, 'auto_plan_daily_gate', {'bad': True})
                with character_context(other):
                    with pytest.raises(CharacterBoundaryError):
                        await record_job_result(session, 'visibility_aging_last_run', {'bad': True})
                await record_job_result(session, 'visibility_aging_last_run', {'status': 'pending'})
                await session.rollback()
            async with factory() as session:
                assert await session.get(models.AppSetting, 'visibility_aging_last_run') is None
                await record_job_result(session, 'visibility_aging_last_run', {'status': 'first'})
                await session.commit()
            async with factory() as session:
                await record_job_result(session, 'visibility_aging_last_run', {'status': 'updated'})
                await session.commit()
            async with factory() as session:
                assert (await session.get(models.AppSetting, 'visibility_aging_last_run')).value == {'status': 'updated'}
        with character_context(other):
            async with factory() as session:
                assert await session.get(models.AppSetting, 'visibility_aging_last_run') is None
    finally:
        await engine.dispose()
