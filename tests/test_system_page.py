"""System navigation must keep live controls separate and preserve preview safety."""
import re
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from app import db, main, updater


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('MP_PREVIEW', '0')
    monkeypatch.setattr(main.fanvue, 'is_connected', lambda: False)
    # No lifespan: tests never start workers.
    return TestClient(main.app)


def test_system_cards_move_without_changing_bot_state(client, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Reading System must not execute an administrative action')
    monkeypatch.setattr(main, '_run_admin', unexpected)
    monkeypatch.setattr(updater, 'install', unexpected)
    db.set_setting('bot_running', True)
    db.save_tokens('access', 'refresh', 123456789, account_uuid='existing-account')
    before = (db.all_settings(), dict(db.get_tokens()))
    dashboard = client.get('/')
    system = client.get('/system')
    assert dashboard.status_code == system.status_code == 200
    for marker in ['id="upd-card"', 'id="server-load"', 'id="server-controls"']:
        assert marker in system.text
        assert marker not in dashboard.text
    assert 'Master-Schalter' in dashboard.text
    assert 'Letzte Aktivität' in dashboard.text
    assert 'href="/system" class="active"' in system.text
    assert 'href="/" class="active"' not in system.text
    assert 'href="/system"' in client.get('/settings/shared').text
    assert (db.all_settings(), dict(db.get_tokens())) == before


@pytest.mark.parametrize('action,result', [('restart-service', 'restart'), ('reboot', 'reboot'), ('update', 'update')])
def test_actions_return_to_system(client, monkeypatch, action, result):
    actions = []
    monkeypatch.setattr(main, '_run_admin', lambda value: actions.append(value))
    monkeypatch.setattr(updater, 'install', lambda: actions.append('update') or (True, 'started'))
    response = client.post('/system/' + action, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/system?sys=' + result
    assert actions == [action]


@pytest.mark.parametrize('action', ['restart-service', 'reboot', 'update'])
def test_action_errors_are_visible_on_system(client, monkeypatch, action):
    def fail(*args):
        raise subprocess.CalledProcessError(1, 'test', stderr=b'sudo: a password is required')
    monkeypatch.setattr(main, '_run_admin', fail)
    monkeypatch.setattr(updater, 'install', lambda: (False, 'sudo: a password is required'))
    response = client.post('/system/' + action)
    assert response.url.path == '/system'
    assert 'sudo: a password is required' in response.text


def test_legacy_system_result_link_still_works(client):
    response = client.get('/?sys_err=test%20message', follow_redirects=False)
    url = urlsplit(response.headers['location'])
    assert response.status_code == 303
    assert url.path == '/system'
    assert parse_qs(url.query)['sys_err'] == ['test message']


def test_system_remains_readable_but_actions_blocked_in_preview(client, monkeypatch):
    monkeypatch.setenv('MP_PREVIEW', '1')
    actions = []
    monkeypatch.setattr(main, '_run_admin', lambda value: actions.append(value))
    monkeypatch.setattr(updater, 'install', lambda: actions.append('update') or (True, 'started'))
    assert client.get('/system').status_code == 200
    for action in ['update', 'restart-service', 'reboot']:
        assert client.post('/system/' + action).status_code == 403
    assert actions == []


@pytest.mark.parametrize('path', ['/settings/shared', '/system', '/upload'])
def test_settings_and_system_share_navigation(client, path):
    response = client.get(path)
    assert response.status_code == 200
    top = re.search(r'<nav class="workspace-switcher".*?</nav>', response.text, re.S).group()
    assert re.findall(r'href="([^"]+)"', top) == ['/', '/autoposter/', '/settings/shared']
    assert 'href="/settings/shared" class="active"' in top
    sidebar = re.search(r'<nav class="workspace-nav".*?</nav>', response.text, re.S).group()
    assert re.findall(r'href="([^"]+)"', sidebar) == [
        '/settings/shared#fanvue', '/settings/shared#telegram', '/system',
        '/settings/shared#updates', '/upload']
    assert 'Verbindungen' in sidebar and 'Verwaltung' in sidebar
