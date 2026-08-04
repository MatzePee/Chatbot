"""Fanvue-API-Client inkl. OAuth 2.0 (Authorization Code + PKCE).

Doku: https://api.fanvue.com/docs
- Authorize: https://auth.fanvue.com/oauth2/auth
- Token:     https://auth.fanvue.com/oauth2/token
- API-Base:  https://api.fanvue.com
- Pflicht-Header: X-Fanvue-API-Version
Refresh-Tokens rotieren und sind Single-Use -> wir speichern nach jedem Refresh
sofort das neue Token und serialisieren Refreshes ueber ein Lock.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import threading
import time
from typing import Any, Optional

import httpx

from . import db

AUTH_BASE = "https://auth.fanvue.com"
API_BASE = "https://api.fanvue.com"
AUTHORIZE_URL = f"{AUTH_BASE}/oauth2/auth"
TOKEN_URL = f"{AUTH_BASE}/oauth2/token"

# Scopes, die der Bot braucht.
# Fanvue-Pflicht-Scopes: openid, offline_access, offline (Refresh-Token / Langzeitzugriff).
# read:media -> Vault-Ordner/Medien fuer PPV lesen.
SCOPES = ["openid", "offline_access", "offline",
          "read:self", "read:chat", "write:chat", "read:fan", "read:media", "read:insights"]

_refresh_lock = threading.Lock()


class FanvueError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message


class NotAuthenticated(FanvueError):
    def __init__(self, message: str = "Nicht mit Fanvue verbunden"):
        super().__init__(401, message)


# ------------------------------------------------------------------ PKCE utils
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_pkce() -> tuple[str, str]:
    """Gibt (code_verifier, code_challenge) zurueck."""
    verifier = _b64url(os.urandom(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def build_authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Erzwingt eine frische Einwilligung, damit NEU hinzugefuegte Scopes
        # (z.B. read:fan, read:insights) auch tatsaechlich gewaehrt werden und
        # Fanvue nicht den alten, bereits zugestimmten Scope-Satz wiederverwendet.
        "prompt": "consent",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def new_state() -> str:
    return secrets.token_urlsafe(24)


# --------------------------------------------------------------- Token handling
def exchange_code(code: str, code_verifier: str) -> dict[str, Any]:
    """Tauscht den Authorization Code gegen Tokens und speichert sie."""
    client_id = db.get_setting("fanvue_client_id")
    client_secret = db.get_setting("fanvue_client_secret")
    redirect_uri = db.get_setting("fanvue_redirect_uri")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    # Fanvue erwartet die Client-Zugangsdaten per HTTP-Basic-Auth (client_secret_basic)
    auth = (client_id, client_secret) if client_secret else None
    resp = httpx.post(TOKEN_URL, data=data, auth=auth, timeout=30)
    if resp.status_code != 200:
        raise FanvueError(resp.status_code, f"Token-Exchange fehlgeschlagen: {resp.text}")
    payload = resp.json()
    _store_token_response(payload)
    _fetch_and_store_account()
    return payload


def _store_token_response(payload: dict[str, Any]) -> None:
    expires_in = payload.get("expires_in", 3600)
    db.save_tokens(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", ""),
        expires_at=time.time() + float(expires_in) - 60,  # 60s Puffer
        scope=payload.get("scope", ""),
    )


def _refresh_token() -> None:
    """Erneuert den Access-Token via Refresh-Token (serialisiert)."""
    with _refresh_lock:
        tokens = db.get_tokens()
        if not tokens or not tokens["refresh_token"]:
            raise NotAuthenticated("Kein Refresh-Token vorhanden - bitte neu verbinden")
        # Nach dem Lock erneut pruefen, ob ein anderer Thread bereits erneuert hat
        if tokens["expires_at"] and tokens["expires_at"] > time.time():
            return
        client_id = db.get_setting("fanvue_client_id")
        client_secret = db.get_setting("fanvue_client_secret")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
        }
        # Client-Zugangsdaten per HTTP-Basic-Auth (client_secret_basic)
        auth = (client_id, client_secret) if client_secret else None
        resp = httpx.post(TOKEN_URL, data=data, auth=auth, timeout=30)
        if resp.status_code != 200:
            db.log("error", "oauth", "Refresh fehlgeschlagen", resp.text)
            # invalid_grant -> Kette gebrochen, neu autorisieren
            raise NotAuthenticated("Refresh fehlgeschlagen - bitte Fanvue neu verbinden")
        _store_token_response(resp.json())
        db.log("info", "oauth", "Access-Token erneuert")


def get_access_token() -> str:
    tokens = db.get_tokens()
    if not tokens or not tokens["access_token"]:
        raise NotAuthenticated()
    if not tokens["expires_at"] or tokens["expires_at"] <= time.time():
        _refresh_token()
        tokens = db.get_tokens()
    return tokens["access_token"]


def is_connected() -> bool:
    tokens = db.get_tokens()
    return bool(tokens and tokens["refresh_token"])


# -------------------------------------------------------------- HTTP low-level
def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "X-Fanvue-API-Version": db.get_setting("fanvue_api_version", "2025-06-26"),
        "Accept": "application/json",
    }


# Voruebergehende Fanvue/CloudFront-Fehler, die einen erneuten Versuch wert sind.
_TRANSIENT_STATUS = {502, 503, 504}
# getrennte Timeouts: schnelles Verbinden, laengeres Lesen (Vault/Medien koennen dauern)
_TIMEOUT = httpx.Timeout(connect=10.0, read=45.0, write=30.0, pool=10.0)


def _request(method: str, path: str, *, params: dict | None = None,
             json_body: dict | None = None, _retry: bool = True,
             _attempts: int = 3) -> httpx.Response:
    url = f"{API_BASE}{path}"
    last_err = ""
    for attempt in range(_attempts):
        try:
            resp = httpx.request(method, url, headers=_headers(), params=params,
                                 json=json_body, timeout=_TIMEOUT)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Netzwerk-/Timeout-Problem -> mit Backoff erneut versuchen
            last_err = str(exc) or exc.__class__.__name__
            if attempt < _attempts - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise FanvueError(504, "Fanvue-API nicht erreichbar (Timeout/Netzwerk) – "
                              "bitte in ein paar Minuten erneut versuchen") from exc

        if resp.status_code == 401 and _retry:
            # Token evtl. abgelaufen -> einmal erneuern und wiederholen
            _refresh_token()
            return _request(method, path, params=params, json_body=json_body, _retry=False)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            db.log("warn", "system", f"Rate-Limit erreicht, warte {retry_after}s")
            time.sleep(min(retry_after, 30))
            if attempt < _attempts - 1:
                continue
        if resp.status_code in _TRANSIENT_STATUS and attempt < _attempts - 1:
            db.log("warn", "system",
                   f"Fanvue {resp.status_code} (Versuch {attempt + 1}/{_attempts}) – neuer Versuch")
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code >= 500:
            # Server-/Gateway-Fehler: kurze, verstaendliche Meldung statt roher HTML-Seite
            raise FanvueError(resp.status_code,
                              f"Fanvue-Serverfehler {resp.status_code} (Gateway/Timeout) – "
                              "vorübergehendes Problem bei Fanvue, bitte später erneut versuchen")
        if resp.status_code >= 400:
            raise FanvueError(resp.status_code, (resp.text or "")[:500])
        return resp
    # Alle Versuche erschoepft (nur transiente Fehler)
    raise FanvueError(504, f"Fanvue-API nicht erreichbar – {last_err or 'wiederholte Timeouts'}")


# ------------------------------------------------------------------- API calls
def get_me() -> dict[str, Any]:
    return _request("GET", "/users/me").json()


def account_uuid() -> str:
    """Eigene Konto-Kennung - holt sie bei Bedarf nach.

    Sie wurde bisher NUR einmal beim OAuth-Verbinden geschrieben. Schlug das
    fehl, blieb sie dauerhaft leer - und damit konnte der Bot eigene
    Nachrichten nicht mehr von Fan-Nachrichten unterscheiden. Folge: im Prompt
    landete der komplette Verlauf als 'user', das Modell sah seine eigenen
    Antworten als Fan-Text und wusste nie, was es bereits geschrieben hatte.
    """
    tokens = db.get_tokens()
    uuid = (tokens["account_uuid"] if tokens else "") or ""
    if uuid or not (tokens and tokens["access_token"]):
        return uuid
    _fetch_and_store_account()
    tokens = db.get_tokens()
    return (tokens["account_uuid"] if tokens else "") or ""


def _fetch_and_store_account() -> None:
    try:
        me = get_me()
        # Fanvue liefert die Kennung je nach Endpunkt unterschiedlich benannt
        uuid = (me.get("uuid") or me.get("id") or me.get("userUuid")
                or me.get("user_uuid") or "")
        handle = me.get("handle") or me.get("displayName") or ""
        if not uuid:
            db.log("warn", "oauth",
                   "Konto-Kennung fehlt in der Fanvue-Antwort – eigene Nachrichten "
                   "können nicht erkannt werden", str(list(me.keys()))[:200])
        if uuid:
            tokens = db.get_tokens()
            db.save_tokens(
                access_token=tokens["access_token"],
                refresh_token=tokens["refresh_token"],
                expires_at=tokens["expires_at"],
                scope=tokens["scope"] or "",
                account_uuid=uuid,
                account_handle=handle,
            )
            db.log("info", "oauth", f"Konto-Kennung geladen: {handle or uuid[:8]}", "")
    except Exception as exc:  # noqa: BLE001
        db.log("warn", "oauth", "Konnte Account-Infos nicht laden", str(exc))


def list_chats(filter_: str = "unread", page: int = 1, size: int = 15,
               custom_list_id: str = "") -> dict[str, Any]:
    params: dict[str, Any] = {"page": page, "size": size}
    if filter_:
        params["filter"] = filter_
    if custom_list_id:
        params["customListId"] = custom_list_id
    return _request("GET", "/chats", params=params).json()


def list_custom_lists() -> list[dict[str, Any]]:
    """Eigene, in Fanvue angelegte Listen (Custom Lists)."""
    resp = _request("GET", "/chats/lists/custom", params={"size": 50})
    return resp.json().get("data", [])


def list_custom_list_members(list_uuid: str, page: int = 1, size: int = 50) -> dict[str, Any]:
    """Alle Mitglieder einer Custom List (auch ohne bestehenden Chat)."""
    from urllib.parse import quote
    return _request("GET", f"/chats/lists/custom/{quote(list_uuid, safe='')}",
                    params={"page": page, "size": size}).json()


# --------------------------------------------------------------- Fan-Insights
def get_fan_insights(user_uuid: str) -> dict[str, Any]:
    """Ausgaben-/Abo-Insights zu einem Fan (Scope read:insights + read:fan)."""
    return _request("GET", f"/insights/fans/{user_uuid}").json()


def fan_insights_bulk(uuids: list[str]) -> dict[str, Any]:
    """Insights fuer viele Fans (bis 100 pro Request), Ergebnis nach UUID."""
    out: dict[str, Any] = {}
    for i in range(0, len(uuids), 100):
        chunk = uuids[i:i + 100]
        data = _request("POST", "/insights/fans/batch",
                        json_body={"userUuids": chunk}).json()
        if isinstance(data, dict):
            out.update(data)
    return out


# ------------------------------------------------------------------ Vault / PPV
def list_vault_folders() -> list[dict[str, Any]]:
    """Alle Vault-Ordner (nach Name adressiert): [{name, createdAt, mediaCount}]."""
    folders: list[dict[str, Any]] = []
    page = 1
    while True:
        data = _request("GET", "/vault/folders", params={"page": page, "size": 50}).json()
        folders.extend(data.get("data", []))
        if not data.get("pagination", {}).get("hasMore"):
            break
        page += 1
        if page > 20:  # Sicherheitslimit
            break
    return folders


def list_folder_media(folder_name: str, page: int = 1, size: int = 50,
                      media_type: str = "image",
                      variants: str = "thumbnail_gallery,thumbnail,main,blurred") -> dict[str, Any]:
    """Medien eines Ordners. folder_name wird URL-kodiert.

    WICHTIG: Die Varianten-URLs kommen nur zurueck, wenn man die gewuenschten
    Varianten-Typen ueber den `variants`-Query-Parameter explizit anfordert.
    """
    from urllib.parse import quote
    params: dict[str, Any] = {"page": page, "size": size}
    if media_type:
        params["mediaType"] = media_type
    if variants:
        params["variants"] = variants
    return _request("GET", f"/vault/folders/{quote(folder_name, safe='')}/media",
                    params=params).json()


def full_message_history(user_uuid: str, max_pages: int = 40) -> list[dict[str, Any]]:
    """Kompletter Chatverlauf (chronologisch, aelteste zuerst)."""
    msgs: list[dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        res = list_messages(user_uuid, size=50, mark_as_read=False, page=page)
        msgs.extend(res.get("data", []))
        if not res.get("pagination", {}).get("hasMore"):
            break
        page += 1
    return list(reversed(msgs))  # API liefert neueste zuerst -> drehen


def collect_purchased_ppv(user_uuid: str, me_uuid: str, max_pages: int = 100) -> list[dict[str, Any]]:
    """Sammelt alle vom Creator gesendeten, bezahlten UND gekauften Nachrichten.
    Rueckgabe: [{message_uuid, media_uuids: set, purchased_at, price_cents}]."""
    found: list[dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        res = list_messages(user_uuid, size=50, mark_as_read=False, page=page)
        for m in res.get("data", []):
            sender = (m.get("sender") or {}).get("uuid", "")
            if sender == me_uuid and m.get("pricing") and m.get("purchasedAt"):
                media = set(m.get("mediaUuids") or [])
                if media:
                    price = 0
                    try:
                        price = int((m.get("pricing") or {}).get("USD", {}).get("price", 0))
                    except (TypeError, ValueError):
                        price = 0
                    found.append({"message_uuid": m.get("uuid"), "media_uuids": media,
                                  "purchased_at": m.get("purchasedAt"), "price_cents": price})
        if not res.get("pagination", {}).get("hasMore"):
            break
        page += 1
    return found


def folder_media_uuids(folder_name: str, max_pages: int = 10) -> set[str]:
    """Alle Media-UUIDs eines Vault-Ordners (fuer die Zuordnung Kauf -> Set)."""
    uuids: set[str] = set()
    page = 1
    while page <= max_pages:
        r = list_folder_media(folder_name, page=page, size=50, media_type="")
        for it in r.get("data", []):
            if it.get("uuid"):
                uuids.add(it["uuid"])
        if not r.get("pagination", {}).get("hasMore"):
            break
        page += 1
    return uuids


# In-Process-Cache fuer den Medien->Ordner-Index (teuer zu bauen, fuer alle Fans gleich)
_media_index_cache: dict[str, Any] = {"ts": 0.0, "index": {}}


def media_folder_index(ttl: float = 900, ppv_only: bool = True,
                       force: bool = False) -> dict[str, set[str]]:
    """Baut {Ordnername: set(media_uuids)} ueber alle (PPV-)Vault-Ordner.
    Wird zwischengespeichert (Standard 15 Min), da fuer alle Fans identisch."""
    now = time.time()
    if not force and _media_index_cache["index"] and (now - _media_index_cache["ts"]) < ttl:
        return _media_index_cache["index"]
    index: dict[str, set[str]] = {}
    for f in list_vault_folders():
        name = f.get("name", "")
        if not name:
            continue
        if ppv_only and not name.upper().startswith("PPV"):
            continue
        try:
            index[name] = folder_media_uuids(name)
        except FanvueError:
            continue
    _media_index_cache["ts"] = now
    _media_index_cache["index"] = index
    return index


def media_variant_url(media: dict[str, Any], prefer: tuple[str, ...] = ("thumbnail_gallery",
                      "thumbnail", "main")) -> str:
    """Beste Bild-URL aus den Varianten eines Media-Items ziehen (Fallback: top-level url)."""
    variants = media.get("variants") or []
    by_type = {v.get("variantType"): v.get("url") for v in variants if v.get("url")}
    for t in prefer:
        if by_type.get(t):
            return by_type[t]
    # irgendeine Variante oder das top-level Feld
    if by_type:
        return next(iter(by_type.values()))
    return media.get("url", "") or ""


def message_image_url(msg: dict[str, Any]) -> str:
    """Beste anschaubare Bild-URL aus einer Chat-Nachricht ziehen (Fan-Foto).
    Die Fanvue-API kann Medien in unterschiedlichen Formen liefern – wir probieren
    die gaengigen Felder defensiv durch. Gibt '' zurueck, wenn nichts gefunden wird."""
    # 1) Liste unter 'media' / 'medias' / 'attachments' mit Media-Objekten (ggf. Varianten)
    for key in ("media", "medias", "attachments"):
        val = msg.get(key)
        if isinstance(val, dict):
            val = [val]
        if isinstance(val, list):
            for m in val:
                if not isinstance(m, dict):
                    continue
                url = media_variant_url(m, prefer=("main", "thumbnail_gallery", "thumbnail"))
                if url:
                    return url
    # 2) Direkte URL-Felder auf der Nachricht selbst
    for key in ("mediaUrl", "url", "imageUrl", "thumbnailUrl"):
        if msg.get(key):
            return str(msg[key])
    return ""


def list_messages(user_uuid: str, size: int = 15, mark_as_read: bool = False,
                  page: int = 1) -> dict[str, Any]:
    params = {"size": size, "page": page, "markAsRead": "true" if mark_as_read else "false"}
    return _request("GET", f"/chats/{user_uuid}/messages", params=params).json()


def send_message(user_uuid: str, text: str, *, price_cents: int | None = None,
                 media_uuids: list[str] | None = None,
                 media_preview_uuid: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"text": text}
    if media_uuids:
        body["mediaUuids"] = media_uuids
    if price_cents:
        body["price"] = price_cents  # min. 300 (=$3) laut API
    if media_preview_uuid:
        body["mediaPreviewUuid"] = media_preview_uuid
    return _request("POST", f"/chats/{user_uuid}/message", json_body=body).json()
