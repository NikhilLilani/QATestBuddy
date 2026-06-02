-- =============================================================================
-- 0008_locator_index.sql — locator & route extraction tables (M3).
--
-- Populated by services.locator_extract whenever a dev repo is (re-)indexed.
-- Queried by services.locator_search to give the codegen agent real,
-- in-repo selectors instead of letting it invent CSS strings.
-- =============================================================================

-- ---------- locator_index ----------
-- One row per (file, line, attribute) candidate. We don't merge duplicates
-- across files — `<button data-testid="login">` appearing in both `Login.tsx`
-- and a storybook fixture is signal worth keeping separate (the codegen
-- agent can decide which file looks more "real").
create table public.locator_index (
  id              uuid primary key default uuid_generate_v4(),
  workspace_id    uuid not null references public.workspaces(id) on delete cascade,
  repo_file_id    uuid not null references public.repo_files(id) on delete cascade,
  line            int  not null,
  -- testid | role | text | aria | id | name | placeholder | href
  selector_kind   text not null,
  -- The literal attribute / text value, e.g. "login-btn" or "Sign in".
  selector_value  text not null,
  -- Best-effort component or HTML tag the locator was attached to.
  -- E.g. "button", "Input", "MyButton". Helps disambiguate identical labels.
  component       text,
  -- Best-effort visible label for the element (used as a fallback selector
  -- when the primary attribute isn't unique).
  label           text,
  -- Free-form sibling attributes for the LLM to read when picking a
  -- selector, e.g. {"type":"submit","aria-disabled":"false"}.
  context         jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now()
);
create index on public.locator_index (workspace_id);
create index on public.locator_index (repo_file_id);
create index on public.locator_index (workspace_id, selector_kind);

-- Full-text index over (selector_value, component, label) so the codegen
-- agent can search by intent ("login button", "submit form").
create index locator_index_search_gin
  on public.locator_index
  using gin (
    to_tsvector(
      'english',
      coalesce(selector_value, '') || ' ' ||
      coalesce(component, '')      || ' ' ||
      coalesce(label, '')
    )
  );

-- ---------- dev_repo_routes ----------
-- One row per route detected in the dev repo. Source files vary by framework
-- (`app/**/page.tsx` for Next 13+, `pages/**` for Next pages router, etc.).
create table public.dev_repo_routes (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  repo_id       uuid not null references public.repos(id) on delete cascade,
  path_pattern  text not null,             -- e.g. "/auth/sign-in", "/users/[id]"
  source_file   text not null,             -- repo-relative path
  line          int,
  framework     text,                      -- "next-app" | "next-pages" | "react-router" | "vue-router" | "html"
  created_at    timestamptz not null default now()
);
create index on public.dev_repo_routes (workspace_id);
create index on public.dev_repo_routes (repo_id);

-- ---------- RLS ----------
alter table public.locator_index    enable row level security;
alter table public.dev_repo_routes  enable row level security;

-- The definer-helper pattern from 0003 covers any table with a workspace_id
-- column, but the policies are added per-table. Mirror what 0003 does for
-- repo_files: select/insert/update/delete restricted to workspace members.
do $$
begin
  -- locator_index policies
  execute $sql$
    create policy locator_index_select on public.locator_index
      for select using (public.is_member(workspace_id));
  $sql$;
  execute $sql$
    create policy locator_index_modify on public.locator_index
      for all using (public.is_member(workspace_id))
                with check (public.is_member(workspace_id));
  $sql$;

  -- dev_repo_routes policies
  execute $sql$
    create policy dev_repo_routes_select on public.dev_repo_routes
      for select using (public.is_member(workspace_id));
  $sql$;
  execute $sql$
    create policy dev_repo_routes_modify on public.dev_repo_routes
      for all using (public.is_member(workspace_id))
                with check (public.is_member(workspace_id));
  $sql$;
exception when undefined_function then
  -- If is_member() isn't defined (older schema), skip — service-role bypasses
  -- RLS anyway and 0003 will install policies when applied.
  raise notice 'is_member() not present; skipping RLS policies for locator_index/dev_repo_routes';
end $$;
