"""Confirmed, stopped-process COPY upgrade. No provider or service operations.

The GUI only stages intent. bootstrap runs before application/database imports.
Original stores/configuration remain untouched; an atomic pointer selects copies.
"""
from __future__ import annotations
import contextlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import UUID, uuid4

_LOCK = None
ROOT = Path(__file__).resolve().parents[1]


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.character-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def status(root):
    root = Path(root)
    if (root / 'data/character-active.json').exists():
        return {'phase': 'active', **json.loads((root / 'data/character-active.json').read_text())}
    path = root / 'data/character-upgrade.json'
    return json.loads(path.read_text()) if path.exists() else {'phase': 'legacy'}


def stage(root, *, name, confirmed=False, technical_sources=None):
    from app.setting_scope import GLOBAL_TECHNICAL_KEYS
    technical_sources = dict(technical_sources or {})
    if any(key not in GLOBAL_TECHNICAL_KEYS or source not in {'autochat', 'autopost'}
           for key, source in technical_sources.items()):
        raise ValueError('Invalid technical source selection')
    if (Path(root) / 'data/deployment-pending').exists():
        raise ValueError('Wait until the current software update has completed')
    if confirmed is not True:
        raise ValueError('Explicit migration confirmation required')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ValueError('Existing character name required (1–120 characters)')
    current = status(root)
    if current['phase'] == 'active':
        raise ValueError('Character installation is already active')
    if current['phase'] == 'pending_restart':
        if current['name'] != name.strip() or current.get('technical_sources', {}) != technical_sources:
            raise ValueError('Cancel the pending assignment before changing its name or technical source choices')
        return current
    value = {'phase': 'pending_restart', 'name': name.strip(), 'id': str(uuid4())}
    if technical_sources:
        value['technical_sources'] = technical_sources
    _save(Path(root) / 'data/character-upgrade.json', value)
    return value


def cancel(root):
    if status(root)['phase'] == 'active':
        raise ValueError('Active migration cannot be cancelled or automatically rolled back')
    _save(Path(root) / 'data/character-upgrade.json', {'phase': 'legacy'})


def _active_paths(root, pointer):
    identity = str(UUID(pointer['id']))
    if identity != pointer['id']:
        raise ValueError('Invalid activation receipt')
    active = root / 'data/character-releases' / identity / 'active'
    if active.is_symlink() or not active.is_dir():
        raise ValueError('Activation output missing')
    if active.resolve() != active.absolute():
        raise ValueError('Symlink activation path unsupported')
    for rel in ('bot.db', 'autoposter.db', 'migration-report.json', 'sqlite-layout-report.json'):
        if not (active / rel).is_file() or (active / rel).is_symlink():
            raise ValueError('Incomplete activation output')
    return active


def bootstrap(root=None):
    """Called only at process entry, before db/config imports. Never from a route."""
    global _LOCK
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    # No new runtime files or settings changes for an untouched legacy install.
    current = status(root)
    if current['phase'] == 'legacy':
        return
    if _LOCK is None:
        _LOCK = (root / '.character-upgrade.lock').open('a')
    if current['phase'] == 'pending_restart':
        # Never interleave data activation with code-update health/rollback.
        if (root / 'data/deployment-pending').exists():
            return
        update_status = root / 'data/update-status.json'
        if update_status.exists() and json.loads(update_status.read_text()).get('phase') in {'preparing', 'testing', 'switching', 'checking'}:
            return
        fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            subprocess.run([sys.executable, '-m', 'app.character_upgrade', str(root)],
                           cwd=Path(__file__).resolve().parents[1],
                           capture_output=True, timeout=1200)
        except (subprocess.TimeoutExpired, OSError):
            pass
        # A timeout can occur AFTER pointer publication. Never silently fall
        # back to stale legacy data once a generation has become authoritative.
        current = status(root)
        if current['phase'] != 'active':
            try:
                _save(root / 'data/character-upgrade.json', {**current, 'phase': 'failed',
                      'message': 'Migration refused; original installation retained. Check stopped writers, schema, paths and technical-setting conflicts.'})
            finally:
                fcntl.flock(_LOCK, fcntl.LOCK_UN)
            return
    if current['phase'] == 'active':
        fcntl.flock(_LOCK, fcntl.LOCK_SH | fcntl.LOCK_NB)
        active = _active_paths(root, current)
        os.environ.update(MP_CHARACTERS_ENABLED='1', MP_AUTOCHAT_DATA_DIR=str(active),
                          MP_AUTOPOSTER_DATABASE_URL='sqlite+aiosqlite:///' + str(active / 'autoposter.db'),
                          MP_AUTOPOSTER_DATA_DIR=str(active), MP_AUTOPOSTER_MEDIA_ROOT=str(active / 'media'),
                          MP_EXPORT_DIR=str(active.parent / 'exports'))


def inspect_configuration(root):
    """Read-only, nonroot startup resolution. No DB/worker/main imports.

    AutoPost uses pydantic-settings' own env > autoposter.env > defaults;
    AutoChat's systemd uvicorn entrypoint uses os.environ, not load_dotenv.
    In particular do NOT load .env with python-dotenv: its interpolation and
    null-value semantics are not systemd EnvironmentFile semantics.
    """
    import hashlib
    from autoposter.config import settings
    from sqlalchemy.engine import make_url
    root = Path(root).resolve()
    if settings.scheduler_mode == 'celery':
        raise ValueError('External worker installations require a coordinated upgrade')
    url = make_url(settings.database_url)
    if url.drivername != 'sqlite+aiosqlite' or not url.database:
        raise ValueError('Only local SQLite upgrade supported')
    post = Path(url.database).absolute()
    chat = Path(os.environ.get('MP_AUTOCHAT_DATA_DIR') or root / 'data').absolute() / 'bot.db'
    if chat != root / 'data/bot.db':
        raise ValueError('Custom AutoChat path requires coordinated migration; source will not be guessed')
    if Path(os.environ.get('MP_EXPORT_DIR') or root / 'exports').absolute() != root / 'exports':
        raise ValueError('Custom export path requires coordinated migration')
    media = Path(settings.media_root).absolute()
    for path in (post, chat, media, Path(settings.data_dir).absolute()):
        if path.resolve(strict=True) != path:
            raise ValueError('Symlink/ambiguous source path unsupported')
        if any(reserved == path or reserved in path.parents
               for reserved in (root / 'data/releases', root / 'data/backups')):
            raise ValueError('Runtime sources in reserved update/backup paths unsupported')
        if path.is_symlink() or (path != root and root not in path.parents):
            raise ValueError('Custom/external paths require coordinated migration')
    # Hash only in-memory; never emit config or key values. Recomputed in the
    # stopped child before copy/publication, so pydantic fallback changes refuse.
    payload = {'settings': settings.model_dump(mode='json'), 'chat': str(chat),
               'exports': str(root / 'exports'), 'environment': dict(os.environ)}
    payload['environment'].pop('MP_STOPPED_SOURCE_FD', None)
    payload['environment'].pop('MP_EXPECTED_CONFIGURATION_SHA256', None)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return post, chat, media, digest


def apply_stopped(root):
    """Private subprocess entry. Input path comes from the fixed process root."""
    import shutil
    import sqlite3
    from tools.sqlite_layout import _assert_stopped, convert_copy
    from tools.character_migrate import build_workspace_copy, LEGACY_ID
    root = Path(root).resolve()
    request = status(root)
    if request['phase'] != 'pending_restart':
        raise ValueError('No confirmed pending migration')
    post, chat, media, digest = inspect_configuration(root)
    expected = os.environ.get('MP_EXPECTED_CONFIGURATION_SHA256')
    if expected is not None and expected != digest:
        raise ValueError('Application configuration changed before stopped copy')
    _assert_stopped(root / 'data')
    # Check external custom local stores too; no live writer may be copied.
    if root / 'data' not in post.parents:
        _assert_stopped(post.parent)
    identity = str(UUID(request['id']))
    generation = root / 'data/character-releases' / identity
    generation.mkdir(parents=True, exist_ok=True, mode=0o700)
    copied = generation / 'migration'
    build_workspace_copy(post, chat, media, copied, name=request['name'],
                         technical_sources=request.get('technical_sources', {}))
    active = generation / 'active'
    convert_copy(copied, active, autopost_db='autoposter.db', autochat_db='bot.db', sources_stopped=True)
    # Verify the complete current model before publication, never repair startup.
    from autoposter.db import Base
    import autoposter.models  # noqa: F401
    with contextlib.closing(sqlite3.connect(active / 'autoposter.db')) as conn:
        from autoposter.paid_media_schema import upgrade_sqlite
        upgrade_sqlite(conn)
        for table in Base.metadata.sorted_tables:
            actual = {row[1] for row in conn.execute('PRAGMA table_info("' + table.name + '")')}
            if not set(table.columns.keys()).issubset(actual):
                raise ValueError('Incomplete old schema: ' + table.name)
    # Preserve original configuration/keys outside DBs; do not rewrite them.
    config = generation / 'configuration'
    config.mkdir(exist_ok=True, mode=0o700)
    for rel in ('.env', 'data/autoposter.env'):
        source = root / rel
        if source.is_symlink():
            raise ValueError('Symlink configuration unsupported')
        if source.exists():
            destination = config / source.name
            shutil.copy2(source, destination)
            destination.chmod(0o600)
    exports = root / 'exports'
    if exports.exists():
        from tools.sqlite_layout import _inventory, _publish_copy
        if exports.is_symlink():
            raise ValueError('Symlink exports unsupported')
        expected = _inventory(exports)
        destination = generation / 'exports'
        if destination.exists():
            if _inventory(destination / 'characters' / LEGACY_ID) != expected:
                raise ValueError('Interrupted export copy differs from original')
        else:
            with tempfile.TemporaryDirectory(prefix='.exports-', dir=generation) as temporary:
                stage_root = Path(temporary) / 'tree'
                shutil.copytree(exports, stage_root / 'characters' / LEGACY_ID)
                if _inventory(stage_root / 'characters' / LEGACY_ID) != expected or _inventory(exports) != expected:
                    raise ValueError('Export source changed')
                _publish_copy(stage_root, destination)
    # Original databases, schedules, rotating secrets and media are not replaced.
    _save(root / 'data/character-active.json', {'id': identity, 'name': request['name'], 'version': 1})


if __name__ == '__main__':
    if sys.argv[1:2] == ['--inspect']:
        try:
            print(inspect_configuration(Path(sys.argv[2]))[3])
        except Exception:
            # Validation errors from pydantic can include input secrets.
            print('Application configuration refused', file=sys.stderr)
            sys.exit(1)
    else:
        apply_stopped(Path(sys.argv[1]))
