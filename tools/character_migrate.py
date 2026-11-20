"""Explicit OFFLINE COPY migration. Never edits, replaces or activates inputs.

CLI defaults to read-only preflight. --apply-copy builds a NEW directory only.
No provider imports, workers, scheduler, lifespan or network operations.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager, closing
from uuid import UUID


@contextmanager
def _connection(*args, **kwargs):
    with closing(sqlite3.connect(*args, **kwargs)) as conn:
        with conn:
            yield conn

LEGACY_ID='00000000-0000-0000-0000-000000000001'
OWNED_TABLES=(
 'persona','persona_example','posting_policy','channel_credential','channel',
 'media_asset','media_set','media_set_item','media_assignment','media_channel_usage',
 'assignment_rule','saved_view','content_plan','post','x_image_comment',
 'post_revision','publish_attempt','external_media_ref','blackout_period',
 'audit_log','notification','llm_usage','oauth_state')


def _ro(path):
    path=Path(path).resolve(strict=True)
    return _connection(path.as_uri()+'?mode=ro',uri=True)


def _hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(post,chat,media):
    files={'autoposter':_hash(post),'autochat':_hash(chat)}
    for name,file in [('autoposter',post),('autochat',chat)]:
        wal=Path(str(file)+'-wal')
        if wal.exists():files[name+'_wal']=_hash(wal)
    for file in sorted(Path(media).rglob('*')):
        if file.is_symlink():raise ValueError('Symlinks in media input are unsupported')
        if file.is_file():files['media/'+file.relative_to(media).as_posix()]=_hash(file)
    return files


def _counts(conn):
    names=[row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    result={}
    for name in names:
        quoted='"'+name.replace('"','""')+'"'
        result[name]=conn.execute('SELECT count(*) FROM '+quoted).fetchone()[0]
    return result


def preflight(autopost_db,autochat_db,media_root):
    post,chat,media=map(Path,(autopost_db,autochat_db,media_root))
    if not post.is_file() or not chat.is_file() or not media.is_dir():
        raise ValueError('Explicit local source DB files and media directory required')
    with _ro(post) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('AutoPost integrity check failed')
        counts=_counts(conn)
        if '_character_migration' in counts:raise ValueError('Source is already migrated; do not assign it again')
        if 'character' in counts:
            # A fresh compatibility schema may contain only the neutral legacy root.
            owners=conn.execute('SELECT id FROM character').fetchall()
            if owners and owners != [(UUID(LEGACY_ID).hex,)]:
                raise ValueError('Source already has character workspaces; migration needs explicit review')
    with _ro(chat) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('AutoChat integrity check failed')
        chat_counts=_counts(conn)
        if 'workspace_owner' in chat_counts:raise ValueError('AutoChat source already has an owner')
    return {'autopost_counts':counts,'autochat_counts':chat_counts,
            'source_fingerprint':_fingerprint(post,chat,media)}


def _backup(source,target):
    with _ro(source) as src,_connection(target) as dst:src.backup(dst)
    os.chmod(target,0o600)


def _migrate_post(path,owner,name):
    owner_hex=UUID(owner).hex
    with _connection(path) as conn:
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('''CREATE TABLE IF NOT EXISTS character (
 id CHAR(32) PRIMARY KEY NOT NULL,name VARCHAR(120) NOT NULL,
 planning_enabled BOOLEAN NOT NULL DEFAULT 0,chat_enabled BOOLEAN NOT NULL DEFAULT 0,
 is_archived BOOLEAN NOT NULL DEFAULT 0,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('INSERT OR IGNORE INTO character(id,name) VALUES(?,?)',(owner_hex,name))
        conn.execute('UPDATE character SET name=? WHERE id=?',(name,owner_hex))
        tables=set(_counts(conn))
        for table in OWNED_TABLES:
            if table not in tables:continue
            cols=[row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            if 'character_id' not in cols:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN character_id CHAR(32) NOT NULL DEFAULT \'{owner_hex}\' REFERENCES character(id) ON DELETE RESTRICT')
            else:
                owners=conn.execute(f'SELECT DISTINCT character_id FROM "{table}"').fetchall()
                # Only the two known equivalent neutral legacy encodings are
                # accepted. Normalize in this new copy, never in the source.
                accepted = (None, owner_hex, LEGACY_ID) if owner == LEGACY_ID else (None, owner_hex)
                if any(row[0] not in accepted for row in owners):raise ValueError('Ambiguous existing character ownership')
                conn.execute(f'UPDATE "{table}" SET character_id=? WHERE character_id IS NULL OR character_id=?', (owner_hex, LEGACY_ID if owner == LEGACY_ID else owner_hex))
            conn.execute(f'CREATE INDEX IF NOT EXISTS "ix_{table}_character_id" ON "{table}"(character_id)')
        from tools.sqlite_scope import scope_unique
        if 'media_asset' in tables:
            scope_unique(conn, 'media_asset', 'sha256', 'uq_character_media_sha256')
        if 'persona' in tables:
            scope_unique(conn, 'persona', 'slug', 'uq_character_persona_slug')
        if 'app_setting' in tables:
            rows=conn.execute('SELECT * FROM app_setting').fetchall()
            cols=[row[1] for row in conn.execute('PRAGMA table_info(app_setting)')]
            key_idx=cols.index('key')
            for row in rows:
                values=list(row);values[key_idx]=owner+':'+str(values[key_idx])
                conn.execute('INSERT INTO app_setting ('+','.join('"'+c+'"' for c in cols)+') VALUES ('+','.join('?' for _ in cols)+')',values)
                conn.execute('DELETE FROM app_setting WHERE key=?', (row[key_idx],))
        conn.execute('CREATE TABLE _character_migration(version INTEGER NOT NULL, owner_id TEXT NOT NULL)')
        conn.execute('INSERT INTO _character_migration VALUES(1,?)', (owner,))
        problems=conn.execute('PRAGMA foreign_key_check').fetchall()
        if problems:raise ValueError('Foreign-key preflight failed in migrated copy')
        conn.commit()


def _consolidate_technical(post_path, technical_path, owner, sources):
    """Consolidate only infrastructure; keep account ciphertext with its owner."""
    from app.setting_scope import GLOBAL_TECHNICAL_KEYS, encode_technical_value, decode_technical_value
    from autoposter.crypto import decrypt
    with _connection(post_path) as post, _connection(technical_path) as technical:
        if 'app_setting' not in _counts(post):
            return
        row = post.execute('SELECT value FROM app_setting WHERE key=?', (owner + ':integrations',)).fetchone()
        if row is None:
            return
        values = json.loads(row[0])
        for key in sorted(GLOBAL_TECHNICAL_KEYS & values.keys()):
            value = values.pop(key)
            if value not in (None, ''):
                if key in {'openrouter_api_key', 'github_token'}:
                    value = decrypt(value)
                existing = technical.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                cleared = technical.execute('SELECT value FROM settings WHERE key=?', ('__technical_cleared__:' + key,)).fetchone()
                previous = decode_technical_value(key, json.loads(existing[0])) if existing else None
                if not cleared and previous not in (None, '') and previous != value:
                    if sources.get(key) not in {'autochat', 'autopost'}:
                        raise ValueError('Technical setting conflict: ' + key)
                    if sources[key] == 'autochat':
                        continue
                if not cleared and (previous in (None, '') or sources.get(key) == 'autopost'):
                    technical.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                      (key, json.dumps(encode_technical_value(key, value))))
        post.execute('UPDATE app_setting SET value=? WHERE key=?', (json.dumps(values), owner + ':integrations'))


def _output_hashes(root):
    return {p.relative_to(root).as_posix():_hash(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p.name!='migration-report.json'}


def build_workspace_copy(autopost_db,autochat_db,media_root,destination,*,name,technical_sources=None):
    technical_sources = dict(technical_sources or {})
    from app.setting_scope import GLOBAL_TECHNICAL_KEYS
    if any(key not in GLOBAL_TECHNICAL_KEYS or value not in ('autochat', 'autopost')
           for key, value in technical_sources.items()):
        raise ValueError('Invalid technical source selection')
    if not isinstance(name,str) or not name.strip() or len(name.strip())>120:
        raise ValueError('Explicit existing-character name required (1–120 characters)')
    name=name.strip()
    report=preflight(autopost_db,autochat_db,media_root)
    dest=Path(destination).resolve()
    if any(dest==Path(src).resolve() or dest in Path(src).resolve().parents
           or Path(src).resolve() in dest.parents
           for src in (autopost_db,autochat_db,media_root)):
        raise ValueError('Destination must be separate from all source paths')
    if dest.exists():
        receipt=dest/'migration-report.json'
        if not receipt.is_file():raise ValueError('Destination already exists; never overwrite it')
        previous=json.loads(receipt.read_text())
        if (previous.get('source_fingerprint')!=report['source_fingerprint'] or previous.get('name')!=name
                or previous.get('technical_sources', {}) != technical_sources):
            raise ValueError('Destination belongs to a different migration')
        if previous.get('output_hashes')!=_output_hashes(dest):
            raise ValueError('Destination changed since migration; refusing idempotent reuse')
        return previous

    dest.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.character-copy-',dir=dest.parent))
    try:
        _backup(autopost_db,stage/'autoposter.db')
        _migrate_post(stage/'autoposter.db',LEGACY_ID,name)
        chat_dir=stage/'characters'/LEGACY_ID;chat_dir.mkdir(parents=True,mode=0o700)
        _backup(autochat_db,chat_dir/'bot.db')
        with _connection(chat_dir/'bot.db') as conn:
            conn.execute('PRAGMA journal_mode=DELETE')
            conn.execute('CREATE TABLE workspace_owner(id TEXT PRIMARY KEY)')
            conn.execute('INSERT INTO workspace_owner VALUES(?)',(LEGACY_ID,))
        # The global store contains infrastructure settings only, never copied
        # tokens/conversations. Preserve every other row in the existing owner.
        from app.setting_scope import GLOBAL_TECHNICAL_KEYS, encode_technical_value, decode_technical_value
        with _connection(chat_dir/'bot.db') as owned, _connection(stage/'bot.db') as technical:
            from app.technical_store import initialize as initialize_technical
            initialize_technical(technical)
            tables = {row[0] for row in owned.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'settings' in tables:
                rows = owned.execute('SELECT key,value FROM settings').fetchall()
                selected = [(key,value) for key,value in rows
                            if key in GLOBAL_TECHNICAL_KEYS or key.startswith('__technical_cleared__:')]
                protected = [(key, json.dumps(encode_technical_value(key,
                              decode_technical_value(key, json.loads(value)))))
                             for key, value in selected]
                technical.executemany('INSERT INTO settings(key,value) VALUES(?,?)', protected)
                owned.executemany('DELETE FROM settings WHERE key=?', [(key,) for key,_ in selected])
        os.chmod(stage/'bot.db', 0o600)
        _consolidate_technical(stage/'autoposter.db', stage/'bot.db', LEGACY_ID, technical_sources)
        shutil.copytree(media_root,stage/'media'/'characters'/LEGACY_ID)
        if _fingerprint(autopost_db,autochat_db,media_root)!=report['source_fingerprint']:
            raise ValueError('Source changed during copy; output discarded')
        report.update(version=1,character_id=LEGACY_ID,name=name,technical_sources=technical_sources,
                      output_hashes=_output_hashes(stage),activation_performed=False)
        (stage/'migration-report.json').write_text(json.dumps(report,indent=2))
        os.chmod(stage/'migration-report.json',0o600)
        if dest.exists():raise ValueError('Destination appeared during migration')
        stage.rename(dest)
        return report
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise


def initialize_empty(destination, *, name):
    """Explicit fresh-install schema creation, never called by application startup."""
    if not isinstance(name,str) or not name.strip() or len(name.strip()) > 120:
        raise ValueError('Explicit character name required (1–120 characters)')
    name = name.strip()
    destination = Path(destination).resolve()
    if destination.exists():
        receipt = destination / 'migration-report.json'
        if not receipt.is_file():
            raise ValueError('Destination already exists; never overwrite it')
        previous = json.loads(receipt.read_text())
        if previous.get('kind') != 'fresh' or previous.get('name') != name or previous.get('output_hashes') != _output_hashes(destination):
            raise ValueError('Destination does not match the fresh-install receipt')
        return previous
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.character-empty-',dir=destination.parent) as temporary:
        source = Path(temporary)
        from sqlalchemy import create_engine
        from autoposter.db import Base
        import autoposter.models
        from app.db import SCHEMA, DEFAULT_SETTINGS
        engine = create_engine('sqlite:///' + str(source / 'post.db'))
        try:
            Base.metadata.create_all(engine)
        finally:
            engine.dispose()
        with _connection(source / 'post.db') as conn:
            conn.execute('INSERT INTO app_setting(key,value) VALUES(?,?)',('runtime',json.dumps({'global_pause':True,'dry_run':True})))
        with _connection(source / 'chat.db') as conn:
            conn.executescript(SCHEMA)
            for key,value in DEFAULT_SETTINGS.items():
                conn.execute('INSERT INTO settings(key,value) VALUES(?,?)',(key,json.dumps(value)))
        (source / 'media').mkdir()
        report = build_workspace_copy(source / 'post.db',source / 'chat.db',source / 'media',destination,name=name)
    report['kind'] = 'fresh'
    (destination / 'migration-report.json').write_text(json.dumps(report,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--autopost-db',type=Path)
    parser.add_argument('--autochat-db',type=Path)
    parser.add_argument('--media-root',type=Path)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--existing-character-name')
    parser.add_argument('--apply-copy',action='store_true')
    parser.add_argument('--initialize-empty',action='store_true')
    parser.add_argument('--technical-sources', type=Path,
                        help='JSON object: conflicting technical field -> autochat or autopost; never secret values')
    args=parser.parse_args()
    sources = None
    if args.technical_sources:
        if not args.apply_copy or args.initialize_empty:
            parser.error('--technical-sources requires --apply-copy')
        sources = json.loads(args.technical_sources.read_text())
        if not isinstance(sources, dict):
            parser.error('--technical-sources must contain a JSON object')
    if args.initialize_empty:
        if args.apply_copy or args.autopost_db or args.autochat_db or args.media_root:
            parser.error('--initialize-empty cannot accept existing source paths')
        if not args.destination:
            parser.error('--initialize-empty requires --destination')
        result=initialize_empty(args.destination,name=args.existing_character_name)
    else:
        if not all((args.autopost_db,args.autochat_db,args.media_root)):
            parser.error('Explicit --autopost-db, --autochat-db and --media-root required')
        if args.apply_copy:
            if not args.destination:parser.error('--apply-copy requires --destination')
            result=build_workspace_copy(args.autopost_db,args.autochat_db,args.media_root,args.destination,name=args.existing_character_name,technical_sources=sources)
        else:result=preflight(args.autopost_db,args.autochat_db,args.media_root)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
