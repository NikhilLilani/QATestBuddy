"""Pull stable test selectors out of dev-repo source files.

The goal: when the codegen agent wants to write `page.getByTestId('login-btn')`,
the string 'login-btn' must come from a row in `locator_index` — i.e. it
actually exists in the user's app. If it doesn't, the agent must ask a
clarify question instead of inventing.

Coverage strategy:
  - HTML / Vue templates / Svelte templates → `html.parser` (stdlib, robust).
  - JSX / TSX → regex pass. We don't transpile; we pattern-match attributes.
    Misses conditional rendering and dynamic attribute names, which is
    fine — those would be unstable selectors anyway.

The extractor is intentionally permissive: false positives are cheap (the
agent ranks by relevance), false negatives mean made-up selectors. We err
toward more rows.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterable

from app.db.queries import execute, fetch, get_pool

log = logging.getLogger("qatb.locator")


# Attributes we treat as stable test anchors, in roughly descending order of
# preference (the codegen agent uses this order when picking among matches).
_ATTR_PRIORITY = (
    "data-testid", "data-test", "data-cy", "data-qa",
    "data-automation-id", "data-test-id",
    "id", "name",
    "aria-label", "aria-labelledby",
    "role",
    "placeholder",
    "href",
    "for",
)

# Map every supported attribute to a normalised selector_kind for the DB.
_KIND_BY_ATTR = {
    "data-testid": "testid", "data-test": "testid",
    "data-cy": "testid", "data-qa": "testid",
    "data-automation-id": "testid", "data-test-id": "testid",
    "id": "id", "name": "name",
    "aria-label": "aria", "aria-labelledby": "aria", "role": "role",
    "placeholder": "placeholder", "href": "href", "for": "for",
}

# Tags we consider "interactive enough" that their visible text counts as a
# locator candidate (Playwright's getByRole + name strategy).
_TEXT_BEARING_TAGS = {
    "button", "a", "label", "summary",
    "h1", "h2", "h3", "h4", "h5", "h6",
    # We also include input/textarea for their placeholder, which is captured
    # as an attribute above — not via visible text.
}


@dataclass
class LocatorRecord:
    line: int
    selector_kind: str
    selector_value: str
    component: str | None
    label: str | None
    context: dict[str, str]


# ============================================================================
# HTML parser (also handles Vue <template> and Svelte template blocks)
# ============================================================================


class _LocatorHTMLParser(HTMLParser):
    """Walks raw HTML / template text and emits one LocatorRecord per locator
    anchor found. We accumulate visible text per open tag so the closing-tag
    handler can emit a `text` locator for buttons/links/etc."""

    def __init__(self) -> None:
        # html.parser doesn't recognise some custom attrs cleanly under strict
        # mode; convert_charrefs=True so we get plain text for visible labels.
        super().__init__(convert_charrefs=True)
        self.records: list[LocatorRecord] = []
        # Stack of (tag, line, attrs_dict, text_buf) for currently-open tags.
        self._stack: list[tuple[str, int, dict[str, str], list[str]]] = []

    # ---- helpers ----

    def _current_line(self) -> int:
        # html.parser uses 1-based positions in `getpos()`.
        return self.getpos()[0]

    def _emit_attributes(self, tag: str, line: int, attrs: dict[str, str]) -> None:
        # Build the per-tag context once so we don't repeat all attrs on
        # every emitted record.
        context = {k: v for k, v in attrs.items() if v is not None and len(v) <= 200}
        # Visible label heuristic: prefer aria-label, then placeholder, then
        # the tag's own text (filled in on close).
        label_hint = attrs.get("aria-label") or attrs.get("placeholder")
        for attr in _ATTR_PRIORITY:
            val = attrs.get(attr)
            if val is None or not val.strip():
                continue
            # `role` is only useful when it names a real ARIA role; skip
            # bare presentation/none values that don't help selection.
            if attr == "role" and val.strip().lower() in ("presentation", "none"):
                continue
            self.records.append(LocatorRecord(
                line=line,
                selector_kind=_KIND_BY_ATTR[attr],
                selector_value=val.strip(),
                component=tag,
                label=label_hint,
                context=context,
            ))

    # ---- HTMLParser overrides ----

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line = self._current_line()
        attr_map = {k: (v or "") for k, v in attrs}
        self._emit_attributes(tag, line, attr_map)
        self._stack.append((tag, line, attr_map, []))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing tag (`<input />`) — no text body to track.
        line = self._current_line()
        attr_map = {k: (v or "") for k, v in attrs}
        self._emit_attributes(tag, line, attr_map)

    def handle_endtag(self, tag: str) -> None:
        # Pop down to the matching opening tag. Some HTML is unbalanced;
        # tolerate that by walking the stack in reverse.
        for idx in range(len(self._stack) - 1, -1, -1):
            if self._stack[idx][0] == tag:
                open_tag, open_line, attrs, text_buf = self._stack.pop(idx)
                self._maybe_emit_text(open_tag, open_line, attrs, text_buf)
                # Drop anything that was nested below the unmatched tags.
                del self._stack[idx:]
                return
        # If we never found the open tag, ignore — html.parser will still
        # carry on with the next event.

    def handle_data(self, data: str) -> None:
        if not data.strip() or not self._stack:
            return
        self._stack[-1][3].append(data)

    def _maybe_emit_text(
        self, tag: str, line: int, attrs: dict[str, str], text_buf: list[str],
    ) -> None:
        if tag not in _TEXT_BEARING_TAGS:
            return
        text = " ".join(t.strip() for t in text_buf if t.strip())
        text = re.sub(r"\s+", " ", text).strip()
        # Skip if there's no real text or it's so long it's clearly a
        # paragraph rather than a button label.
        if not text or len(text) > 80:
            return
        # `text` locators are powerful in Playwright but lose to testids
        # when both exist — we still record them for the cases where the
        # team has no testids at all.
        self.records.append(LocatorRecord(
            line=line,
            selector_kind="text",
            selector_value=text,
            component=tag,
            label=text,
            context={k: v for k, v in attrs.items() if v is not None and len(v) <= 200},
        ))


def _extract_html(text: str) -> list[LocatorRecord]:
    parser = _LocatorHTMLParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as e:  # noqa: BLE001 — html.parser raises on malformed input
        log.debug("html.parser bailed mid-file: %s", e)
    return parser.records


# ============================================================================
# JSX / TSX extractor — regex pass over attribute lists
# ============================================================================
# JSX is HTML-shaped but with `{expr}` values that html.parser doesn't
# handle. We do a regex sweep for `<Component … attr="value" …>` patterns.
# We accept attrs with either double-quoted strings or single quotes; brace
# expressions are skipped (their values are dynamic and useless as anchors).

_JSX_TAG_RE = re.compile(
    r"""
    <(?P<tag>[A-Za-z][A-Za-z0-9_.\-]*)        # opening tag/component name
    (?P<attrs>(?:\s+[^>]*?)??)                # attribute soup (lazy)
    \s*/?>                                    # closing > or />
    """,
    re.VERBOSE | re.DOTALL,
)

# Matches `attr="value"` or `attr='value'`. Ignores `attr={...}`.
_JSX_ATTR_RE = re.compile(
    r"""(?P<name>[A-Za-z_][A-Za-z0-9_:\-]*)\s*=\s*(?P<q>["'])(?P<val>.*?)(?P=q)""",
    re.DOTALL,
)


def _extract_jsx(text: str) -> list[LocatorRecord]:
    records: list[LocatorRecord] = []
    # Pre-compute line numbers by counting newlines up to each match start.
    # Faster than calling text.count() per match.
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)

    def _line_of(offset: int) -> int:
        # Binary search would be tidier; linear is fine — files are small.
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    for tag_m in _JSX_TAG_RE.finditer(text):
        tag = tag_m.group("tag")
        attrs_str = tag_m.group("attrs") or ""
        attrs: dict[str, str] = {}
        for attr_m in _JSX_ATTR_RE.finditer(attrs_str):
            name = attr_m.group("name")
            val = attr_m.group("val").strip()
            # JSX renames `for` → `htmlFor` and `class` → `className`. Map back
            # so the codegen agent sees consistent attribute names downstream.
            if name == "htmlFor":
                name = "for"
            elif name == "className":
                name = "class"
            attrs[name] = val
        if not attrs:
            continue
        line = _line_of(tag_m.start())
        context = {k: v for k, v in attrs.items() if len(v) <= 200}
        label_hint = attrs.get("aria-label") or attrs.get("placeholder") or attrs.get("title")

        for attr in _ATTR_PRIORITY:
            val = attrs.get(attr)
            if not val:
                continue
            if attr == "role" and val.strip().lower() in ("presentation", "none"):
                continue
            records.append(LocatorRecord(
                line=line,
                selector_kind=_KIND_BY_ATTR[attr],
                selector_value=val,
                component=tag,
                label=label_hint,
                context=context,
            ))
    return records


# ============================================================================
# Public API
# ============================================================================


def extract_locators(path: str, content: str) -> list[LocatorRecord]:
    """Dispatch on file extension. Returns [] for non-UI files."""
    p = path.lower()
    if p.endswith((".html", ".htm")):
        return _extract_html(content)
    if p.endswith((".tsx", ".jsx")):
        return _extract_jsx(content)
    if p.endswith((".vue", ".svelte")):
        # Both formats embed an HTML template block. Pull it out and feed
        # the rest of the file through the HTML parser. If we can't find
        # a template block, treat the whole file as template (Svelte does
        # this implicitly).
        m = re.search(r"<template[^>]*>(.*?)</template>", content, re.DOTALL | re.IGNORECASE)
        body = m.group(1) if m else content
        return _extract_html(body)
    return []


# ============================================================================
# Persistence — called from dev_repo.ingest_repo()
# ============================================================================


async def index_locators_for_file(
    *,
    workspace_id: str,
    repo_file_id: str,
    path: str,
    content: str,
) -> int:
    """Extract and store locators for one file. Returns count inserted.

    Caller is responsible for having already deleted prior rows for this
    file (done at the repo level inside ingest_repo's transaction)."""
    records = extract_locators(path, content)
    if not records:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        for r in records:
            await conn.execute(
                """
                insert into public.locator_index
                    (workspace_id, repo_file_id, line, selector_kind,
                     selector_value, component, label, context)
                values ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::jsonb)
                """,
                workspace_id, repo_file_id, r.line, r.selector_kind,
                r.selector_value, r.component, r.label, r.context,
            )
    return len(records)


async def delete_locators_for_repo(workspace_id: str, repo_id: str) -> None:
    """Wipe locator rows for an entire repo. Used at re-index time."""
    await execute(
        """
        delete from public.locator_index
         where workspace_id = $1::uuid
           and repo_file_id in (select id from public.repo_files where repo_id = $2::uuid)
        """,
        workspace_id, repo_id,
    )


# ============================================================================
# Search — the `find_locator` tool the codegen agent uses
# ============================================================================


@dataclass
class LocatorMatch:
    id: str
    repo_file_id: str
    path: str
    line: int
    selector_kind: str
    selector_value: str
    component: str | None
    label: str | None
    context: dict
    score: float


async def find_locator(
    *,
    workspace_id: str,
    intent: str,
    k: int = 6,
    repo_id: str | None = None,
    kinds: Iterable[str] | None = None,
) -> list[LocatorMatch]:
    """Score-rank locator candidates for a natural-language intent.

    "Login button"            → matches by tag/component=button + label=login
    "OTP input"               → matches placeholder/name='otp'
    "Submit form"             → matches role=submit / type=submit + text=submit

    The ranking is BM25 over (selector_value, component, label). We also
    boost preferred kinds (testid > id > aria > role > text). Returned in
    descending score; empty list means "no match — ask a clarify question".
    """
    q = (intent or "").strip()
    if not q:
        return []
    args: list = [workspace_id, q, max(1, min(k, 50))]
    # Compute placeholder indices dynamically — hard-coding $4/$5 broke when
    # `kinds` was supplied without `repo_id` (kinds bound to $4 instead of $5).
    repo_clause = ""
    if repo_id:
        args.append(repo_id)
        repo_clause = f"and f.repo_id = ${len(args)}::uuid"
    kind_clause = ""
    if kinds:
        args.append(list(kinds))
        kind_clause = f"and l.selector_kind = any(${len(args)})"

    rows = await fetch(
        f"""
        select l.id::text as id, l.repo_file_id::text as repo_file_id,
               f.path, l.line, l.selector_kind, l.selector_value,
               l.component, l.label, l.context,
               ts_rank_cd(
                 to_tsvector('english',
                   coalesce(l.selector_value,'') || ' ' ||
                   coalesce(l.component,'')      || ' ' ||
                   coalesce(l.label,'')),
                 q
               ) as bm25,
               case l.selector_kind
                 when 'testid' then 1.0
                 when 'id'     then 0.8
                 when 'aria'   then 0.7
                 when 'role'   then 0.6
                 when 'name'   then 0.55
                 when 'placeholder' then 0.5
                 when 'text'   then 0.4
                 else 0.2
               end as kind_boost
          from public.locator_index l
          join public.repo_files f on f.id = l.repo_file_id
         cross join websearch_to_tsquery('english', $2) as q
         where l.workspace_id = $1::uuid
           and to_tsvector('english',
                 coalesce(l.selector_value,'') || ' ' ||
                 coalesce(l.component,'') || ' ' ||
                 coalesce(l.label,'')) @@ q
           {repo_clause}
           {kind_clause}
         order by (bm25 * kind_boost) desc
         limit $3
        """,
        *args,
    )
    return [
        LocatorMatch(
            id=r["id"],
            repo_file_id=r["repo_file_id"],
            path=r["path"],
            line=int(r["line"]),
            selector_kind=r["selector_kind"],
            selector_value=r["selector_value"],
            component=r.get("component"),
            label=r.get("label"),
            context=r.get("context") or {},
            score=float(r["bm25"]) * float(r["kind_boost"]),
        )
        for r in rows
    ]
