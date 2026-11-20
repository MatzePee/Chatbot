"""Exercise only the actual startup hook in a fresh process, never lifespan."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid


def test_active_startup_backup_contains_databases_keys_and_configuration(tmp_path):
    from tools.character_migrate import initialize_empty, LEGACY_ID
    from tools.sqlite_layout import convert_copy
    repo=tmp_path/'repo';identity=str(uuid.uuid4())
    generation=repo/'data/character-releases'/identity
    active=generation/'active'
    initialize_empty(tmp_path/'workspace',name='Cherry')
    convert_copy(tmp_path/'workspace',active,autopost_db='autoposter.db',autochat_db='bot.db',sources_stopped=True)
    other=str(uuid.uuid4());owner_dir=active/'characters'/other;owner_dir.mkdir()
    with sqlite3.connect(owner_dir/'bot.db') as c:
        c.execute('create table proof(value text)');c.execute("insert into proof values('second-owner')")
    (repo/'.env').write_text('SECRET_KEY=synthetic-key\n')
    (repo/'data/autoposter.env').write_text('ENCRYPTION_KEY=synthetic-encryption-key\n')
    (generation/'configuration').mkdir();(generation/'configuration/keys.json').write_text('{"key":"synthetic"}')
    pointer={'id':identity,'name':'Cherry','version':1}
    (repo/'data/character-active.json').write_text(json.dumps(pointer))
    # Keep uncheckpointed committed WAL bytes alive across the snapshot.
    live=sqlite3.connect(active/'bot.db');live.execute('pragma journal_mode=WAL')
    live.execute("insert into settings values('backup_proof','\"committed-wal\"')");live.commit()
    script=tmp_path/'startup.py'
    script.write_text('''import sys
sys.path.insert(0,sys.argv[1])
from app.character_upgrade import bootstrap
bootstrap(sys.argv[2])
from app import main
main._startup()
''')
    env=dict(os.environ,MP_AUTOPOSTER_SCHEDULER_MODE='off',PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([sys.executable,'-B',str(script),str(Path(__file__).parents[1]),str(repo)],cwd=tmp_path,env=env,check=True,timeout=30)
    backups=list((repo/'data/backups').glob('creatorpilot-*'))
    assert len(backups)==1
    backup=backups[0]
    prefix=Path('data/character-releases')/identity/'active'
    required=[prefix/'bot.db',prefix/'autoposter.db',prefix/'characters'/LEGACY_ID/'bot.db',prefix/'characters'/other/'bot.db',Path('.env'),Path('data/autoposter.env'),Path('data/character-active.json'),Path('data/character-releases')/identity/'configuration/keys.json']
    assert all((backup/p).is_file() for p in required), [str(p) for p in required if not (backup/p).is_file()]
    for p in required:
        if p.suffix=='.db':
            with sqlite3.connect(backup/p) as c:assert c.execute('pragma integrity_check').fetchone()[0]=='ok'
        else:assert (backup/p).read_bytes()==(repo/p).read_bytes()
    with sqlite3.connect(backup/prefix/'bot.db') as c:
        assert c.execute("select value from settings where key='backup_proof'").fetchone()[0]=='"committed-wal"'
    with sqlite3.connect(backup/prefix/'characters'/other/'bot.db') as c:
        assert c.execute('select value from proof').fetchone()[0]=='second-owner'
    live.close()
