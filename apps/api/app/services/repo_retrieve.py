"""Hybrid retrieval over `repo_chunks` (dev-repo code) — sibling of rag_retrieve.

Same recipe as `rag_retrieve`: BM25 on the generated tsvector + dense vector
cosine on the HNSW index, fused with Reciprocal Rank Fusion. The schema is
slightly different (repo_files instead of rag_documents) so the SQL is
duplicated rather than abstracted — keeping the two retrievers separate makes
it easy to add code-specific re-rankers later without polluting the doc path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from app.db.queries import fetch
from app.services.embeddings import embed_query, to_pgvector_literal

log = logging.getLogger("qatb.repo.retrieve")

_RRF_K = 60


@dataclass
class RetrievedCodeChunk:
    chunk_id: str
    repo_file_id: str
    repo_id: str
    repo_name: str        # "{owner}/{name}" for the citation pill
    path: str
    lang: str | None
    ord: int
    content: str
    score: float
    bm25_rank: int | None
    vector_rank: int | None
    # Best-effort start/end line for citations. We compute these from the
    # chunk's offset inside the original file at retrieval time; for now we
    # ship `None` and let the citation layer fall back to "(chunk N)".
    line_start: int | None = None
    line_end: int | None = None


async def retrieve_code(
    *,
    workspace_id: str,
    query: str,
    k: int = 8,
    pool_size: int = 30,
    repo_id: str | None = None,
) -> list[RetrievedCodeChunk]:
    """Hybrid retrieve over the workspace's indexed dev-repo code.

    If `repo_id` is given, restrict to that one repo. Useful when codegen
    wants selectors from a *specific* dev repo and not whatever else the
    workspace has indexed.
    """
    q = (query or "").strip()
    if not q:
        return []

    repo_filter = "and r.id = $4::uuid" if repo_id else ""
    args_bm25: list = [workspace_id, q, pool_size]
    if repo_id:
        args_bm25.append(repo_id)

    bm25 = await fetch(
        f"""
        select c.id::text as chunk_id, c.repo_file_id::text as repo_file_id,
               f.repo_id::text as repo_id,
               coalesce(r.owner, '_') || '/' || coalesce(r.name, '_') as repo_name,
               f.path, f.lang, c.ord, c.content,
               ts_rank_cd(c.tsv, q) as bm25
          from public.repo_chunks c
          join public.repo_files  f on f.id = c.repo_file_id
          join public.repos       r on r.id = f.repo_id
         cross join websearch_to_tsquery('english', $2) as q
         where c.workspace_id = $1::uuid
           and c.tsv @@ q
           {repo_filter}
         order by bm25 desc
         limit $3
        """,
        *args_bm25,
    )

    qvec = await embed_query(q)
    args_vec: list = [workspace_id, to_pgvector_literal(qvec), pool_size]
    if repo_id:
        args_vec.append(repo_id)

    vector = await fetch(
        f"""
        select c.id::text as chunk_id, c.repo_file_id::text as repo_file_id,
               f.repo_id::text as repo_id,
               coalesce(r.owner, '_') || '/' || coalesce(r.name, '_') as repo_name,
               f.path, f.lang, c.ord, c.content,
               (c.embedding <=> $2::vector) as cosine_distance
          from public.repo_chunks c
          join public.repo_files  f on f.id = c.repo_file_id
          join public.repos       r on r.id = f.repo_id
         where c.workspace_id = $1::uuid
           {repo_filter}
         order by c.embedding <=> $2::vector
         limit $3
        """,
        *args_vec,
    )

    return _rrf_fuse(bm25, vector, k=k)


def _rrf_fuse(
    bm25_rows: list[dict],
    vector_rows: list[dict],
    *,
    k: int,
) -> list[RetrievedCodeChunk]:
    by_id: dict[str, RetrievedCodeChunk] = {}

    def _ingest(rows: Iterable[dict], rank_key: str) -> None:
        for idx, row in enumerate(rows):
            cid = row["chunk_id"]
            contribution = 1.0 / (_RRF_K + idx + 1)
            existing = by_id.get(cid)
            if existing is None:
                by_id[cid] = RetrievedCodeChunk(
                    chunk_id=cid,
                    repo_file_id=row["repo_file_id"],
                    repo_id=row["repo_id"],
                    repo_name=row["repo_name"],
                    path=row["path"],
                    lang=row.get("lang"),
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

    return sorted(by_id.values(), key=lambda r: r.score, reverse=True)[:k]


def format_for_prompt(chunks: list[RetrievedCodeChunk], *, max_chars: int = 6000) -> str:
    """Render code chunks for the LLM. Each block is fenced with the file
    path + language hint so the model treats it as code, not prose."""
    if not chunks:
        return ""
    parts: list[str] = []
    used = 0
    for c in chunks:
        lang = c.lang or ""
        header = f"[code:{c.repo_name}:{c.path}  chunk_id={c.chunk_id}]"
        fence_start = f"```{lang}" if lang else "```"
        block = f"{header}\n{fence_start}\n{c.content.strip()}\n```"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts)
