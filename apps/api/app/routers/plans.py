"""Plans workflow — real PlannerAgent + CaseAuthorAgent.

POST /plans                  -> SSE stream of plan generation
GET  /plans/{id}             -> persisted plan
POST /plans/{id}/cases       -> SSE stream of case generation
GET  /plans/{id}/cases       -> persisted cases
"""
from __future__ import annotations

import json
import logging
import traceback
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.case_author import TestCase, generate_cases
from app.agents.codegen import CodegenInput, generate_playwright_bundle
from app.agents.data_needs import (
    analyze_data_needs,
    format_data_values_for_codegen,
)
from app.agents.fixer import fix_bundle
from app.agents.validator import validate_bundle
from app.agents.planner import PlanContent, generate_plan
from app.core.workspace import CurrentWorkspace
from app.db.queries import execute, fetch, fetchrow
from app.services import jira as jira_svc
from app.services.github_repo import fetch_repo_snapshot, snapshot_to_context

log = logging.getLogger("qatb.plans")
router = APIRouter(tags=["plans"])


# ----------------------------- request models -----------------------------


class PlanRequest(BaseModel):
    ticket_key: str


# ------------------------------ SSE helpers -----------------------------------


def _sse_bytes(event: str, data: object) -> bytes:
    """Format a single SSE frame as bytes.

    Each frame ends with a blank line (\n\n) so consumers (browsers,
    parseSSE on the frontend) can split frames reliably.
    """
    payload = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # disable proxy buffering if behind nginx
    "Content-Type": "text/event-stream; charset=utf-8",
}


def _sse_response(gen: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers=SSE_HEADERS)


# ------------------------------ POST /plans -------------------------------


@router.post("/plans")
async def create_plan(body: PlanRequest, ws: CurrentWorkspace) -> StreamingResponse:
    workspace_id = ws.workspace_id
    user_id = ws.user_id
    ticket_key = body.ticket_key.upper()

    async def stream() -> AsyncIterator[bytes]:
        log.info("plans.stream START key=%s ws=%s", ticket_key, workspace_id)
        try:
            yield _sse_bytes("step", {"name": "fetch_ticket", "status": "start", "key": ticket_key})
            ticket = await jira_svc.get_issue(workspace_id, ticket_key)
            log.info("plans.stream fetched ticket title=%r", ticket.get("title"))
            yield _sse_bytes(
                "step",
                {"name": "fetch_ticket", "status": "done", "title": ticket["title"]},
            )

            yield _sse_bytes("step", {"name": "generate_plan", "status": "start"})
            envelope = await generate_plan(workspace_id, ticket)
            log.info(
                "plans.stream plan generated tokens=%d/%d conf=%s",
                envelope.tokens.in_,
                envelope.tokens.out,
                envelope.confidence,
            )
            yield _sse_bytes(
                "step",
                {
                    "name": "generate_plan",
                    "status": "done",
                    "tokens": envelope.tokens.model_dump(by_alias=True),
                    "confidence": envelope.confidence,
                },
            )

            ticket_row = await fetchrow(
                "select id::text from public.tickets where workspace_id = $1 and jira_key = $2",
                workspace_id,
                ticket_key,
            )
            if not ticket_row:
                raise HTTPException(500, "Ticket row missing after fetch")
            plan_row = await fetchrow(
                """
                insert into public.plans
                  (workspace_id, ticket_id, version, content, citations, tokens_in, tokens_out, created_by)
                values ($1, $2, 1, $3, $4, $5, $6, $7)
                returning id::text, created_at::text
                """,
                workspace_id,
                ticket_row["id"],
                envelope.data.model_dump(),
                [c.model_dump() for c in envelope.citations],
                envelope.tokens.in_,
                envelope.tokens.out,
                user_id,
            )
            log.info("plans.stream persisted id=%s", plan_row["id"])

            yield _sse_bytes(
                "result",
                {
                    "id": plan_row["id"],
                    "ticket": ticket,
                    "data": envelope.data.model_dump(),
                    "citations": [c.model_dump() for c in envelope.citations],
                    "confidence": envelope.confidence,
                    "clarifications_used": envelope.clarifications_used,
                    "clarifications": envelope.clarifications,
                    "tokens": envelope.tokens.model_dump(by_alias=True),
                    "created_at": plan_row["created_at"],
                },
            )
            yield _sse_bytes("done", {})
            log.info("plans.stream DONE")
        except HTTPException as e:
            log.warning("plans.stream HTTPException %s: %s", e.status_code, e.detail)
            yield _sse_bytes("error", {"message": e.detail, "status": e.status_code})
            yield _sse_bytes("done", {})
        except Exception as e:  # noqa: BLE001
            log.exception("plans.stream unhandled error")
            yield _sse_bytes(
                "error",
                {
                    "message": str(e),
                    "status": 500,
                    "trace": traceback.format_exc()[-1200:],
                },
            )
            yield _sse_bytes("done", {})

    return _sse_response(stream())


# --------------------------- GET /plans  (list) ---------------------------


@router.get("/plans")
async def list_plans(ws: CurrentWorkspace, limit: int = 50) -> list[dict]:
    """All plans in the workspace, newest first, with ticket info + case count.

    Soft-deleted plans are hidden.
    """
    rows = await fetch(
        """
        select p.id::text                                   as id,
               p.created_at::text                           as created_at,
               p.tokens_in                                  as tokens_in,
               p.tokens_out                                 as tokens_out,
               t.jira_key                                   as jira_key,
               t.title                                      as title,
               t.status                                     as status,
               (select count(*) from public.cases c
                 where c.plan_id = p.id and c.deleted_at is null) as case_count,
               (select count(*) from public.runs r
                 where r.ticket_id = t.id
                   and r.kind = 'playwright_local'
                   and r.deleted_at is null) as run_count
        from public.plans p
        join public.tickets t on t.id = p.ticket_id
        where p.workspace_id = $1
          and p.deleted_at is null
        order by p.created_at desc
        limit $2
        """,
        ws.workspace_id,
        limit,
    )
    return rows


# --------------------------- GET /runs    (list) ---------------------------


@router.get("/runs")
async def list_runs(ws: CurrentWorkspace, limit: int = 50) -> list[dict]:
    """All codegen runs in the workspace, newest first. Soft-deleted runs hidden."""
    rows = await fetch(
        """
        select r.id::text                              as id,
               r.kind::text                            as kind,
               r.status                                as status,
               r.created_at::text                      as created_at,
               r.repo_ref                              as base_url,
               r.summary                               as summary,
               t.jira_key                              as jira_key,
               t.title                                 as title,
               (select count(*) from public.generated_tests g where g.run_id = r.id) as file_count
        from public.runs r
        left join public.tickets t on t.id = r.ticket_id
        where r.workspace_id = $1
          and r.deleted_at is null
        order by r.created_at desc
        limit $2
        """,
        ws.workspace_id,
        limit,
    )
    return rows


# --------------------- GET /runs/{id}/files  (re-download) ----------------


@router.get("/runs/{run_id}/files")
async def get_run_files(run_id: str, ws: CurrentWorkspace) -> dict:
    """Return all generated files for a past codegen run, for re-download / re-view."""
    head = await fetchrow(
        """
        select r.id::text          as id,
               r.created_at::text  as created_at,
               r.summary            as summary,
               r.repo_ref           as base_url,
               t.jira_key           as jira_key,
               t.title              as title
        from public.runs r
        left join public.tickets t on t.id = r.ticket_id
        where r.id = $1
          and r.workspace_id = $2
          and r.deleted_at is null
        """,
        run_id,
        ws.workspace_id,
    )
    if not head:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    files = await fetch(
        """
        select repo_path as path, content
        from public.generated_tests
        where run_id = $1 and workspace_id = $2
        order by repo_path asc
        """,
        run_id,
        ws.workspace_id,
    )
    return {**head, "files": files}


# --------------------------- DELETE /plans/{id} ---------------------------


class BulkDeleteRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


@router.post("/plans/bulk-delete")
async def bulk_delete_plans(body: BulkDeleteRequest, ws: CurrentWorkspace) -> dict:
    """Soft-delete many plans at once + cascade to their cases.

    Idempotent: rows already soft-deleted are unaffected. Returns the count
    actually transitioned from active to deleted.
    """
    plans_result = await execute(
        """
        update public.plans
           set deleted_at = now()
         where id = any($1::uuid[])
           and workspace_id = $2
           and deleted_at is null
        """,
        body.ids,
        ws.workspace_id,
    )
    # Cascade soft-delete to child cases
    await execute(
        """
        update public.cases
           set deleted_at = now()
         where plan_id = any($1::uuid[])
           and workspace_id = $2
           and deleted_at is null
        """,
        body.ids,
        ws.workspace_id,
    )
    # asyncpg returns "UPDATE n"
    deleted = int(plans_result.split()[-1]) if plans_result else 0
    return {"deleted": deleted}


@router.post("/cases/bulk-delete")
async def bulk_delete_cases(body: BulkDeleteRequest, ws: CurrentWorkspace) -> dict:
    result = await execute(
        """
        update public.cases
           set deleted_at = now()
         where id = any($1::uuid[])
           and workspace_id = $2
           and deleted_at is null
        """,
        body.ids,
        ws.workspace_id,
    )
    deleted = int(result.split()[-1]) if result else 0
    return {"deleted": deleted}


@router.post("/runs/bulk-delete")
async def bulk_delete_runs(body: BulkDeleteRequest, ws: CurrentWorkspace) -> dict:
    result = await execute(
        """
        update public.runs
           set deleted_at = now()
         where id = any($1::uuid[])
           and workspace_id = $2
           and deleted_at is null
        """,
        body.ids,
        ws.workspace_id,
    )
    deleted = int(result.split()[-1]) if result else 0
    return {"deleted": deleted}


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(plan_id: str, ws: CurrentWorkspace) -> None:
    """Soft-delete a plan + cascade soft-delete its cases. Rows stay in the DB
    for recovery/audit but are hidden from all list/get queries."""
    result = await execute(
        """
        update public.plans
           set deleted_at = now()
         where id = $1
           and workspace_id = $2
           and deleted_at is null
        """,
        plan_id,
        ws.workspace_id,
    )
    if result.endswith(" 0"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found or already deleted")
    # Cascade: soft-delete child cases so list_cases doesn't surface them.
    await execute(
        """
        update public.cases
           set deleted_at = now()
         where plan_id = $1
           and workspace_id = $2
           and deleted_at is null
        """,
        plan_id,
        ws.workspace_id,
    )


# --------------------------- DELETE /cases/{id} ---------------------------


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(case_id: str, ws: CurrentWorkspace) -> None:
    """Soft-delete a single test case."""
    result = await execute(
        """
        update public.cases
           set deleted_at = now()
         where id = $1
           and workspace_id = $2
           and deleted_at is null
        """,
        case_id,
        ws.workspace_id,
    )
    if result.endswith(" 0"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found or already deleted")


# --------------------------- DELETE /runs/{id} ----------------------------


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(run_id: str, ws: CurrentWorkspace) -> None:
    """Soft-delete a codegen run. Generated files stay but are hidden via the
    parent's deleted_at."""
    result = await execute(
        """
        update public.runs
           set deleted_at = now()
         where id = $1
           and workspace_id = $2
           and deleted_at is null
        """,
        run_id,
        ws.workspace_id,
    )
    if result.endswith(" 0"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found or already deleted")


# --------------------------- POST .../restore -----------------------------


@router.post("/plans/{plan_id}/restore", status_code=status.HTTP_200_OK)
async def restore_plan(plan_id: str, ws: CurrentWorkspace) -> dict:
    """Undo a soft-delete on a plan + cascade to its cases."""
    result = await execute(
        "update public.plans set deleted_at = null where id = $1 and workspace_id = $2",
        plan_id,
        ws.workspace_id,
    )
    if result.endswith(" 0"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    await execute(
        "update public.cases set deleted_at = null where plan_id = $1 and workspace_id = $2",
        plan_id,
        ws.workspace_id,
    )
    return {"ok": True}


@router.post("/cases/{case_id}/restore", status_code=status.HTTP_200_OK)
async def restore_case(case_id: str, ws: CurrentWorkspace) -> dict:
    result = await execute(
        "update public.cases set deleted_at = null where id = $1 and workspace_id = $2",
        case_id,
        ws.workspace_id,
    )
    if result.endswith(" 0"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")
    return {"ok": True}


@router.post("/runs/{run_id}/fix-issues")
async def fix_run_issues(run_id: str, ws: CurrentWorkspace) -> dict:
    """Manual re-fix pass on a persisted bundle.

    Used when the user looks at the BundleViewer's validator panel and sees
    leftover errors — they can click "Fix with AI" to trigger another
    validator → fixer → re-validator loop without regenerating the bundle
    from scratch. Patched files replace the existing `generated_tests` rows
    and the run summary is updated.
    """
    from app.agents.codegen import CodegenBundle, GeneratedFile

    head = await fetchrow(
        """
        select r.id::text                              as id,
               r.summary                                as summary,
               r.repo_ref                              as base_url,
               coalesce((r.summary ->> 'fix_iterations')::int, 0) as fix_iterations
          from public.runs r
         where r.id = $1 and r.workspace_id = $2 and r.deleted_at is null
        """,
        run_id,
        ws.workspace_id,
    )
    if not head:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    files = await fetch(
        """
        select repo_path as path, content
          from public.generated_tests
         where run_id = $1 and workspace_id = $2
         order by repo_path
        """,
        run_id,
        ws.workspace_id,
    )
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Run has no files to fix.")

    bundle = CodegenBundle(
        files=[GeneratedFile(path=f["path"], operation="update", content=f["content"]) for f in files],
    )

    # Validate → fix → re-validate (single pass; user can click again).
    report = await validate_bundle(ws.workspace_id, bundle, "")
    err_count_before = sum(1 for i in report.issues if i.severity == "error")
    if err_count_before == 0:
        return {"ok": True, "errors_before": 0, "errors_after": 0, "patched": 0, "message": "No errors to fix."}

    new_bundle, fixer = await fix_bundle(ws.workspace_id, bundle, report, "", iteration=head["fix_iterations"] + 1)

    # Re-validate
    report2 = await validate_bundle(ws.workspace_id, new_bundle, "")
    err_count_after = sum(1 for i in report2.issues if i.severity == "error")

    # Persist patched files
    if fixer.patches:
        await execute(
            "delete from public.generated_tests where run_id = $1 and workspace_id = $2",
            run_id,
            ws.workspace_id,
        )
        for f in new_bundle.files:
            await execute(
                "insert into public.generated_tests (workspace_id, run_id, repo_path, content) values ($1, $2, $3, $4)",
                ws.workspace_id,
                run_id,
                f.path,
                f.content,
            )
        # Update run summary with new counts
        new_summary = dict(head["summary"] or {})
        new_summary["fix_iterations"] = head["fix_iterations"] + 1
        new_summary["errors_auto_fixed"] = (
            int(new_summary.get("errors_auto_fixed", 0)) + max(0, err_count_before - err_count_after)
        )
        new_summary["validator_errors"] = err_count_after
        await execute(
            "update public.runs set summary = $1::jsonb where id = $2 and workspace_id = $3",
            new_summary,
            run_id,
            ws.workspace_id,
        )

    return {
        "ok": True,
        "errors_before": err_count_before,
        "errors_after": err_count_after,
        "patched": len(fixer.patches),
        "notes": fixer.notes,
        "unresolved": fixer.unresolved,
        "files": [f.model_dump() for f in new_bundle.files],
        "validator": {
            "issues": [i.model_dump() for i in report2.issues],
            "summary": report2.summary,
        },
    }


@router.post("/runs/{run_id}/restore", status_code=status.HTTP_200_OK)
async def restore_run(run_id: str, ws: CurrentWorkspace) -> dict:
    result = await execute(
        "update public.runs set deleted_at = null where id = $1 and workspace_id = $2",
        run_id,
        ws.workspace_id,
    )
    if result.endswith(" 0"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return {"ok": True}


# --------------------------- GET /plans/{id} ------------------------------


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, ws: CurrentWorkspace) -> dict:
    row = await fetchrow(
        """
        select p.id::text, p.content, p.citations, p.tokens_in, p.tokens_out,
               p.created_at::text,
               t.jira_key, t.title, t.description, t.status as t_status,
               t.raw -> 'fields' -> 'priority' ->> 'name' as priority,
               t.raw -> 'fields' -> 'issuetype' ->> 'name' as issuetype
        from public.plans p
        join public.tickets t on t.id = p.ticket_id
        where p.id = $1 and p.workspace_id = $2 and p.deleted_at is null
        """,
        plan_id,
        ws.workspace_id,
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    return {
        "id": row["id"],
        "data": row["content"],
        "citations": row["citations"],
        "tokens": {"in": row["tokens_in"], "out": row["tokens_out"]},
        "created_at": row["created_at"],
        "ticket": {
            "key": row["jira_key"],
            "title": row["title"],
            "description": row["description"],
            "status": row["t_status"],
            "priority": row["priority"],
            "issuetype": row["issuetype"],
        },
    }


# --------------------------- POST /plans/{id}/cases -----------------------


@router.post("/plans/{plan_id}/cases")
async def create_cases(plan_id: str, ws: CurrentWorkspace) -> StreamingResponse:
    workspace_id = ws.workspace_id

    plan_row = await fetchrow(
        """
        select p.content, t.jira_key, t.title, t.description, t.status,
               t.raw -> 'fields' -> 'priority' ->> 'name' as priority,
               t.raw -> 'fields' -> 'issuetype' ->> 'name' as issuetype
        from public.plans p
        join public.tickets t on t.id = p.ticket_id
        where p.id = $1 and p.workspace_id = $2 and p.deleted_at is null
        """,
        plan_id,
        workspace_id,
    )
    if not plan_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")

    ticket = {
        "key": plan_row["jira_key"],
        "title": plan_row["title"],
        "description": plan_row["description"],
        "status": plan_row["status"],
        "priority": plan_row["priority"],
        "issuetype": plan_row["issuetype"],
    }
    plan_content = PlanContent.model_validate(plan_row["content"])

    async def stream() -> AsyncIterator[bytes]:
        try:
            yield _sse_bytes("step", {"name": "generate_cases", "status": "start"})
            envelope = await generate_cases(workspace_id, ticket, plan_content)
            yield _sse_bytes(
                "step",
                {
                    "name": "generate_cases",
                    "status": "done",
                    "count": len(envelope.data),
                    "tokens": envelope.tokens.model_dump(by_alias=True),
                },
            )

            await execute(
                "delete from public.cases where plan_id = $1 and workspace_id = $2",
                plan_id,
                workspace_id,
            )
            rows = []
            for i, case in enumerate(envelope.data):
                row = await fetchrow(
                    """
                    insert into public.cases
                      (workspace_id, plan_id, ord, title, gherkin, fields, citations, status)
                    values ($1, $2, $3, $4, $5, $6, $7, 'draft')
                    returning id::text
                    """,
                    workspace_id,
                    plan_id,
                    i,
                    case.title,
                    _to_gherkin(case),
                    case.model_dump(),
                    [c.model_dump() for c in envelope.citations],
                )
                rows.append(
                    {
                        "id": row["id"],
                        "ord": i,
                        **case.model_dump(),
                        "citations": [c.model_dump() for c in envelope.citations],
                    }
                )

            yield _sse_bytes(
                "result",
                {
                    "cases": rows,
                    "confidence": envelope.confidence,
                    "clarifications_used": envelope.clarifications_used,
                    "clarifications": envelope.clarifications,
                    "tokens": envelope.tokens.model_dump(by_alias=True),
                },
            )
            yield _sse_bytes("done", {})
        except HTTPException as e:
            yield _sse_bytes("error", {"message": e.detail, "status": e.status_code})
            yield _sse_bytes("done", {})
        except Exception as e:  # noqa: BLE001
            yield _sse_bytes(
                "error",
                {
                    "message": str(e),
                    "status": 500,
                    "trace": traceback.format_exc()[-1200:],
                },
            )
            yield _sse_bytes("done", {})

    return _sse_response(stream())


# --------- POST /tickets/{key}/cases (direct, no plan required) -----------


class DirectCasesRequest(BaseModel):
    # M5: either ticket_key XOR flow_text must be provided. flow_text is
    # the "no Jira, just describe the flow" entry point for Flow 2.
    ticket_key: str = ""
    flow_text: str = ""
    flow_title: str = ""


@router.post("/cases")
async def create_cases_direct(body: DirectCasesRequest, ws: CurrentWorkspace) -> StreamingResponse:
    """Generate test cases from a Jira ticket OR from a free-text flow.

    Creates a stub plan record so cases still have a parent (plan_id is NOT
    NULL in the schema). When using flow_text, also creates a synthetic
    `tickets` row with jira_key = '_flow_<hash>' so the schema is satisfied.
    """
    workspace_id = ws.workspace_id
    user_id = ws.user_id
    ticket_key = body.ticket_key.upper() if body.ticket_key else ""
    flow_text = (body.flow_text or "").strip()

    if not ticket_key and not flow_text:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Either ticket_key or flow_text must be provided.",
        )

    async def stream() -> AsyncIterator[bytes]:
        try:
            if ticket_key:
                yield _sse_bytes(
                    "step", {"name": "fetch_ticket", "status": "start", "key": ticket_key},
                )
                ticket = await jira_svc.get_issue(workspace_id, ticket_key)
                yield _sse_bytes(
                    "step",
                    {"name": "fetch_ticket", "status": "done", "title": ticket["title"]},
                )
            else:
                # Build a virtual ticket from the supplied flow. The key
                # prefix `_flow_` is the signal case_author uses to switch
                # its prompt framing (no Jira, no description field cite).
                import hashlib
                virt_key = "_flow_" + hashlib.sha256(flow_text.encode()).hexdigest()[:8]
                ticket = {
                    "key": virt_key,
                    "title": (body.flow_title or "User-supplied flow")[:200],
                    "description": flow_text,
                    "status": "open",
                    "priority": "P2",
                    "issuetype": "Test",
                    "attachments": [],
                }
                yield _sse_bytes(
                    "step",
                    {"name": "fetch_ticket", "status": "done",
                     "title": ticket["title"], "source": "flow_text"},
                )
                # Persist the virtual ticket so plans.ticket_id has a parent.
                # We dedupe by jira_key so re-submitting the same flow text
                # within a workspace reuses the row instead of duplicating.
                existing = await fetchrow(
                    "select id::text from public.tickets "
                    "where workspace_id = $1 and jira_key = $2",
                    workspace_id, virt_key,
                )
                if not existing:
                    await execute(
                        """
                        insert into public.tickets
                          (workspace_id, jira_key, title, description, status, raw)
                        values ($1, $2, $3, $4, 'open', $5::jsonb)
                        on conflict (workspace_id, jira_key) do nothing
                        """,
                        workspace_id, virt_key, ticket["title"], ticket["description"],
                        {"source": "flow_text", "fields": {}},
                    )
                # Use the synthetic key downstream so the rest of the stream
                # treats this exactly like a Jira ticket.
                ticket_key = virt_key

            yield _sse_bytes("step", {"name": "generate_cases", "status": "start"})
            envelope = await generate_cases(workspace_id, ticket, plan=None)
            yield _sse_bytes(
                "step",
                {
                    "name": "generate_cases",
                    "status": "done",
                    "count": len(envelope.data),
                    "tokens": envelope.tokens.model_dump(by_alias=True),
                },
            )

            ticket_row = await fetchrow(
                "select id::text from public.tickets where workspace_id = $1 and jira_key = $2",
                workspace_id,
                ticket_key,
            )
            if not ticket_row:
                raise HTTPException(500, "Ticket row missing after fetch")

            # Auto-create a stub plan record so cases.plan_id has a parent.
            stub_plan_content = {
                "scope": f"Auto-generated cases for {ticket_key} (no plan)",
                "in_scope": [],
                "out_of_scope": [],
                "risks": [],
                "environments": [],
                "data_needs": [],
                "exit_criteria": [],
            }
            stub_citation = [{"kind": "jira", "key": ticket_key, "field": "description"}]
            plan_row = await fetchrow(
                """
                insert into public.plans
                  (workspace_id, ticket_id, version, content, citations, tokens_in, tokens_out, created_by)
                values ($1, $2, 1, $3, $4, 0, 0, $5)
                returning id::text, created_at::text
                """,
                workspace_id,
                ticket_row["id"],
                stub_plan_content,
                stub_citation,
                user_id,
            )

            # Insert all cases
            rows = []
            for i, case in enumerate(envelope.data):
                row = await fetchrow(
                    """
                    insert into public.cases
                      (workspace_id, plan_id, ord, title, gherkin, fields, citations, status)
                    values ($1, $2, $3, $4, $5, $6, $7, 'draft')
                    returning id::text
                    """,
                    workspace_id,
                    plan_row["id"],
                    i,
                    case.title,
                    _to_gherkin(case),
                    case.model_dump(),
                    [c.model_dump() for c in envelope.citations],
                )
                rows.append(
                    {
                        "id": row["id"],
                        "ord": i,
                        **case.model_dump(),
                        "citations": [c.model_dump() for c in envelope.citations],
                    }
                )

            yield _sse_bytes(
                "result",
                {
                    "plan_id": plan_row["id"],
                    "cases": rows,
                    "confidence": envelope.confidence,
                    "clarifications_used": envelope.clarifications_used,
                    "clarifications": envelope.clarifications,
                    "tokens": envelope.tokens.model_dump(by_alias=True),
                },
            )
            yield _sse_bytes("done", {})
        except HTTPException as e:
            log.warning("cases.direct HTTPException %s: %s", e.status_code, e.detail)
            yield _sse_bytes("error", {"message": e.detail, "status": e.status_code})
            yield _sse_bytes("done", {})
        except Exception as e:  # noqa: BLE001
            log.exception("cases.direct unhandled error")
            yield _sse_bytes(
                "error",
                {
                    "message": str(e),
                    "status": 500,
                    "trace": traceback.format_exc()[-1200:],
                },
            )
            yield _sse_bytes("done", {})

    return _sse_response(stream())


# --------------------------- GET /plans/{id}/cases ------------------------


@router.get("/plans/{plan_id}/cases")
async def list_cases(plan_id: str, ws: CurrentWorkspace) -> list[dict]:
    rows = await fetch(
        """
        select id::text, ord, title, fields, citations, status::text, created_at::text,
               coalesce(fields->>'automation_candidate', 'yes') as automation_candidate,
               coalesce(fields->>'automation_reason', '')       as automation_reason
        from public.cases
        where plan_id = $1
          and workspace_id = $2
          and deleted_at is null
        order by ord asc
        """,
        plan_id,
        ws.workspace_id,
    )
    return [
        {
            "id": r["id"],
            "ord": r["ord"],
            "title": r["title"],
            "status": r["status"],
            "created_at": r["created_at"],
            **r["fields"],
            "citations": r["citations"],
        }
        for r in rows
    ]


# --------------- POST /plans/{id}/codegen -- Playwright .spec.ts ----------


class CodegenRequest(BaseModel):
    base_url: str
    framework_hints: str = ""
    test_file_name: str = "generated.spec.ts"
    framework_id: str | None = None
    local_path: str = ""
    # Test data the user filled in for the data-needs step. Keys are the
    # `DataNeed.key` values returned by /data-needs. We render them as a hint
    # block appended to framework_hints + bake env vars into .env.example.
    data_values: dict[str, str] = Field(default_factory=dict)
    data_needs: list[dict] = Field(default_factory=list)
    # Automation triage — if provided, codegen runs ONLY for these case IDs.
    # Empty list = use all non-skipped cases (status != automation_candidate=no).
    case_ids: list[str] = Field(default_factory=list)
    # M4: dev-repo selection for selector/route grounding. Optional.
    # When set, find_locator/find_route are scoped to this repo only —
    # multi-app workspaces don't bleed selectors across products.
    dev_repo_id: str | None = None
    # M4: user answers to a prior clarify SSE event. The key is the
    # ClarifyQuestion.id; the value is whatever the user typed. The codegen
    # request resumes with these answers folded into the grounded set.
    clarifications: dict[str, str] = Field(default_factory=dict)
    # M5: project state branching. If omitted we auto-detect via
    # /project-state/detect (same heuristics). Pass a value to override.
    project_state: str | None = None
    migration_mode: str = "migrate"
    # M5: per-case override prompts, keyed by case ord (as string).
    case_overrides: dict[str, str] = Field(default_factory=dict)


class DataNeedsRequest(BaseModel):
    ticket_key: str
    framework_id: str | None = None
    flow_guidance: str = ""


@router.post("/data-needs")
async def detect_data_needs(body: DataNeedsRequest, ws: CurrentWorkspace) -> dict:
    """Look at the ticket + framework + flow_guidance and propose what
    concrete test data the user must provide before codegen.

    Returns a list of inputs the frontend renders as a dynamic form.
    """
    workspace_id = ws.workspace_id
    ticket_key = body.ticket_key.upper()
    ticket = await jira_svc.get_issue(workspace_id, ticket_key)

    framework_snapshot = ""
    if body.framework_id:
        fw = await fetchrow(
            """
            select source_kind::text, git_url, git_branch, conventions
              from public.frameworks
             where id = $1 and workspace_id = $2
            """,
            body.framework_id,
            workspace_id,
        )
        if fw:
            framework_snapshot = fw["conventions"] or ""
            if fw["source_kind"] == "git" and fw["git_url"]:
                try:
                    snap = await fetch_repo_snapshot(fw["git_url"], fw["git_branch"])
                    framework_snapshot = (
                        framework_snapshot
                        + "\n\n"
                        + snapshot_to_context(snap, max_chars=6000)
                    ).strip()
                except Exception as e:  # noqa: BLE001
                    log.warning("data-needs: repo snapshot failed: %s", e)

    report = await analyze_data_needs(
        workspace_id, ticket, body.flow_guidance, framework_snapshot
    )
    return {
        "inputs": [n.model_dump() for n in report.inputs],
        "notes": report.notes,
    }


@router.post("/plans/{plan_id}/codegen")
async def create_codegen(
    plan_id: str, body: CodegenRequest, ws: CurrentWorkspace
) -> StreamingResponse:
    """Translate the plan's test cases into a Playwright TypeScript spec file."""
    workspace_id = ws.workspace_id

    # Load plan + ticket + cases up-front so we can fail fast outside the stream
    head = await fetchrow(
        """
        select p.id::text, t.jira_key, t.title, t.description, t.status,
               t.raw -> 'fields' -> 'priority' ->> 'name' as priority,
               t.raw -> 'fields' -> 'issuetype' ->> 'name' as issuetype
        from public.plans p
        join public.tickets t on t.id = p.ticket_id
        where p.id = $1 and p.workspace_id = $2 and p.deleted_at is null
        """,
        plan_id,
        workspace_id,
    )
    if not head:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    # Load cases. If case_ids was provided, narrow to those (explicit user
    # selection). Otherwise default to "all cases the LLM marked as 'yes' or
    # 'partial' for automation" — anything marked 'no' is skipped because
    # those aren't supposed to be automated.
    if body.case_ids:
        case_rows = await fetch(
            """
            select ord, title, fields
            from public.cases
            where id = any($1::uuid[])
              and plan_id = $2
              and workspace_id = $3
              and deleted_at is null
            order by ord asc
            """,
            body.case_ids,
            plan_id,
            workspace_id,
        )
    else:
        case_rows = await fetch(
            """
            select ord, title, fields
            from public.cases
            where plan_id = $1 and workspace_id = $2 and deleted_at is null
              and coalesce(fields->>'automation_candidate', 'yes') in ('yes','partial')
            order by ord asc
            """,
            plan_id,
            workspace_id,
        )
    if not case_rows:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No automatable test cases for this plan. Either generate cases first, "
            "mark some cases as automatable, or pass explicit case_ids.",
        )

    ticket = {
        "key": head["jira_key"],
        "title": head["title"],
        "description": head["description"],
        "status": head["status"],
        "priority": head["priority"],
        "issuetype": head["issuetype"],
    }
    cases = [TestCase.model_validate(r["fields"]) for r in case_rows]

    # Load framework context if a framework was selected. For Git source we
    # fetch a small snapshot to inform locator + import conventions.
    framework_context = ""
    framework_hints = body.framework_hints or ""
    if body.framework_id:
        fw = await fetchrow(
            """
            select source_kind::text, git_url, git_branch, conventions
            from public.frameworks where id = $1 and workspace_id = $2
            """,
            body.framework_id,
            workspace_id,
        )
        if fw:
            framework_hints = f"{framework_hints}\n\n{fw['conventions']}".strip()
            if fw["source_kind"] == "git" and fw["git_url"]:
                try:
                    snap = await fetch_repo_snapshot(fw["git_url"], fw["git_branch"])
                    framework_context = snapshot_to_context(snap, max_chars=10000)
                except Exception as e:  # noqa: BLE001
                    log.warning("codegen: could not fetch repo snapshot: %s", e)

    # If the user filled out the data-needs step, append concrete values to
    # the hints block. The codegen LLM then bakes them into .env.example
    # and references them via process.env in the generated tests.
    if body.data_values:
        from app.agents.data_needs import DataNeed

        needs_models = []
        for raw in body.data_needs:
            try:
                needs_models.append(DataNeed.model_validate(raw))
            except Exception:  # noqa: BLE001
                continue
        data_hint = format_data_values_for_codegen(body.data_values, needs_models)
        if data_hint:
            framework_hints = f"{framework_hints}\n{data_hint}".strip()

    # M5: resolve project state. User override wins; otherwise auto-detect.
    from app.services.project_state import detect_state as _detect_state
    if body.project_state in ("new_project", "existing_no_tests",
                              "existing_same_fw", "existing_diff_fw"):
        resolved_state = body.project_state
    else:
        verdict = await _detect_state(
            workspace_id=workspace_id,
            framework_id=body.framework_id,
            dev_repo_id=body.dev_repo_id,
        )
        resolved_state = verdict.state
        log.info(
            "codegen: auto-detected project_state=%s (confidence=%.2f, evidence=%s)",
            resolved_state, verdict.confidence, "; ".join(verdict.evidence)[:200],
        )

    inp = CodegenInput(
        base_url=body.base_url.strip(),
        framework_hints=framework_hints,
        test_file_name=body.test_file_name.strip() or "generated.spec.ts",
        framework_context=framework_context,
        local_path=body.local_path.strip(),
        project_state=resolved_state,  # type: ignore[arg-type]
        migration_mode=(
            body.migration_mode if body.migration_mode in ("migrate", "keep_both") else "migrate"
        ),  # type: ignore[arg-type]
        case_overrides=body.case_overrides,
    )

    async def stream() -> AsyncIterator[bytes]:
        try:
            # Stage 0 — pre-flight grounding (M4)
            # If the workspace has an indexed dev repo, every UI intent in
            # the cases must resolve to a real locator/route or the user has
            # to clarify. Returns immediately with an empty grounded block
            # when no dev repo is indexed.
            from app.agents.grounding import postflight_scan, preflight_grounding
            yield _sse_bytes("step", {"name": "grounding", "status": "start"})
            preflight = await preflight_grounding(
                workspace_id=workspace_id,
                cases=cases,
                dev_repo_id=body.dev_repo_id,
                user_clarifications=body.clarifications,
            )
            yield _sse_bytes(
                "step",
                {
                    "name": "grounding",
                    "status": "done",
                    "intents_total": preflight.intents_total,
                    "intents_resolved": preflight.intents_resolved,
                    "clarify_count": len(preflight.clarify),
                },
            )

            # If anything is unresolved, halt and ask the user. The frontend
            # collects the answers and re-POSTs with `clarifications` filled
            # in; this same stream restarts from Stage 0 and the answered
            # questions are now treated as canonical grounded refs.
            if preflight.clarify:
                yield _sse_bytes(
                    "clarify",
                    {
                        "stage": "preflight",
                        "questions": [
                            {
                                "id": q.id,
                                "label": q.label,
                                "reason": q.reason,
                                "kind": q.kind,
                                "intent": q.intent,
                                "placeholder": q.placeholder,
                                "suggested": q.suggested,
                            }
                            for q in preflight.clarify
                        ],
                        "resolved": [
                            {
                                "intent": g.intent,
                                "kind": g.kind,
                                "value": g.value,
                                "selector_kind": g.selector_kind,
                                "source_file": g.source_file,
                                "source_line": g.source_line,
                            }
                            for g in preflight.grounded
                        ],
                    },
                )
                yield _sse_bytes("done", {})
                return

            # All intents resolved — inject the grounded block into the prompt.
            inp.grounded_block = preflight.prompt_block

            # Stage 1 — codegen
            yield _sse_bytes(
                "step",
                {"name": "codegen", "status": "start", "cases": len(cases), "base_url": inp.base_url},
            )
            envelope = await generate_playwright_bundle(workspace_id, ticket, cases, inp)
            total_lines = sum(f.content.count("\n") + 1 for f in envelope.data.files)

            # Stage 1.5 — post-flight scan (M4). Catches anything the model
            # invented in spite of the grounded block. Only blocks when we
            # have something to check against (i.e. an indexed dev repo).
            postflight = await postflight_scan(
                workspace_id=workspace_id,
                files=envelope.data.files,
                grounded_locator_values={
                    g.value for g in preflight.grounded if g.kind == "locator"
                },
                grounded_route_values={
                    g.value for g in preflight.grounded if g.kind == "route"
                },
                dev_repo_id=body.dev_repo_id,
            )
            if postflight.clarify:
                yield _sse_bytes(
                    "clarify",
                    {
                        "stage": "postflight",
                        "questions": [
                            {
                                "id": q.id,
                                "label": q.label,
                                "reason": q.reason,
                                "kind": q.kind,
                                "intent": q.intent,
                                "placeholder": q.placeholder,
                                "suggested": q.suggested,
                            }
                            for q in postflight.clarify
                        ],
                        "flags": [
                            {
                                "kind": fl.kind,
                                "value": fl.value,
                                "file": fl.file,
                                "line": fl.line,
                                "reason": fl.reason,
                            }
                            for fl in postflight.flags
                        ],
                    },
                )
                yield _sse_bytes("done", {})
                return

            yield _sse_bytes(
                "step",
                {
                    "name": "codegen",
                    "status": "done",
                    "tokens": envelope.tokens.model_dump(by_alias=True),
                    "files": len(envelope.data.files),
                    "lines": total_lines,
                },
            )

            # Stage 2 — validator (with fix-with-AI loop, Phase 4f)
            #
            # We run validate → if errors > 0 → fix → re-validate. Hard cap of
            # 2 fix iterations to bound LLM cost and avoid loops. After the cap
            # we ship what we have along with the remaining issues so the user
            # can still see what's wrong.
            yield _sse_bytes("step", {"name": "validate", "status": "start"})
            current_bundle = envelope.data
            report = await validate_bundle(workspace_id, current_bundle, framework_context)
            err_count = sum(1 for i in report.issues if i.severity == "error")
            warn_count = sum(1 for i in report.issues if i.severity == "warning")
            yield _sse_bytes(
                "step",
                {
                    "name": "validate",
                    "status": "done",
                    "errors": err_count,
                    "warnings": warn_count,
                    "iteration": 0,
                },
            )

            MAX_FIX_ITERATIONS = 2
            fix_iterations_run = 0
            errors_fixed = 0
            fixer_notes: list[str] = []
            fixer_unresolved: list[str] = []

            for iteration in range(1, MAX_FIX_ITERATIONS + 1):
                if err_count == 0:
                    break  # Nothing to fix
                yield _sse_bytes(
                    "step",
                    {
                        "name": "fix",
                        "status": "start",
                        "iteration": iteration,
                        "issues_to_fix": err_count,
                    },
                )
                current_bundle, fixer_result = await fix_bundle(
                    workspace_id, current_bundle, report, framework_context, iteration
                )
                fix_iterations_run = iteration
                fixer_notes.extend(fixer_result.notes)
                fixer_unresolved.extend(fixer_result.unresolved)
                yield _sse_bytes(
                    "step",
                    {
                        "name": "fix",
                        "status": "done",
                        "iteration": iteration,
                        "files_patched": len(fixer_result.patches),
                    },
                )

                # Re-validate the patched bundle.
                yield _sse_bytes(
                    "step",
                    {"name": "validate", "status": "start", "iteration": iteration},
                )
                report = await validate_bundle(workspace_id, current_bundle, framework_context)
                new_err = sum(1 for i in report.issues if i.severity == "error")
                new_warn = sum(1 for i in report.issues if i.severity == "warning")
                errors_fixed += max(0, err_count - new_err)
                err_count = new_err
                warn_count = new_warn
                yield _sse_bytes(
                    "step",
                    {
                        "name": "validate",
                        "status": "done",
                        "iteration": iteration,
                        "errors": err_count,
                        "warnings": warn_count,
                    },
                )

            # Stage 3 — persist. Single run row + one generated_tests row per file.
            # Note: we persist `current_bundle` (post-fixer) rather than the raw
            # codegen output, so the user always downloads the cleanest version.
            final_total_lines = sum(f.content.count("\n") + 1 for f in current_bundle.files)
            run = await fetchrow(
                """
                insert into public.runs
                  (workspace_id, kind, ticket_id, repo_ref, status, started_at, finished_at, summary)
                values ($1, 'playwright_local',
                        (select ticket_id from public.plans
                          where id = $2 and deleted_at is null),
                        $3, 'generated', now(), now(), $4)
                returning id::text
                """,
                workspace_id,
                plan_id,
                inp.base_url,
                {
                    "source": "codegen",
                    "files": len(current_bundle.files),
                    "lines": final_total_lines,
                    "validator_errors": err_count,
                    "validator_warnings": warn_count,
                    "fix_iterations": fix_iterations_run,
                    "errors_auto_fixed": errors_fixed,
                },
            )
            for f in current_bundle.files:
                await execute(
                    """
                    insert into public.generated_tests
                      (workspace_id, run_id, repo_path, content)
                    values ($1, $2, $3, $4)
                    """,
                    workspace_id,
                    run["id"],
                    f.path,
                    f.content,
                )

            # Stage 4 — result. Note the bundle returned is post-fixer.
            extra_notes = list(current_bundle.notes)
            if errors_fixed > 0:
                extra_notes.insert(
                    0,
                    f"Auto-fixed {errors_fixed} validator error{'s' if errors_fixed != 1 else ''} "
                    f"across {fix_iterations_run} pass{'es' if fix_iterations_run != 1 else ''}.",
                )
            if fixer_unresolved:
                extra_notes.append(
                    "Some issues weren't auto-fixed: " + "; ".join(fixer_unresolved[:5])
                )

            yield _sse_bytes(
                "result",
                {
                    "run_id": run["id"],
                    "bundle": {
                        "files": [f.model_dump() for f in current_bundle.files],
                        "install_commands": current_bundle.install_commands,
                        "run_commands": current_bundle.run_commands,
                        "notes": extra_notes,
                    },
                    "validator": {
                        "issues": [i.model_dump() for i in report.issues],
                        "summary": report.summary,
                    },
                    "fixer": {
                        "iterations": fix_iterations_run,
                        "errors_auto_fixed": errors_fixed,
                        "notes": fixer_notes,
                        "unresolved": fixer_unresolved,
                    },
                    "citations": [c.model_dump() for c in envelope.citations],
                    "tokens": envelope.tokens.model_dump(by_alias=True),
                    "stats": {
                        "files": len(current_bundle.files),
                        "lines": final_total_lines,
                        "validator_errors": err_count,
                        "validator_warnings": warn_count,
                        "fix_iterations": fix_iterations_run,
                        "errors_auto_fixed": errors_fixed,
                    },
                },
            )
            yield _sse_bytes("done", {})
        except HTTPException as e:
            log.warning("codegen HTTPException %s: %s", e.status_code, e.detail)
            yield _sse_bytes("error", {"message": e.detail, "status": e.status_code})
            yield _sse_bytes("done", {})
        except Exception as e:  # noqa: BLE001
            log.exception("codegen unhandled error")
            yield _sse_bytes(
                "error",
                {
                    "message": str(e),
                    "status": 500,
                    "trace": traceback.format_exc()[-1200:],
                },
            )
            yield _sse_bytes("done", {})

    return _sse_response(stream())


# ------------------------------ helpers -----------------------------------


def _to_gherkin(case) -> str:  # type: ignore[no-untyped-def]
    lines = [f"Scenario: {case.title}"]
    for p in case.preconditions:
        lines.append(f"  Given {p}")
    for i, step in enumerate(case.steps):
        keyword = "When" if i == 0 else "And"
        lines.append(f"  {keyword} {step.action}")
        lines.append(f"  Then {step.expected}")
    return "\n".join(lines)
