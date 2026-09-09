from unittest.mock import Mock

import pytest

from autoposter.models import Channel
from autoposter.services import chatbot_fanvue as shared, credentials, oauthapp, appconfig
from autoposter.adapters.base import AdapterError


@pytest.fixture
def shared_connection(monkeypatch):
    values = {'fanvue_client_id': 'chatbot-id', 'fanvue_client_secret': 'chatbot-secret',
              'fanvue_api_version': '2025-06-26', 'fanvue_redirect_uri': 'http://example.test/oauth/callback'}
    tokens = {'account_handle': 'creator', 'account_uuid': 'account-one',
              'scope': 'read:self read:chat', 'expires_at': 2000000000,
              'refresh_token': 'must-stay-in-chatbot', 'access_token': 'old-access'}
    monkeypatch.setattr(shared.chatbot_db, 'get_setting', lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(shared.chatbot_db, 'get_tokens', lambda: tokens)
    monkeypatch.setattr(shared.fanvue, 'is_connected', lambda: True)
    fresh = Mock(return_value='current-chatbot-access')
    monkeypatch.setattr(shared.fanvue, 'get_access_token', fresh)
    return values, tokens, fresh


@pytest.mark.asyncio
async def test_fanvue_reuses_current_chatbot_token_without_studio_credentials(shared_connection):
    channel = Channel(platform='fanvue', handle='@creator', display_name='Creator')
    result = await credentials.get_valid_credentials(None, channel)
    assert result.access_token == 'current-chatbot-access'
    assert result.refresh_token is None
    assert result.external_user_id == 'account-one'
    assert channel.credential_id is None
    shared_connection[2].assert_called_once()


@pytest.mark.asyncio
async def test_stale_studio_token_cannot_override_chatbot(shared_connection):
    import uuid
    channel = Channel(platform='fanvue', handle='creator', display_name='Creator',
                      credential_id=uuid.uuid4(), oauth_client_id='stale-studio-app',
                      oauth_client_secret_enc='not-decryptable')
    result = await credentials.get_valid_credentials(None, channel)
    assert result.access_token == 'current-chatbot-access'
    assert oauthapp.for_channel(channel).client_id == 'chatbot-id'
    assert not oauthapp.uses_own_app(channel)


@pytest.mark.asyncio
async def test_wrong_account_fails_before_token_use(shared_connection):
    channel = Channel(platform='fanvue', handle='different-creator', display_name='Other')
    with pytest.raises(AdapterError, match='nicht zum verbundenen'):
        await credentials.get_valid_credentials(None, channel)
    shared_connection[2].assert_not_called()
    assert not credentials.is_connected(channel)


def test_configuration_is_dynamic_and_callback_stays_in_chatbot(shared_connection, monkeypatch):
    values, _, _ = shared_connection
    monkeypatch.setattr(appconfig, '_snapshot', {'fanvue_client_id': 'obsolete-studio-id'})
    assert appconfig.current('fanvue_client_id') == 'chatbot-id'
    values['fanvue_client_id'] = 'updated-chatbot-id'
    assert oauthapp.global_app('fanvue').client_id == 'updated-chatbot-id'
    assert oauthapp.global_app('fanvue').redirect_uri('fanvue') == 'http://example.test/oauth/callback'


def test_separate_fanvue_settings_are_not_written(shared_connection):
    channel = Channel(platform='fanvue', handle='creator', oauth_client_id='legacy-id')
    oauthapp.store_app(channel, 'new-id', 'new-secret', 'other')
    assert channel.oauth_client_id == 'legacy-id'
    assert oauthapp.for_channel(channel).client_id == 'chatbot-id'


@pytest.mark.asyncio
async def test_second_oauth_flow_is_blocked(shared_connection):
    from fastapi import HTTPException
    from autoposter.api.channels import oauth_start, oauth_callback
    with pytest.raises(HTTPException) as start:
        await oauth_start('fanvue', db=None, _=None)
    assert start.value.status_code == 409
    with pytest.raises(HTTPException) as callback:
        await oauth_callback('fanvue', db=None)
    assert callback.value.status_code == 409


def test_legacy_missing_connection_does_not_block_shared_channel(shared_connection):
    channel = Channel(platform='fanvue', handle='creator', health='needs_reauth', is_active=True)
    assert shared.health(channel) == 'ok'
    channel.is_active = False
    assert shared.health(channel) == 'paused'
