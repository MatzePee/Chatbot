"""Verschlüsselung von OAuth-Tokens (at rest)."""
from __future__ import annotations

import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from autoposter.config import settings


def _fernet() -> Fernet:
    key = settings.encryption_key.strip()
    if not key:
        # Fallback: aus secret_key ableiten, damit Dev-Setups laufen.
        digest = hashlib.sha256(settings.secret_key.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover
        raise RuntimeError(
            "Token konnte nicht entschlüsselt werden. Wurde ENCRYPTION_KEY geändert?"
        ) from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()
