"""Anti-hallucination grounding pass.

Two responsibilities:

  PRE-FLIGHT  (before codegen):
    Walk the approved test cases, find concrete UI intents ("click Login",
    "go to /home", "fill OTP input"), resolve each against the workspace's
    locator_index / dev_repo_routes, and emit one of:
      - A grounded reference  → injected into the codegen prompt as the
        authoritative selector/route, with the source file:line cited.
      - A clarify question     → blocks the SSE stream until the user
        answers ("I couldn't find a stable selector for 'Submit'. Use
        getByRole('button', {name:/submit/i}) or supply a data-testid?").

  POST-FLIGHT (after codegen):
    Scan the generated bundle's content for selectors and `page.goto(...)`
    calls. Anything that doesn't match a row in locator_index / dev_repo_routes
    is flagged. If the user has indexed a dev repo, those flags become hard
    clarify questions (the contract is real). If no dev repo is indexed, the
    flags become soft warnings — we don't block when we have nothing to
    check against.

The clarify question format mirrors the existing `data_needs` schema so the
frontend's clarify UI can reuse the same form components in M4's UI follow-up.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

from app.agents.case_author import TestCase
from app.db.queries import fetchrow
from app.services.locator_extract import LocatorMatch, find_locator
from app.services.route_extract import RouteMatch, find_route

log = logging.getLogger("qatb.grounding")

# Confidence threshold below which we treat the top locator hit as "no match"
# and ask the user instead. Tuned empirically; raise if false positives become
# annoying, lower if the agent invents too much. BM25 * kind_boost on a
# well-named testid match typically scores 0.4+, so 0.05 is conservative.
_MIN_LOCATOR_SCORE = 0.05


# =============================================================================
# Models — clarify questions follow the same shape as data_needs so the UI
# can render them with the same components.
# =============================================================================


@dataclass
class ClarifyQuestion:
    """A blocking question surfaced to the user. The `id` becomes the dict
    key when the frontend re-submits with `clarifications: {id: answer}`."""

    id: str
    label: str
    reason: str
    # "selector" → user provides a stable selector for the missing element
    # "route"    → user provides the URL path that step actually navigates to
    # "free"     → free-text fallback for ambiguous cases
    kind: str = "selector"
    placeholder: str = ""
    intent: str = ""
    suggested: list[str] = field(default_factory=list)


@dataclass
class GroundedReference:
    """A successful match the codegen prompt should treat as canonical."""

    intent: str
    kind: str   # "locator" | "route"
    value: str  # the selector value or path pattern
    selector_kind: str | None = None
    source_file: str | None = None
    source_line: int | None = None
    component: str | None = None


@dataclass
class PreflightResult:
    grounded: list[GroundedReference]
    unresolved: list[ClarifyQuestion]
    prompt_block: str
    intents_total: int
    intents_resolved: int


# =============================================================================
# Pre-flight: extract UI intents from cases
# =============================================================================


# Heuristic patterns. We're not building a real NLU here — just catching the
# 80% case: "click <thing>", "enter ... in <field>", "tap <thing>",
# "navigate to <path>", "go to <page>".
_CLICK_RE = re.compile(
    r"""
    \b(?:click|tap|press|select|choose|hit)\s+
    (?:on\s+)?(?:the\s+)?
    (?P<thing>(?:["'][^"']+["']|[A-Za-z0-9 ._\-]{2,60}?))
    (?:\s+(?:button|link|tab|icon|menu|item|option|toggle|checkbox|radio))?
    (?=$|[.,;:])
    """,
    re.IGNORECASE | re.VERBOSE,
)

_TYPE_RE = re.compile(
    r"""
    \b(?:enter|type|fill|input|paste)\s+
    (?:[^,]+?\s+)?               # value description (skip — we want the field)
    (?:in(?:to)?|on)\s+
    (?:the\s+)?
    (?P<thing>["']?[A-Za-z0-9 ._\-]{2,60}?["']?)
    (?:\s+(?:field|input|box|textbox|textarea))?
    (?=$|[.,;:])
    """,
    re.IGNORECASE | re.VERBOSE,
)

_GOTO_RE = re.compile(
    r"""
    \b(?:navigate\s+to|go\s+to|open|visit|load)\s+
    (?:the\s+)?
    (?P<thing>["']?[A-Za-z0-9 ._/\-]{2,80}?["']?)
    (?:\s+page)?
    (?=$|[.,;:])
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _clean(s: str) -> str:
    return s.strip().strip("\"'").strip()


def extract_intents(cases: list[TestCase]) -> list[tuple[str, str]]:
    """Return list of (kind, intent_text) tuples found across all cases.
    kind is one of 'locator' or 'route'. De-duplicated, intent_text is the
    raw UI thing the user wants to interact with."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, raw: str) -> None:
        intent = _clean(raw)
        if not intent or len(intent) > 80:
            return
        key = (kind, intent.lower())
        if key in seen:
            return
        seen.add(key)
        found.append((kind, intent))

    sources: list[str] = []
    for c in cases:
        sources.append(c.title)
        sources.extend(c.preconditions)
        for s in c.steps:
            sources.append(s.action)
            sources.append(s.expected)

    for text in sources:
        if not text:
            continue
        for m in _GOTO_RE.finditer(text):
            _add("route", m.group("thing"))
        for m in _CLICK_RE.finditer(text):
            _add("locator", m.group("thing"))
        for m in _TYPE_RE.finditer(text):
            _add("locator", m.group("thing"))

    return found


# =============================================================================
# Pre-flight: resolve + build prompt block + emit clarify questions
# =============================================================================


def _quick_id(prefix: str, intent: str) -> str:
    """Stable, slug-ish id from an intent string. Lower-case alnum + dashes."""
    slug = re.sub(r"[^a-z0-9]+", "-", intent.lower()).strip("-")[:50]
    return f"{prefix}.{slug or 'unknown'}"


def _has_dev_repo(workspace_id: str) -> bool:
    # Implemented async below; sync alias kept for type ergonomics.
    raise NotImplementedError


async def _workspace_has_indexed_repo(workspace_id: str) -> bool:
    row = await fetchrow(
        """
        select 1
          from public.repos
         where workspace_id = $1::uuid
           and file_count is not null
           and file_count > 0
         limit 1
        """,
        workspace_id,
    )
    return row is not None


async def _resolve_locator(
    *, workspace_id: str, intent: str, repo_id: str | None,
) -> LocatorMatch | None:
    matches = await find_locator(
        workspace_id=workspace_id, intent=intent, k=3, repo_id=repo_id,
    )
    if not matches:
        return None
    top = matches[0]
    return top if top.score >= _MIN_LOCATOR_SCORE else None


async def _resolve_route(
    *, workspace_id: str, intent: str,
) -> RouteMatch | None:
    # Routes are sparse and intent often already contains the path, so we
    # also try the raw intent as a substring match.
    matches = await find_route(workspace_id=workspace_id, intent=intent, k=3)
    return matches[0] if matches else None


async def preflight_grounding(
    *,
    workspace_id: str,
    cases: list[TestCase],
    dev_repo_id: str | None = None,
) -> PreflightResult:
    """Resolve every detected UI intent to a real locator/route where
    possible. If the workspace has no indexed dev repo at all, we skip the
    whole pass — there's nothing to ground against. Anything that doesn't
    resolve against an indexed repo is returned as `unresolved`; the caller
    folds those into the prompt as a best-effort instruction (see
    `format_unresolved_prompt_block`) rather than blocking on them — the
    contract activates the moment a repo is indexed, but never halts codegen.
    """
    if not await _workspace_has_indexed_repo(workspace_id):
        log.info(
            "preflight grounding skipped — no indexed dev repo for workspace %s",
            workspace_id,
        )
        return PreflightResult(grounded=[], unresolved=[], prompt_block="",
                               intents_total=0, intents_resolved=0)

    intents = extract_intents(cases)
    grounded: list[GroundedReference] = []
    unresolved: list[ClarifyQuestion] = []

    for kind, intent in intents:
        qid = _quick_id(kind, intent)

        if kind == "locator":
            hit = await _resolve_locator(
                workspace_id=workspace_id, intent=intent, repo_id=dev_repo_id,
            )
            if hit:
                grounded.append(GroundedReference(
                    intent=intent,
                    kind="locator",
                    value=hit.selector_value,
                    selector_kind=hit.selector_kind,
                    source_file=hit.path,
                    source_line=hit.line,
                    component=hit.component,
                ))
            else:
                # Build a short list of "almost matches" — surfaced to the
                # model as candidates it can use for its best-effort guess.
                near = await find_locator(
                    workspace_id=workspace_id, intent=intent, k=3, repo_id=dev_repo_id,
                )
                unresolved.append(ClarifyQuestion(
                    id=qid,
                    label=f"Stable selector for '{intent}'",
                    reason=(
                        f"No confident selector match for '{intent}' in the "
                        f"indexed dev repo."
                    ),
                    kind="selector",
                    intent=intent,
                    placeholder="e.g. data-testid=login-btn  or  role=button,name=Login",
                    suggested=[
                        f"{m.selector_kind}={m.selector_value} ({m.path}:{m.line})"
                        for m in near[:3]
                    ],
                ))
        else:  # route
            hit = await _resolve_route(workspace_id=workspace_id, intent=intent)
            if hit:
                grounded.append(GroundedReference(
                    intent=intent,
                    kind="route",
                    value=hit.path_pattern,
                    source_file=hit.source_file,
                    source_line=hit.line,
                ))
            else:
                unresolved.append(ClarifyQuestion(
                    id=qid,
                    label=f"URL for '{intent}'",
                    reason=(
                        f"No route matching '{intent}' in the indexed dev repo."
                    ),
                    kind="route",
                    intent=intent,
                    placeholder="/auth/sign-in",
                ))

    return PreflightResult(
        grounded=grounded,
        unresolved=unresolved,
        prompt_block=_format_prompt_block(grounded),
        intents_total=len(intents),
        intents_resolved=len(grounded),
    )


def format_unresolved_prompt_block(unresolved: list[ClarifyQuestion]) -> str:
    """Best-effort instruction block for intents that didn't resolve against
    the indexed repo. Rather than blocking on a clarify question, we tell the
    model to write its best guess AND flag it inline so a human can spot and
    verify it later."""
    if not unresolved:
        return ""
    lines: list[str] = [
        "=== UNRESOLVED REFERENCES (best effort — do not skip) ===\n"
        "No confident match was found in the indexed repo for these. Write "
        "your best working guess for each AND add an inline comment directly "
        "above the line that uses it:\n"
        "  // UNVERIFIED: <intent> — not found in indexed repo, confirm before relying on this\n"
        "Still write complete, runnable code — do not omit the step."
    ]
    for q in unresolved:
        near = f"  (candidates: {', '.join(q.suggested)})" if q.suggested else ""
        lines.append(f"  • '{q.intent}' (kind: {q.kind}){near}")
    lines.append("=== end unresolved references ===")
    return "\n".join(lines)


def _format_prompt_block(grounded: list[GroundedReference]) -> str:
    """Render the resolved references for injection into the codegen prompt.
    The instruction is short and absolute — these references are CANONICAL,
    the model uses them as-is, no variants."""
    if not grounded:
        return ""
    locators = [g for g in grounded if g.kind == "locator"]
    routes = [g for g in grounded if g.kind == "route"]

    lines: list[str] = []
    lines.append(
        "=== GROUNDED REFERENCES (anti-hallucination contract) ===\n"
        "These selectors and routes were resolved from the indexed dev repo "
        "or supplied by the user. They are AUTHORITATIVE. Use them VERBATIM "
        "in the generated code. Do not invent variants, do not 'improve' them, "
        "do not substitute equivalent-looking selectors. If a step requires "
        "an element NOT listed here, ground it on the test case's own "
        "data dict or emit a TODO referencing the case — do not fabricate."
    )
    if locators:
        lines.append("\nLocators:")
        for g in locators:
            ref = (
                f"  • '{g.intent}' → use selector_kind={g.selector_kind} "
                f"value={g.value!r}"
            )
            if g.source_file:
                ref += f"  (from {g.source_file}"
                if g.source_line:
                    ref += f":{g.source_line}"
                ref += ")"
            if g.component:
                ref += f"  [tag: <{g.component}>]"
            lines.append(ref)
    if routes:
        lines.append("\nRoutes:")
        for g in routes:
            ref = f"  • '{g.intent}' → page.goto({g.value!r})"
            if g.source_file:
                ref += f"  (from {g.source_file})"
            lines.append(ref)
    lines.append("=== end grounded references ===")
    return "\n".join(lines)


# =============================================================================
# Post-flight: scan generated bundle for invented selectors / routes
# =============================================================================


# Conservative scan — we only flag literals we're confident the LLM made up.
# Dynamic selectors (template-string concatenation) are out of scope.
_TESTID_RE = re.compile(r"""getByTestId\(\s*['"]([^'"]+)['"]""")
_GOTO_PATH_RE = re.compile(r"""(?:page|context|browser)\.goto\(\s*['"]([^'"]+)['"]""")


@dataclass
class PostflightFlag:
    """One suspicious literal found in the generated bundle."""

    kind: str           # "selector" | "route"
    value: str
    file: str
    line: int
    reason: str


@dataclass
class PostflightResult:
    flags: list[PostflightFlag]
    unresolved: list[ClarifyQuestion]


async def postflight_scan(
    *,
    workspace_id: str,
    files: Iterable,  # list of GeneratedFile-shaped objects
    grounded_locator_values: set[str],
    grounded_route_values: set[str],
    dev_repo_id: str | None = None,
) -> PostflightResult:
    """Scan the model's output for selectors/routes it might have invented.
    A literal that wasn't in the pre-flight grounded set AND has no row in
    locator_index/dev_repo_routes for this workspace becomes a flag.
    """
    if not await _workspace_has_indexed_repo(workspace_id):
        return PostflightResult(flags=[], unresolved=[])

    flags: list[PostflightFlag] = []
    for f in files:
        path = getattr(f, "path", "")
        content = getattr(f, "content", "") or ""
        for m in _TESTID_RE.finditer(content):
            value = m.group(1)
            if value in grounded_locator_values:
                continue
            hits = await find_locator(
                workspace_id=workspace_id, intent=value, k=1,
                repo_id=dev_repo_id, kinds=["testid"],
            )
            if hits and hits[0].selector_value == value:
                continue
            line = content.count("\n", 0, m.start()) + 1
            flags.append(PostflightFlag(
                kind="selector",
                value=value,
                file=path,
                line=line,
                reason=(
                    f"data-testid={value!r} is referenced in the generated "
                    f"code but does not appear in the indexed dev repo."
                ),
            ))
        for m in _GOTO_PATH_RE.finditer(content):
            value = m.group(1)
            # Skip absolute URLs (page.goto('https://...')) — those are the
            # base URL, not a repo route.
            if value.startswith(("http://", "https://", "//")):
                continue
            if value in grounded_route_values:
                continue
            hits = await find_route(workspace_id=workspace_id, intent=value, k=1)
            if hits and hits[0].path_pattern == value:
                continue
            line = content.count("\n", 0, m.start()) + 1
            flags.append(PostflightFlag(
                kind="route",
                value=value,
                file=path,
                line=line,
                reason=(
                    f"page.goto({value!r}) targets a route that does not "
                    f"appear in the indexed dev repo."
                ),
            ))

    # Convert flags into informational items. We dedupe by (kind, value) so
    # the same invented selector across 10 files yields ONE entry. These are
    # surfaced to the user as warnings alongside the completed bundle, not as
    # a blocking question — the code was already generated.
    seen: set[tuple[str, str]] = set()
    unresolved: list[ClarifyQuestion] = []
    for fl in flags:
        key = (fl.kind, fl.value)
        if key in seen:
            continue
        seen.add(key)
        unresolved.append(ClarifyQuestion(
            id=_quick_id(f"post.{fl.kind}", fl.value),
            label=(
                f"Verify selector '{fl.value}'"
                if fl.kind == "selector"
                else f"Verify route '{fl.value}'"
            ),
            reason=fl.reason + f"  (first occurrence: {fl.file}:{fl.line})",
            kind=fl.kind,
            intent=fl.value,
        ))
    return PostflightResult(flags=flags, unresolved=unresolved)
