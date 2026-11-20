"""Only the systemctl read boundary is simulated; no stop is permitted on refusal."""
from pathlib import Path
import pytest
import shlex
from deploy import creatorstudio_update as helper

PROPERTIES = helper.SERVICE_PROPERTIES


def service_properties(repo, **changes):
    values={key:'' for key in PROPERTIES}
    values.update(User='nobody',Group='nogroup',WorkingDirectory=str(repo),DynamicUser='no',
                  ExecStart='{ path='+str(repo/'.venv/bin/python')+' ; argv[]='+shlex.quote(str(repo/'.venv/bin/python'))+' -m uvicorn app.main:app --port 8000 ; ignore_errors=no ; }',
                  ActiveState='inactive',SubState='dead',MainPID='0',ControlPID='0')
    values.update(changes)
    return '\n'.join(k+'='+v for k,v in values.items())


@pytest.mark.parametrize('field,value', [('Environment','MP_AUTOCHAT_DATA_DIR=/elsewhere'),
    ('EnvironmentFiles','/private/custom.env (ignore_errors=no)'),('PassEnvironment','MP_AUTOPOSTER_DATABASE_URL'),
    ('UnsetEnvironment','PATH'),('PAMName','custom'),('RootDirectory','/elsewhere'),
    ('ExecSearchPath','/mutable'),('DynamicUser','yes'),('Group','root'),
    ('ExecStart','{ path=/bin/sh ; argv[]=/bin/sh run.sh ; ignore_errors=no ; }')])
def test_restart_refuses_unsupported_service_before_stop_or_writes(tmp_path, monkeypatch, field, value):
    (tmp_path/'data').mkdir()
    request=tmp_path/'data/character-upgrade.json'
    request.write_text('{"phase":"pending_restart"}')
    __import__('os').chown(request,65534,65534)
    original=request.read_bytes()
    props=service_properties(tmp_path, **{field:value})
    calls=[]
    def boundary(args, **kwargs):
        calls.append(args)
        if args[:2]==['systemctl','show']:
            return '' if '--value' in args else props
        pytest.fail('Service was stopped or status mutated before environment validation: '+repr(args))
    monkeypatch.setattr(helper,'command',boundary)
    with pytest.raises(RuntimeError,match='[Ee]nvironment|[Ss]ervice|[Ss]tart|[Gg]roup'):
        helper.restart(tmp_path,'nobody','fixture.service')
    assert request.read_bytes()==original
    assert not (tmp_path/'data/restart-status.json').exists()
    assert all(c[:2]==['systemctl','show'] for c in calls)


def test_activation_captures_real_service_environment_before_stop(tmp_path, monkeypatch):
    import os,tempfile
    from tests.test_environment_parity import running
    with tempfile.TemporaryDirectory(prefix='env-parity-') as directory:
        root=Path(directory)
        with running(root) as (process, service_env, props):
            (root/'app').mkdir()
            (root/'app/__init__.py').write_text('')
            (root/'app/character_upgrade.py').write_text("print('a'*64)\n")
            request=root/'data/character-upgrade.json'
            request.write_text('{"phase":"pending_restart"}')
            os.chown(request,65534,65534)
            events=[]; received=[]
            def boundary(args, **kwargs):
                events.append(args[:2])
                if args[:2]==['systemctl','show']:
                    return '' if '--value' in args else props
                if args[0]=='runuser': return 'synthetic-revision'
                return ''
            monkeypatch.setattr(helper,'command',boundary)
            monkeypatch.setattr(helper,'stopped_migrate',lambda *a,**k:received.append(k['environment']))
            monkeypatch.setattr(helper,'healthy',lambda *a,**k:True)
            monkeypatch.setenv('PATH','/root-supervisor-WRONG')
            monkeypatch.setenv('ROOT_ONLY_SECRET','must-not-inherit')
            helper.restart(root,'nobody','fixture.service')
            assert received==[service_env]
            assert events.index(['systemctl','show']) < events.index(['systemctl','stop'])


def test_installed_readiness_entrypoint_is_readonly_and_secret_safe(tmp_path,monkeypatch,capsys):
    import sys
    monkeypatch.setattr(sys,'argv',['installed-root-helper','readiness',str(tmp_path),'nobody','fixture.service'])
    calls=[]
    def boundary(args,**kwargs):
        calls.append(args)
        if args[:2]==['systemctl','show']:
            return '' if '--value' in args else service_properties(tmp_path,Environment='SECRET=must-not-appear')
        pytest.fail('readiness attempted non-read action')
    monkeypatch.setattr(helper,'command',boundary)
    helper.main()
    import json
    result=json.loads(capsys.readouterr().out)
    assert result=={'ready':False,'reason':'service_contract_refused'}
    assert not (tmp_path/'data').exists()


def test_stopped_migration_never_falls_back_to_root_environment(tmp_path, monkeypatch):
    import os,pwd
    account=pwd.getpwnam('nobody')
    (tmp_path/'data').mkdir()
    os.chown(tmp_path,account.pw_uid,account.pw_gid);os.chown(tmp_path/'data',account.pw_uid,account.pw_gid)
    monkeypatch.setattr(helper,'command',lambda *a,**k:service_properties(tmp_path))
    calls=[]
    monkeypatch.setattr(helper.subprocess,'run',lambda *a,**k:calls.append(k) or type('Result',(),{'returncode':0})())
    with pytest.raises(RuntimeError,match='[Ee]nvironment'):
        helper.stopped_migrate(tmp_path,'nobody','fixture.service')
    assert not calls


@pytest.mark.parametrize('fault',['symlink','oversize','wrong-owner'])
def test_root_restart_refuses_untrusted_intent_before_service_commands(tmp_path,monkeypatch,fault):
    import os,pwd
    account=pwd.getpwnam('nobody')
    data=tmp_path/'data';data.mkdir()
    os.chown(tmp_path,account.pw_uid,account.pw_gid);os.chown(data,account.pw_uid,account.pw_gid)
    request=data/'character-upgrade.json'
    request.write_text('{"phase":"pending_restart"}')
    os.chown(request,account.pw_uid,account.pw_gid);request.chmod(0o600)
    if fault=='symlink':
        target=tmp_path/'other';request.rename(target);request.symlink_to(target)
    elif fault=='oversize':request.write_bytes(b' '*8193)
    else:os.chown(request,0,0)
    monkeypatch.setattr(helper,'command',lambda *a,**k:pytest.fail('untrusted root read reached service boundary'))
    with pytest.raises(RuntimeError,match='[Uu]nsafe.*request'):
        helper.restart(tmp_path,'nobody','fixture.service')


def test_stopped_migration_rechecks_environment_after_stop(tmp_path,monkeypatch):
    import os,pwd
    account=pwd.getpwnam('nobody')
    (tmp_path/'data').mkdir()
    os.chown(tmp_path,account.pw_uid,account.pw_gid);os.chown(tmp_path/'data',account.pw_uid,account.pw_gid)
    monkeypatch.setattr(helper,'command',lambda *a,**k:service_properties(tmp_path,EnvironmentFiles='/changed-after-check.env'))
    monkeypatch.setattr(helper.subprocess,'run',lambda *a,**k:pytest.fail('environment change reached migration child'))
    with pytest.raises(RuntimeError,match='[Ee]nvironment'):
        helper.stopped_migrate(tmp_path,'nobody','fixture.service',environment={})
