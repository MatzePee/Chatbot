"""Real nonroot /proc fixture; only systemctl transport is synthetic."""
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
import time

import pytest
from deploy import creatorstudio_update as helper
from tests.test_helper_environment import service_properties
from tests.test_helper_acceptance import installed


@contextlib.contextmanager
def running(repo, *, environment=None, groups=None, arguments=None, cwd=None):
    account = pwd.getpwnam('nobody')
    repo = Path(repo)
    repo.mkdir(exist_ok=True)
    repo.chmod(0o755)
    if not (repo/'.venv/bin/python').exists():
        (repo/'.venv/bin').mkdir(parents=True, exist_ok=True)
        (repo/'.venv/bin/python').symlink_to(sys.executable)
    # Synthetic uvicorn entrypoint holds a real process with the exact standard
    # argv/executable/cwd. Real application startup is tested separately.
    module = repo/'uvicorn.py'
    module.write_text('import time\ntime.sleep(90)\n')
    (repo/'.env').write_text('SECRET_KEY="synthetic $ literal"\nEMPTY=\nNULL\n')
    (repo/'data').mkdir(exist_ok=True)
    for p in (repo, repo/'data', repo/'.env'):
        os.chown(p, account.pw_uid, account.pw_gid)
    env = {'PATH':'/usr/bin:/bin', 'HOME':str(repo), 'SECRET_KEY':'synthetic $ literal',
           'EMPTY':'', 'OVERRIDE':'environment-file-wins'}
    if environment is not None: env.update(environment)
    args = [str(repo/'.venv/bin/python'), '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000']
    if arguments is not None: args = arguments
    unit=repo/'fixture.service'
    unit.write_text((Path(__file__).parents[1]/'deploy/fanvue-chatbot.service').read_text().replace(
        '/srv/fanvue/Fanvue_Chatbot',str(repo)).replace('User=matze','User=nobody').replace('Group=matze','Group=nogroup'))
    time.sleep(.03)  # configuration must unambiguously predate the exec tick
    process = subprocess.Popen(args, cwd=cwd or repo, env=env,
        user=account.pw_uid, group=account.pw_gid,
        extra_groups=os.getgrouplist(account.pw_name, account.pw_gid) if groups is None else groups)
    try:
        # Wait for exec, not a blind assumption about Popen's initial state.
        for _ in range(100):
            if (Path('/proc')/str(process.pid)/'cmdline').read_bytes().startswith(os.fsencode(args[0])+b'\0'):
                break
            time.sleep(.01)
        props = service_properties(repo, MainPID=str(process.pid),ActiveState='active',SubState='running',
            EnvironmentFiles=str(repo/'.env')+' (ignore_errors=no)',
            Environment='OVERRIDE=unit-value', FragmentPath=str(unit), NeedDaemonReload='no',
            ExecStart='{ path='+str(repo/'.venv/bin/python')+' ; argv[]='+__import__('shlex').join(args)+' ; ignore_errors=no ; }')
        yield process, env, props
    finally:
        process.terminate(); process.wait(timeout=10)


def test_standard_distributed_environmentfile_captures_resolved_values(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix='parity-') as d:
        repo=Path(d)
        with running(repo) as (process, env, props):
            monkeypatch.setattr(helper,'command',lambda args,**kw: '' if '--value' in args else props)
            contract=helper.service_contract(repo,'nobody','fixture.service')
            captured=helper.running_environment(contract,'nobody')
            assert dict(captured)==env
            assert captured['OVERRIDE']=='environment-file-wins'
            assert captured['EMPTY']=='' and 'NULL' not in captured
            assert 'ROOT_ONLY_SECRET' not in captured
            assert captured.groups==tuple(sorted(set(os.getgrouplist('nobody',65534))))
            assert 'EnvironmentFile=' in (Path(__file__).parents[1]/'deploy/fanvue-chatbot.service').read_text()


@pytest.mark.parametrize('fault', ['file-edited', 'autopost-config-edited', 'pid-changed', 'unit-edited'])
def test_runtime_changes_refuse_before_stop_or_status_writes(monkeypatch, fault):
    with tempfile.TemporaryDirectory(prefix='parity-') as d:
        repo=Path(d)
        with running(repo) as (process, env, props):
            request=repo/'data/character-upgrade.json'
            request.write_text('{"phase":"pending_restart"}')
            os.chown(request,65534,65534)
            before=request.read_bytes()
            if fault=='file-edited': (repo/'.env').write_text('SECRET_KEY=changed\n')
            if fault=='unit-edited':
                (repo/'fixture.service').write_text('[Service]\nEnvironment=SECRET_KEY=changed\n')
            if fault=='autopost-config-edited': (repo/'data/autoposter.env').write_text('MP_AUTOPOSTER_DATABASE_URL=sqlite+aiosqlite:////wrong.db\n')
            reads=[]
            def boundary(args, **kwargs):
                if args[:2]==['systemctl','show']:
                    if '--value' in args:return ''
                    reads.append(args)
                    return props if len(reads)==1 or fault!='pid-changed' else props.replace('MainPID='+str(process.pid),'MainPID=1')
                pytest.fail('Invalid configuration reached a write/stop')
            monkeypatch.setattr(helper,'command',boundary)
            if fault=='unit-edited':
                with pytest.raises(RuntimeError, match='configuration changed'):
                    helper.running_environment(helper.service_contract(repo,'nobody','fixture.service'),'nobody')
            with pytest.raises(RuntimeError): helper.restart(repo,'nobody','fixture.service')
            assert request.read_bytes()==before
            assert not (repo/'data/restart-status.json').exists()




@pytest.mark.parametrize('field,value', [('BindPaths','/other:/srv'), ('ProtectHome','yes'),
    ('PrivateUsers','yes'), ('LoadCredential','key:/private/key'),
    ('ExecStart','{ path=/bin/sh ; argv[]=/bin/sh -c unsafe ; ignore_errors=no ; }')])
def test_unsupported_namespace_or_start_never_reaches_stop(monkeypatch, field, value):
    with tempfile.TemporaryDirectory(prefix='parity-') as d:
        repo=Path(d)
        with running(repo) as (process,env,props):
            request=repo/'data/character-upgrade.json'
            request.write_text('{"phase":"pending_restart"}')
            os.chown(request,65534,65534)
            props += '\n'+field+'='+value
            def boundary(args,**kw):
                if args[:2]==['systemctl','show']:return '' if '--value' in args else props
                pytest.fail('Unsupported namespace reached status/stop')
            monkeypatch.setattr(helper,'command',boundary)
            with pytest.raises(RuntimeError, match='Service'):
                helper.service_contract(repo,'nobody','fixture.service')
            with pytest.raises(RuntimeError): helper.restart(repo,'nobody','fixture.service')
            assert not (repo/'data/restart-status.json').exists()



@pytest.mark.parametrize('path_override', ['MP_AUTOCHAT_DATA_DIR', 'MP_AUTOPOSTER_DATABASE_URL', 'MP_EXPORT_DIR'])
def test_effective_path_mismatch_refuses_before_stop(installed, monkeypatch, path_override):
    repo, account, env, child = installed
    # Existing intent was confirmed separately; restart must preflight actual
    # config under the actual service UID/env before touching status or stopping.
    child("from app.character_upgrade import stage; stage(Path.cwd(),name='Cherry',confirmed=True)")
    env[path_override] = ('sqlite+aiosqlite:////wrong.db' if path_override.endswith('URL') else '/wrong')
    with running(repo, environment=env) as (process, service_env, props):
        before=(repo/'data/character-upgrade.json').read_bytes()
        def boundary(args,**kw):
            if args[:2]==['systemctl','show']:return '' if '--value' in args else props
            pytest.fail('Unsupported effective paths reached status/stop')
        monkeypatch.setattr(helper,'command',boundary)
        with pytest.raises(RuntimeError):helper.restart(repo,'nobody','fixture.service')
        assert (repo/'data/character-upgrade.json').read_bytes()==before
        assert not (repo/'data/restart-status.json').exists()
        assert not (repo/'data/character-releases').exists()


def test_standard_environmentfile_real_stopped_copy_and_startup(installed, monkeypatch):
    import io
    repo, account, env, child = installed
    # pydantic's actual fallback file and environment precedence, not a parser
    # reimplementation. All keys here are synthetic, never provider credentials.
    (repo/'data/autoposter.env').write_text('MP_AUTOPOSTER_OPENROUTER_API_KEY=synthetic-file-secret\nMP_AUTOPOSTER_X_CLIENT_SECRET=file-loses\n')
    env['MP_AUTOPOSTER_X_CLIENT_SECRET']='environment-wins'
    child("from app.character_upgrade import stage; stage(Path.cwd(),name='Cherry',confirmed=True)")
    # Assert inside the REAL migration child, not a mocked subprocess.run.
    source=repo/'app/character_upgrade.py'
    source.write_text(source.read_text().replace('    import shutil\n',
        "    assert os.getuid()==65534 and os.getgid()==65534\n"
        "    assert set(os.getgroups())==set(os.getgrouplist('nobody',65534))\n"
        "    assert os.environ['SECRET_KEY']=='synthetic $ literal'\n"
        "    assert os.environ['EMPTY']=='' and 'NULL' not in os.environ\n"
        "    assert 'ROOT_ONLY_SECRET' not in os.environ\n"
        "    from autoposter.config import settings\n"
        "    assert settings.openrouter_api_key=='synthetic-file-secret'\n"
        "    assert settings.x_client_secret=='environment-wins'\n    import shutil\n"))
    monkeypatch.setenv('ROOT_ONLY_SECRET','not-in-child')
    with running(repo, environment=env) as (process, service_env, props):
        originals={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in
            (repo/'data/bot.db',repo/'data/autoposter/autoposter.db',repo/'.env',repo/'data/autoposter.env',repo/'exports/history.md')}
        events=[]; health={}; stopped=[False]
        original=helper.command
        def boundary(args, **kwargs):
            if args[0]=='systemctl':
                if args[1]=='show':
                    if '--value' in args:return ''
                    return props if not stopped[0] else props.replace('MainPID='+str(process.pid),'MainPID=0').replace('ActiveState=active','ActiveState=inactive').replace('SubState=running','SubState=dead')
                events.append(args[1])
                if args[1]=='stop':
                    process.terminate();process.wait(timeout=10);stopped[0]=True
                elif args[1]=='start':
                    assert (repo/'data/character-active.json').exists()
                    health.update(json.loads(child('''
from app.main import app
from fastapi.testclient import TestClient
import json
with TestClient(app) as client:print(json.dumps(client.get('/api/creatorpilot/health').json()))
''').splitlines()[-1]))
                return ''
            return original(args,**kwargs)
        monkeypatch.setattr(helper,'command',boundary)
        monkeypatch.setattr(helper.urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(json.dumps(health).encode()))
        helper.restart(repo,'nobody','fixture.service')
        assert events==['stop','start']
        assert json.loads((repo/'data/restart-status.json').read_text())['phase']=='complete'
        assert all(hashlib.sha256(p.read_bytes()).hexdigest()==digest for p,digest in originals.items())
        child("from app.character_upgrade import bootstrap; bootstrap(Path.cwd()); from app import db; from autoposter.workspaces import character_context; from tools.character_migrate import LEGACY_ID;\nwith character_context(LEGACY_ID): assert db.get_setting('system_prompt')=='Cherry identity'")


@pytest.mark.parametrize('fault', ['groups', 'argv', 'cwd', 'invalid-pid', 'dead-pid'])
def test_actual_process_identity_refuses_misleading_unit_metadata(monkeypatch, fault):
    with tempfile.TemporaryDirectory(prefix='parity-') as d:
        repo=Path(d)
        with running(repo, groups=[] if fault=='groups' else None) as (process,env,text):
            props=dict(line.split('=',1) for line in text.splitlines())
            if fault=='argv':props['ExecStart']=props['ExecStart'].replace('--port 8000','--port 8001')
            if fault=='cwd':props['WorkingDirectory']='/tmp'
            if fault=='invalid-pid':props['MainPID']='not-a-pid'
            if fault=='dead-pid':process.terminate();process.wait(timeout=10)
            with pytest.raises(RuntimeError):helper.running_environment(props,'nobody')
            assert not (repo/'data/character-releases').exists()


@pytest.mark.parametrize('fault',['file', 'unit-environment', 'pid'])
def test_post_stop_change_refuses_before_copy(installed,monkeypatch,fault):
    repo,account,env,child=installed
    child("from app.character_upgrade import stage; stage(Path.cwd(),name='Cherry',confirmed=True)")
    with running(repo,environment=env) as (process,service_env,text):
        monkeypatch.setattr(helper,'command',lambda args,**kw:'' if '--value' in args else text)
        captured=helper.running_environment(helper.service_contract(repo,'nobody','fixture.service'),'nobody')
        captured.configuration=helper.configuration_probe(repo,'nobody',captured)
        process.terminate();process.wait(timeout=10)
        stopped=text.replace('MainPID='+str(process.pid),'MainPID=0').replace('ActiveState=active','ActiveState=inactive').replace('SubState=running','SubState=dead')
        if fault=='file':(repo/'.env').write_text('SECRET_KEY=changed-after-stop\n')
        if fault=='unit-environment':stopped=stopped.replace('Environment=OVERRIDE=unit-value','Environment=SECRET_KEY=changed-after-stop')
        if fault=='pid':stopped=stopped.replace('MainPID=0','MainPID=1')
        monkeypatch.setattr(helper,'command',lambda args,**kw:'' if '--value' in args else stopped)
        before=(repo/'data/character-upgrade.json').read_bytes()
        with pytest.raises(RuntimeError):helper.stopped_migrate(repo,'nobody','fixture.service',environment=captured)
        assert not (repo/'data/character-releases').exists()
        assert not (repo/'data/character-active.json').exists()
        assert (repo/'data/character-upgrade.json').read_bytes()==before


