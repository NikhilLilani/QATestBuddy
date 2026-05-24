"""Agent base contract.

Every concrete agent returns an AgentEnvelope. Middleware (orchestrator) validates:
  1. citations non-empty
  2. confidence in [0, 1]; below threshold -> clarify
  3. output matches strict Pydantic schema; missing required field -> clarify

This file holds the envelope only. Agents live in subclasses.
"""
from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class JiraCitation(BaseModel):
    kind: Literal["jira"] = "jira"
    key: str
    field: str


class RagCitation(BaseModel):
    kind: Literal["rag_chunk"] = "rag_chunk"
    chunk_id: str


class RepoCitation(BaseModel):
    kind: Literal["repo_file"] = "repo_file"
    repo: str
    path: str
    line_start: int
    line_end: int


Citation = JiraCitation | RagCitation | RepoCitation


class TokenUsage(BaseModel):
    in_: int = Field(0, alias="in")
    out: int = 0

    model_config = {"populate_by_name": True}


class AgentEnvelope(BaseModel, Generic[T]):
    data: T
    citations: list[Citation] = Field(min_length=1)
    # Questions the agent wants the human to answer before relying on the
    # output. Always optional — empty list = the agent was confident.
    clarifications: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    clarifications_used: int = 0
    tokens: TokenUsage = TokenUsage()


class ClarifyRequest(BaseModel):
    """Raised by an agent when it needs the user to fill a gap."""

    id: str
    question: str
    schema_: dict | None = Field(default=None, alias="schema")

    model_config = {"populate_by_name": True}
