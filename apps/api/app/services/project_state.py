"""Detect the project state for codegen branching (M5).

Returns one of four states:

  new_project       — no existing framework, no existing dev repo. Codegen
                      emits a complete project skeleton.
  existing_no_tests — dev repo exists but has no test framework yet. Codegen
                      adds Playwright into the project, merges config files,
                      doesn't recreate anything that already exists.
  existing_same_fw  — there's a Playwright framework that matches the user's
                      selection. Codegen adds/edits inside the existing
                      structure (operation: update/merge).
  existing_diff_fw  — there's a framework but it's a different one (Cypress,
                      Selenium, etc.). Codegen translates into Playwright in
                      a parallel folder, optional MIGRATION.md.

The detection runs three cheap signals:
  - Framework row presence  (frameworks table)
  - Dev repo's indexed files (repo_files: looking for known framework markers)
  - Dev repo's language (repos.default_branch ingest stored a per-file lang)

Heuristics are conservative: if uncertain, fall back to the most permissive
state. The UI lets the user override the auto-classification anyway.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.db.queries import fetch, fetchrow

log = logging.getLogger("qatb.project_state")

ProjectState = Literal["new_project", "existing_no_tests", "existing_same_fw", "existing_diff_fw"]


@dataclass
class StateVerdict:
    state: ProjectState
    detected_framework: str | None  # "playwright" | "cypress" | "selenium" | "wdio" | None
    evidence: list[str]
    confidence: float  # 0.0-1.0


# Filename signatures we use to detect frameworks inside the dev repo. Each
# entry: (file_glob_like, framework_name). We match by suffix or basename
# so a deep monorepo folder doesn't fool us.
_FRAMEWORK_MARKERS: list[tuple[str, str]] = [
    ("playwright.config.ts", "playwright"),
    ("playwright.config.js", "playwright"),
    ("playwright.config.mjs", "playwright"),
    ("cypress.config.ts", "cypress"),
    ("cypress.config.js", "cypress"),
    ("cypress.json", "cypress"),
    ("wdio.conf.ts", "wdio"),
    ("wdio.conf.js", "wdio"),
    ("nightwatch.conf.js", "nightwatch"),
    ("nightwatch.json", "nightwatch"),
    # Selenium has no canonical config file; we look for pom.xml + selenium
    # dep in M5 follow-up. For now we miss Maven Selenium projects, which
    # the user can override manually.
]

# A repo with these dirs is "a project" even when no framework is detected —
# distinguishes existing_no_tests from new_project.
_PROJECT_MARKERS = (
    "package.json", "tsconfig.json", "pyproject.toml", "Cargo.toml",
    "pom.xml", "build.gradle", "go.mod",
)


async def _scan_repo_files(workspace_id: str, repo_id: str) -> dict:
    """Return {markers: set, frameworks: set} found in this repo's indexed files."""
    rows = await fetch(
        """
        select path from public.repo_files
         where workspace_id = $1::uuid and repo_id = $2::uuid
         limit 5000
        """,
        workspace_id, repo_id,
    )
    markers: set[str] = set()
    frameworks: set[str] = set()
    for r in rows:
        base = r["path"].rsplit("/", 1)[-1].lower()
        for marker, name in _FRAMEWORK_MARKERS:
            if base == marker:
                frameworks.add(name)
        if base in _PROJECT_MARKERS:
            markers.add(base)
    return {"markers": markers, "frameworks": frameworks, "file_count": len(rows)}


async def detect_state(
    *,
    workspace_id: str,
    framework_id: str | None = None,
    dev_repo_id: str | None = None,
) -> StateVerdict:
    """Run the detection heuristics. Either or both inputs may be None."""
    evidence: list[str] = []

    selected_framework_lang: str | None = None
    if framework_id:
        row = await fetchrow(
            """
            select language::text, source_kind::text
              from public.frameworks
             where id = $1 and workspace_id = $2
            """,
            framework_id, workspace_id,
        )
        if row:
            selected_framework_lang = row.get("language") or None
            evidence.append(
                f"User selected framework (lang={selected_framework_lang}, "
                f"source={row.get('source_kind')})."
            )

    repo_scan: dict | None = None
    if dev_repo_id:
        repo_scan = await _scan_repo_files(workspace_id, dev_repo_id)
        evidence.append(
            f"Dev repo has {repo_scan['file_count']} indexed files; "
            f"project markers: {sorted(repo_scan['markers']) or 'none'}; "
            f"framework markers: {sorted(repo_scan['frameworks']) or 'none'}."
        )

    # ---------------- decision tree ----------------

    # Case 1: nothing to learn from. Fresh slate.
    if repo_scan is None and selected_framework_lang is None:
        evidence.append("No framework or dev repo provided — treating as new project.")
        return StateVerdict(
            state="new_project",
            detected_framework=None,
            evidence=evidence,
            confidence=0.95,
        )

    # Case 2: framework is selected as "new from scratch" and no dev repo.
    # The framework row tells us what language we'll write in but there's
    # no existing project to merge into.
    if repo_scan is None and selected_framework_lang:
        evidence.append("Framework selected but no dev repo — treating as new project skeleton.")
        return StateVerdict(
            state="new_project",
            detected_framework=None,
            evidence=evidence,
            confidence=0.8,
        )

    # From here, repo_scan is non-None.
    detected_framework: str | None = None
    if repo_scan["frameworks"]:
        # If multiple are present (rare), prefer Playwright if it's one of
        # them, otherwise pick alphabetical for determinism.
        if "playwright" in repo_scan["frameworks"]:
            detected_framework = "playwright"
        else:
            detected_framework = sorted(repo_scan["frameworks"])[0]
        evidence.append(f"Detected framework in dev repo: {detected_framework}.")

    has_project = bool(repo_scan["markers"])

    # Case 3: project markers present but no framework detected.
    if has_project and detected_framework is None:
        evidence.append("Project files present but no test framework found — adding first automation.")
        return StateVerdict(
            state="existing_no_tests",
            detected_framework=None,
            evidence=evidence,
            confidence=0.85,
        )

    # Case 4: framework detected. Same as selection or different?
    if detected_framework:
        # We treat language=playwright_ts as Playwright family. Other languages
        # don't yet have a 1:1 detection — be conservative and assume "same"
        # only when both sides explicitly say Playwright.
        user_chose_playwright = selected_framework_lang in (None, "playwright_ts")
        if detected_framework == "playwright" and user_chose_playwright:
            evidence.append("Dev repo and user selection both indicate Playwright.")
            return StateVerdict(
                state="existing_same_fw",
                detected_framework="playwright",
                evidence=evidence,
                confidence=0.9,
            )
        evidence.append(
            f"Dev repo has {detected_framework}; user picked {selected_framework_lang or 'playwright'} "
            "— migration scenario."
        )
        return StateVerdict(
            state="existing_diff_fw",
            detected_framework=detected_framework,
            evidence=evidence,
            confidence=0.85,
        )

    # Fall-through: empty repo or unrecognised layout. Safest: new_project
    # so codegen emits a full skeleton the user can prune.
    evidence.append("Repo provided but neither project nor framework markers found.")
    return StateVerdict(
        state="new_project",
        detected_framework=None,
        evidence=evidence,
        confidence=0.5,
    )
