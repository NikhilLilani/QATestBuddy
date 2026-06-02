"""POST /api/v1/project-state/detect — returns the inferred codegen mode.

Wired into the new codegen workflow's "what does your project look like?"
step. Cheap enough to call on every framework/dev-repo selection change.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.workspace import CurrentWorkspace
from app.services.project_state import detect_state

router = APIRouter(prefix="/project-state", tags=["project-state"])


class DetectIn(BaseModel):
    framework_id: str | None = None
    dev_repo_id: str | None = None


class DetectOut(BaseModel):
    state: str
    detected_framework: str | None = None
    evidence: list[str]
    confidence: float


@router.post("/detect")
async def detect(body: DetectIn, ctx: CurrentWorkspace) -> DetectOut:
    verdict = await detect_state(
        workspace_id=ctx.workspace_id,
        framework_id=body.framework_id,
        dev_repo_id=body.dev_repo_id,
    )
    return DetectOut(
        state=verdict.state,
        detected_framework=verdict.detected_framework,
        evidence=verdict.evidence,
        confidence=verdict.confidence,
    )
