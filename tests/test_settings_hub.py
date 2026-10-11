"""Unified settings, using disposable conftest DB and no application lifespan."""
import json
import hashlib
from pathlib import Path
from html.parser import HTMLParser
import pytest
from fastapi.testclient import TestClient
from app import main, db

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('MP_PREVIEW', '0')
    monkeypatch.setattr(main.fanvue, 'is_connected', lambda: False)
    return TestClient(main.app)


def test_top_settings_contains_real_sections(client):
    for section in ('shared', 'autochat', 'autopost'):
        r = client.get('/settings/shared', params={'section': section})
        assert r.status_code == 200
        assert 'class="settings-hub"' in r.text
        for name in ('Gemeinsam', 'AutoChat', 'AutoPost'):
            assert f'>{name}</a>' in r.text
        assert '/static/settings-hub.css' in r.text
        assert 'href="/settings/shared" class="active"' in r.text
        import os
        if output := os.environ.get('SETTINGS_RENDER_OUTPUT'):
            target = Path(output)
            target.mkdir(parents=True, exist_ok=True)
            (target / (section + '.html')).write_text(r.text)
    assert 'name="system_prompt"' in client.get('/settings/shared?section=autochat').text
    assert 'id="autopost-settings"' in client.get('/settings/shared?section=autopost').text
    assert 'class="settings-hub"' in client.get('/settings').text


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('input', 'select', 'textarea', 'button', 'form', 'a'):
            self.items.append({'tag': tag, **{k: v for k, v in a.items() if k in ('name', 'id', 'type', 'action', 'href')}})


def test_manifest_controls_actions_are_all_preserved(client):
    manifest = json.loads((ROOT / 'docs/settings-completeness.json').read_text())
    for section in ('shared', 'autochat', 'updates'):
        parser = Controls()
        # Source-level inventory covers both connected/disconnected and
        # unavailable/live list branches, not just one synthetic render.
        parser.feed((ROOT / manifest[section]['source']).read_text())
        for original in manifest[section]['controls_actions']:
            assert original in parser.items, original
        from collections import Counter
        old_counts = Counter(json.dumps(row, sort_keys=True) for row in manifest[section]['controls_actions'])
        new_counts = Counter(json.dumps(row, sort_keys=True) for row in parser.items)
        assert not (old_counts - new_counts), 'No duplicate or anonymous control/action may disappear'
        class Options(HTMLParser):
            def __init__(self):
                super().__init__()
                self.items = []
            def handle_starttag(self, tag, attrs):
                if tag == 'option':
                    self.items.append(dict(attrs))
        options = Options()
        options.feed((ROOT / manifest[section]['source']).read_text())
        assert all(row in options.items for row in manifest[section]['original_select_options'])
    source = ROOT / manifest['autopost']['source']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == manifest['autopost']['source_sha256']


@pytest.mark.parametrize('route,key', [('/settings/shared/fanvue','fanvue_client_secret'), ('/settings/shared/telegram','telegram_bot_token'), ('/settings','openrouter_api_key'), ('/settings','vision_api_key')])
def test_blank_secret_preserves_existing_value(client, route, key):
    db.set_setting(key, 'synthetic-existing-secret')
    assert client.post(route, data={key: ''}, follow_redirects=False).status_code == 303
    assert db.get_setting(key) == 'synthetic-existing-secret'


@pytest.mark.parametrize('mask', ['********', '••••••••'])
@pytest.mark.parametrize('route,key', [('/settings/shared/fanvue','fanvue_client_secret'), ('/settings/shared/telegram','telegram_bot_token'), ('/settings','openrouter_api_key'), ('/settings','vision_api_key')])
def test_masked_secret_preserves_existing_value(client, route, key, mask):
    db.set_setting(key, 'synthetic-existing-secret')
    assert client.post(route, data={key: mask}, follow_redirects=False).status_code == 303
    assert db.get_setting(key) == 'synthetic-existing-secret'


def test_missing_remote_lists_preserve_prepopulation(client):
    db.set_setting('chat_custom_list_id', 'test-list-one')
    db.set_setting('prio2_custom_list_id', 'test-list-two')
    html = client.get('/settings/shared?section=autochat').text
    assert 'value="test-list-one" selected' in html
    assert 'value="test-list-two" selected' in html


def test_navigation_never_mutates_settings(client):
    before = db.all_settings()
    for section in ('shared','autochat','autopost'):
        assert client.get('/settings/shared?section=' + section).status_code == 200
    assert db.all_settings() == before


class FormValues(HTMLParser):
    """Serialize successful controls as a browser does, including unchecked omission."""
    def __init__(self):
        super().__init__()
        self.values = {}
        self.current = None
        self.option = False
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'input' and a.get('name') and 'disabled' not in a:
            if a.get('type') != 'checkbox' or 'checked' in a:
                self.values[a['name']] = a.get('value', 'on' if a.get('type') == 'checkbox' else '')
        elif tag in ('select', 'textarea'):
            self.current = a.get('name')
            if self.current:
                self.values[self.current] = ''
        elif tag == 'option' and self.current:
            if 'selected' in a or not self.option:
                self.values[self.current] = a.get('value', '')
            self.option = True
    def handle_data(self, data):
        if self.current and not self.option:
            self.values[self.current] += data
    def handle_endtag(self, tag):
        if tag in ('select', 'textarea'):
            self.current = None
            self.option = False


def test_complete_chat_form_roundtrip_does_not_lose_settings(client):
    db.set_setting('system_prompt', 'Synthetic persona\nLine two & <safe>')
    db.set_setting('chat_custom_list_id', 'list-one')
    db.set_setting('chat_custom_list_name', 'Saved list one')
    db.set_setting('prio2_custom_list_id', 'list-two')
    db.set_setting('prio2_custom_list_name', 'Saved list two')
    before = db.all_settings()
    parser = FormValues()
    parser.feed(client.get('/settings/shared?section=autochat').text)
    assert client.post('/settings', data=parser.values, follow_redirects=False).status_code == 303
    assert db.all_settings() == before


def test_autopost_module_is_real_javascript_with_preserved_mount(client):
    html = client.get('/settings/shared?section=autopost').text
    assert 'id="autopost-settings" data-live-preserve' in html
    assert 'id="modals"' in html and 'id="toasts"' in html
    module = client.get('/static/settings-autopost.js').text
    import re
    for url in re.findall(r"from '([^']+)'", module):
        response = client.get(url)
        assert response.status_code == 200
        assert 'javascript' in response.headers['content-type']


@pytest.mark.parametrize('route,key', [('/settings/shared/fanvue','fanvue_client_secret'), ('/settings/shared/telegram','telegram_bot_token'), ('/settings','openrouter_api_key'), ('/settings','vision_api_key')])
def test_secret_removal_requires_explicit_control(client, route, key):
    db.set_setting(key, 'synthetic-existing-secret')
    html = client.get('/settings' if route == '/settings' else '/settings/shared').text
    assert f'name="clear_{key}"' in html
    response = client.post(route, data={f'clear_{key}': 'on'}, follow_redirects=False)
    assert response.status_code == 303
    assert db.get_setting(key) == ''
