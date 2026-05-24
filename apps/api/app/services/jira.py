"""Jira REST client scoped to a single workspace's saved PAT.

Uses Basic auth with `email:api_token` (per Atlassian docs for Jira Cloud).
Same client also works against Jira Server / Data Center installations where
the user pastes their PAT as the api_token.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx
from fastapi import HTTPException, status

from app.core.crypto import decrypt
from app.db.queries import fetchrow


@dataclass(slots=True)
class JiraCreds:
    base_url: str
    email: str
    pat: str


async def load_creds(workspace_id: str) -> JiraCreds:
    row = await fetchrow(
        "select config, encrypted_secret from public.integrations "
        "where workspace_id = $1 and type = 'jira'",
        workspace_id,
    )
    if not row or not row["encrypted_secret"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Jira not connected. Go to Settings → Integrations to add a PAT.",
        )
    base_url = row["config"].get("base_url", "").rstrip("/")
    email, pat = decrypt(bytes(row["encrypted_secret"])).split("\n", 1)
    return JiraCreds(base_url=base_url, email=email, pat=pat)


def _auth_header(creds: JiraCreds) -> dict[str, str]:
    token = base64.b64encode(f"{creds.email}:{creds.pat}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}


async def get_issue(workspace_id: str, key: str) -> dict:
    """Fetch a single issue by key. Persists into `tickets` for later reuse."""
    creds = await load_creds(workspace_id)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{creds.base_url}/rest/api/3/issue/{key}",
            headers=_auth_header(creds),
            params={"fields": "summary,description,status,issuetype,priority,labels,components,assignee,reporter,attachment"},
        )
    if r.status_code == 404:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Jira issue {key} not found")
    if not r.is_success:
        raise HTTPException(r.status_code, f"Jira error: {r.text[:300]}")
    raw = r.json()
    fields = raw.get("fields", {})
    title = fields.get("summary") or key
    desc = _adf_to_text(fields.get("description")) if fields.get("description") else None
    status_name = (fields.get("status") or {}).get("name")

    # Upsert into tickets table
    await fetchrow(
        """
        insert into public.tickets (workspace_id, jira_key, title, description, status, raw, fetched_at)
        values ($1, $2, $3, $4, $5, $6, now())
        on conflict (workspace_id, jira_key) do update
          set title = excluded.title,
              description = excluded.description,
              status = excluded.status,
              raw = excluded.raw,
              fetched_at = now()
        returning id::text
        """,
        workspace_id,
        key,
        title,
        desc,
        status_name,
        raw,
    )
    # Extract attachment metadata. We don't download the bytes — we just tell
    # the LLM "this ticket has screenshots X, Y, Z" so it knows there's a UI
    # flow it can't directly see and asks for clarifications if needed.
    attachments = []
    for att in fields.get("attachment") or []:
        attachments.append(
            {
                "filename": att.get("filename"),
                "mime_type": att.get("mimeType"),
                "size": att.get("size"),
            }
        )

    return {
        "key": key,
        "title": title,
        "description": desc,
        "status": status_name,
        "issuetype": (fields.get("issuetype") or {}).get("name"),
        "priority": (fields.get("priority") or {}).get("name"),
        "labels": fields.get("labels") or [],
        "attachments": attachments,
        "url": f"{creds.base_url}/browse/{key}",
    }


async def search_issues(workspace_id: str, jql: str, limit: int = 25) -> list[dict]:
    creds = await load_creds(workspace_id)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{creds.base_url}/rest/api/3/search",
            headers=_auth_header(creds),
            params={"jql": jql, "maxResults": limit, "fields": "summary,status,issuetype,priority,updated"},
        )
    if not r.is_success:
        raise HTTPException(r.status_code, f"Jira error: {r.text[:300]}")
    issues = r.json().get("issues", [])
    return [
        {
            "key": i["key"],
            "title": (i.get("fields") or {}).get("summary"),
            "status": ((i.get("fields") or {}).get("status") or {}).get("name"),
            "issuetype": ((i.get("fields") or {}).get("issuetype") or {}).get("name"),
            "priority": ((i.get("fields") or {}).get("priority") or {}).get("name"),
            "url": f"{creds.base_url}/browse/{i['key']}",
        }
        for i in issues
    ]


# --- helpers ----------------------------------------------------------------


def _adf_to_text(node: dict | list | str | None) -> str:
    """Crude Atlassian Document Format -> plain text. Good enough for grounding."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(_adf_to_text(x) for x in node)
    if isinstance(node, dict):
        t = node.get("type")
        if t == "text":
            return node.get("text", "")
        if t in {"hardBreak"}:
            return "\n"
        if t in {"paragraph", "heading", "listItem", "bulletList", "orderedList"}:
            return _adf_to_text(node.get("content", [])) + "\n"
        return _adf_to_text(node.get("content", []))
    return ""


def _json_dumps(o: object) -> str:
    import json

    return json.dumps(o)
