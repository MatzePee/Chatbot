"""Consistent SQLite snapshots before any additive schema initialization."""
from pathlib import Path
import sqlite3
import shutil
import datetime


def backup(root):
    root = Path(root)
    destination = root / 'data/backups' / ('creatorpilot-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    destination.mkdir(parents=True, mode=0o700)
    for relative in ('data/bot.db', 'data/autoposter/autoposter.db'):
        source = root / relative
        if not source.exists():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Sicherung ist beschädigt: ' + relative)
    for relative in ('.env', 'data/autoposter.env'):
        source = root / relative
        if source.exists():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            target.chmod(0o600)
    return destination

if __name__ == '__main__':
    print(backup(Path(__file__).resolve().parents[1]))
