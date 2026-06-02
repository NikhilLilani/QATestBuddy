"""Dev-repo connector — add / list / reindex / search / delete.

Three ways to attach a repo (M2 scope):
  - Public GitHub URL  (source_kind = git_public, no token)
  - Private GitHub URL + PAT  (source_kind = git_pat, token encrypted)
  - Zip upload  (source_kind = zip, archive stored on disk)

Re-indexing runs synchronously for now — small/medium repos finish in
seconds and the response carries the file/chunk counts. For repos that
push past `_MAX_FILES_PER_REPO`, we'll move ingest into a background
task in a follow-up.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.core.workspace import CurrentWorkspace
from app.services.dev_repo import (
    create_repo,
    delete_repo,
    ingest_repo,
    list_repos,
)
from app.services.locator_extract import find_locator
from app.services.repo_retrieve import retrieve_code
from app.services.route_extract import find_route

router = APIRouter(prefix="/dev-repos", tags=["dev-repos"])

# Where uploaded zips live until ingestion. Local FS for now; swap to S3
# by changing this constant and the create/zip code path together.
_ZIP_DIR = os.environ.get("QATB_ZIP_DIR", os.path.join(tempfile.gettempdir(), "qatb_devrepo_zips"))
os.makedirs(_ZIP_DIR, exist_ok=True)

_MAX_ZIP_BYTES = 100 * 1024 * 1024  # 100 MB hard cap


# ----------------------------- models ----------------------------------------


class DevRepoOut(BaseModel):
    id: str
    provider: str
    owner: str | None = None
    name: str | None = None
    default_branch: str | None = None
    source_kind: str
    access_token_hint: str | None = None
    zip_storage_key: str | None = None
    notes: str = ""
    last_indexed_at: str | None = None
    file_count: int | None = None
    last_error: str | None = None


class IngestResultOut(BaseModel):
    repo_id: str
    files_indexed: int
    files_skipped: int
    chunks: int
    locators: int = 0
    routes: int = 0


class LocatorOut(BaseModel):
    id: str
    repo_file_id: str
    path: str
    line: int
    selector_kind: str
    selector_value: str
    component: str | None = None
    label: str | None = None
    context: dict = {}
    score: float


class RouteOut(BaseModel):
    path_pattern: str
    source_file: str
    line: int | None = None
    framework: str


class AddGitIn(BaseModel):
    git_url: str = Field(min_length=1)
    branch: str | None = None
    # Optional: if provided, repo is treated as private (source_kind=git_pat)
    # and the token is encrypted at rest.
    access_token: str | None = None
    notes: str = ""
    # Whether to run ingest right after creating the row. Default true so the
    # UI gets immediate feedback; false lets the caller defer for background.
    ingest_now: bool = True


class SearchOut(BaseModel):
    chunk_id: str
    repo_name: str
    path: str
    lang: str | None
    ord: int
    score: float
    bm25_rank: int | None
    vector_rank: int | None
    snippet: str


# ----------------------------- endpoints -------------------------------------


@router.get("")
async def list_(ctx: CurrentWorkspace) -> list[DevRepoOut]:
    rows = await list_repos(ctx.workspace_id)
    return [
        DevRepoOut(
            id=str(r["id"]),
            provider=r["provider"],
            owner=r.get("owner"),
            name=r.get("name"),
            default_branch=r.get("default_branch"),
            source_kind=r["source_kind"],
            access_token_hint=r.get("access_token_hint"),
            zip_storage_key=r.get("zip_storage_key"),
            notes=r.get("notes") or "",
            last_indexed_at=(
                r["last_indexed_at"].isoformat() if r.get("last_indexed_at") else None
            ),
            file_count=r.get("file_count"),
            last_error=r.get("last_error"),
        )
        for r in rows
    ]


@router.post("/git", status_code=status.HTTP_201_CREATED)
async def add_git(ctx: CurrentWorkspace, body: AddGitIn) -> IngestResultOut | DevRepoOut:
    """Attach a GitHub repo (public or private). If `ingest_now` is true the
    response is the ingest summary; otherwise it's the newly-created row so
    the caller can render it and trigger ingest later."""
    source_kind: Literal["git_public", "git_pat"] = (
        "git_pat" if body.access_token else "git_public"
    )
    try:
        repo_id = await create_repo(
            workspace_id=ctx.workspace_id,
            source_kind=source_kind,
            git_url=body.git_url,
            branch=body.branch,
            access_token=body.access_token,
            notes=body.notes,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    if not body.ingest_now:
        rows = await list_repos(ctx.workspace_id)
        row = next((r for r in rows if str(r["id"]) == repo_id), None)
        if not row:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "row missing")
        return DevRepoOut(
            id=str(row["id"]), provider=row["provider"], owner=row.get("owner"),
            name=row.get("name"), default_branch=row.get("default_branch"),
            source_kind=row["source_kind"],
            access_token_hint=row.get("access_token_hint"),
            zip_storage_key=row.get("zip_storage_key"),
            notes=row.get("notes") or "",
            last_indexed_at=None, file_count=None, last_error=None,
        )

    result = await ingest_repo(ctx.workspace_id, repo_id)
    return IngestResultOut(
        repo_id=result.repo_id,
        files_indexed=result.files_indexed,
        files_skipped=result.files_skipped,
        chunks=result.chunks,
        locators=result.locators,
        routes=result.routes,
    )


@router.post("/zip", status_code=status.HTTP_201_CREATED)
async def add_zip(
    ctx: CurrentWorkspace,
    file: Annotated[UploadFile, File()],
    notes: Annotated[str, Form()] = "",
) -> IngestResultOut:
    """Attach a repo from an uploaded .zip. Synchronously ingests."""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Upload must be a .zip archive.",
        )
    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded zip is empty.")
    if len(raw) > _MAX_ZIP_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Zip exceeds the {_MAX_ZIP_BYTES // (1024 * 1024)} MB limit.",
        )

    # Store on disk so the ingest service can stream from it without keeping
    # the whole archive in memory. Filename is a UUID so two users uploading
    # `repo.zip` don't collide.
    storage_key = os.path.join(_ZIP_DIR, f"{uuid.uuid4().hex}.zip")
    with open(storage_key, "wb") as fh:
        fh.write(raw)

    try:
        repo_id = await create_repo(
            workspace_id=ctx.workspace_id,
            source_kind="zip",
            zip_storage_key=storage_key,
            notes=notes,
        )
    except ValueError as e:
        os.unlink(storage_key)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    result = await ingest_repo(ctx.workspace_id, repo_id)
    return IngestResultOut(
        repo_id=result.repo_id,
        files_indexed=result.files_indexed,
        files_skipped=result.files_skipped,
        chunks=result.chunks,
        locators=result.locators,
        routes=result.routes,
    )


@router.post("/{repo_id}/reindex")
async def reindex(ctx: CurrentWorkspace, repo_id: str) -> IngestResultOut:
    try:
        result = await ingest_repo(ctx.workspace_id, repo_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return IngestResultOut(
        repo_id=result.repo_id,
        files_indexed=result.files_indexed,
        files_skipped=result.files_skipped,
        chunks=result.chunks,
        locators=result.locators,
        routes=result.routes,
    )


@router.get("/{repo_id}/search")
async def search(
    ctx: CurrentWorkspace,
    repo_id: str,
    q: str,
    k: int = 8,
) -> list[SearchOut]:
    """Debug endpoint — runs hybrid retrieval scoped to one repo. Handy for
    confirming that an indexed dev repo actually surfaces the locators /
    files you'd expect for a given query."""
    if not q.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "q is required")
    results = await retrieve_code(
        workspace_id=ctx.workspace_id,
        query=q,
        k=max(1, min(k, 50)),
        repo_id=repo_id,
    )
    return [
        SearchOut(
            chunk_id=c.chunk_id,
            repo_name=c.repo_name,
            path=c.path,
            lang=c.lang,
            ord=c.ord,
            score=round(c.score, 6),
            bm25_rank=c.bm25_rank,
            vector_rank=c.vector_rank,
            # Trim to a single screenful so the response stays browsable.
            snippet=c.content[:600] + ("…" if len(c.content) > 600 else ""),
        )
        for c in results
    ]


@router.get("/locators")
async def search_locators(
    ctx: CurrentWorkspace,
    intent: str,
    k: int = 6,
    repo_id: str | None = None,
    kind: str | None = None,
) -> list[LocatorOut]:
    """Workspace-wide locator search — what the codegen agent will call as a
    tool. `intent` is natural language ("login button", "OTP input"); the
    response is the ranked candidate list. Empty result = the agent should
    raise a clarify question.

    Optional `repo_id` scopes to one dev repo (preferred — multi-repo
    workspaces shouldn't bleed selectors across apps). `kind` filters by
    selector_kind (e.g. only `testid`)."""
    if not intent.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "intent is required")
    kinds = [kind] if kind else None
    matches = await find_locator(
        workspace_id=ctx.workspace_id,
        intent=intent,
        k=max(1, min(k, 50)),
        repo_id=repo_id,
        kinds=kinds,
    )
    return [
        LocatorOut(
            id=m.id,
            repo_file_id=m.repo_file_id,
            path=m.path,
            line=m.line,
            selector_kind=m.selector_kind,
            selector_value=m.selector_value,
            component=m.component,
            label=m.label,
            context=m.context,
            score=round(m.score, 6),
        )
        for m in matches
    ]


@router.get("/routes")
async def search_routes(
    ctx: CurrentWorkspace,
    intent: str,
    k: int = 6,
) -> list[RouteOut]:
    """List route URLs that match `intent` (substring match on path + source).
    Used by codegen to ground `page.goto(...)` in real routes rather than
    inventing `/login` when the app uses `/auth/sign-in`."""
    if not intent.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "intent is required")
    matches = await find_route(
        workspace_id=ctx.workspace_id,
        intent=intent,
        k=max(1, min(k, 50)),
    )
    return [
        RouteOut(
            path_pattern=m.path_pattern,
            source_file=m.source_file,
            line=m.line,
            framework=m.framework,
        )
        for m in matches
    ]


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_(ctx: CurrentWorkspace, repo_id: str) -> None:
    await delete_repo(ctx.workspace_id, repo_id)
