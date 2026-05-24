"""Symmetric encryption for stored secrets (API keys, PATs).

Uses Fernet (AES-128-CBC + HMAC) keyed by APP_ENCRYPTION_KEY.
Generate one with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


@lru_cache
def _fernet() -> Fernet:
    if not settings.app_encryption_key:
        raise RuntimeError("APP_ENCRYPTION_KEY is not set")
    return Fernet(settings.app_encryption_key.encode())


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    try:
        return _fernet().decrypt(ciphertext).decode()
    except InvalidToken as e:
        raise RuntimeError("Failed to decrypt secret (key mismatch?)") from e


def hint(plaintext: str, last: int = 4) -> str:
    """Return last N characters for UI display, e.g. '••••a3f9'."""
    if len(plaintext) <= last:
        return "•" * len(plaintext)
    return f"••••{plaintext[-last:]}"
