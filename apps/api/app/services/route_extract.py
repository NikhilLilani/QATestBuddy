"""Extract real route URLs from the dev repo so codegen never invents `/login`.

Frameworks supported on day 1, in order of how reliably we can detect them:

  - next-app    : Next.js app router. Every `app/**/page.{tsx,jsx,ts,js}` file
                  defines a route; the URL is the directory path. Bracketed
                  segments (`[id]`) are dynamic params.
  - next-pages  : Next.js pages router. Files under `pages/**` are routes,
                  with the same brackets-for-params convention.
  - react-router: Look for `<Route path="…">` JSX inside any file. Misses
                  routes built dynamically from arrays, but those are rare
                  in user-facing UI.
  - vue-router  : `path: '…'` inside a `routes:` array in a router file.
  - html        : Static sites — every `*.html` file at the repo root or
                  under `public/` / `static/` is a route by filename.

Output is one row per route in `dev_repo_routes`. Population happens inside
`dev_repo.ingest_repo()` after locator extraction.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.db.queries import execute, fetch, get_pool

log = logging.getLogger("qatb.route")


@dataclass
class RouteRecord:
    path_pattern: str
    source_file: str
    line: int | None
    framework: str


# ----------------------------- Next.js app router ----------------------------

_NEXT_APP_PAGE_RE = re.compile(r"(?:^|/)app(/.*?)/page\.(?:tsx|ts|jsx|js|mjs)$", re.IGNORECASE)
_NEXT_PAGES_RE = re.compile(r"(?:^|/)pages(/[^.]+)\.(?:tsx|ts|jsx|js|mjs)$", re.IGNORECASE)


def _next_app_path(file_path: str) -> str | None:
    m = _NEXT_APP_PAGE_RE.search(file_path.replace("\\", "/"))
    if not m:
        return None
    p = m.group(1) or "/"
    # Strip route-group folders: `(marketing)`, `(auth)` — they don't affect URL.
    p = re.sub(r"/\([^)]+\)", "", p)
    # `page.tsx` at the root → "/", not "" or "/page".
    return p or "/"


def _next_pages_path(file_path: str) -> str | None:
    m = _NEXT_PAGES_RE.search(file_path.replace("\\", "/"))
    if not m:
        return None
    p = m.group(1)
    # `_app`, `_document`, `_error`, `api/*` are framework, not routes.
    base = p.rsplit("/", 1)[-1]
    if base.startswith("_"):
        return None
    if p.startswith("/api/"):
        return None
    # `index` → directory root.
    if p.endswith("/index"):
        p = p[: -len("/index")] or "/"
    return p or "/"


# ----------------------------- React Router ---------------------------------

# Matches `<Route path="..." …>` in any JSX/TSX file. Lazy match on the path
# to avoid eating the next attribute.
_REACT_ROUTE_RE = re.compile(r"""<Route\s+[^>]*?path\s*=\s*["'](?P<path>[^"']+)["']""")


def _extract_react_routes(file_path: str, content: str) -> list[RouteRecord]:
    out: list[RouteRecord] = []
    for m in _REACT_ROUTE_RE.finditer(content):
        line = content.count("\n", 0, m.start()) + 1
        out.append(RouteRecord(
            path_pattern=m.group("path"),
            source_file=file_path,
            line=line,
            framework="react-router",
        ))
    return out


# ----------------------------- Vue Router -----------------------------------

# Inside a `routes:` array of objects with `path: '…'`. We don't try to
# resolve nested children — flatten in a follow-up if real users need it.
_VUE_ROUTE_RE = re.compile(r"""path\s*:\s*["'](?P<path>/[^"']*)["']""")


def _extract_vue_routes(file_path: str, content: str) -> list[RouteRecord]:
    # Heuristic gate: only look in files that mention `createRouter` or
    # `VueRouter` so we don't grab `path:` lines from random configs.
    if "createRouter" not in content and "VueRouter" not in content:
        return []
    out: list[RouteRecord] = []
    for m in _VUE_ROUTE_RE.finditer(content):
        line = content.count("\n", 0, m.start()) + 1
        out.append(RouteRecord(
            path_pattern=m.group("path"),
            source_file=file_path,
            line=line,
            framework="vue-router",
        ))
    return out


# ----------------------------- Static HTML ----------------------------------

_STATIC_HTML_RE = re.compile(r"(?:^|/)(?:public|static)?/?([^/]+)\.html?$", re.IGNORECASE)


def _static_html_path(file_path: str) -> str | None:
    norm = file_path.replace("\\", "/")
    if not norm.endswith((".html", ".htm")):
        return None
    # Strip a leading public/static prefix.
    p = re.sub(r"^(?:public|static|dist|out)/", "/", norm)
    if not p.startswith("/"):
        p = "/" + p
    # index.html → directory root.
    p = p.rsplit("/", 1)
    name, ext = p[1].rsplit(".", 1)
    parent = p[0] or ""
    if name.lower() == "index":
        return parent or "/"
    return f"{parent}/{name}" if parent else f"/{name}"


# ============================================================================
# Public API — called per-file at ingest time
# ============================================================================


def extract_routes(file_path: str, content: str) -> list[RouteRecord]:
    """Try every framework heuristic, return the union. False positives are
    cheaper than misses — the codegen agent ranks by relevance later."""
    out: list[RouteRecord] = []

    # File-path-based detectors (no content read needed beyond the path).
    p = _next_app_path(file_path)
    if p:
        out.append(RouteRecord(path_pattern=p, source_file=file_path, line=None,
                               framework="next-app"))
    p = _next_pages_path(file_path)
    if p:
        out.append(RouteRecord(path_pattern=p, source_file=file_path, line=None,
                               framework="next-pages"))
    p = _static_html_path(file_path)
    if p:
        out.append(RouteRecord(path_pattern=p, source_file=file_path, line=None,
                               framework="html"))

    # Content-based detectors.
    if file_path.lower().endswith((".tsx", ".jsx", ".ts", ".js")):
        out.extend(_extract_react_routes(file_path, content))
        out.extend(_extract_vue_routes(file_path, content))

    # De-dup by (path_pattern, source_file) — Next + React Router occasionally
    # both fire on the same file (rare but seen in hybrid apps).
    seen: set[tuple[str, str]] = set()
    unique: list[RouteRecord] = []
    for r in out:
        key = (r.path_pattern, r.source_file)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique


# ----------------------------- persistence ----------------------------------


async def index_routes_for_file(
    *,
    workspace_id: str,
    repo_id: str,
    path: str,
    content: str,
) -> int:
    records = extract_routes(path, content)
    if not records:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        rows = [
            (workspace_id, repo_id, r.path_pattern, r.source_file, r.line, r.framework)
            for r in records
        ]
        await conn.executemany(
            """
            insert into public.dev_repo_routes
                (workspace_id, repo_id, path_pattern, source_file, line, framework)
            values ($1::uuid, $2::uuid, $3, $4, $5, $6)
            """,
            rows,
        )
    return len(records)


async def delete_routes_for_repo(workspace_id: str, repo_id: str) -> None:
    await execute(
        "delete from public.dev_repo_routes "
        "where workspace_id = $1::uuid and repo_id = $2::uuid",
        workspace_id, repo_id,
    )


# ----------------------------- search ---------------------------------------


@dataclass
class RouteMatch:
    path_pattern: str
    source_file: str
    line: int | None
    framework: str


async def find_route(*, workspace_id: str, intent: str, k: int = 6) -> list[RouteMatch]:
    """Naive route lookup — ILIKE over path_pattern. Routes are sparse so
    we don't need BM25 here; if a repo has 200 routes we can revisit."""
    q = (intent or "").strip().lower()
    if not q:
        return []
    rows = await fetch(
        """
        select path_pattern, source_file, line, framework
          from public.dev_repo_routes
         where workspace_id = $1::uuid
           and (lower(path_pattern) like '%' || $2 || '%'
                or lower(source_file) like '%' || $2 || '%')
         order by length(path_pattern) asc
         limit $3
        """,
        workspace_id, q, max(1, min(k, 50)),
    )
    return [
        RouteMatch(
            path_pattern=r["path_pattern"],
            source_file=r["source_file"],
            line=r.get("line"),
            framework=r["framework"],
        )
        for r in rows
    ]
