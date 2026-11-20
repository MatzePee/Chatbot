"""Instagram via Upload-Post, using binary multipart uploads, never media URLs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx

from autoposter.adapters.base import AccountInfo, AdapterError, MediaRef, PublishResult
from autoposter.adapters.buffer import BufferAdapter, BufferUncertain

BASE = "https://api.upload-post.com"


def safe_message(value, token=""):
    text = str(value or "Unbekannter Anbieterfehler")
    if token:
        text = text.replace(token, "[entfernt]")
    return re.sub(r"https?://\S+", "[Medienadresse]", text)[:500]


async def request(token, path, *, files=None, data=None, params=None, request_id=None):
    writing = files is not None
    if writing:
        if path not in ("/api/upload_photos", "/api/upload"):
            raise AdapterError("Unbekannte Upload-Post-Schreiboperation.")
        from autoposter.services.runtime import assert_external_write_allowed
        await assert_external_write_allowed()
    elif not (path == "/api/uploadposts/status" or path == "/api/uploadposts/users" or path.startswith("/api/uploadposts/users/")):
        raise AdapterError("Unbekannte Upload-Post-Leseoperation.")
    if not token:
        raise AdapterError("Upload-Post API-Key fehlt.", needs_reauth=True)
    headers = {"Authorization": "Apikey " + token}
    if request_id:
        headers["Idempotency-Key"] = request_id
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=20), follow_redirects=False) as client:
            response = await client.request("POST" if writing else "GET", BASE + path, headers=headers,
                                            files=files, data=data, params=params)
    except httpx.HTTPError:
        raise (BufferUncertain if writing else AdapterError)(
            "Upload-Post: Verbindung abgebrochen. Status prüfen; nicht erneut senden." if writing
            else "Upload-Post momentan nicht erreichbar.") from None
    if response.status_code == 404 and not writing and path == "/api/uploadposts/status":
        return {"status": "not_found"}
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError()
    except ValueError:
        raise (BufferUncertain if writing else AdapterError)("Upload-Post-Antwort nicht lesbar.") from None
    if response.status_code in (401, 403):
        raise AdapterError("Upload-Post: API-Key oder Berechtigung abgelehnt.", needs_reauth=True)
    if response.status_code >= 400:
        cls = BufferUncertain if writing and response.status_code >= 500 else AdapterError
        reason = safe_message(payload.get("message") or payload.get("error"), token)
        raise cls(f"Upload-Post ({response.status_code}): {reason}")
    if payload.get("success") is False and writing:
        raise AdapterError("Upload-Post: " + safe_message(payload.get("message") or payload.get("error"), token))
    return payload


def account(profile):
    if not isinstance(profile, dict) or not isinstance(profile.get("social_accounts"), dict):
        return None
    social = profile["social_accounts"].get("instagram")
    if not isinstance(social, dict):
        return None
    account_id = str(social.get("username") or "").strip()
    handle = social.get("handle") or ""
    if not handle and account_id and not account_id.isdigit():
        handle = account_id  # Older API responses used handles in username.
    if not account_id and not str(handle).strip():
        return None
    # List responses may deliberately omit the Instagram ID. The single-profile
    # endpoint supplies it; verify_account binds that ID before publishing.
    return {"profile": str(profile.get("username") or ""), "label": profile.get("display_name") or profile.get("username") or "",
            "handle": str(handle).strip().lstrip("@"), "account_id": account_id,
            "reauth_required": bool(social.get("reauth_required") or profile.get("reauth_required"))}


async def profiles(cred):
    payload = await request(cred.access_token, "/api/uploadposts/users")
    if payload.get("success") is False or not isinstance(payload.get("profiles"), list):
        raise AdapterError("Upload-Post: Profile konnten nicht gelesen werden.")
    return [item for p in payload["profiles"] if (item := account(p)) and item["profile"]]


async def verify_account(cred, channel=None):
    if not cred.external_user_id:
        raise AdapterError("Zuerst ein Upload-Post-Profil auswählen.")
    payload = await request(cred.access_token, "/api/uploadposts/users/" + quote(cred.external_user_id, safe=""))
    item = account(payload.get("profile") or {})
    if not item or not item["account_id"] or item["profile"] != cred.external_user_id or item["reauth_required"]:
        raise AdapterError("Upload-Post: Instagram-Konto fehlt oder muss neu verbunden werden.", needs_reauth=True)
    expected = (channel.handle if channel else cred.raw_meta.get("destination") or "").strip().lstrip("@").lower()
    if not expected or item["handle"].lower() != expected:
        raise AdapterError("Upload-Post: Das verbundene Instagram-Konto passt nicht zum Kanal-Handle.", needs_reauth=True)
    bound = cred.raw_meta.get("instagram_account_id")
    if bound and item["account_id"] != bound:
        raise AdapterError("Upload-Post: Im Profil wurde ein anderes Instagram-Konto verbunden. Zuerst erneut einrichten und testen.", needs_reauth=True)
    return item


def timestamp_text(value):
    """Upload Status uses Mongo Extended JSON dates; synchronous results use text."""
    if isinstance(value, dict):
        value = value.get("$date")
    return value.strip() if isinstance(value, str) and value.strip() else None


def normalized(payload, request_id, profile, token=""):
    """Convert provider state to the existing Instagram delivery ledger contract."""
    if payload.get("request_id") and str(payload["request_id"]) != request_id:
        raise AdapterError("Upload-Post: unerwartete Vorgangs-ID. Kein erneuter Versand.")
    results = payload.get("results") or []
    if isinstance(results, dict):
        result = results.get("instagram") or {}
    else:
        result = next((r for r in results if isinstance(r, dict) and r.get("platform") == "instagram"), {}) if isinstance(results, list) else {}
    if not isinstance(result, dict):
        result = {}
    state = payload.get("status") or ""
    if result.get("success") is True and not result.get("skipped") and (
        state == "completed" or not state and (result.get("url") or result.get("post_url"))):
        remote_state = "sent"
    elif result.get("status") in ("queued", "processing", "retryable") or state in ("pending", "queued", "processing", "in_progress"):
        remote_state = "sending"
    elif state in ("failed", "completed") or result.get("success") is False or result.get("skipped"):
        remote_state = "error"
    else:
        remote_state = "sending"  # Missing/unknown result never counts as published.
    url = str(result.get("url") or result.get("post_url") or "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ("instagram.com", "www.instagram.com"):
        url = ""
    return {"id": request_id, "channelId": profile, "status": remote_state,
            "externalLink": url, "sentAt": timestamp_text(result.get("upload_timestamp")) or timestamp_text(payload.get("last_update")),
            "provider_error": safe_message(result.get("error") or result.get("skip_reason") or payload.get("message"), token) if remote_state == "error" else ""}


@dataclass
class UploadPostMediaRef(MediaRef):
    data: bytes = b""


class UploadPostAdapter(BufferAdapter):
    async def verify(self, app, cred):
        item = await verify_account(cred)
        return AccountInfo(True, item["account_id"], item["handle"], "Upload-Post-Verbindung geprüft.")

    async def upload_media(self, cred, asset, data):
        return UploadPostMediaRef(str(asset.id), "", mime="video/mp4" if asset.mime == "video/mp4" else "image/jpeg", data=data)

    async def publish(self, cred, channel, post, media):
        from autoposter.services.media_rules import combination_issue
        issue = combination_issue("instagram", media)
        if issue or len(media) != len(post.media_asset_ids or []):
            raise AdapterError(issue or "Instagram: Medien fehlen oder sind doppelt ausgewählt.")
        await verify_account(cred, channel)
        tags = " ".join("#" + str(t).strip().lstrip("#") for t in post.hashtags or [] if str(t).strip())
        text = "\n\n".join(p for p in [(post.body_text or "").strip(), tags] if p)
        if len(text) > 2200:
            raise AdapterError("Instagram: maximal 2200 Zeichen einschließlich Hashtags.")
        request_id = (post.generation_meta or {}).get("buffer_delivery", {}).get("request_id")
        if not request_id:
            raise AdapterError("Upload-Post: dauerhafte Vorgangs-ID fehlt; Versand blockiert.")
        video = len(media) == 1 and media[0].mime == "video/mp4"
        files = [("video" if video else "photos[]", (f"media-{index}.mp4" if video else f"media-{index}.jpg", ref.data, ref.mime)) for index, ref in enumerate(media)]
        data = {"user": cred.external_user_id, "platform[]": "instagram", "title": text,
                "async_upload": "true", "request_id": request_id, "external_id": str(post.id)}
        if video:
            data["media_type"] = "REELS"
        payload = await request(cred.access_token, "/api/upload" if video else "/api/upload_photos",
                                files=files, data=data, request_id=request_id)
        if payload.get("request_id") and str(payload["request_id"]) != request_id:
            raise BufferUncertain("Upload-Post hat eine abweichende Vorgangs-ID zurückgegeben. Anbieter prüfen; nicht erneut senden.")
        state = normalized(payload, request_id, cred.external_user_id, cred.access_token)
        return PublishResult(request_id, state["externalLink"], raw=state)
