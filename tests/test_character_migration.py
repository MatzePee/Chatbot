"""Migration only exercises temporary synthetic input files, never installed data."""
import hashlib,json,sqlite3,uuid
from pathlib import Path


def synthetic(tmp_path):
    post=tmp_path/'old-post.db'
    with sqlite3.connect(post) as conn:
        conn.executescript('''CREATE TABLE saved_view(id CHAR(32) PRIMARY KEY,name TEXT NOT NULL,icon TEXT,query JSON,is_system BOOLEAN,position INTEGER,owner_id CHAR(32),created_at DATETIME,updated_at DATETIME);
CREATE TABLE app_setting(key TEXT PRIMARY KEY,value JSON,created_at DATETIME,updated_at DATETIME);
INSERT INTO app_setting VALUES('nightly_gate','{"day":"2026-11-15"}',NULL,NULL);''')
        conn.execute('INSERT INTO saved_view VALUES(?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,'Private view','bookmark','{}',0,0,None,'2026-11-15','2026-11-15'))
    chat=tmp_path/'old-chat.db'
    with sqlite3.connect(chat) as conn:
        conn.executescript("CREATE TABLE tokens(id INTEGER PRIMARY KEY,access_token TEXT);INSERT INTO tokens VALUES(1,'synthetic-not-secret');CREATE TABLE drafts(id INTEGER PRIMARY KEY,edited_text TEXT);INSERT INTO drafts VALUES(9,'Keep me');")
    media=tmp_path/'old-media';media.mkdir();(media/'synthetic.jpg').write_bytes(b'not-real-media')
    return post,chat,media


def test_explicit_copy_migration_preserves_sources_is_idempotent_and_names_existing_character(tmp_path):
    from tools.character_migrate import build_workspace_copy,preflight
    post,chat,media=synthetic(tmp_path)
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (post,chat,media/'synthetic.jpg')}
    before=set(tmp_path.iterdir())
    report=preflight(post,chat,media)
    assert report['autopost_counts']['saved_view']==1
    assert set(tmp_path.iterdir())==before
    dest=tmp_path/'migration-output'
    result=build_workspace_copy(post,chat,media,dest,name='Cherry existing installation')
    owner=result['character_id'];assert uuid.UUID(owner)
    with sqlite3.connect(dest/'autoposter.db') as conn:
        assert conn.execute('SELECT name FROM character WHERE id=?',(uuid.UUID(owner).hex,)).fetchone()[0]=='Cherry existing installation'
        assert conn.execute('SELECT name,character_id FROM saved_view').fetchone()==('Private view',uuid.UUID(owner).hex)
        assert json.loads(conn.execute('SELECT value FROM app_setting WHERE key=?',(owner+':nightly_gate',)).fetchone()[0])=={'day':'2026-11-15'}
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
    with sqlite3.connect(dest/'characters'/owner/'bot.db') as conn:
        assert conn.execute('SELECT edited_text FROM drafts WHERE id=9').fetchone()[0]=='Keep me'
        assert conn.execute('SELECT id FROM workspace_owner').fetchone()[0]==owner
    assert (dest/'media'/'characters'/owner/'synthetic.jpg').read_bytes()==b'not-real-media'
    assert build_workspace_copy(post,chat,media,dest,name='Cherry existing installation')==result
    for p,digest in hashes.items():assert hashlib.sha256(p.read_bytes()).hexdigest()==digest


def test_migration_requires_name_and_refuses_existing_unknown_destination(tmp_path):
    import pytest
    from tools.character_migrate import build_workspace_copy
    post,chat,media=synthetic(tmp_path)
    with pytest.raises(ValueError):build_workspace_copy(post,chat,media,tmp_path/'out',name='')
    dest=tmp_path/'existing';dest.mkdir();(dest/'precious').write_text('keep')
    with pytest.raises(ValueError):build_workspace_copy(post,chat,media,dest,name='Existing')
    assert (dest/'precious').read_text()=='keep'


def test_copy_migration_places_technical_configuration_only_in_global_store(tmp_path):
    from tools.character_migrate import build_workspace_copy
    post,chat,media=synthetic(tmp_path)
    with sqlite3.connect(chat) as conn:
        conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
        conn.executemany('INSERT INTO settings VALUES(?,?)',[
            ('openrouter_api_key',json.dumps('synthetic-global-key')),
            ('git_branch',json.dumps('private-installation-branch')),
            ('system_prompt',json.dumps('Existing character private')),
            ('fanvue_client_secret',json.dumps('synthetic-character-secret'))])
    dest=tmp_path/'output'
    result=build_workspace_copy(post,chat,media,dest,name='Existing')
    with sqlite3.connect(dest/'bot.db') as conn:
        values=dict(conn.execute('SELECT key,value FROM settings'))
        from app.setting_scope import decode_technical_value
        assert decode_technical_value('openrouter_api_key', json.loads(values['openrouter_api_key']))=='synthetic-global-key'
        assert json.loads(values['git_branch'])=='private-installation-branch'
        assert 'system_prompt' not in values and 'fanvue_client_secret' not in values
        assert 'tokens' not in [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    with sqlite3.connect(dest/'characters'/result['character_id']/'bot.db') as conn:
        values=dict(conn.execute('SELECT key,value FROM settings'))
        assert 'openrouter_api_key' not in values and 'git_branch' not in values
        assert json.loads(values['system_prompt'])=='Existing character private'
        assert json.loads(values['fanvue_client_secret'])=='synthetic-character-secret'


def test_copy_migration_encrypts_technical_secret_with_installation_key(tmp_path):
    from tools.character_migrate import build_workspace_copy
    from app.setting_scope import decode_technical_value
    post, chat, media = synthetic(tmp_path)
    with sqlite3.connect(chat) as conn:
        conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
        conn.execute('INSERT INTO settings VALUES(?,?)', ('github_token', json.dumps('synthetic-legacy-github')))
    dest = tmp_path / 'output'
    result = build_workspace_copy(post, chat, media, dest, name='Existing')
    with sqlite3.connect(dest / 'bot.db') as conn:
        raw = conn.execute("SELECT value FROM settings WHERE key='github_token'").fetchone()[0]
        assert 'synthetic-legacy-github' not in raw
        assert decode_technical_value('github_token', json.loads(raw)) == 'synthetic-legacy-github'
    assert build_workspace_copy(post, chat, media, dest, name='Existing') == result


def test_copy_migration_moves_post_technical_fields_and_never_leaves_global_accounts(tmp_path):
    from tools.character_migrate import build_workspace_copy
    from autoposter.crypto import encrypt, decrypt
    from app.setting_scope import decode_technical_value
    post, chat, media = synthetic(tmp_path)
    secret = encrypt('synthetic-post-account')
    with sqlite3.connect(post) as conn:
        conn.execute('INSERT INTO app_setting VALUES(?,?,NULL,NULL)', ('integrations', json.dumps({
            'openrouter_api_key': encrypt('synthetic-post-global'),
            'git_branch': 'preserve-branch', 'x_client_secret': secret,
            'auto_plan_enabled': True,
        })))
    dest = tmp_path / 'output'
    result = build_workspace_copy(post, chat, media, dest, name='Existing')
    with sqlite3.connect(dest / 'bot.db') as conn:
        values = dict(conn.execute('SELECT key,value FROM settings'))
        assert 'openrouter_api_key' in values
        assert decode_technical_value('openrouter_api_key', json.loads(values['openrouter_api_key'])) == 'synthetic-post-global'
        assert json.loads(values['git_branch']) == 'preserve-branch'
        assert 'x_client_secret' not in values
    with sqlite3.connect(dest / 'autoposter.db') as conn:
        rows = dict(conn.execute('SELECT key,value FROM app_setting'))
        assert all(key.startswith(result['character_id'] + ':') for key in rows)
        owned = json.loads(rows[result['character_id'] + ':integrations'])
        assert 'openrouter_api_key' not in owned and 'git_branch' not in owned
        assert decrypt(owned['x_client_secret']) == 'synthetic-post-account'
        assert owned['auto_plan_enabled'] is True


def test_conflicting_technical_migration_fails_closed_without_secret_values(tmp_path):
    import pytest
    from tools.character_migrate import build_workspace_copy
    from autoposter.crypto import encrypt
    post, chat, media = synthetic(tmp_path)
    with sqlite3.connect(chat) as conn:
        conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
        conn.execute('INSERT INTO settings VALUES(?,?)', ('openrouter_api_key', json.dumps('synthetic-chat-conflict')))
    with sqlite3.connect(post) as conn:
        conn.execute('INSERT INTO app_setting VALUES(?,?,NULL,NULL)', ('integrations', json.dumps({
            'openrouter_api_key': encrypt('synthetic-post-conflict')})))
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (post, chat)}
    dest = tmp_path / 'output'
    with pytest.raises(ValueError, match='Technical setting conflict: openrouter_api_key') as error:
        build_workspace_copy(post, chat, media, dest, name='Existing')
    assert 'synthetic-' not in str(error.value)
    assert not dest.exists()
    assert not list(tmp_path.glob('.character-copy-*'))
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (post, chat)}


def test_operator_resolves_conflicts_without_values_in_receipt(tmp_path):
    from tools.character_migrate import build_workspace_copy
    from app.setting_scope import decode_technical_value
    post, chat, media = synthetic(tmp_path)
    with sqlite3.connect(chat) as conn:
        conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
        conn.executemany('INSERT INTO settings VALUES(?,?)', [
            ('git_branch', json.dumps('chat-branch')), ('public_base_url', json.dumps('https://chat.invalid'))])
    with sqlite3.connect(post) as conn:
        conn.execute('INSERT INTO app_setting VALUES(?,?,NULL,NULL)', ('integrations', json.dumps({
            'git_branch': 'post-branch', 'public_base_url': 'https://post.invalid'})))
    choices = {'git_branch': 'autopost', 'public_base_url': 'autochat'}
    dest = tmp_path / 'output'
    result = build_workspace_copy(post, chat, media, dest, name='Existing', technical_sources=choices)
    assert result['technical_sources'] == choices
    assert 'post-branch' not in json.dumps(result)
    with sqlite3.connect(dest / 'bot.db') as conn:
        values = {key: decode_technical_value(key, json.loads(value)) for key, value in conn.execute('SELECT key,value FROM settings')}
        assert values['git_branch'] == 'post-branch'
        assert values['public_base_url'] == 'https://chat.invalid'
    assert build_workspace_copy(post, chat, media, dest, name='Existing', technical_sources=choices) == result
    import pytest
    with pytest.raises(ValueError, match='different migration'):
        build_workspace_copy(post, chat, media, dest, name='Existing', technical_sources={'git_branch': 'autochat'})


def test_migration_rejects_invalid_source_choices_before_creating_output(tmp_path):
    import pytest
    from tools.character_migrate import build_workspace_copy
    post, chat, media = synthetic(tmp_path)
    for choices in ({'x_client_secret': 'autochat'}, {'git_branch': 'synthetic-not-a-source'}):
        dest = tmp_path / 'invalid-output'
        with pytest.raises(ValueError, match='Invalid technical source selection') as error:
            build_workspace_copy(post, chat, media, dest, name='Existing', technical_sources=choices)
        assert 'synthetic-not-a-source' not in str(error.value)
        assert not dest.exists()


def test_copy_cli_accepts_explicit_technical_source_selection(tmp_path):
    import os, subprocess, sys
    post, chat, media = synthetic(tmp_path)
    with sqlite3.connect(chat) as conn:
        conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
        conn.execute('INSERT INTO settings VALUES(?,?)', ('git_branch', json.dumps('chat-branch')))
    with sqlite3.connect(post) as conn:
        conn.execute('INSERT INTO app_setting VALUES(?,?,NULL,NULL)', ('integrations', json.dumps({'git_branch': 'post-branch'})))
    choices = tmp_path / 'technical-sources.json'
    choices.write_text(json.dumps({'git_branch': 'autopost'}))
    destination = tmp_path / 'cli-output'
    env = dict(os.environ, MP_AUTOPOSTER_DATA_DIR=str(tmp_path/'config'), MP_AUTOPOSTER_MEDIA_ROOT=str(tmp_path/'config-media'), MP_AUTOPOSTER_DATABASE_URL='sqlite+aiosqlite:///:memory:')
    command = [sys.executable, '-m', 'tools.character_migrate', '--apply-copy', '--autopost-db', str(post), '--autochat-db', str(chat), '--media-root', str(media), '--destination', str(destination), '--existing-character-name', 'Existing', '--technical-sources', str(choices)]
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)['technical_sources'] == {'git_branch': 'autopost'}
    with sqlite3.connect(destination / 'bot.db') as conn:
        assert json.loads(conn.execute("SELECT value FROM settings WHERE key='git_branch'").fetchone()[0]) == 'post-branch'


def test_migration_refuses_output_inside_source_before_creating_staging(tmp_path,monkeypatch):
    import pytest
    from tools import character_migrate as migration
    post,chat,media=synthetic(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('must reject nested output before staging')
    monkeypatch.setattr(migration.tempfile,'mkdtemp',forbidden)
    with pytest.raises(ValueError,match='separate'):
        migration.build_workspace_copy(post,chat,media,media/'output',name='Existing')


def test_copy_migration_scopes_inline_sha_and_persona_unique_preserving_triggers(tmp_path):
    from tools.character_migrate import build_workspace_copy
    post,chat,media=synthetic(tmp_path)
    with sqlite3.connect(post) as conn:
        conn.executescript('''CREATE TABLE media_asset(id TEXT PRIMARY KEY,sha256 TEXT UNIQUE NOT NULL, note TEXT DEFAULT 'UNIQUE');
CREATE TABLE persona(id TEXT PRIMARY KEY,slug TEXT NOT NULL,extra TEXT,CONSTRAINT legacy_slug UNIQUE(slug));
CREATE TABLE audit(id TEXT);
CREATE TRIGGER media_audit AFTER INSERT ON media_asset BEGIN INSERT INTO audit VALUES(NEW.id); END;
INSERT INTO media_asset(id,sha256,note) VALUES('one','same-bytes','Keep');
INSERT INTO persona VALUES('persona-one','main','Keep persona');''')
    output=tmp_path/'output'
    result=build_workspace_copy(post,chat,media,output,name='Operator approved name')
    owner=uuid.UUID(result['character_id']).hex
    other=uuid.uuid4().hex
    with sqlite3.connect(output/'autoposter.db') as conn:
        conn.execute('INSERT INTO character(id,name) VALUES(?,?)',(other,'Other'))
        conn.execute('INSERT INTO media_asset(id,sha256,character_id) VALUES(?,?,?)',('two','same-bytes',other))
        conn.execute('INSERT INTO persona(id,slug,character_id) VALUES(?,?,?)',('persona-two','main',other))
        assert conn.execute("SELECT note FROM media_asset WHERE id='two'").fetchone()[0]=='UNIQUE'
        assert conn.execute("SELECT extra FROM persona WHERE id='persona-one'").fetchone()[0]=='Keep persona'
        assert conn.execute('SELECT id FROM audit ORDER BY id').fetchall()==[('one',),('two',)]
        import pytest
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO media_asset(id,sha256,character_id) VALUES(?,?,?)',('three','same-bytes',owner))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO persona(id,slug,character_id) VALUES(?,?,?)',('persona-three','main',owner))
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_fresh_initializer_cli_is_explicit_idempotent_and_disabled(tmp_path):
    import os, subprocess, sys
    destination=tmp_path/'new-installation'
    env=dict(os.environ,MP_AUTOPOSTER_DATA_DIR=str(tmp_path/'config'),MP_AUTOPOSTER_MEDIA_ROOT=str(tmp_path/'config-media'),MP_AUTOPOSTER_DATABASE_URL='sqlite+aiosqlite:///:memory:')
    command=[sys.executable,'-m','tools.character_migrate','--initialize-empty','--destination',str(destination),'--existing-character-name','Neutral operator choice']
    first=subprocess.run(command,env=env,capture_output=True,text=True,timeout=30)
    assert first.returncode==0,first.stderr
    report=json.loads(first.stdout)
    second=subprocess.run(command,env=env,capture_output=True,text=True,timeout=30)
    assert second.returncode==0,second.stderr
    assert json.loads(second.stdout)==report
    with sqlite3.connect(destination/'autoposter.db') as conn:
        assert conn.execute('SELECT name,planning_enabled,chat_enabled FROM character').fetchall()==[('Neutral operator choice',0,0)]
        runtime=json.loads(conn.execute('SELECT value FROM app_setting WHERE key=?',(report['character_id']+':runtime',)).fetchone()[0])
        assert runtime=={'global_pause':True,'dry_run':True}
    with sqlite3.connect(destination/'characters'/report['character_id']/'bot.db') as conn:
        assert conn.execute('SELECT count(*) FROM tokens').fetchone()[0]==0
        assert json.loads(conn.execute("SELECT value FROM settings WHERE key='bot_running'").fetchone()[0]) is False
