/**
 * Server-side helper to call the FastAPI backend with the current user's
 * Supabase access token and active workspace id.
 *
 * Used by Server Components and Server Actions. Do NOT import from Client
 * Components — call it via a Server Action instead.
 */
import { createClient } from '@/lib/supabase/server';
import { getCurrentWorkspace } from '@/lib/workspace';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  init: RequestInit & { workspaceId?: string } = {},
): Promise<T> {
  const supabase = await createClient();
  const [{ data: sessionData }, { data: userData }] = await Promise.all([
    supabase.auth.getSession(),
    supabase.auth.getUser(),
  ]);
  const session = sessionData.session;
  const user = userData.user;

  if (!session?.access_token || !user?.id) throw new ApiError(401, null, 'Not authenticated');

  const workspaceId = init.workspaceId ?? (await getCurrentWorkspace(user.id))?.id;
  if (!workspaceId) throw new ApiError(400, null, 'No active workspace');

  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${session.access_token}`);
  headers.set('X-Workspace-Id', workspaceId);
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');

  const res = await fetch(`${API_URL}${path}`, { ...init, headers, cache: 'no-store' });
  if (!res.ok) {
    // Read body once as text, then try to parse as JSON. Reading the same
    // response body twice (e.g., .json() then .text()) throws "Body is
    // unusable" in Next.js's fetch — that error masks the real failure.
    const raw = await res.text();
    let body: unknown = raw;
    try {
      body = raw ? JSON.parse(raw) : null;
    } catch {
      /* keep raw text */
    }
    const detail =
      (typeof body === 'object' && body && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : '') || raw || `API ${res.status}`;
    throw new ApiError(res.status, body, `API ${res.status}: ${path} — ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
