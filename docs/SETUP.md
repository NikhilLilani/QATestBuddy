# Phase 0 setup — local bring-up

This walks you from a fresh clone to running the web + api locally with auth
emails captured by Mailpit.

## 0. Prereqs

| Tool | Version | Status on your machine |
|------|---------|------------------------|
| Node | ≥ 20 (22.18 verified) | ✅ |
| pnpm | 11.x (installed via npm to `%LOCALAPPDATA%\npm-global`) | ✅ |
| Python | ≥ 3.11 (3.13 verified) | ✅ |
| Docker Desktop | latest | ⚠ install if you want Mailpit |
| Git | any recent | ✅ |

If pnpm is not on PATH in a new shell:

```powershell
$env:Path = "$env:LOCALAPPDATA\npm-global;$env:Path"
```

Or open a fresh terminal — the user PATH was updated permanently.

## 1. Install dependencies

```powershell
cd D:\QAtestbuddy
pnpm install
```

Python deps for the API:

```powershell
cd D:\QAtestbuddy\apps\api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## 2. Create a free Supabase project

1. Go to https://supabase.com → **New project** (free tier).
2. Region: closest to you. Project name: `qatestbuddy-dev`.
3. Wait for provisioning (~2 min).
4. **SQL Editor → New query** → paste contents of `infra/supabase/migrations/0001_init.sql` → Run.
5. **Authentication → Providers**:
   - Email: enable. Turn on **Confirm email** and **Email OTP**.
   - Google: enable, add OAuth client ID/secret (follow Supabase docs).
   - Azure (Microsoft): enable, tenant = `common`.
6. **Project Settings → API** → copy:
   - `Project URL` → `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_URL`
   - `anon` key → `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   - `service_role` key → `SUPABASE_SERVICE_ROLE_KEY`
   - `JWT Secret` (under JWT Settings) → `SUPABASE_JWT_SECRET`

## 3. Bring up Mailpit (optional, for testing email OTP locally)

Requires Docker Desktop installed and running.

```powershell
docker compose -f infra/docker-compose.dev.yml up -d
```

Mailpit UI: http://localhost:8025

In Supabase Dashboard → **Authentication → Email → SMTP**, set:

- Host: `host.docker.internal` (if Supabase Auth runs locally) or skip and use
  Supabase's default SMTP (Inbucket) — emails are visible in the Auth dashboard.

> For purely local Supabase, run `supabase start` (Supabase CLI) which spins up
> its own Inbucket on port 54324.

## 4. Fill env files

```powershell
copy apps\web\.env.example apps\web\.env.local
copy apps\api\.env.example apps\api\.env
```

Edit each and paste the values from step 2.

Generate `APP_ENCRYPTION_KEY` (32 bytes base64):

```powershell
python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

## 5. Run dev servers

Two terminals:

```powershell
# terminal 1 — web
pnpm --filter @qa/web dev
```

```powershell
# terminal 2 — api
cd apps\api
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --port 8000
```

Open:
- Web: http://localhost:3000  → landing page
- API health: http://localhost:8000/health
- API docs (Swagger): http://localhost:8000/docs

## 6. Smoke test

1. http://localhost:3000 — landing renders.
2. http://localhost:3000/sign-in — placeholder sign-in renders (real auth = Phase 1).
3. http://localhost:8000/health → `{"status":"ok",...}`.

If all three pass, **Phase 0 is done**.

## Phase 1 — done ✅

Wired into the codebase:

- Supabase Auth (browser + server clients, middleware-based session refresh)
- Sign-in / sign-up / 6-digit OTP verify / password reset / OAuth callback
- Google + Microsoft (`common` tenant) buttons + email/password
- Auto-create workspace on first sign-in (DB trigger adds owner membership)
- Settings pages: Profile / API keys / Integrations / qa-bridge
- Backend endpoints (FastAPI):
  - `GET /api/v1/workspaces`
  - `GET|POST|DELETE /api/v1/settings/api-keys` (Fernet-encrypted)
  - `POST /api/v1/settings/api-keys/{id}/test` (live ping to provider)
  - `GET|POST|DELETE /api/v1/integrations/jira` + `/test`
  - `GET /api/v1/integrations/github/oauth/start` (stub callback, finished in Phase 3)
  - `GET|POST|DELETE /api/v1/bridge/tokens`
- All workspace-scoped routes enforce membership via `X-Workspace-Id` header.

## Smoke test Phase 1

1. Run `0001_init.sql` in Supabase SQL Editor.
2. Enable Email/password (with confirm + OTP), Google, Azure (tenant `common`) in Auth → Providers.
3. Set `APP_ENCRYPTION_KEY` in `apps/api/.env` (32-byte Fernet key — see file comment).
4. `pnpm --filter @qa/web dev` and (in venv) `python -m uvicorn app.main:app --reload --port 8000`.
5. http://localhost:3000/sign-up → email + password → see OTP in Supabase Auth dashboard (or Mailpit if SMTP configured).
6. Enter OTP → lands on /dashboard with auto-created workspace.
7. /settings/api-keys → add a fake OpenAI key → click Test → expect HTTP 401 from OpenAI (proves the pipeline works).
8. /settings/integrations → enter a real Jira PAT → Test → expect HTTP 200.
9. /settings/bridge → create a token → save the shown value.

## Next: Phase 2

Wire the real `PlannerAgent` and `CaseAuthorAgent`:
- Replace `/api/v1/plans` SSE stub with actual LLM-driven generation
- RAG ingestion endpoint + retrieval over PDFs/MD/JSON
- Citation enforcement + clarify channel
- Export plans/cases to Jira / Markdown / CSV
