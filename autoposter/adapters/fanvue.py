"""Adapter für die Fanvue API.

Fakten aus der offiziellen Dokumentation (https://api.fanvue.com/docs):
- Keine API-Keys. Ausschließlich OAuth 2.0 Authorization Code.
  auth:  https://auth.fanvue.com/oauth2/auth
  token: https://auth.fanvue.com/oauth2/token
- Pflicht-Header bei JEDEM Request: X-Fanvue-API-Version (aktuell 2025-06-26).
  Bei HTTP 410 ist die Version abgekündigt -> Admin benachrichtigen.
- Medien: Multipart-Upload-Session anlegen -> Teile nach S3 hochladen ->
  Session abschließen -> Status 'processing' abwarten.
- Post: text (<=5000), mediaUuids[], mediaPreviewUuid, price (Cent, min. 300),
  audience (Pflicht), publishAt, expiresAt, collectionUuids[].
- Rate-Limit: 200 Requests / 60 s pro clientId:userUuid.
"""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from autoposter.adapters.base import (
    AccountInfo,
    AdapterError,
    ChannelLimits,
    Credentials,
    MediaRef,
    OAuthApp,
    PublishResult,
    http_client,
    raise_for_response,
)
from autoposter.config import settings
from autoposter.models import Channel, MediaAsset, Post
from autoposter.services.appconfig import current as cfg

#: Die ersten drei sind laut Dokumentation Pflicht („Always include these
#: default scopes"). Ohne `offline_access` liefert Fanvue KEIN Refresh-Token –
#: die Verbindung wäre nach einer Stunde tot und müsste von Hand erneuert
#: werden. Das fällt im Test nicht auf, sondern erst am nächsten Tag.
SCOPES = [
    "openid",
    "offline_access",
    "offline",
    "read:self",
    "read:media",
    "write:media",
    "read:post",
    "write:post",
    "read:insights",
]

MIN_PRICE_CENTS = 300
MAX_TEXT = 5000
PART_SIZE = 8 * 1024 * 1024



class FanvueAdapter:
    platform = "fanvue"

    def __init__(self) -> None:
        self.base = settings.fanvue_api_base.rstrip("/")

    # ------------------------------------------------------------------ #
    def limits(self) -> ChannelLimits:
        return ChannelLimits(
            max_chars=MAX_TEXT,
            max_images_per_post=20,
            mime_whitelist=["image/jpeg", "image/png", "image/webp"],
            supports_text_only=True,
            supports_alt_text=False,
            supports_platform_scheduling=True,
            supports_price=True,
            max_image_bytes=50 * 1024 * 1024,
        )

    def _headers(self, cred: Credentials) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {cred.access_token}",
            "X-Fanvue-API-Version": cfg("fanvue_api_version"),
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------ #
    # OAuth
    # ------------------------------------------------------------------ #
    def authorize_url(self, app: OAuthApp, state: str, code_challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": app.client_id,
            "redirect_uri": app.redirect_uri("fanvue"),
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{settings.fanvue_oauth_authorize_url}?{urlencode(params)}"

    @staticmethod
    def _client_auth(app: OAuthApp) -> tuple:
        """Zugangsdaten für den Token-Endpunkt: Header und zusätzliche Felder.

        Fanvue registriert vertrauliche Clients mit `client_secret_basic` – die
        Zugangsdaten gehören also in den Basic-Auth-Header, NICHT in den Rumpf.
        Im Rumpf (`client_secret_post`) lehnt der Token-Endpunkt mit
        `invalid_client` ab; die Dokumentation nennt das ausdrücklich als
        häufigsten Fehler, weil viele Bibliotheken so voreingestellt sind.

        Öffentliche Clients ohne Secret schicken gar keine Anmeldung, sondern
        nur die client_id – dort beglaubigt der code_verifier den Tausch.
        """
        if app.client_secret:
            raw = f"{app.client_id}:{app.client_secret}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}, {}
        return {}, {"client_id": app.client_id}

    async def exchange_code(
        self, app: OAuthApp, code: str, code_verifier: str
    ) -> Credentials:
        headers, extra = self._client_auth(app)
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": app.redirect_uri("fanvue"),
            "code_verifier": code_verifier,
            **extra,
        }
        async with http_client() as client:
            resp = await client.post(
                settings.fanvue_oauth_token_url, data=data, headers=headers
            )
            raise_for_response(resp, "Fanvue Token-Tausch")
            payload = resp.json()
        cred = self._credentials_from_payload(payload)
        cred.external_user_id = await self._fetch_self_uuid(cred)
        return cred

    async def refresh(self, app: OAuthApp, cred: Credentials) -> Credentials:
        if not cred.refresh_token:
            raise AdapterError("Kein Refresh-Token vorhanden", needs_reauth=True)
        headers, extra = self._client_auth(app)
        data = {
            "grant_type": "refresh_token",
            "refresh_token": cred.refresh_token,
            **extra,
        }
        async with http_client() as client:
            resp = await client.post(
                settings.fanvue_oauth_token_url, data=data, headers=headers
            )
            raise_for_response(resp, "Fanvue Token-Refresh")
            payload = resp.json()
        new_cred = self._credentials_from_payload(payload)
        new_cred.refresh_token = payload.get("refresh_token") or cred.refresh_token
        new_cred.external_user_id = cred.external_user_id
        return new_cred

    @staticmethod
    def _credentials_from_payload(payload: Dict[str, Any]) -> Credentials:
        expires_in = int(payload.get("expires_in", 3600))
        return Credentials(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60),
            scopes=str(payload.get("scope", "")).split(),
        )

    async def _fetch_self_uuid(self, cred: Credentials) -> Optional[str]:
        async with http_client() as client:
            resp = await client.get(f"{self.base}/users/me", headers=self._headers(cred))
            if resp.status_code >= 400:
                return None
            data = resp.json()
            return str(data.get("uuid") or data.get("userUuid") or data.get("id") or "") or None

    async def verify(self, app: OAuthApp, cred: Credentials) -> AccountInfo:
        """Prüft Token und Pflicht-Header gegen /users/me."""
        async with http_client(timeout=30.0) as client:
            resp = await client.get(f"{self.base}/users/me", headers=self._headers(cred))
        if resp.status_code == 401:
            return AccountInfo(ok=False, message="Token abgelehnt (401). Kanal neu verbinden.")
        if resp.status_code == 403:
            return AccountInfo(ok=False, message="Fehlende Scopes (403).")
        if resp.status_code == 410:
            return AccountInfo(
                ok=False,
                message=(
                    "API-Version abgekündigt (410). Unter Einstellungen die neue "
                    "Fanvue-API-Version eintragen."
                ),
            )
        if resp.status_code >= 400:
            return AccountInfo(ok=False, message=f"Fanvue antwortete {resp.status_code}: {resp.text[:180]}")

        data = resp.json()
        handle = str(data.get("handle") or data.get("username") or "")
        return AccountInfo(
            ok=True,
            account_id=str(data.get("uuid") or data.get("userUuid") or data.get("id") or ""),
            username=handle,
            message=f"Verbunden als {handle}" if handle else "Verbunden",
            raw=data,
        )

    # ------------------------------------------------------------------ #
    # Medien
    # ------------------------------------------------------------------ #
    async def upload_media(self, cred: Credentials, asset: MediaAsset, data: bytes) -> MediaRef:
        headers = self._headers(cred)
        parts_count = max(1, -(-len(data) // PART_SIZE))

        async with http_client(timeout=300.0) as client:
            # 1) Upload-Session anlegen (Media-Record + S3-Multipart)
            create = await client.post(
                f"{self.base}/media/upload-sessions",
                headers={**headers, "Content-Type": "application/json"},
                content=json.dumps(
                    {
                        "filename": asset.filename,
                        "mimeType": asset.mime,
                        "sizeInBytes": len(data),
                        "partsCount": parts_count,
                    }
                ),
            )
            raise_for_response(create, "Fanvue Upload-Session anlegen")
            session = create.json()

            media_uuid = str(
                session.get("mediaUuid") or session.get("uuid") or session.get("media", {}).get("uuid", "")
            )
            upload_id = session.get("uploadId") or session.get("sessionUuid")
            urls = session.get("partUrls") or session.get("urls") or []
            if not media_uuid:
                raise AdapterError(f"Fanvue lieferte keine mediaUuid: {create.text[:300]}")

            # 2) Teile direkt nach S3 laden (presigned URLs, ohne Auth-Header!)
            etags: List[Dict[str, Any]] = []
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as s3:
                for index in range(parts_count):
                    chunk = data[index * PART_SIZE : (index + 1) * PART_SIZE]
                    if index >= len(urls):
                        raise AdapterError(
                            "Fanvue lieferte zu wenige presigned URLs für den Upload"
                        )
                    entry = urls[index]
                    url = entry if isinstance(entry, str) else entry.get("url")
                    put = await s3.put(url, content=chunk,
                                       headers={"Content-Type": asset.mime})
                    if put.status_code >= 400:
                        raise AdapterError(
                            f"S3-Teil-Upload #{index} fehlgeschlagen ({put.status_code})",
                            retryable=True,
                        )
                    etags.append(
                        {"partNumber": index + 1, "eTag": put.headers.get("ETag", "").strip('"')}
                    )

            # 3) Session abschließen
            complete = await client.post(
                f"{self.base}/media/upload-sessions/{upload_id or media_uuid}/complete",
                headers={**headers, "Content-Type": "application/json"},
                content=json.dumps({"mediaUuid": media_uuid, "parts": etags}),
            )
            raise_for_response(complete, "Fanvue Upload-Session abschließen")

            # 4) Auf 'ready' warten — vorher darf nicht gepostet werden
            for _ in range(60):
                status = await client.get(
                    f"{self.base}/media/{media_uuid}", headers=headers
                )
                if status.status_code >= 400:
                    await asyncio.sleep(3)
                    continue
                state = str(status.json().get("status", "")).lower()
                if state in ("ready", "processed", "available", "active", ""):
                    break
                if state in ("failed", "error"):
                    raise AdapterError(f"Fanvue Medienverarbeitung fehlgeschlagen: {media_uuid}")
                await asyncio.sleep(3)
            else:
                raise AdapterError("Fanvue Medienverarbeitung dauert zu lange", retryable=True)

        return MediaRef(asset_id=str(asset.id), external_id=media_uuid)

    # ------------------------------------------------------------------ #
    # Publish
    # ------------------------------------------------------------------ #
    async def publish(
        self, cred: Credentials, channel: Channel, post: Post, media: List[MediaRef]
    ) -> PublishResult:
        from autoposter.services.fanvue_channels import audience_for
        try:
            audience = audience_for(channel, post.audience)
        except ValueError as exc:
            raise AdapterError(str(exc)) from exc
        body: Dict[str, Any] = {
            "audience": audience,
        }
        text = (post.body_text or "").strip()
        tags = " ".join(f"#{t.lstrip('#')}" for t in (post.hashtags or []))
        if tags:
            text = f"{text}\n\n{tags}".strip()
        if text:
            body["text"] = text[:MAX_TEXT]
        if media:
            body["mediaUuids"] = [m.external_id for m in media]
        if post.media_preview_id:
            preview = next(
                (m.external_id for m in media if m.asset_id == str(post.media_preview_id)), None
            )
            if preview:
                body["mediaPreviewUuid"] = preview
        if post.price_cents:
            if not media:
                raise AdapterError("Bezahlte Posts benötigen Medien")
            if post.price_cents < MIN_PRICE_CENTS:
                raise AdapterError(f"Mindestpreis sind {MIN_PRICE_CENTS} Cent")
            body["price"] = post.price_cents
        if channel.platform_side_scheduling and post.scheduled_at:
            if post.scheduled_at > datetime.now(timezone.utc):
                body["publishAt"] = post.scheduled_at.astimezone(timezone.utc).isoformat()
        if post.expires_at:
            body["expiresAt"] = post.expires_at.astimezone(timezone.utc).isoformat()

        async with http_client() as client:
            resp = await client.post(
                f"{self.base}/posts",
                headers={**self._headers(cred), "Content-Type": "application/json"},
                content=json.dumps(body),
            )
            raise_for_response(resp, "Fanvue Post erstellen")
            payload = resp.json()

        post_uuid = str(payload.get("uuid") or payload.get("data", {}).get("uuid", ""))

        if post.pin_after_publish and post_uuid:
            async with http_client() as client:
                pin = await client.post(
                    f"{self.base}/posts/{post_uuid}/pin", headers=self._headers(cred)
                )
                if pin.status_code >= 400 and pin.status_code != 404:
                    raise_for_response(pin, "Fanvue Post anpinnen")

        handle = channel.handle.lstrip("@")
        return PublishResult(
            external_post_id=post_uuid,
            external_url=f"https://www.fanvue.com/{handle}/post/{post_uuid}" if handle else "",
            http_status=resp.status_code,
            request_id=resp.headers.get("x-request-id", ""),
            raw=payload,
        )

    async def bulk_publish(
        self, cred: Credentials, items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """POST /agencies/posts — HTTP 200 heißt NICHT, dass alle Items erfolgreich waren.
        Jedes Item wird index-aligned einzeln ausgewertet."""
        async with http_client(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base}/agencies/posts",
                headers={**self._headers(cred), "Content-Type": "application/json"},
                content=json.dumps({"posts": items[:100]}),
            )
            raise_for_response(resp, "Fanvue Bulk-Post")
            return list(resp.json().get("results", []))

    async def delete(self, cred: Credentials, external_post_id: str) -> None:
        async with http_client() as client:
            resp = await client.delete(
                f"{self.base}/posts/{external_post_id}", headers=self._headers(cred)
            )
            if resp.status_code != 404:
                raise_for_response(resp, "Fanvue Post löschen")

    async def fetch_metrics(
        self, cred: Credentials, external_post_id: str
    ) -> Optional[Dict[str, Any]]:
        async with http_client() as client:
            resp = await client.get(
                f"{self.base}/posts/{external_post_id}", headers=self._headers(cred)
            )
            if resp.status_code >= 400:
                return None
            data = resp.json()
        return {
            "likes": data.get("likesCount", data.get("likes")),
            "comments": data.get("commentsCount", data.get("comments")),
            "tips": data.get("tipsCount"),
            "purchases": data.get("purchaseCount"),
        }
