"""Local text embeddings via fastembed (BAAI/bge-base-en-v1.5).

Why this model:
  - 768 dimensions → matches the `vector(768)` columns in 0001_init.sql exactly,
    so no schema change is needed.
  - MIT-licensed, runs on CPU via ONNX. No API key, no per-call cost.
  - Strong MTEB scores; comparable to `text-embedding-3-small` for retrieval.

The model is downloaded lazily on first use (~440 MB → ~/.cache/fastembed/).
We keep a single process-wide instance because warm-up dominates startup.

If we ever swap providers (Voyage, Cohere, OpenAI) only this module changes —
callers should depend on `embed_texts` / `embed_query` only.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable

log = logging.getLogger("qatb.embeddings")

# 768-dim BGE base. We hard-code the model so callers can never accidentally
# write embeddings of a wrong dimensionality into the `vector(768)` column.
MODEL_NAME = "BAAI/bge-base-en-v1.5"
DIM = 768

# BGE family was trained with task-specific instructions. The "query" side
# benefits from a short prefix; the document side does not. Skipping the
# document prefix matches the model card and slightly improves recall.
_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

_model = None
_lock = asyncio.Lock()


async def _get_model():
    """Lazy-load fastembed. First call downloads the ONNX model (~440 MB)."""
    global _model
    if _model is not None:
        return _model
    async with _lock:
        if _model is not None:
            return _model
        # Import is local so importing this module never triggers the heavy
        # ONNX runtime load (matters for CLI scripts and tests).
        from fastembed import TextEmbedding  # type: ignore[import-not-found]
        log.info("Loading embedding model %s (one-time download on first run)", MODEL_NAME)
        # fastembed's constructor is blocking — push it off the event loop so
        # FastAPI's lifespan doesn't stall.
        _model = await asyncio.to_thread(TextEmbedding, model_name=MODEL_NAME)
        log.info("Embedding model ready (dim=%d)", DIM)
        return _model


async def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    """Embed many passages. Use for indexing — no query prefix applied."""
    items = [t.strip() for t in texts if t and t.strip()]
    if not items:
        return []
    model = await _get_model()
    # fastembed.embed() returns a generator of numpy arrays. We materialise to
    # plain Python lists so they're JSON-serialisable and asyncpg-friendly.
    def _run() -> list[list[float]]:
        return [vec.tolist() for vec in model.embed(items)]
    return await asyncio.to_thread(_run)


async def embed_query(query: str) -> list[float]:
    """Embed a single search query. Applies BGE's query-side instruction."""
    q = (query or "").strip()
    if not q:
        return [0.0] * DIM
    model = await _get_model()
    text = _QUERY_INSTRUCTION + q

    def _run() -> list[float]:
        # `embed` returns a generator; take the first (only) vector.
        return next(iter(model.embed([text]))).tolist()

    return await asyncio.to_thread(_run)


def to_pgvector_literal(vec: list[float]) -> str:
    """Render a vector as the literal Postgres `vector` accepts: `[1.0,2.0,...]`.

    We use this with asyncpg's text formatting instead of registering the
    pgvector codec, because we already disable the prepared-statement cache for
    Supabase's transaction pooler, which conflicts with custom type codecs.
    """
    if len(vec) != DIM:
        raise ValueError(f"vector has {len(vec)} dims, expected {DIM}")
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
