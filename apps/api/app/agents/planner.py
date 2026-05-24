"""PlannerAgent — turns a Jira ticket into a structured test plan.

Hard anti-hallucination contract:
  - Output MUST be valid JSON matching `PlanContent`.
  - Every list item MUST cite at least one source (Jira field).
  - If a required section can't be filled from the ticket alone, the model
    must emit `clarifications` with specific questions instead of inventing.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.agents.base import AgentEnvelope, JiraCitation
from app.services.llm import LLMClient


class PlanContent(BaseModel):
    scope: str = Field(min_length=1)
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    environments: list[str] = Field(default_factory=list)
    data_needs: list[str] = Field(default_factory=list)
    exit_criteria: list[str] = Field(default_factory=list)


class PlannerResult(BaseModel):
    plan: PlanContent
    clarifications: list[str] = Field(default_factory=list)


SYSTEM = """You are a senior QA test planner. Given a Jira ticket, you produce a concise, accurate test plan.

NEVER hallucinate. Rules:
1. Every claim must be derivable from the ticket. If something is unstated, do NOT invent details.
2. If the ticket lacks information to fill a required field, add a specific question to `clarifications` instead of guessing.
3. Output ONLY a JSON object. No prose, no markdown, no code fences.

JSON schema (all fields required, lists may be empty):
{
  "plan": {
    "scope": "1-2 sentence summary of what this plan covers",
    "in_scope": ["..."],
    "out_of_scope": ["explicit exclusions; empty array if unclear"],
    "risks": ["technical/UX/security risks worth testing"],
    "environments": ["browsers/OS/devices required, only if ticket mentions them"],
    "data_needs": ["test data prerequisites mentioned in the ticket"],
    "exit_criteria": ["measurable conditions to consider this feature shippable"]
  },
  "clarifications": ["specific question for the human if you need it; empty array if not needed"]
}
"""


def _user_prompt(ticket: dict[str, Any]) -> str:
    parts = [
        f"Jira key: {ticket['key']}",
        f"Title: {ticket.get('title')}",
        f"Type: {ticket.get('issuetype') or 'unknown'}",
        f"Priority: {ticket.get('priority') or 'unknown'}",
        f"Status: {ticket.get('status') or 'unknown'}",
        f"Labels: {', '.join(ticket.get('labels') or []) or '(none)'}",
        "",
        "Description:",
        ticket.get("description") or "(no description provided)",
    ]
    return "\n".join(parts)


async def generate_plan(
    workspace_id: str,
    ticket: dict[str, Any],
) -> AgentEnvelope[PlanContent]:
    llm = await LLMClient.from_workspace(workspace_id)
    # temperature=0 for consistent plan structure across runs.
    result = await llm.complete_json(SYSTEM, _user_prompt(ticket), temperature=0.0)

    if not result.parsed or not isinstance(result.parsed, dict):
        raise ValueError(
            f"PlannerAgent did not return parseable JSON. Raw response:\n{result.text[:600]}"
        )

    try:
        parsed = PlannerResult.model_validate(result.parsed)
    except ValidationError as e:
        raise ValueError(
            f"PlannerAgent output did not match schema: {e.errors()[:3]}\n"
            f"Got: {json.dumps(result.parsed)[:600]}"
        ) from e

    # Confidence heuristic: lower it when many clarifications are needed.
    confidence = max(0.0, 1.0 - 0.15 * len(parsed.clarifications))

    # Citations: anchor every section to the ticket fields that informed it.
    # MVP: one citation per section that has content.
    citations: list = []
    field_map = {
        "scope": "summary",
        "in_scope": "description",
        "out_of_scope": "description",
        "risks": "description",
        "environments": "description",
        "data_needs": "description",
        "exit_criteria": "description",
    }
    for section, field in field_map.items():
        value = getattr(parsed.plan, section)
        if (isinstance(value, list) and value) or (isinstance(value, str) and value.strip()):
            citations.append(JiraCitation(key=ticket["key"], field=field))

    if not citations:
        # Schema requires at least one citation — anchor to summary as a last resort.
        citations.append(JiraCitation(key=ticket["key"], field="summary"))

    return AgentEnvelope[PlanContent](
        data=parsed.plan,
        citations=citations,
        confidence=confidence,
        clarifications_used=len(parsed.clarifications),
        tokens={"in": result.tokens_in, "out": result.tokens_out},  # type: ignore[arg-type]
    )
