"""Offline real-UID acceptance. Only systemd/transport boundaries may be simulated."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile

import pytest
from deploy import creatorstudio_update as helper
from tests.test_helper_environment import service_properties

SOURCE = Path(__file__).resolve().parents[1]


@pytest.fixture
def installed():
    assert os.geteuid() == 0, 'Real privilege acceptance requires root test supervisor'
    account = pwd.getpwnam('nobody')
    root = Path(tempfile.mkdtemp(prefix='helper-acceptance-'))
    root.chmod(0o755)
    repo = root / 'Cherry Bot'
    shutil.copytree(SOURCE, repo, ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache', 'data', 'exports'))
    (repo / '.venv').symlink_to(sys.prefix,target_is_directory=True)
    for path in [repo, *repo.rglob('*')]:
        os.chown(path, account.pw_uid, account.pw_gid, follow_symlinks=False)
    env = {**os.environ, 'PYTHONPATH': str(repo), 'MP_AUTOPOSTER_SCHEDULER_MODE': 'off',
           'MP_AUTOPOSTER_AUTH_ENABLED': 'false', 'MP_AUTOCHAT_DATA_DIR': str(repo/'data'),
           'MP_AUTOPOSTER_DATA_DIR': str(repo/'data/autoposter'),
           'MP_AUTOPOSTER_DATABASE_URL': 'sqlite+aiosqlite:///'+str(repo/'data/autoposter/autoposter.db'),
           'MP_AUTOPOSTER_MEDIA_ROOT': str(repo/'data/autoposter/media')}
    fixture_helpers = Path(tempfile.mkdtemp(prefix='readiness-installed-', dir='/run'))
    fixture_helpers.chmod(0o755)
    from deploy import repair_update as repair
    rendered = repair.rendered_files(repair.Installation('synthetic-cherry.service', account.pw_name, repo, 8000))
    (fixture_helpers/'wrapper').write_text(rendered[repair.WRAPPER][0])
    # Only systemctl reads are replaced in this installed root-owned fixture.
    # The real -I/FD entrypoint and real non-root /proc env read remain exercised.
    boundary = '''
def _fixture_systemctl(args, **kwargs):
    if args[:2] != ['systemctl','show']:
        raise RuntimeError('Unexpected fixture command')
    if '--value' in args: return ''
    repo=Path(sys.argv[2])
    props={key:'' for key in SERVICE_PROPERTIES}
    info=json.loads((repo/'data/.fixture-service.json').read_text())
    props.update(User='nobody',Group='nogroup',WorkingDirectory=str(repo),DynamicUser='no',
        MainPID=str(info['pid']),ControlPID='0',ActiveState='active',SubState='running',
        EnvironmentFiles=str(repo/'.env')+' (ignore_errors=no)',
        FragmentPath=info['fragment'],NeedDaemonReload='no',
        ExecStart='{ path='+str(repo/'.venv/bin/python')+' ; argv[]='+__import__('shlex').join(info['args'])+' ; ignore_errors=no ; }')
    return '\\n'.join(k+'='+v for k,v in props.items())
command=_fixture_systemctl
'''
    (fixture_helpers/'supervisor').write_text(rendered[repair.SUPERVISOR][0].replace("if __name__ == '__main__':", boundary+"\nif __name__ == '__main__':"))
    (fixture_helpers/'uvicorn.py').write_text('import time\ntime.sleep(90)\n')
    (fixture_helpers/'fixture.service').write_text((SOURCE/'deploy/fanvue-chatbot.service').read_text())
    for file in fixture_helpers.iterdir():file.chmod(0o755)
    def child(code, **kwargs):
        # A real nonroot PID with standard service argv/cwd/implicit groups.
        # Only its uvicorn module is a sleeping fixture, never an app worker.
        args=[str(repo/'.venv/bin/python'),'-m','uvicorn','app.main:app']
        holder=subprocess.Popen(args,cwd=repo,env={**env,'PYTHONPATH':str(fixture_helpers)},
            user=account.pw_uid,group=account.pw_gid,extra_groups=os.getgrouplist(account.pw_name,account.pw_gid))
        (repo/'data').mkdir(exist_ok=True)
        os.chown(repo/'data',account.pw_uid,account.pw_gid)
        metadata=repo/'data/.fixture-service.json'
        metadata.write_text(json.dumps({'pid':holder.pid,'args':args,'fragment':str(fixture_helpers/'fixture.service')}))
        metadata.chmod(0o644)
        prefix = ('from app import helper_readiness as _gate; from pathlib import Path; '
                  '_gate.WRAPPER=Path('+repr(str(fixture_helpers/'wrapper'))+'); '
                  '_gate.SUPERVISOR=Path('+repr(str(fixture_helpers/'supervisor'))+');\n')
        try:
            result = subprocess.run([sys.executable, '-c', prefix+code], cwd=repo, env=env,
                 user=account.pw_uid, group=account.pw_gid,
                 extra_groups=os.getgrouplist(account.pw_name,account.pw_gid),
                 capture_output=True, text=True, timeout=90, **kwargs)
            assert result.returncode == 0, result.stdout + result.stderr
            return result.stdout
        finally:
            holder.terminate();holder.wait(timeout=10)
    child('''
from pathlib import Path
from app import db
from sqlalchemy import create_engine
from autoposter.db import Base
import autoposter.models
root=Path.cwd()
(root/'data/autoposter/media').mkdir(parents=True,exist_ok=True)
db._seed_from_env=lambda:None
db.init_db(); db.set_setting('bot_running',False); db.set_setting('system_prompt','Cherry identity')
db._conn.close(); db._conn=None
engine=create_engine('sqlite:///'+str(root/'data/autoposter/autoposter.db'))
Base.metadata.create_all(engine);engine.dispose()
(root/'.env').write_text('PRIVATE_SYNTHETIC_KEY=unchanged\\n')
(root/'data/autoposter.env').write_text('SYNTHETIC_CONFIG=unchanged\\n')
(root/'exports').mkdir();(root/'exports/history.md').write_text('synthetic history')
import subprocess
for args in [['init','-q'],['config','user.name','Offline Fixture'],['config','user.email','fixture@example.invalid'],['add','.'],['commit','-qm','synthetic existing installation'],['tag','v9.0.0']]:
 result=subprocess.run(['git',*args],capture_output=True,text=True)
 assert result.returncode==0,(args,result.stderr)
''')
    # Installed version and subsequent release share real source; only the
    # package-resolution boundary uses an empty offline requirements fixture.
    child('''
import subprocess
from pathlib import Path
Path('requirements.txt').write_text('# Offline dependency boundary; libraries supplied by test environment\\n')
for args in [['add','requirements.txt'],['commit','-qm','offline code update'],['tag','v9.0.1'],
 ['clone','--bare','-q','.', 'data/offline-origin.git'],['checkout','--detach','-q','v9.0.0'],
 ['remote','add','origin',str(Path.cwd()/'data/offline-origin.git')]]:
 result=subprocess.run(['git',*args],capture_output=True,text=True)
 assert result.returncode==0,(args,result.stderr)
''')
    env['PYTHONPATH']=str(Path(sys.prefix)/'lib'/('python'+str(sys.version_info.major)+'.'+str(sys.version_info.minor))/'site-packages')
    env['PIP_NO_INDEX']='1'
    env['PIP_CONFIG_FILE']='/dev/null'
    try:
        yield repo, account, env, child
    finally:
        shutil.rmtree(root)
        shutil.rmtree(fixture_helpers)


def stage(child):
    # Real existing GUI handler/auth dependency, not direct migration shortcut.
    child('''
from fastapi import FastAPI
from fastapi.testclient import TestClient
from autoposter.api import system
from autoposter.deps import require_admin
app=FastAPI();app.include_router(system.router,prefix='/api/v1')
app.dependency_overrides[require_admin]=lambda:object()
with TestClient(app) as client:
 r=client.post('/api/v1/system/character-upgrade',json={'name':'Cherry','confirmed':True})
 assert r.status_code==200,r.text
 assert r.json()['phase']=='pending_restart'
''')


def simulate_stopped(monkeypatch,repo):
    calls=[]
    original=helper.command
    def command(args, **kwargs):
        if args[0]=='systemctl':
            calls.append(list(args))
            if 'show' in args:
                return service_properties(repo) if '--value' not in args else ''
            return ''
        return original(args,**kwargs)
    monkeypatch.setattr(helper,'command',command)
    return calls


def test_real_nonroot_proc_requires_authority_for_unreadable_other_users(installed):
    repo, account, env, child=installed
    output=child('''
from tools.sqlite_layout import _assert_stopped
from pathlib import Path
try: _assert_stopped(Path('data'))
except ValueError as e:
 assert 'inaccessible' in str(e),str(e)
 print('UNPRIVILEGED_GLOBAL_PROC_REFUSED')
else: raise AssertionError('fixture must reproduce inaccessible all-system /proc')
''')
    assert 'UNPRIVILEGED_GLOBAL_PROC_REFUSED' in output


@pytest.mark.parametrize('fault', ['old', 'missing', 'wrapper-old'])
def test_old_installed_helper_blocks_real_nonroot_gui_before_stage(installed, fault):
    repo, account, env, child = installed
    supervisor=Path(child("print(_gate.SUPERVISOR)").strip())
    wrapper=Path(child("print(_gate.WRAPPER)").strip())
    if fault=='missing':supervisor.unlink()
    elif fault=='old':supervisor.write_text(supervisor.read_text().replace('HELPER_VERSION = 4','HELPER_VERSION = 3'))
    else:wrapper.write_text(wrapper.read_text().replace('HELPER_PROTOCOL=4','HELPER_PROTOCOL=3'))
    child('''
from fastapi import FastAPI
from fastapi.testclient import TestClient
from autoposter.api import system
from autoposter.deps import require_admin
api=FastAPI();api.include_router(system.router,prefix='/api/v1')
api.dependency_overrides[require_admin]=lambda:object()
with TestClient(api) as client:
 status=client.get('/api/v1/system/character-upgrade').json()
 assert status['helper']['ready'] is False,status
 assert 'Administrator' in status['helper']['message']
 response=client.post('/api/v1/system/character-upgrade',json={'name':'Cherry','confirmed':True})
 assert response.status_code==409,response.text
assert not Path('data/character-upgrade.json').exists()
assert not Path('data/character-releases').exists()
''')


def test_child_crash_after_pointer_keeps_active_generation(installed,monkeypatch):
    repo,account,env,child=installed
    stage(child)
    source=repo/'app/character_upgrade.py'
    text=source.read_text()
    needle="_save(root / 'data/character-active.json', {'id': identity, 'name': request['name'], 'version': 1})"
    assert text.count(needle)==1
    source.write_text(text.replace(needle,needle+'; os._exit(23)'))
    simulate_stopped(monkeypatch,repo)
    with pytest.raises(RuntimeError,match='Stopped migration refused'):
        helper.stopped_migrate(repo,account.pw_name,'fixture.service',environment=env)
    pointer=(repo/'data/character-active.json').read_bytes()
    child('''
from app.character_upgrade import bootstrap
bootstrap(Path.cwd())
from app import db
from autoposter.workspaces import character_context
from tools.character_migrate import LEGACY_ID
with character_context(LEGACY_ID):
 db.set_setting('system_prompt','FRESH WRITE AFTER CRASH')
 assert db.get_setting('system_prompt')=='FRESH WRITE AFTER CRASH'
''')
    assert (repo/'data/character-active.json').read_bytes()==pointer


def test_missing_dependency_aborts_real_offline_prepare_before_stop(installed,monkeypatch):
    repo,account,env,child=installed
    child('''
from pathlib import Path
import subprocess
Path('requirements.txt').write_text('nonexistent-offline-readiness-fixture-package==0.0.0\\n')
for args in [['add','requirements.txt'],['commit','-qm','missing dependency fixture'],['tag','v9.0.2'],
 ['push','-q','origin','HEAD:refs/heads/main','--tags'],['checkout','--detach','-q','v9.0.0']]:
 result=subprocess.run(['git',*args],capture_output=True,text=True)
 assert result.returncode==0,result.stderr
''')
    for key,value in env.items():
        if key.startswith(('MP_','PIP_')) or key=='PYTHONPATH':monkeypatch.setenv(key,value)
    original=helper.command
    def boundary(args,**kwargs):
        if args[0]=='systemctl':pytest.fail('Missing dependency reached service stop')
        return original(args,**kwargs)
    monkeypatch.setattr(helper,'command',boundary)
    with pytest.raises(RuntimeError,match='No matching distribution|Could not find a version'):
        helper.supervise(repo,account.pw_name,'fixture.service')
    assert json.loads((repo/'data/update-status.json').read_text())['phase']=='failed'
    assert not (repo/'data/character-active.json').exists()
    assert (repo/'.venv').resolve()==Path(sys.prefix).resolve()
    child("import subprocess;assert subprocess.check_output(['git','describe','--tags','--exact-match'],text=True).strip()=='v9.0.0'")


def test_reserved_update_tree_is_not_a_runtime_database(installed,monkeypatch):
    repo,account,env,child=installed
    child('''
from pathlib import Path
import shutil
Path('data/releases').mkdir()
shutil.copy2('data/autoposter/autoposter.db','data/releases/unsafe.db')
from app.character_upgrade import stage
stage(Path.cwd(),name='Cherry',confirmed=True)
''')
    simulate_stopped(monkeypatch,repo)
    env['MP_AUTOPOSTER_DATABASE_URL']='sqlite+aiosqlite:///'+str(repo/'data/releases/unsafe.db')
    with pytest.raises(RuntimeError,match='Stopped migration refused'):
        helper.stopped_migrate(repo,account.pw_name,'fixture.service',environment=env)
    assert not (repo/'data/character-active.json').exists()


def test_restart_helper_uses_stopped_migration_before_real_startup(installed,monkeypatch):
    repo,account,env,child=installed
    stage(child)
    events=[]
    original=helper.command
    health={}
    def boundary(args,**kwargs):
        if args[0]=='systemctl':
            events.append(args[1])
            if args[1]=='show':return service_properties(repo) if '--value' not in args else ''
            if args[1]=='start':
                assert (repo/'data/character-active.json').exists()
                health.update(json.loads(child('''
from app.main import app
from fastapi.testclient import TestClient
import json
with TestClient(app) as client:print(json.dumps(client.get('/api/creatorpilot/health').json()))
''').splitlines()[-1]))
            return ''
        return original(args,**kwargs)
    import io
    monkeypatch.setattr(helper,'command',boundary)
    monkeypatch.setattr(helper.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(json.dumps(health).encode()))
    helper.restart(repo,account.pw_name,'synthetic-cherry.service',environment=env)
    assert events==['show','show','stop','show','show','start']
    assert json.loads((repo/'data/restart-status.json').read_text())['phase']=='complete'


def test_installed_helper_stopped_nonroot_gui_migration(installed,monkeypatch):
    repo, account, env, child=installed
    original={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in
              [repo/'data/bot.db',repo/'data/autoposter/autoposter.db',repo/'.env',repo/'data/autoposter.env',repo/'exports/history.md']}
    stage(child)
    calls=simulate_stopped(monkeypatch,repo)
    monkeypatch.setattr(helper,'HEALTH_PORT',8766)
    real_run=subprocess.run
    def observed(*args,**kwargs):
        result=real_run(*args,**kwargs)
        assert result.returncode==0,result.stderr
        return result
    monkeypatch.setattr(helper.subprocess,'run',observed)
    helper.stopped_migrate(repo,account.pw_name,'synthetic-cherry.service',environment=env)
    assert calls and any('show' in c for c in calls)
    assert json.loads((repo/'data/character-active.json').read_text())['name']=='Cherry'
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==v for p,v in original.items())
    child('''
from app.character_upgrade import bootstrap,status
from pathlib import Path
bootstrap(Path.cwd())
from app import db
from autoposter.workspaces import character_context
from tools.character_migrate import LEGACY_ID
with character_context(LEGACY_ID):
 assert db.get_setting('system_prompt')=='Cherry identity'
 assert db.get_setting('bot_running') is False
print('REAL_NONROOT_ACTIVE')
''')
    child('''
from deploy.creatorstudio_update import snapshot
from pathlib import Path
import json
root=Path.cwd(); target=root/'data/backups/active-proof'
snapshot(root,target)
pointer=json.loads((root/'data/character-active.json').read_text())
relative='data/character-releases/'+pointer['id']+'/active'
assert (target/relative/'autoposter.db').is_file(),'Active generation absent from helper backup'
assert (target/'data/character-active.json').read_bytes()==(root/'data/character-active.json').read_bytes()
assert (target/'exports/history.md').read_bytes()==(root/'exports/history.md').read_bytes()
assert (target/'data/autoposter.env').read_bytes()==(root/'data/autoposter.env').read_bytes()
import sqlite3
from contextlib import closing
with closing(sqlite3.connect(target/relative/'bot.db')) as copied, closing(sqlite3.connect(root/relative/'bot.db')) as original:
 assert list(copied.iterdump())==list(original.iterdump())
 assert copied.execute('PRAGMA integrity_check').fetchone()==('ok',)
''')
    health=json.loads(child('''
from app.main import app
from fastapi.testclient import TestClient
import json
with TestClient(app) as client:
 r=client.get('/api/creatorpilot/health'); assert r.status_code==200,r.text
 print(json.dumps(r.json()))
''').splitlines()[-1])
    import io
    monkeypatch.setattr(helper.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(json.dumps(health).encode()))
    assert helper.healthy(health['revision'],False,timeout=0.05),'Disabled characters must be healthy without auto-enabling chat'


def test_installed_updater_two_code_updates_and_failed_health_rollback(installed,monkeypatch):
    from deploy import repair_update as repair
    import importlib.machinery
    import importlib.util
    import io
    repo,account,env,child=installed
    system=repo.parent/'installed-root-helper';system.mkdir();system.chmod(0o755)
    for name in ('WRAPPER','SUPERVISOR','CHECKER','SUDOERS'):
        monkeypatch.setattr(repair,name,system/name.lower())
    monkeypatch.setattr(repair,'BACKUPS',system/'backups')
    real_run=repair.run
    def install_boundary(args,**kwargs):
        if args[0] in ('visudo','runuser'): return '' # sudo/service authorization boundary only
        return real_run(args,**kwargs)
    monkeypatch.setattr(repair,'run',install_boundary)
    repair.install(repair.Installation('synthetic-cherry.service',account.pw_name,repo,8766))
    assert repair.SUPERVISOR.stat().st_uid==0
    loader=importlib.machinery.SourceFileLoader('installed_fixture_helper',str(repair.SUPERVISOR))
    spec=importlib.util.spec_from_loader(loader.name,loader);actual=importlib.util.module_from_spec(spec);loader.exec_module(actual)
    for key,value in env.items():
        if key.startswith(('MP_','PIP_')) or key=='PYTHONPATH':monkeypatch.setenv(key,value)
    health={};events=[];reject=[False]
    real_command=actual.command
    def boundary(args,**kwargs):
        if args[0]=='systemctl':
            events.append(args[1])
            if args[1]=='show':return service_properties(repo) if '--value' not in args else ''
            if args[1]=='start':
                health.clear()
                health.update(json.loads(child('''
from app.main import app
from fastapi.testclient import TestClient
import json
with TestClient(app) as client:
 print(json.dumps(client.get('/api/creatorpilot/health').json()))
''').splitlines()[-1]))
            return ''
        return real_command(args,**kwargs)
    monkeypatch.setattr(actual,'command',boundary)
    monkeypatch.setattr(actual.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(json.dumps(
        {'ok':True} if a[0].endswith('/health') and '/creatorpilot/' not in a[0] else health).encode()))
    original_healthy=actual.healthy
    monkeypatch.setattr(actual,'healthy',lambda *a,**k:False if reject[0] else original_healthy(*a,**k))
    actual.supervise(repo,account.pw_name,'synthetic-cherry.service')
    assert json.loads((repo/'data/update-status.json').read_text())['phase']=='complete'
    assert not (repo/'data/character-active.json').exists(),'Plain GUI code update must not activate characters'
    stage(child)
    actual.restart(repo,account.pw_name,'synthetic-cherry.service',environment=env)
    pointer=(repo/'data/character-active.json').read_bytes()
    child('''
from pathlib import Path
from app.character_upgrade import bootstrap
bootstrap(Path.cwd())
from app import db
from autoposter.workspaces import character_context
from tools.character_migrate import LEGACY_ID
with character_context(LEGACY_ID):db.set_setting('system_prompt','Cherry AFTER ACTIVE WRITE')
import subprocess
Path('VERSION').write_text('v9.0.2\\n')
for args in [['add','VERSION'],['commit','-qm','subsequent code update'],['tag','v9.0.2'],['push','-q','origin','HEAD:refs/heads/main','--tags'],['checkout','--detach','-q','v9.0.1']]:
 result=subprocess.run(['git',*args],capture_output=True,text=True)
 assert result.returncode==0,(args,result.stderr)
''')
    child("import subprocess; s=subprocess.check_output(['git','status','--porcelain'],text=True);assert not s,s")
    actual.supervise(repo,account.pw_name,'synthetic-cherry.service')
    assert json.loads((repo/'data/update-status.json').read_text())['phase']=='complete'
    assert (repo/'data/character-active.json').read_bytes()==pointer
    child('''
from pathlib import Path
import subprocess
Path('VERSION').write_text('v9.0.3\\n')
for args in [['add','VERSION'],['commit','-qm','unhealthy release'],['tag','v9.0.3'],['push','-q','origin','HEAD:refs/heads/main','--tags'],['checkout','--detach','-q','v9.0.2']]:
 result=subprocess.run(['git',*args],capture_output=True,text=True)
 assert result.returncode==0,(args,result.stderr)
''')
    reject[0]=True
    with pytest.raises(RuntimeError,match='Funktionsprüfung'):
        actual.supervise(repo,account.pw_name,'synthetic-cherry.service')
    assert json.loads((repo/'data/update-status.json').read_text())['phase']=='rolled_back'
    assert (repo/'data/character-active.json').read_bytes()==pointer
    child('''
from pathlib import Path
import subprocess
assert subprocess.check_output(['git','describe','--tags','--exact-match'],text=True).strip()=='v9.0.2'
from app.character_upgrade import bootstrap
bootstrap(Path.cwd())
from app import db
from autoposter.workspaces import character_context
from tools.character_migrate import LEGACY_ID
with character_context(LEGACY_ID):assert db.get_setting('system_prompt')=='Cherry AFTER ACTIVE WRITE'
''')

