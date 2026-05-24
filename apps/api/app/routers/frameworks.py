"""Frameworks router — CRUD + relatedness check + context preview."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.workspace import CurrentWorkspace
from app.db.queries import execute, fetch, fetchrow
from app.services import jira as jira_svc
from app.services.framework_check import RelatednessVerdict, check_relatedness
from app.services.github_repo import fetch_repo_snapshot, parse_github_url, snapshot_to_context

router = APIRouter(prefix="/frameworks", tags=["frameworks"])


SourceKind = Literal["git", "local", "zip", "new"]
Language = Literal["playwright_ts", "cypress", "selenium", "other"]


# ----------------------------- models -----------------------------------


class FrameworkIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    language: Language = "playwright_ts"
    source_kind: SourceKind
    git_url: str | None = None
    git_branch: str | None = None
    local_path: str | None = None
    zip_storage_key: str | None = None
    conventions: str = ""

    def validate_source(self) -> None:
        if self.source_kind == "git":
            if not self.git_url or not parse_github_url(self.git_url):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Source 'git' requires a valid https://github.com/owner/repo URL.",
                )
        elif self.source_kind == "local":
            if not self.local_path or not self.local_path.strip():
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Source 'local' requires a local_path.",
                )
        elif self.source_kind == "zip":
            if not self.zip_storage_key:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Source 'zip' requires a zip_storage_key (upload the file first).",
                )
        # 'new' is always valid (no source needed)


class FrameworkOut(BaseModel):
    id: str
    name: str
    language: Language
    source_kind: SourceKind
    git_url: str | None
    git_branch: str | None
    local_path: str | None
    zip_storage_key: str | None
    conventions: str
    status: str
    created_at: str
    updated_at: str


# ----------------------------- routes ------------------------------------


@router.get("", response_model=list[FrameworkOut])
async def list_frameworks(ws: CurrentWorkspace) -> list[FrameworkOut]:
    rows = await fetch(
        """
        select id::text, name, language::text, source_kind::text,
               git_url, git_branch, local_path, zip_storage_key,
               conventions, status, created_at::text, updated_at::text
        from public.frameworks
        where workspace_id = $1
        order by created_at desc
        """,
        ws.workspace_id,
    )
    return [FrameworkOut(**r) for r in rows]


@router.get("/{framework_id}", response_model=FrameworkOut)
async def get_framework(framework_id: str, ws: CurrentWorkspace) -> FrameworkOut:
    row = await fetchrow(
        """
        select id::text, name, language::text, source_kind::text,
               git_url, git_branch, local_path, zip_storage_key,
               conventions, status, created_at::text, updated_at::text
        from public.frameworks
        where id = $1 and workspace_id = $2
        """,
        framework_id,
        ws.workspace_id,
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Framework not found")
    return FrameworkOut(**row)


@router.post("", response_model=FrameworkOut, status_code=status.HTTP_201_CREATED)
async def create_framework(body: FrameworkIn, ws: CurrentWorkspace) -> FrameworkOut:
    body.validate_source()
    # Null out fields not relevant to the chosen kind so the DB row is clean
    git_url = body.git_url if body.source_kind == "git" else None
    git_branch = body.git_branch if body.source_kind == "git" else None
    local_path = body.local_path if body.source_kind == "local" else None
    zip_key = body.zip_storage_key if body.source_kind == "zip" else None

    row = await fetchrow(
        """
        insert into public.frameworks
          (workspace_id, name, language, source_kind, git_url, git_branch, local_path, zip_storage_key, conventions, created_by)
        values ($1, $2, $3::framework_language, $4::framework_kind, $5, $6, $7, $8, $9, $10)
        returning id::text, name, language::text, source_kind::text,
                  git_url, git_branch, local_path, zip_storage_key,
                  conventions, status, created_at::text, updated_at::text
        """,
        ws.workspace_id,
        body.name,
        body.language,
        body.source_kind,
        git_url,
        git_branch,
        local_path,
        zip_key,
        body.conventions,
        ws.user_id,
    )
    assert row is not None
    return FrameworkOut(**row)


@router.put("/{framework_id}", response_model=FrameworkOut)
async def update_framework(
    framework_id: str, body: FrameworkIn, ws: CurrentWorkspace
) -> FrameworkOut:
    body.validate_source()
    git_url = body.git_url if body.source_kind == "git" else None
    git_branch = body.git_branch if body.source_kind == "git" else None
    local_path = body.local_path if body.source_kind == "local" else None
    zip_key = body.zip_storage_key if body.source_kind == "zip" else None
    row = await fetchrow(
        """
        update public.frameworks set
          name = $3, language = $4::framework_language, source_kind = $5::framework_kind,
          git_url = $6, git_branch = $7, local_path = $8, zip_storage_key = $9,
          conventions = $10
        where id = $1 and workspace_id = $2
        returning id::text, name, language::text, source_kind::text,
                  git_url, git_branch, local_path, zip_storage_key,
                  conventions, status, created_at::text, updated_at::text
        """,
        framework_id,
        ws.workspace_id,
        body.name,
        body.language,
        body.source_kind,
        git_url,
        git_branch,
        local_path,
        zip_key,
        body.conventions,
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Framework not found")
    return FrameworkOut(**row)


@router.delete("/{framework_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_framework(framework_id: str, ws: CurrentWorkspace) -> None:
    await execute(
        "delete from public.frameworks where id = $1 and workspace_id = $2",
        framework_id,
        ws.workspace_id,
    )


# ------------------------- /frameworks/{id}/check ------------------------


class CheckBody(BaseModel):
    ticket_key: str
    flow_guidance: str = Field(
        default="",
        description=(
            "User's explicit instructions on which parts of the framework to change. "
            "Required by the UI — empty values are heavily penalized in the verdict."
        ),
    )


class CheckOut(BaseModel):
    verdict: RelatednessVerdict
    source: str  # short summary of what we read (for display)


@router.post("/{framework_id}/check", response_model=CheckOut)
async def check_framework(
    framework_id: str, body: CheckBody, ws: CurrentWorkspace
) -> CheckOut:
    fw = await fetchrow(
        """
        select source_kind::text, git_url, git_branch, local_path, conventions
        from public.frameworks where id = $1 and workspace_id = $2
        """,
        framework_id,
        ws.workspace_id,
    )
    if not fw:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Framework not found")

    ticket = await jira_svc.get_issue(ws.workspace_id, body.ticket_key.upper())

    # Build context per source kind
    if fw["source_kind"] == "git" and fw["git_url"]:
        snap = await fetch_repo_snapshot(fw["git_url"], fw["git_branch"])
        ctx = snapshot_to_context(snap)
        source = f"GitHub: {snap.owner}/{snap.repo}@{snap.branch}"
    elif fw["source_kind"] == "local":
        ctx = (
            f"(no source content — framework is at local path: {fw['local_path']})\n"
            f"Conventions notes provided by user:\n{fw['conventions']}"
        )
        source = f"Local path (text only): {fw['local_path']}"
    elif fw["source_kind"] == "zip":
        ctx = f"(no source content — zip upload not yet indexed)\n\nConventions:\n{fw['conventions']}"
        source = "Zip (not indexed yet)"
    else:
        ctx = f"(no source — new framework will be scaffolded)\n\nConventions:\n{fw['conventions']}"
        source = "New framework (no source to compare)"

    verdict = await check_relatedness(ws.workspace_id, ticket, ctx, body.flow_guidance)
    return CheckOut(verdict=verdict, source=source)
