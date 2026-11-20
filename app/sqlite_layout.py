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


def stopped_authority(root):
    """Root supervisor attestation inherited by its direct, unprivileged child.

    This is NOT an acknowledgement flag. Only an unlinked, root-owned, read-only
    file passed from a live root parent can authorize inaccessible OTHER UIDs.
    Same-UID and otherwise visible descriptors are always inspected.
    """
    try:
        fd = int(os.environ['MP_STOPPED_SOURCE_FD'])
        info = os.fstat(fd)
        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
            return False
        if not (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 0
                and stat.S_IMODE(info.st_mode) == 0o400):
            return False
        value = json.loads(os.pread(fd, 8192, 0))
        parent = os.getppid()
        if value['parent'] != parent or Path('/proc/' + str(parent)).stat().st_uid != 0:
            return False
        if value['uid'] != os.geteuid() or not 0 <= time.monotonic() - value['created'] <= 1200:
            return False
        allowed = Path(value['source'])
        actual = Path(root).resolve(strict=True)
        return allowed.is_absolute() and (actual == allowed or allowed in actual.parents)
    except (KeyError, ValueError, TypeError, OSError):
        return False


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
