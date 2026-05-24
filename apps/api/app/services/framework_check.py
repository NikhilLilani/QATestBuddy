"""Framework ↔ ticket relatedness check.

Reads framework metadata + ticket and asks the LLM whether the ticket is
about this project. Returns a structured verdict with evidence so the UI
can show "Are you sure?" with reasoning.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.services.llm import LLMClient


class RelatednessVerdict(BaseModel):
    related: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    evidence: list[str] = Field(default_factory=list)
    suggestion: str = ""


SYSTEM = """You are a senior software engineer reviewing whether a Jira ticket + the user's flow-guidance plan plausibly belong to a given codebase.

You receive THREE inputs:
1. A Jira ticket (what needs to be tested).
2. The user's *flow guidance* — explicit instructions on which parts of the framework to change / where the new flow lives.
3. The framework's codebase context (README, package.json, sample tests).

Score the THREE-WAY match — not just ticket↔repo, but also whether the user's flow guidance lines up with the structure of the framework.

Output ONLY a JSON object (no prose, no fences, no markdown):
{
  "related": true|false,
  "confidence": 0.0-1.0,
  "reasoning": "2-3 sentence explanation — explicitly mention how well the FLOW GUIDANCE matches the framework's structure (e.g. 'guidance refers to LoginPage.ts which exists at src/pages/appA/AppALoginPage.ts ✓' or 'guidance mentions a CheckoutModule but the repo has no modules/ directory ✗')",
  "evidence": ["specific signals — at least one for ticket↔repo match, at least one for guidance↔repo match"],
  "suggestion": "if not related: concrete change to fix it (e.g. 'Add OTP step to AppALoginModule.ts'); empty string if related"
}

Strong positive signals:
- Ticket mentions routes/components/models also present in the codebase.
- Same product domain (e-commerce / SaaS auth / etc.) as the README.
- Flow-guidance file paths or class names appear in the repo tree.

Strong negative signals:
- Ticket is about a completely different product than the repo.
- Flow guidance references files/folders that don't exist in the repo.
- Guidance is too vague or empty to verify against the codebase.

Be honest. If there isn't enough evidence either way, say `related=false` with low confidence and ask for clarification in `suggestion`."""


def _user_prompt(ticket: dict[str, Any], framework_summary: str, flow_guidance: str) -> str:
    return (
        f"Jira ticket {ticket['key']}: {ticket.get('title')}\n"
        f"Type: {ticket.get('issuetype') or 'unknown'} · Priority: {ticket.get('priority') or 'unknown'}\n\n"
        f"Description:\n{ticket.get('description') or '(no description)'}\n\n"
        f"=== User-supplied flow guidance (where to change in the framework) ===\n"
        f"{flow_guidance.strip() or '(empty — penalize confidence heavily)'}\n\n"
        f"=== Framework / codebase context ===\n{framework_summary}"
    )


async def check_relatedness(
    workspace_id: str,
    ticket: dict[str, Any],
    framework_summary: str,
    flow_guidance: str = "",
) -> RelatednessVerdict:
    if not framework_summary.strip() or framework_summary.strip().startswith("(no source"):
        # No source content to compare. Return a neutral "unknown" verdict
        # so the UI can ask the user to confirm.
        return RelatednessVerdict(
            related=False,
            confidence=0.0,
            reasoning=(
                "Cannot automatically verify match — this framework has no readable source "
                "(it's a local path, zip, or 'new' framework). Confirm manually that the "
                "ticket belongs to this project."
            ),
            evidence=[],
            suggestion="Continue anyway if you're confident, or switch to a framework with a Git URL for auto-check.",
        )

    llm = await LLMClient.from_workspace(workspace_id)
    result = await llm.complete_json(
        SYSTEM,
        _user_prompt(ticket, framework_summary, flow_guidance),
        max_tokens=2000,
        temperature=0.0,
    )
    if not result.parsed or not isinstance(result.parsed, dict):
        return RelatednessVerdict(
            related=False,
            confidence=0.0,
            reasoning="Could not parse the relatedness verdict from the model.",
            evidence=[result.text[:300]] if result.text else [],
            suggestion="Continue anyway, or retry.",
        )
    try:
        return RelatednessVerdict.model_validate(result.parsed)
    except ValidationError as e:
        return RelatednessVerdict(
            related=False,
            confidence=0.0,
            reasoning=f"Schema mismatch: {e.errors()[:2]}",
            evidence=[json.dumps(result.parsed)[:300]],
            suggestion="Continue anyway, or retry.",
        )
