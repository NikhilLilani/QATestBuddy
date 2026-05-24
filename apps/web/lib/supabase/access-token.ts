'use client';
import { createClient } from './client';

/**
 * Get the current Supabase access token from the browser session.
 * Used by client components that call the FastAPI backend directly with a
 * Bearer header (e.g. SSE streams that can't use EventSource because it
 * lacks header support).
 */
export async function getAccessToken(): Promise<string | null> {
  const supabase = createClient();
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}
