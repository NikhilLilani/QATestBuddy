-- =============================================================================
-- 0004 — Frameworks: stored automation framework profiles per workspace.
-- =============================================================================
-- A workspace can have many framework profiles (e.g. "Web App POM",
-- "Mobile E2E", "API tests"). Each plan's codegen run picks exactly one.
-- =============================================================================

create type public.framework_kind as enum ('git', 'local', 'zip', 'new');
create type public.framework_language as enum ('playwright_ts', 'cypress', 'selenium', 'other');

create table public.frameworks (
  id              uuid primary key default uuid_generate_v4(),
  workspace_id    uuid not null references public.workspaces(id) on delete cascade,
  name            text not null,
  language        public.framework_language not null default 'playwright_ts',
  source_kind     public.framework_kind not null,
  -- Source-specific location:
  --   git:   git_url (full https url; optional branch)
  --   local: local_path (just stored, can't be read by backend)
  --   zip:   stored in supabase storage at zip_storage_key
  --   new:   no source, conventions used to scaffold
  git_url         text,
  git_branch      text,
  local_path      text,
  zip_storage_key text,
  conventions     text not null default '',  -- free-form text shown to the codegen agent
  -- Cached metadata gathered when we successfully read the source:
  detected_signals jsonb not null default '{}'::jsonb,  -- { stack: [...], routes: [...], components: [...] }
  last_inspected_at timestamptz,
  status          text not null default 'active',
  created_by      uuid references auth.users(id) on delete set null,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (workspace_id, name)
);
create trigger frameworks_touch before update on public.frameworks
  for each row execute function public.touch_updated_at();
create index on public.frameworks (workspace_id);

-- RLS: same workspace-isolation pattern as the rest
alter table public.frameworks enable row level security;
create policy frameworks_isolation on public.frameworks for all
  using (public.is_workspace_member(workspace_id))
  with check (public.is_workspace_member(workspace_id));
