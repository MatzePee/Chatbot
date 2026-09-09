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
              'update_check_interval_hours': 12}
    for key, value in values.items():
        db.set_setting(key, value)
    tokens = dict(db.get_tokens())
    response = client.post('/settings', data={'openrouter_model': 'new-chat-model',
                           'fanvue_client_secret': 'must-be-ignored'}, follow_redirects=False)
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
    for path in ['/settings/shared/fanvue', '/settings/shared/updates']:
        assert client.post(path, data={'fanvue_client_id': 'blocked', 'update_check_interval_hours': '24'}).status_code == 403
    assert db.all_settings() == before
