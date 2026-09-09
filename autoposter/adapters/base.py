"""Plattform-Abstraktion. Außerhalb dieses Pakets steht kein plattformspezifischer Code."""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

import httpx

from autoposter.models import Channel, MediaAsset, Post


class AdapterError(RuntimeError):
    """Basisklasse für Adapterfehler."""

    def __init__(self, message: str, *, status: Optional[int] = None, retryable: bool = False,
                 retry_after: Optional[int] = None, needs_reauth: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after
        self.needs_reauth = needs_reauth


class ValidationIssue(Exception):
    pass


@dataclass
class OAuthApp:
    """Zugangsdaten einer OAuth-App.

    Bei X gehört zu jeder App genau ein Account – deshalb wird die App überall
    explizit durchgereicht statt aus einer globalen Einstellung gelesen.
    """

    client_id: str
    client_secret: str = ""
    redirect_base: str = ""
    api_version: str = ""
    #: Nur zur Anzeige, damit man App und Kanal zuordnen kann.
    name: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id)

    def redirect_uri(self, platform: str) -> str:
        return "{}/api/v1/oauth/{}/callback".format(
            str(self.redirect_base).rstrip("/"), platform
        )


@dataclass
class AccountInfo:
    """Ergebnis eines Verbindungstests."""

    ok: bool
    account_id: str = ""
    username: str = ""
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Credentials:
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    scopes: List[str] = field(default_factory=list)
    external_user_id: Optional[str] = None
    raw_meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaRef:
    asset_id: str
    external_id: str
    expires_at: Optional[datetime] = None


@dataclass
class PublishResult:
    external_post_id: str
    external_url: str = ""
    http_status: int = 200
    request_id: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelLimits:
    max_chars: int
    max_images_per_post: int
    mime_whitelist: List[str]
    supports_text_only: bool
    supports_alt_text: bool
    supports_platform_scheduling: bool
    supports_price: bool
    max_image_bytes: int


@dataclass
class RateState:
    limit: Optional[int] = None
    remaining: Optional[int] = None
    reset_at: Optional[datetime] = None


class ChannelAdapter(Protocol):
    platform: str

    def limits(self) -> ChannelLimits: ...

    def authorize_url(self, app: OAuthApp, state: str, code_challenge: str) -> str: ...

    async def exchange_code(
        self, app: OAuthApp, code: str, code_verifier: str
    ) -> Credentials: ...

    async def refresh(self, app: OAuthApp, cred: Credentials) -> Credentials: ...

    async def verify(self, app: OAuthApp, cred: Credentials) -> AccountInfo: ...

    async def upload_media(self, cred: Credentials, asset: MediaAsset, data: bytes) -> MediaRef: ...

    async def publish(
        self, cred: Credentials, channel: Channel, post: Post, media: List[MediaRef]
    ) -> PublishResult: ...

    async def delete(self, cred: Credentials, external_post_id: str) -> None: ...

    async def fetch_metrics(
        self, cred: Credentials, external_post_id: str
    ) -> Optional[Dict[str, Any]]: ...


# --------------------------------------------------------------------------- #
# Gemeinsame HTTP-Helfer
# --------------------------------------------------------------------------- #
def parse_retry_after(response: httpx.Response) -> int:
    raw = response.headers.get("retry-after")
    if raw:
        try:
            return max(1, int(float(raw)))
        except ValueError:
            pass
    reset = response.headers.get("x-ratelimit-reset")
    if reset:
        try:
            delta = int(float(reset)) - int(datetime.now(timezone.utc).timestamp())
            return max(1, min(delta, 900))
        except ValueError:
            pass
    return 30


def rate_state(response: httpx.Response) -> RateState:
    def _int(name: str) -> Optional[int]:
        v = response.headers.get(name)
        try:
            return int(float(v)) if v is not None else None
        except ValueError:
            return None

    reset_ts = _int("x-ratelimit-reset")
    return RateState(
        limit=_int("x-ratelimit-limit"),
        remaining=_int("x-ratelimit-remaining"),
        reset_at=datetime.fromtimestamp(reset_ts, tz=timezone.utc) if reset_ts else None,
    )


def raise_for_response(response: httpx.Response, context: str) -> None:
    """Einheitliche Fehlerklassifikation für alle Adapter."""
    if response.status_code < 400:
        return
    body = response.text[:800]
    if response.status_code in (401,):
        raise AdapterError(
            f"{context}: Zugangsdaten abgelehnt (401). {body}",
            status=401,
            needs_reauth=True,
        )
    if response.status_code == 403:
        raise AdapterError(
            f"{context}: Fehlende Berechtigung/Scope (403). {body}", status=403
        )
    if response.status_code == 410:
        raise AdapterError(
            f"{context}: API-Version abgelaufen (410). Bitte FANVUE_API_VERSION aktualisieren. {body}",
            status=410,
        )
    if response.status_code == 429:
        raise AdapterError(
            f"{context}: Rate-Limit erreicht (429). {body}",
            status=429,
            retryable=True,
            retry_after=parse_retry_after(response),
        )
    if response.status_code >= 500:
        raise AdapterError(
            f"{context}: Serverfehler ({response.status_code}). {body}",
            status=response.status_code,
            retryable=True,
            retry_after=15,
        )
    raise AdapterError(f"{context}: Anfrage abgelehnt ({response.status_code}). {body}",
                       status=response.status_code)


async def backoff_sleep(attempt: int, base: float = 1.5) -> None:
    delay = min(60.0, base ** attempt) + random.uniform(0, 1.0)
    await asyncio.sleep(delay)


def http_client(timeout: float = 60.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=15.0),
        follow_redirects=False,
        headers={"User-Agent": "MP-CreatorStudio-AutoPost/1.0"},
    )
