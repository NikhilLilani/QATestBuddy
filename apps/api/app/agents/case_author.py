"""CaseAuthorAgent — generates test cases from an approved plan.

Hard anti-hallucination contract: every case must derive from the plan/ticket;
required fields enforced via Pydantic.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agents.base import AgentEnvelope, JiraCitation, RagCitation, RepoCitation
from app.agents.planner import PlanContent
from app.services.llm import LLMClient
from app.services.rag_retrieve import format_for_prompt, retrieve
from app.services.repo_retrieve import (
    format_for_prompt as format_code_for_prompt,
    retrieve_code,
)


# Allowed values we *prefer*, but we accept any string and normalize unknowns.
ALLOWED_TYPES = {"functional", "regression", "edge", "negative", "a11y", "perf", "security", "smoke", "integration"}
ALLOWED_PRIORITIES = {"P0", "P1", "P2", "P3"}


class Step(BaseModel):
    action: str = Field(min_length=1)
    expected: str = Field(min_length=1)


class TestCase(BaseModel):
    title: str = Field(min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    priority: str = "P2"
    type: str = "functional"
    data: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    # Automation triage — set by CaseAuthorAgent, overridable by the user
    # in the UI before codegen.
    #   "yes"     → good candidate, generate Playwright code
    #   "partial" → automatable but with caveats (visual diff, flakey, manual setup)
    #   "no"      → keep as documentation only, don't generate code
    automation_candidate: str = "yes"
    automation_reason: str = ""

    @field_validator("automation_candidate", mode="before")
    @classmethod
    def _normalize_automation(cls, v: Any) -> str:
        s = str(v).strip().lower() if v is not None else "yes"
        if s in ("yes", "true", "y", "automate", "playwright"):
            return "yes"
        if s in ("no", "false", "n", "skip", "manual", "out_of_scope"):
            return "no"
        if s in ("partial", "maybe", "manual_with_automation", "mixed"):
            return "partial"
        return "yes"  # default to including unless clearly opted out

    @field_validator("priority", mode="before")
    @classmethod
    def _normalize_priority(cls, v: Any) -> str:
        s = str(v).strip().upper() if v is not None else "P2"
        # Accept "P1", "1", "HIGH", "CRITICAL", etc.
        if s in ALLOWED_PRIORITIES:
            return s
        mapping = {
            "1": "P1", "2": "P2", "3": "P3", "0": "P0",
            "CRITICAL": "P0", "BLOCKER": "P0",
            "HIGH": "P1", "MAJOR": "P1",
            "MEDIUM": "P2", "MODERATE": "P2",
            "LOW": "P3", "MINOR": "P3", "TRIVIAL": "P3",
        }
        return mapping.get(s, "P2")

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, v: Any) -> str:
        s = str(v).strip().lower() if v is not None else "functional"
        # Accept anything; just lowercase. UI displays whatever the model chose.
        return s or "functional"

    @field_validator("data", mode="before")
    @classmethod
    def _coerce_data(cls, v: Any) -> dict[str, Any]:
        # Some models return a list or null — normalize to dict.
        if v is None:
            return {}
        if isinstance(v, dict):
            return v
        if isinstance(v, list):
            # Convert list of {k, v} or list of strings into a dict
            out: dict[str, Any] = {}
            for i, item in enumerate(v):
                if isinstance(item, dict) and "key" in item:
                    out[str(item["key"])] = item.get("value", "")
                else:
                    out[f"_{i}"] = item
            return out
        return {"value": v}


class CasesResult(BaseModel):
    cases: list[TestCase] = Field(min_length=1)
    clarifications: list[str] = Field(default_factory=list)


SYSTEM = """You are a senior QA engineer. Given a test plan and the underlying Jira ticket, produce a set of executable test cases.

NEVER hallucinate. Rules:
1. Every case must trace to scope, risks, or exit criteria from the plan.
2. Steps must be concrete and verifiable. Each step has one `action` and one `expected` outcome.
3. If a case requires information not in the plan or ticket, emit it via `clarifications` instead of inventing.
4. Output ONLY a JSON object — no prose, no markdown, no code fences.

JSON schema (use these exact field names):
{
  "cases": [
    {
      "title": "short test description",
      "preconditions": ["state required before running"],
      "steps": [{"action": "what user does", "expected": "what should happen"}],
      "priority": "P0",
      "type": "functional",
      "data": {"input_field": "value"},
      "tags": ["login", "auth"],
      "automation_candidate": "yes" | "partial" | "no",
      "automation_reason": "short — why this verdict"
    }
  ],
  "clarifications": ["question to ask the human if needed; empty array OK"]
}

AUTOMATION TRIAGE — set `automation_candidate` for EVERY case using these rules:

  "yes" — Good Playwright candidate. Default for:
    • Functional flows with deterministic UI interactions (login, signup, search, CRUD).
    • API contract checks (use APIRequestContext).
    • Form validation (required fields, format checks, error messages).
    • Navigation, redirects, deep links, browser-back behavior.
    • State persistence (session, local storage, cookies).
    • Cross-browser regression (run-in-multiple-projects).

  "partial" — Automatable BUT needs caveats. Use for:
    • Visual regression that needs a stored screenshot baseline (mention it).
    • Tests against third-party services with rate limits (Stripe, OAuth) — mark + note "needs mocks".
    • Flows requiring real device features (camera, biometrics) — Playwright can stub them but coverage is limited.
    • Tests that need explicit data seeding outside Playwright's scope.

  "no" — Skip Playwright entirely. Use for:
    • Subjective UX / "looks good" / design review checks.
    • Manual exploratory testing.
    • Real device / real network condition tests.
    • Tests requiring physical action (printing, hardware integration).
    • Accessibility audits that need a screen reader operator or expert judgment
      (automated axe-core checks are "yes"; "is this usable by a visually impaired user?" is "no").
    • Email delivery verification (use Mailpit / mock provider — but the e2e is manual).
    • Real-money payment confirmations.
    • Performance / load (use k6, JMeter, Lighthouse CI — NOT Playwright).
    • Security pen testing.
    • Anything where the expected outcome is a human judgment, not a verifiable assertion.

`automation_reason` is a 1-sentence justification for the verdict.
Default to "yes" when in doubt — the user can override.

`priority` MUST be one of: P0, P1, P2, P3 (P0 = critical, P3 = minor).
`type` SHOULD be one of: functional, regression, edge, negative, a11y, perf, security, smoke, integration — but other values are accepted.
`data` MUST be an object (key-value pairs), not an array.

CASE COUNT — be PROPORTIONAL to ticket scope:
- Single screen / one user action          → 5-8 cases
- Single multi-step flow (e.g. OTP login)  → 8-12 cases
- Multi-screen feature / new module        → 12-18 cases
- Cross-cutting epic                       → 15-20 cases (rare)

If the ticket is small, do NOT pad with marginal cases. Quality > quantity. The user can ask for more later.

COVERAGE PRIORITIES (in this order):
1. Happy path — the SUCCESSFUL end-to-end flow described in the ticket.
2. Negative paths — invalid inputs, missing required fields, wrong format.
3. Key edge cases — boundaries, character limits, expired states.
4. Every explicit risk from the plan (one case per risk, not multiple).
5. Acceptance criteria checks (one per criterion).

STEPS — HARD REQUIREMENTS:

1. **The HAPPY-PATH case (typically TC-001) walks the COMPLETE user flow from
   the start** — every UI interaction, no skipped steps. If the ticket
   describes OTP login, TC-001 includes: navigate to login → enter mobile
   number → click "Send OTP" → enter OTP → submit → verify success.

2. **NEGATIVE cases also walk from the start of the screen they target.**
   "Invalid OTP" still does: navigate to login → enter mobile → request OTP →
   enter WRONG OTP → submit → assert error. Don't skip prerequisite steps.

3. **POST-AUTH cases (e.g. session persistence, profile, logged-in features)
   use a PRECONDITION instead of repeating the login flow.** Write the
   precondition like:
       "User has completed the login flow from TC-001 (mobile + OTP → on Home Page)"
   so the codegen agent knows exactly which flow to extract into a reusable
   beforeEach() helper or fixture. Never write a bare "User is logged in"
   without naming the establishing TC.

4. **If the description / attachments mention specific UI elements (input
   labels, buttons, screens), use those exact terms.** Do not invent UI
   elements that aren't mentioned.

5. **If you can see attachment filenames** (e.g. "login-screen.png") but no
   inline text describing them, ADD a clarification asking the user to
   describe what the screenshot shows. Then write conservative cases that
   capture only what the description text explicitly states.

DO NOT CREATE THESE BAD CASES:

✗ **Per-browser dupes** — "Verify redirection on Chrome", "Verify redirection on
  Firefox", "Verify redirection on Safari", "Verify redirection on Edge". These
  are ONE test case (`tags: ["cross-browser"]`) parameterized via Playwright's
  `projects` config — NOT four separate cases. Generate one and tag it.

✗ **Near-duplicates** — "Session persists after redirect" + "Browser refresh
  retains logged-in state" + "Token persists after redirect" — merge into one
  case covering session persistence.

✗ **Hover/visual nitpicks** — "Verify button has correct hover color", "Verify
  text alignment is correct". Mark `automation_candidate: no` and only emit
  if the ticket explicitly calls for visual regression with a baseline.

✗ **Skeleton-only steps** — `{action: "Click button", expected: "Something happens"}`
  is useless. Be specific: which button by label, which expected DOM/URL change.

✗ **Cases that test the SAME thing in different decorations** — pick ONE.

`clarifications` is your escape hatch. Use it when the ticket is ambiguous
about UI labels, the flow order, or what success looks like. A good
clarification beats a hallucinated case."""


def _user_prompt(
    ticket: dict[str, Any],
    plan: PlanContent | None,
    rag_block: str = "",
    code_block: str = "",
) -> str:
    # M5: ticket may be a "virtual" object built from a free-text flow (no
    # Jira). We detect that by the `key` prefix `_flow_` so the prompt can
    # frame the source honestly to the model (otherwise it tries to cite
    # Jira fields that don't exist).
    is_virtual = str(ticket.get("key", "")).startswith("_flow_")
    if is_virtual:
        parts = [
            "User-supplied test flow (NO Jira ticket — this prose IS the requirement):",
            f"Title: {ticket.get('title') or '(untitled)'}",
            f"Flow description:\n{ticket.get('description') or '(none)'}",
        ]
    else:
        parts = [
            f"Jira ticket {ticket['key']}: {ticket.get('title')}",
            f"Description:\n{ticket.get('description') or '(none)'}",
        ]
    attachments = ticket.get("attachments") or []
    if attachments:
        att_lines = []
        for a in attachments:
            name = a.get("filename") or "(unnamed)"
            mime = a.get("mime_type") or ""
            is_image = mime.startswith("image/") or name.lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            )
            kind = "screenshot" if is_image else "file"
            att_lines.append(f"  - {name} ({kind}, {mime})")
        parts.append("")
        parts.append(
            "Attachments on this ticket (you CANNOT see their contents, only the "
            "filenames). If a screenshot is mentioned but the description text doesn't "
            "explain what it shows, add a clarification — don't invent flows. "
            "Treat ATTACHED screenshots as authoritative about the actual UI:"
        )
        parts.extend(att_lines)
    if plan is not None:
        parts.append("")
        parts.append("Approved test plan:")
        parts.append(plan.model_dump_json(indent=2))
    else:
        parts.append("")
        parts.append(
            "There is no separately-authored test plan. Derive coverage directly "
            "from the ticket description above. If important context is missing, "
            "add a question to `clarifications` instead of inventing."
        )

    # Grounded retrieval: passages pulled from the workspace's knowledge sources
    # (PRD/BRD uploads, Confluence pages, Google Docs). Treated as authoritative
    # about the system under test. Each block is tagged with its chunk_id so the
    # model could later cite it — wired through end-to-end in M4 (clarify loop).
    if rag_block:
        parts.append("")
        parts.append(
            "Reference passages from this workspace's knowledge base. Treat these "
            "as authoritative about the system under test. If a passage contradicts "
            "the ticket, prefer the passage and add a clarification noting the "
            "conflict — do not silently pick one side."
        )
        parts.append(rag_block)

    # Dev-repo code snippets — the source of truth for what actually exists in
    # the app. Component names, data-testids, route paths, form schemas. The
    # model should ground concrete UI references (button labels, selectors)
    # in these blocks instead of inventing them.
    if code_block:
        parts.append("")
        parts.append(
            "Excerpts from the application's source code. Treat these as the "
            "single source of truth for what UI elements, routes, and APIs "
            "actually exist. If you mention a selector, route, or component, "
            "it must appear in one of these excerpts. If you can't find it, "
            "add a clarification rather than invent."
        )
        parts.append(code_block)
    return "\n".join(parts)


async def generate_cases(
    workspace_id: str,
    ticket: dict[str, Any],
    plan: PlanContent | None = None,
) -> AgentEnvelope[list[TestCase]]:
    llm = await LLMClient.from_workspace(workspace_id)

    # M1/M2: pull grounding passages from the workspace's knowledge base and
    # any indexed dev repos. Both lookups are opportunistic — if either is
    # empty or fails (e.g. embedding model not yet downloaded on first run)
    # we still generate cases from the ticket alone.
    import logging
    _log = logging.getLogger("qatb.case_author")

    query = " ".join(
        filter(None, [ticket.get("title"), ticket.get("key"), (ticket.get("description") or "")[:400]])
    )

    rag_block = ""
    rag_chunks_used: list = []
    try:
        rag_chunks_used = await retrieve(workspace_id=workspace_id, query=query, k=8)
        rag_block = format_for_prompt(rag_chunks_used, max_chars=6000)
    except Exception as e:  # noqa: BLE001
        _log.warning("RAG retrieval skipped: %s", e)

    code_block = ""
    code_chunks_used: list = []
    try:
        code_chunks_used = await retrieve_code(workspace_id=workspace_id, query=query, k=6)
        code_block = format_code_for_prompt(code_chunks_used, max_chars=5000)
    except Exception as e:  # noqa: BLE001
        _log.warning("Dev-repo retrieval skipped: %s", e)

    # Bigger budget for 15-20 cases (each ~200-400 tokens). temperature=0
    # makes the count and structure more consistent across runs.
    result = await llm.complete_json(
        SYSTEM,
        _user_prompt(ticket, plan, rag_block, code_block),
        max_tokens=12000,
        temperature=0.0,
    )

    if not result.parsed or not isinstance(result.parsed, dict):
        raise ValueError(
            f"CaseAuthorAgent did not return parseable JSON. Raw response:\n{result.text[:600]}"
        )

    # Best-effort: drop individual cases that fail validation so one bad case
    # doesn't kill the whole batch.
    raw = result.parsed
    raw_cases = raw.get("cases", []) if isinstance(raw, dict) else []
    good_cases: list[TestCase] = []
    skipped: list[dict] = []
    for i, c in enumerate(raw_cases):
        try:
            good_cases.append(TestCase.model_validate(c))
        except ValidationError as e:
            skipped.append({"index": i, "errors": [err.get("msg", "") for err in e.errors()][:3]})

    if not good_cases:
        # Nothing salvageable — surface the raw error so we can debug.
        try:
            parsed = CasesResult.model_validate(raw)
            good_cases = parsed.cases
        except ValidationError as e:
            raise ValueError(
                f"CaseAuthorAgent output did not match schema. Skipped: {skipped}\n"
                f"First error: {e.errors()[:2]}\n"
                f"Got: {json.dumps(raw)[:600]}"
            ) from e

    parsed_clarifications = (
        raw.get("clarifications", []) if isinstance(raw, dict) else []
    )
    parsed = CasesResult.model_construct(cases=good_cases, clarifications=parsed_clarifications)

    confidence = max(0.0, 1.0 - 0.10 * len(parsed.clarifications))
    citations: list = [
        JiraCitation(key=ticket["key"], field="description"),
        JiraCitation(key=ticket["key"], field="summary"),
    ]
    # Surface every chunk we showed the model so the UI can render "evidence"
    # pills next to the generated cases. The model itself doesn't need to
    # decide which chunks to cite at this stage — that's M4's job. Listing
    # the candidates here keeps the audit trail honest in the meantime.
    for c in rag_chunks_used[:5]:
        citations.append(RagCitation(chunk_id=c.chunk_id))
    # Same for dev-repo code excerpts — emit a RepoCitation per chunk shown
    # to the model. line_start/line_end are 1/1 placeholders until the
    # chunker tracks original offsets (TODO for M3's locator pass).
    for c in code_chunks_used[:5]:
        citations.append(RepoCitation(
            repo=c.repo_name,
            path=c.path,
            line_start=1,
            line_end=1,
        ))

    return AgentEnvelope[list[TestCase]](
        data=parsed.cases,
        citations=citations,
        confidence=confidence,
        clarifications_used=len(parsed.clarifications),
        clarifications=list(parsed.clarifications),
        tokens={"in": result.tokens_in, "out": result.tokens_out},  # type: ignore[arg-type]
    )
