"""Passwort-Hashing und JWT."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from jose import JWTError, jwt

from autoposter.config import settings

_hasher = PasswordHasher()
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def create_token(subject: str, kind: str = "access", extra: Optional[Dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    if kind == "access":
        expires = now + timedelta(minutes=settings.access_token_ttl_minutes)
    else:
        expires = now + timedelta(days=settings.refresh_token_ttl_days)
    payload: Dict[str, Any] = {
        "sub": subject,
        "typ": kind,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
