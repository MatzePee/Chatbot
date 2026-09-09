"""OAuth-App je Kanal ermitteln.

Bei X gehört zu jeder API-App genau ein Account. Wer mehrere X-Profile
betreibt, hinterlegt deshalb pro Kanal eigene Zugangsdaten. Sind sie leer,
greifen die globalen Werte aus den Einstellungen – praktisch für Fanvue, wo
eine App für den Creator genügt.
"""
from __future__ import annotations

from typing import Optional, Tuple
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.adapters.base import OAuthApp
from autoposter.crypto import decrypt, encrypt
from autoposter.models import Channel
from autoposter.services.appconfig import current as cfg


def global_app(platform: str) -> OAuthApp:
    if platform == "fanvue":
        from .chatbot_fanvue import oauth_app
        return oauth_app()
    return OAuthApp(
        client_id=str(cfg(f"{platform}_client_id") or ""),
        client_secret=str(cfg(f"{platform}_client_secret") or ""),
        redirect_base=str(cfg("public_base_url") or ""),
        api_version=str(cfg("fanvue_api_version") or "") if platform == "fanvue" else "",
        name="global",
    )


def redirect_base_for(channel: Optional[Channel]) -> str:
    """Basis der Redirect-URI: Kanal-Eintrag schlägt PUBLIC_BASE_URL.

    Die Adresse muss auf den Server zurückführen und mit dem Eintrag im
    Entwicklerportal übereinstimmen. Ein optionaler Reverse-Proxy-Pfad bleibt
    Teil der Basis. Fanvue verwendet separat die gemeinsame AutoChat-Verbindung.
    """
    override = (getattr(channel, "oauth_redirect_base", "") or "").strip()
    return (override or str(cfg("public_base_url") or "")).rstrip("/")


def normalize_redirect_base(value: Optional[str], platform: str) -> str:
    """Accept a server URL or its complete callback, retaining the existing DB field."""
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ("http", "https") and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment
                 and not any(c.isspace() or c == "\\" for c in value))
        parsed.port  # Reject malformed/out-of-range ports, too.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Bitte eine gültige Serveradresse mit http:// oder https:// angeben, ohne Zugangsdaten, Abfrage oder Fragment.")
    suffix = f"/api/v1/oauth/{platform}/callback"
    if value.endswith(suffix):
        value = value[:-len(suffix)].rstrip("/")
    elif "/api/v1/oauth/" in parsed.path:
        raise ValueError(f"Die Callback-Adresse muss auf {suffix} enden.")
    if len(value) > 255:
        raise ValueError("Die Serveradresse darf höchstens 255 Zeichen lang sein.")
    return value


def for_channel(channel: Channel) -> OAuthApp:
    """Eigene App des Kanals, sonst die globale."""
    if channel.platform == "fanvue":
        return global_app("fanvue")
    if channel.oauth_client_id:
        return OAuthApp(
            client_id=channel.oauth_client_id,
            client_secret=decrypt(channel.oauth_client_secret_enc) or "",
            redirect_base=redirect_base_for(channel),
            api_version=str(cfg("fanvue_api_version") or "") if channel.platform == "fanvue" else "",
            name=channel.oauth_app_name or channel.display_name,
        )
    app = global_app(channel.platform)
    # Auch bei einer globalen X-App gilt die Callback-Adresse des Kanals.
    app.redirect_base = redirect_base_for(channel)
    return app


def uses_own_app(channel: Channel) -> bool:
    return channel.platform != "fanvue" and bool(channel.oauth_client_id)


def store_app(channel: Channel, client_id: str, client_secret: Optional[str], app_name: str = "") -> Channel:
    """Zugangsdaten am Kanal speichern. Leeres Secret lässt das alte stehen,
    damit ein maskiert angezeigtes Feld beim Speichern nichts überschreibt."""
    if channel.platform == "fanvue":
        return channel  # Preserve legacy data, but never create a second connection.
    channel.oauth_client_id = (client_id or "").strip()
    if client_secret and client_secret not in ("********",):
        channel.oauth_client_secret_enc = encrypt(client_secret.strip())
    if not channel.oauth_client_id:
        # Zurück auf die globale App: gespeichertes Secret verwerfen.
        channel.oauth_client_secret_enc = None
    if app_name is not None:
        channel.oauth_app_name = (app_name or "").strip()
    return channel


async def conflicting_channels(
    db: AsyncSession, channel: Channel
) -> Tuple[bool, list]:
    """Prüft, ob dieselbe Client-ID schon an einem anderen Kanal hängt.

    Bei X ist das ein Fehler: eine App kann nur einen Account bedienen. Zwei
    Kanäle mit derselben App würden sich beim Verbinden gegenseitig überschreiben.
    """
    if not channel.oauth_client_id:
        return False, []
    rows = (
        await db.execute(
            select(Channel).where(
                Channel.oauth_client_id == channel.oauth_client_id,
                Channel.id != channel.id,
            )
        )
    ).scalars().all()
    return bool(rows), [c.display_name for c in rows]
