import sqlite3
import pytest
from tools.character_release_smoke import snapshot_tables, assert_tables_preserved


def fixture_db():
    connection = sqlite3.connect(':memory:')
    connection.execute('CREATE TABLE media_asset (id TEXT PRIMARY KEY, filename TEXT, width INTEGER)')
    connection.execute("INSERT INTO media_asset VALUES ('asset', 'original.jpg', 600)")
    connection.execute('CREATE TABLE channel (id TEXT PRIMARY KEY, handle TEXT)')
    connection.execute("INSERT INTO channel VALUES ('channel', '@original')")
    connection.execute('CREATE TABLE post (id TEXT PRIMARY KEY, body TEXT)')
    connection.execute("INSERT INTO post VALUES ('post', 'original caption')")
    return connection


def test_additive_columns_and_tables_preserve_existing_cells():
    with fixture_db() as connection:
        before = snapshot_tables(connection)
        connection.execute('ALTER TABLE media_asset ADD COLUMN telegram_price_stars INTEGER')
        connection.execute('ALTER TABLE channel ADD COLUMN instagram_is_ai_generated BOOLEAN NOT NULL DEFAULT FALSE')
        connection.execute('ALTER TABLE post ADD COLUMN price_stars INTEGER')
        connection.execute('CREATE TABLE media_image_edit (id TEXT)')
        assert_tables_preserved(connection, before)


@pytest.mark.parametrize('mutation',[
    "UPDATE media_asset SET filename='changed.jpg'",
    'UPDATE media_asset SET width=700',
    "UPDATE channel SET handle='@changed'",
    "UPDATE post SET body='changed caption'",
    'DELETE FROM post',
    "INSERT INTO media_asset VALUES ('other','other.jpg',500)",
])
def test_existing_cell_changes_and_row_changes_are_still_rejected(mutation):
    with fixture_db() as connection:
        before = snapshot_tables(connection)
        connection.execute(mutation)
        with pytest.raises(AssertionError, match='Existing workspace data changed'):
            assert_tables_preserved(connection, before)


def test_character_upgrade_seeds_missing_default_without_changing_existing_settings(monkeypatch):
    from app import db
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    monkeypatch.setattr(db, 'get_conn', lambda: connection)
    monkeypatch.setattr(db, '_character_owner', lambda: 'test-character')
    db.init_db()
    connection.execute("DELETE FROM settings WHERE key='send_promise_guard_enabled'")
    connection.execute("UPDATE settings SET value='false' WHERE key='time_guard_enabled'")
    before = snapshot_tables(connection, character_chat=True)
    strict = snapshot_tables(connection)
    db.init_db()
    assert connection.execute("SELECT value FROM settings WHERE key='send_promise_guard_enabled'").fetchone()[0] == 'true'
    assert_tables_preserved(connection, before)
    with pytest.raises(AssertionError, match='settings'):
        assert_tables_preserved(connection, strict)


@pytest.mark.parametrize('mutation', [
    "INSERT INTO settings VALUES ('send_promise_guard_enabled', 'false')",
    "INSERT INTO settings VALUES ('unknown', 'true')",
    "UPDATE settings SET value='true' WHERE key='time_guard_enabled'",
    "DELETE FROM settings WHERE key='time_guard_enabled'",
])
def test_character_upgrade_rejects_unexpected_settings_changes(mutation):
    with fixture_db() as connection:
        connection.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
        connection.execute("INSERT INTO settings VALUES ('time_guard_enabled', 'false')")
        before = snapshot_tables(connection, character_chat=True)
        connection.execute(mutation)
        with pytest.raises(AssertionError, match='settings'):
            assert_tables_preserved(connection, before)
