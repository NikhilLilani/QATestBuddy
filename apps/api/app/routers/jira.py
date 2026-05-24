"""Jira router — fetch issues using the workspace's stored PAT."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.workspace import CurrentWorkspace
from app.services import jira as jira_svc

router = APIRouter(prefix="/jira", tags=["jira"])


@router.get("/issues/{key}")
async def get_issue(key: str, ws: CurrentWorkspace) -> dict:
    return await jira_svc.get_issue(ws.workspace_id, key.upper())


@router.get("/search")
async def search_issues(
    ws: CurrentWorkspace,
    jql: str = Query("ORDER BY updated DESC"),
    limit: int = Query(25, ge=1, le=100),
) -> list[dict]:
    return await jira_svc.search_issues(ws.workspace_id, jql, limit)
