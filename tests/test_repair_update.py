import ast
import io
import os
from pathlib import Path
import pwd
import shlex
import subprocess
import sys

import pytest
from deploy import repair_update as repair
from deploy import creatorstudio_update as update


@pytest.fixture
def existing(tmp_path):
    root = tmp_path / 'Sabrinas Bot'
    (root / 'data').mkdir(parents=True)
    (root / 'app').mkdir()
    (root / '.venv/bin').mkdir(parents=True)
    (root / 'data/bot.db').write_bytes(b'untouched database')
    (root / '.env').write_text('PRIVATE=preserved\n')
    (root / 'app/main.py').touch()
    (root / '.venv/bin/python').symlink_to(sys.executable)
    user = pwd.getpwuid(os.getuid()).pw_name
    return repair.Installation('sabrinas-bot.service', user, root, 8766)


def info(existing, **changes):
    return {'Id': existing.service, 'LoadState': 'loaded', 'User': existing.user,
            'WorkingDirectory': str(existing.repo), 'MainPID': '0', 'DynamicUser': 'no',
            'NoNewPrivileges': 'no', 'Environment': '',
            'ExecStart': '{ path=' + sys.executable + ' ; argv[]=' + sys.executable
            + ' -m uvicorn app.main:app --host 0.0.0.0 --port 8766 ; ignore_errors=no ; }', **changes}


def test_discovers_custom_user_path_and_port(existing, monkeypatch):
    properties = '\n'.join(f'{k}={v}' for k, v in info(existing).items())
    output = 'Id=unrelated.service\nLoadState=loaded\nExecStart=/bin/true\n\n' + properties
    calls = []
    def execute(args, **kwargs):
        calls.append(args)
        return 'unrelated.service enabled\nsabrinas-bot.service enabled\ngetty@.service static' if any(a.startswith('list-unit') for a in args) else output
    monkeypatch.setattr(repair, 'run', execute)
    assert repair.discover() == existing
    assert all('start' not in args and 'restart' not in args for args in calls)
    assert all('getty@.service' not in args for args in calls if 'show' in args)


def test_ambiguous_installations_are_not_guessed(existing, monkeypatch):
    blocks = ['\n'.join(f'{k}={v}' for k, v in info(existing, Id=name).items())
              for name in ('one.service', 'two.service')]
    monkeypatch.setattr(repair, 'run', lambda args, **kw: 'one.service enabled\ntwo.service enabled'
                        if any(a.startswith('list-unit') for a in args) else '\n\n'.join(blocks))
    with pytest.raises(RuntimeError, match='Mehrere Installationen'):
        repair.discover()


@pytest.mark.parametrize('changes', [{'NoNewPrivileges': 'yes'}, {'DynamicUser': 'yes'}, {'User': 'root'}])
def test_incompatible_service_is_rejected_before_changes(existing, changes):
    with pytest.raises(RuntimeError):
        repair.inspect_installation(info(existing, **changes))


def test_rendering_preserves_spaces_and_uses_detected_port(existing):
    files = repair.rendered_files(existing)
    wrapper = files[repair.WRAPPER][0]
    assert 'REPO=' + shlex.quote(str(existing.repo)) in wrapper
    assert 'SVC_USER=' + existing.user in wrapper
    assert 'SERVICE=' + existing.service in wrapper
    subprocess.run(['bash', '-n'], input=wrapper, text=True, check=True)
    namespace = {}
    exec(compile(ast.parse(files[repair.SUPERVISOR][0]), '<supervisor>', 'exec'), namespace)
    assert namespace['HEALTH_PORT'] == 8766
    rule = files[repair.SUDOERS][0]
    assert rule.startswith(existing.user + ' ALL=(root) NOPASSWD:')
    assert rule.count('/usr/local/bin/fanvue-admin ') == 3


@pytest.fixture
def destinations(tmp_path, monkeypatch):
    paths = {}
    for name in ('WRAPPER', 'SUPERVISOR', 'CHECKER', 'SUDOERS'):
        path = tmp_path / 'system' / name.lower()
        path.parent.mkdir(exist_ok=True)
        monkeypatch.setattr(repair, name, path)
        paths[name] = path
    monkeypatch.setattr(repair, 'BACKUPS', tmp_path / 'backups')
    return paths


def test_installation_is_repeatable_and_does_not_touch_application(existing, destinations, monkeypatch):
    calls = []
    monkeypatch.setattr(repair, 'run', lambda args, **kw: calls.append(args) or '')
    original = {p: p.read_bytes() for p in [existing.repo / '.env', existing.repo / 'data/bot.db']}
    first = repair.install(existing)
    second = repair.install(existing)
    assert first != second
    assert all(p.read_bytes() == value for p, value in original.items())
    assert destinations['SUDOERS'].stat().st_mode & 0o777 == 0o440
    assert destinations['WRAPPER'].stat().st_mode & 0o777 == 0o755
    assert all(args[0] != 'systemctl' for args in calls)
    assert any(args[0] == 'runuser' and str(repair.CHECKER) == str(args[-1]) for args in calls)


@pytest.mark.parametrize('failure', ['permissions', 'syntax'])
def test_failed_install_restores_previous_helpers_and_rule(existing, destinations, monkeypatch, failure):
    previous = {}
    for name in ('WRAPPER', 'SUPERVISOR', 'SUDOERS'):
        path = destinations[name]
        path.write_text('original ' + name)
        path.chmod(0o440 if name == 'SUDOERS' else 0o755)
        previous[path] = (path.read_bytes(), path.stat().st_mode)
    def execute(args, **kw):
        if (failure == 'permissions' and args[0] == 'runuser') or (failure == 'syntax' and '-f' in args):
            raise RuntimeError('simulated failure')
        return ''
    monkeypatch.setattr(repair, 'run', execute)
    with pytest.raises(RuntimeError, match='simulated failure'):
        repair.install(existing)
    for path, (content, mode) in previous.items():
        assert path.read_bytes() == content
        assert path.stat().st_mode == mode
    assert not destinations['CHECKER'].exists()
    if failure == 'permissions':
        diagnostic = next(repair.BACKUPS.glob('fix-update-*/diagnose.txt')).read_text()
        assert 'Passwortlose Berechtigungen' in diagnostic
        assert 'simulated failure' in diagnostic
        assert 'wiederhergestellt' in diagnostic


def test_run_hides_service_details_but_exposes_controlled_checker_errors(monkeypatch):
    def fail(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout='Environment=PRIVATE_SECRET',
                                           stderr='Passwortlose Freigabe nicht bestätigt: update')
    monkeypatch.setattr(repair.subprocess, 'run', fail)
    with pytest.raises(RuntimeError) as error:
        repair.run(['systemctl', 'show'])
    assert 'PRIVATE_SECRET' not in str(error.value)
    assert 'nicht bestätigt' not in str(error.value)
    with pytest.raises(RuntimeError, match='nicht bestätigt: update'):
        repair.run(['runuser', 'checker'], include_error=True)


def test_failed_rollback_reports_unrestored_file_and_keeps_original_error(existing, destinations, monkeypatch):
    for path in destinations.values():
        path.write_text('original')
    original_write = repair.atomic_write
    def write(path, content, mode):
        if path == destinations['WRAPPER'] and content == 'original':
            raise OSError('simulated restore failure')
        original_write(path, content, mode)
    def execute(args, **kw):
        if args[0] == 'runuser':
            raise RuntimeError('simulated check failure')
        return ''
    monkeypatch.setattr(repair, 'atomic_write', write)
    monkeypatch.setattr(repair, 'run', execute)
    with pytest.raises(RuntimeError, match='simulated check failure') as error:
        repair.install(existing)
    assert 'Wiederherstellung unvollständig: ' + str(destinations['WRAPPER']) in str(error.value)
    assert all(destinations[name].read_text() == 'original' for name in ('SUPERVISOR', 'CHECKER', 'SUDOERS'))


def test_health_check_uses_discovered_port(monkeypatch):
    urls = []
    monkeypatch.setattr(update, 'HEALTH_PORT', 8766)
    def response(url, **kwargs):
        urls.append(url)
        return io.BytesIO(b'{"application":"MP CreatorStudio","revision":"new","preview":false,"deployment_pending":false,"autochat_worker":true}')
    monkeypatch.setattr(update.urllib.request, 'urlopen', response)
    assert update.healthy('new', False, timeout=1)
    assert urls == ['http://127.0.0.1:8766/api/creatorpilot/health']


@pytest.mark.parametrize('failure', ['', 'repair_update.py'])
def test_bootstrap_downloads_pinned_files_before_running_installer(tmp_path, failure):
    binary = tmp_path / 'bin'
    binary.mkdir()
    log = tmp_path / 'installed'
    (binary / 'id').write_text('#!/bin/sh\nprintf "0\\n"\n')
    curl = binary / 'curl'
    stub = binary / 'fake_curl.py'
    curl.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(stub)) + ' "$@"\n')
    stub.write_text('''
import os,pathlib,sys
args=sys.argv[1:]
url=next(a for a in args if a.startswith('https://'))
assert url.startswith('https://raw.githubusercontent.com/MatzePee/Chatbot/v2.0.2/deploy/')
name=url.rsplit('/',1)[1]
if name==os.environ.get('TEST_FAIL'): sys.exit(22)
out=pathlib.Path(args[args.index('-o')+1])
if name=='install-update-helper.sh':
 out.write_text('#!/bin/sh\\nprintf "%s" "$*" > "$TEST_LOG"\\n')
else: out.write_text('# downloaded fixture\\n')
''')
    for path in binary.iterdir(): path.chmod(0o755)
    bootstrap = (Path(__file__).resolve().parents[1] / 'deploy/fix-update.sh').read_text()
    result = subprocess.run(['bash', '-s', '--', '--check'], input=bootstrap, text=True, capture_output=True,
                            env={**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                                 'TEST_LOG': str(log), 'TEST_FAIL': failure})
    assert result.returncode == (22 if failure else 0), result.stderr
    if failure:
        assert not log.exists()
    else:
        assert log.read_text() == '--check'
