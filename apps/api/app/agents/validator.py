"""ValidatorAgent — second-pass review of a CodegenBundle.

Re-reads the bundle plus the framework snapshot and flags problems before
the user downloads the zip: missing imports, undefined fixtures, references
to files/classes that don't exist in the framework, style mismatches, etc.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.agents.codegen import CodegenBundle
from app.services.llm import LLMClient


Severity = Literal["error", "warning", "info"]


class ValidatorIssue(BaseModel):
    file: str = ""
    line: int | None = None
    severity: Severity = "warning"
    message: str
    fix_hint: str = ""


class ValidatorReport(BaseModel):
    issues: list[ValidatorIssue] = Field(default_factory=list)
    summary: str = ""


SYSTEM = """You are a strict code reviewer for Playwright + TypeScript bundles.

You receive:
1. A multi-file bundle that an upstream agent just generated.
2. A snapshot of the framework the bundle should fit into (README, package.json, sample tests).

Your only job: find bugs and surface them as STRUCTURED issues. Don't rewrite code; report.

Output ONLY this JSON shape (no prose, no markdown, no fences):
{
  "issues": [
    {
      "file": "src/tests/appA/otp.spec.ts",
      "line": 14,                       // optional, omit if not file-specific
      "severity": "error" | "warning" | "info",
      "message": "what's wrong in plain English",
      "fix_hint": "what the user should do to fix it"
    }
  ],
  "summary": "2-3 sentence overall verdict"
}

What to look for (each becomes an issue):

ERRORS (will break compile / runtime):
  - Imports referencing files/exports that don't exist in either the bundle or the framework snapshot.
  - Uses of fixtures (loggedInPage, authToken) that aren't exported from the framework's fixtures barrel.
  - Uses of POM classes that don't exist anywhere.
  - File path doesn't fit the framework's existing folder structure
    (e.g. wrote 'tests/' but framework uses 'src/tests/').
  - CommonJS `require()` in an ESM project, or vice versa.
  - Spec file with no test() calls.
  - test() block with no expect(...) assertion.
  - Test references a state that was never set up — common smell is a
    placeholder comment like `// Assume user is logged in`, `// User is on
    the X page`, `// Pre: …`, `// TODO: set up state`. These comments mean
    the precondition wasn't materialized into real code. Flag every one
    of them as an ERROR with fix_hint pointing to either an existing
    fixture, a beforeEach hook, or inline setup steps.
  - Test calls `loginAsTestUser` / `loginAsUser` / `authenticate` etc. that
    is not defined anywhere in the bundle or framework.

WARNINGS (won't break compile, but worth a look):
  - Brittle locators (CSS selectors, XPath) when semantic options exist.
  - Hard-coded credentials in the file (should be process.env).
  - TODO comments left in the generated code.
  - Test name doesn't reference TC-### from the source cases.
  - Multiple spec files in the same folder that may duplicate coverage.

INFO (nice-to-knows, don't block):
  - Suggested cleanups, style nudges.

Be SPECIFIC. Quote class/file names. Cite line numbers when you can. Empty `issues` array is acceptable when the bundle is clean."""


def _user_prompt(bundle: CodegenBundle, framework_summary: str) -> str:
    files_block = "\n".join(
        f"--- {f.path} ({f.operation}) ---\n{f.content[:6000]}" for f in bundle.files
    )
    parts = [
        "=== Generated bundle ===",
        files_block,
        "",
        "=== Framework snapshot (READ-ONLY) ===",
        framework_summary[:10000]
        if framework_summary.strip()
        else "(no source — this is a greenfield bundle; validate against generic Playwright TS conventions)",
    ]
    return "\n".join(parts)


async def validate_bundle(
    workspace_id: str,
    bundle: CodegenBundle,
    framework_summary: str = "",
) -> ValidatorReport:
    llm = await LLMClient.from_workspace(workspace_id)
    result = await llm.complete_json(
        SYSTEM,
        _user_prompt(bundle, framework_summary),
        max_tokens=3000,
        temperature=0.0,
    )
    if not result.parsed or not isinstance(result.parsed, dict):
        return ValidatorReport(
            issues=[
                ValidatorIssue(
                    severity="warning",
                    message="Validator could not parse its own output; skipping checks for this run.",
                    fix_hint="Re-run codegen, or accept the bundle as-is if it looks correct.",
                )
            ],
            summary="Validator pass skipped (parse error).",
        )
    try:
        return ValidatorReport.model_validate(result.parsed)
    except ValidationError as e:
        return ValidatorReport(
            issues=[
                ValidatorIssue(
                    severity="warning",
                    message=f"Validator output mismatched schema: {e.errors()[:2]}",
                    fix_hint=f"Raw model output (first 200 chars): {json.dumps(result.parsed)[:200]}",
                )
            ],
            summary="Validator pass mostly succeeded but the schema didn't match exactly.",
        )
