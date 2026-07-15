'use server';

import { revalidatePath } from 'next/cache';
import { apiFetch, ApiError } from '@/lib/api';

export type Result<T = unknown> = { ok: true; data: T } | { ok: false; error: string };

function fail<T>(e: unknown): Result<T> {
  if (e instanceof ApiError) {
    const body = e.body as { detail?: string } | string | null;
    const msg = typeof body === 'string' ? body : body?.detail ?? e.message;
    return { ok: false, error: msg };
  }
  return { ok: false, error: e instanceof Error ? e.message : String(e) };
}

export async function updateBugStatusAction(
  id: string,
  status: 'open' | 'resolved',
): Promise<Result<{ id: string; status: string }>> {
  try {
    const data = await apiFetch<{ id: string; status: string }>(`/api/v1/bugs/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    });
    revalidatePath('/bugs');
    revalidatePath(`/bugs/${id}`);
    return { ok: true, data };
  } catch (e) {
    return fail(e);
  }
}
