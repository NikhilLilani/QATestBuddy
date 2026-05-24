-- =============================================================================
-- QAtestbuddy — initial schema (Supabase Postgres)
-- =============================================================================
-- Run via:  supabase db push   (or paste into SQL editor)
-- Assumes auth.users exists (provided by Supabase Auth).
-- =============================================================================

create extension if not exists "uuid-ossp";
create extension if not exists "pgcrypto";
create extension if not exists "vector";
-- pgsodium is enabled in Supabase by default for secret encryption.

-- -----------------------------------------------------------------------------
-- Helpers
-- -----------------------------------------------------------------------------
create or replace function public.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end$$;

-- -----------------------------------------------------------------------------
-- Workspaces & membership
-- -----------------------------------------------------------------------------
create table public.workspaces (
  id          uuid primary key default uuid_generate_v4(),
  name        text not null,
  slug        text not null unique,
  owner_id    uuid not null references auth.users(id) on delete cascade,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create trigger workspaces_touch before update on public.workspaces
  for each row execute function public.touch_updated_at();

create type public.membership_role as enum ('owner', 'admin', 'member');

create table public.memberships (
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id      uuid not null references auth.users(id) on delete cascade,
  role         public.membership_role not null default 'member',
  created_at   timestamptz not null default now(),
  primary key (workspace_id, user_id)
);
create index on public.memberships (user_id);

create table public.invites (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  email         text not null,
  role          public.membership_role not null default 'member',
  token         text not null unique,
  expires_at    timestamptz not null,
  created_at    timestamptz not null default now()
);

create table public.audit_log (
  id           bigserial primary key,
  workspace_id uuid references public.workspaces(id) on delete cascade,
  actor_id     uuid references auth.users(id) on delete set null,
  action       text not null,
  target       text,
  meta         jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now()
);

-- -----------------------------------------------------------------------------
-- Settings: API keys, integrations, bridge tokens
-- -----------------------------------------------------------------------------
create type public.api_provider as enum ('anthropic', 'openai', 'gemini', 'openrouter');

create table public.api_keys (
  id              uuid primary key default uuid_generate_v4(),
  workspace_id    uuid not null references public.workspaces(id) on delete cascade,
  provider        public.api_provider not null,
  name            text not null,
  encrypted_key   bytea not null,        -- pgsodium encrypted
  key_hint        text not null,         -- last 4 chars for UI
  last_used_at    timestamptz,
  created_by      uuid references auth.users(id) on delete set null,
  created_at      timestamptz not null default now()
);
create index on public.api_keys (workspace_id);

create type public.integration_type as enum ('jira', 'github');

create table public.integrations (
  id                uuid primary key default uuid_generate_v4(),
  workspace_id      uuid not null references public.workspaces(id) on delete cascade,
  type              public.integration_type not null,
  config            jsonb not null default '{}'::jsonb,
  encrypted_secret  bytea,
  status            text not null default 'active',
  last_sync_at      timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (workspace_id, type)
);
create trigger integrations_touch before update on public.integrations
  for each row execute function public.touch_updated_at();

create table public.bridge_tokens (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  user_id       uuid not null references auth.users(id) on delete cascade,
  token_hash    text not null unique,    -- sha256 of token
  label         text not null,
  last_seen_at  timestamptz,
  revoked_at    timestamptz,
  created_at    timestamptz not null default now()
);
create index on public.bridge_tokens (workspace_id);

-- -----------------------------------------------------------------------------
-- RAG sources, documents, chunks
-- -----------------------------------------------------------------------------
create type public.rag_kind as enum ('pdf', 'docx', 'md', 'json', 'git', 'jira_history', 'url');

create table public.rag_sources (
  id           uuid primary key default uuid_generate_v4(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  kind         public.rag_kind not null,
  name         text not null,
  source_uri   text,
  status       text not null default 'pending',
  bytes        bigint,
  created_by   uuid references auth.users(id) on delete set null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index on public.rag_sources (workspace_id);

create table public.rag_documents (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  rag_source_id uuid not null references public.rag_sources(id) on delete cascade,
  path          text not null,
  mime          text,
  hash          text,
  metadata      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);
create index on public.rag_documents (workspace_id);
create index on public.rag_documents (rag_source_id);

create table public.rag_chunks (
  id               uuid primary key default uuid_generate_v4(),
  workspace_id     uuid not null references public.workspaces(id) on delete cascade,
  rag_document_id  uuid not null references public.rag_documents(id) on delete cascade,
  ord              int not null,
  content          text not null,
  tokens           int,
  embedding        vector(768),
  tsv              tsvector generated always as (to_tsvector('english', content)) stored,
  created_at       timestamptz not null default now()
);
create index on public.rag_chunks (workspace_id);
create index on public.rag_chunks (rag_document_id);
create index rag_chunks_embedding_hnsw on public.rag_chunks using hnsw (embedding vector_cosine_ops);
create index rag_chunks_tsv_gin on public.rag_chunks using gin (tsv);

-- -----------------------------------------------------------------------------
-- Repos & code indexing
-- -----------------------------------------------------------------------------
create type public.repo_index_mode as enum ('full', 'on_demand');

create table public.repos (
  id              uuid primary key default uuid_generate_v4(),
  workspace_id    uuid not null references public.workspaces(id) on delete cascade,
  provider        text not null default 'github',
  owner           text not null,
  name            text not null,
  default_branch  text,
  index_mode      public.repo_index_mode not null default 'on_demand',
  last_indexed_at timestamptz,
  file_count      int,
  created_at      timestamptz not null default now(),
  unique (workspace_id, provider, owner, name)
);

create table public.repo_files (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  repo_id       uuid not null references public.repos(id) on delete cascade,
  path          text not null,
  sha           text,
  lang          text,
  bytes         int,
  indexed       boolean not null default false,
  created_at    timestamptz not null default now(),
  unique (repo_id, path)
);
create index on public.repo_files (workspace_id);

create table public.repo_chunks (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  repo_file_id  uuid not null references public.repo_files(id) on delete cascade,
  ord           int not null,
  content       text not null,
  embedding     vector(768),
  created_at    timestamptz not null default now()
);
create index on public.repo_chunks (workspace_id);
create index repo_chunks_embedding_hnsw on public.repo_chunks using hnsw (embedding vector_cosine_ops);

-- -----------------------------------------------------------------------------
-- QA artifacts: tickets, plans, cases, runs, bugs, generated tests, artifacts
-- -----------------------------------------------------------------------------
create table public.tickets (
  id           uuid primary key default uuid_generate_v4(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  jira_key     text not null,
  title        text not null,
  description  text,
  status       text,
  raw          jsonb not null default '{}'::jsonb,
  fetched_at   timestamptz not null default now(),
  unique (workspace_id, jira_key)
);

create table public.plans (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  ticket_id     uuid not null references public.tickets(id) on delete cascade,
  version       int not null default 1,
  content       jsonb not null,
  citations     jsonb not null default '[]'::jsonb,
  model         text,
  tokens_in     int default 0,
  tokens_out    int default 0,
  created_by    uuid references auth.users(id) on delete set null,
  created_at    timestamptz not null default now()
);
create index on public.plans (workspace_id);

create type public.case_status as enum ('draft', 'approved', 'exported');

create table public.cases (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  plan_id       uuid not null references public.plans(id) on delete cascade,
  ord           int not null,
  title         text not null,
  gherkin       text,
  fields        jsonb not null default '{}'::jsonb,
  citations     jsonb not null default '[]'::jsonb,
  status        public.case_status not null default 'draft',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create trigger cases_touch before update on public.cases
  for each row execute function public.touch_updated_at();
create index on public.cases (workspace_id);

create type public.run_kind as enum ('static', 'live', 'playwright_local', 'playwright_cloud');

create table public.runs (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  kind          public.run_kind not null,
  ticket_id     uuid references public.tickets(id) on delete set null,
  repo_id       uuid references public.repos(id) on delete set null,
  repo_ref      text,
  status        text not null default 'pending',
  started_at    timestamptz,
  finished_at   timestamptz,
  summary       jsonb not null default '{}'::jsonb,
  logs_uri      text,
  created_by    uuid references auth.users(id) on delete set null,
  created_at    timestamptz not null default now()
);
create index on public.runs (workspace_id);

create type public.bug_severity as enum ('critical', 'high', 'medium', 'low');

create table public.bugs (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  run_id        uuid not null references public.runs(id) on delete cascade,
  severity      public.bug_severity not null default 'medium',
  title         text not null,
  description   text,
  file_refs     jsonb not null default '[]'::jsonb,
  trace_uri     text,
  status        text not null default 'open',
  created_at    timestamptz not null default now()
);
create index on public.bugs (workspace_id);

create table public.generated_tests (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  run_id        uuid not null references public.runs(id) on delete cascade,
  repo_path     text not null,
  content       text not null,
  applied_at    timestamptz,
  created_at    timestamptz not null default now()
);
create index on public.generated_tests (workspace_id);

create table public.artifacts (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  run_id        uuid not null references public.runs(id) on delete cascade,
  kind          text not null,    -- trace | video | screenshot | log
  uri           text not null,
  bytes         bigint,
  created_at    timestamptz not null default now()
);
create index on public.artifacts (workspace_id);

-- =============================================================================
-- Row-Level Security
-- =============================================================================
-- Standard policy: a row is visible iff the caller is a member of its workspace.
-- Service-role bypasses RLS by design (used by backend jobs with audit_log).
-- =============================================================================

alter table public.workspaces       enable row level security;
alter table public.memberships      enable row level security;
alter table public.invites          enable row level security;
alter table public.audit_log        enable row level security;
alter table public.api_keys         enable row level security;
alter table public.integrations     enable row level security;
alter table public.bridge_tokens    enable row level security;
alter table public.rag_sources      enable row level security;
alter table public.rag_documents    enable row level security;
alter table public.rag_chunks       enable row level security;
alter table public.repos            enable row level security;
alter table public.repo_files       enable row level security;
alter table public.repo_chunks      enable row level security;
alter table public.tickets          enable row level security;
alter table public.plans            enable row level security;
alter table public.cases            enable row level security;
alter table public.runs             enable row level security;
alter table public.bugs             enable row level security;
alter table public.generated_tests  enable row level security;
alter table public.artifacts        enable row level security;

-- workspaces & memberships: caller must be a member
create policy workspaces_select on public.workspaces for select using (
  id in (select workspace_id from public.memberships where user_id = auth.uid())
);
create policy workspaces_owner_write on public.workspaces for all using (owner_id = auth.uid())
  with check (owner_id = auth.uid());

create policy memberships_self on public.memberships for select using (
  user_id = auth.uid()
    or workspace_id in (select workspace_id from public.memberships where user_id = auth.uid())
);

-- Macro: per-workspace isolation policy
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
      'create policy %I_isolation on public.%I for all using (
         workspace_id in (select workspace_id from public.memberships where user_id = auth.uid())
       ) with check (
         workspace_id in (select workspace_id from public.memberships where user_id = auth.uid())
       )',
      t, t
    );
  end loop;
end$$;

-- Auto-add the workspace creator as owner-member
create or replace function public.add_owner_membership() returns trigger
language plpgsql security definer as $$
begin
  insert into public.memberships (workspace_id, user_id, role)
  values (new.id, new.owner_id, 'owner')
  on conflict do nothing;
  return new;
end$$;

create trigger workspaces_add_owner after insert on public.workspaces
  for each row execute function public.add_owner_membership();
