"""API key management for BYOK LLM providers.

Keys are encrypted server-side via Fernet. Only `key_hint` (last 4 chars) is
ever returned to clients.
"""
from __future__ import annotations

import secrets
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.crypto import decrypt, encrypt, hint
from app.core.workspace import CurrentWorkspace
from app.db.queries import execute, fetch, fetchrow

router = APIRouter(prefix="/settings/api-keys", tags=["settings"])

Provider = Literal["anthropic", "openai", "gemini", "openrouter"]


class CreateApiKey(BaseModel):
    provider: Provider
    name: str = Field(min_length=1, max_length=80)
    key: str = Field(min_length=8)


class ApiKeyOut(BaseModel):
    id: str
    provider: Provider
    name: str
    key_hint: str
    last_used_at: str | None
    created_at: str


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(ws: CurrentWorkspace) -> list[ApiKeyOut]:
    rows = await fetch(
        """
        select id::text, provider::text, name, key_hint,
               last_used_at::text, created_at::text
        from public.api_keys
        where workspace_id = $1
        order by created_at desc
        """,
        ws.workspace_id,
    )
    return [ApiKeyOut(**r) for r in rows]


@router.post("", response_model=ApiKeyOut, status_code=status.HTTP_201_CREATED)
async def create_key(body: CreateApiKey, ws: CurrentWorkspace) -> ApiKeyOut:
    enc = encrypt(body.key)
    row = await fetchrow(
        """
        insert into public.api_keys (workspace_id, provider, name, encrypted_key, key_hint, created_by)
        values ($1, $2::api_provider, $3, $4, $5, $6)
        returning id::text, provider::text, name, key_hint,
                  last_used_at::text, created_at::text
        """,
        ws.workspace_id,
        body.provider,
        body.name,
        enc,
        hint(body.key),
        ws.user_id,
    )
    assert row is not None
    return ApiKeyOut(**row)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(key_id: str, ws: CurrentWorkspace) -> None:
    await execute(
        "delete from public.api_keys where id = $1 and workspace_id = $2",
        key_id,
        ws.workspace_id,
    )


@router.post("/{key_id}/test")
async def test_key(key_id: str, ws: CurrentWorkspace) -> dict:
    row = await fetchrow(
        "select provider::text, encrypted_key from public.api_keys where id = $1 and workspace_id = $2",
        key_id,
        ws.workspace_id,
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    provider = row["provider"]
    key = decrypt(bytes(row["encrypted_key"]))

    async with httpx.AsyncClient(timeout=10) as client:
        if provider == "anthropic":
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                json={"model": "claude-3-5-haiku-latest", "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
            )
        elif provider == "openai":
            r = await client.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
        elif provider == "gemini":
            r = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
            )
        elif provider == "openrouter":
            r = await client.get(
                "https://openrouter.ai/api/v1/models", headers={"Authorization": f"Bearer {key}"}
            )
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown provider")

    ok = 200 <= r.status_code < 300
    if ok:
        await execute(
            "update public.api_keys set last_used_at = now() where id = $1", key_id
        )
    return {"ok": ok, "status": r.status_code, "snippet": r.text[:200] if not ok else None}


# Internal: load decrypted key for the orchestrator (NOT exposed via router)
async def load_decrypted_key(workspace_id: str, provider: Provider) -> str | None:
    row = await fetchrow(
        """
        select encrypted_key from public.api_keys
        where workspace_id = $1 and provider = $2::api_provider
        order by created_at desc limit 1
        """,
        workspace_id,
        provider,
    )
    if not row:
        return None
    return decrypt(bytes(row["encrypted_key"]))


# Used internally by the orchestrator — not registered on the router.
_ = secrets  # silence import if unused
