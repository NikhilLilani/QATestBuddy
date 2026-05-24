"""qa-bridge CLI tokens.

Tokens are issued once (shown to the user in plaintext), then only the SHA-256
hash is stored. Revocable per token.
"""
from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.core.workspace import CurrentWorkspace
from app.db.queries import execute, fetch, fetchrow

router = APIRouter(prefix="/bridge/tokens", tags=["settings"])


class CreateBridgeToken(BaseModel):
    label: str = Field(min_length=1, max_length=80)


class BridgeTokenOut(BaseModel):
    id: str
    label: str
    last_seen_at: str | None
    revoked_at: str | None
    created_at: str


class CreateBridgeTokenOut(BridgeTokenOut):
    token: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.get("", response_model=list[BridgeTokenOut])
async def list_tokens(ws: CurrentWorkspace) -> list[BridgeTokenOut]:
    rows = await fetch(
        """
        select id::text, label, last_seen_at::text, revoked_at::text, created_at::text
        from public.bridge_tokens
        where workspace_id = $1
        order by created_at desc
        """,
        ws.workspace_id,
    )
    return [BridgeTokenOut(**r) for r in rows]


@router.post("", response_model=CreateBridgeTokenOut, status_code=status.HTTP_201_CREATED)
async def create_token(body: CreateBridgeToken, ws: CurrentWorkspace) -> CreateBridgeTokenOut:
    raw = "qatb_" + secrets.token_urlsafe(32)
    row = await fetchrow(
        """
        insert into public.bridge_tokens (workspace_id, user_id, token_hash, label)
        values ($1, $2, $3, $4)
        returning id::text, label, last_seen_at::text, revoked_at::text, created_at::text
        """,
        ws.workspace_id,
        ws.user_id,
        _hash(raw),
        body.label,
    )
    assert row is not None
    return CreateBridgeTokenOut(**row, token=raw)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: str, ws: CurrentWorkspace) -> None:
    await execute(
        """
        update public.bridge_tokens set revoked_at = now()
        where id = $1 and workspace_id = $2
        """,
        token_id,
        ws.workspace_id,
    )
