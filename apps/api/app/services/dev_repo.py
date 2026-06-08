"""Dev-repo ingestion — fetch files from GitHub or a zip, chunk, embed, store.

This is the M2 entry point. The `repos`, `repo_files`, and `repo_chunks`
tables were stubbed in 0001_init.sql and extended in 0007_dev_repos.sql.

Supported sources on day 1:
  - git_public : a public GitHub URL (no token)
  - git_pat    : private GitHub repo via a personal-access-token (encrypted)
  - zip        : user-uploaded archive, extracted in-memory

Locator extraction (HTML / JSX) lives in a sibling service `locator_extract`
and is wired in M3 — it walks the same `repo_files` rows we populate here.

Design choices to flag:
  - We index TEXT files only; binaries (images, fonts, locks) are skipped.
  - We hard-cap each file at 200 KB and the whole repo at 500 files to keep
    embedding costs and DB size bounded for the open-source plan.
  - Re-ingest is idempotent on content hash per file — touching a file with
    the same bytes is free.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import zipfile
from dataclasses import dataclass
from typing import Literal

import httpx

from app.core.crypto import decrypt, encrypt, hint
from app.db.queries import execute, fetch, fetchrow, get_pool
from app.services.embeddings import embed_texts, to_pgvector_literal
from app.services.github_repo import parse_github_url
from app.services.locator_extract import index_locators_for_file
from app.services.rag_ingest import chunk_text
from app.services.route_extract import index_routes_for_file

log = logging.getLogger("qatb.devrepo")

SourceKind = Literal["git_public", "git_pat", "zip"]

# ---------------------- file filter knobs ------------------------------------

# Extensions we treat as indexable source. Everything else is skipped.
# HTML is first because it's the easiest path to real selectors for tests.
_TEXT_EXTENSIONS = {
    ".html", ".htm",
    ".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs",
    ".vue", ".svelte",
    ".py", ".rb", ".php",
    ".java", ".kt",
    ".go", ".rs", ".cs",
    ".css", ".scss", ".sass", ".less",
    ".md", ".mdx", ".txt",
    ".json", ".yaml", ".yml", ".toml",
}

# Path fragments that mean "almost certainly not user code".
_SKIP_DIR_PARTS = {
    "node_modules", ".git", ".next", ".turbo", ".cache",
    "dist", "build", "out", "coverage", "target", "venv", ".venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "vendor", "Pods", "DerivedData",
}

# Lockfiles and generated noise that pass the extension filter but add zero value.
_SKIP_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "uv.lock", "composer.lock",
    "Gemfile.lock", "Cargo.lock",
}

_MAX_FILE_BYTES = 200_000           # 200 KB
_MAX_FILES_PER_REPO = 500
_MAX_REPO_TOTAL_BYTES = 50_000_000  # 50 MB — guardrail against accidental monorepo dumps

# Patterns that look like PATs / secrets. httpx error reprs occasionally include
# Authorization headers or URLs with embedded credentials; we redact before
# anything goes into the (workspace-readable) `last_error` column.
_SECRET_PATTERNS = [
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bghu_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bghs_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\b(?:authorization|bearer|token)\b\s*[:=]\s*\S+"),
    # `https://user:secret@host/...` form sometimes shown by httpx tracebacks.
    re.compile(r"://[^/\s]+:[^/\s@]+@"),
]


def _scrub_secrets(s: str) -> str:
    """Redact anything that looks like a PAT/credential. Used before writing
    error strings to a workspace-readable column."""
    out = s
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


@dataclass
class IngestedRepo:
    repo_id: str
    files_indexed: int
    files_skipped: int
    chunks: int
    locators: int = 0
    routes: int = 0


def _should_index(path: str, size: int) -> bool:
    """Cheap pre-fetch filter — decides whether to even download the file."""
    if size > _MAX_FILE_BYTES:
        return False
    norm = path.replace("\\", "/").lower()
    parts = norm.split("/")
    base = parts[-1] if parts else ""
    if base in {f.lower() for f in _SKIP_FILENAMES}:
        return False
    for p in parts:
        if p in _SKIP_DIR_PARTS:
            return False
    _, ext = os.path.splitext(norm)
    return ext in _TEXT_EXTENSIONS


def _lang_for(path: str) -> str | None:
    _, ext = os.path.splitext(path.lower())
    return {
        ".tsx": "tsx", ".ts": "typescript",
        ".jsx": "jsx", ".js": "javascript",
        ".vue": "vue", ".svelte": "svelte",
        ".html": "html", ".htm": "html",
        ".py": "python", ".rb": "ruby", ".php": "php",
        ".java": "java", ".kt": "kotlin",
        ".go": "go", ".rs": "rust", ".cs": "csharp",
        ".css": "css", ".scss": "scss",
        ".md": "markdown", ".mdx": "markdown",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    }.get(ext)


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------- repo CRUD --------------------------------------


async def create_repo(
    *,
    workspace_id: str,
    source_kind: SourceKind,
    git_url: str | None = None,
    branch: str | None = None,
    access_token: str | None = None,
    zip_storage_key: str | None = None,
    notes: str = "",
) -> str:
    """Create the `repos` row (does not start ingestion). Returns repo_id.

    For zip uploads we synthesise owner/name from the storage key so the unique
    constraint `(workspace, provider, owner, name)` doesn't reject identical
    zip uploads from different users.
    """
    owner: str | None = None
    name: str | None = None
    provider = "github"

    if source_kind in ("git_public", "git_pat"):
        if not git_url:
            raise ValueError("git_url required for git_public / git_pat")
        parsed = parse_github_url(git_url)
        if not parsed:
            raise ValueError(
                "Only public-form GitHub URLs are accepted: "
                "https://github.com/owner/repo"
            )
        owner, name = parsed
    else:
        provider = "zip"
        owner = "_upload"
        name = (zip_storage_key or "anon").replace("/", "_")[:80]

    # Encrypt PAT before writing. We also store a 4-char hint for the UI so
    # the user can recognise which token without decrypting.
    enc_token: bytes | None = None
    token_hint: str | None = None
    if source_kind == "git_pat":
        if not access_token:
            raise ValueError("access_token required for git_pat")
        enc_token = encrypt(access_token)
        token_hint = hint(access_token, last=4)

    row = await fetchrow(
        """
        insert into public.repos
            (workspace_id, provider, owner, name, default_branch,
             source_kind, access_token, access_token_hint, zip_storage_key, notes)
        values ($1::uuid, $2, $3, $4, $5,
                $6::public.repo_source_kind, $7, $8, $9, $10)
        on conflict (workspace_id, provider, owner, name) do update
          set default_branch = excluded.default_branch,
              source_kind    = excluded.source_kind,
              access_token   = coalesce(excluded.access_token, public.repos.access_token),
              access_token_hint = coalesce(excluded.access_token_hint, public.repos.access_token_hint),
              zip_storage_key= excluded.zip_storage_key,
              notes          = excluded.notes
        returning id
        """,
        workspace_id, provider, owner, name, branch,
        source_kind, enc_token, token_hint, zip_storage_key, notes,
    )
    if not row:
        raise RuntimeError("Failed to upsert repo row")
    return str(row["id"])


async def list_repos(workspace_id: str) -> list[dict]:
    return await fetch(
        """
        select id, provider, owner, name, default_branch,
               source_kind::text as source_kind, access_token_hint,
               zip_storage_key, notes, last_indexed_at, file_count, last_error,
               created_at
          from public.repos
         where workspace_id = $1::uuid
         order by created_at desc
        """,
        workspace_id,
    )


async def delete_repo(workspace_id: str, repo_id: str) -> None:
    await execute(
        "delete from public.repos where id = $1::uuid and workspace_id = $2::uuid",
        repo_id, workspace_id,
    )


# ---------------------------- ingestion --------------------------------------


async def ingest_repo(workspace_id: str, repo_id: str) -> IngestedRepo:
    """Fetch and (re-)index every text file in the repo. Idempotent per file."""
    repo = await fetchrow(
        """
        select id, source_kind::text as source_kind, owner, name, default_branch,
               access_token, zip_storage_key
          from public.repos
         where id = $1::uuid and workspace_id = $2::uuid
        """,
        repo_id, workspace_id,
    )
    if not repo:
        raise ValueError(f"repo {repo_id} not found in workspace")

    try:
        if repo["source_kind"] in ("git_public", "git_pat"):
            files = await _fetch_github_files(
                owner=repo["owner"],
                name=repo["name"],
                branch=repo["default_branch"],
                pat=(decrypt(repo["access_token"]) if repo["access_token"] else None),
            )
        elif repo["source_kind"] == "zip":
            files = _fetch_zip_files(repo["zip_storage_key"])
        else:  # pragma: no cover — enum keeps this unreachable
            raise ValueError(f"unsupported source_kind {repo['source_kind']}")
    except Exception as e:  # noqa: BLE001
        log.exception("dev-repo fetch failed")
        # Scrub PATs from the error message before persisting — last_error is
        # readable by anyone in the workspace via list_repos.
        safe_msg = _scrub_secrets(f"fetch failed: {type(e).__name__}: {e}")[:500]
        await execute(
            "update public.repos set last_error = $1 where id = $2::uuid",
            safe_msg, repo_id,
        )
        raise

    indexed = 0
    skipped = 0
    total_chunks = 0
    total_bytes = 0
    file_payloads: list[tuple[str, str, str]] = []  # (file_id, path, content) for post-tx extraction

    # NOTE on stale-artifact wiping:
    # locator_index rows reference repo_files.id ON DELETE CASCADE, so the
    # `delete from repo_files` below atomically clears them along with the
    # chunks. dev_repo_routes references repo_id (not repo_files) so we wipe
    # those explicitly inside the same transaction below — keeps everything
    # consistent if the transaction rolls back.
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # Wipe stale chunks + routes for files we're about to re-ingest.
        # locator_index rows cascade automatically when repo_files is deleted
        # (FK ON DELETE CASCADE). Routes reference repo_id directly, so do
        # them here inside the same transaction to keep consistency on rollback.
        await conn.execute(
            "delete from public.repo_chunks "
            "where repo_file_id in (select id from public.repo_files where repo_id = $1::uuid)",
            repo_id,
        )
        await conn.execute(
            "delete from public.dev_repo_routes where repo_id = $1::uuid",
            repo_id,
        )
        await conn.execute(
            "delete from public.repo_files where repo_id = $1::uuid",
            repo_id,
        )

        for path, content in files:
            # _MAX_FILES_PER_REPO cap — the github fetcher already trims to
            # this, but the zip path doesn't, so enforce again here.
            if indexed >= _MAX_FILES_PER_REPO:
                skipped += 1
                continue
            if total_bytes + len(content) > _MAX_REPO_TOTAL_BYTES:
                skipped += 1
                continue

            content_hash = _sha256(content.encode("utf-8", errors="ignore"))
            file_row = await conn.fetchrow(
                """
                insert into public.repo_files
                    (workspace_id, repo_id, path, sha, lang, bytes,
                     indexed, content_hash)
                values ($1::uuid, $2::uuid, $3, $4, $5, $6, true, $7)
                returning id
                """,
                workspace_id, repo_id, path, content_hash[:40],
                _lang_for(path), len(content), content_hash,
            )
            file_id = str(file_row["id"])

            chunks = chunk_text(content)
            if not chunks:
                # File was binary-ish or otherwise unreadable post-decode.
                continue
            vectors = await embed_texts(chunks)
            for ord_, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True)):
                await conn.execute(
                    """
                    insert into public.repo_chunks
                        (workspace_id, repo_file_id, ord, content, embedding)
                    values ($1::uuid, $2::uuid, $3, $4, $5::vector)
                    """,
                    workspace_id, file_id, ord_, chunk,
                    to_pgvector_literal(vec),
                )
            indexed += 1
            total_chunks += len(chunks)
            total_bytes += len(content)
            # Stash for the post-commit locator/route pass. We keep the full
            # content in memory; alternative is to re-fetch from `repo_files`
            # but that's another round-trip per file.
            file_payloads.append((file_id, path, content))

        await conn.execute(
            """
            update public.repos
               set last_indexed_at = now(),
                   file_count      = $1,
                   last_error      = null
             where id = $2::uuid
            """,
            indexed, repo_id,
        )

    # ----- M3: locator + route extraction (post-commit so repo_files exist) -----
    # We swallow failures here so a parser hiccup never invalidates the
    # successfully-stored chunks. Indexing can be retried; extraction can be
    # re-run by clicking "re-index" again.
    locators_total = 0
    routes_total = 0
    for file_id, path, content in file_payloads:
        try:
            locators_total += await index_locators_for_file(
                workspace_id=workspace_id,
                repo_file_id=file_id,
                path=path,
                content=content,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("locator extract failed for %s: %s", path, e)
        try:
            routes_total += await index_routes_for_file(
                workspace_id=workspace_id,
                repo_id=repo_id,
                path=path,
                content=content,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("route extract failed for %s: %s", path, e)

    return IngestedRepo(
        repo_id=repo_id, files_indexed=indexed,
        files_skipped=skipped, chunks=total_chunks,
        locators=locators_total, routes=routes_total,
    )


# ---------------------------- source fetchers --------------------------------


async def _fetch_github_files(
    *, owner: str, name: str, branch: str | None, pat: str | None
) -> list[tuple[str, str]]:
    """Walk a GitHub repo's git tree and download text files.

    For private repos we use the GitHub REST API exclusively (PAT supports
    Authorization header). For public repos we use the API for the tree and
    raw.githubusercontent.com for file contents — faster and rate-limit-free.
    """
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    if pat:
        headers["Authorization"] = f"Bearer {pat}"

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        # 1. Resolve default branch if the user didn't specify one.
        effective_branch = branch
        if not effective_branch:
            r = await client.get(
                f"https://api.github.com/repos/{owner}/{name}", headers=headers,
            )
            if r.status_code != 200:
                raise RuntimeError(
                    f"GitHub repo lookup failed ({r.status_code}). "
                    "Check the URL, branch, and (for private repos) PAT scopes."
                )
            effective_branch = r.json().get("default_branch") or "main"

        # 2. List the tree.
        tree_url = (
            f"https://api.github.com/repos/{owner}/{name}"
            f"/git/trees/{effective_branch}?recursive=1"
        )
        r = await client.get(tree_url, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(
                f"GitHub tree listing failed ({r.status_code}). "
                "Branch may not exist."
            )
        tree = r.json().get("tree", [])

        # 3. Pre-filter so we don't blow rate limit on irrelevant files.
        eligible = [
            item for item in tree
            if item.get("type") == "blob"
            and _should_index(item.get("path", ""), int(item.get("size") or 0))
        ][:_MAX_FILES_PER_REPO]

        # 4. Download bodies. Public + no token → raw.githubusercontent.com is
        #    fastest. Private → use the contents API with Authorization.
        # We aggregate fetch failures by status code so users can see e.g.
        # "147 files skipped: 122x 404, 25x 403 — PAT may lack `repo` scope"
        # rather than a useless "0 files indexed".
        out: list[tuple[str, str]] = []
        fail_counts: dict[str, int] = {}
        for item in eligible:
            path = item["path"]
            content, fail_reason = await _download_one(
                client, owner, name, effective_branch, path, pat=pat,
            )
            if content is None:
                if fail_reason:
                    fail_counts[fail_reason] = fail_counts.get(fail_reason, 0) + 1
                continue
            out.append((path, content))
        if fail_counts:
            summary = ", ".join(f"{n}x {reason}" for reason, n in sorted(fail_counts.items()))
            log.warning(
                "dev-repo %s/%s: %d files fetched, %d skipped (%s). "
                "If you used a PAT, check it has the `repo` scope; if public, "
                "the file may exceed GitHub's 1 MB raw limit.",
                owner, name, len(out), sum(fail_counts.values()), summary,
            )
        return out


async def _download_one(
    client: httpx.AsyncClient,
    owner: str,
    name: str,
    branch: str,
    path: str,
    *,
    pat: str | None,
) -> tuple[str | None, str | None]:
    """Fetch one file's text content. Returns (content, fail_reason).
    On success, fail_reason is None. On failure, content is None and
    fail_reason is a short tag like '404', '403', '413', 'network'."""
    headers: dict[str, str] = {}
    if pat:
        headers["Authorization"] = f"Bearer {pat}"

    last_status: int | None = None

    # Public-fast path.
    if not pat:
        raw_url = f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/{path}"
        try:
            r = await client.get(raw_url, headers=headers)
            if r.status_code == 200 and r.text:
                return r.text, None
            last_status = r.status_code
        except httpx.HTTPError:
            return None, "network"

    # Private or raw failed → contents API (returns base64).
    api_url = (
        f"https://api.github.com/repos/{owner}/{name}/contents/{path}"
        f"?ref={branch}"
    )
    try:
        r = await client.get(
            api_url, headers={**headers, "Accept": "application/vnd.github.v3.raw"},
        )
        if r.status_code == 200:
            return r.text, None
        last_status = r.status_code
    except httpx.HTTPError:
        return None, "network"
    return None, str(last_status) if last_status is not None else "unknown"


def _fetch_zip_files(zip_storage_key: str | None) -> list[tuple[str, str]]:
    """Read text files out of an uploaded zip. `zip_storage_key` is the path
    on disk where the router saved the upload (local FS for now; swap to S3
    by changing this single function later)."""
    if not zip_storage_key or not os.path.exists(zip_storage_key):
        raise FileNotFoundError(f"zip not found at {zip_storage_key!r}")
    out: list[tuple[str, str]] = []
    with zipfile.ZipFile(zip_storage_key, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # Strip the top-level folder GitHub zips include
            # ("owner-repo-sha1/..." → "...") so paths look natural.
            rel = re.sub(r"^[^/]+/", "", info.filename)
            # Reject path-traversal in the archive entries. We never write
            # files to disk, but `rel` is persisted in repo_files.path and
            # echoed back in citation links / the locator UI; a malicious
            # zip with "../etc/passwd" entries would otherwise show up as
            # confusing citations and could poison logs.
            norm = rel.replace("\\", "/")
            if (
                norm.startswith("/")
                or any(seg in ("..",) for seg in norm.split("/"))
            ):
                continue
            rel = norm
            if not _should_index(rel, info.file_size):
                continue
            try:
                with zf.open(info) as f:
                    raw = f.read()
                text = raw.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — skip files we can't decode
                continue
            out.append((rel, text))
    return out
