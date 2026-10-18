"""Per-channel encrypted credentials; notification bot settings are never accessed."""
import re
from datetime import datetime, timezone
from fastapi import HTTPException
from autoposter.adapters import Credentials, AdapterError, get_adapter
from autoposter.adapters.base import OAuthApp
from autoposter.models import ChannelCredential
from autoposter.services import credentials

async def save(db, channel, payload):
    destination = payload.destination.strip()
    if not re.fullmatch(r'(?:@[A-Za-z][A-Za-z0-9_]{4,31}|-100[0-9]+)', destination):
        raise HTTPException(422, 'Ziel muss @kanalname oder eine private -100… Kanal-ID sein.')
    token = payload.bot_token.strip()
    row = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
    preserve = not token or token in ('********', '••••••••')
    if not preserve and not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        raise HTTPException(422, 'Ungültiges Bot-Token-Format.')
    if preserve and not row:
        raise HTTPException(422, 'Bot-Token fehlt.')
    if not row:
        row = ChannelCredential(platform='telegram')
        db.add(row)
        await db.flush()
        channel.credential_id = row.id
    cred = credentials.to_credentials(row) if preserve else Credentials(token)
    if not preserve or cred.raw_meta.get('destination') != destination:
        cred.raw_meta = {'destination':destination}
        channel.last_verified_at = None
        channel.verified_account = ''
        channel.health = 'needs_reauth'
        channel.health_note = 'Gespeichert; Bot und Kanalrechte noch nicht geprüft.'
    channel.handle = destination
    # Do not rotate ciphertext for a blank/masked preservation request.
    if not preserve:
        row.external_user_id = None
        credentials.store_credentials(row, cred)
    else:
        row.raw_meta = dict(cred.raw_meta)
    db.add_all([channel,row])
    await db.flush()

async def test(db, channel):
    row = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
    result = {'channel_id':str(channel.id),'channel_name':channel.display_name,'platform':'telegram','ok':False,'step':'telegram'}
    try:
        if not row or row.platform != 'telegram':
            raise AdapterError('Telegram: Bot-Token und Zielkanal zuerst speichern.')
        cred = credentials.to_credentials(row)
        if cred.raw_meta.get('destination') != channel.handle:
            raise AdapterError('Telegram: Ziel geändert; Bot-Zugang erneut speichern.')
        info = await get_adapter('telegram').verify(OAuthApp(''), cred)
        if not info.ok:
            raise AdapterError('Telegram: Verbindung nicht bestätigt.')
        row.external_user_id = info.account_id
        row.raw_meta = {**cred.raw_meta, **info.raw}
        channel.last_verified_at = datetime.now(timezone.utc)
        channel.verified_account = info.username
        if channel.health != 'paused':
            channel.health = 'ok'
        channel.health_note = info.message
        result.update(ok=True, message=info.message, account=info.username, account_id=info.account_id, chat_id=info.raw['chat_id'])
        db.add(row)
    except AdapterError as exc:
        channel.last_verified_at = None
        channel.health = 'needs_reauth'
        channel.health_note = str(exc)
        result['message'] = str(exc)
    db.add(channel)
    await db.flush()
    return result
