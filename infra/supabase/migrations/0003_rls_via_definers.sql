-- =============================================================================
-- 0003 — break all RLS recursion by routing membership checks through
-- security-definer functions (which bypass RLS by design).
-- =============================================================================

-- 1. Helper functions ---------------------------------------------------------

create or replace function public.is_workspace_member(ws uuid)
returns boolean
language sql
security definer
stable
set search_path = public
as $$
  select exists (
    select 1 from public.memberships
    where workspace_id = ws and user_id = auth.uid()
  );
$$;

create or replace function public.is_workspace_owner(ws uuid)
returns boolean
language sql
security definer
stable
set search_path = public
as $$
  select exists (
    select 1 from public.workspaces
    where id = ws and owner_id = auth.uid()
  );
$$;

revoke all on function public.is_workspace_member(uuid) from public;
revoke all on function public.is_workspace_owner(uuid)  from public;
grant execute on function public.is_workspace_member(uuid) to authenticated, anon;
grant execute on function public.is_workspace_owner(uuid)  to authenticated, anon;

-- 2. Drop every policy we may have created earlier ----------------------------

drop policy if exists workspaces_select       on public.workspaces;
drop policy if exists workspaces_owner_write  on public.workspaces;
drop policy if exists memberships_self        on public.memberships;
drop policy if exists memberships_self_select on public.memberships;
drop policy if exists memberships_owner_manage on public.memberships;

do $$
declare
  t text;
  tables text[] := array[
    'invites','audit_log','api_keys','integrations','bridge_tokens',
    'rag_sources','rag_documents','rag_chunks',
    'repos','repo_files','repo_chunks',
    'tickets','plans','cases','runs','bugs','generated_tests','artifacts'
  ];
begin
  foreach t in array tables loop
    execute format('drop policy if exists %I_isolation on public.%I', t, t);
  end loop;
end$$;

-- 3. Recreate policies using only definer functions ---------------------------

-- workspaces: visible if owner OR member, owners can do anything.
create policy workspaces_owner_all on public.workspaces
  for all
  using (owner_id = auth.uid())
  with check (owner_id = auth.uid());

create policy workspaces_member_select on public.workspaces
  for select
  using (public.is_workspace_member(id));

-- memberships: you see your own rows; owners manage memberships on their workspaces.
create policy memberships_self_select on public.memberships
  for select
  using (user_id = auth.uid());

create policy memberships_owner_manage on public.memberships
  for all
  using (public.is_workspace_owner(workspace_id))
  with check (public.is_workspace_owner(workspace_id));

-- All other workspace-scoped tables: isolation via is_workspace_member().
do $$
declare
  t text;
  tables text[] := array[
    'invites','audit_log','api_keys','integrations','bridge_tokens',
    'rag_sources','rag_documents','rag_chunks',
    'repos','repo_files','repo_chunks',
    'tickets','plans','cases','runs','bugs','generated_tests','artifacts'
  ];
begin
  foreach t in array tables loop
    execute format(
      'create policy %I_isolation on public.%I for all
         using (public.is_workspace_member(workspace_id))
         with check (public.is_workspace_member(workspace_id))',
      t, t
    );
  end loop;
end$$;
