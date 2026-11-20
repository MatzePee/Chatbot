"""Einstellungen, die zur Laufzeit über die Oberfläche änderbar sind.

Alles, was hier liegt, hat Vorrang vor den Werten aus der .env. Geheimnisse
(API-Schlüssel, OAuth-Secrets) werden verschlüsselt abgelegt – genau wie die
OAuth-Tokens der Kanäle.

Damit muss niemand die .env anfassen, um Fanvue oder OpenRouter zu verbinden.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from sqlalchemy import select, literal, String
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
    "visibility_aging_enabled": None,
    "visibility_aging_days": None,
    "post_retention_days": None,
    "post_retention_delete_ppv": None,
    "post_retention_automatic": None,
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
    "visibility_aging_enabled": False,
    "visibility_aging_days": 7,
    "post_retention_days": 30,
    "post_retention_delete_ppv": False,
    "post_retention_automatic": False,
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
_character_cache = {}
_character_snapshots = {}


def _owner():
    from autoposter.workspaces import current_character
    return current_character()


def _technical(field):
    import os
    from app.setting_scope import is_global_technical
    return (_owner() is not None or os.environ.get('MP_CHARACTERS_ENABLED') == '1') and is_global_technical(field)


def _control_only():
    import os
    return _owner() is None and os.environ.get('MP_CHARACTERS_ENABLED') == '1'


def _account_configuration():
    from .chatbot_fanvue import configuration, FIELDS as account_fields
    if _control_only():
        return {field: '' for field in account_fields}
    return configuration()


def _technical_overlay(values):
    from app import db as chat_db
    out = dict(values)
    for field in FIELDS:
        if _technical(field):
            present, value = chat_db.technical_setting(field)
            if present:
                out[field] = value
    return out


def current(field: str, default: Any = None) -> Any:
    """Aktueller Wert ohne DB-Zugriff. Reihenfolge: Oberfläche > .env > Default."""
    if _technical(field):
        from app import db as chat_db
        present, value = chat_db.technical_setting(field)
        if present:
            return value
    if _control_only() and not _technical(field):
        return default
    if field in ("fanvue_client_id", "fanvue_client_secret", "fanvue_api_version"):
        return _account_configuration()[field]
    snapshot = _character_snapshots.get(_owner(), {}) if _owner() else _snapshot
    value = snapshot.get(field)
    if value not in (None, ""):
        return value
    if _owner() and field in ('x_client_id', 'x_client_secret'):
        return default
    env_attr = FIELDS.get(field)
    if env_attr and hasattr(settings, env_attr):
        env_value = getattr(settings, env_attr)
        if env_value not in (None, ""):
            return env_value
    return DEFAULTS.get(field, default)


async def refresh_snapshot(db: AsyncSession) -> Dict[str, Any]:
    global _snapshot
    if _owner():
        values = await get_all(db, use_cache=False)
        _character_snapshots[_owner()] = dict(values)
        return values
    _snapshot = await get_all(db, use_cache=False)
    return dict(_snapshot)


def invalidate() -> None:
    global _cache, _cache_time
    if _owner():
        _character_cache.pop(_owner(), None)
        _character_snapshots.pop(_owner(), None)
        return
    _cache = None
    _cache_time = 0.0


async def _load_raw(db: AsyncSession) -> Dict[str, Any]:
    row = (
        await db.execute(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)
                         .execution_options(populate_existing=True))
    ).scalar_one_or_none()
    return dict(row.value or {}) if row else {}


async def _legacy_global_technical(db):
    """Read only the allowlisted infrastructure portion of the unprefixed row.

    This intentionally uses the bound connection, not an unscoped ORM session or
    a changed active context. The literal key is NOT character-prefixed by its
    mapped TypeDecorator. Account credentials are never returned by this helper.
    """
    if _owner() is None:
        return {}
    from app.setting_scope import GLOBAL_TECHNICAL_KEYS
    connection = await db.connection()
    value = (await connection.execute(select(AppSetting.__table__.c.value).where(
        AppSetting.__table__.c.key == literal(SETTINGS_KEY, String())))).scalar_one_or_none()
    return {key:item for key,item in (value or {}).items() if key in GLOBAL_TECHNICAL_KEYS}


async def get_all(db: AsyncSession, *, use_cache: bool = True) -> Dict[str, Any]:
    """Effektive Werte: DB vor .env vor Default. Geheimnisse entschlüsselt."""
    global _cache, _cache_time
    owner = _owner()
    cache, stamp = _character_cache.get(owner, (None, 0)) if owner else (_cache, _cache_time)
    # A process cache must never expose an uncommitted caller transaction.
    transactional = owner is not None or _control_only()
    if use_cache and not transactional and cache is not None and time.time() - stamp < CACHE_TTL:
        return _technical_overlay({**cache, **_account_configuration()})

    stored = await _load_raw(db)
    legacy_technical = await _legacy_global_technical(db)
    # Technical fields are installation-owned, never inherited from a character
    # JSON copy left by an older migration. Keep only character fields there.
    if owner:
        stored = {key:value for key,value in stored.items() if not _technical(key)}
        stored.update(legacy_technical)
    effective: Dict[str, Any] = {}
    for field, env_attr in FIELDS.items():
        if _control_only() and not _technical(field):
            effective[field] = '' if field in ('x_client_id', 'x_client_secret') else DEFAULTS.get(field)
            continue
        if field in stored and stored[field] not in (None, ""):
            value = stored[field]
            if field in SECRET_FIELDS:
                try:
                    value = decrypt(value)
                except Exception:
                    value = ""
            effective[field] = value
        elif owner and field in ('x_client_id', 'x_client_secret'):
            effective[field] = ''
        elif env_attr and hasattr(settings, env_attr):
            effective[field] = getattr(settings, env_attr)
        else:
            effective[field] = DEFAULTS.get(field)

    effective.update(_account_configuration())
    if transactional:
        from .settings_transaction import overlay
        return await overlay(db, effective)
    effective = _technical_overlay(effective)
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
    if 'post_retention_days' in values:
        days = values['post_retention_days']
        if type(days) is not int or not 1 <= days <= 36500:
            raise ValueError('Löschalter muss eine ganze Zahl von 1 bis 36500 Tagen sein.')
    for field in ('post_retention_delete_ppv', 'post_retention_automatic'):
        if field in values and type(values[field]) is not bool:
            raise ValueError(f'{field} muss ein boolescher Wert sein.')
    if 'visibility_aging_days' in values:
        days = values['visibility_aging_days']
        if type(days) is not int or not 1 <= days <= 36500:
            raise ValueError('Sichtbarkeitsalter muss eine ganze Zahl von 1 bis 36500 Tagen sein.')
    if 'visibility_aging_enabled' in values and type(values['visibility_aging_enabled']) is not bool:
        raise ValueError('Sichtbarkeitsautomatik muss ein boolescher Wert sein.')
    technical_values = {
        field: value for field, value in values.items()
        if _technical(field) and field in FIELDS
        and (field not in SECRET_FIELDS or not (value is None or (isinstance(value, str) and set(value.strip()) <= set('*•●'))))
    }
    from . import settings_transaction
    if technical_values:
        # No autoflush or character JSON mutation before storage preflight.
        await settings_transaction.enlist(db)
    elif _owner() is not None or _control_only():
        await settings_transaction.lock_character_settings(db)
    stored = await _load_raw(db)

    for field, value in values.items():
        if _technical(field) and field in FIELDS:
            continue
        if field in ("fanvue_client_id", "fanvue_client_secret", "fanvue_api_version"):
            continue  # Managed exclusively in AutoChat settings.
        if field not in FIELDS:
            continue
        if field in SECRET_FIELDS:
            if value is None or (isinstance(value, str) and set(value.strip()) <= set('*•●')):
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
    if technical_values:
        await settings_transaction.write(db, technical_values)
    invalidate()
    if _owner() is not None or _control_only():
        return await get_all(db, use_cache=False)
    return await refresh_snapshot(db)


async def clear_secret(db: AsyncSession, field: str) -> None:
    if field in SECRET_FIELDS and _technical(field):
        from . import settings_transaction
        await settings_transaction.enlist(db)
        await settings_transaction.write(db, {field: ''})
        invalidate()
        return
    if field == "fanvue_client_secret":
        return  # The shared secret is owned by AutoChat settings.
    if _owner() is not None or _control_only():
        from . import settings_transaction
        await settings_transaction.lock_character_settings(db)
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
