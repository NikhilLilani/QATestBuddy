# Supabase

## Apply migrations

Option 1 — Supabase CLI (recommended):

```powershell
# one-time
npm i -g supabase
supabase login
supabase link --project-ref <your-project-ref>

# apply
supabase db push
```

Option 2 — SQL Editor:

Open `migrations/0001_init.sql` in the Supabase Dashboard SQL Editor and run it.

## Auth providers to enable (in Dashboard → Authentication → Providers)

- Email + password (with "Confirm email" enabled — uses 6-digit OTP if Email OTP is on)
- Google
- Microsoft (Azure) — Tenant = `common` so personal + work/school accounts both work

## Email templates

Replace `{{ .SiteURL }}` references with `${NEXT_PUBLIC_APP_URL}` value. For local
development, point SMTP at Mailpit (host `localhost`, port `1025`) under
Settings → Auth → SMTP.
