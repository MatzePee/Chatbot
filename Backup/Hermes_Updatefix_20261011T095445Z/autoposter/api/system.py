"""Version, Update und Veröffentlichen nach GitHub."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db import get_db
from autoposter.deps import current_user, require_admin, require_editor
from autoposter.models import User
from autoposter.schemas import GitSettingsIn, PublishRequest
from autoposter.services import appconfig, gitops, gitpublish, updater

router = APIRouter(prefix="/system", tags=["system"])


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

    await appconfig.set_many(db, values)
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
