"""JWT verification.

Supabase signs auth JWTs with one of:
  - HS256 (legacy projects, key = SUPABASE_JWT_SECRET)
  - ES256 / RS256 (new projects, public keys served via JWKS)

We inspect the token header and pick the right path. JWKS is cached in memory
for one hour and re-fetched on `kid` miss.
"""
from __future__ import annotations

import time
from typing import Annotated, Any

import httpx
from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import settings


class AuthUser(BaseModel):
    sub: str
    email: str | None = None
    role: str | None = None


# ----------------------------- JWKS cache --------------------------------

_jwks_cache: dict[str, Any] = {"keys": [], "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 3600


async def _fetch_jwks(force: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    if not force and _jwks_cache["keys"] and (now - _jwks_cache["fetched_at"]) < _JWKS_TTL_SECONDS:
        return _jwks_cache["keys"]
    if not settings.supabase_url:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "SUPABASE_URL not configured"
        )
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    _jwks_cache["keys"] = data.get("keys", [])
    _jwks_cache["fetched_at"] = now
    return _jwks_cache["keys"]


def _find_key(keys: list[dict[str, Any]], kid: str | None) -> dict[str, Any] | None:
    if not kid:
        return keys[0] if keys else None
    return next((k for k in keys if k.get("kid") == kid), None)


# ------------------------------- verify ----------------------------------


async def _verify(token: str) -> AuthUser:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Malformed token: {e}") from e

    alg = header.get("alg", "")
    kid = header.get("kid")

    try:
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                if settings.env == "dev":
                    return AuthUser(sub="dev-user", email="dev@localhost", role="authenticated")
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR, "SUPABASE_JWT_SECRET missing"
                )
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
        elif alg in {"ES256", "RS256"}:
            keys = await _fetch_jwks()
            key = _find_key(keys, kid)
            if key is None:
                # Force refresh once in case of key rotation
                keys = await _fetch_jwks(force=True)
                key = _find_key(keys, kid)
            if key is None:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, f"No JWKS key matched kid={kid!r}"
                )
            payload = jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience="authenticated",
            )
        else:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, f"Unsupported JWT alg: {alg!r}"
            )
    except JWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {e}") from e

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing 'sub' claim")
    return AuthUser(sub=sub, email=payload.get("email"), role=payload.get("role"))


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    return await _verify(authorization.split(" ", 1)[1])


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
