"""Einstellungen, die zur Laufzeit über die Oberfläche änderbar sind.

Alles, was hier liegt, hat Vorrang vor den Werten aus der .env. Geheimnisse
(API-Schlüssel, OAuth-Secrets) werden verschlüsselt abgelegt – genau wie die
OAuth-Tokens der Kanäle.

Damit muss niemand die .env anfassen, um Fanvue oder OpenRouter zu verbinden.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config import settings
from autoposter.crypto import decrypt, encrypt
from autoposter.models import AppSetting

SETTINGS_KEY = "integrations"

#: Diese Felder werden verschlüsselt gespeichert und nie im Klartext ausgeliefert.
SECRET_FIELDS = {
    "openrouter_api_key",
    "fanvue_client_secret",
    "x_client_secret",
    #: Ein leeres Feld bedeutet "unverändert lassen", nicht "löschen" – sonst
    #: würde jedes versehentliche Speichern den Token entfernen.
    "github_token",
}

#: Alle bekannten Felder mit ihrer Herkunft aus der .env als Rückfallwert.
FIELDS: Dict[str, str] = {
    "openrouter_api_key": "openrouter_api_key",
    "openrouter_model_caption": "openrouter_model_caption",
    "openrouter_model_text": "openrouter_model_text",
    "openrouter_model_vision": "openrouter_model_vision",
    "openrouter_model_fallback": "openrouter_model_fallback",
    "openrouter_monthly_budget_usd": "openrouter_monthly_budget_usd",
    "fanvue_client_id": "fanvue_client_id",
    "fanvue_client_secret": "fanvue_client_secret",
    "fanvue_api_version": "fanvue_api_version",
    "x_client_id": "x_client_id",
    "x_client_secret": "x_client_secret",
    "x_api_tier": "x_api_tier",
    "public_base_url": "public_base_url",
    "inventory_warn_days": "inventory_warn_days",
    #: Mindestabstand zwischen Posts VERSCHIEDENER Kanäle, damit nicht alles
    #: zur selben Minute rausgeht.
    "cross_channel_min_gap_minutes": None,
    #: Stündliches automatisches Nachplanen. Standardmäßig AUS: Wer den
    #: Kalender selbst plant, will keine Posts, die von allein dazukommen.
    "auto_plan_enabled": None,
    "auto_plan_days": None,
    "auto_plan_interval_hours": None,
    #: Vom Programm gepflegt – Zeitstempel des letzten Laufs.
    "auto_plan_last_run": None,
    # --- Veröffentlichen und Aktualisieren über GitHub ---
    "git_remote_url": None,
    "git_branch": None,
    "git_user_name": None,
    "git_user_email": None,
    "github_token": None,
    "git_commit_default": None,
    "update_check_enabled": None,
    "update_check_interval_hours": None,
    #: Vom Programm gepflegt, nicht von Hand bedienen. JSON.
    "update_state": None,
}

DEFAULTS: Dict[str, Any] = {
    #: 0 = Kanäle dürfen gleichzeitig posten. Das ist der Normalfall: Zwei
    #: Profile sind für ihr Publikum zwei verschiedene Personen, ein zeitlicher
    #: Sicherheitsabstand zwischen ihnen bringt nichts und verschiebt nur die
    #: sorgfältig gewählten Zeitfenster. Wer bewusst entzerren will, setzt hier
    #: Minuten ein. Der Mindestabstand INNERHALB eines Kanals steht davon
    #: unabhängig in den Kanal-Einstellungen.
    "cross_channel_min_gap_minutes": 0,
    "auto_plan_enabled": False,
    "auto_plan_days": 14,
    "auto_plan_interval_hours": 24,
    "git_branch": "main",
    "git_commit_default": "Aktualisierung",
    "update_check_enabled": True,
    "update_check_interval_hours": 6,
}

_cache: Optional[Dict[str, Any]] = None
_cache_time = 0.0
CACHE_TTL = 5.0

#: Synchron lesbare Momentaufnahme. Adapter laufen teils außerhalb einer
#: DB-Sitzung; sie greifen hierauf zu statt auf die Datenbank.
_snapshot: Dict[str, Any] = {}


def current(field: str, default: Any = None) -> Any:
    """Aktueller Wert ohne DB-Zugriff. Reihenfolge: Oberfläche > .env > Default."""
    if field in ("fanvue_client_id", "fanvue_client_secret", "fanvue_api_version"):
        from .chatbot_fanvue import configuration
        return configuration()[field]
    value = _snapshot.get(field)
    if value not in (None, ""):
        return value
    env_attr = FIELDS.get(field)
    if env_attr and hasattr(settings, env_attr):
        env_value = getattr(settings, env_attr)
        if env_value not in (None, ""):
            return env_value
    return DEFAULTS.get(field, default)


async def refresh_snapshot(db: AsyncSession) -> Dict[str, Any]:
    global _snapshot
    _snapshot = await get_all(db, use_cache=False)
    return dict(_snapshot)


def invalidate() -> None:
    global _cache, _cache_time
    _cache = None
    _cache_time = 0.0


async def _load_raw(db: AsyncSession) -> Dict[str, Any]:
    row = (
        await db.execute(select(AppSetting).where(AppSetting.key == SETTINGS_KEY))
    ).scalar_one_or_none()
    return dict(row.value or {}) if row else {}


async def get_all(db: AsyncSession, *, use_cache: bool = True) -> Dict[str, Any]:
    """Effektive Werte: DB vor .env vor Default. Geheimnisse entschlüsselt."""
    global _cache, _cache_time
    if use_cache and _cache is not None and time.time() - _cache_time < CACHE_TTL:
        from .chatbot_fanvue import configuration
        return {**_cache, **configuration()}

    stored = await _load_raw(db)
    effective: Dict[str, Any] = {}
    for field, env_attr in FIELDS.items():
        if field in stored and stored[field] not in (None, ""):
            value = stored[field]
            if field in SECRET_FIELDS:
                try:
                    value = decrypt(value)
                except Exception:
                    value = ""
            effective[field] = value
        elif env_attr and hasattr(settings, env_attr):
            effective[field] = getattr(settings, env_attr)
        else:
            effective[field] = DEFAULTS.get(field)

    from .chatbot_fanvue import configuration
    effective.update(configuration())
    _cache = dict(effective)
    _cache_time = time.time()
    return dict(effective)


async def get(db: AsyncSession, field: str, default: Any = None) -> Any:
    values = await get_all(db)
    value = values.get(field)
    return default if value in (None, "") else value


async def set_many(db: AsyncSession, values: Dict[str, Any]) -> Dict[str, Any]:
    """Speichert nur bekannte Felder. Leere Geheimnisse lassen den alten Wert stehen,
    damit ein maskiert angezeigtes Feld beim Speichern nichts überschreibt."""
    stored = await _load_raw(db)

    for field, value in values.items():
        if field in ("fanvue_client_id", "fanvue_client_secret", "fanvue_api_version"):
            continue  # Managed exclusively in AutoChat settings.
        if field not in FIELDS:
            continue
        if field in SECRET_FIELDS:
            if value in (None, "", "********"):
                continue  # unverändert lassen
            stored[field] = encrypt(str(value))
        else:
            stored[field] = value

    row = (
        await db.execute(select(AppSetting).where(AppSetting.key == SETTINGS_KEY))
    ).scalar_one_or_none()
    if row:
        row.value = stored
        db.add(row)
    else:
        db.add(AppSetting(key=SETTINGS_KEY, value=stored))
    await db.flush()
    invalidate()
    return await refresh_snapshot(db)


async def clear_secret(db: AsyncSession, field: str) -> None:
    if field == "fanvue_client_secret":
        return  # The shared secret is owned by AutoChat settings.
    stored = await _load_raw(db)
    stored.pop(field, None)
    row = (
        await db.execute(select(AppSetting).where(AppSetting.key == SETTINGS_KEY))
    ).scalar_one_or_none()
    if row:
        row.value = stored
        db.add(row)
        await db.flush()
    invalidate()


def mask(values: Dict[str, Any]) -> Dict[str, Any]:
    """Für die Ausgabe an die Oberfläche: Geheimnisse nur als gesetzt/nicht gesetzt."""
    out = dict(values)
    for field in SECRET_FIELDS:
        raw = values.get(field)
        out[field] = "********" if raw else ""
        out[f"{field}_set"] = bool(raw)
    return out
