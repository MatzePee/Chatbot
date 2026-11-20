"""Character-owned immutable runtime snapshot; technical configuration stays global."""
import os
from contextvars import ContextVar
from contextlib import contextmanager, asynccontextmanager, closing
import json
import sqlite3
from pathlib import Path

_autochat_automatic = ContextVar('autochat_automatic', default=False)
_autopost_automatic = ContextVar('autopost_automatic', default=False)


@asynccontextmanager
async def autopost_automation():
    token = _autopost_automatic.set(True)
    try:
        yield
    finally:
        _autopost_automatic.reset(token)



@contextmanager
def autochat_automation():
    """Only automatic chat work obeys bot_running; explicit manual work remains usable."""
    token = _autochat_automatic.set(True)
    try:
        yield
    finally:
        _autochat_automatic.reset(token)


def assert_autochat_action_allowed():
    """Fresh committed SQLite reads, never the shared AutoChat connection/cache.

    AutoPost publication switches are deliberately NOT AutoChat switches.
    Validate even manual content ownership; only automation obeys bot_running.
    """
    from autoposter.adapters.base import ExternalActionBlocked
    from autoposter import db as database
    from app import db as chat_db
    try:
        if os.environ.get('MP_CHARACTERS_ENABLED') == '1':
            owner = _owner()
            url = database.engine.url
            if url.get_backend_name() != 'sqlite' or not url.database or url.database == ':memory:':
                raise ValueError('Unsupported synchronous owner store')
            with closing(sqlite3.connect(Path(url.database).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                active = conn.execute('SELECT id FROM character WHERE id=? AND is_archived=0',
                                      (owner.hex,)).fetchone()
            if active is None:
                raise ValueError('Invalid owner')
        if _autochat_automatic.get():
            # Resolve the owner file through the normal factory, but do not read
            # its potentially old transaction. mode=ro never creates a new file.
            path = chat_db.get_conn().execute('PRAGMA database_list').fetchone()[2]
            with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                row = conn.execute("SELECT value FROM settings WHERE key='bot_running'").fetchone()
            if row is None or json.loads(row[0]) is not True:
                raise ValueError('Paused automation')
    except Exception:
        # No paths, settings, tokens, SQL or driver exception chains in UI/logs.
        raise ExternalActionBlocked('AutoChat pausiert oder Arbeitsbereich ungültig; externe Aktion blockiert') from None
from sqlalchemy import select
from autoposter.config import settings as technical_settings
from autoposter.models import AppSetting
from autoposter.workspaces import current_character, CharacterBoundaryError

_snapshot = ContextVar('character_runtime', default=None)
_FIELDS = frozenset({'dry_run','global_pause'})


def _owner():
    owner = current_character()
    if owner is None:
        raise CharacterBoundaryError('Character context required for runtime settings')
    return owner


async def load(session):
    owner = _owner()
    row = (await session.execute(select(AppSetting).where(AppSetting.key == 'runtime'))).scalar_one_or_none()
    stored = row.value if row and isinstance(row.value, dict) else {}
    value = {key:stored[key] if type(stored.get(key)) is bool else True for key in _FIELDS}
    _snapshot.set((owner, value))
    return dict(value)


async def assert_external_write_allowed(*, content_generation=False):
    """Gate the next write on committed state, never a request snapshot.

    Explicit content tools remain usable while publication is paused. Automatic
    planning generation obeys global_pause, but NOT dry_run (processing allowed).
    """
    if os.environ.get('MP_CHARACTERS_ENABLED') != '1':
        return
    from autoposter import db as database
    from autoposter.models import Character
    from autoposter.adapters.base import ExternalActionBlocked
    try:
        owner = _owner()
        async with database.engine.connect() as connection:
            active = (await connection.execute(select(Character.id).where(
                Character.id == owner, Character.is_archived.is_(False)))).scalar_one_or_none()
            stored = (await connection.execute(select(AppSetting.__table__.c.value).where(
                AppSetting.__table__.c.key == 'runtime'))).scalar_one_or_none()
    except Exception:
        raise ExternalActionBlocked('Arbeitsbereich oder Sicherheitsstatus ungültig; externe Aktion blockiert') from None
    if active is None:
        raise ExternalActionBlocked('Arbeitsbereich ungültig; externe Aktion blockiert')
    if content_generation and not _autopost_automatic.get():
        return
    if not isinstance(stored, dict) or stored.get('global_pause') is not False:
        raise ExternalActionBlocked('Notfall-Stopp aktiv; externe Aktion blockiert')
    if not content_generation and stored.get('dry_run') is not False:
        raise ExternalActionBlocked('Trockenlauf aktiv; externe Aktion blockiert')


async def guard_generation_request(request):
    if request.method == 'POST':
        await assert_external_write_allowed(content_generation=True)


class RuntimeView:
    def __getattr__(self, key):
        if key not in _FIELDS or os.environ.get('MP_CHARACTERS_ENABLED') != '1':
            return getattr(technical_settings, key)
        owner = _owner()
        saved = _snapshot.get()
        return saved[1][key] if saved and saved[0] == owner else True

    def __setattr__(self, key, value):
        if key not in _FIELDS or os.environ.get('MP_CHARACTERS_ENABLED') != '1':
            return setattr(technical_settings, key, value)
        owner = _owner()
        saved = _snapshot.get()
        values = dict(saved[1]) if saved and saved[0] == owner else {field:True for field in _FIELDS}
        values[key] = bool(value)
        _snapshot.set((owner, values))


runtime = RuntimeView()
