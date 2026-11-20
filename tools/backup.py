"""Consistent per-SQLite snapshots before additive initialization.

This is an online startup snapshot, not a cross-database stopped-writer barrier.
The privileged updater's stopped-installation backup remains separate.
"""
from pathlib import Path
from contextlib import closing
import sqlite3
import shutil
import datetime
import json
from uuid import UUID


def repository_root(data_dir):
    data = Path(data_dir).resolve()
    if data.name == 'active' and data.parent.parent.name == 'character-releases':
        root = data.parents[3]
        pointer = json.loads((root/'data/character-active.json').read_text())
        if str(UUID(pointer['id'])) != data.parent.name or pointer.get('version') != 1:
            raise ValueError('Selected generation does not match backup root')
        return root
    return data.parent


def backup(root):
    root = Path(root).resolve()
    relatives = ['data/bot.db', 'data/autoposter/autoposter.db', '.env', 'data/autoposter.env']
    pointer = root/'data/character-active.json'
    required = []
    if pointer.exists():
        value = json.loads(pointer.read_text())
        identity = str(UUID(value['id']))
        if identity != value['id'] or value.get('version') != 1:
            raise ValueError('Invalid active generation')
        generation = Path('data/character-releases')/identity
        active = root/generation/'active'
        required = [generation/'active/bot.db', generation/'active/autoposter.db']
        required.extend(p.relative_to(root) for p in (active/'characters').glob('*/bot.db'))
        for p in required:
            if not (root/p).is_file():raise ValueError('Missing active database: '+str(p))
        relatives += ['data/character-active.json', 'data/character-upgrade.json',
                      str(generation/'active'), str(generation/'configuration')]
    files = {}
    for relative in relatives:
        source = root/relative
        if source.is_symlink() or source.resolve() != source:
            raise ValueError('Unsafe backup source: '+str(relative))
        if not source.exists():continue
        for path in ([source] if source.is_file() else source.rglob('*')):
            if path.is_symlink():raise ValueError('Symlinks in backup input are unsupported')
            if path.is_file() and not path.name.endswith(('-wal','-shm','-journal')):
                files[path.relative_to(root)] = path
    destination = root/'data/backups'/('creatorpilot-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    destination.mkdir(parents=True, mode=0o700)
    (destination/'.incomplete').touch(mode=0o600)
    for relative,source in sorted(files.items()):
        target = destination/relative
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if source.suffix == '.db':
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Sicherung ist beschädigt: '+str(relative))
        else:
            shutil.copy2(source,target)
        target.chmod(0o600)
    if not all((destination/p).is_file() for p in required):
        raise RuntimeError('Active database snapshot incomplete')
    (destination/'.incomplete').unlink()
    return destination

if __name__ == '__main__':
    print(backup(Path(__file__).resolve().parents[1]))
