"""RAG knowledge sources — upload PRD/BRD/MD/TXT, list, delete.

This is the user-facing entry into the M1 retrieval foundation. The actual
chunking, embedding and storage live in `services.rag_ingest`. This file is
thin glue: auth → file decode → ingest call → JSON response.

Confluence / Google Docs / dev-repo ingestion get their own routers in M2
and M7 — they share the same ingest service.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.core.workspace import CurrentWorkspace
from app.services.rag_ingest import (
    RagKind,
    decode_file,
    delete_source,
    ingest_text,
    kind_for_filename,
    list_sources,
)

router = APIRouter(prefix="/rag", tags=["rag"])

# Hard cap so a stray 200 MB PDF can't OOM the worker. Real PRDs are
# typically < 5 MB; bump if a real customer hits this.
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class SourceOut(BaseModel):
    id: str
    kind: str
    name: str
    source_uri: str | None = None
    status: str
    bytes: int | None = None
    chunk_count: int = 0
    created_at: str | None = None


class IngestResult(BaseModel):
    source_id: str
    document_id: str
    chunks: int
    deduped: bool = Field(
        description="True if this exact content was already indexed; the existing source was returned."
    )


@router.get("/sources")
async def list_(ctx: CurrentWorkspace) -> list[SourceOut]:
    rows = await list_sources(ctx.workspace_id)
    return [
        SourceOut(
            id=str(r["id"]),
            kind=str(r["kind"]),
            name=r["name"],
            source_uri=r.get("source_uri"),
            status=r["status"],
            bytes=r.get("bytes"),
            chunk_count=int(r.get("chunk_count") or 0),
            created_at=r["created_at"].isoformat() if r.get("created_at") else None,
        )
        for r in rows
    ]


@router.post("/sources/upload", status_code=status.HTTP_201_CREATED)
async def upload(
    ctx: CurrentWorkspace,
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
) -> IngestResult:
    """Upload a PRD/BRD/MD/TXT and index it for retrieval.

    Empty files are rejected (we don't want zero-chunk sources cluttering the
    list). The same content uploaded twice short-circuits to the existing
    source — no duplicate chunks, no wasted embedding work.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty.")
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        )

    text = decode_file(file.filename or "upload", raw)
    if not text.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Could not extract any text from this file. Try a different format.",
        )

    result = await ingest_text(
        workspace_id=ctx.workspace_id,
        name=(name or file.filename or "upload").strip(),
        kind=kind_for_filename(file.filename or ""),
        text=text,
        created_by=ctx.user_id,
    )
    return IngestResult(
        source_id=result.source_id,
        document_id=result.document_id,
        chunks=result.chunks,
        deduped=result.was_already_indexed,
    )


class TextIngestIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    kind: RagKind = "md"
    source_uri: str | None = None


@router.post("/sources/text", status_code=status.HTTP_201_CREATED)
async def ingest_raw_text(ctx: CurrentWorkspace, body: TextIngestIn) -> IngestResult:
    """Ingest a raw text blob — used by the Confluence/Google Docs importer
    (M7) and by the test-suite. Same idempotency contract as `/upload`."""
    result = await ingest_text(
        workspace_id=ctx.workspace_id,
        name=body.name,
        kind=body.kind,
        text=body.text,
        source_uri=body.source_uri,
        created_by=ctx.user_id,
    )
    return IngestResult(
        source_id=result.source_id,
        document_id=result.document_id,
        chunks=result.chunks,
        deduped=result.was_already_indexed,
    )


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_(ctx: CurrentWorkspace, source_id: str) -> None:
    await delete_source(ctx.workspace_id, source_id)
