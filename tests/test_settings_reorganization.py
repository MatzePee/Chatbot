"""Approved task-specific canonical AI configuration; no lifespan or providers."""
import uuid
import pytest
from app import db
from autoposter.workspaces import character_context


def test_consolidation_requires_explicit_sources_and_preserves_originals(tmp_path):
    import json, sqlite3, hashlib
    from tools.settings_consolidate import consolidate
    def store(name, values):
        p = tmp_path / name
        with sqlite3.connect(p) as c:
            c.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            c.executemany('INSERT INTO settings VALUES(?,?)', [(k,json.dumps(v)) for k,v in values.items()])
        return p
    a=store('a.db', {'openrouter_model':'model/a','vision_model':'vision/chat'})
    b=store('b.db', {'openrouter_model':'model/b','vision_model':'vision/chat'})
    dest=store('global.db', {'openrouter_model':db.DEFAULT_SETTINGS['openrouter_model']})
    before={p:p.read_bytes() for p in (a,b,dest)}
    args=dict(root=tmp_path,destination='global.db',sources={'a':'a.db','b':'b.db'})
    report=consolidate(**args)
    assert report['conflicts']==['openrouter_model'] and not report['applied']
    assert all(p.read_bytes()==raw for p,raw in before.items())
    with pytest.raises(ValueError,match='conflict'):
        consolidate(**args,apply=True,stopped_ack=True)
    with pytest.raises(ValueError,match='stopped'):
        consolidate(**args,apply=True,select={'openrouter_model':'a'})
    result=consolidate(**args,apply=True,stopped_ack=True,select={'openrouter_model':'b'})
    assert result['applied']
    assert all(p.read_bytes()==before[p] for p in (a,b))
    with sqlite3.connect(dest) as c:
        assert json.loads(c.execute("SELECT value FROM settings WHERE key='openrouter_model'").fetchone()[0])=='model/b'
    assert 'model/b' not in json.dumps(result)
    # A deliberately written canonical value wins; a seeded default did not.
    assert consolidate(**args)['conflicts']==[]


def test_consolidation_explicit_clear_beats_nonempty_and_rejects_paths(tmp_path):
    import sqlite3,json
    from tools.settings_consolidate import consolidate
    for name, rows in [('a.db',{'classifier_model':'owner/model'}),('global.db',{'classifier_model':'','__technical_cleared__:classifier_model':True})]:
        with sqlite3.connect(tmp_path/name) as c:
            c.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            c.executemany('INSERT INTO settings VALUES(?,?)',[(k,json.dumps(v)) for k,v in rows.items()])
    r=consolidate(tmp_path,'global.db',{'a':'a.db'})
    assert 'classifier_model' in r['canonical_retained']
    with pytest.raises(ValueError):
        consolidate(tmp_path,'global.db',{'a':'../a.db'})

PROMOTED = {'openrouter_model': 'deepseek/deepseek-v4-pro',
            'classifier_model': 'openai/gpt-5.6-luna',
            'ppv_caption_model': 'qwen/qwen3-vl-235b-a22b-instruct',
            'vision_model': 'qwen/qwen3-vl-235b-a22b-instruct',
            'memory_model': 'memory/global',
            'temperature': 0.7, 'max_tokens': 1234, 'reasoning_mode': 'exclude',
            'generation_retries': 4, 'generation_retry_delay': 42}


def test_six_characters_use_canonical_task_models_and_knobs_not_owner_rows():
    from app.setting_scope import GLOBAL_TECHNICAL_KEYS
    assert set(PROMOTED) <= GLOBAL_TECHNICAL_KEYS
    for key, value in PROMOTED.items():
        db.set_setting(key, value)
    db.set_setting('openrouter_model_vision', 'openai/gpt-5.6-luna-pro')
    for i in range(6):
        with character_context(uuid.uuid4()):
            conn = db.get_conn()
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('vision_model','\"stale/owner\"')")
            conn.commit()
            db.set_setting('vision_prompt', 'Owner prompt ' + str(i))
            for key, value in PROMOTED.items():
                assert db.get_setting(key) == value
                assert db.all_settings()[key] == value
            assert db.get_setting('openrouter_model_vision') == 'openai/gpt-5.6-luna-pro'
            assert db.get_setting('vision_prompt') == 'Owner prompt ' + str(i)
