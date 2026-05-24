/**
 * Workspace bootstrap and lookup helpers.
 *
 * On first sign-in, a workspace is created automatically and the user is
 * added as its owner-member (the membership trigger in 0001_init.sql handles
 * the membership row).
 */
import { createClient } from '@/lib/supabase/server';

export type Workspace = { id: string; name: string; slug: string; owner_id: string };

function slugify(input: string): string {
  return (
    input
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 40) || 'workspace'
  );
}

export async function bootstrapWorkspace(userId: string, email: string | null): Promise<Workspace> {
  const supabase = await createClient();

  // 1. Try to find an existing workspace via membership.
  const { data: memberships } = await supabase
    .from('memberships')
    .select('workspace_id, workspaces!inner(id, name, slug, owner_id)')
    .eq('user_id', userId)
    .limit(1);

  const existing = memberships?.[0]?.workspaces as unknown as Workspace | undefined;
  if (existing) return existing;

  // 2. None — create one. The DB trigger adds the owner membership.
  const base = slugify(email?.split('@')[0] ?? 'workspace');
  const slug = `${base}-${Math.random().toString(36).slice(2, 7)}`;
  const name = email ? `${email.split('@')[0]}'s workspace` : 'My workspace';

  const { data, error } = await supabase
    .from('workspaces')
    .insert({ name, slug, owner_id: userId })
    .select('id, name, slug, owner_id')
    .single();

  if (error || !data) {
    throw new Error(`Failed to bootstrap workspace: ${error?.message ?? 'unknown'}`);
  }
  return data as Workspace;
}

export async function getCurrentWorkspace(userId: string): Promise<Workspace | null> {
  const supabase = await createClient();
  const { data } = await supabase
    .from('memberships')
    .select('workspaces!inner(id, name, slug, owner_id)')
    .eq('user_id', userId)
    .limit(1)
    .single();
  return (data?.workspaces as unknown as Workspace) ?? null;
}
