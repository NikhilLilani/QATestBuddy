-- =============================================================================
-- 0006 — qa-bridge live execution
-- =============================================================================
-- Two tables backing Phase 4e:
--
--   bridge_jobs          — work queue. Web UI inserts a `pending` row; the
--                          qa-bridge CLI (authenticated with a bridge_token)
--                          claims rows by atomically flipping `pending` →
--                          `claimed`, then `running` → `done` / `failed`.
--
--   bridge_test_results  — per-test rows streamed back from the CLI. One row
--                          per Playwright test, append-only as the CLI posts
--                          updates. The UI tails these via SSE.
--
-- RLS: web-facing tables follow the standard `is_workspace_member` pattern.
-- Service role bypasses RLS (used by the FastAPI bridge endpoints).
-- =============================================================================

-- ────────────────────── bridge_jobs (work queue) ──────────────────────
create table if not exists public.bridge_jobs (
  id            uuid primary key default uuid_generate_v4(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  run_id        uuid not null references public.runs(id) on delete cascade,

  -- Lifecycle:
  --   pending  → row exists, no CLI has picked it up yet
  --   claimed  → a specific CLI (bridge_token_id) has claimed it
  --   running  → CLI is executing playwright
  --   done     → CLI posted final results
  --   failed   → CLI errored out before finishing
  --   cancelled → user cancelled from the UI
  status text not null default 'pending'
    check (status in ('pending','claimed','running','done','failed','cancelled')),

  -- Claim info — null until a CLI picks up the job
  bridge_token_id  uuid references public.bridge_tokens(id) on delete set null,
  claimed_at       timestamptz,
  started_at       timestamptz,
  finished_at      timestamptz,

  -- What the CLI needs to execute. We embed the bundle inline (small;
  -- typically <100KB) so the CLI gets it in one call without a separate
  -- fetch. `files` is the same shape as the codegen bundle.
  payload jsonb not null default '{}'::jsonb,

  -- Summary the CLI posts at the end (counts, durations).
  summary jsonb not null default '{}'::jsonb,

  -- If `status = failed`, the human-readable reason goes here.
  error_message text,

  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now()
);

create index if not exists bridge_jobs_workspace_idx
  on public.bridge_jobs (workspace_id, created_at desc);

-- Hot index for the CLI's `claim next pending` query.
create index if not exists bridge_jobs_pending_idx
  on public.bridge_jobs (workspace_id, created_at)
  where status = 'pending';

create index if not exists bridge_jobs_run_idx
  on public.bridge_jobs (run_id);

-- ──────────────────── bridge_test_results (per-test) ────────────────────
create table if not exists public.bridge_test_results (
  id          uuid primary key default uuid_generate_v4(),
  job_id      uuid not null references public.bridge_jobs(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,

  -- Identity: a Playwright test is uniquely identified by file + title path.
  test_id     text not null,        -- stable id: `${file}::${titlePath.join(' > ')}`
  file        text not null,
  title       text not null,
  project     text,                 -- e.g. "chromium", "firefox"

  -- Lifecycle:
  --   pending  → known about, not started
  --   running  → currently executing
  --   passed   → green
  --   failed   → red
  --   skipped  → orange
  --   timedOut → red, distinct
  status text not null default 'pending'
    check (status in ('pending','running','passed','failed','skipped','timedOut','interrupted')),

  duration_ms integer,              -- null while running
  retry       integer not null default 0,

  -- Failure detail
  error_message text,
  error_stack   text,

  -- Attachments — uris into Supabase Storage. Can be many per test.
  attachments jsonb not null default '[]'::jsonb,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  unique (job_id, test_id, retry)
);

create index if not exists bridge_results_job_idx
  on public.bridge_test_results (job_id, created_at);

create index if not exists bridge_results_workspace_idx
  on public.bridge_test_results (workspace_id);

-- ──────────────────── RLS ────────────────────
alter table public.bridge_jobs          enable row level security;
alter table public.bridge_test_results  enable row level security;

drop policy if exists bridge_jobs_member       on public.bridge_jobs;
drop policy if exists bridge_results_member    on public.bridge_test_results;

create policy bridge_jobs_member on public.bridge_jobs
  for all
  using (public.is_workspace_member(workspace_id))
  with check (public.is_workspace_member(workspace_id));

create policy bridge_results_member on public.bridge_test_results
  for all
  using (public.is_workspace_member(workspace_id))
  with check (public.is_workspace_member(workspace_id));

-- ──────────────────── auto-bump updated_at ────────────────────
create or replace function public.bump_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end
$$;

drop trigger if exists bridge_results_updated on public.bridge_test_results;
create trigger bridge_results_updated
  before update on public.bridge_test_results
  for each row execute function public.bump_updated_at();
