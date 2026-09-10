"""Retain the active/previous environments and three update snapshots.

Runs in the service account after the installed (also older) root supervisor
reports success and releases its lock. Never participates in switching/rollback.
"""
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time

logger = logging.getLogger(__name__)
UPDATE_LOCK = Path('/run/lock/mp-creatorstudio-update.lock')
RELEASE = re.compile(r'^v\d+\.\d+\.\d+-(\d+)$')
BACKUP = re.compile(r'^before-update-(\d+)$')


def _read(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Update-Metadaten sind ungültig.')
    return value


def _directories(root, pattern):
    return sorted(((int(match[1]), child) for child in root.iterdir()
                   if (match := pattern.fullmatch(child.name))
                   and not child.is_symlink() and child.is_dir()), reverse=True)


def _environment(path, releases):
    """Only environments directly within updater-generated, real directories."""
    if not path.is_absolute() or path.name not in ('venv', 'previous-venv'):
        raise ValueError('Unbekannte Update-Umgebung.')
    if path.parent.parent != releases or not RELEASE.fullmatch(path.parent.name):
        raise ValueError('Update-Umgebung liegt außerhalb der Versionsordner.')
    if path.resolve() != path or not (path / 'pyvenv.cfg').is_file():
        raise ValueError('Update-Umgebung fehlt oder ist verknüpft.')
    return path


def cleanup(repo, revision, environment, lock_path=UPDATE_LOCK):
    """Return a small report; uncertain/in-progress installations are untouched."""
    if os.environ.get('MP_PREVIEW') == '1':
        return {'skipped': 'preview'}
    repo = Path(repo).resolve()
    try:
        lock = Path(lock_path).open('rb')
    except OSError:
        return {'skipped': 'update-lock-unavailable'}
    with lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'skipped': 'update-running'}
        return _cleanup_locked(repo, revision, Path(environment).resolve())


def _cleanup_locked(repo, revision, environment):
    data = repo / 'data'
    releases, backups = data / 'releases', data / 'backups'
    try:
        if (data / 'deployment-pending').exists():
            return {'skipped': 'deployment-pending'}
        # Do not traverse redirected data/backup roots or unknown storage layouts.
        for root in (data, releases, backups):
            if root.resolve() != root or not root.is_dir():
                return {'skipped': 'unknown-storage-layout'}
        status = _read(data / 'update-status.json')
        plan = _read(data / 'update-plan.json')
        if status.get('phase') != 'complete' or not plan.get('activated'):
            return {'skipped': 'update-not-complete'}
        if not revision or plan.get('revision') != revision or status.get('tag') != plan.get('tag'):
            return {'skipped': 'revision-mismatch'}
        active = _environment(Path(plan['environment']), releases)
        previous = _environment(Path(plan['old_environment']), releases)
        if (repo / '.venv').resolve() != active or environment != active:
            return {'skipped': 'environment-mismatch'}
        if Path(plan['release']) != active.parent:
            return {'skipped': 'release-mismatch'}
        current_backup = repo / plan['backup']
        if (current_backup.parent != backups or not BACKUP.fullmatch(current_backup.name)
                or current_backup.resolve() != current_backup or not current_backup.is_dir()):
            return {'skipped': 'backup-mismatch'}
        signature = [revision, str(active), str(previous), str(current_backup)]
        marker = data / 'update-cleanup.json'
        if marker.exists():
            recorded = _read(marker)
            if recorded.get('signature') == signature and not recorded.get('errors'):
                return {'skipped': 'already-cleaned'}
        if not shutil.rmtree.avoids_symlink_attacks:
            return {'skipped': 'safe-removal-unavailable'}
        release_dirs = _directories(releases, RELEASE)
        backup_dirs = _directories(backups, BACKUP)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'skipped': 'invalid-update-metadata', 'detail': str(exc)}

    kept_releases = {active.parent, previous.parent}
    cutoff = int(RELEASE.fullmatch(active.parent.name)[1])
    candidates = [path for stamp, path in release_dirs if stamp < cutoff and path not in kept_releases]
    # The first migration moves the original .venv into previous-venv. On the
    # following update it is obsolete even while its containing release is kept.
    for root in kept_releases:
        legacy = root / 'previous-venv'
        if legacy not in (active, previous) and legacy.is_dir() and not legacy.is_symlink():
            candidates.append(legacy)
    kept_backups = {path for _, path in backup_dirs[:3]} | {current_backup}
    candidates += [path for _, path in backup_dirs if path not in kept_backups]
    removed, errors = [], []
    for path in candidates:
        try:
            if path.is_symlink():
                raise ValueError('Verzeichnis wurde durch eine Verknüpfung ersetzt.')
            shutil.rmtree(path)
            removed.append(str(path.relative_to(repo)))
        except (OSError, ValueError) as exc:
            errors.append(f'{path.name}: {exc}')
    result = {'signature': signature, 'updated_at': time.time(), 'removed': removed, 'errors': errors}
    fd, temporary = tempfile.mkstemp(dir=data, prefix='.cleanup-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(result, stream, ensure_ascii=False)
        os.replace(temporary, marker)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if removed:
        logger.info('Update-Bereinigung: %s alte Versions-/Sicherungsordner entfernt.', len(removed))
    if errors:
        logger.warning('Update-Bereinigung teilweise fehlgeschlagen: %s', '; '.join(errors))
    return result


def start(repo, revision):
    """Background check also handles success reported just after app startup."""
    stop = threading.Event()
    def run():
        while not stop.wait(60):
            try:
                cleanup(repo, revision, sys.prefix)
            except Exception:
                # Storage maintenance must never stop the bot or trigger rollback.
                logger.exception('Update-Bereinigung konnte nicht abgeschlossen werden.')
    worker = threading.Thread(target=run, name='update-cleanup', daemon=True)
    worker.start()
    return stop
