"""Upload-Post setup and binary media preparation for one Instagram channel."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from autoposter.adapters.base import AdapterError
from autoposter.adapters.upload_post import UploadPostAdapter, profiles, verify_account, normalized, request
from autoposter.crypto import encrypt
from autoposter.models import Channel, ChannelCredential, Post
from autoposter.services import credentials


async def provider(db, channel):
    row = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
    return "upload_post" if row and (row.raw_meta or {}).get("provider") == "upload_post" else "buffer"


async def row_for(db, channel):
    row = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
    if channel.platform != "instagram" or not row or (row.raw_meta or {}).get("provider") != "upload_post":
        raise HTTPException(409, "Bitte einen neuen Instagram-Kanal mit Anbieter Upload-Post anlegen. Bestehende Buffer-Vorgänge bleiben bei Buffer.")
    return row


async def settings(db, channel):
    row = await row_for(db, channel)
    return {"has_api_key": bool(row.access_token_enc), "profile": row.external_user_id or ""}


async def save(db, channel, payload):
    row = await row_for(db, channel)
    from autoposter.services import buffer_channels
    if await buffer_channels.pending(db, channel):
        raise HTTPException(409, "Zuerst offene Instagram-Versandvorgänge klären.")
    secret = payload.api_key.get_secret_value().strip()
    if secret:
        row.access_token_enc = encrypt(secret)
    if not row.access_token_enc:
        raise HTTPException(422, "Upload-Post API-Key fehlt.")
    row.external_user_id = payload.profile.strip() or None
    row.raw_meta = {"provider": "upload_post", "destination": channel.handle}
    channel.last_verified_at = None
    channel.verified_account = ""
    channel.health = "needs_reauth"
    channel.health_note = "Upload-Post gespeichert – Profil auswählen und Verbindung testen."
    channel.is_active = False
    channel.auto_plan_enabled = False
    db.add_all([row, channel])
    await db.flush()


async def discover(db, channel):
    row = await row_for(db, channel)
    try:
        return await profiles(credentials.to_credentials(row))
    except AdapterError as exc:
        raise HTTPException(502, str(exc)) from None


async def test(db, channel):
    row = await row_for(db, channel)
    channel.last_verified_at = None
    result = {"channel_id": str(channel.id), "channel_name": channel.display_name, "platform": "instagram", "ok": False}
    try:
        item = await verify_account(credentials.to_credentials(row), channel)
        row.raw_meta = {**(row.raw_meta or {}), "instagram_account_id": item["account_id"]}
        channel.last_verified_at = datetime.now(timezone.utc)
        channel.verified_account = item["handle"]
        channel.health = "ok" if channel.is_active else "paused"
        channel.health_note = "Upload-Post-Profil und Instagram-Konto geprüft – Direktupload bereit."
        result.update(ok=True, account=item["handle"], account_id=item["account_id"], message=channel.health_note)
        db.add(row)
    except AdapterError as exc:
        channel.health = "needs_reauth"
        channel.health_note = str(exc)
        result["message"] = str(exc)
    db.add(channel)
    return result


async def prepare(db, post, channel, cred, assets):
    from autoposter.services import media, watermark
    from autoposter.services.buffer_storage import jpeg
    from autoposter.services.media_rules import combination_issue
    issue = combination_issue("instagram", assets)
    if issue or len(assets) != len(post.media_asset_ids or []):
        raise AdapterError(issue or "Instagram: Medien fehlen oder sind doppelt ausgewählt.")
    adapter = UploadPostAdapter()
    result = []
    total = 0
    for asset in assets:
        data = media.read_bytes(asset, platform="instagram")
        if asset.mime == "video/mp4":
            if len(data) > adapter.limits().max_video_bytes:
                raise AdapterError("Instagram: Video größer als 50 MB.")
        else:
            if asset.mime not in adapter.limits().mime_whitelist:
                raise AdapterError("Instagram: Medienformat nicht unterstützt.")
            data = await asyncio.to_thread(watermark.apply_bytes, data, channel, mime=asset.mime)
            data = await asyncio.to_thread(jpeg, data)
        total += len(data)
        if total > 100_000_000:
            raise AdapterError("Instagram: maximal 100 MB pro Direktupload.")
        result.append(await adapter.upload_media(cred, asset, data))
    return result


async def reconcile(db, post):
    from autoposter.services import buffer_channels
    state = buffer_channels.delivery(post)
    if state.get("state") == "confirmed":
        return "Bereits auf Instagram veröffentlicht"
    channel = await db.get(Channel, post.channel_id)
    if not channel or await provider(db, channel) != "upload_post":
        raise AdapterError("Upload-Post-Verbindung fehlt.")
    cred = await credentials.get_valid_credentials(db, channel)
    if state.get("profile") != cred.external_user_id:
        raise AdapterError("Upload-Post: Profil stimmt nicht mit dem Versandvorgang überein.")
    request_id = state.get("request_id")
    if not request_id:
        raise AdapterError("Upload-Post: Vorgangs-ID fehlt. Anbieter manuell prüfen.")
    payload = await request(cred.access_token, "/api/uploadposts/status", params={"request_id": request_id})
    if payload.get("status") == "not_found":
        return "Upload-Post kennt die Vorgangs-ID noch nicht. Anbieter prüfen; nicht erneut senden."
    if payload.get("external_id") and str(payload["external_id"]) != str(post.id):
        raise AdapterError("Upload-Post: externer Bezug stimmt nicht mit diesem Beitrag überein.")
    remote = normalized(payload, request_id, cred.external_user_id, cred.access_token)
    post.external_post_id = request_id  # Also recover a lost create response by its durable client ID.
    db.add(post)
    await db.flush()
    return await buffer_channels.apply_remote(db, post, channel, remote)


async def tick(db):
    from autoposter.services import buffer_channels
    posts = (await db.execute(select(Post).join(Channel, Post.channel_id == Channel.id).where(
        Channel.platform == "instagram", Post.status.in_(["publishing", "failed"])))).scalars().all()
    checked = errors = 0
    for post in posts:
        state = buffer_channels.delivery(post)
        if state.get("provider") != "upload_post" or state.get("state") not in ("pending", "queued", "uncertain"):
            continue
        try:
            await reconcile(db, post)
            checked += 1
        except AdapterError:
            errors += 1
        except Exception:
            await db.rollback()
            errors += 1
    return {"checked": checked, "errors": errors}
