"""Login, Logout, Benutzerverwaltung."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config import settings
from autoposter.db import get_db
from autoposter.deps import current_user, require_admin
from autoposter.models import AuditLog, User
from autoposter.schemas import LoginRequest, UserCreate, UserOut
from autoposter.security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILED = 8
LOCK_MINUTES = 15


@router.post("/login", response_model=UserOut)
async def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    user = (
        await db.execute(select(User).where(User.email == payload.email.lower()))
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if user and user.locked_until and user.locked_until > now:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Konto vorübergehend gesperrt")

    if not user or not verify_password(payload.password, user.password_hash):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED:
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                user.failed_logins = 0
            db.add(user)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-Mail oder Passwort falsch")

    if user.totp_secret:
        if not payload.totp or not pyotp.TOTP(user.totp_secret).verify(payload.totp, valid_window=1):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2FA-Code fehlt oder ist falsch")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Konto deaktiviert")

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    db.add(user)
    db.add(
        AuditLog(
            actor_id=user.id,
            action="login",
            entity="user",
            entity_id=str(user.id),
            ip=request.client.host if request.client else "",
        )
    )

    token = create_token(str(user.id), "access", {"role": user.role})
    response.set_cookie(
        settings.cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.access_token_ttl_minutes * 60,
        path="/",
    )
    return UserOut.model_validate(user)


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/mode")
async def auth_mode() -> dict:
    """Sagt der Oberfläche, ob eine Anmeldeseite gebraucht wird.
    Bewusst ohne Authentifizierung – sonst käme man nie an die Information."""
    return {"auth_enabled": settings.auth_enabled}


@router.get("/users", response_model=List[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
) -> List[UserOut]:
    rows = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
    return [UserOut.model_validate(r) for r in rows]


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> UserOut:
    exists = (
        await db.execute(select(User).where(User.email == payload.email.lower()))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "E-Mail bereits vergeben")
    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        display_name=payload.display_name or payload.email.split("@")[0],
        role=payload.role,
    )
    db.add(user)
    await db.flush()
    return UserOut.model_validate(user)


@router.delete("/users/{user_id}", status_code=204, response_model=None)
async def delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> None:
    if admin.id == user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Eigenes Konto kann nicht gelöscht werden")
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user:
        await db.delete(user)
