"""Compare all original columns and rows without exposing private values."""
from pathlib import Path
import sqlite3
import hashlib
import json
import sys


def signature(rows):
    return sorted(hashlib.sha256(repr(tuple(row)).encode()).hexdigest() for row in rows)


def verify(source, target):
    source, target = Path(source), Path(target)
    assert (source / '.env').read_bytes() == (target / '.env').read_bytes(), '.env differs'
    report = {}
    with sqlite3.connect(f'file:{source}/data/bot.db?mode=ro', uri=True) as original, sqlite3.connect(f'file:{target}/data/bot.db?mode=ro', uri=True) as migrated:
        assert migrated.execute('pragma integrity_check').fetchone()[0] == 'ok'
        tables = original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for (table,) in tables:
            name = '"' + table.replace('"', '""') + '"'
            columns = original.execute(f'pragma table_info({name})').fetchall()
            fields = ','.join('"' + col[1].replace('"','""') + '"' for col in columns)
            old = signature(original.execute(f'SELECT {fields} FROM {name}'))
            new = signature(migrated.execute(f'SELECT {fields} FROM {name}'))
            from collections import Counter
            assert not (Counter(old) - Counter(new)), f'Changed or missing rows: {table}'
            report[table] = len(old)
    return {'configuration_identical': True, 'integrity': 'ok', 'preserved_rows': report}

if __name__ == '__main__':
    print(json.dumps(verify(*sys.argv[1:3]), indent=2))
