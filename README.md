# QAtestbuddy

> Grounded AI for QA — test plans, test cases, and Playwright tests generated from your Jira tickets, with citations and an anti-hallucination clarify loop.

**Working name:** `QAtestbuddy` — rebrandable via `packages/brand/brand.config.ts`.

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full design and [`docs/SETUP.md`](./docs/SETUP.md) for local bring-up.

## Monorepo layout

```
apps/
  web/          Next.js 15 frontend (Vercel)
  api/          FastAPI backend (Fly.io)
  bridge-cli/   qa-bridge npm CLI for local Playwright runs
packages/
  brand/        rebrand-safe brand config + assets
  shared-types/ shared types (Zod / Pydantic)
infra/
  supabase/     migrations + RLS
  docker-compose.dev.yml
```

## Quick start

```powershell
pnpm install
pnpm dev
```

See [`docs/SETUP.md`](./docs/SETUP.md) for full setup including Supabase + Mailpit.
