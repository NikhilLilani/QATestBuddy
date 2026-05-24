"""Minimal GitHub repo reader.

Reads a handful of files from a public Git URL to build context for the
framework relatedness check and codegen. For private repos we'll later
piggy-back on the stored GitHub OAuth token (Phase 3 work).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx
from fastapi import HTTPException, status


@dataclass(slots=True)
class RepoSnapshot:
    owner: str
    repo: str
    branch: str
    readme: str = ""
    package_json: str = ""
    playwright_config: str = ""
    sample_tests: list[tuple[str, str]] = field(default_factory=list)  # (path, content)
    error: str | None = None


_GITHUB_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$"
)


def parse_github_url(url: str) -> tuple[str, str] | None:
    m = _GITHUB_URL_RE.match(url.strip())
    if not m:
        return None
    return m.group("owner"), m.group("repo")


async def fetch_repo_snapshot(git_url: str, branch: str | None = None) -> RepoSnapshot:
    parsed = parse_github_url(git_url)
    if not parsed:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only public GitHub URLs are supported right now (e.g. https://github.com/owner/repo).",
        )
    owner, repo = parsed
    branch = branch or "main"
    snap = RepoSnapshot(owner=owner, repo=repo, branch=branch)

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        # 1. README — try several common names + the branch
        for candidate_branch in (branch, "main", "master"):
            readme = await _fetch_raw(client, owner, repo, candidate_branch, "README.md")
            if readme:
                snap.readme = readme[:6000]
                snap.branch = candidate_branch
                break

        # 2. package.json (Node) — strongest signal for Playwright/Cypress
        pkg = await _fetch_raw(client, owner, repo, snap.branch, "package.json")
        if pkg:
            snap.package_json = pkg[:6000]

        # 3. playwright config — confirms framework
        for path in ("playwright.config.ts", "playwright.config.js", "playwright.config.mjs"):
            cfg = await _fetch_raw(client, owner, repo, snap.branch, path)
            if cfg:
                snap.playwright_config = cfg[:4000]
                break

        # 4. Sample tests — first 1-2 *.spec.ts under tests/ or e2e/ via the tree API
        tree = await _list_tree(client, owner, repo, snap.branch)
        if tree:
            test_paths = [
                p for p in tree
                if (p.endswith(".spec.ts") or p.endswith(".test.ts") or p.endswith(".spec.js"))
                and ("test" in p.lower() or "e2e" in p.lower())
            ][:2]
            for p in test_paths:
                body = await _fetch_raw(client, owner, repo, snap.branch, p)
                if body:
                    snap.sample_tests.append((p, body[:4000]))

    return snap


async def _fetch_raw(client: httpx.AsyncClient, owner: str, repo: str, branch: str, path: str) -> str | None:
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    try:
        r = await client.get(url)
        if r.status_code == 200:
            return r.text
    except httpx.HTTPError:
        pass
    return None


async def _list_tree(client: httpx.AsyncClient, owner: str, repo: str, branch: str) -> list[str]:
    """Call the GitHub git/trees API (public, no auth) to list files."""
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    try:
        r = await client.get(url, headers={"Accept": "application/vnd.github.v3+json"})
        if r.status_code != 200:
            return []
        data = r.json()
        return [
            item["path"]
            for item in data.get("tree", [])
            if item.get("type") == "blob" and len(item.get("path", "")) < 200
        ][:2000]
    except httpx.HTTPError:
        return []


def snapshot_to_context(snap: RepoSnapshot, max_chars: int = 12000) -> str:
    """Render the snapshot as a single prompt-friendly string."""
    parts: list[str] = [f"Repo: github.com/{snap.owner}/{snap.repo} (branch {snap.branch})"]
    if snap.readme:
        parts.append("\n=== README.md ===\n" + snap.readme)
    if snap.package_json:
        parts.append("\n=== package.json ===\n" + snap.package_json)
    if snap.playwright_config:
        parts.append("\n=== playwright config ===\n" + snap.playwright_config)
    for path, body in snap.sample_tests:
        parts.append(f"\n=== {path} ===\n{body}")
    out = "\n".join(parts)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n…(truncated)"
    return out
