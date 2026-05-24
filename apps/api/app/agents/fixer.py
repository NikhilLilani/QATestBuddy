"""FixerAgent — applies patches to a CodegenBundle based on validator issues.

Phase 4f. The validator finds problems; this agent rewrites the affected
files to fix them. Output is the same `CodegenBundle` shape — a list of
patched files. Files the fixer didn't touch are returned unchanged so the
caller can do a clean replacement.

Strategy:
  - Feed the LLM ONLY the files that have validator issues (saves tokens).
  - Bundle the issues into a compact list per file.
  - Demand a strict JSON response with the patched file contents.
  - Merge the patches back into the original bundle.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agents.codegen import CodegenBundle, GeneratedFile
from app.agents.validator import ValidatorIssue, ValidatorReport
from app.services.llm import LLMClient

log = logging.getLogger("qatb.fixer")


class FixerPatch(BaseModel):
    """One patched file. `path` must match an existing bundle file."""

    path: str = Field(min_length=1)
    operation: Literal["create", "update", "merge"] = "create"
    content: str = Field(min_length=1)
    summary: str = ""
    reason: str = ""

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, v: Any) -> str:
        return str(v).replace("\\", "/").lstrip("/")


class FixerResult(BaseModel):
    patches: list[FixerPatch] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(
        default_factory=list,
        description="Issues the fixer chose not to patch (with reason).",
    )


SYSTEM = """You are a senior Playwright + TypeScript engineer fixing bugs in a freshly-generated test bundle.

You receive:
1. A list of files from the bundle that have problems.
2. A list of STRUCTURED issues (each has file + line + severity + message + fix_hint).
3. A framework snapshot for grounding (so you know what imports / classes / fixtures actually exist).

Your job: produce patched versions of the files so the issues go away.

HARD RULES:
- Output ONLY this JSON shape — no prose, no markdown, no code fences:
{
  "patches": [
    {
      "path": "src/pages/appA/AppALoginPage.ts",
      "operation": "create" | "update" | "merge",
      "content": "<the FULL new file content — not a diff>",
      "summary": "what changed in 1 sentence",
      "reason": "which validator issue(s) this fixes"
    }
  ],
  "notes": ["short notes for the human"],
  "unresolved": ["issue X — why I left it: ..."]
}

- For each file you patch, return its ENTIRE new content, not a diff or a patch hunk.
- KEEP the original `operation` field (create/update/merge) — don't downgrade an `update` to a `create`.
- Only include files you actually changed. Don't echo unchanged files back.
- Address EVERY validator error. Warnings: address when easy, skip with a note in `unresolved` when they're stylistic.
- Don't introduce new dependencies, new imports from packages not in the framework snapshot, or new top-level files unless explicitly needed to resolve an issue.
- Preserve TC-### numbering and existing test titles where they're already correct.
- Fix imports by checking the framework snapshot for actual exported names. If the snapshot shows `export class AppALoginModule` in `src/modules/appA/AppALoginModule.ts`, the correct import is `import { AppALoginModule } from '../../modules/appA/AppALoginModule'` (relative path inferred from the file's own path).

COMMON FIXES YOU'LL APPLY:
- Truncated files → complete the missing braces / closing `});` / final test block.
- BOM at start of file → strip the U+FEFF.
- Wrong import path → match the framework's actual file casing and folder structure.
- Hardcoded credentials → replace with `process.env.X` and add a note to set X in `.env`.
- Invalid regex → escape the special chars or simplify.
- Missing assertions → add at least one `expect(...)` per `test()`.
- References to undefined fixtures → either drop the fixture or import it from the framework's barrel.

If an issue is too ambiguous to fix mechanically (e.g. "this locator may be brittle"), list it in `unresolved` with a short reason. Don't make up code."""


def _user_prompt(
    bundle: CodegenBundle,
    report: ValidatorReport,
    framework_snapshot: str,
    iteration: int,
) -> str:
    # Pick only files that have issues — saves tokens drastically.
    files_with_issues = {iss.file for iss in report.issues if iss.file}
    target_files = [f for f in bundle.files if f.path in files_with_issues]
    # If the validator emitted a global / file-less issue, include the whole bundle
    # so the model has full context.
    if not target_files or any(not iss.file for iss in report.issues):
        target_files = list(bundle.files)

    files_block = "\n".join(
        f"--- {f.path} ({f.operation}) ---\n{f.content[:8000]}" for f in target_files
    )

    issues_block = json.dumps(
        [iss.model_dump(exclude_none=True) for iss in report.issues],
        indent=2,
    )

    parts = [
        f"=== Fix iteration {iteration} ===",
        "",
        "=== Validator summary ===",
        report.summary or "(no summary)",
        "",
        "=== Validator issues ===",
        issues_block,
        "",
        "=== Files to patch ===",
        files_block,
        "",
        "=== Framework snapshot (READ-ONLY — use to verify imports / class names) ===",
        framework_snapshot[:8000]
        if framework_snapshot.strip()
        else "(no framework — this is a greenfield bundle)",
    ]
    return "\n".join(parts)


async def fix_bundle(
    workspace_id: str,
    bundle: CodegenBundle,
    report: ValidatorReport,
    framework_snapshot: str = "",
    iteration: int = 1,
) -> tuple[CodegenBundle, FixerResult]:
    """Apply patches from the LLM and return (new_bundle, fixer_result).

    The bundle returned is the original bundle with patched files swapped in.
    Files the fixer didn't touch are preserved verbatim.
    """
    llm = await LLMClient.from_workspace(workspace_id)
    result = await llm.complete_json(
        SYSTEM,
        _user_prompt(bundle, report, framework_snapshot, iteration),
        max_tokens=16000,
        temperature=0.0,
    )

    if not result.parsed or not isinstance(result.parsed, dict):
        log.warning("fixer: could not parse LLM output, returning original bundle")
        return bundle, FixerResult(
            notes=["Fixer LLM call returned unparseable output — bundle unchanged."]
        )

    try:
        fixer = FixerResult.model_validate(result.parsed)
    except ValidationError as e:
        log.warning("fixer: schema mismatch: %s", e.errors()[:2])
        return bundle, FixerResult(
            notes=[
                f"Fixer schema mismatch: {str(e.errors()[:1])[:200]}. Bundle unchanged.",
            ]
        )

    if not fixer.patches:
        log.info("fixer: LLM returned 0 patches (iteration=%d)", iteration)
        return bundle, fixer

    # Merge patches back into the bundle.
    patch_by_path = {p.path: p for p in fixer.patches}
    new_files: list[GeneratedFile] = []
    patched_paths: list[str] = []
    for f in bundle.files:
        patch = patch_by_path.pop(f.path, None)
        if patch is None:
            new_files.append(f)
            continue
        # Keep the original operation if the patch tries to downgrade it.
        op = f.operation if f.operation in ("update", "merge") and patch.operation == "create" else patch.operation
        new_files.append(
            GeneratedFile(
                path=patch.path,
                operation=op,  # type: ignore[arg-type]
                content=patch.content,
                summary=patch.summary or f.summary,
                reason=patch.reason or f.reason,
            )
        )
        patched_paths.append(patch.path)

    # Any patches with paths not in the original bundle = LLM trying to add new files.
    # We allow this but log a warning.
    for path, patch in patch_by_path.items():
        log.info("fixer: adding new file %s (not in original bundle)", path)
        new_files.append(
            GeneratedFile(
                path=patch.path,
                operation=patch.operation,
                content=patch.content,
                summary=patch.summary,
                reason=patch.reason,
            )
        )
        patched_paths.append(patch.path)

    new_bundle = CodegenBundle(
        files=new_files,
        install_commands=bundle.install_commands,
        run_commands=bundle.run_commands,
        notes=bundle.notes,
    )
    log.info(
        "fixer: patched %d files (iteration=%d) — %s",
        len(patched_paths),
        iteration,
        ", ".join(patched_paths),
    )
    return new_bundle, fixer
