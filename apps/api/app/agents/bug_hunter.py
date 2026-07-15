"""BugHunterAgent — static mode.

Turns a Jira ticket + the workspace's indexed dev-repo code into concrete,
cited bug/risk findings. Static analysis only — no live probing of a running
app (that's BugHunter-live, a separate follow-up).

No standalone CodeReaderAgent: this codebase has no agent-chaining
orchestrator (every agent is a single-shot function called directly from a
router — see planner.py / case_author.py), so retrieval
(retrieve_code / find_locator / find_route) is folded in here directly
instead of standing up a second agent+envelope for zero benefit.

Hard anti-hallucination contract, same as every other agent:
  - Output MUST be valid JSON matching `BugFindingsResult`.
  - Every finding's file_refs are cross-checked against the code chunks we
    actually showed the model — a ref to a file we never retrieved is
    dropped, not trusted.
  - If nothing can be grounded (no indexed repo), we lower confidence and
    add a clarification note instead of blocking the stream.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agents.base import AgentEnvelope, JiraCitation, RepoCitation
from app.agents.grounding import _workspace_has_indexed_repo
from app.services.llm import LLMClient
from app.services.repo_retrieve import RetrievedCodeChunk, format_for_prompt, retrieve_code

log = logging.getLogger("qatb.bug_hunter")

ALLOWED_SEVERITIES = {"critical", "high", "medium", "low"}


class BugFinding(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    severity: str = "medium"
    # Model-cited "path" or "path:line" strings. Anything not among the
    # retrieved paths gets stripped out post-parse — see hunt_bugs().
    file_refs: list[str] = Field(default_factory=list)

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, v: Any) -> str:
        s = str(v).strip().lower() if v is not None else "medium"
        mapping = {"blocker": "critical", "major": "high", "minor": "low", "trivial": "low"}
        s = mapping.get(s, s)
        return s if s in ALLOWED_SEVERITIES else "medium"


class BugFindingsResult(BaseModel):
    findings: list[BugFinding] = Field(default_factory=list)
    clarifications: list[str] = Field(default_factory=list)


SYSTEM = """You are a senior QA engineer performing static bug/risk analysis. Given a Jira ticket and excerpts from the application's actual source code, find concrete bugs, risks, or gaps a reviewer would flag before this ships.

NEVER hallucinate. Rules:
1. Only report something as a finding if you can point at a specific file from the excerpts below that supports it. If no code excerpts are provided, or none are relevant, only report findings clearly implied by the ticket text itself (missing edge cases, ambiguous requirements) and leave `file_refs` empty for those.
2. `file_refs` MUST be exact paths copied from the excerpt headers (format: `[code:{repo}:{path} chunk_id=...]`). Do not invent paths, do not guess line numbers.
3. If you're not confident something is a real issue, omit it. A short, accurate list beats a padded one.
4. Output ONLY a JSON object. No prose, no markdown, no code fences.

JSON schema:
{
  "findings": [
    {
      "title": "short bug/risk title",
      "description": "1-3 sentences: what's wrong and why it matters",
      "severity": "critical" | "high" | "medium" | "low",
      "file_refs": ["exact/path/from/excerpt"]
    }
  ],
  "clarifications": ["question for the human if the ticket is too ambiguous to analyze; empty array OK"]
}

SEVERITY GUIDE:
- critical: data loss, security hole, broken core flow with no workaround.
- high: broken flow with a workaround, or wrong behavior on a common path.
- medium: incorrect behavior on a less common path, missing validation.
- low: cosmetic, minor edge case, nice-to-have hardening.

Do NOT pad the list. If the ticket + code excerpts don't surface real issues, return an empty `findings` array — that's a valid, useful result."""


def _user_prompt(ticket: dict[str, Any], code_block: str) -> str:
    parts = [
        f"Jira ticket {ticket['key']}: {ticket.get('title')}",
        f"Description:\n{ticket.get('description') or '(none)'}",
    ]
    if code_block:
        parts.append("")
        parts.append(
            "Excerpts from the application's source code, each headed by its "
            "exact file path. Use ONLY these paths in `file_refs` — never a "
            "path you haven't seen here."
        )
        parts.append(code_block)
    else:
        parts.append("")
        parts.append(
            "No indexed source code is available for this workspace. Base "
            "findings only on the ticket text (ambiguities, missing edge "
            "cases) and leave `file_refs` empty."
        )
    return "\n".join(parts)


async def hunt_bugs(
    workspace_id: str,
    ticket: dict[str, Any],
    *,
    dev_repo_id: str | None = None,
) -> AgentEnvelope[BugFindingsResult]:
    has_repo = await _workspace_has_indexed_repo(workspace_id)

    query = " ".join(
        filter(None, [ticket.get("title"), ticket.get("key"), (ticket.get("description") or "")[:400]])
    )

    code_chunks: list[RetrievedCodeChunk] = []
    code_block = ""
    if has_repo:
        try:
            code_chunks = await retrieve_code(
                workspace_id=workspace_id, query=query, k=12, repo_id=dev_repo_id,
            )
            code_block = format_for_prompt(code_chunks, max_chars=6000)
        except Exception as e:  # noqa: BLE001
            log.warning("Dev-repo retrieval skipped: %s", e)

    retrieved_paths = {c.path for c in code_chunks}
    repo_name_by_path = {c.path: c.repo_name for c in code_chunks}

    llm = await LLMClient.from_workspace(workspace_id)
    result = await llm.complete_json(
        SYSTEM, _user_prompt(ticket, code_block), max_tokens=6000, temperature=0.0,
    )

    if not result.parsed or not isinstance(result.parsed, dict):
        raise ValueError(
            f"BugHunterAgent did not return parseable JSON. Raw response:\n{result.text[:600]}"
        )

    raw = result.parsed
    raw_findings = raw.get("findings", []) if isinstance(raw, dict) else []
    good: list[BugFinding] = []
    skipped: list[dict] = []
    for i, f in enumerate(raw_findings):
        try:
            good.append(BugFinding.model_validate(f))
        except ValidationError as e:
            skipped.append({"index": i, "errors": [err.get("msg", "") for err in e.errors()][:3]})

    # Grounding check: drop any file_ref whose path was never actually shown
    # to the model. Don't trust the model to police its own citations.
    dropped_refs = 0
    grounded: list[BugFinding] = []
    for f in good:
        valid_refs = [r for r in f.file_refs if r.split(":")[0] in retrieved_paths]
        dropped_refs += len(f.file_refs) - len(valid_refs)
        grounded.append(f.model_copy(update={"file_refs": valid_refs}))

    clarifications = list(raw.get("clarifications", []) if isinstance(raw, dict) else [])
    if not has_repo:
        clarifications.append(
            "No indexed dev repo for this workspace — findings are ticket-only "
            "and not verified against real code."
        )

    citations: list = []
    for f in grounded:
        for ref in f.file_refs:
            path = ref.split(":")[0]
            citations.append(RepoCitation(
                repo=repo_name_by_path.get(path, "unknown"),
                path=path,
                # retrieve_code doesn't carry real line numbers yet (same
                # gap case_author.py already accepts) — 1/1 placeholder.
                line_start=1,
                line_end=1,
            ))
    if not citations:
        # Envelope requires >=1 citation — anchor to the ticket itself when
        # nothing could be grounded in code.
        citations.append(JiraCitation(key=ticket["key"], field="description"))

    penalty = 0.1 * len(skipped) + 0.05 * dropped_refs + (0.2 if not has_repo else 0.0)
    confidence = max(0.0, 1.0 - penalty)

    return AgentEnvelope[BugFindingsResult](
        data=BugFindingsResult(findings=grounded, clarifications=clarifications),
        citations=citations,
        confidence=confidence,
        clarifications_used=len(clarifications),
        tokens={"in": result.tokens_in, "out": result.tokens_out},  # type: ignore[arg-type]
    )
