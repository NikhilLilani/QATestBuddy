"""Integrations: Jira (PAT) and GitHub (OAuth).

Jira: user pastes base URL + email + API token. We test against /myself.
GitHub: OAuth Device or Web flow (web flow here).
"""
from __future__ import annotations

import base64
import secrets

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, HttpUrl

from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.workspace import CurrentWorkspace
from app.db.queries import execute, fetchrow

router = APIRouter(prefix="/integrations", tags=["settings"])


# --------------------------------- Jira ----------------------------------


class JiraConnect(BaseModel):
    base_url: HttpUrl
    email: str
    pat: str


@router.post("/jira")
async def connect_jira(body: JiraConnect, ws: CurrentWorkspace) -> dict:
    secret_blob = encrypt(f"{body.email}\n{body.pat}")
    await execute(
        """
        insert into public.integrations (workspace_id, type, config, encrypted_secret, status)
        values ($1, 'jira', $2, $3, 'active')
        on conflict (workspace_id, type) do update
          set config = excluded.config,
              encrypted_secret = excluded.encrypted_secret,
              status = 'active',
              updated_at = now()
        """,
        ws.workspace_id,
        {"base_url": str(body.base_url)},
        secret_blob,
    )
    return {"ok": True}


@router.post("/jira/test")
async def test_jira(ws: CurrentWorkspace) -> dict:
    row = await fetchrow(
        "select config, encrypted_secret from public.integrations where workspace_id = $1 and type = 'jira'",
        ws.workspace_id,
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jira not connected")
    base_url = row["config"]["base_url"].rstrip("/")
    email, pat = decrypt(bytes(row["encrypted_secret"])).split("\n", 1)
    token = base64.b64encode(f"{email}:{pat}".encode()).decode()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{base_url}/rest/api/3/myself",
            headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
        )
    return {"ok": r.is_success, "status": r.status_code, "user": r.json() if r.is_success else None}


@router.get("/jira")
async def get_jira(ws: CurrentWorkspace) -> dict:
    row = await fetchrow(
        "select config, status, last_sync_at::text from public.integrations where workspace_id = $1 and type = 'jira'",
        ws.workspace_id,
    )
    if not row:
        return {"connected": False}
    return {"connected": True, "base_url": row["config"].get("base_url"), "status": row["status"]}


@router.delete("/jira", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_jira(ws: CurrentWorkspace) -> None:
    await execute(
        "delete from public.integrations where workspace_id = $1 and type = 'jira'",
        ws.workspace_id,
    )


# -------------------------------- GitHub ---------------------------------


@router.get("/github/oauth/start")
async def github_oauth_start(ws: CurrentWorkspace) -> dict:
    if not settings.github_oauth_client_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "GitHub OAuth not configured")
    state = secrets.token_urlsafe(24)
    await execute(
        """
        insert into public.audit_log (workspace_id, actor_id, action, target, meta)
        values ($1, $2, 'github.oauth.start', $3, '{}'::jsonb)
        """,
        ws.workspace_id,
        ws.user_id,
        state,
    )
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_oauth_client_id}"
        "&scope=repo%20read:user"
        f"&state={state}"
        f"&redirect_uri={settings.api_url}/api/v1/integrations/github/oauth/callback"
    )
    return {"url": url, "state": state}


@router.get("/github/oauth/callback")
async def github_oauth_callback(code: str, state: str) -> dict:
    # Phase 3 will: validate state, exchange code -> access_token via POST,
    # then store encrypted token + scope on the workspace's integrations row.
    return {"received": True, "code_prefix": code[:6], "state_prefix": state[:6]}
