# QAtestbuddy — Architecture

> **Working name:** `QAtestbuddy` (placeholder — rebrandable via `packages/brand/brand.config.ts`).
> **Status:** Pre-Phase 0. This is the source-of-truth design doc; update it whenever a decision changes.

---

## 1. Product summary

Multi-tenant SaaS for AI-assisted QA. Connect Jira + GitHub, drop in PRDs/historical docs, and get **grounded** (RAG-backed, hallucination-resistant) artifacts:

1. Test plans
2. Test cases
3. Static bug analysis from code + ticket
4. Live Playwright (TypeScript) automation generated against the user's repo conventions, run locally via a small CLI bridge — optional cloud run in a Docker sandbox
5. Live E2E bug discovery against a deployed app URL

Free to operate: **BYOK** for LLM inference, free tiers for every infra dependency.

---

## 2. Non-negotiable principles

| # | Principle | Mechanism |
|---|---|---|
| 1 | **No hallucination** | Every agent output must carry `citations[]` referencing real source IDs (Jira keys, chunk IDs, file:line). Outputs missing citations are flagged. |
| 2 | **Ask, don't invent** | Every agent has a `clarify(question)` tool. If confidence < threshold or a required schema field is unknown → surface a question to the user. |
| 3 | **Rebrand-safe** | Name/logo/domain/email come from `brand.config.ts` + env vars. No brand strings hardcoded. |
| 4 | **Tenant isolation** | Postgres RLS on `workspace_id` for every row. No backend code may bypass RLS without an audit-logged service role call. |
| 5 | **Local-first execution** | Playwright runs on the user's machine by default. Cloud runner is opt-in. |
| 6 | **Zero recurring cost** | Every dependency must have a free tier sufficient for MVP. No paid line items. |
| 7 | **Encrypted secrets** | BYOK API keys + Jira PAT + GitHub tokens encrypted at rest (Supabase Vault / pgsodium). |

---

## 3. Tech stack

| Layer | Choice | Free? |
|---|---|---|
| Frontend | Next.js 15 (App Router, RSC) + TypeScript + Tailwind + shadcn/ui + Framer Motion | ✅ Vercel free |
| Backend | Python 3.12 + FastAPI + Pydantic v2 + Uvicorn | ✅ Fly.io / Railway free |
| Auth | Supabase Auth (Google + Microsoft `common` + email/password + OTP + reset) | ✅ |
| DB | Supabase Postgres + RLS | ✅ 500 MB free |
| Vector | pgvector (same Postgres) | ✅ |
| Storage | Supabase Storage | ✅ 1 GB free |
| Queue / jobs | Postgres `pgmq` (preferred — one less service) | ✅ |
| LLM | BYOK: Anthropic / OpenAI / Gemini / OpenRouter | User-paid |
| Embeddings | BYOK (Gemini `text-embedding-004` free tier preferred) | User-paid |
| Reranker | BGE-reranker-base, runs in backend Python (CPU OK) | ✅ |
| MCP | Atlassian Jira MCP via Jira REST API + PAT (user-provided) | ✅ |
| Repo | GitHub OAuth App + Octokit (read-only scopes) | ✅ |
| Playwright runner | `qa-bridge` npm CLI (local) + Playwright Docker image (optional cloud) | ✅ |
| Email (dev) | **Mailpit** in Docker (web UI on `localhost:8025`) | ✅ |
| Email (prod, later) | Resend free tier (100/day) once a domain exists | ✅ |
| Observability | Sentry free + Logfire free | ✅ |
| Hosting | Vercel (web) + Fly.io (api) + Supabase (data) | ✅ |
| CI | GitHub Actions free tier | ✅ |

---

## 4. Monorepo layout (Turborepo + pnpm)

```
qa-platform/
├── apps/
│   ├── web/                     # Next.js 15 frontend
│   │   ├── app/
│   │   │   ├── (marketing)/     # Landing, pricing, docs (ISR, SEO)
│   │   │   ├── (auth)/          # Sign in / sign up / OTP / reset
│   │   │   └── (app)/           # Authed app shell
│   │   │       ├── workspaces/[ws]/
│   │   │       │   ├── dashboard/
│   │   │       │   ├── tickets/[key]/
│   │   │       │   ├── plans/[id]/
│   │   │       │   ├── cases/[id]/
│   │   │       │   ├── runs/[id]/
│   │   │       │   ├── bugs/[id]/
│   │   │       │   ├── rag/
│   │   │       │   └── settings/
│   │   └── lib/
│   ├── api/                     # FastAPI backend
│   │   ├── app/
│   │   │   ├── routers/         # REST + SSE endpoints
│   │   │   ├── agents/          # Planner, CaseAuthor, CodeReader, BugHunter, PWCodegen
│   │   │   ├── services/        # jira, github, rag, llm, codegen, runner
│   │   │   ├── orchestrator/    # State machine, budgets, streaming
│   │   │   ├── mcp/             # MCP clients
│   │   │   ├── db/              # SQLAlchemy / asyncpg + migrations (Alembic)
│   │   │   └── core/            # config, security, crypto, telemetry
│   └── bridge-cli/              # `qa-bridge` npm package (TypeScript)
│       ├── src/
│       │   ├── commands/        # login, link, run, codegen
│       │   └── ws-client.ts     # WS back to api
│       └── package.json         # bin: qa-bridge
├── packages/
│   ├── brand/                   # brand.config.ts + assets/ (logo, OG)
│   ├── shared-types/            # Zod + Pydantic-equivalent schemas (generated)
│   ├── ui/                      # shadcn re-exports + custom primitives
│   └── eslint-config/
├── infra/
│   ├── docker-compose.dev.yml   # Mailpit, Postgres (optional), pgmq
│   └── supabase/                # migrations, RLS policies, seed
├── .github/workflows/           # CI: lint, type, test, build, e2e
├── turbo.json
├── pnpm-workspace.yaml
└── ARCHITECTURE.md              # this file
```

---

## 5. System architecture

```
┌──────────────────── Vercel ─────────────────────┐
│  Next.js 15                                     │
│  ├─ (marketing)  ISR, SEO, JSON-LD, sitemap     │
│  ├─ (auth)       Supabase Auth UI flows         │
│  └─ (app)        RSC + SSE streams from API     │
└───────┬────────────────────────┬────────────────┘
        │ supabase-js (auth/db)  │ REST + SSE (Bearer JWT)
        ▼                        ▼
┌──────────────┐         ┌──────────────────────────┐
│  Supabase    │◄────────│  FastAPI (Fly.io)        │
│  Auth        │  SQL    │  - Routers (REST + SSE)  │
│  Postgres    │         │  - Orchestrator (FSM)    │
│  + RLS       │         │  - Agents (5)            │
│  + pgvector  │         │  - Services             │
│  + Storage   │         │  - MCP clients           │
│  + pgmq      │         │  - Crypto (pgsodium)     │
└──────────────┘         └──┬──────────────┬────────┘
                            │              │
                  ┌─────────┘              └──────────┐
                  ▼                                   ▼
        ┌─────────────────┐               ┌────────────────────────┐
        │ Jira REST (PAT) │               │ qa-bridge (user laptop)│
        │ GitHub API      │               │  WS ⇄ api (workspace   │
        │ User's LLM API  │               │  token-scoped)         │
        └─────────────────┘               │  Runs Playwright local │
                                          └────────────────────────┘
```

---

## 6. Data model (Postgres + RLS)

> All app tables include `workspace_id uuid not null` and have an RLS policy: `workspace_id in (select workspace_id from memberships where user_id = auth.uid())`.

### Core

```
users                  -- managed by Supabase Auth (auth.users)
workspaces             id, name, slug, owner_id, created_at
memberships            workspace_id, user_id, role (owner|admin|member), created_at
invites                workspace_id, email, role, token, expires_at
audit_log              workspace_id, actor_id, action, target, meta jsonb, created_at
```

### Settings / integrations (secrets encrypted via pgsodium)

```
api_keys               workspace_id, provider (anthropic|openai|gemini|openrouter),
                       name, encrypted_key bytea, last_used_at, created_by
integrations           workspace_id, type (jira|github),
                       config jsonb, encrypted_secret bytea, status, last_sync_at
bridge_tokens          workspace_id, user_id, token_hash, label, last_seen_at
```

### RAG

```
rag_sources            workspace_id, kind (pdf|docx|md|json|git|jira_history|url),
                       name, source_uri, status, bytes, created_by
rag_documents          rag_source_id, path, mime, hash, metadata jsonb
rag_chunks             rag_document_id, ord, content text, tokens int,
                       embedding vector(768), tsv tsvector
                       -- HNSW index on embedding, GIN on tsv
```

### QA artifacts

```
tickets                workspace_id, jira_key, title, description, status, raw jsonb, fetched_at
plans                  workspace_id, ticket_id, version, content jsonb,
                       citations jsonb, model, tokens_in, tokens_out, created_by
cases                  workspace_id, plan_id, ord, title, gherkin text, fields jsonb,
                       citations jsonb, status (draft|approved|exported)
runs                   workspace_id, kind (static|live|playwright_local|playwright_cloud),
                       ticket_id, repo_ref, status, started_at, finished_at,
                       summary jsonb, logs_uri
bugs                   workspace_id, run_id, severity, title, description,
                       file_refs jsonb, trace_uri, status
generated_tests        workspace_id, run_id, repo_path, content text, applied_at
artifacts              workspace_id, run_id, kind (trace|video|screenshot|log), uri, bytes
```

### Repo indexing

```
repos                  workspace_id, provider, owner, name, default_branch,
                       index_mode (full|on_demand), last_indexed_at, file_count
repo_files             repo_id, path, sha, lang, bytes, indexed bool
repo_chunks            repo_file_id, ord, content text, embedding vector(768)
                       -- only populated when index_mode = full
```

### Jobs (pgmq)

Queues: `ingest`, `agent_run`, `repo_index`, `playwright_cloud`.

### RLS policy template

```sql
alter table <t> enable row level security;
create policy <t>_isolation on <t>
  using (workspace_id in (
    select workspace_id from memberships where user_id = auth.uid()
  ));
```

Service-role mutations (system jobs) go through a wrapper that logs to `audit_log`.

---

## 7. API surface (FastAPI)

All under `/api/v1`. Auth: `Authorization: Bearer <supabase JWT>` (or bridge token for `/bridge/*`). Workspace context: `X-Workspace-Id` header, validated against memberships.

### Workspaces & members

```
POST   /workspaces
GET    /workspaces
POST   /workspaces/{id}/invites
POST   /workspaces/{id}/members/{user_id}/role
```

### Settings

```
POST   /settings/api-keys              { provider, name, key }
GET    /settings/api-keys              # masked
DELETE /settings/api-keys/{id}
POST   /settings/api-keys/{id}/test    # validates with provider

POST   /integrations/jira              { base_url, email, pat }
POST   /integrations/jira/test
POST   /integrations/github/oauth/start
GET    /integrations/github/oauth/callback
POST   /bridge/tokens                  { label } -> token (shown once)
```

### RAG

```
POST   /rag/sources                    multipart (pdf/docx/md/json) or { git_url } or { url }
GET    /rag/sources
DELETE /rag/sources/{id}
POST   /rag/sources/{id}/reindex
GET    /rag/search?q=...               # debug
```

### Jira / tickets

```
GET    /jira/issues?jql=...
GET    /jira/issues/{key}              # fetched + cached as ticket
```

### Agent workflows (all streamed via SSE)

```
POST   /plans                          { ticket_key }                   -> SSE
POST   /plans/{id}/cases               { }                              -> SSE
POST   /cases/{id}/regenerate          { feedback }                     -> SSE
POST   /cases/{id}/export-to-jira

POST   /bugs/static                    { ticket_key, repo_ref }         -> SSE
POST   /bugs/live                      { ticket_key, app_url }          -> SSE
POST   /codegen/playwright             { case_ids, repo_ref, target }   -> SSE
                                       # target: local | cloud
```

### Runs / artifacts

```
GET    /runs                           ?kind=&status=
GET    /runs/{id}
GET    /runs/{id}/events               # SSE replay
GET    /artifacts/{id}                 # signed Supabase URL
```

### Bridge (CLI <-> api)

```
WS     /bridge/ws                      # auth: bridge token
       events: hello, run.start, run.file.write, run.exec.cmd,
               run.exec.stdout, run.result, codegen.locator.request
```

### Clarify channel

Any SSE stream may emit `event: clarify { id, question, schema }`. UI shows modal; client replies with `POST /clarifications/{id} { answer }` and the agent resumes.

---

## 8. Agent contracts

Common envelope:

```ts
type AgentOutput<T> = {
  data: T;
  citations: Citation[];          // required, non-empty
  confidence: number;             // 0..1
  clarifications_used: number;
  tokens: { in: number; out: number };
};
type Citation =
  | { kind: 'jira'; key: string; field: string }
  | { kind: 'rag_chunk'; chunk_id: string }
  | { kind: 'repo_file'; repo: string; path: string; line_start: number; line_end: number };
```

### PlannerAgent

In: `{ ticket, rag_hits }` → Out:
```ts
{ scope, in_scope[], out_of_scope[], risks[], environments[], data_needs[], exit_criteria[] }
```

### CaseAuthorAgent

In: `{ plan, rag_hits }` → Out:
```ts
TestCase[] = {
  id, title, preconditions[], steps: { action, expected }[],
  priority, type (functional|regression|edge|negative|a11y|perf),
  data, tags[]
}
```
Hard schema; missing required field → `clarify`.

### CodeReaderAgent

Tools: `repo.list`, `repo.read(path)`, `repo.search(query)`.
In: `{ ticket, repo_ref }` → Out: `{ affected_files[], hotspots[], notes[] }` — every claim cites file:line.

### BugHunterAgent

Modes:
- **static**: needs CodeReader output; emits findings with code citations.
- **live**: drives a sandboxed Playwright probe against `app_url`; failures become bugs with `trace_uri`.

### PWCodegenAgent

Tools: `repo.read`, `bridge.locator_probe(url, hint)` (asks user to run `npx playwright codegen` via bridge), `repo.list_tests`.
In: `{ cases, repo_ref, target }` → emits `repo_path + content` events; bridge writes files locally and optionally executes.

---

## 9. Orchestrator

Tiny Python state machine — no LangChain/LangGraph. Each workflow is a class with typed steps, per-step token + tool-call budgets, retries with backoff, and SSE event emission. State persisted in `runs` + `pgmq` for resumability.

Hallucination guards enforced in middleware:

1. **Citation validator** — rejects any output where `citations` is empty or references unknown IDs.
2. **Schema validator** — Pydantic strict; missing required field triggers `clarify`.
3. **Confidence gate** — `confidence < 0.6` ⇒ `clarify` instead of emitting.

---

## 10. RAG pipeline

```
ingest → detect mime → extract text →
  if code: tree-sitter AST chunks (function-level)
  else:    semantic split (800 tok, 100 overlap) →
embed (BYOK provider) → store chunks + tsv → done.

retrieve(q, k=24) :
  vec_hits   = pgvector cosine top-50 (RLS-scoped)
  bm25_hits  = ts_rank on tsv top-50
  fused      = reciprocal-rank-fusion
  reranked   = BGE-reranker → top-k
return chunks with source metadata for citation
```

Repo indexing strategy auto-picks per repo:
- `< 5k files AND < 50 MB` → full index (`rag_sources` + `repo_chunks`).
- larger → `on_demand`: agent uses `repo.read`/`repo.search` tools that hit GitHub API live, no precomputed embeddings.

---

## 11. Frontend specifics

### Marketing (SEO)

- `app/(marketing)/` with ISR (revalidate 1h).
- Per-route `generateMetadata` from `brand.config.ts`.
- JSON-LD `SoftwareApplication` + `FAQPage` + `BreadcrumbList`.
- OG images via `@vercel/og` (dynamic, branded).
- `sitemap.ts`, `robots.ts`, canonical URLs.
- Lighthouse targets: Performance ≥ 95, A11y ≥ 95, SEO = 100.
- Hero, "how it works" (3-step animated with Framer Motion), feature grid, interactive demo (sandboxed Jira fixture, no signup), pricing (Free), FAQ, footer.

### App shell

- Workspace switcher (top-left), command palette (⌘K), breadcrumbs, dark mode (next-themes), toaster (sonner).
- Streaming token UI for every LLM call (renders SSE deltas).
- Citation pills inline in generated artifacts → click to peek source.
- Diff viewer (monaco) for generated test files before write.
- A11y: WCAG AA, focus rings, keyboard navigable, prefers-reduced-motion respected.

### Auth flows

- `/sign-in` — Google, Microsoft (`common` tenant), email/password.
- `/sign-up` — same providers; email path requires 6-digit OTP from Supabase, then onboarding (create workspace).
- `/reset` — OTP-based reset.
- Dev: emails caught by Mailpit at `http://localhost:8025`.

---

## 12. `qa-bridge` CLI

```
qa-bridge login                     # paste workspace token from Settings -> opens WS
qa-bridge link                      # detects repo, registers repo_ref
qa-bridge run <runId>               # executes a run streamed from server
qa-bridge codegen <url>             # wraps `npx playwright codegen` and returns selectors
qa-bridge status
qa-bridge logout
```

Implementation: Node 20+, `commander`, `ws`, `execa`, `chokidar`. Distributed via npm; `npx qa-bridge` works without install.

Sandbox: only writes inside the linked repo path; refuses writes outside; logs every command before execution; user can set `--dry-run`.

---

## 13. Security

- All BYOK secrets encrypted via Supabase pgsodium (envelope encryption). Key never returned in API; only `key_hint` (last 4).
- JWT auth (Supabase) → backend verifies with JWKS.
- Bridge tokens: random 32-byte, stored as `sha256` hash, scoped to one workspace + one user, revocable.
- RLS on every table; service role usage audited.
- GitHub OAuth: minimum scopes (`repo:read`, `read:user`). Stored encrypted.
- Rate limits per workspace per route (slowapi).
- CSP, HSTS, secure cookies, OWASP headers via Next middleware.
- No telemetry of user content; only metadata (counts, latencies) to Sentry/Logfire.

---

## 14. Branding & rebrand procedure

`packages/brand/brand.config.ts`:

```ts
export const brand = {
  name: 'QAtestbuddy',
  shortName: 'QAtb',
  tagline: 'Grounded AI for QA — plans, cases, and Playwright tests from your Jira.',
  domain: 'localhost:3000',        // -> qatestbuddy.vercel.app -> final domain
  supportEmail: 'support@localhost',
  themeColor: '#0EA5E9',
  logoLight: './assets/logo-light.svg',
  logoDark: './assets/logo-dark.svg',
  ogImage: './assets/og.png',
} as const;
```

**To rebrand later:** edit this file, swap PNG/SVG assets, rename OAuth apps in Google/Microsoft/GitHub consoles. No code refactor.

---

## 15. `.env.example`

### `apps/web/.env.example`

```
NEXT_PUBLIC_APP_NAME=QAtestbuddy
NEXT_PUBLIC_APP_URL=http://localhost:3000
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_API_URL=http://localhost:8000
SENTRY_DSN=
```

### `apps/api/.env.example`

```
APP_NAME=QAtestbuddy
APP_URL=http://localhost:3000
API_URL=http://localhost:8000
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_JWT_SECRET=
DATABASE_URL=postgresql+asyncpg://...
PGSODIUM_KEY_ID=
GITHUB_OAUTH_CLIENT_ID=
GITHUB_OAUTH_CLIENT_SECRET=
SMTP_HOST=localhost
SMTP_PORT=1025                # Mailpit
SENTRY_DSN=
LOGFIRE_TOKEN=
ALLOWED_ORIGINS=http://localhost:3000
```

### `apps/bridge-cli/.env.example`

```
QATB_API_URL=http://localhost:8000
```

---

## 16. Phase 0 — Day-1 setup checklist

1. `pnpm dlx create-turbo@latest qa-platform` → adopt layout in §4.
2. `pnpm add -w -D typescript eslint prettier @types/node turbo`.
3. Scaffold `apps/web` (Next 15, app router, Tailwind, shadcn init).
4. Scaffold `apps/api` (FastAPI + Pydantic v2 + asyncpg + Alembic).
5. Scaffold `apps/bridge-cli` (TS, commander, ws, execa).
6. Create `packages/brand` with `brand.config.ts` + placeholder logo.
7. Create Supabase project (free) → run migrations from `infra/supabase/migrations/0001_init.sql` (tables + RLS from §6).
8. `docker compose -f infra/docker-compose.dev.yml up -d` → Mailpit on `:8025`.
9. Wire Supabase Auth in `apps/web`: enable Google + Microsoft (`common`) + email/password + OTP.
10. CI: GitHub Actions for lint, typecheck, unit tests, build.
11. Smoke test: sign up locally, see OTP in Mailpit, create workspace, land on dashboard.

---

## 17. Phase plan (recap)

| Phase | Scope | Exit criteria |
|---|---|---|
| 0 | Foundations (§16) | App boots, auth works, empty dashboard renders |
| 1 | Auth + Settings + Workspaces | All 3 providers + OTP + reset; encrypted API key storage; Jira PAT + GitHub OAuth saved |
| 2 (MVP) | Jira → Plan → Cases | Pick ticket → streamed plan + cases with citations + clarify loop; export to Jira/MD/CSV |
| 3 | RAG ingestion + GitHub indexing + Static BugHunter | Upload PDFs/MD/JSON, link repo, run static bug analysis |
| 4 | Playwright codegen + `qa-bridge` local runner | Generate TS tests, write to local repo, run, stream results |
| 5 | Live E2E bug mode + Docker cloud runner + polish + SEO audit | Live URL → autonomous probe → bug records; Lighthouse ≥ 95 |

---

## 18. Open items deferred

- Final brand name (user researching).
- Production domain + Resend.
- Billing (intentionally none).
- VS Code extension wrapping `qa-bridge` (post-MVP, only if demand).
- Self-hosted distribution (Docker compose stack) — post-MVP.

---

*Last updated: 2026-05-22. Edit this file with every architectural decision.*
