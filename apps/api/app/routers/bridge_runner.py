"""qa-bridge runner endpoints (CLI-facing).

Auth path:
    The CLI sends its bridge token in the `Authorization: Bearer qatb_...`
    header. We hash it, look up the matching `bridge_tokens` row, and bind
    the request to that workspace. JWT auth is bypassed entirely on these
    endpoints — the CLI never has a Supabase user session.

Endpoints:
    POST /bridge/poll                       — claim the next pending job
    POST /bridge/jobs/{id}/status           — mark running/done/failed
    POST /bridge/jobs/{id}/results          — upsert per-test results

All writes go through the FastAPI service-role asyncpg pool, which bypasses
RLS — that's fine because we have already authenticated the workspace via
the bridge token.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.db.queries import execute, fetchrow, fetch

log = logging.getLogger("qatb.bridge_runner")
router = APIRouter(prefix="/bridge", tags=["bridge-runner"])


# ───────────────────────────── auth ─────────────────────────────


class BridgeCtx(BaseModel):
    workspace_id: str
    token_id: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def get_bridge_ctx(
    authorization: Annotated[str | None, Header()] = None,
) -> BridgeCtx:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bridge token")
    raw = authorization[7:].strip()
    if not raw.startswith("qatb_"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not a bridge token")
    row = await fetchrow(
        """
        select id::text as id, workspace_id::text as workspace_id
          from public.bridge_tokens
         where token_hash = $1 and revoked_at is null
        """,
        _hash(raw),
    )
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked token")
    # Touch last_seen_at so the Settings page can show "active 2m ago".
    await execute(
        "update public.bridge_tokens set last_seen_at = now() where id = $1::uuid",
        row["id"],
    )
    return BridgeCtx(workspace_id=row["workspace_id"], token_id=row["id"])


CurrentBridge = Annotated[BridgeCtx, Depends(get_bridge_ctx)]


# ───────────────────────── POST /bridge/poll ─────────────────────────


class PollResponse(BaseModel):
    job_id: str | None = None
    run_id: str | None = None
    payload: dict | None = None


@router.post("/poll", response_model=PollResponse)
async def poll(bridge: CurrentBridge, wait_seconds: int = 25) -> PollResponse:
    """Atomically claim the oldest pending job in the workspace.

    Returns immediately if a job is available. Otherwise long-polls up to
    `wait_seconds` (capped at 50 — Fly.io / Vercel proxies typically close
    idle connections around 60s). Returns an empty response on timeout so
    the CLI knows to reconnect.
    """
    wait_seconds = max(1, min(wait_seconds, 50))
    deadline = asyncio.get_event_loop().time() + wait_seconds
    while True:
        row = await fetchrow(
            """
            update public.bridge_jobs
               set status           = 'claimed',
                   bridge_token_id  = $1::uuid,
                   claimed_at       = now()
             where id = (
                select id from public.bridge_jobs
                 where workspace_id = $2::uuid
                   and status       = 'pending'
                 order by created_at asc
                 for update skip locked
                 limit 1
             )
            returning id::text     as id,
                      run_id::text as run_id,
                      payload       as payload
            """,
            bridge.token_id,
            bridge.workspace_id,
        )
        if row:
            return PollResponse(
                job_id=row["id"],
                run_id=row["run_id"],
                payload=row["payload"],
            )
        if asyncio.get_event_loop().time() >= deadline:
            return PollResponse()
        await asyncio.sleep(1.0)


# ─────────────────── POST /bridge/jobs/{id}/status ───────────────────


class JobStatusUpdate(BaseModel):
    status: Literal["running", "done", "failed", "cancelled"]
    summary: dict | None = None
    error_message: str | None = None


@router.post("/jobs/{job_id}/status")
async def update_job_status(
    job_id: str, body: JobStatusUpdate, bridge: CurrentBridge
) -> dict:
    # Verify the CLI owns this job (must have claimed it).
    owner = await fetchrow(
        """
        select status, bridge_token_id::text as token_id, workspace_id::text as workspace_id
          from public.bridge_jobs
         where id = $1::uuid
        """,
        job_id,
    )
    if not owner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if owner["workspace_id"] != bridge.workspace_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Wrong workspace")
    if owner["token_id"] != bridge.token_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Job claimed by another bridge")

    new_status = body.status
    started_clause = ""
    finished_clause = ""
    if new_status == "running":
        started_clause = ", started_at = coalesce(started_at, now())"
    if new_status in ("done", "failed", "cancelled"):
        finished_clause = ", finished_at = now()"

    await execute(
        f"""
        update public.bridge_jobs
           set status        = $1
              {started_clause}{finished_clause},
               summary       = coalesce($2::jsonb, summary),
               error_message = coalesce($3, error_message)
         where id = $4::uuid
        """,
        new_status,
        body.summary,
        body.error_message,
        job_id,
    )
    return {"ok": True}


# ─────────────────── POST /bridge/jobs/{id}/results ───────────────────


class TestResultIn(BaseModel):
    test_id: str = Field(min_length=1, max_length=500)
    file: str
    title: str
    project: str | None = None
    status: Literal[
        "pending", "running", "passed", "failed", "skipped", "timedOut", "interrupted"
    ]
    duration_ms: int | None = None
    retry: int = 0
    error_message: str | None = None
    error_stack: str | None = None
    attachments: list[dict] = Field(default_factory=list)


class BulkResultsIn(BaseModel):
    results: list[TestResultIn]


@router.post("/jobs/{job_id}/results")
async def post_results(
    job_id: str, body: BulkResultsIn, bridge: CurrentBridge
) -> dict:
    # Verify ownership once for the whole batch.
    owner = await fetchrow(
        """
        select bridge_token_id::text as token_id,
               workspace_id::text    as workspace_id
          from public.bridge_jobs
         where id = $1::uuid
        """,
        job_id,
    )
    if not owner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if owner["workspace_id"] != bridge.workspace_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Wrong workspace")
    if owner["token_id"] != bridge.token_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Job claimed by another bridge")

    for r in body.results:
        await execute(
            """
            insert into public.bridge_test_results
                (job_id, workspace_id, test_id, file, title, project,
                 status, duration_ms, retry, error_message, error_stack, attachments)
            values ($1::uuid, $2::uuid, $3, $4, $5, $6,
                    $7, $8, $9, $10, $11, $12::jsonb)
            on conflict (job_id, test_id, retry) do update set
                status        = excluded.status,
                duration_ms   = excluded.duration_ms,
                error_message = excluded.error_message,
                error_stack   = excluded.error_stack,
                attachments   = excluded.attachments,
                project       = excluded.project,
                file          = excluded.file,
                title         = excluded.title
            """,
            job_id,
            bridge.workspace_id,
            r.test_id,
            r.file,
            r.title,
            r.project,
            r.status,
            r.duration_ms,
            r.retry,
            r.error_message,
            r.error_stack,
            r.attachments,
        )
    return {"ok": True, "count": len(body.results)}


# ─────────────────── GET /bridge/whoami (sanity check) ───────────────────


@router.get("/whoami")
async def whoami(bridge: CurrentBridge) -> dict:
    ws = await fetchrow(
        "select name from public.workspaces where id = $1::uuid",
        bridge.workspace_id,
    )
    return {
        "workspace_id": bridge.workspace_id,
        "workspace_name": ws["name"] if ws else None,
        "token_id": bridge.token_id,
    }


# ─────────────────── helper used by /api/v1/runs/{id}/execute ───────────────────


async def list_pending_jobs(workspace_id: str) -> list[dict]:
    return await fetch(
        """
        select id::text, status, run_id::text, created_at::text
          from public.bridge_jobs
         where workspace_id = $1::uuid
           and status in ('pending','claimed','running')
         order by created_at desc
        """,
        workspace_id,
    )
