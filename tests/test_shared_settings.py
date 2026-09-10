"""Settings moved into the common shell must preserve credentials and module state."""
import pytest
from fastapi.testclient import TestClient
from app import main, db


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('MP_PREVIEW', '0')
    monkeypatch.setattr(main.fanvue, 'is_connected', lambda: False)
    db.save_tokens('test-access', 'test-refresh', 123456789, account_uuid='test-account')
    # No lifespan: never start workers or contact a real provider in these tests.
    return TestClient(main.app)


def test_chat_settings_preserve_shared_values_and_tokens(client):
    values = {'fanvue_client_id': 'existing-client', 'fanvue_client_secret': 'existing-secret',
              'fanvue_redirect_uri': 'https://example.test/oauth/callback', 'fanvue_api_version': 'v1',
              'update_check_enabled': True, 'update_notify_telegram': True,
              'update_check_interval_hours': 12, 'telegram_bot_token': 'existing-token',
              'telegram_chat_id': '12345', 'app_base_url': 'https://studio.example'}
    for key, value in values.items():
        db.set_setting(key, value)
    tokens = dict(db.get_tokens())
    response = client.post('/settings', data={'openrouter_model': 'new-chat-model',
                           'fanvue_client_secret': 'must-be-ignored',
                           'telegram_bot_token': 'must-be-ignored', 'telegram_chat_id': 'wrong-chat',
                           'app_base_url': 'https://wrong.example'}, follow_redirects=False)
    assert response.status_code == 303
    assert db.get_setting('openrouter_model') == 'new-chat-model'
    assert {key: db.get_setting(key) for key in values} == values
    assert dict(db.get_tokens()) == tokens


def test_shared_forms_only_change_their_own_group(client):
    db.set_setting('time_context_enabled', True)
    db.set_setting('telegram_enabled', True)
    db.set_setting('update_check_enabled', True)
    db.set_setting('openrouter_api_key', 'existing-ai-key')
    db.set_setting('fanvue_client_secret', 'existing-secret')
    tokens = dict(db.get_tokens())
    response = client.post('/settings/shared/fanvue', data={'fanvue_client_id': 'new-client',
                           'openrouter_api_key': 'must-be-ignored'}, follow_redirects=False)
    assert response.status_code == 303
    assert db.get_setting('fanvue_client_id') == 'new-client'
    assert db.get_setting('fanvue_client_secret') == 'existing-secret'
    assert db.get_setting('update_check_enabled') is True
    response = client.post('/settings/shared/updates', data={'update_check_interval_hours': '24',
                           'update_check_enabled': 'on', 'fanvue_client_secret': 'must-be-ignored'},
                           follow_redirects=False)
    assert response.status_code == 303
    assert db.get_setting('update_check_interval_hours') == 24
    assert db.get_setting('update_notify_telegram') is False
    assert db.get_setting('fanvue_client_secret') == 'existing-secret'
    assert db.get_setting('openrouter_api_key') == 'existing-ai-key'
    assert db.get_setting('time_context_enabled') is True
    assert db.get_setting('telegram_enabled') is True
    assert dict(db.get_tokens()) == tokens


@pytest.mark.parametrize('interval', ['invalid', '0', '169', ''])
def test_invalid_update_interval_does_not_partially_save(client, interval):
    before = db.all_settings()
    assert client.post('/settings/shared/updates', data={'update_check_interval_hours': interval}).status_code == 422
    assert db.all_settings() == before


def test_shared_controls_render_only_on_shared_page(client):
    shared = client.get('/settings/shared')
    chat = client.get('/settings')
    assert shared.status_code == chat.status_code == 200
    for key in main._SHARED_SETTINGS_KEYS:
        assert f'name="{key}"' in shared.text
        assert f'name="{key}"' not in chat.text
    assert 'name="openrouter_api_key"' in chat.text
    assert 'name="openrouter_api_key"' not in shared.text
    assert 'href="/settings/shared" class="active"' in shared.text
    assert 'href="/" class="active"' in chat.text


def test_shared_mutations_stay_blocked_in_preview(client, monkeypatch):
    monkeypatch.setenv('MP_PREVIEW', '1')
    before = db.all_settings()
    for path in ['/settings/shared/fanvue', '/settings/shared/updates', '/settings/shared/telegram',
                 '/settings/shared/telegram-test', '/settings/shared/telegram-chatid']:
        assert client.post(path, data={'fanvue_client_id': 'blocked', 'update_check_interval_hours': '24'}).status_code == 403
    assert db.all_settings() == before


def test_telegram_connection_save_preserves_notification_choices(client):
    db.set_setting('telegram_enabled', True)
    db.set_setting('alert_keywords_enabled', True)
    db.set_setting('alert_keywords', 'custom keyword')
    db.set_setting('update_notify_telegram', True)
    before = db.all_settings()
    tokens = dict(db.get_tokens())
    values = {'telegram_bot_token': 'new-token', 'telegram_chat_id': '-10012345',
              'app_base_url': 'https://studio.example'}
    response = client.post('/settings/shared/telegram', data={**values,
                           'alert_keywords': 'must-be-ignored',
                           'fanvue_client_secret': 'must-be-ignored'}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/settings/shared?saved=telegram#telegram'
    assert db.all_settings() == {**before, **values}
    assert dict(db.get_tokens()) == tokens
    # A partial request must not clear an existing token or public URL.
    client.post('/settings/shared/telegram', data={'telegram_chat_id': '67890'})
    assert db.get_setting('telegram_bot_token') == 'new-token'
    assert db.get_setting('app_base_url') == 'https://studio.example'


def test_notification_options_and_publication_render_in_their_own_area(client):
    shared = client.get('/settings/shared').text
    chat = client.get('/settings').text
    assert '<title>Einstellungen · MP CreatorStudio</title>' in shared
    for name in ['telegram_enabled', 'alert_keywords_enabled', 'alert_keywords']:
        assert f'name="{name}"' in chat
        assert f'name="{name}"' not in shared
    assert 'id="tg-chatid"' in shared and 'id="tg-chatid"' not in chat
    assert 'id="kw-test"' in chat and 'id="kw-test"' not in shared
    assert '>Veröffentlichen</h2>' not in shared and '>Veröffentlichen</h2>' not in chat
    assert 'Zur Upload-Seite' not in shared and 'Zur Upload-Seite' not in chat
    assert 'href="/upload"' in shared and 'GitHub-Veröffentlichung' in shared


@pytest.mark.parametrize('prefix', ['/settings', '/settings/shared'])
def test_connection_tools_use_entered_values_without_saving_or_real_messages(client, monkeypatch, prefix):
    from app import notify
    before = db.all_settings()
    sent = []
    monkeypatch.setattr(notify, 'get_me', lambda **kw: {'username': 'test_bot'})
    monkeypatch.setattr(notify, 'send_or_raise', lambda text, **kw: sent.append((text, kw)))
    monkeypatch.setattr(notify, 'discover_chat_id', lambda **kw: ('-10042', 'test chat'))
    response = client.post(prefix + '/telegram-test', data={'token': 'entered-token', 'chat_id': '42'})
    assert response.json()['ok'] is True
    assert sent[0][1] == {'token': 'entered-token', 'chat_id': '42'}
    assert 'MP CreatorStudio' in sent[0][0]
    assert client.post(prefix + '/telegram-chatid', data={'token': 'entered-token'}).json()['chat_id'] == '-10042'
    assert db.all_settings() == before


def test_update_controls_are_visible_in_shared_settings_and_system(client):
    for path in ['/settings/shared', '/system']:
        page = client.get(path).text
        assert page.count('id="upd-card"') == 1
        assert 'id="upd-now">Nach Updates suchen</button>' in page
        assert 'action="/system/update" id="upd-install-form"' in page
        assert 'id="upd-install" disabled>Update installieren</button>' in page
        assert '/static/program-updates.js' in page
    # Installation must not be nested in the settings-save form.
    shared = client.get('/settings/shared').text
    assert shared.index('id="upd-install-form"') < shared.index('action="/settings/shared/updates"')
