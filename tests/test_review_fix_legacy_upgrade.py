"""Real original metadata -> normal additive legacy startup -> explicit copy."""
import os
from pathlib import Path
import subprocess
import sys
import sqlite3
import hashlib
import pytest

BASE = Path('/root/multi-character-activation/fresh-baseline')

@pytest.mark.parametrize('old_default', [False, True])
def test_real_baseline_then_legacy_startup_then_copy(tmp_path, old_default):
    env = dict(os.environ, MP_AUTOPOSTER_DATABASE_URL='sqlite+aiosqlite:///'+str(tmp_path/'post.db'),
               MP_AUTOPOSTER_DATA_DIR=str(tmp_path/'studio'), MP_AUTOPOSTER_MEDIA_ROOT=str(tmp_path/'media'),
               MP_AUTOCHAT_DATA_DIR=str(tmp_path/'chat'), PYTHONDONTWRITEBYTECODE='1')
    script = tmp_path/'baseline.py'
    script.write_text('''import sys,asyncio
sys.path.insert(0, sys.argv[1])
from autoposter.db import Base,engine
from autoposter import models
from sqlalchemy.ext.asyncio import async_sessionmaker
async def main():
 async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
 async with async_sessionmaker(engine)() as s:
  s.add(models.Persona(name='Cherry',slug='cherry')); await s.commit()
 await engine.dispose()
asyncio.run(main())
''')
    subprocess.run([sys.executable, '-B', str(script), str(BASE)], env=env, cwd=tmp_path, check=True)
    script.write_text('''import sys,asyncio
sys.path.insert(0, sys.argv[1])
from autoposter.db import Base,engine
from autoposter import models,schema_sync
async def main():
 async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
 assert not (await schema_sync.sync(engine))['failed']
 await engine.dispose()
asyncio.run(main())
''')
    subprocess.run([sys.executable, '-B', str(script), str(Path(__file__).parents[1])], env=env, cwd=tmp_path, check=True)
    from tools.character_migrate import build_workspace_copy, LEGACY_ID
    with sqlite3.connect(tmp_path/'post.db') as c:
        c.execute('pragma wal_checkpoint(TRUNCATE)')
        c.execute('pragma journal_mode=DELETE')
        raw=c.execute('select character_id from persona').fetchone()[0]
        if not old_default:
            assert raw == LEGACY_ID.replace('-', '')
        else:
            # Exact value emitted by the independently reproduced previous sync.
            c.execute('update persona set character_id=?', (LEGACY_ID,))
    (tmp_path/'media').mkdir(exist_ok=True)
    with sqlite3.connect(tmp_path/'chat.db') as c: c.execute('create table settings(key text primary key,value text)')
    originals={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path/'post.db',tmp_path/'chat.db')}
    args=(tmp_path/'post.db',tmp_path/'chat.db',tmp_path/'media',tmp_path/'copy')
    report=build_workspace_copy(*args,name='Cherry')
    assert build_workspace_copy(*args,name='Cherry') == report
    with sqlite3.connect(tmp_path/'copy/autoposter.db') as c:
        assert c.execute('select character_id from persona').fetchone()[0] == LEGACY_ID.replace('-','')
        assert c.execute('pragma foreign_key_check').fetchall() == []
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in originals.items())
    with sqlite3.connect(tmp_path/'post.db') as c: c.execute('update persona set character_id=?', ('10000000-0000-0000-0000-000000000001',))
    with pytest.raises(ValueError, match='Ambiguous'):
        build_workspace_copy(*args[:3],tmp_path/'foreign',name='Cherry')
