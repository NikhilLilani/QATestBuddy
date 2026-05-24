-- =============================================================================
-- 0002 — fix infinite recursion in memberships RLS policy
-- =============================================================================
-- The original `memberships_self` policy used a subquery against memberships
-- itself, which Postgres treats as recursive. Replace with a simple
-- "you can see your own membership rows" policy. Workspace-scoped policies
-- still resolve via "workspace_id in (select ... from memberships where
-- user_id = auth.uid())" — that subquery is non-recursive once the
-- memberships policy doesn't loop back into itself.
-- =============================================================================

drop policy if exists memberships_self on public.memberships;

create policy memberships_self_select on public.memberships
  for select
  using (user_id = auth.uid());

-- Also allow workspace owners to manage memberships of their workspaces
-- (so they can invite / remove members later without bypassing RLS).
create policy memberships_owner_manage on public.memberships
  for all
  using (
    exists (
      select 1 from public.workspaces w
      where w.id = memberships.workspace_id and w.owner_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1 from public.workspaces w
      where w.id = memberships.workspace_id and w.owner_id = auth.uid()
    )
  );

-- Security-definer helper used by future code paths that need to verify
-- membership without triggering the per-row policy evaluation.
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
revoke all on function public.is_workspace_member(uuid) from public;
grant execute on function public.is_workspace_member(uuid) to authenticated, anon;
