"""Laden, Entschlüsseln und Auffrischen von Kanal-Zugangsdaten."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.adapters import AdapterError, Credentials, get_adapter
from autoposter.crypto import decrypt, encrypt
from autoposter.models import Channel, ChannelCredential, ChannelHealth
from autoposter.services import oauthapp

REFRESH_MARGIN = timedelta(minutes=5)


def to_credentials(row: ChannelCredential) -> Credentials:
    return Credentials(
        access_token=decrypt(row.access_token_enc) or "",
        refresh_token=decrypt(row.refresh_token_enc),
        expires_at=row.expires_at,
        scopes=list(row.scopes or []),
        external_user_id=row.external_user_id,
        raw_meta=dict(row.raw_meta or {}),
    )


def store_credentials(row: ChannelCredential, cred: Credentials) -> ChannelCredential:
    row.access_token_enc = encrypt(cred.access_token)
    row.refresh_token_enc = encrypt(cred.refresh_token)
    row.expires_at = cred.expires_at
    row.scopes = list(cred.scopes or [])
    if cred.external_user_id:
        row.external_user_id = cred.external_user_id
    row.raw_meta = dict(cred.raw_meta or {})
    return row


async def get_valid_credentials(db: AsyncSession, channel: Channel) -> Credentials:
    """Gibt gültige Zugangsdaten zurück und frischt bei Bedarf auf.

    Die Refresh-Token-Rotation wird atomar gespeichert: X gibt bei jedem Refresh
    ein neues Refresh-Token aus und macht das alte ungültig.
    """
    if channel.platform == "fanvue":
        from . import chatbot_fanvue
        return await chatbot_fanvue.credentials(channel)
    if not channel.credential_id:
        raise AdapterError(
            f"Kanal '{channel.display_name}' ist nicht verbunden", needs_reauth=True
        )
    row = (
        await db.execute(
            select(ChannelCredential).where(ChannelCredential.id == channel.credential_id)
        )
    ).scalar_one_or_none()
    if not row or not row.access_token_enc:
        raise AdapterError(
            f"Kanal '{channel.display_name}' hat keine Zugangsdaten", needs_reauth=True
        )

    cred = to_credentials(row)
    needs_refresh = bool(
        cred.expires_at and cred.expires_at <= datetime.now(timezone.utc) + REFRESH_MARGIN
    )
    if not needs_refresh:
        return cred

    adapter = get_adapter(channel.platform)
    app = oauthapp.for_channel(channel)
    if not app.is_configured:
        raise AdapterError(
            f"Für '{channel.display_name}' sind keine API-Zugangsdaten hinterlegt",
            needs_reauth=True,
        )
    try:
        fresh = await adapter.refresh(app, cred)
    except AdapterError as exc:
        channel.health = ChannelHealth.needs_reauth.value
        channel.health_note = f"Token-Refresh fehlgeschlagen: {exc}"
        db.add(channel)
        await db.flush()
        raise
    store_credentials(row, fresh)
    db.add(row)
    if channel.health == ChannelHealth.needs_reauth.value:
        channel.health = ChannelHealth.ok.value
        channel.health_note = ""
        db.add(channel)
    await db.flush()
    return fresh


async def mark_reauth(db: AsyncSession, channel: Channel, note: str) -> None:
    channel.health = ChannelHealth.needs_reauth.value
    channel.health_note = note[:500]
    db.add(channel)
    await db.flush()


async def token_expiry(db: AsyncSession, channel: Channel) -> Optional[datetime]:
    if channel.platform == "fanvue":
        from . import chatbot_fanvue
        return chatbot_fanvue.expiry()
    if not channel.credential_id:
        return None
    row = (
        await db.execute(
            select(ChannelCredential).where(ChannelCredential.id == channel.credential_id)
        )
    ).scalar_one_or_none()
    return row.expires_at if row else None


def is_connected(channel):
    if channel.platform == 'fanvue':
        from . import chatbot_fanvue
        return chatbot_fanvue.connected(channel)
    return bool(channel.credential_id)
