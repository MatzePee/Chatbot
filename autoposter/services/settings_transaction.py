"""Enlist canonical technical settings in the caller's SQLite transaction.

No second commit, compensation, or after-commit callback. Cross-file SQLite
atomic commit requires durable rollback journals (not WAL or :memory:).
Unsupported layouts fail closed without changing journal modes or settings.
"""
from pathlib import Path
import json

from app.setting_scope import encode_technical_value, decode_technical_value


class SettingsTransactionUnsupported(ValueError):
    """The installation cannot safely commit these stores together."""


_ERROR = ('Atomic technical settings require file-backed SQLite: either the same '
          'database file, or both files using DELETE/TRUNCATE/PERSIST journals '
          'with synchronous=FULL. No settings were written by this operation.')
_INFO = '_canonical_technical_transaction'


def _validate_session(db):
    from autoposter.workspaces import current_character, CharacterBoundaryError
    if db.info.get('character_id') != current_character():
        raise CharacterBoundaryError('Settings session does not match character context')
    # A legacy SQLite SAVEPOINT can be the physical outer transaction; RELEASE
    # would then commit despite a later SQLAlchemy outer rollback. Fail closed.
    if db.in_nested_transaction():
        raise SettingsTransactionUnsupported('Settings writes require an outer transaction, not a savepoint')


async def enlist(db):
    """Validate before autoflush, lock before read/modify/write, never commit."""
    from app import db as chat
    _validate_session(db)
    if db.get_bind().dialect.name != 'sqlite':
        raise SettingsTransactionUnsupported(_ERROR)
    connection = await db.connection()
    databases = (await connection.exec_driver_sql('PRAGMA database_list')).all()
    main = next(row[2] for row in databases if row[1] == 'main')
    target = Path(chat.DB_PATH).resolve()
    if not main or not target.is_file():
        raise SettingsTransactionUnsupported(_ERROR)
    same_file = Path(main).resolve() == target
    schema = 'main' if same_file else 'technical_config'
    if not same_file:
        existing = next((row[2] for row in databases if row[1] == schema), None)
        if existing is not None and Path(existing).resolve() != target:
            raise SettingsTransactionUnsupported(_ERROR)
        # Validate main first; ATTACH itself does not modify either database.
        mode = (await connection.exec_driver_sql('PRAGMA main.journal_mode')).scalar()
        sync = (await connection.exec_driver_sql('PRAGMA main.synchronous')).scalar()
        if mode not in ('delete', 'truncate', 'persist') or sync < 2:
            raise SettingsTransactionUnsupported(_ERROR)
        if existing is None:
            await connection.exec_driver_sql('ATTACH DATABASE ? AS technical_config', (str(target),))
    mode = (await connection.exec_driver_sql(f'PRAGMA {schema}.journal_mode')).scalar()
    sync = (await connection.exec_driver_sql(f'PRAGMA {schema}.synchronous')).scalar()
    if (same_file and (mode in ('off', 'memory') or sync < 1)) or (
        not same_file and (mode not in ('delete', 'truncate', 'persist') or sync < 2)
    ):
        raise SettingsTransactionUnsupported(_ERROR)
    # Verify the existing schema without CREATE/migration side effects.
    await connection.exec_driver_sql(f'SELECT key, value FROM {schema}.settings LIMIT 0')
    # pysqlite legacy mode does not BEGIN on SELECT. Explicitly begin before DML
    # and before SAVEPOINTs; BEGIN IMMEDIATE serializes competing JSON updates.
    active = await connection.run_sync(lambda conn: conn.connection.driver_connection.in_transaction)
    if not active:
        await connection.exec_driver_sql('BEGIN IMMEDIATE')
    else:
        # A caller may already have flushed. Obtain locks in a consistent order.
        await connection.exec_driver_sql('UPDATE main.app_setting SET value=value WHERE 0')
        if not same_file:
            await connection.exec_driver_sql('UPDATE technical_config.settings SET value=value WHERE 0')
    db.info[_INFO] = (db.sync_session.get_transaction(), schema)
    return connection, schema


async def lock_character_settings(db):
    """Serialize character-only competitors too, before their JSON read."""
    _validate_session(db)
    if db.get_bind().dialect.name != 'sqlite':
        return
    connection = await db.connection()
    active = await connection.run_sync(lambda conn: conn.connection.driver_connection.in_transaction)
    if not active:
        await connection.exec_driver_sql('BEGIN IMMEDIATE')
    else:
        await connection.exec_driver_sql('UPDATE main.app_setting SET value=value WHERE 0')


def _schema(db):
    enlisted = db.info.get(_INFO)
    if enlisted and enlisted[0] is db.sync_session.get_transaction():
        return enlisted[1]
    return None


async def overlay(db, values):
    """Read own pending writes without publishing them in process caches."""
    from app.setting_scope import is_global_technical
    schema = _schema(db)
    if schema is None:
        from app import db as chat
        result = dict(values)
        for key in values:
            if is_global_technical(key):
                present, value = chat.technical_setting(key)
                if present:
                    result[key] = value
        return result
    connection = await db.connection()
    keys = [key for key in values if is_global_technical(key)]
    keys += ['__technical_cleared__:' + key for key in keys]
    result = dict(values)
    if not keys:
        return result
    placeholders = ','.join('?' for _ in keys)
    rows = dict((await connection.exec_driver_sql(
        f'SELECT key, value FROM {schema}.settings WHERE key IN ({placeholders})', tuple(keys)
    )).all())
    for key in values:
        if is_global_technical(key) and key in rows:
            value = decode_technical_value(key, json.loads(rows[key]))
            marker = rows.get('__technical_cleared__:' + key)
            if value not in (None, '') or (marker and json.loads(marker)):
                result[key] = value
    return result


async def write(db, values):
    # Encode the complete batch before any SQL write (including clear markers).
    rows = [(key, json.dumps(encode_technical_value(key, value))) for key, value in values.items()]
    schema = _schema(db)
    if schema is None:
        raise SettingsTransactionUnsupported('Technical settings transaction was not enlisted')
    connection = await db.connection()
    sql = (f'INSERT INTO {schema}.settings(key,value) VALUES(?,?) '
           'ON CONFLICT(key) DO UPDATE SET value=excluded.value')
    if rows:
        await connection.exec_driver_sql(sql, rows)
    for key, value in values.items():
        marker = '__technical_cleared__:' + key
        if value in (None, ''):
            await connection.exec_driver_sql(sql, (marker, 'true'))
        else:
            await connection.exec_driver_sql(f'DELETE FROM {schema}.settings WHERE key=?', (marker,))
