"""Gemeinsame FastAPI-Dependencies."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config import settings
from autoposter.db import get_db
from autoposter.models import Role, User
from autoposter.security import decode_token, hash_password

ROLE_ORDER = {Role.viewer.value: 0, Role.editor.value: 1, Role.admin.value: 2}


async def get_system_user(db: AsyncSession) -> User:
    """Benutzer für den anmeldefreien Betrieb im internen Netz.

    Es braucht ihn, weil Posts, Zuordnungen und Audit-Einträge auf einen Urheber
    verweisen. Er kann sich nicht anmelden – das Passwortfeld enthält einen
    Zufallswert, der nirgends gespeichert wird.
    """
    email = settings.system_user_email.lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user:
        return user
    user = User(
        email=email,
        password_hash=hash_password(uuid.uuid4().hex + uuid.uuid4().hex),
        display_name="System",
        role=Role.admin.value,
    )
    db.add(user)
    await db.flush()
    return user


async def current_user(
    db: AsyncSession = Depends(get_db),
    session_cookie: Optional[str] = Cookie(default=None, alias=settings.cookie_name),
    authorization: Optional[str] = Header(default=None),
) -> User:
    # Anmeldefreier Betrieb: alles läuft unter dem Systembenutzer.
    if not settings.auth_enabled:
        return await get_system_user(db)

    token = session_cookie
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")

    payload = decode_token(token)
    if not payload or payload.get("typ") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token ungültig oder abgelaufen")

    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token ungültig")

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Benutzer inaktiv")
    return user


def require_role(minimum: str):
    async def _dep(user: User = Depends(current_user)) -> User:
        if not settings.auth_enabled:
            return user
        if ROLE_ORDER.get(user.role, 0) < ROLE_ORDER[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Rolle '{minimum}' erforderlich")
        return user

    return _dep


require_editor = require_role(Role.editor.value)
require_admin = require_role(Role.admin.value)
