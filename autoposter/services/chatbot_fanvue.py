"""Single Fanvue connection, owned by the AutoChat (including token rotation)."""
import asyncio
from datetime import datetime, timezone

from app import db as chatbot_db, fanvue, preview
from autoposter.adapters.base import Credentials, OAuthApp, AdapterError

FIELDS = ('fanvue_client_id', 'fanvue_client_secret', 'fanvue_api_version')


class SharedOAuthApp(OAuthApp):
    def redirect_uri(self, platform):
        return str(chatbot_db.get_setting('fanvue_redirect_uri', '') or '')


def configuration():
    return {key: chatbot_db.get_setting(key, '2025-06-26' if key == 'fanvue_api_version' else '') or '' for key in FIELDS}


def oauth_app():
    values = configuration()
    return SharedOAuthApp(client_id=values['fanvue_client_id'],
                          client_secret=values['fanvue_client_secret'],
                          api_version=values['fanvue_api_version'], name='AutoChat')


def account_matches(channel):
    tokens = chatbot_db.get_tokens()
    bound = getattr(channel, 'fanvue_account_uuid', '') or ''
    if bound:
        return bool(tokens and tokens['account_uuid'] == bound)
    expected = (channel.handle or '').strip().lstrip('@').casefold()
    actual = (tokens['account_handle'] or '').strip().lstrip('@').casefold() if tokens else ''
    return not expected or bool(actual and expected == actual)


def connected(channel):
    return fanvue.is_connected() and account_matches(channel)


def expiry():
    # A local snapshot cannot describe the server token's current expiry.
    tokens = preview._token_cache if preview.enabled() else chatbot_db.get_tokens()
    timestamp = tokens.get('expires_at') if isinstance(tokens, dict) else (tokens['expires_at'] if tokens else None)
    return datetime.fromtimestamp(timestamp, timezone.utc) if timestamp else None


async def credentials(channel):
    if not account_matches(channel):
        raise AdapterError('Dieser Fanvue-Kanal gehört nicht zum verbundenen AutoChat-Konto. Bitte das Kanal-Handle prüfen.', needs_reauth=False)
    try:
        # This is also the sole refresh owner in production; in shadow mode it
        # reads the server token through the restricted SSH connection instead.
        token = await asyncio.to_thread(fanvue.get_access_token)
    except (fanvue.FanvueError, preview.PreviewNetworkBlocked) as exc:
        raise AdapterError(str(exc), needs_reauth=False) from exc
    row = chatbot_db.get_tokens()
    return Credentials(access_token=token, refresh_token=None, expires_at=expiry(),
                       scopes=str(row['scope'] or '').split() if row else [],
                       external_user_id=row['account_uuid'] if row else None,
                       raw_meta={'source': 'chatbot'})


def health(channel):
    if channel.is_active is False or channel.health == 'paused':
        return 'paused'
    if not connected(channel):
        return 'needs_reauth'
    return 'ok' if channel.health in (None, 'needs_reauth') else channel.health
