from fastapi import APIRouter
from pydantic import BaseModel

from app.core.security import CurrentUser
from app.db.queries import fetch

router = APIRouter()


class WorkspaceOut(BaseModel):
    id: str
    name: str
    slug: str
    role: str


@router.get("/workspaces", response_model=list[WorkspaceOut])
async def list_workspaces(user: CurrentUser) -> list[WorkspaceOut]:
    rows = await fetch(
        """
        select w.id::text, w.name, w.slug, m.role::text
        from public.memberships m
        join public.workspaces w on w.id = m.workspace_id
        where m.user_id = $1
        order by w.created_at asc
        """,
        user.sub,
    )
    return [WorkspaceOut(**r) for r in rows]
