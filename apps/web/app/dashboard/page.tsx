import { redirect } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { bootstrapWorkspace } from '@/lib/workspace';
import { apiFetch, ApiError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { SectionHeader } from '@/components/section-header';
import { AppShell } from '@/components/app-shell';
import Link from 'next/link';
import {
  Ticket,
  ClipboardList,
  PlayCircle,
  Boxes,
  KeyRound,
  Plug,
  ArrowRight,
  Sparkles,
  ListChecks,
  FileCode2,
} from 'lucide-react';

export const metadata = { title: 'Dashboard' };

type PlanRow = {
  id: string;
  created_at: string;
  jira_key: string;
  title: string;
  case_count: number;
};

type RunRow = {
  id: string;
  kind: string;
  created_at: string;
  jira_key: string | null;
  title: string | null;
  file_count: number;
};

function formatRelative(iso: string): string {
  const now = Date.now();
  const then = new Date(iso).getTime();
  const diff = Math.max(0, now - then);
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export default async function DashboardPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in');

  const workspace = await bootstrapWorkspace(user.id, user.email ?? null);

  let recentPlans: PlanRow[] = [];
  let recentRuns: RunRow[] = [];
  try {
    [recentPlans, recentRuns] = await Promise.all([
      apiFetch<PlanRow[]>('/api/v1/plans?limit=5'),
      apiFetch<RunRow[]>('/api/v1/runs?limit=5'),
    ]);
  } catch (e) {
    void (e instanceof ApiError);
  }

  const totalCases = recentPlans.reduce((acc, p) => acc + (p.case_count ?? 0), 0);
  const totalFiles = recentRuns.reduce((acc, r) => acc + (r.file_count ?? 0), 0);

  return (
    <AppShell
      user={{ email: user.email, id: user.id }}
      workspaceName={workspace.name}
      breadcrumb={['Dashboard']}
      title={`Welcome back · ${workspace.name}`}
      subtitle="Pick a Jira ticket to generate a grounded test plan + cases. Each output cites the fields it was derived from — no hallucinated facts."
      actions={
        <Link href="/tickets">
          <Button>
            <Ticket className="mr-1.5 h-4 w-4" />
            Open a ticket
          </Button>
        </Link>
      }
    >
      <div className="space-y-6">
        {/* ───────────────── Hero workflow ───────────────── */}
        <section className="relative overflow-hidden rounded-xl border border-brand/30 bg-gradient-to-br from-brand/10 via-brand/5 to-transparent p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="max-w-xl">
              <div className="mb-1 inline-flex items-center gap-1.5 rounded-full bg-white/70 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-brand-700 ring-1 ring-brand/20">
                <Sparkles className="h-3 w-3" />
                AI-assisted QA
              </div>
              <h2 className="text-lg font-semibold">Ticket → Plan → Cases → Automation</h2>
              <p className="mt-1 text-sm text-[hsl(var(--muted-foreground))]">
                Three grounded LLM passes turn any Jira issue into a runnable Playwright bundle.
                You stay in control — every output cites its sources.
              </p>
            </div>
            <Link href="/tickets">
              <Button size="sm">
                Start now
                <ArrowRight className="ml-1.5 h-4 w-4" />
              </Button>
            </Link>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-3">
            <WorkflowMini
              icon={<ClipboardList className="h-4 w-4" />}
              step={1}
              title="Plan"
              hint="Scope, risks, exit criteria"
            />
            <WorkflowMini
              icon={<ListChecks className="h-4 w-4" />}
              step={2}
              title="Test cases"
              hint="Preconditions, steps, results"
            />
            <WorkflowMini
              icon={<FileCode2 className="h-4 w-4" />}
              step={3}
              title="Automation"
              hint="Playwright TS bundle"
            />
          </div>
        </section>

        {/* ───────────────── Stat tiles ───────────────── */}
        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile
            icon={<ClipboardList className="h-4 w-4" />}
            tone="brand"
            label="Recent plans"
            value={recentPlans.length}
            href="/plans"
          />
          <StatTile
            icon={<ListChecks className="h-4 w-4" />}
            tone="emerald"
            label="Cases generated"
            value={totalCases}
            href="/plans"
          />
          <StatTile
            icon={<PlayCircle className="h-4 w-4" />}
            tone="violet"
            label="Codegen runs"
            value={recentRuns.length}
            href="/runs"
          />
          <StatTile
            icon={<FileCode2 className="h-4 w-4" />}
            tone="sky"
            label="Files generated"
            value={totalFiles}
            href="/runs"
          />
        </section>

        {/* ───────────────── Recent activity ───────────────── */}
        <section className="grid gap-4 md:grid-cols-2">
          <Card>
            <SectionHeader
              icon={<ClipboardList className="h-4 w-4" />}
              title="Recent plans"
              description="Your latest 5 generated test plans."
              actions={
                <Link href="/plans" className="text-xs font-medium text-brand hover:underline">
                  See all →
                </Link>
              }
            />
            <CardContent>
              {recentPlans.length === 0 ? (
                <EmptyMini
                  icon={<ClipboardList className="h-5 w-5" />}
                  message="No plans yet."
                  cta={
                    <Link href="/tickets">
                      <Button size="sm">Generate your first plan</Button>
                    </Link>
                  }
                />
              ) : (
                <ul className="divide-y text-sm">
                  {recentPlans.map((p) => (
                    <li key={p.id} className="group flex items-center justify-between gap-3 py-3">
                      <Link
                        href={`/tickets/${p.jira_key}`}
                        className="min-w-0 flex-1 truncate"
                      >
                        <div className="flex items-center gap-2">
                          <span className="rounded bg-[hsl(var(--muted))] px-1.5 py-0.5 font-mono text-xs text-[hsl(var(--muted-foreground))]">
                            {p.jira_key}
                          </span>
                          <span className="truncate text-[15px] font-medium group-hover:text-brand-700 group-hover:underline">
                            {p.title}
                          </span>
                        </div>
                      </Link>
                      <span className="shrink-0 text-xs text-[hsl(var(--muted-foreground))]">
                        {p.case_count} cases · {formatRelative(p.created_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <SectionHeader
              icon={<PlayCircle className="h-4 w-4" />}
              tone="violet"
              title="Recent codegen runs"
              description="Your latest 5 Playwright bundles."
              actions={
                <Link href="/runs" className="text-xs font-medium text-brand hover:underline">
                  See all →
                </Link>
              }
            />
            <CardContent>
              {recentRuns.length === 0 ? (
                <EmptyMini
                  icon={<PlayCircle className="h-5 w-5" />}
                  message="No runs yet. Open a ticket and click Next: Generate automation →."
                  cta={
                    <Link href="/tickets">
                      <Button size="sm">Open a ticket</Button>
                    </Link>
                  }
                />
              ) : (
                <ul className="divide-y text-sm">
                  {recentRuns.map((r) => (
                    <li key={r.id} className="group flex items-center justify-between gap-3 py-3">
                      <Link
                        href={r.jira_key ? `/tickets/${r.jira_key}/codegen` : '/runs'}
                        className="min-w-0 flex-1 truncate"
                      >
                        <div className="flex items-center gap-2">
                          <span className="rounded bg-[hsl(var(--muted))] px-1.5 py-0.5 font-mono text-xs text-[hsl(var(--muted-foreground))]">
                            {r.jira_key ?? 'run'}
                          </span>
                          <span className="truncate text-[15px] font-medium group-hover:text-brand-700 group-hover:underline">
                            {r.title ?? r.kind}
                          </span>
                        </div>
                      </Link>
                      <span className="shrink-0 text-xs text-[hsl(var(--muted-foreground))]">
                        {r.file_count} files · {formatRelative(r.created_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </section>

        {/* ───────────────── Setup shortcuts ───────────────── */}
        <section className="grid gap-4 md:grid-cols-3">
          <ShortcutCard
            icon={<Boxes className="h-4 w-4" />}
            tone="sky"
            title="Frameworks"
            description="Saved automation framework profiles (Git / local / zip / new)."
            href="/settings/frameworks"
            ctaLabel="Manage frameworks"
          />
          <ShortcutCard
            icon={<KeyRound className="h-4 w-4" />}
            tone="amber"
            title="LLM keys"
            description="Bring your own Anthropic / OpenAI / Gemini / OpenRouter key."
            href="/settings/api-keys"
            ctaLabel="Manage keys"
          />
          <ShortcutCard
            icon={<Plug className="h-4 w-4" />}
            tone="emerald"
            title="Integrations"
            description="Connect Jira to fetch tickets — and GitHub for repo grounding."
            href="/settings/integrations"
            ctaLabel="Open integrations"
          />
        </section>
      </div>
    </AppShell>
  );
}

/* ───────────────────────── helpers ───────────────────────── */

function WorkflowMini({
  icon,
  step,
  title,
  hint,
}: {
  icon: React.ReactNode;
  step: number;
  title: string;
  hint: string;
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-[hsl(var(--border))] bg-white/70 px-3 py-2.5 backdrop-blur">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-brand/10 text-brand">
        {icon}
      </div>
      <div>
        <div className="text-[10px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
          Step {step}
        </div>
        <div className="text-sm font-medium leading-tight">{title}</div>
        <div className="text-[11px] text-[hsl(var(--muted-foreground))]">{hint}</div>
      </div>
    </div>
  );
}

function StatTile({
  icon,
  tone,
  label,
  value,
  href,
}: {
  icon: React.ReactNode;
  tone: 'brand' | 'emerald' | 'sky' | 'violet';
  label: string;
  value: number;
  href: string;
}) {
  const toneClass =
    tone === 'emerald'
      ? 'bg-emerald-100 text-emerald-700'
      : tone === 'sky'
        ? 'bg-sky-100 text-sky-700'
        : tone === 'violet'
          ? 'bg-violet-100 text-violet-700'
          : 'bg-brand/10 text-brand';
  return (
    <Link
      href={href as never}
      className="group rounded-xl border border-[hsl(var(--border))] bg-white p-4 transition-colors hover:border-brand/40 hover:bg-brand/[0.02]"
    >
      <div className="flex items-center justify-between">
        <div
          className={`flex h-8 w-8 items-center justify-center rounded-md ${toneClass}`}
        >
          {icon}
        </div>
        <ArrowRight className="h-3.5 w-3.5 text-[hsl(var(--muted-foreground))] transition-transform group-hover:translate-x-0.5 group-hover:text-brand" />
      </div>
      <div className="mt-3 text-2xl font-semibold tracking-tight">{value}</div>
      <div className="text-xs text-[hsl(var(--muted-foreground))]">{label}</div>
    </Link>
  );
}

function ShortcutCard({
  icon,
  tone,
  title,
  description,
  href,
  ctaLabel,
}: {
  icon: React.ReactNode;
  tone: 'brand' | 'emerald' | 'amber' | 'sky' | 'violet';
  title: string;
  description: string;
  href: string;
  ctaLabel: string;
}) {
  return (
    <Card className="flex h-full flex-col">
      <SectionHeader icon={icon} title={title} description={description} tone={tone} />
      <CardContent className="mt-auto">
        <Link
          href={href as never}
          className="inline-flex items-center gap-1 text-sm font-medium text-brand hover:underline"
        >
          {ctaLabel}
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </CardContent>
    </Card>
  );
}

function EmptyMini({
  icon,
  message,
  cta,
}: {
  icon: React.ReactNode;
  message: string;
  cta?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-[hsl(var(--border))] bg-[hsl(var(--muted))]/20 px-4 py-6 text-center">
      <div className="flex h-9 w-9 items-center justify-center rounded-full bg-brand/10 text-brand">
        {icon}
      </div>
      <p className="text-xs text-[hsl(var(--muted-foreground))]">{message}</p>
      {cta}
    </div>
  );
}
