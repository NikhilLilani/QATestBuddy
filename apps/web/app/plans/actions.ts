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

export async function deletePlanAction(id: string): Promise<Result> {
  try {
    await apiFetch(`/api/v1/plans/${id}`, { method: 'DELETE' });
    revalidatePath('/plans');
    revalidatePath('/dashboard');
    return { ok: true, data: null };
  } catch (e) {
    return fail(e);
  }
}

export async function deleteRunAction(id: string): Promise<Result> {
  try {
    await apiFetch(`/api/v1/runs/${id}`, { method: 'DELETE' });
    revalidatePath('/runs');
    revalidatePath('/dashboard');
    return { ok: true, data: null };
  } catch (e) {
    return fail(e);
  }
}

export async function bulkDeletePlansAction(
  ids: string[],
): Promise<Result<{ deleted: number }>> {
  try {
    const data = await apiFetch<{ deleted: number }>('/api/v1/plans/bulk-delete', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    });
    revalidatePath('/plans');
    revalidatePath('/dashboard');
    return { ok: true, data };
  } catch (e) {
    return fail(e);
  }
}

export async function bulkDeleteRunsAction(
  ids: string[],
): Promise<Result<{ deleted: number }>> {
  try {
    const data = await apiFetch<{ deleted: number }>('/api/v1/runs/bulk-delete', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    });
    revalidatePath('/runs');
    revalidatePath('/dashboard');
    return { ok: true, data };
  } catch (e) {
    return fail(e);
  }
}

export async function bulkDeleteCasesAction(
  ids: string[],
): Promise<Result<{ deleted: number }>> {
  try {
    const data = await apiFetch<{ deleted: number }>('/api/v1/cases/bulk-delete', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    });
    return { ok: true, data };
  } catch (e) {
    return fail(e);
  }
}
