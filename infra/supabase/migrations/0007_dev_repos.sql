-- =============================================================================
-- 0007_dev_repos.sql — extend repo* tables for the M2 dev-repo connector.
--
-- The repos / repo_files / repo_chunks tables were stubbed in 0001_init.sql but
-- never wired to code. This migration adds the columns needed for actual
-- ingestion (PAT for private repos, zip uploads, BM25 indexing on chunks).
-- =============================================================================

-- ---------- new enum for how the repo was supplied ----------
create type public.repo_source_kind as enum ('git_public', 'git_pat', 'zip');

-- ---------- repos: add credentials + source-kind ----------
alter table public.repos
  add column if not exists source_kind          public.repo_source_kind not null default 'git_public',
  -- Encrypted PAT (Fernet bytes from app.core.crypto). Nullable for public/zip.
  add column if not exists access_token         bytea,
  -- Last 4 chars of the PAT, shown in the UI so the user can identify which
  -- token is stored without decrypting it.
  add column if not exists access_token_hint    text,
  -- Storage key for uploaded zips (filesystem path or S3 key). Nullable for git.
  add column if not exists zip_storage_key      text,
  -- Free-text notes from the user about this repo, surfaced in prompts.
  add column if not exists notes                text not null default '',
  -- Last-error message from the most recent ingest. Cleared on success.
  add column if not exists last_error           text;

-- Zip uploads don't have owner/name in the traditional sense — relax the NOT
-- NULL so we can store them with synthetic placeholders.
alter table public.repos alter column owner drop not null;
alter table public.repos alter column name  drop not null;

-- ---------- repo_chunks: add tsvector + GIN for BM25 ----------
-- Mirrors the rag_chunks design so repo_retrieve can do hybrid search.
alter table public.repo_chunks
  add column if not exists tsv tsvector
    generated always as (to_tsvector('english', content)) stored;

create index if not exists repo_chunks_tsv_gin
  on public.repo_chunks using gin (tsv);

-- ---------- repo_files: track content hash so re-index can skip unchanged ----------
-- The `sha` column already exists; add a content-hash for files we fetched
-- without a git SHA (e.g. from a zip upload).
alter table public.repo_files
  add column if not exists content_hash text;

create index if not exists repo_files_content_hash
  on public.repo_files (workspace_id, content_hash);

-- ---------- RLS policies — extend the same definer-function pattern ----------
-- The tables already had row security enabled in 0001; the definer-function
-- helpers in 0003 cover them. Nothing extra to do here.

-- ---------- updated_at trigger ----------
-- 0001 didn't add one for `repos`. Add it now since callers will mutate.
create or replace function public.touch_repos_updated()
returns trigger language plpgsql as $$
begin
  -- last_indexed_at is set explicitly by the ingest job; updated_at would
  -- be a useful audit field but the table doesn't have one. Skip for now;
  -- this function is here as a placeholder for future audit columns.
  return new;
end $$;
