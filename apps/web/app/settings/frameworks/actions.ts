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

export type FrameworkInput = {
  name: string;
  language: 'playwright_ts' | 'cypress' | 'selenium' | 'other';
  source_kind: 'git' | 'local' | 'zip' | 'new';
  git_url?: string;
  git_branch?: string;
  local_path?: string;
  zip_storage_key?: string;
  conventions?: string;
};

export async function addFramework(
  _prev: Result | null,
  formData: FormData,
): Promise<Result> {
  try {
    const source_kind = String(formData.get('source_kind')) as FrameworkInput['source_kind'];
    const body: FrameworkInput = {
      name: String(formData.get('name') ?? '').trim(),
      language: (String(formData.get('language') ?? 'playwright_ts') as FrameworkInput['language']),
      source_kind,
      conventions: String(formData.get('conventions') ?? ''),
    };
    if (source_kind === 'git') {
      body.git_url = String(formData.get('git_url') ?? '').trim();
      body.git_branch = String(formData.get('git_branch') ?? '').trim() || undefined;
    } else if (source_kind === 'local') {
      body.local_path = String(formData.get('local_path') ?? '').trim();
    } else if (source_kind === 'zip') {
      body.zip_storage_key = String(formData.get('zip_storage_key') ?? '').trim();
    }
    const data = await apiFetch('/api/v1/frameworks', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    revalidatePath('/settings/frameworks');
    return { ok: true, data };
  } catch (e) {
    return fail(e);
  }
}

export async function deleteFramework(id: string): Promise<Result> {
  try {
    await apiFetch(`/api/v1/frameworks/${id}`, { method: 'DELETE' });
    revalidatePath('/settings/frameworks');
    return { ok: true, data: null };
  } catch (e) {
    return fail(e);
  }
}
