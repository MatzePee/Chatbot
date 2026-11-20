"""OFFLINE, local COPY ONLY: convert a stopped data tree to rollback journals.

No app startup, encryption, providers or source SQLite connections. Source byte
copies include committed WAL frames. Never run on a live/shared source.
"""
from __future__ import annotations
from contextlib import closing
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from app.sqlite_layout import MARKER, LAYOUT

REPORT = 'sqlite-layout-report.json'


def _inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlinks are unsupported in offline sources')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('Only regular files and directories are supported')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        result[path.relative_to(root).as_posix()] = digest.hexdigest()
    return result


def _assert_stopped(root):
    # Fail closed on Linux if open-descriptor discovery is unavailable. This is
    # an additional local check, NOT a substitute for stopping every writer.
    proc = Path('/proc')
    if not (proc / 'self/fd').is_dir():
        raise ValueError('Offline source verification requires Linux /proc')
    from app.sqlite_layout import stopped_authority, stopped_process_verified
    authorized = stopped_authority(root)
    identities = {(p.stat().st_dev, p.stat().st_ino) for p in root.rglob('*') if p.is_file()}
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        try:
            descriptors = list((process / 'fd').iterdir())
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            if authorized and (process.stat().st_uid != os.geteuid()
                               or stopped_process_verified(root, process)):
                continue
            raise ValueError('Cannot verify stopped sources; process descriptors inaccessible') from exc
        for descriptor in descriptors:
            try:
                stat = descriptor.stat()
            except FileNotFoundError:
                continue
            except PermissionError as exc:
                if authorized and (process.stat().st_uid != os.geteuid()
                               or stopped_process_verified(root, process)):
                    continue
                raise ValueError('Cannot verify stopped sources; descriptor inaccessible') from exc
            if (stat.st_dev, stat.st_ino) in identities:
                raise ValueError('Source is open by a process; stop all readers/writers first')


def _database(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Database paths must be relative within source root')
    result = root / path
    if not result.is_file() or result.is_symlink():
        raise ValueError('Explicit existing database files required')
    return result


def _migration_metadata(report):
    metadata = {key: value for key, value in report.items() if key != 'output_hashes'}
    return hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()


def _convert_database(path):
    # Work ONLY on the private stage. Opening normally replays committed WAL
    # frames; immutable=1 on an original WAL DB would incorrectly omit them.
    shm = Path(str(path) + '-shm')
    if shm.exists():
        shm.unlink()
    with closing(sqlite3.connect(path)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Copied database integrity check failed')
        if conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
            raise ValueError('Copied database cannot use rollback journal')
        conn.execute('PRAGMA synchronous=FULL')
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (MARKER,)).fetchone()
        if exists:
            if conn.execute('SELECT version,layout FROM ' + MARKER).fetchall() != [(1, LAYOUT)]:
                raise ValueError('Unsupported pre-existing layout marker')
        else:
            conn.execute('CREATE TABLE ' + MARKER + '(version INTEGER NOT NULL, layout TEXT NOT NULL)')
            conn.execute('INSERT INTO ' + MARKER + ' VALUES(1,?)', (LAYOUT,))
        conn.commit()
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Copied database foreign-key check failed')
    os.chmod(path, 0o600)


def _publish_copy(stage, destination):
    """Linux atomic no-replace rename; never overwrite a racing destination."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError('Linux renameat2 required for no-replace publication')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(destination), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def convert_copy(source_root, destination, *, autopost_db, autochat_db, sources_stopped=False):
    if sources_stopped is not True:
        raise ValueError('Explicit sources_stopped acknowledgement required; stop ALL processes first')
    original = Path(source_root)
    if original.is_symlink() or not original.is_dir():
        raise ValueError('Explicit local source directory required')
    source = original.resolve(strict=True)
    dest = Path(destination).absolute()
    if dest.is_symlink():
        raise ValueError('Symlink destination unsupported')
    dest = dest.resolve()
    if dest == source or source in dest.parents or dest in source.parents:
        raise ValueError('Destination must be separate from source')
    post, chat = (_database(source, value) for value in (autopost_db, autochat_db))
    if post == chat:
        raise ValueError('This conversion requires two distinct SQLite files')
    before = _inventory(source)
    _assert_stopped(source)
    migration_receipt = source / 'migration-report.json'
    if migration_receipt.is_file():
        from tools.character_migrate import _output_hashes
        previous_migration = json.loads(migration_receipt.read_text())
        if previous_migration.get('output_hashes') != _output_hashes(source):
            raise ValueError('Source migration outputs changed; refusing conversion')
    # Unknown hot rollback journals are not safely recoverable by a byte-copy
    # operation; require operator SQLite recovery on a different backup first.
    if any(p.name.endswith('-journal') and p.stat().st_size for p in source.rglob('*') if p.is_file()):
        raise ValueError('Source rollback journal present; recovery/review required')
    options = {'autopost_db': str(Path(autopost_db)), 'autochat_db': str(Path(autochat_db))}
    if dest.exists():
        report = dest / REPORT
        if not report.is_file():
            raise ValueError('Destination exists without layout receipt; never overwrite')
        previous = json.loads(report.read_text())
        current = _inventory(dest)
        current.pop(REPORT, None)
        if previous.get('source_hashes') != before or previous.get('options') != options:
            raise ValueError('Source or options changed; refusing reuse')
        if previous.get('migration_metadata_sha256'):
            from tools.character_migrate import _output_hashes
            migration = json.loads((dest / 'migration-report.json').read_text())
            if migration.get('output_hashes') != _output_hashes(dest):
                raise ValueError('Destination migration outputs changed; refusing reuse')
            current.pop('migration-report.json', None)
            if _migration_metadata(migration) != previous['migration_metadata_sha256']:
                raise ValueError('Destination migration metadata changed; refusing reuse')
        if previous.get('output_hashes') != current:
            raise ValueError('Destination changed; refusing reuse')
        return previous
    dest.parent.mkdir(parents=True, exist_ok=True)
    container = Path(tempfile.mkdtemp(prefix='.sqlite-layout-', dir=dest.parent))
    stage = container / 'tree'
    try:
        # copytree copies source directory modes too. Keep an outer 0700
        # container until the copied root itself has been restricted.
        shutil.copytree(source, stage, dirs_exist_ok=True)
        os.chmod(stage, 0o700)
        _assert_stopped(source)
        if _inventory(source) != before or _inventory(stage) != before:
            raise ValueError('Source changed during copy; output discarded')
        # Convert all SQLite .db files, including existing character AutoChat
        # stores, without changing ownership/schema/data migrations.
        databases = {stage / p.relative_to(source) for p in (post, chat)}
        databases.update(stage.rglob('*.db'))
        for path in sorted(databases):
            _convert_database(path)
        migration = stage / 'migration-report.json'
        migration_report = None
        if migration.is_file():
            migration_report = json.loads(migration.read_text())
            migration_report['sqlite_layout'] = LAYOUT
        if _inventory(source) != before:
            raise ValueError('Source changed during conversion; output discarded')
        output_hashes = _inventory(stage)
        report = {'version': 1, 'layout': LAYOUT, 'options': options,
                  'source_hashes': before, 'output_hashes': output_hashes,
                  'activation_performed': False, 'sources_stopped_acknowledged': True}
        if migration_report is not None:
            # Avoid circular receipt hashes without excluding any existing
            # migration output: migration hashes the layout receipt; layout
            # validates migration metadata plus the migration's full hash map.
            output_hashes.pop('migration-report.json')
            report['migration_metadata_sha256'] = _migration_metadata(migration_report)
        (stage / REPORT).write_text(json.dumps(report, indent=2))
        os.chmod(stage / REPORT, 0o600)
        if migration_report is not None:
            from tools.character_migrate import _output_hashes
            migration_report['output_hashes'] = _output_hashes(stage)
            migration.write_text(json.dumps(migration_report, indent=2))
        # fsync every staged file and directory before exposing the completed
        # copy. A crash can leave a private staging tree, never replace input.
        for path in stage.rglob('*'):
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        fd = os.open(stage, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        _assert_stopped(source)
        if _inventory(source) != before:
            raise ValueError('Source changed before publication; output discarded')
        if dest.exists():
            raise ValueError('Destination appeared during conversion')
        _publish_copy(stage, dest)
        container.rmdir()
        fd = os.open(dest.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return report
    except BaseException:
        shutil.rmtree(container, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--autopost-db', required=True, help='Relative to source-root')
    parser.add_argument('--autochat-db', required=True, help='Canonical technical DB, relative to source-root')
    parser.add_argument('--sources-stopped', action='store_true', help='ALL processes stopped, local quiescent input only')
    args = parser.parse_args()
    try:
        report = convert_copy(args.source_root, args.destination, autopost_db=args.autopost_db,
                              autochat_db=args.autochat_db, sources_stopped=args.sources_stopped)
    except (ValueError, sqlite3.DatabaseError, OSError) as exc:
        parser.exit(2, 'Offline conversion refused: ' + str(exc) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
