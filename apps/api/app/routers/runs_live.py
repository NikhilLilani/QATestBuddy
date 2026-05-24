"""Live execution endpoints for qa-bridge.

User-facing (JWT-authed) side of Phase 4e:

    POST /runs/{run_id}/execute      — enqueue a bridge job for this run
    GET  /runs/{run_id}/live         — SSE stream of per-test results
    GET  /runs/{run_id}/jobs         — most recent jobs for this run

The CLI-facing endpoints live in `routers/bridge_runner.py`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.workspace import CurrentWorkspace
from app.db.queries import execute, fetch, fetchrow

log = logging.getLogger("qatb.runs_live")
router = APIRouter(tags=["runs-live"])


# ───────────────────────────── SSE helpers ─────────────────────────────


def _sse_bytes(event: str, data: object) -> bytes:
    payload = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "Content-Type": "text/event-stream; charset=utf-8",
}


# ───────────────────── POST /runs/{id}/execute ─────────────────────


class ExecuteRequest(BaseModel):
    # Optional override; defaults to whatever the run was generated against.
    base_url: str | None = None


@router.post("/runs/{run_id}/execute", status_code=status.HTTP_201_CREATED)
async def execute_run(
    run_id: str, body: ExecuteRequest, ws: CurrentWorkspace
) -> dict:
    """Queue a bridge job that will tell a connected qa-bridge CLI to run
    this bundle locally on the user's machine.

    Returns the job ID immediately. Progress comes back via
    GET /runs/{id}/live (SSE).
    """
    # 1) Make sure the run exists in this workspace and pull the bundle.
    run = await fetchrow(
        """
        select r.id::text          as id,
               r.repo_ref           as base_url,
               t.jira_key           as jira_key,
               t.title              as title
          from public.runs r
          left join public.tickets t on t.id = r.ticket_id
         where r.id = $1::uuid
           and r.workspace_id = $2::uuid
           and r.deleted_at is null
        """,
        run_id,
        ws.workspace_id,
    )
    if not run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")

    files = await fetch(
        """
        select repo_path as path, content
          from public.generated_tests
         where run_id = $1::uuid and workspace_id = $2::uuid
         order by repo_path asc
        """,
        run_id,
        ws.workspace_id,
    )
    if not files:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This run has no files — it may be from before bundle support.",
        )

    # 2) Refuse if there's already an active job for this run.
    existing = await fetchrow(
        """
        select id::text as id, status
          from public.bridge_jobs
         where run_id = $1::uuid
           and status in ('pending','claimed','running')
         order by created_at desc
         limit 1
        """,
        run_id,
    )
    if existing:
        return {"job_id": existing["id"], "status": existing["status"], "reused": True}

    # 3) Verify there's at least one non-revoked bridge token (otherwise the
    #    job would just sit pending forever).
    has_token = await fetchrow(
        """
        select 1 from public.bridge_tokens
         where workspace_id = $1::uuid and revoked_at is null
         limit 1
        """,
        ws.workspace_id,
    )
    if not has_token:
        raise HTTPException(
            status.HTTP_412_PRECONDITION_FAILED,
            "No qa-bridge tokens for this workspace. Create one in Settings → qa-bridge "
            "and run `qa-bridge listen` on your machine first.",
        )

    base_url = (body.base_url or run["base_url"] or "").strip()
    payload = {
        "run_id": run_id,
        "ticket_key": run["jira_key"],
        "ticket_title": run["title"],
        "base_url": base_url,
        "files": files,
    }

    job = await fetchrow(
        """
        insert into public.bridge_jobs
          (workspace_id, run_id, payload, created_by)
        values ($1::uuid, $2::uuid, $3::jsonb, $4::uuid)
        returning id::text as id, status, created_at::text
        """,
        ws.workspace_id,
        run_id,
        payload,
        ws.user_id,
    )
    assert job is not None
    log.info("runs.execute queued job=%s run=%s", job["id"], run_id)
    return {"job_id": job["id"], "status": job["status"], "reused": False}


# ───────────────────── GET /runs/{id}/jobs ─────────────────────


@router.get("/runs/{run_id}/jobs")
async def list_jobs_for_run(run_id: str, ws: CurrentWorkspace) -> list[dict]:
    rows = await fetch(
        """
        select id::text                              as id,
               status,
               claimed_at::text                      as claimed_at,
               started_at::text                      as started_at,
               finished_at::text                     as finished_at,
               summary,
               error_message,
               created_at::text                      as created_at
          from public.bridge_jobs
         where run_id = $1::uuid and workspace_id = $2::uuid
         order by created_at desc
         limit 20
        """,
        run_id,
        ws.workspace_id,
    )
    return rows


# ───────────────────── GET /jobs/{id} (with results) ─────────────────────


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, ws: CurrentWorkspace) -> dict:
    head = await fetchrow(
        """
        select id::text                              as id,
               run_id::text                          as run_id,
               status,
               claimed_at::text                      as claimed_at,
               started_at::text                      as started_at,
               finished_at::text                     as finished_at,
               summary,
               error_message,
               created_at::text                      as created_at
          from public.bridge_jobs
         where id = $1::uuid and workspace_id = $2::uuid
        """,
        job_id,
        ws.workspace_id,
    )
    if not head:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    results = await fetch(
        """
        select test_id, file, title, project, status,
               duration_ms, retry, error_message, error_stack, attachments,
               updated_at::text as updated_at
          from public.bridge_test_results
         where job_id = $1::uuid
         order by file asc, title asc, retry asc
        """,
        job_id,
    )
    return {**head, "results": results}


# ───────────────────── POST /jobs/{id}/cancel ─────────────────────


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, ws: CurrentWorkspace) -> dict:
    result = await execute(
        """
        update public.bridge_jobs
           set status      = 'cancelled',
               finished_at = now()
         where id = $1::uuid
           and workspace_id = $2::uuid
           and status in ('pending','claimed','running')
        """,
        job_id,
        ws.workspace_id,
    )
    if result.endswith(" 0"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Job not found or already finished.",
        )
    return {"ok": True}


# ───────────────────── GET /jobs/{id}/live (SSE) ─────────────────────


@router.get("/jobs/{job_id}/live")
async def stream_job_live(job_id: str, ws: CurrentWorkspace) -> StreamingResponse:
    """Stream live test-result updates for a job.

    Implementation note — we poll the DB every ~1s rather than use Postgres
    LISTEN/NOTIFY because Supabase's transaction pooler does not support
    long-lived listeners. The polling is bounded (one query per second,
    workspace-scoped index) and well within free-tier limits.
    """
    workspace_id = ws.workspace_id

    # Up-front 404 so the browser doesn't open an SSE connection just to discover.
    head = await fetchrow(
        "select id from public.bridge_jobs where id = $1::uuid and workspace_id = $2::uuid",
        job_id,
        workspace_id,
    )
    if not head:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")

    async def stream() -> AsyncIterator[bytes]:
        last_result_ts: str | None = None
        last_job_status: str | None = None

        # Initial snapshot — emit everything we know right away so the UI
        # can populate without waiting for the first tick.
        snap = await fetchrow(
            """
            select status, summary, error_message
              from public.bridge_jobs
             where id = $1::uuid
            """,
            job_id,
        )
        if snap:
            yield _sse_bytes("job", snap)
            last_job_status = snap["status"]

        initial = await fetch(
            """
            select test_id, file, title, project, status, duration_ms, retry,
                   error_message, error_stack, attachments,
                   updated_at::text as updated_at
              from public.bridge_test_results
             where job_id = $1::uuid
             order by updated_at asc
            """,
            job_id,
        )
        for r in initial:
            yield _sse_bytes("result", r)
            last_result_ts = r["updated_at"]

        # Poll loop. Bail out after the job has been terminal for one tick
        # so the client gets a final state.
        terminal_emitted = False
        while True:
            await asyncio.sleep(1.0)

            # Job-level update?
            cur = await fetchrow(
                """
                select status, summary, error_message
                  from public.bridge_jobs
                 where id = $1::uuid
                """,
                job_id,
            )
            if cur and cur["status"] != last_job_status:
                yield _sse_bytes("job", cur)
                last_job_status = cur["status"]

            # New / changed results since our last tick.
            if last_result_ts is None:
                new_rows = await fetch(
                    """
                    select test_id, file, title, project, status, duration_ms, retry,
                           error_message, error_stack, attachments,
                           updated_at::text as updated_at
                      from public.bridge_test_results
                     where job_id = $1::uuid
                     order by updated_at asc
                    """,
                    job_id,
                )
            else:
                new_rows = await fetch(
                    """
                    select test_id, file, title, project, status, duration_ms, retry,
                           error_message, error_stack, attachments,
                           updated_at::text as updated_at
                      from public.bridge_test_results
                     where job_id = $1::uuid
                       and updated_at > $2::timestamptz
                     order by updated_at asc
                    """,
                    job_id,
                    last_result_ts,
                )
            for r in new_rows:
                yield _sse_bytes("result", r)
                last_result_ts = r["updated_at"]

            if last_job_status in ("done", "failed", "cancelled"):
                if terminal_emitted:
                    yield _sse_bytes("done", {"status": last_job_status})
                    return
                # One extra tick to drain any straggling results.
                terminal_emitted = True

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
