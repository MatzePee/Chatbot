"""Explicit scheduler/task ownership and durable per-owner execution leases.

Core SQL is confined to this control-plane seam: the UUID is validated before
opening a connection, owner existence is checked, and state keys are namespaced
by the immutable context. Provider/LLM work runs outside the DB transaction.
"""
from __future__ import annotations
import time
from uuid import UUID, uuid4
from sqlalchemy import select, update, insert
from autoposter import db as database
from autoposter.models import Character, AppSetting
from autoposter.workspaces import character_context, current_character, CharacterBoundaryError

LEASE_SECONDS = 1900  # exceeds Celery's hard task limit, survives process death


async def active_ids():
    async with database.SessionLocal(info={'control_plane': True}) as session:
        return list((await session.scalars(select(Character.id).where(Character.is_archived.is_(False)))).all())


async def _state(owner, name, *, claim=False, token=None, result=None, error=None):
    key = 'worker_job:' + name
    table = AppSetting.__table__
    now = time.time()
    async with database.engine.begin() as connection:
        # Serialize SQLite writers and PostgreSQL owner-row users before reading.
        found = await connection.execute(update(Character.__table__).where(
            Character.id == owner, Character.is_archived.is_(False)).values(updated_at=Character.updated_at))
        if not found.rowcount:
            raise CharacterBoundaryError('Character workspace not found')
        row = (await connection.execute(select(table.c.value).where(table.c.key == key))).scalar_one_or_none()
        value = dict(row or {})
        if claim:
            if value.get('expires_at', 0) > now:
                return None
            value = {'token':str(uuid4()), 'character_id':str(owner), 'started_at':now,
                     'expires_at':now + LEASE_SECONDS, 'status':'running'}
        elif token is not None:
            if value.get('token') != token:
                return value  # an expired worker never erases its replacement
            value.update(expires_at=0, finished_at=now, result=result,
                         error=error, status='failed' if error else 'complete')
        else:
            return value
        if row is None:
            await connection.execute(insert(table).values(key=key, value=value))
        else:
            await connection.execute(update(table).where(table.c.key == key).values(value=value))
        return value


async def invoke(character_id, name, function, *args, **kwargs):
    if character_id is None:
        raise CharacterBoundaryError('Explicit character ID required in task payload')
    owner = UUID(str(character_id))
    with character_context(owner):
        claim = await _state(owner, name, claim=True)
        if claim is None:
            return {'status':'busy'}
        result = None
        error = None
        try:
            from autoposter.services.runtime import load as load_runtime
            from autoposter.services.appconfig import refresh_snapshot
            async with database.SessionLocal() as session:
                await load_runtime(session)
                await refresh_snapshot(session)
            result = await function(*args, **kwargs)
            return result
        except BaseException as exc:
            error = type(exc).__name__  # status must never contain provider secrets
            raise
        finally:
            await _state(owner, name, token=claim['token'], result=result, error=error)


async def job_status(name):
    owner = current_character()
    if owner is None:
        raise CharacterBoundaryError('Character context required for job status')
    return await _state(owner, name)
