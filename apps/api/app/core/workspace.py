"""Workspace context dependency.

Extracts the workspace ID from the X-Workspace-Id header and verifies the
caller's membership. Returns (user, workspace_id, role).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel

from app.core.security import CurrentUser
from app.db.queries import require_member


class WorkspaceCtx(BaseModel):
    user_id: str
    workspace_id: str
    role: str


async def get_workspace_ctx(
    user: CurrentUser,
    x_workspace_id: Annotated[str | None, Header()] = None,
) -> WorkspaceCtx:
    if not x_workspace_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing X-Workspace-Id header")
    role = await require_member(user.sub, x_workspace_id)
    return WorkspaceCtx(user_id=user.sub, workspace_id=x_workspace_id, role=role)


CurrentWorkspace = Annotated[WorkspaceCtx, Depends(get_workspace_ctx)]
