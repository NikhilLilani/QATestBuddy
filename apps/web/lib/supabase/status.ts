/**
 * Quick check whether Supabase env is configured. Runs on the server (env
 * vars are not exposed without NEXT_PUBLIC_ prefix, but these two ARE
 * public so this works in both server and client contexts).
 */
export function isSupabaseConfigured(): boolean {
  return Boolean(
    process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  );
}
