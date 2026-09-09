"""Every test gets a disposable AutoChat database, including its cached connection."""
import pytest
from app import db


@pytest.fixture(autouse=True)
def isolated_chat_database(tmp_path, monkeypatch):
    # Changing DB_PATH alone does not replace db's process-wide cached connection.
    monkeypatch.setattr(db, '_conn', None)
    monkeypatch.setattr(db, 'DATA_DIR', str(tmp_path / 'autochat'))
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'autochat/bot.db'))
    monkeypatch.setattr(db, '_seed_from_env', lambda: None)
    db.init_db()
    try:
        yield
    finally:
        if db._conn is not None:
            db._conn.close()

