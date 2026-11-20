"""Buffer setup and asynchronous delivery journal, scoped to one character DB."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, update

from autoposter.adapters import AdapterError
from autoposter.adapters.buffer import account_matches, instagram_channels, read
from autoposter.crypto import encrypt
from autoposter.models import Channel, ChannelCredential, MediaAsset, Post, PostStatus
from autoposter.services import buffer_storage, credentials, lifecycle

LOCKED = {"preparing", "uploading", "pending", "queued", "uncertain", "remote_error"}


def delivery(post):
    return dict((post.generation_meta or {}).get("buffer_delivery") or {})


def locked(post):
    return delivery(post).get("state") in LOCKED


def editable(post, *, deleting=False):
    if locked(post) or deleting and delivery(post).get("files"):
        name = "Upload-Post" if delivery(post).get("provider") == "upload_post" else "Buffer"
        raise HTTPException(409, f"Beitrag wurde an {name} übergeben oder der Versand ist unklar. Status prüfen und beim Anbieter kontrollieren; nicht erneut senden.")


def deletion_details(posts):
    states = [delivery(post) for post in posts]
    external = sum(1 for state in states if state.get("state") in LOCKED)
    files = sum(len(state.get("files") or []) for state in states)
    return {"external_deliveries": external, "hosted_files": files,
            "requires_external_detach": bool(external or files)}


def validate_channel_deletion(channel, posts, *, detach_external=False, confirm_name=""):
    if detach_external:
        if confirm_name.strip() != channel.display_name.strip():
            raise HTTPException(422, "Zum lokalen Löschen bitte den vollständigen Kanalnamen bestätigen.")
        return  # Explicit local removal: never delete, retry or cancel at the provider.
    for post in posts:
        editable(post, deleting=True)


async def pending(db, channel):
    posts = (await db.execute(select(Post).where(Post.channel_id == channel.id))).scalars().all()
    return any(locked(post) for post in posts)


async def settings(db, channel):
    row = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
    meta = row.raw_meta or {} if row else {}
    return {key: meta.get(key, default) for key, default in {
        "ftp_host": "", "ftp_port": 21, "ftp_user": "", "ftp_directory": "/",
        "public_base_url": "https://sally.sallylarsen.net", "buffer_channel_id": "",
    }.items()} | {"has_api_key": bool(row and row.access_token_enc), "has_ftp_password": bool(meta.get("ftp_password_enc"))}


async def save(db, channel, payload):
    from autoposter.services import upload_post
    if await upload_post.provider(db, channel) != "buffer":
        raise HTTPException(409, "Dieser Kanal verwendet Upload-Post.")
    if await pending(db, channel):
        raise HTTPException(409, "Zuerst offene Buffer-Versandvorgänge klären. Zugangsdaten werden noch für die Statusprüfung benötigt.")
    row = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
    meta = dict(row.raw_meta or {}) if row else {}
    values = payload.model_dump(exclude={"api_key", "ftp_password"})
    try:
        config = buffer_storage.normalize(values)
    except AdapterError as exc:
        raise HTTPException(422, str(exc)) from None
    if row is None:
        row = ChannelCredential(platform="instagram")
        db.add(row)
        await db.flush()
    if payload.api_key.get_secret_value().strip():
        row.access_token_enc = encrypt(payload.api_key.get_secret_value().strip())
    if payload.ftp_password.get_secret_value():
        meta["ftp_password_enc"] = encrypt(payload.ftp_password.get_secret_value())
    if not row.access_token_enc or not meta.get("ftp_password_enc"):
        raise HTTPException(422, "Buffer-API-Key und FTPS-Passwort fehlen.")
    row.external_user_id = payload.buffer_channel_id.strip() or None
    row.raw_meta = {**config, "ftp_password_enc": meta["ftp_password_enc"], "buffer_channel_id": row.external_user_id or "", "destination": channel.handle}
    channel.credential_id = row.id
    channel.last_verified_at = None
    channel.verified_account = ""
    channel.health = "needs_reauth"
    channel.health_note = "Buffer und Webspace gespeichert – Verbindung testen."
    channel.is_active = False
    channel.auto_plan_enabled = False
    db.add_all([row, channel])
    await db.flush()


async def discover(db, channel):
    row = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
    if not row:
        raise HTTPException(422, "Zuerst Buffer-Key und Webspace speichern.")
    try:
        return await instagram_channels(credentials.to_credentials(row).access_token)
    except AdapterError as exc:
        raise HTTPException(502, str(exc)) from None


async def test(db, channel):
    result = {"channel_id": str(channel.id), "channel_name": channel.display_name, "platform": "instagram", "ok": False}
    row = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
    channel.last_verified_at = None
    try:
        if not row or not row.external_user_id:
            raise AdapterError("Buffer-Zugang speichern, Instagram-Konten laden und das Zielkonto auswählen.")
        cred = credentials.to_credentials(row)
        account = (await read(cred.access_token, "channel", id=row.external_user_id))["channel"]
        if not account_matches(channel, account):
            raise AdapterError("Instagram-Handle passt nicht zum gewählten Buffer-Konto oder die Verbindung ist getrennt/gesperrt.")
        # A harmless random text file tests write permission AND the HTTPS mapping.
        # No Instagram post is created. Secrets never enter the HTTP response.
        await buffer_storage.probe(cred, channel.id)
        channel.last_verified_at = datetime.now(timezone.utc)
        channel.verified_account = account["name"].lstrip("@")
        channel.health = "ok" if channel.is_active else "paused"
        channel.health_note = "Buffer-Konto und verschlüsselter Webspace-Upload geprüft."
        result.update(ok=True, account=channel.verified_account, account_id=account["id"], message=channel.health_note)
    except AdapterError as exc:
        channel.health = "needs_reauth"
        channel.health_note = str(exc)
        result["message"] = str(exc)
    except Exception:
        channel.health = "needs_reauth"
        channel.health_note = "Webspace-Test fehlgeschlagen. FTPS-Rechte und öffentliches HTTPS-Verzeichnis prüfen."
        result["message"] = channel.health_note
    db.add(channel)
    return result


async def apply_remote(db, post, channel, remote):
    previous = dict(post.generation_meta or {})
    state = delivery(post)
    if state.get("state") == "confirmed":
        return "Bereits bestätigt"
    if not remote.get("id") or remote.get("id") != post.external_post_id:
        raise AdapterError("Buffer-Post-ID stimmt nicht überein.")
    row = await db.get(ChannelCredential, channel.credential_id) if channel.credential_id else None
    if not row or remote.get("channelId") != row.external_user_id:
        raise AdapterError("Buffer-Zielkonto stimmt nicht überein.")
    # CAS prevents two status polls from recording usage twice, including SQLite.
    claim = await db.execute(update(Post).where(Post.id == post.id, Post.generation_meta == previous)
                             .values(generation_meta=previous).execution_options(synchronize_session=False))
    if claim.rowcount != 1:
        await db.rollback()
        return "Status wird bereits aktualisiert"
    provider_name = "Upload-Post" if state.get("provider") == "upload_post" else "Buffer"
    remote_state = remote.get("status")
    now = datetime.now(timezone.utc)
    state.update(remote_status=remote_state, checked_at=now.isoformat())
    if remote_state == "sent":
        post.status = "published"
        try:
            from autoposter.adapters.upload_post import timestamp_text
            moment = datetime.fromisoformat((timestamp_text(remote.get("sentAt")) or "").replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
        except ValueError:
            moment = now
        post.published_at = moment
        post.external_url = remote.get("externalLink") or ""
        post.error_message = ""
        state.update(state="confirmed", cleanup_after=(now + timedelta(days=7)).isoformat())
        await lifecycle.record_usage(db, asset_ids=[uuid.UUID(str(a)) for a in post.media_asset_ids or []], channel_id=channel.id, post_id=post.id)
        channel.last_published_at = moment
        db.add(channel)
        message = "Auf Instagram veröffentlicht"
    elif remote_state in ("scheduled", "sending"):
        post.status = "publishing"
        state["state"] = "queued"
        post.error_message = f"An {provider_name} übergeben – Instagram-Bestätigung steht aus."
        message = post.error_message
    else:
        state["state"] = "remote_error"
        post.status = "failed"
        detail = str(remote.get("provider_error") or "")[:500]
        post.error_message = f"{provider_name}: " + (detail or str(remote_state)) + ". Anbieter prüfen; kein automatischer Neuversand."
        message = post.error_message
    post.generation_meta = {**previous, "buffer_delivery": state}
    db.add(post)
    await db.commit()
    return message


async def reconcile(db, post):
    if delivery(post).get("provider") == "upload_post":
        from autoposter.services import upload_post
        return await upload_post.reconcile(db, post)
    if not post.external_post_id:
        return "Keine bestätigte Buffer-ID. Beitrag direkt in Buffer prüfen; nicht erneut senden."
    channel = await db.get(Channel, post.channel_id)
    if not channel or channel.platform != "instagram":
        raise AdapterError("Kein Instagram-Beitrag.")
    cred = await credentials.get_valid_credentials(db, channel)
    remote = (await read(cred.access_token, "post", id=post.external_post_id))["post"]
    return await apply_remote(db, post, channel, remote)


async def cleanup(db, post, channel):
    state = delivery(post)
    if state.get("state") != "confirmed" or not state.get("files") or not state.get("cleanup_after"):
        return False
    if datetime.fromisoformat(state["cleanup_after"]) > datetime.now(timezone.utc):
        return False
    cred = await credentials.get_valid_credentials(db, channel)
    if state.get("storage") != buffer_storage.fingerprint(cred.raw_meta):
        return False  # A changed FTP target must never receive old delete operations.
    from autoposter.services.runtime import assert_external_write_allowed
    await assert_external_write_allowed()
    remaining = []
    for entry in state["files"]:
        if entry.get("storage", state.get("storage")) != buffer_storage.fingerprint(cred.raw_meta):
            remaining.append(entry)
            continue
        expected = f"{buffer_storage.PREFIX}/{channel.id}/{post.id}/"
        if not entry["path"].startswith(expected):
            raise AdapterError("Webspace-Aufräumen: fremder Post-Pfad blockiert.")
        await asyncio.to_thread(buffer_storage.remove, cred, entry["path"])
    state.update(files=remaining, cleaned_at=datetime.now(timezone.utc).isoformat())
    post.generation_meta = {**(post.generation_meta or {}), "buffer_delivery": state}
    db.add(post)
    await db.commit()
    return True


async def tick(db):
    rows = (await db.execute(select(Post, Channel).join(Channel, Post.channel_id == Channel.id).where(Channel.platform == "instagram"))).all()
    checked = cleaned = errors = 0
    for post, channel in rows:
        state = delivery(post)
        try:
            if state.get("provider") != "upload_post" and state.get("state") == "queued" and post.external_post_id:
                await reconcile(db, post)
                checked += 1
            if await cleanup(db, post, channel):
                cleaned += 1
        except AdapterError:
            errors += 1
        except Exception:
            await db.rollback()
            errors += 1
    return {"checked": checked, "cleaned": cleaned, "errors": errors}
