'use server';

import { revalidatePath } from 'next/cache';
import { apiFetch, ApiError } from '@/lib/api';

type Result<T = unknown> = { ok: true; data: T } | { ok: false; error: string };

function fail(e: unknown): Result {
  if (e instanceof ApiError) {
    const body = e.body as { detail?: string } | string | null;
    const msg = typeof body === 'string' ? body : body?.detail ?? e.message;
    return { ok: false, error: msg };
  }
  return { ok: false, error: e instanceof Error ? e.message : String(e) };
}

/* ------------------------------ API keys ------------------------------- */

export async function addApiKey(_prev: Result | null, formData: FormData): Promise<Result> {
  try {
    const data = await apiFetch('/api/v1/settings/api-keys', {
      method: 'POST',
      body: JSON.stringify({
        provider: String(formData.get('provider')),
        name: String(formData.get('name')),
        key: String(formData.get('key')),
      }),
    });
    revalidatePath('/settings/api-keys');
    return { ok: true, data };
  } catch (e) {
    return fail(e);
  }
}

export async function deleteApiKey(id: string): Promise<Result> {
  try {
    await apiFetch(`/api/v1/settings/api-keys/${id}`, { method: 'DELETE' });
    revalidatePath('/settings/api-keys');
    return { ok: true, data: null };
  } catch (e) {
    return fail(e);
  }
}

export async function testApiKey(id: string): Promise<Result> {
  try {
    const data = await apiFetch(`/api/v1/settings/api-keys/${id}/test`, { method: 'POST' });
    return { ok: true, data };
  } catch (e) {
    return fail(e);
  }
}

/* ------------------------------ Jira ----------------------------------- */

export async function connectJira(_prev: Result | null, formData: FormData): Promise<Result> {
  try {
    await apiFetch('/api/v1/integrations/jira', {
      method: 'POST',
      body: JSON.stringify({
        base_url: String(formData.get('base_url')),
        email: String(formData.get('email')),
        pat: String(formData.get('pat')),
      }),
    });
    revalidatePath('/settings/integrations');
    return { ok: true, data: null };
  } catch (e) {
    return fail(e);
  }
}

export async function disconnectJira(): Promise<Result> {
  try {
    await apiFetch('/api/v1/integrations/jira', { method: 'DELETE' });
    revalidatePath('/settings/integrations');
    return { ok: true, data: null };
  } catch (e) {
    return fail(e);
  }
}

export async function testJira(): Promise<Result> {
  try {
    const data = await apiFetch('/api/v1/integrations/jira/test', { method: 'POST' });
    return { ok: true, data };
  } catch (e) {
    return fail(e);
  }
}

/* ----------------------------- Bridge tokens --------------------------- */

export async function createBridgeToken(
  _prev: Result | null,
  formData: FormData,
): Promise<Result<{ token: string }>> {
  try {
    const data = await apiFetch<{ token: string }>('/api/v1/bridge/tokens', {
      method: 'POST',
      body: JSON.stringify({ label: String(formData.get('label')) }),
    });
    revalidatePath('/settings/bridge');
    return { ok: true, data };
  } catch (e) {
    return fail(e) as Result<{ token: string }>;
  }
}

export async function revokeBridgeToken(id: string): Promise<Result> {
  try {
    await apiFetch(`/api/v1/bridge/tokens/${id}`, { method: 'DELETE' });
    revalidatePath('/settings/bridge');
    return { ok: true, data: null };
  } catch (e) {
    return fail(e);
  }
}
