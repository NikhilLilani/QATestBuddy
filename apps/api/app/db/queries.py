"""Direct asyncpg helpers used by routers.

We bypass SQLAlchemy ORM for these write paths to keep the surface small.
RLS does not apply to the backend's connection (service role), so every
query that crosses workspace boundaries must check membership explicitly
via `require_member`.
"""
from __future__ import annotations

import json
from typing import Any

import asyncpg
from fastapi import HTTPException, status

from app.core.config import settings

_pool: asyncpg.Pool | None = None


def _normalize_dsn(dsn: str) -> str:
    # asyncpg uses postgresql:// (not the SQLAlchemy-style +asyncpg).
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _init_conn(conn: asyncpg.Connection) -> None:
    """Install JSON(B) codecs so jsonb columns decode to dict/list, not str."""
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            _normalize_dsn(settings.db_url),
            min_size=1,
            max_size=5,
            init=_init_conn,
            # Supabase's Transaction Pooler runs PgBouncer in transaction mode,
            # which does not support prepared statements. Disable asyncpg's
            # statement cache so each call sends raw SQL.
            statement_cache_size=0,
        )
    return _pool


async def require_member(user_id: str, workspace_id: str) -> str:
    """Return the role string if the user is a member of the workspace, else 403."""
    pool = await get_pool()
    role = await pool.fetchval(
        "select role::text from public.memberships where user_id = $1 and workspace_id = $2",
        user_id,
        workspace_id,
    )
    if role is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of this workspace")
    return role


async def fetch(query: str, *args: Any) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(query, *args)
    return [dict(r) for r in rows]


async def fetchrow(query: str, *args: Any) -> dict | None:
    pool = await get_pool()
    row = await pool.fetchrow(query, *args)
    return dict(row) if row else None


async def execute(query: str, *args: Any) -> str:
    pool = await get_pool()
    return await pool.execute(query, *args)
