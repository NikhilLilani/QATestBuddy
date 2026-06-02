"""Hybrid retrieval over rag_chunks: BM25 (ts_rank_cd) + vector (cosine) + RRF.

Why hybrid:
  - Vector search alone misses exact-token matches ("data-testid=foo" is a
    string the embedder doesn't tokenize well).
  - BM25 alone misses paraphrases ("login flow" vs "auth journey").
  - Reciprocal Rank Fusion (RRF) merges the two without needing to calibrate
    score distributions across the two systems — it's just rank-based.

Output rows include the chunk content + document/source metadata so the
caller can build `RagCitation` envelopes without a second DB round-trip.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from app.db.queries import fetch
from app.services.embeddings import embed_query, to_pgvector_literal

log = logging.getLogger("qatb.rag.retrieve")

# RRF constant — 60 is the value Cormack/Clarke recommended in the original
# paper and the de-facto default in Elastic / Weaviate / Vespa. It dampens
# the influence of very low-ranked results without dropping them entirely.
_RRF_K = 60


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    source_id: str
    source_name: str
    source_kind: str
    ord: int
    content: str
    score: float          # fused RRF score
    bm25_rank: int | None
    vector_rank: int | None


async def retrieve(
    *,
    workspace_id: str,
    query: str,
    k: int = 8,
    pool_size: int = 30,
) -> list[RetrievedChunk]:
    """Hybrid retrieve top-k chunks for `query` inside one workspace.

    `pool_size` is how many candidates each subsystem (BM25 / vector) returns
    before fusion. 30 is comfortable headroom for k≤10.
    """
    q = (query or "").strip()
    if not q:
        return []

    # Subsystem 1: BM25 via Postgres tsvector + ts_rank_cd. We use websearch
    # query syntax so quoted phrases and OR work like a search box.
    bm25 = await fetch(
        """
        select c.id::text as chunk_id, c.rag_document_id::text as document_id,
               d.rag_source_id::text as source_id, s.name as source_name,
               s.kind::text as source_kind, c.ord, c.content,
               ts_rank_cd(c.tsv, q) as bm25
          from public.rag_chunks c
          join public.rag_documents d on d.id = c.rag_document_id
          join public.rag_sources   s on s.id = d.rag_source_id
         cross join websearch_to_tsquery('english', $2) as q
         where c.workspace_id = $1::uuid
           and c.tsv @@ q
         order by bm25 desc
         limit $3
        """,
        workspace_id, q, pool_size,
    )

    # Subsystem 2: dense vector cosine via HNSW. `<=>` is the cosine-distance
    # operator from pgvector (smaller = closer), so we order ascending.
    qvec = await embed_query(q)
    vector = await fetch(
        f"""
        select c.id::text as chunk_id, c.rag_document_id::text as document_id,
               d.rag_source_id::text as source_id, s.name as source_name,
               s.kind::text as source_kind, c.ord, c.content,
               (c.embedding <=> $2::vector) as cosine_distance
          from public.rag_chunks c
          join public.rag_documents d on d.id = c.rag_document_id
          join public.rag_sources   s on s.id = d.rag_source_id
         where c.workspace_id = $1::uuid
         order by c.embedding <=> $2::vector
         limit $3
        """,
        workspace_id, to_pgvector_literal(qvec), pool_size,
    )

    return _rrf_fuse(bm25, vector, k=k)


def _rrf_fuse(
    bm25_rows: list[dict],
    vector_rows: list[dict],
    *,
    k: int,
) -> list[RetrievedChunk]:
    """Merge two ranked lists into one by Reciprocal Rank Fusion.

    score(d) = sum_over_lists( 1 / (RRF_K + rank_in_list(d)) )

    A chunk that ranks well in either list bubbles up; one that ranks well
    in BOTH bubbles much higher. We keep the per-list rank around for
    debugging in the UI ("matched on text and meaning").
    """
    by_id: dict[str, RetrievedChunk] = {}

    def _ingest(rows: Iterable[dict], rank_key: str) -> None:
        for idx, row in enumerate(rows):
            cid = row["chunk_id"]
            contribution = 1.0 / (_RRF_K + idx + 1)
            existing = by_id.get(cid)
            if existing is None:
                by_id[cid] = RetrievedChunk(
                    chunk_id=cid,
                    document_id=row["document_id"],
                    source_id=row["source_id"],
                    source_name=row["source_name"],
                    source_kind=row["source_kind"],
                    ord=int(row["ord"]),
                    content=row["content"],
                    score=contribution,
                    bm25_rank=(idx + 1) if rank_key == "bm25" else None,
                    vector_rank=(idx + 1) if rank_key == "vector" else None,
                )
            else:
                existing.score += contribution
                if rank_key == "bm25" and existing.bm25_rank is None:
                    existing.bm25_rank = idx + 1
                if rank_key == "vector" and existing.vector_rank is None:
                    existing.vector_rank = idx + 1

    _ingest(bm25_rows, "bm25")
    _ingest(vector_rows, "vector")

    ranked = sorted(by_id.values(), key=lambda r: r.score, reverse=True)
    return ranked[:k]


def format_for_prompt(chunks: list[RetrievedChunk], *, max_chars: int = 6000) -> str:
    """Render retrieved chunks into a single block to paste into an LLM prompt.

    Each chunk is wrapped with `[doc:<source_name>#<ord>]` headers so the
    model can reference them in its `citations` field by chunk_id. The total
    is hard-capped at `max_chars` to keep token budget predictable.
    """
    if not chunks:
        return ""
    parts: list[str] = []
    used = 0
    for c in chunks:
        header = f"[doc:{c.source_name}#{c.ord}  chunk_id={c.chunk_id}]"
        body = c.content.strip()
        block = f"{header}\n{body}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts)
