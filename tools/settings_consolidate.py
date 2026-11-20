"""Offline, explicit AI promotion. Default is read-only; never starts the app.

Only newly promoted non-secret fields are copied. Existing keys/post task models
already have canonical storage. No credentials, account data or schema migration.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

FIELDS = frozenset({'openrouter_model','classifier_model','ppv_caption_model',
    'vision_model','memory_model','temperature','max_tokens','reasoning_mode',
    'generation_retries','generation_retry_delay'})
MARK = '__settings_reorg_intent__:'
CLEAR = '__technical_cleared__:'


def _path(root, relative):
    p = Path(relative)
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise ValueError('Explicit relative database paths required')
    full = root / p
    if any(x.is_symlink() for x in [full, *full.parents]) or not full.is_file():
        raise ValueError('Existing regular database files required; no symlinks')
    return full


def _read(path):
    # mode=ro, not immutable: do not ignore committed WAL frames.
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
        return {k:json.loads(v) for k,v in c.execute('SELECT key,value FROM settings')
                if k in FIELDS or any(k == prefix+f for f in FIELDS for prefix in (MARK,CLEAR))}


def consolidate(root, destination, sources, *, select=None, intentional=(), apply=False, stopped_ack=False):
    root=Path(root).absolute()
    dest=_path(root,destination)
    if not sources:
        raise ValueError('Explicit active-generation owner sources required')
    paths={owner:_path(root,relative) for owner,relative in sources.items()}
    def identities():
        files=[dest,*paths.values()]
        values=[(p.stat().st_dev,p.stat().st_ino) for p in files]
        if len(set(values))!=len(values):
            raise ValueError('Database aliases are forbidden')
        return values
    initial_identities=identities()
    select=select or {}
    if not set(select)<=FIELDS or not set(intentional)<=FIELDS:
        raise ValueError('Unknown promotion field')
    if any(owner not in paths for owner in select.values()):
        raise ValueError('Unknown selected source')
    canonical=_read(dest)
    originals={owner:_read(p) for owner,p in paths.items()}
    writes={}; retained=[]; conflicts=[]; missing=[]
    for field in sorted(FIELDS):
        # Presence alone cannot distinguish old inert default stubs. An
        # operator must attest intentional legacy globals; tool writes attest
        # themselves, and explicit clears are always intentional.
        if canonical.get(CLEAR+field) or canonical.get(MARK+field) or field in intentional:
            if field not in canonical:
                raise ValueError('Intentional canonical field is absent')
            retained.append(field); continue
        values={o:rows[field] for o,rows in originals.items()
                if field in rows and (rows[field] not in ('',None) or rows.get(CLEAR+field))}
        if field in select:
            owner=select[field]
            if field not in originals[owner]:
                raise ValueError('Selected field absent from source')
            writes[field]=originals[owner][field]
        elif len({json.dumps(v,sort_keys=True) for v in values.values()})>1:
            conflicts.append(field)
        elif values:
            writes[field]=next(iter(values.values()))
        else:
            missing.append(field)
    report={'applied':False,'conflicts':conflicts,'canonical_retained':retained,
            'promote':sorted(writes),'missing':missing,'source_ids':sorted(paths)}
    if not apply:
        return report
    if not stopped_ack:
        raise ValueError('Explicit stopped deployment acknowledgement required')
    if conflicts:
        raise ValueError('Unresolved field conflict; select a source per field')
    # Global-only destination transaction; no owner connection is writable.
    # Recheck all source snapshots and canonical rows under the destination lock.
    with closing(sqlite3.connect(dest)) as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            if identities()!=initial_identities or _read(dest)!=canonical or any(_read(paths[o])!=rows for o,rows in originals.items()):
                raise ValueError('Settings changed after preflight; stop all writers')
            for field,value in writes.items():
                for key,val in ((field,value),(MARK+field,True)):
                    c.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(val)))
                if value in ('',None):
                    c.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(CLEAR+field,'true'))
                else:
                    c.execute('DELETE FROM settings WHERE key=?',(CLEAR+field,))
            c.commit()
        except BaseException:
            c.rollback(); raise
    report['applied']=True
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--active-generation-root',required=True)
    parser.add_argument('--destination',required=True)
    parser.add_argument('--source',action='append',required=True,help='OWNER=relative/bot.db')
    parser.add_argument('--select',action='append',default=[],help='FIELD=OWNER')
    parser.add_argument('--intentional-canonical',action='append',default=[],choices=sorted(FIELDS))
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--acknowledge-stopped-deployment',action='store_true')
    a=parser.parse_args()
    def pairs(rows):
        result={}
        for row in rows:
            k,v=row.split('=',1)
            if k in result: raise ValueError('Duplicate selection')
            result[k]=v
        return result
    try:
        result=consolidate(a.active_generation_root,a.destination,pairs(a.source),select=pairs(a.select),intentional=a.intentional_canonical,apply=a.apply,stopped_ack=a.acknowledge_stopped_deployment)
        print(json.dumps(result,sort_keys=True))
        return 2 if result['conflicts'] else 0
    except (ValueError,sqlite3.Error,OSError):
        # Never echo database values or filesystem/provider exception details.
        print(json.dumps({'error':'Promotion refused; verify stopped acknowledgement, field selections and local database paths.'}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
