"""Provider-agnostic LLM client for BYOK keys.

Supports Anthropic and OpenAI for MVP. JSON-mode completion only — agents
build structured output, and the UI streams via the orchestrator's own SSE
events (step / token / clarify / result), not raw provider stream.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass
from typing import Literal

import httpx
from fastapi import HTTPException, status

from app.core.crypto import decrypt
from app.db.queries import fetchrow

log = logging.getLogger("qatb.llm")

Provider = Literal["anthropic", "openai", "gemini", "openrouter"]

# HTTP status codes that indicate a transient error worth retrying.
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_MAX_RETRIES = 4  # so we try 1 + 4 = 5 times total
_BASE_DELAY_S = 1.5


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
    provider_name: str = "LLM",
) -> httpx.Response:
    """POST with exponential backoff on transient errors.

    Free-tier LLM APIs (especially Gemini) sometimes return 503 UNAVAILABLE
    or 429 RESOURCE_EXHAUSTED when demand spikes. We retry these.
    """
    last_response: httpx.Response | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            r = await client.post(url, headers=headers, json=json_body)
        except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.NetworkError) as e:
            if attempt < _MAX_RETRIES:
                delay = _BASE_DELAY_S * (2 ** attempt) + random.uniform(0, 0.5)
                log.warning("%s network error (attempt %d): %s — retrying in %.1fs", provider_name, attempt + 1, e, delay)
                await asyncio.sleep(delay)
                continue
            raise
        last_response = r
        if r.status_code not in _RETRYABLE_STATUS:
            return r
        if attempt < _MAX_RETRIES:
            # Honor Retry-After if the server set one
            retry_after = r.headers.get("retry-after")
            try:
                delay = float(retry_after) if retry_after else _BASE_DELAY_S * (2 ** attempt)
            except ValueError:
                delay = _BASE_DELAY_S * (2 ** attempt)
            delay += random.uniform(0, 0.5)
            log.warning(
                "%s %d (attempt %d) — retrying in %.1fs. Body: %s",
                provider_name, r.status_code, attempt + 1, delay, r.text[:200],
            )
            await asyncio.sleep(delay)
            continue
    # All retries exhausted
    assert last_response is not None
    return last_response


@dataclass(slots=True)
class LLMResult:
    text: str
    parsed: dict | list | None
    tokens_in: int
    tokens_out: int
    model: str


@dataclass(slots=True)
class LLMClient:
    provider: Provider
    api_key: str
    model: str

    @classmethod
    async def from_workspace(
        cls,
        workspace_id: str,
        provider: Provider | None = None,
    ) -> "LLMClient":
        """Load the most recent API key for the workspace.

        If `provider` is None, picks the most-recently-created key regardless
        of provider.
        """
        if provider:
            row = await fetchrow(
                """
                select provider::text, encrypted_key from public.api_keys
                where workspace_id = $1 and provider = $2::api_provider
                order by created_at desc limit 1
                """,
                workspace_id,
                provider,
            )
        else:
            row = await fetchrow(
                """
                select provider::text, encrypted_key from public.api_keys
                where workspace_id = $1
                order by created_at desc limit 1
                """,
                workspace_id,
            )
        if not row:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "No LLM API key configured. Add one in Settings → API keys.",
            )
        p: Provider = row["provider"]  # type: ignore[assignment]
        key = decrypt(bytes(row["encrypted_key"]))
        return cls(provider=p, api_key=key, model=_default_model(p))

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> LLMResult:
        """Single-shot completion that returns parsed JSON.

        We instruct the model to emit JSON only; we then tolerate fenced
        code blocks and stray prose by extracting the first {...} or [...]
        substring before parsing.

        `temperature` defaults to 0.1 for near-deterministic output. LLMs
        are not 100% reproducible, but low temperature keeps the count and
        structure of outputs (e.g. number of test cases) consistent.
        """
        if self.provider == "anthropic":
            return await _anthropic(self.api_key, self.model, system, user, max_tokens, temperature)
        if self.provider == "openai":
            return await _openai(self.api_key, self.model, system, user, max_tokens, temperature)
        if self.provider == "gemini":
            return await _gemini(self.api_key, self.model, system, user, max_tokens, temperature)
        if self.provider == "openrouter":
            return await _openrouter(self.api_key, self.model, system, user, max_tokens, temperature)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown provider: {self.provider}")


# --------------------------------- providers -------------------------------


def _default_model(provider: Provider) -> str:
    return {
        "anthropic": "claude-3-5-haiku-latest",
        "openai": "gpt-4o-mini",
        "gemini": "gemini-2.5-flash",  # gemini-1.5-flash was retired
        "openrouter": "anthropic/claude-3.5-haiku",
    }[provider]


async def _anthropic(key: str, model: str, system: str, user: str, max_tokens: int, temperature: float) -> LLMResult:
    async with httpx.AsyncClient(timeout=90) as client:
        r = await _post_with_retry(
            client,
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json_body={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            provider_name="Anthropic",
        )
    if not r.is_success:
        raise HTTPException(r.status_code, f"Anthropic error: {r.text[:400]}")
    data = r.json()
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    usage = data.get("usage", {})
    return LLMResult(
        text=text,
        parsed=_extract_json(text),
        tokens_in=int(usage.get("input_tokens", 0)),
        tokens_out=int(usage.get("output_tokens", 0)),
        model=data.get("model", model),
    )


async def _openai(key: str, model: str, system: str, user: str, max_tokens: int, temperature: float) -> LLMResult:
    async with httpx.AsyncClient(timeout=90) as client:
        r = await _post_with_retry(
            client,
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json_body={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            provider_name="OpenAI",
        )
    if not r.is_success:
        raise HTTPException(r.status_code, f"OpenAI error: {r.text[:400]}")
    data = r.json()
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return LLMResult(
        text=text,
        parsed=_extract_json(text),
        tokens_in=int(usage.get("prompt_tokens", 0)),
        tokens_out=int(usage.get("completion_tokens", 0)),
        model=data.get("model", model),
    )


async def _gemini(key: str, model: str, system: str, user: str, max_tokens: int, temperature: float) -> LLMResult:
    # Gemini free tier returns 503 UNAVAILABLE under load — try the primary
    # model with retries, then fall back to a lighter variant if all retries
    # fail. Both share the same key.
    fallback_models = [model]
    if model not in {"gemini-2.5-flash-lite", "gemini-2.0-flash"}:
        fallback_models.append("gemini-2.5-flash-lite")

    last_text = ""
    last_status = 500
    for candidate in fallback_models:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await _post_with_retry(
                client,
                f"https://generativelanguage.googleapis.com/v1beta/models/{candidate}:generateContent?key={key}",
                json_body={
                    "system_instruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {
                        "maxOutputTokens": max_tokens,
                        "temperature": temperature,
                        "topP": 0.95,
                        "responseMimeType": "application/json",
                        # Gemini 2.5 Flash silently uses tokens for chain-of-thought
                        # "thinking" before producing output. Disable so all of
                        # maxOutputTokens goes to the actual JSON response.
                        "thinkingConfig": {"thinkingBudget": 0},
                    },
                },
                provider_name=f"Gemini[{candidate}]",
            )
        if r.is_success:
            data = r.json()
            cands = data.get("candidates", [])
            text = ""
            if cands:
                parts = (cands[0].get("content") or {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
            usage = data.get("usageMetadata", {})
            return LLMResult(
                text=text,
                parsed=_extract_json(text),
                tokens_in=int(usage.get("promptTokenCount", 0)),
                tokens_out=int(usage.get("candidatesTokenCount", 0)),
                model=candidate,
            )
        last_text = r.text
        last_status = r.status_code
        log.warning("Gemini %s exhausted retries (HTTP %d). Trying next fallback if any.", candidate, r.status_code)
    raise HTTPException(
        last_status,
        f"Gemini error after retries (tried {fallback_models}): {last_text[:400]}",
    )
    data = r.json()
    cands = data.get("candidates", [])
    text = ""
    if cands:
        parts = (cands[0].get("content") or {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
    usage = data.get("usageMetadata", {})
    return LLMResult(
        text=text,
        parsed=_extract_json(text),
        tokens_in=int(usage.get("promptTokenCount", 0)),
        tokens_out=int(usage.get("candidatesTokenCount", 0)),
        model=model,
    )


async def _openrouter(key: str, model: str, system: str, user: str, max_tokens: int, temperature: float) -> LLMResult:
    async with httpx.AsyncClient(timeout=90) as client:
        r = await _post_with_retry(
            client,
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json_body={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            provider_name="OpenRouter",
        )
    if not r.is_success:
        raise HTTPException(r.status_code, f"OpenRouter error: {r.text[:400]}")
    data = r.json()
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return LLMResult(
        text=text,
        parsed=_extract_json(text),
        tokens_in=int(usage.get("prompt_tokens", 0)),
        tokens_out=int(usage.get("completion_tokens", 0)),
        model=data.get("model", model),
    )


# --------------------------------- helpers ---------------------------------


_CODE_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(text: str) -> dict | list | None:
    """Best-effort JSON extraction from a model response.

    Tolerates fenced code blocks (``` ... ```) and leading/trailing prose.
    If the JSON is truncated (e.g. Gemini hit max_tokens mid-array), we
    try to salvage by repairing: drop the trailing partial item and close
    open arrays/objects.
    """
    if not text:
        return None
    # Strip code fences
    m = _CODE_FENCE.search(text)
    candidate = m.group(1).strip() if m else text.strip()
    # Whole-string parse
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # Find first {...} or [...]
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
    # Repair pass: walk forward tracking brace/bracket depth + string state,
    # and snapshot a "last valid prefix" whenever we hit depth 0 boundaries
    # inside arrays. This recovers truncated `{"cases":[{...},{...}, <half>...`
    repaired = _repair_truncated_json(candidate)
    if repaired:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass
    return None


def _repair_truncated_json(text: str) -> str | None:
    """Try to recover a truncated JSON object by closing it after the
    last complete top-level array element.

    Returns a re-closed JSON string, or None if not recoverable.
    """
    if not text:
        return None
    text = text.lstrip()
    if not text.startswith("{") and not text.startswith("["):
        return None

    in_str = False
    escape = False
    stack: list[str] = []
    # Last position where we have a complete sub-object inside the outermost array.
    last_safe_end = -1
    last_safe_stack: list[str] = []

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                return None  # malformed
            expected = "{" if ch == "}" else "["
            if stack[-1] != expected:
                return None
            stack.pop()
            # Snapshot the safe point: just after closing a `{...}` while we're
            # still inside an array (next element of the array starts after).
            if ch == "}" and stack and stack[-1] == "[":
                last_safe_end = i
                last_safe_stack = stack.copy()

    if last_safe_end == -1:
        return None
    truncated = text[: last_safe_end + 1]
    # Close every still-open container that was open at the safe point.
    closer = "".join("}" if c == "{" else "]" for c in reversed(last_safe_stack))
    return truncated + closer
