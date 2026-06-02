"""PWCodegenAgent — generates a runnable Playwright bundle from approved cases.

Output is a multi-file *bundle* (CodegenBundle), not a single string. Each
file has a target path and an operation marker (create / update / merge) so
the frontend can render a file tree, show diffs, and zip everything for
download with the correct repo layout.

The agent receives an optional framework_context snapshot (README, package.json,
sample tests). When present it MUST reuse existing imports/page-objects/fixtures
instead of inventing new ones; when absent (greenfield) it generates a full
runnable project skeleton.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agents.base import AgentEnvelope, JiraCitation
from app.agents.case_author import TestCase
from app.services.llm import LLMClient


class CodegenInput(BaseModel):
    base_url: str = Field(min_length=1)
    framework_hints: str = ""
    test_file_name: str = Field(default="generated.spec.ts")
    framework_context: str = ""
    local_path: str = ""
    # M4: pre-resolved selectors and routes from the dev repo. Empty when no
    # dev repo is indexed; non-empty blocks invented selectors via the
    # codegen prompt (see SYSTEM).
    grounded_block: str = ""
    # M5: branch the system prompt by project state.
    #   new_project       — full skeleton
    #   existing_no_tests — add tests into an existing project, merge config
    #   existing_same_fw  — extend the existing Playwright framework
    #   existing_diff_fw  — migrate from another framework into Playwright
    project_state: Literal[
        "new_project", "existing_no_tests", "existing_same_fw", "existing_diff_fw"
    ] = "new_project"
    # M5: only used when project_state == existing_diff_fw.
    #   migrate   — generate Playwright in parallel folder
    #   keep_both — same as migrate + emit MIGRATION.md mapping old→new
    migration_mode: Literal["migrate", "keep_both"] = "migrate"
    # M5: per-case override prompts (case ord/id → user delta-prompt). Folded
    # into each case's prompt block so the LLM applies them locally.
    case_overrides: dict[str, str] = Field(default_factory=dict)


class GeneratedFile(BaseModel):
    """One file in the codegen output bundle."""

    path: str = Field(min_length=1, description="Repo-relative path, e.g. 'src/tests/appA/otp.spec.ts'")
    operation: Literal["create", "update", "merge"] = "create"
    content: str = Field(min_length=1)
    summary: str = ""
    reason: str = ""

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, v: Any) -> str:
        # Force forward slashes + strip leading slashes
        s = str(v).replace("\\", "/").lstrip("/")
        # Disallow path traversal
        if ".." in s.split("/"):
            raise ValueError(f"Refusing path with parent-dir segment: {s!r}")
        return s


class CodegenBundle(BaseModel):
    files: list[GeneratedFile] = Field(min_length=1)
    install_commands: list[str] = Field(default_factory=list)
    run_commands: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# --- back-compat alias used by the older single-file flow ----------------
# (Kept so import sites still work; only `files` is the canonical output.)
class CodegenResult(BaseModel):
    code: str = ""
    file_name: str = "generated.spec.ts"
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------


SYSTEM = """You are a senior Playwright + TypeScript engineer.

You produce a complete, runnable **bundle** of files that translates the given Jira ticket + test cases into Playwright tests inside the provided framework.

OUTPUT FORMAT — JSON ONLY, no prose, no markdown, no code fences:
{
  "files": [
    {
      "path": "src/tests/appA/otp.spec.ts",
      "operation": "create",       // "create" | "update" | "merge"
      "content": "<FULL FILE CONTENT — NEVER use placeholders like '...' or '// rest unchanged'>",
      "summary": "Adds OTP login spec covering happy/invalid/expired paths",
      "reason": "Required by TC-005..TC-007 in the flow guidance"
    }
  ],
  "install_commands": ["npm install", "npx playwright install --with-deps"],
  "run_commands": ["npx playwright test src/tests/appA/otp.spec.ts"],
  "notes": ["Set TEST_USER_EMAIL and TEST_USER_PASSWORD in .env before running"]
}

Hard requirements:

1. Output MUST be ONE JSON object. No leading prose, no trailing prose.
2. `files` is an array of 1-15 entries. Each entry's `content` is the COMPLETE
   file as it should exist on disk — never truncate, never use '...'.
3. `operation`:
   - "create"  → file doesn't exist in the framework; emitting fresh.
   - "update"  → file exists; emit the FULL new content (the diff is computed by the UI).
   - "merge"   → file is a barrel/index export; merge new entries while
                 preserving the rest. Still emit the FULL final content.
4. `path` is relative to the repo root. Use forward slashes. Never include `..`.
5. Group changes correctly:
   - New POM → src/pages/<app>/<NewPage>.ts (operation: create)
   - Existing POM gaining a field/method → operation: update, full file
   - New module method → either new module file (create) or update existing
   - Spec file → src/tests/<app>/<feature>.spec.ts (create)
   - Barrel exports → src/pages/index.ts etc. (operation: merge)
6. Imports:
   - Use the EXISTING import patterns from the framework context.
   - For Playwright tests in this framework: `import { test, expect } from '../../fixtures';`
   - If using `loggedInPage` fixture confirm it's exported from the fixtures barrel.
7. Locators MUST be semantic (`getByRole`, `getByLabel`, `getByPlaceholder`,
   `getByTestId`). Never use brittle CSS like `.btn-primary` unless the framework
   already uses it.
8. Each test() block must have:
   - A comment line above with TC-### and the source case title.
   - At least one expect(...) assertion.
   - test.step('phase', async () => {...}) wrappers when there are 3+ actions.
9. If the case set requires data not provided, reference env vars via
   `process.env.NAME` and list them under `notes`.
10. NEVER fabricate selectors that contradict the framework's sample tests.
11. If `framework_context` is empty (greenfield), output the FULL project skeleton:
    package.json, tsconfig.json, playwright.config.ts, .env.example, .gitignore,
    src/fixtures/index.ts, src/pages/<app>/*Page.ts, src/tests/<app>/<feature>.spec.ts,
    and a README.md with run instructions. Set `install_commands` and `run_commands`
    accordingly.
12. If the framework context shows existing POMs/modules covering the feature,
    PREFER updating them (operation: update) over creating new files.

13. PRECONDITIONS — HARD REQUIREMENT, no exceptions:

    Each test case has a `preconditions` array describing the STATE that must
    exist before the test's `steps` run. You MUST materialize every
    precondition as actual code. NEVER emit a placeholder comment like
    `// Assume user is logged in` or `// Setup done elsewhere`.

    Rules for translating preconditions into code:

    (a) "User has completed the login flow from TC-XXX" or "User is logged in"
        appears on 2+ cases:
          → Extract the happy-path login flow (from the referenced TC, or from
            the FIRST case in this set that performs the login) into a reusable
            helper named `loginAsTestUser(page)` (or use the framework's
            existing fixture if one exists in the framework_context — look for
            `loggedInPage`, `authenticatedPage`, etc.).
          → Call that helper inside `test.beforeEach()` of the appropriate
            describe block (or use the existing fixture in test args).
          → The helper itself reads test data from `process.env.TEST_USER_*`
            and follows the codegen instructions for the OTP source / etc.

    (b) "User is on the X page":
          → `await page.goto('/x');` + an assertion the page loaded.

    (c) "User has data X seeded":
          → If the framework has an API helper, use it. Otherwise leave a
            clearly-flagged TODO with a `notes` entry telling the user how to
            seed the data.

    (d) Multi-step preconditions ("User has 3 items in cart"):
          → Inline the setup steps via a helper or APIRequestContext call —
            still real code, never a comment.

    (e) NEGATIVE-path cases (invalid OTP, empty field) that share the SAME
        prerequisite UI navigation as the happy path:
          → Inline the navigate + mobile-entry + OTP-request steps at the
            start of each negative test (they're SHORT and the negative
            branches at the input step). Do NOT skip these steps.

    If a test would fail because the precondition wasn't set up, you have
    NOT done your job. Always emit real, executable setup code."""


def _user_prompt(ticket: dict[str, Any], cases: list[TestCase], inp: CodegenInput) -> str:
    parts = [
        f"Jira ticket: {ticket['key']} — {ticket.get('title') or ''}",
        f"Base URL: {inp.base_url}",
    ]
    if inp.local_path.strip():
        parts.append(f"Target local path (for relative imports): {inp.local_path.strip()}")
    if inp.framework_hints.strip():
        parts.append("")
        parts.append("Flow guidance / conventions from the user (PRIMARY INSTRUCTIONS):")
        parts.append(inp.framework_hints.strip())
    if inp.framework_context.strip():
        parts.append("")
        parts.append(
            "=== Existing framework snapshot (READ-ONLY — reuse imports / page objects "
            "/ fixtures from this code rather than writing new equivalents) ==="
        )
        parts.append(inp.framework_context.strip()[:12000])
        parts.append("=== end framework snapshot ===")
    else:
        parts.append("")
        parts.append(
            "(No existing framework provided — this is a GREENFIELD run. Output the "
            "complete project skeleton including package.json, playwright.config.ts, "
            "tsconfig.json, fixtures, POMs, the new spec, and README.md.)"
        )
    if inp.grounded_block.strip():
        parts.append("")
        parts.append(inp.grounded_block.strip())

    # M5: tell the model what *kind* of output we expect. Each branch has a
    # different "do/don't" list — keeping these out of SYSTEM means we don't
    # ship the full instruction set when the user is just adding one spec.
    parts.append("")
    parts.append(_project_state_directive(inp))

    parts.append("")
    parts.append(f"Suggested spec file name: {inp.test_file_name}")
    parts.append("")
    parts.append("Test cases to translate (JSON):")
    # M5: per-case overrides are attached on the case dict so the model sees
    # them next to the case body. Keyed by ord (str) — matches CodegenRequest.
    parts.append(
        json.dumps(
            [
                {
                    "tc_no": f"TC-{i + 1:03d}",
                    "title": c.title,
                    "type": c.type,
                    "priority": c.priority,
                    "preconditions": c.preconditions,
                    "steps": [s.model_dump() for s in c.steps],
                    "data": c.data,
                    "tags": c.tags,
                    # Only emit the key when an override is present; keeps
                    # the JSON tight for the common no-override path.
                    **(
                        {"override_prompt": inp.case_overrides[str(i)]}
                        if str(i) in inp.case_overrides
                        else {}
                    ),
                }
                for i, c in enumerate(cases)
            ],
            indent=2,
        )
    )
    if inp.case_overrides:
        parts.append("")
        parts.append(
            "When a case has an `override_prompt` field, apply that instruction "
            "ONLY to that case — it's the user's delta on top of the case body. "
            "Do not let one case's override leak into another's generated code."
        )
    return "\n".join(parts)


def _project_state_directive(inp: CodegenInput) -> str:
    """Return the right 'what to emit' instructions for the project state."""
    if inp.project_state == "new_project":
        return (
            "=== PROJECT STATE: new_project ===\n"
            "Emit a COMPLETE runnable Playwright project skeleton. Every file is "
            "operation=create. Required files at minimum:\n"
            "  - package.json (with @playwright/test + dotenv)\n"
            "  - playwright.config.ts (browsers, retries, html reporter)\n"
            "  - tsconfig.json\n"
            "  - .env.example with every process.env.* the tests reference\n"
            "  - .gitignore (node_modules, .env, playwright-report, test-results)\n"
            "  - README.md with install + run instructions\n"
            "  - src/fixtures/index.ts (test fixtures)\n"
            "  - src/pages/* (page objects for every screen referenced)\n"
            "  - src/tests/*.spec.ts (one per feature)\n"
            "  - .github/workflows/playwright.yml (CI ready)"
        )
    if inp.project_state == "existing_no_tests":
        return (
            "=== PROJECT STATE: existing_no_tests ===\n"
            "The user has a project but no test framework yet. ADD Playwright "
            "alongside their code without disturbing it. Rules:\n"
            "  - package.json: operation=merge — only add the new @playwright/test "
            "    + dotenv deps. Do NOT replace the user's scripts, deps, or version.\n"
            "  - tsconfig.json: operation=merge IF one exists, else create. Never "
            "    overwrite the user's compilerOptions.\n"
            "  - playwright.config.ts: operation=create — place at repo root.\n"
            "  - All test files under tests/ (or e2e/ if that folder already exists).\n"
            "  - Do not emit a .gitignore (the user has one)."
        )
    if inp.project_state == "existing_same_fw":
        return (
            "=== PROJECT STATE: existing_same_fw ===\n"
            "A Playwright framework already exists in the dev repo. EXTEND it; "
            "do NOT recreate scaffolding. Hard rules:\n"
            "  - NEVER emit package.json, tsconfig.json, playwright.config.ts.\n"
            "  - Reuse existing fixtures, page objects, helpers from the framework "
            "    context block above — operation=update when extending them.\n"
            "  - New specs: operation=create under the framework's existing tests/ "
            "    directory, following the same naming convention as the samples.\n"
            "  - If a similar spec already exists in the framework context, "
            "    operation=update on THAT file and ADD test() blocks rather than "
            "    creating a duplicate file."
        )
    # existing_diff_fw
    keep_both = inp.migration_mode == "keep_both"
    return (
        "=== PROJECT STATE: existing_diff_fw ===\n"
        f"Migration mode: {inp.migration_mode}.\n"
        "The dev repo uses a different test framework. Translate the cases into "
        "Playwright in a PARALLEL folder so the user's existing tests are not "
        "touched. Rules:\n"
        "  - Place all generated files under tests-playwright/ at the repo root.\n"
        "  - operation=create for everything (the folder is new).\n"
        "  - Include playwright.config.ts + minimal package.json IF the project's "
        "    package.json doesn't already include @playwright/test.\n"
        "  - Reuse selectors that already appear in the other framework's tests "
        "    (they're in the framework_context above) — those selectors are PROVEN "
        "    against the live app, do not invent variants.\n"
        + (
            "  - ALSO emit MIGRATION.md at the repo root: a markdown table mapping "
            "each old test file → its Playwright equivalent, with a one-line note "
            "on parity (full / partial / skipped + reason)."
            if keep_both else
            "  - Do NOT emit MIGRATION.md; user picked migrate-only."
        )
    )


_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.MULTILINE)


def _strip_code_fences(s: str) -> str:
    return _FENCE_RE.sub("", s).strip()


def _normalize_bundle(b: CodegenBundle) -> CodegenBundle:
    """Tidy up any model quirks before returning."""
    seen_paths: set[str] = set()
    dedup: list[GeneratedFile] = []
    for f in b.files:
        f.content = _strip_code_fences(f.content)
        if f.path in seen_paths:
            continue
        seen_paths.add(f.path)
        dedup.append(f)
    b.files = dedup
    return b


async def generate_playwright_bundle(
    workspace_id: str,
    ticket: dict[str, Any],
    cases: list[TestCase],
    inp: CodegenInput,
) -> AgentEnvelope[CodegenBundle]:
    if not cases:
        raise ValueError("No test cases provided — generate cases first.")

    llm = await LLMClient.from_workspace(workspace_id)
    # Bundle output runs longer than a single file; give it room.
    result = await llm.complete_json(
        SYSTEM, _user_prompt(ticket, cases, inp), max_tokens=24000, temperature=0.0
    )

    if not result.parsed or not isinstance(result.parsed, dict):
        raise ValueError(
            f"PWCodegenAgent did not return parseable JSON. Raw response:\n{result.text[:800]}"
        )

    try:
        bundle = CodegenBundle.model_validate(result.parsed)
    except ValidationError as e:
        # Try a partial-rescue: drop any file entry that fails validation and
        # validate the rest. Better than nothing if the model produced 9 good
        # files and 1 malformed one.
        raw_files = (result.parsed.get("files") or []) if isinstance(result.parsed, dict) else []
        good_files: list[GeneratedFile] = []
        skipped = []
        for i, f in enumerate(raw_files):
            try:
                good_files.append(GeneratedFile.model_validate(f))
            except ValidationError:
                skipped.append(i)
        if not good_files:
            raise ValueError(
                f"PWCodegenAgent output did not match schema: {e.errors()[:3]}\n"
                f"Got keys: {list(result.parsed.keys()) if isinstance(result.parsed, dict) else 'n/a'}"
            ) from e
        bundle = CodegenBundle(
            files=good_files,
            install_commands=result.parsed.get("install_commands", []) or [],
            run_commands=result.parsed.get("run_commands", []) or [],
            notes=(result.parsed.get("notes", []) or [])
            + ([f"Dropped {len(skipped)} malformed file entries from the model output."] if skipped else []),
        )

    bundle = _normalize_bundle(bundle)

    citations = [
        JiraCitation(key=ticket["key"], field="summary"),
        JiraCitation(key=ticket["key"], field="description"),
    ]
    confidence = 1.0 if sum(len(f.content) for f in bundle.files) > 400 else 0.5

    return AgentEnvelope[CodegenBundle](
        data=bundle,
        citations=citations,
        confidence=confidence,
        clarifications_used=0,
        tokens={"in": result.tokens_in, "out": result.tokens_out},  # type: ignore[arg-type]
    )


# --- Legacy single-file generator (kept for callers that still need it) ---


async def generate_playwright(
    workspace_id: str,
    ticket: dict[str, Any],
    cases: list[TestCase],
    inp: CodegenInput,
) -> AgentEnvelope[CodegenResult]:
    """Back-compat shim: returns the first spec file of the bundle as a flat
    `{code, file_name, notes}` result. New callers should use
    `generate_playwright_bundle` directly."""
    env = await generate_playwright_bundle(workspace_id, ticket, cases, inp)
    spec = next(
        (f for f in env.data.files if f.path.endswith((".spec.ts", ".test.ts"))),
        env.data.files[0],
    )
    return AgentEnvelope[CodegenResult](
        data=CodegenResult(
            code=spec.content,
            file_name=spec.path.rsplit("/", 1)[-1],
            notes=env.data.notes,
        ),
        citations=env.citations,
        confidence=env.confidence,
        clarifications_used=env.clarifications_used,
        tokens=env.tokens,
    )
