"""Narrow AppSetting coordination writes; never an arbitrary SQL escape hatch.

The key type performs physical owner prefixing. A workspace session must still
match its captured owner before any connection is acquired or value is bound.
Legacy sessions retain the same atomic SQL without a prefix.
"""
from sqlalchemy import select, update
from autoposter.models import AppSetting
from autoposter.workspaces import current_character, CharacterBoundaryError

_RESULT_KEYS = {'fanvue_retention_last_run', 'visibility_aging_last_run'}
_EXACT = {'auto_plan_daily_gate', 'x_top_posts_collection'} | _RESULT_KEYS
_PREFIXES = ('instagram_followers:', 'x_followers:', 'telegram_members:', 'fanvue_visibility:',
             'fanvue_retention:', 'fanvue_ppv_restore:')

def _validate(db, key):
    import os
    owner = db.info.get('character_id')
    if owner is None and os.environ.get('MP_CHARACTERS_ENABLED') == '1':
        raise CharacterBoundaryError('Character context required for coordination writes')
    if owner != current_character():
        raise CharacterBoundaryError('Character context changed while session was open')
    if not isinstance(key, str) or len(key) > 150 or not (
        key in _EXACT or any(key.startswith(p) and len(key) > len(p) for p in _PREFIXES)):
        raise CharacterBoundaryError('Unsupported coordination setting')
    if owner is not None and key.startswith(str(owner) + ':'):
        raise CharacterBoundaryError('Use an unprefixed coordination key')

async def locked_setting(db, key):
    _validate(db, key)
    if db.bind.dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    elif db.bind.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise ValueError('Unsupported coordination dialect')
    connection = await db.connection()
    await connection.execute(insert(AppSetting).values(key=key, value={}).on_conflict_do_nothing(index_elements=['key']))
    if db.bind.dialect.name == 'sqlite':
        await connection.execute(update(AppSetting).where(AppSetting.key == key).values(key=key))
    return (await db.execute(select(AppSetting).where(AppSetting.key == key)
        .with_for_update().execution_options(populate_existing=True))).scalar_one()

async def record_job_result(db, key, value):
    """Persist only the two declared job result keys in the caller transaction."""
    _validate(db, key)
    if key not in _RESULT_KEYS or not isinstance(value, dict):
        raise CharacterBoundaryError('Unsupported job result setting')
    if db.bind.dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    elif db.bind.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise ValueError('Unsupported coordination dialect')
    connection = await db.connection()
    statement = insert(AppSetting).values(key=key, value=value)
    await connection.execute(statement.on_conflict_do_update(
        index_elements=['key'], set_={'value': value}))


async def compare_and_set(db, key, field, expected, value):
    _validate(db, key)
    if field not in {'token', 'attempted_at'} or not isinstance(expected, str) or not isinstance(value, dict):
        raise CharacterBoundaryError('Invalid coordination comparison')
    connection = await db.connection()
    return await connection.execute(update(AppSetting).where(AppSetting.key == key,
        AppSetting.value[field].as_string() == expected).values(value=value))
