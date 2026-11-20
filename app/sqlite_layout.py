"""Explicit copy-layout marker; never change a marked database's journal mode.

Only the offline converter writes this marker. Unmarked installations retain
legacy connection defaults, so installing source alone is not a conversion.
"""
import os
import fcntl
from pathlib import Path
import stat
import time
import json

MARKER = '_settings_atomic_layout'
LAYOUT = 'rollback-full-v1'


def _stopped_proof(root):
    """Root supervisor attestation inherited by its direct, unprivileged child.

    This is NOT an acknowledgement flag. Only an unlinked, root-owned, read-only
    file passed from a live root parent can authorize inaccessible OTHER UIDs.
    Visible descriptors remain inspected by the unprivileged child.
    """
    try:
        fd = int(os.environ['MP_STOPPED_SOURCE_FD'])
        info = os.fstat(fd)
        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
            return None
        if not (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 0
                and stat.S_IMODE(info.st_mode) == 0o400):
            return None
        raw = os.pread(fd, 1048577, 0)
        if len(raw) > 1048576:
            return None
        value = json.loads(raw)
        parent = os.getppid()
        if value['parent'] != parent or Path('/proc/' + str(parent)).stat().st_uid != 0:
            return None
        if value['uid'] != os.geteuid() or not 0 <= time.monotonic() - value['created'] <= 1200:
            return None
        allowed = Path(value['source'])
        actual = Path(root).resolve(strict=True)
        if allowed.is_absolute() and (actual == allowed or allowed in actual.parents):
            return value
        return None
    except (KeyError, ValueError, TypeError, OSError):
        return None


def stopped_authority(root):
    return _stopped_proof(root) is not None


def stopped_process_verified(root, process):
    """Match an inaccessible process to the root scan by PID and start time."""
    proof = _stopped_proof(root)
    if proof is None:
        return False
    inspected = proof.get('verified_processes')
    if not isinstance(inspected, dict):
        return False
    expected = inspected.get(process.name)
    if not isinstance(expected, str) or not expected.isdigit():
        return False
    try:
        started = (process / 'stat').read_bytes().rsplit(b')', 1)[1].split()[19].decode('ascii')
    except (OSError, ValueError, IndexError, UnicodeError):
        return False
    return started == expected


def configure(cursor, *, legacy_sync=None):
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (MARKER,))
    exists = cursor.fetchone()
    if exists:
        cursor.execute('SELECT version, layout FROM ' + MARKER)
        rows = cursor.fetchall()
        if [tuple(row) for row in rows] != [(1, LAYOUT)]:
            raise ValueError('Unsupported SQLite layout marker; offline review required')
        cursor.execute('PRAGMA journal_mode')
        mode = cursor.fetchone()[0]
        if mode not in ('delete', 'truncate', 'persist'):
            raise ValueError('Atomic copy layout journal changed; refusing connection')
        cursor.execute('PRAGMA synchronous=FULL')
    else:
        cursor.execute('PRAGMA journal_mode=WAL')
        if legacy_sync is not None:
            cursor.execute('PRAGMA synchronous=' + legacy_sync)
