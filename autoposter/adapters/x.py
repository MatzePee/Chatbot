"""Adapter für X (Twitter) API v2.

- OAuth 2.0 Authorization Code mit PKCE, Refresh-Token-Rotation.
- Medien-Upload über die v2-Endpunkte initialize, {id}/append und {id}/finalize
  mit anschließendem STATUS-Polling.
- Bildersets mit mehr als 4 Bildern werden vom Publisher als Self-Thread zerlegt.
"""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

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

SCOPES = [
    "tweet.read",
    "tweet.write",
    "users.read",
    "media.write",
    "offline.access",
]

#: Konservative Voreinstellungen je Tarif (Posts pro 24 h). Im UI überschreibbar,
#: da X die tatsächlichen Limits nicht vollständig dokumentiert.
TIER_DAILY_POST_QUOTA = {"free": 17, "basic": 100, "pro": 300}

CHUNK_SIZE = 4 * 1024 * 1024



class XAdapter:
    platform = "x"

    def __init__(self) -> None:
        self.base = settings.x_api_base.rstrip("/")

    # ------------------------------------------------------------------ #
    # Meta
    # ------------------------------------------------------------------ #
    def limits(self) -> ChannelLimits:
        return ChannelLimits(
            max_chars=280,
            max_images_per_post=4,
            mime_whitelist=["image/jpeg", "image/png", "image/webp", "image/gif"],
            supports_text_only=True,
            supports_alt_text=True,
            supports_platform_scheduling=False,
            supports_price=False,
            max_image_bytes=5 * 1024 * 1024,
        )

    def default_daily_quota(self) -> int:
        return TIER_DAILY_POST_QUOTA.get(cfg("x_api_tier"), 100)

    # ------------------------------------------------------------------ #
    # OAuth
    # ------------------------------------------------------------------ #
    def authorize_url(self, app: OAuthApp, state: str, code_challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": app.client_id,
            "redirect_uri": app.redirect_uri("x"),
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{settings.x_oauth_authorize_url}?{urlencode(params)}"

    @staticmethod
    def _basic_auth(app: OAuthApp) -> Dict[str, str]:
        raw = "{}:{}".format(app.client_id, app.client_secret).encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    async def exchange_code(
        self, app: OAuthApp, code: str, code_verifier: str
    ) -> Credentials:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": app.redirect_uri("x"),
            "code_verifier": code_verifier,
            "client_id": app.client_id,
        }
        async with http_client() as client:
            resp = await client.post(
                settings.x_oauth_token_url, data=data, headers=self._basic_auth(app)
            )
            raise_for_response(resp, "X Token-Tausch")
            payload = resp.json()
        cred = self._credentials_from_payload(payload)
        cred.external_user_id = await self._fetch_user_id(cred)
        return cred

    async def refresh(self, app: OAuthApp, cred: Credentials) -> Credentials:
        if not cred.refresh_token:
            raise AdapterError("Kein Refresh-Token vorhanden", needs_reauth=True)
        data = {
            "grant_type": "refresh_token",
            "refresh_token": cred.refresh_token,
            "client_id": app.client_id,
        }
        async with http_client() as client:
            resp = await client.post(
                settings.x_oauth_token_url, data=data, headers=self._basic_auth(app)
            )
            raise_for_response(resp, "X Token-Refresh")
            payload = resp.json()
        new_cred = self._credentials_from_payload(payload)
        # Refresh-Token-Rotation: X gibt ein neues zurück, das alte wird ungültig.
        new_cred.refresh_token = payload.get("refresh_token") or cred.refresh_token
        new_cred.external_user_id = cred.external_user_id
        return new_cred

    @staticmethod
    def _credentials_from_payload(payload: Dict[str, Any]) -> Credentials:
        expires_in = int(payload.get("expires_in", 7200))
        return Credentials(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60),
            scopes=str(payload.get("scope", "")).split(),
            raw_meta={"token_type": payload.get("token_type", "bearer")},
        )

    async def _fetch_user_id(self, cred: Credentials) -> Optional[str]:
        async with http_client() as client:
            resp = await client.get(
                f"{self.base}/2/users/me", headers=self._auth(cred)
            )
            if resp.status_code >= 400:
                return None
            return str(resp.json().get("data", {}).get("id"))

    async def verify(self, app: OAuthApp, cred: Credentials) -> AccountInfo:
        """Prüft, ob Token und App zusammenpassen, und liefert den Account."""
        async with http_client(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base}/2/users/me",
                headers=self._auth(cred),
                params={"user.fields": "username,name,verified"},
            )
        if resp.status_code == 401:
            return AccountInfo(ok=False, message="Token abgelehnt (401). Kanal neu verbinden.")
        if resp.status_code == 403:
            return AccountInfo(
                ok=False,
                message="Zugriff verweigert (403). Fehlen Scopes oder passt der Tarif nicht?",
            )
        if resp.status_code == 429:
            return AccountInfo(ok=False, message="Rate-Limit erreicht (429). Später erneut testen.")
        if resp.status_code >= 400:
            return AccountInfo(ok=False, message=f"X antwortete {resp.status_code}: {resp.text[:180]}")

        data = resp.json().get("data", {})
        username = data.get("username", "")
        return AccountInfo(
            ok=True,
            account_id=str(data.get("id", "")),
            username=username,
            message=f"Verbunden als @{username}" if username else "Verbunden",
            raw=data,
        )

    @staticmethod
    def _auth(cred: Credentials) -> Dict[str, str]:
        return {"Authorization": f"Bearer {cred.access_token}"}

    # ------------------------------------------------------------------ #
    # Medien
    # ------------------------------------------------------------------ #
    async def upload_media(
        self, cred: Credentials, asset: MediaAsset, data: bytes, *, include_alt_text: bool = True
    ) -> MediaRef:
        url = f"{self.base}/2/media/upload"
        headers = self._auth(cred)

        async with http_client(timeout=180.0) as client:
            # INIT
            init = await client.post(
                f"{url}/initialize",
                headers=headers,
                json={
                    "total_bytes": len(data),
                    "media_type": asset.mime,
                    "media_category": "tweet_gif" if asset.mime == "image/gif" else "tweet_image",
                },
            )
            raise_for_response(init, "X Media INIT")
            media_id = str(init.json().get("data", {}).get("id") or init.json().get("media_id_string"))
            if not media_id or media_id == "None":
                raise AdapterError(f"X Media INIT lieferte keine media_id: {init.text[:300]}")

            # APPEND
            for index, offset in enumerate(range(0, len(data), CHUNK_SIZE)):
                chunk = data[offset : offset + CHUNK_SIZE]
                append = await client.post(
                    f"{url}/{media_id}/append",
                    headers=headers,
                    data={"segment_index": str(index)},
                    files={"media": ("chunk", chunk, "application/octet-stream")},
                )
                raise_for_response(append, f"X Media APPEND #{index}")

            # FINALIZE
            finalize = await client.post(
                f"{url}/{media_id}/finalize", headers=headers
            )
            raise_for_response(finalize, "X Media FINALIZE")
            info = finalize.json().get("data", finalize.json())

            # STATUS-Polling, falls Verarbeitung nötig
            processing = info.get("processing_info")
            waited = 0
            while processing and processing.get("state") in ("pending", "in_progress"):
                wait = max(1, int(processing.get("check_after_secs", 3)))
                waited += wait
                if waited > 300:
                    raise AdapterError("X Media-Verarbeitung dauert zu lange", retryable=True)
                await asyncio.sleep(wait)
                status = await client.get(
                    url, headers=headers, params={"command": "STATUS", "media_id": media_id}
                )
                raise_for_response(status, "X Media STATUS")
                processing = status.json().get("data", status.json()).get("processing_info")

            if processing and processing.get("state") == "failed":
                raise AdapterError(f"X Media-Verarbeitung fehlgeschlagen: {processing}")

            # X zeigt bei hinterlegter Bildbeschreibung sein eigenes ALT-Abzeichen.
            alt = (asset.caption_hint or asset.ai_description or "")[:1000]
            if include_alt_text and alt:
                meta = await client.post(
                    f"{self.base}/2/media/metadata",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"id": media_id, "metadata": {"alt_text": {"text": alt}}},
                )
                if meta.status_code >= 400 and meta.status_code not in (403, 404):
                    raise_for_response(meta, "X Alt-Text")

        return MediaRef(asset_id=str(asset.id), external_id=media_id,
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=20))

    # ------------------------------------------------------------------ #
    # Publish
    # ------------------------------------------------------------------ #
    async def create_comment(self, cred: Credentials, post_id: str, text: str) -> str:
        if not post_id or post_id.startswith("dryrun-"):
            raise AdapterError("Kein veröffentlichtes X-Bild für den Kommentar vorhanden")
        async with http_client() as client:
            response = await client.post(
                f"{self.base}/2/tweets", headers=self._auth(cred),
                json={"text": text, "reply": {"in_reply_to_tweet_id": post_id}},
            )
            raise_for_response(response, "X Auto-Kommentar")
            comment_id = str(response.json().get("data", {}).get("id") or "")
        if not comment_id:
            raise AdapterError("X hat keine Kommentar-ID bestätigt; bitte auf X prüfen.")
        return comment_id

    async def publish(
        self, cred: Credentials, channel: Channel, post: Post, media: List[MediaRef]
    ) -> PublishResult:
        body: Dict[str, Any] = {"text": self._compose_text(post)}
        if media:
            body["media"] = {"media_ids": [m.external_id for m in media[:4]]}
            if channel.x_made_with_ai:
                body["made_with_ai"] = True
        if post.reply_settings and post.reply_settings != "everyone":
            body["reply_settings"] = post.reply_settings
        if (post.generation_meta or {}).get("in_reply_to_tweet_id"):
            body["reply"] = {
                "in_reply_to_tweet_id": post.generation_meta["in_reply_to_tweet_id"]
            }
        if post.quote_of_url:
            tweet_id = post.quote_of_url.rstrip("/").split("/")[-1]
            if tweet_id.isdigit():
                body["quote_tweet_id"] = tweet_id

        async with http_client() as client:
            resp = await client.post(
                f"{self.base}/2/tweets",
                headers={**self._auth(cred), "Content-Type": "application/json"},
                content=json.dumps(body),
            )
            raise_for_response(resp, "X Tweet erstellen")
            payload = resp.json()

        tweet_id = str(payload.get("data", {}).get("id", ""))
        handle = channel.handle.lstrip("@") or "i"
        return PublishResult(
            external_post_id=tweet_id,
            external_url=f"https://x.com/{handle}/status/{tweet_id}",
            http_status=resp.status_code,
            request_id=resp.headers.get("x-transaction-id", ""),
            raw=payload,
        )

    @staticmethod
    def _compose_text(post: Post) -> str:
        text = (post.body_text or "").strip()
        tags = " ".join(f"#{t.lstrip('#')}" for t in (post.hashtags or []))
        if tags and tags not in text:
            candidate = f"{text}\n\n{tags}".strip()
            if len(candidate) <= 280:
                return candidate
        return text[:280]

    async def delete(self, cred: Credentials, external_post_id: str) -> None:
        async with http_client() as client:
            resp = await client.delete(
                f"{self.base}/2/tweets/{external_post_id}", headers=self._auth(cred)
            )
            if resp.status_code != 404:
                raise_for_response(resp, "X Tweet löschen")

    async def fetch_metrics(
        self, cred: Credentials, external_post_id: str
    ) -> Optional[Dict[str, Any]]:
        async with http_client() as client:
            resp = await client.get(
                f"{self.base}/2/tweets/{external_post_id}",
                headers=self._auth(cred),
                params={"tweet.fields": "public_metrics,non_public_metrics,created_at"},
            )
            if resp.status_code >= 400:
                return None
            data = resp.json().get("data", {})
        metrics = dict(data.get("public_metrics", {}))
        metrics.update(data.get("non_public_metrics", {}) or {})
        return metrics or None
