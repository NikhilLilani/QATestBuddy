-- =============================================================================
-- 0005 — Soft delete on plans / cases / runs
-- =============================================================================
-- Adds a nullable `deleted_at` to the three artifact tables and partial
-- indexes so non-deleted reads stay fast. No row is ever hard-deleted by the
-- app; recovery / audit / undo all work.
-- =============================================================================

alter table public.plans add column if not exists deleted_at timestamptz;
alter table public.cases add column if not exists deleted_at timestamptz;
alter table public.runs  add column if not exists deleted_at timestamptz;

-- Partial indexes: only living rows. Speeds up the common "list active" reads.
create index if not exists plans_active_idx
  on public.plans (workspace_id, created_at desc)
  where deleted_at is null;

create index if not exists cases_active_idx
  on public.cases (plan_id, ord)
  where deleted_at is null;

create index if not exists runs_active_idx
  on public.runs (workspace_id, created_at desc)
  where deleted_at is null;
