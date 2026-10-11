import re
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app import main, publisher, updater
from autoposter.bootstrap import ensure_config
from deploy import creatorstudio_update as deploy
from deploy import admin_permissions


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL, text=True).strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Release Test')
    git(repo, 'config', 'user.email', 'test@example.test')
    (repo / '.gitignore').write_text('.env\ndata/\n.venv/\n')
    (repo / 'program.txt').write_text('old')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'old')
    git(repo, 'tag', 'v1.0.0')
    bare = tmp_path / 'remote.git'
    bare.mkdir()
    git(bare, 'init', '--bare')
    git(repo, 'remote', 'add', 'origin', str(bare))
    git(repo, 'push', '-u', 'origin', 'main', '--tags')
    monkeypatch.setattr(publisher, 'REPO_DIR', str(repo))
    monkeypatch.setattr(updater, 'REPO_DIR', str(repo))
    monkeypatch.setattr(publisher, 'valid_github_remote', lambda url: url == str(bare))
    return repo, bare


def test_partial_publication_is_reported_and_retry_publishes_tag(repository, monkeypatch):
    repo, remote = repository
    (repo / 'program.txt').write_text('new')
    original = publisher._git
    def fail_tag(*args, **kwargs):
        if args == ('push', 'origin', 'v1.1.0'):
            return False, 'simulated connection failure'
        return original(*args, **kwargs)
    monkeypatch.setattr(publisher, '_git', fail_tag)
    result = publisher.publish('new', 'v1.1.0')
    assert result['ok'] is False
    assert 'Versions-Tag' in result['error']
    assert git(remote, 'rev-parse', 'refs/heads/main') == git(repo, 'rev-parse', 'HEAD')
    assert 'v1.1.0' not in git(remote, 'tag', '-l')
    monkeypatch.setattr(publisher, '_git', original)
    assert publisher.publish('retry', 'v1.1.0')['ok'] is True
    assert git(remote, 'rev-parse', 'v1.1.0^{commit}') == git(repo, 'rev-parse', 'HEAD')


def test_existing_tag_cannot_publish_the_wrong_commit(repository):
    repo, remote = repository
    (repo / 'program.txt').write_text('new')
    result = publisher.publish('new', 'v1.0.0')
    assert not result['ok']
    assert git(repo, 'rev-parse', 'v1.0.0') != git(repo, 'rev-parse', 'HEAD')
    assert git(remote, 'rev-parse', 'v1.0.0') == git(repo, 'rev-parse', 'v1.0.0')


def test_tracked_private_files_are_rejected_even_without_changes(repository):
    repo, _ = repository
    (repo / '.env').write_text('TEST=value\n')
    git(repo, 'add', '-f', '.env')
    git(repo, 'commit', '-m', 'test-only private file')
    assert publisher.guard()
    assert not publisher.publish('blocked', 'v1.1.0')['ok']


def test_preview_allows_only_explicit_csrf_protected_release(monkeypatch):
    monkeypatch.setenv('MP_PREVIEW', '1')
    monkeypatch.setattr(main.fanvue, 'is_connected', lambda: False)
    sent = []
    monkeypatch.setattr(publisher, 'publish', lambda *a, **kw: sent.append(kw) or {'ok': True, 'log': ['test']})
    client = TestClient(main.app)
    assert client.post('/upload/publish', data={'tag': 'v2.0.0'}).status_code == 403
    page = client.get('/upload')
    token = re.search(r'name="upload_csrf" value="([^"]+)"', page.text)[1]
    assert client.post('/upload/publish', data={'upload_csrf': token, 'tag': 'v2.0.0'}, follow_redirects=False).status_code == 303
    assert sent == [{'tag': 'v2.0.0', 'do_push': True}]
    for path in ['/toggle-running', '/queue/1/approve', '/system/update', '/oauth/disconnect']:
        assert client.post(path).status_code == 403


def test_new_install_keys_are_private_and_never_replaced(tmp_path):
    ensure_config(tmp_path)
    config = tmp_path / 'data/autoposter.env'
    content = config.read_bytes()
    assert config.stat().st_mode & 0o777 == 0o600
    assert b'MP_AUTOPOSTER_ENCRYPTION_KEY=' in content
    ensure_config(tmp_path)
    assert config.read_bytes() == content


def test_code_environment_rollback_preserves_newer_messages_and_tokens(repository):
    repo, _ = repository
    old_revision = git(repo, 'rev-parse', 'HEAD')
    (repo / 'program.txt').write_text('new')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'new')
    new_revision = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '--detach', old_revision)
    (repo / '.env').write_text('KEEP=original\n')
    old_env = repo / '.venv'
    old_env.mkdir()
    (old_env / 'identity').write_text('old environment')
    release = repo / 'data/releases/test'
    new_env = release / 'venv'
    new_env.mkdir(parents=True)
    (new_env / 'identity').write_text('new environment')
    with sqlite3.connect(repo / 'data/bot.db') as conn:
        conn.execute('CREATE TABLE tokens (value TEXT)')
        conn.execute("INSERT INTO tokens VALUES ('old')")
    deploy.save(repo / 'data/update-plan.json', {'old_revision': old_revision, 'revision': new_revision,
                'release': str(release), 'environment': str(new_env), 'old_environment': ''})
    deploy.activate(repo)
    assert (repo / 'program.txt').read_text() == 'new'
    assert (repo / '.venv/identity').read_text() == 'new environment'
    with sqlite3.connect(repo / 'data/bot.db') as conn:
        conn.execute("UPDATE tokens SET value='newly refreshed'")
    deploy.rollback(repo)
    assert (repo / 'program.txt').read_text() == 'old'
    assert (repo / '.venv/identity').read_text() == 'old environment'
    assert not (repo / '.venv').is_symlink()
    assert not git(repo, 'status', '--porcelain')
    assert (repo / '.env').read_text() == 'KEEP=original\n'
    with sqlite3.connect(repo / 'data/bot.db') as conn:
        assert conn.execute('SELECT value FROM tokens').fetchone()[0] == 'newly refreshed'
    assert list((repo / 'data/backups').glob('before-update-*/data/bot.db'))


def test_prepare_failure_never_stops_the_running_service(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy.os, 'geteuid', lambda: 0)
    actions = []
    def execute(args, **kwargs):
        actions.append([str(a) for a in args])
        if 'prepare' in args:
            raise RuntimeError('simulated dependency failure')
        return ''
    monkeypatch.setattr(deploy, 'command', execute)
    with pytest.raises(RuntimeError, match='dependency failure'):
        deploy.supervise(tmp_path, 'test-user', 'test-service')
    assert not any('systemctl' in a for a in actions)


def test_release_selection_is_numeric_and_stable_only():
    assert deploy.newest_tag(['v1.9.0', 'v1.10.0', 'v2.0.0-rc1']) == 'v1.10.0'
    assert not publisher.valid_github_remote('https://github.com.evil.test/user/repo')
    assert not publisher.valid_github_remote('https://token@github.com/user/repo')
    assert publisher.valid_github_remote('https://github.com/owner/repo.git')


def test_admin_check_rejects_password_required_rule_even_when_listing_succeeds(monkeypatch):
    def listing(args, **kwargs):
        output = '/usr/local/bin/fanvue-admin ' + args[-1] + '\n'
        if args[-1] == '-ll':
            output = ''.join('Sudoers entry:\n    RunAsUsers: root\n    Options: '
                             + ('authenticate' if action == 'update' else '!authenticate')
                             + '\n    Commands:\n        /usr/local/bin/fanvue-admin ' + action + '\n\n'
                             for action in admin_permissions.ACTIONS)
        return subprocess.CompletedProcess(args, 0, stdout=output)
    monkeypatch.setattr(admin_permissions.subprocess, 'run', listing)
    assert admin_permissions.check() == ['update']


def test_admin_check_accepts_all_passwordless_actions_without_running_them(monkeypatch):
    calls = []
    def listing(args, **kwargs):
        calls.append(args)
        output = '/usr/local/bin/fanvue-admin ' + args[-1] + '\n'
        if args[-1] == '-ll':
            output = 'Sudoers entry:\n    RunAsUsers: root\n    Options: !authenticate\n    Commands:\n' + ''.join(
                '        /usr/local/bin/fanvue-admin ' + action + '\n' for action in admin_permissions.ACTIONS)
        return subprocess.CompletedProcess(args, 0, stdout=output)
    monkeypatch.setattr(admin_permissions.subprocess, 'run', listing)
    assert admin_permissions.check() == []
    assert calls[0] == ['sudo', '-n', '-ll']
    assert [args[-1] for args in calls[1:]] == list(admin_permissions.ACTIONS)
    assert all(args[:5] == ['sudo', '-n', '-l', '-u', 'root'] for args in calls[1:])


@pytest.mark.parametrize('checks', [[False], [True, False], [True, True]])
def test_supervisor_restores_service_after_health_failure(tmp_path, monkeypatch, checks):
    import io
    monkeypatch.setattr(deploy.os, 'geteuid', lambda: 0)
    actions = []
    def execute(args, **kwargs):
        action = args[6] if args[0] == 'runuser' else ''
        actions.append(action or tuple(args))
        if action == 'prepare':
            deploy.save(tmp_path / 'data/update-plan.json', {'revision': 'new', 'old_revision': 'old'})
            deploy.state(tmp_path, 'prepared', 'ready')
        elif action == 'status':
            deploy.state(tmp_path, *args[8:])
        return ''
    monkeypatch.setattr(deploy, 'command', execute)
    results = iter(checks)
    monkeypatch.setattr(deploy, 'healthy', lambda *a: next(results))
    monkeypatch.setattr(deploy.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(b'{"ok":true}'))
    if all(checks):
        deploy.supervise(tmp_path, 'test-user', 'test-service')
        assert 'rollback' not in actions
        assert deploy.json.loads((tmp_path / 'data/update-status.json').read_text())['phase'] == 'complete'
    else:
        with pytest.raises(RuntimeError):
            deploy.supervise(tmp_path, 'test-user', 'test-service')
        assert 'rollback' in actions
        assert ('systemctl', 'start', 'test-service') in actions
        assert deploy.json.loads((tmp_path / 'data/update-status.json').read_text())['phase'] == 'rolled_back'


def test_maintenance_start_holds_workers_and_mutations(tmp_path):
    import os
    import sys
    code = '''
import os
from pathlib import Path
from app import db, deployment
root=Path(os.environ['TEST_ROOT'])
db.DATA_DIR=str(root/'data'); db.DB_PATH=str(root/'data/bot.db'); db._conn=None
db.init_db(); db.set_setting('bot_running', True)
deployment.pending=lambda: True
from app import poller
poller.start=lambda: (_ for _ in ()).throw(AssertionError('worker started during maintenance'))
from app.main import app
from fastapi.testclient import TestClient
with TestClient(app) as client:
    health=client.get('/api/creatorpilot/health').json()
    assert health['deployment_pending'] and not health['autochat_worker']
    assert not client.get('/health').json()['running']
    assert db.get_setting('bot_running') is True
    assert client.post('/test').status_code==503
    assert client.get('/oauth/start').status_code==503
    from autoposter.config import settings
    assert settings.scheduler_mode=='off'
    assert settings.global_pause and settings.dry_run
'''
    env = {**os.environ, 'MP_PREVIEW': '0', 'TEST_ROOT': str(tmp_path),
           'MP_AUTOPOSTER_DATABASE_URL': 'sqlite+aiosqlite:///' + str(tmp_path / 'studio.db'),
           'MP_AUTOPOSTER_DATA_DIR': str(tmp_path / 'studio'),
           'MP_AUTOPOSTER_MEDIA_ROOT': str(tmp_path / 'media')}
    subprocess.run([sys.executable, '-c', code], env=env, cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True)


def test_failed_rollback_is_reported_as_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy.os, 'geteuid', lambda: 0)
    def execute(args, **kwargs):
        action = args[6] if args[0] == 'runuser' else ''
        if action == 'prepare':
            deploy.save(tmp_path / 'data/update-plan.json', {'revision': 'new', 'old_revision': 'old'})
            deploy.state(tmp_path, 'prepared', 'ready')
        elif action == 'rollback':
            raise RuntimeError('simulated rollback failure')
        elif action == 'status':
            deploy.state(tmp_path, *args[8:])
        return ''
    monkeypatch.setattr(deploy, 'command', execute)
    monkeypatch.setattr(deploy, 'healthy', lambda *a: False)
    with pytest.raises(RuntimeError, match='rollback failure'):
        deploy.supervise(tmp_path, 'test-user', 'test-service')
    state = deploy.json.loads((tmp_path / 'data/update-status.json').read_text())
    assert state['phase'] == 'failed'
    assert 'Wiederanlauf fehlgeschlagen' in state['message']
