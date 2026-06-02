"""Chunk + embed + persist into rag_sources / rag_documents / rag_chunks.

Callers:
  - PRD/BRD file upload routes (PDF, DOCX, MD, TXT)
  - Confluence / Google Docs importers (M7)
  - Dev repo indexer (M2 — uses sibling `repos`/`repo_chunks` tables)

Design notes:
  - Chunking: token-aware (target ~500 tokens, ~80 token overlap) using a
    simple whitespace approximation. Keeps paragraphs together when possible.
    Good enough to ship; we can swap in tiktoken later without changing the
    table layout.
  - Idempotency: a SHA-256 hash of every document is stored. Re-ingesting the
    same file is a no-op (returns existing rag_source_id).
  - Workspace scoping is explicit on every insert — RLS doesn't apply to the
    service-role connection, so we must pass workspace_id ourselves.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Iterable, Literal

from app.db.queries import execute, fetch, fetchrow, get_pool
from app.services.embeddings import embed_texts, to_pgvector_literal

log = logging.getLogger("qatb.rag.ingest")

RagKind = Literal["pdf", "docx", "md", "json", "git", "jira_history", "url"]

# Chunking parameters — chosen for ~500-token chunks on prose, which is the
# sweet spot for BGE-base's 512-token context and small enough to stay
# focused per retrieval hit.
_TARGET_CHARS = 1800      # ~ 450 tokens on average English prose
_OVERLAP_CHARS = 250      # ~ 60 tokens — gives the LLM enough boundary context
_MIN_CHUNK_CHARS = 120    # below this we merge with the next chunk


@dataclass
class IngestedSource:
    source_id: str
    document_id: str
    chunks: int
    was_already_indexed: bool


# -------------------------- chunking primitives ------------------------------


def _normalize(text: str) -> str:
    """Strip BOM, normalise line endings, collapse runs of blank lines."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    # Collapse 3+ blank lines to 2 — preserves paragraph breaks for the splitter.
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def chunk_text(text: str) -> list[str]:
    """Split text into ~500-token chunks with overlap, preferring paragraph breaks.

    The algorithm walks paragraphs and greedily appends until the buffer
    exceeds `_TARGET_CHARS`, then emits, retaining the last `_OVERLAP_CHARS`
    as the seed for the next chunk. We never split mid-paragraph unless the
    paragraph itself is bigger than the budget (rare, e.g. dumped logs).
    """
    text = _normalize(text)
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    buf = ""

    def _flush() -> None:
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    for para in paragraphs:
        # Oversized paragraph — slice it with sliding window.
        if len(para) > _TARGET_CHARS:
            if buf:
                _flush()
            start = 0
            while start < len(para):
                end = min(start + _TARGET_CHARS, len(para))
                chunks.append(para[start:end].strip())
                if end == len(para):
                    break
                start = end - _OVERLAP_CHARS
            continue

        if len(buf) + len(para) + 2 <= _TARGET_CHARS:
            buf = f"{buf}\n\n{para}" if buf else para
        else:
            _flush()
            # Seed the new buffer with the last N chars of the previous chunk
            # so retrieval doesn't lose context at chunk boundaries.
            tail = chunks[-1][-_OVERLAP_CHARS:] if chunks else ""
            buf = f"{tail}\n\n{para}" if tail else para

    _flush()

    # Merge tiny tail chunks into their predecessor.
    out: list[str] = []
    for c in chunks:
        if out and len(c) < _MIN_CHUNK_CHARS:
            out[-1] = f"{out[-1]}\n\n{c}"
        else:
            out.append(c)
    return out


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


# --------------------------- file decoders -----------------------------------


def decode_file(filename: str, raw: bytes) -> str:
    """Best-effort text extraction for the four formats we ship on day 1.

    PDF → pypdf, DOCX → python-docx, MD/TXT → utf-8. Anything else falls back
    to a utf-8 decode with replacement so we never crash on a weird upload.
    """
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]
            from io import BytesIO
            reader = PdfReader(BytesIO(raw))
            pages = [(page.extract_text() or "") for page in reader.pages]
            return "\n\n".join(pages)
        except Exception as e:  # noqa: BLE001 — best-effort decode
            log.warning("PDF decode failed for %s: %s — falling back to utf-8", filename, e)
    elif name.endswith(".docx"):
        try:
            from docx import Document  # type: ignore[import-not-found]
            from io import BytesIO
            doc = Document(BytesIO(raw))
            return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as e:  # noqa: BLE001
            log.warning("DOCX decode failed for %s: %s", filename, e)
    # md, txt, json, anything else — try utf-8 with replacement.
    return raw.decode("utf-8", errors="replace")


def kind_for_filename(filename: str) -> RagKind:
    n = (filename or "").lower()
    if n.endswith(".pdf"):
        return "pdf"
    if n.endswith(".docx"):
        return "docx"
    if n.endswith(".md") or n.endswith(".markdown"):
        return "md"
    if n.endswith(".json"):
        return "json"
    return "md"  # default for txt / unknown — treats as plain prose


# ----------------------------- ingest pipeline -------------------------------


async def ingest_text(
    *,
    workspace_id: str,
    name: str,
    kind: RagKind,
    text: str,
    source_uri: str | None = None,
    created_by: str | None = None,
    metadata: dict | None = None,
) -> IngestedSource:
    """Ingest a raw text blob: chunk → embed → store.

    Idempotent on (workspace_id, content_hash). Re-ingesting the same content
    short-circuits and returns the existing rag_source row.
    """
    text = _normalize(text)
    if not text:
        raise ValueError("ingest_text called with empty content")
    content_hash = _sha256(text)

    # De-dupe: same workspace + same content = same source.
    existing = await fetchrow(
        """
        select s.id as source_id, d.id as document_id,
               (select count(*) from public.rag_chunks where rag_document_id = d.id) as n_chunks
          from public.rag_documents d
          join public.rag_sources s on s.id = d.rag_source_id
         where d.workspace_id = $1::uuid
           and d.hash = $2
         limit 1
        """,
        workspace_id, content_hash,
    )
    if existing:
        return IngestedSource(
            source_id=str(existing["source_id"]),
            document_id=str(existing["document_id"]),
            chunks=int(existing["n_chunks"] or 0),
            was_already_indexed=True,
        )

    # Chunk + embed before we touch the DB so a tokenizer crash doesn't leave
    # half-ingested orphan rows around.
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("chunker produced 0 chunks (input was non-empty but unreadable)")
    vectors = await embed_texts(chunks)
    if len(vectors) != len(chunks):
        raise RuntimeError(
            f"embedding count mismatch: {len(vectors)} vectors for {len(chunks)} chunks"
        )

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        source_row = await conn.fetchrow(
            """
            insert into public.rag_sources
                (workspace_id, kind, name, source_uri, status, bytes, created_by)
            values ($1::uuid, $2::public.rag_kind, $3, $4, 'ready', $5, $6::uuid)
            returning id
            """,
            workspace_id, kind, name, source_uri, len(text.encode("utf-8")),
            created_by,
        )
        source_id = str(source_row["id"])

        doc_row = await conn.fetchrow(
            """
            insert into public.rag_documents
                (workspace_id, rag_source_id, path, mime, hash, metadata)
            values ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb)
            returning id
            """,
            workspace_id, source_id, name, _mime_for_kind(kind), content_hash,
            metadata or {},
        )
        document_id = str(doc_row["id"])

        # Bulk insert chunks. We can't use COPY because of the vector literal;
        # executemany on prepared parameters is fast enough for the sizes
        # we expect (PRDs of a few hundred chunks).
        for ord_, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True)):
            await conn.execute(
                """
                insert into public.rag_chunks
                    (workspace_id, rag_document_id, ord, content, tokens, embedding)
                values ($1::uuid, $2::uuid, $3, $4, $5, $6::vector)
                """,
                workspace_id, document_id, ord_, chunk,
                _approx_token_count(chunk), to_pgvector_literal(vec),
            )

    return IngestedSource(
        source_id=source_id,
        document_id=document_id,
        chunks=len(chunks),
        was_already_indexed=False,
    )


def _mime_for_kind(kind: RagKind) -> str:
    return {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "md": "text/markdown",
        "json": "application/json",
        "git": "text/plain",
        "jira_history": "application/json",
        "url": "text/html",
    }.get(kind, "text/plain")


def _approx_token_count(s: str) -> int:
    # Rough rule of thumb: 4 chars ≈ 1 token for English prose. Good enough
    # for budgeting; we don't need exact counts in `rag_chunks.tokens`.
    return max(1, len(s) // 4)


async def list_sources(workspace_id: str) -> list[dict]:
    return await fetch(
        """
        select s.id, s.kind, s.name, s.source_uri, s.status, s.bytes,
               s.created_at, s.updated_at,
               (select count(*) from public.rag_chunks c where c.workspace_id = s.workspace_id
                 and c.rag_document_id in (select d.id from public.rag_documents d where d.rag_source_id = s.id)) as chunk_count
          from public.rag_sources s
         where s.workspace_id = $1::uuid
         order by s.created_at desc
        """,
        workspace_id,
    )


async def delete_source(workspace_id: str, source_id: str) -> None:
    # ON DELETE CASCADE on rag_documents → rag_chunks handles the rest.
    await execute(
        "delete from public.rag_sources where id = $1::uuid and workspace_id = $2::uuid",
        source_id, workspace_id,
    )
