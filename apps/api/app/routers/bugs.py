"""Bugs — BugHunterAgent (static mode) workflow + persisted findings.

POST /bugs/static   -> SSE stream: fetch_ticket -> hunt_bugs -> persist -> result
GET  /bugs          -> list, filterable by ticket_key/status/severity
GET  /bugs/{id}     -> single bug + parent run/ticket info
PATCH /bugs/{id}    -> update status (open|resolved)
"""
from __future__ import annotations

import json
import logging
import traceback
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.bug_hunter import hunt_bugs
from app.core.workspace import CurrentWorkspace
from app.db.queries import fetch, fetchrow
from app.services import jira as jira_svc

log = logging.getLogger("qatb.bugs")
router = APIRouter(prefix="/bugs", tags=["bugs"])


# ----------------------------- request models -----------------------------


class StaticBugRequest(BaseModel):
    ticket_key: str
    dev_repo_id: str | None = None


class BugStatusUpdate(BaseModel):
    status: str  # "open" | "resolved"


# ------------------------------ SSE helpers -----------------------------------
# Same hand-rolled convention as plans.py — not worth extracting to a shared
# module for a 2nd consumer.


def _sse_bytes(event: str, data: object) -> bytes:
    payload = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "Content-Type": "text/event-stream; charset=utf-8",
}


def _sse_response(gen: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers=SSE_HEADERS)


# ------------------------------ POST /bugs/static -------------------------


@router.post("/static")
async def hunt_static_bugs(body: StaticBugRequest, ws: CurrentWorkspace) -> StreamingResponse:
    workspace_id = ws.workspace_id
    user_id = ws.user_id
    ticket_key = body.ticket_key.upper()

    async def stream() -> AsyncIterator[bytes]:
        log.info("bugs.stream START key=%s ws=%s", ticket_key, workspace_id)
        try:
            yield _sse_bytes("step", {"name": "fetch_ticket", "status": "start", "key": ticket_key})
            ticket = await jira_svc.get_issue(workspace_id, ticket_key)
            yield _sse_bytes(
                "step",
                {"name": "fetch_ticket", "status": "done", "title": ticket["title"]},
            )

            yield _sse_bytes("step", {"name": "hunt_bugs", "status": "start"})
            envelope = await hunt_bugs(workspace_id, ticket, dev_repo_id=body.dev_repo_id)
            yield _sse_bytes(
                "step",
                {
                    "name": "hunt_bugs",
                    "status": "done",
                    "tokens": envelope.tokens.model_dump(by_alias=True),
                    "confidence": envelope.confidence,
                    "findings": len(envelope.data.findings),
                },
            )

            ticket_row = await fetchrow(
                "select id::text from public.tickets where workspace_id = $1 and jira_key = $2",
                workspace_id,
                ticket_key,
            )
            if not ticket_row:
                raise HTTPException(500, "Ticket row missing after fetch")

            repo_id: str | None = None
            if body.dev_repo_id:
                repo_row = await fetchrow(
                    "select id::text from public.repos where workspace_id = $1 and id = $2::uuid",
                    workspace_id,
                    body.dev_repo_id,
                )
                repo_id = repo_row["id"] if repo_row else None

            run_row = await fetchrow(
                """
                insert into public.runs
                  (workspace_id, kind, ticket_id, repo_id, status, started_at, finished_at, summary, created_by)
                values ($1, 'static', $2, $3::uuid, 'completed', now(), now(), $4, $5)
                returning id::text, created_at::text
                """,
                workspace_id,
                ticket_row["id"],
                repo_id,
                {
                    "findings": len(envelope.data.findings),
                    "confidence": envelope.confidence,
                    "clarifications": envelope.data.clarifications,
                },
                user_id,
            )

            citation_dumps = [c.model_dump() for c in envelope.citations]
            bug_rows: list[dict] = []
            for finding in envelope.data.findings:
                cited_paths = {r.split(":")[0] for r in finding.file_refs}
                finding_citations = [
                    c for c in citation_dumps if c.get("path") in cited_paths
                ]
                row = await fetchrow(
                    """
                    insert into public.bugs
                      (workspace_id, run_id, severity, title, description, file_refs, status)
                    values ($1, $2::uuid, $3::public.bug_severity, $4, $5, $6, 'open')
                    returning id::text, created_at::text
                    """,
                    workspace_id,
                    run_row["id"],
                    finding.severity,
                    finding.title,
                    finding.description,
                    finding_citations,
                )
                bug_rows.append({
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "severity": finding.severity,
                    "title": finding.title,
                    "description": finding.description,
                    "file_refs": finding.file_refs,
                    "citations": finding_citations,
                    "status": "open",
                })

            yield _sse_bytes(
                "result",
                {
                    "run_id": run_row["id"],
                    "ticket": ticket,
                    "bugs": bug_rows,
                    "citations": citation_dumps,
                    "confidence": envelope.confidence,
                    "clarifications": envelope.data.clarifications,
                    "tokens": envelope.tokens.model_dump(by_alias=True),
                    "created_at": run_row["created_at"],
                },
            )
            yield _sse_bytes("done", {})
            log.info("bugs.stream DONE key=%s findings=%d", ticket_key, len(bug_rows))
        except HTTPException as e:
            log.warning("bugs.stream HTTPException %s: %s", e.status_code, e.detail)
            yield _sse_bytes("error", {"message": e.detail, "status": e.status_code})
            yield _sse_bytes("done", {})
        except Exception as e:  # noqa: BLE001
            log.exception("bugs.stream unhandled error")
            yield _sse_bytes(
                "error",
                {"message": str(e), "status": 500, "trace": traceback.format_exc()[-1200:]},
            )
            yield _sse_bytes("done", {})

    return _sse_response(stream())


# --------------------------------- GET /bugs (list) ------------------------


@router.get("")
async def list_bugs(
    ws: CurrentWorkspace,
    ticket_key: str | None = None,
    status_filter: str | None = None,
    severity: str | None = None,
    limit: int = 100,
) -> list[dict]:
    conditions = ["b.workspace_id = $1"]
    args: list = [ws.workspace_id]

    if ticket_key:
        args.append(ticket_key.upper())
        conditions.append(f"t.jira_key = ${len(args)}")
    if status_filter:
        args.append(status_filter)
        conditions.append(f"b.status = ${len(args)}")
    if severity:
        args.append(severity)
        conditions.append(f"b.severity = ${len(args)}::public.bug_severity")

    args.append(limit)
    where = " and ".join(conditions)
    rows = await fetch(
        f"""
        select b.id::text as id, b.created_at::text as created_at,
               b.severity::text as severity, b.title, b.description,
               b.status, b.file_refs,
               t.jira_key as ticket_key, t.title as ticket_title,
               r.id::text as run_id
        from public.bugs b
        join public.runs r on r.id = b.run_id
        left join public.tickets t on t.id = r.ticket_id
        where {where}
        order by b.created_at desc
        limit ${len(args)}
        """,
        *args,
    )
    return rows


# --------------------------------- GET /bugs/{id} ---------------------------


@router.get("/{bug_id}")
async def get_bug(bug_id: str, ws: CurrentWorkspace) -> dict:
    row = await fetchrow(
        """
        select b.id::text as id, b.created_at::text as created_at,
               b.severity::text as severity, b.title, b.description,
               b.status, b.file_refs,
               r.id::text as run_id, r.kind::text as run_kind, r.repo_ref,
               t.jira_key as ticket_key, t.title as ticket_title
        from public.bugs b
        join public.runs r on r.id = b.run_id
        left join public.tickets t on t.id = r.ticket_id
        where b.workspace_id = $1 and b.id = $2::uuid
        """,
        ws.workspace_id,
        bug_id,
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bug not found")
    return row


# --------------------------------- PATCH /bugs/{id} -------------------------


@router.patch("/{bug_id}")
async def update_bug_status(bug_id: str, body: BugStatusUpdate, ws: CurrentWorkspace) -> dict:
    if body.status not in {"open", "resolved"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status must be 'open' or 'resolved'")
    row = await fetchrow(
        """
        update public.bugs set status = $3
        where workspace_id = $1 and id = $2::uuid
        returning id::text, status
        """,
        ws.workspace_id,
        bug_id,
        body.status,
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bug not found")
    return row
