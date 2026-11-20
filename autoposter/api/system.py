"""Version, Update und Veröffentlichen nach GitHub."""
from __future__ import annotations

from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db import get_db
from autoposter.deps import current_user, require_admin, require_editor
from autoposter.models import User
from autoposter.schemas import GitSettingsIn, PublishRequest
from autoposter.services import appconfig, gitops, gitpublish, updater

router = APIRouter(prefix="/system", tags=["system"])
# Only workspace activation belongs in the assembled app. The older standalone
# update/publish endpoints remain intentionally unmounted.
character_upgrade_router = APIRouter(prefix="/system", tags=["system"])


from pydantic import BaseModel, ConfigDict, Field, StrictBool


class CharacterUpgradeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    confirmed: StrictBool = False
    technical_source: Literal['autochat', 'autopost'] | None = None


@character_upgrade_router.get('/character-upgrade')
@router.get('/character-upgrade')
async def character_upgrade_status(_: User = Depends(require_admin)):
    from app import character_upgrade
    from app.helper_readiness import readiness
    return {**character_upgrade.status(character_upgrade.ROOT), 'helper': readiness(character_upgrade.ROOT)}


@character_upgrade_router.post('/character-upgrade')
@router.post('/character-upgrade')
async def character_upgrade_stage(payload: CharacterUpgradeRequest, _: User = Depends(require_admin)):
    from app import character_upgrade
    from app.preview import enabled
    if enabled():
        raise HTTPException(403, 'Migration ist im Mitlesemodus gesperrt')
    from app.helper_readiness import readiness
    helper = readiness(character_upgrade.ROOT)
    if not helper['ready']:
        raise HTTPException(409, helper['message'])
    try:
        from app.setting_scope import GLOBAL_TECHNICAL_KEYS
        sources = ({key: payload.technical_source for key in GLOBAL_TECHNICAL_KEYS}
                   if payload.technical_source is not None else {})
        return {**character_upgrade.stage(character_upgrade.ROOT, name=payload.name, confirmed=payload.confirmed,
                                         technical_sources=sources),
                'helper': helper}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@character_upgrade_router.delete('/character-upgrade')
@router.delete('/character-upgrade')
async def character_upgrade_cancel(_: User = Depends(require_admin)):
    from app import character_upgrade
    from app.preview import enabled
    if enabled():
        raise HTTPException(403, 'Migration ist im Mitlesemodus gesperrt')
    try:
        character_upgrade.cancel(character_upgrade.ROOT)
        from app.helper_readiness import readiness
        return {**character_upgrade.status(character_upgrade.ROOT), 'helper': readiness(character_upgrade.ROOT)}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


# --------------------------------------------------------------------------- #
# Version und Update
# --------------------------------------------------------------------------- #
@router.get("/version")
async def version(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> Dict[str, Any]:
    """Zustand ohne Netzwerkzugriff – für jeden Seitenaufbau geeignet."""
    state = await updater.cached_state(db)
    state["check_enabled"] = bool(await appconfig.get(db, "update_check_enabled", True))
    state["check_interval_hours"] = int(
        await appconfig.get(db, "update_check_interval_hours", 6) or 6
    )
    return state


@router.post("/update-check")
async def update_check(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_editor)
) -> Dict[str, Any]:
    """Frisch bei GitHub nachsehen. Nur auf ausdrücklichen Knopfdruck."""
    return await updater.check(db, fetch=True)


@router.post("/update")
async def update_now(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
) -> Dict[str, Any]:
    """Installiert wird nur auf Knopfdruck, nie automatisch.

    Ein selbsttätiges Update, das nachts eine laufende Instanz umstellt, ist
    ohne Konsolenzugang nicht zu vertreten.
    """
    result = await updater.install(db)
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "Update fehlgeschlagen"))
    return result


@router.post("/restart")
async def restart_now(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
) -> Dict[str, Any]:
    """Dienst neu starten – ohne SSH-Sitzung auf dem Server.

    Nötig nach Änderungen an der .env und immer dann, wenn ein Job hängt. Der
    Neustart läuft entkoppelt, damit diese Antwort noch herausgeht.
    """
    result = await updater.restart_service(db)
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "Neustart fehlgeschlagen"))
    return result


# --------------------------------------------------------------------------- #
# Veröffentlichen
# --------------------------------------------------------------------------- #
@router.get("/publish/status")
async def publish_status(
    db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
) -> Dict[str, Any]:
    return await gitpublish.status(db)


@router.post("/publish/settings")
async def save_git_settings(
    payload: GitSettingsIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "git_remote_url": payload.git_remote_url.strip(),
        "git_branch": payload.git_branch.strip() or "main",
        "git_user_name": payload.git_user_name.strip(),
        "git_user_email": payload.git_user_email.strip(),
        "git_commit_default": payload.git_commit_default.strip(),
    }
    # Leeres Token-Feld heißt "unverändert lassen". Zum bewussten Löschen gibt
    # es clear_token – sonst wäre der Token nach jedem Speichern weg.
    if payload.github_token.strip():
        values["github_token"] = payload.github_token.strip()
    elif payload.clear_token:
        values["github_token"] = ""

    from autoposter.services.settings_transaction import SettingsTransactionUnsupported
    try:
        await appconfig.set_many(db, values)
    except SettingsTransactionUnsupported as exc:
        raise HTTPException(409, str(exc)) from exc
    return await gitpublish.status(db)


@router.post("/publish")
async def publish(
    payload: PublishRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    result = await gitpublish.publish(
        db, message=payload.message, tag=payload.tag, push=payload.push
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "Veröffentlichen fehlgeschlagen"))
    return result
