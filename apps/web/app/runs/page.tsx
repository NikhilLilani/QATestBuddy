import Link from 'next/link';
import { redirect } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { bootstrapWorkspace } from '@/lib/workspace';
import { apiFetch, ApiError } from '@/lib/api';
import { AppShell } from '@/components/app-shell';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Alert } from '@/components/ui/alert';
import { StatusBadge } from '@/components/ui/status-dot';
import { SectionHeader } from '@/components/section-header';
import { PlayCircle, Ticket } from 'lucide-react';
import { DeleteButton } from '@/components/delete-button';
import { deleteRunAction } from '../plans/actions';
import { ZipDownload } from './zip-download';
import { RunOnMachineButton } from './run-on-machine-button';

export const metadata = { title: 'Runs · history' };

type RunRow = {
  id: string;
  kind: string;
  status: string;
  created_at: string;
  base_url: string | null;
  summary: Record<string, unknown> | null;
  jira_key: string | null;
  title: string | null;
  file_count: number;
};

function formatRelative(iso: string): string {
  const date = new Date(iso);
  const diff = Math.max(0, Date.now() - date.getTime());
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  const w = Math.floor(d / 7);
  if (w < 4) return `${w}w ago`;
  // Beyond ~4 weeks, relative buckets stop being distinguishable (every run
  // from the last few months would otherwise read "1mo ago") — show the
  // actual date instead.
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: sameYear ? undefined : 'numeric',
  });
}

function shortBaseUrl(u: string | null): string | null {
  if (!u) return null;
  try {
    const parsed = new URL(u);
    return parsed.host + (parsed.pathname === '/' ? '' : parsed.pathname);
  } catch {
    return u;
  }
}

export default async function RunsHistoryPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in');
  const workspace = await bootstrapWorkspace(user.id, user.email ?? null);

  let runs: RunRow[] = [];
  let loadError: string | null = null;
  try {
    runs = await apiFetch<RunRow[]>('/api/v1/runs?limit=100');
  } catch (e) {
    loadError = e instanceof ApiError ? `Could not load runs (API ${e.status}).` : String(e);
  }

  return (
    <AppShell
      user={{ email: user.email, id: user.id }}
      workspaceName={workspace.name}
      breadcrumb={['Runs']}
      title="Codegen runs"
      subtitle="Every Playwright bundle you've generated. Click Re-download to rebuild the .zip with all files in their original paths."
      fullWidth
      actions={
        <Link href="/tickets">
          <Button>
            <Ticket className="mr-1.5 h-4 w-4" />
            New ticket
          </Button>
        </Link>
      }
    >
      <Card>
        <SectionHeader
          icon={<PlayCircle className="h-4 w-4" />}
          tone="violet"
          title="All runs"
          description="Sorted newest first."
          badge={
            <span className="inline-flex items-center rounded-full border border-[hsl(var(--border))] bg-[hsl(var(--muted))] px-2 py-0.5 text-[10px] font-medium text-[hsl(var(--muted-foreground))]">
              {runs.length}
            </span>
          }
        />
        <CardContent>
          {loadError ? (
            <Alert variant="error">{loadError}</Alert>
          ) : runs.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-[hsl(var(--border))] bg-[hsl(var(--muted))]/20 px-6 py-10 text-center">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10 text-brand">
                <PlayCircle className="h-5 w-5" />
              </div>
              <div>
                <div className="text-sm font-semibold">No runs yet</div>
                <p className="mx-auto mt-1 max-w-md text-xs text-[hsl(var(--muted-foreground))]">
                  Open a ticket and click <strong>Next: Generate automation →</strong> to make
                  your first bundle.
                </p>
              </div>
              <Link href="/tickets">
                <Button size="sm">Open a ticket</Button>
              </Link>
            </div>
          ) : (
            <div className="overflow-hidden rounded-md border">
              <div className="max-h-[70vh] overflow-y-auto">
                <table className="w-full table-fixed text-base">
                  <colgroup>
                    <col className="w-[7%]" />
                    <col className="w-[21%]" />
                    <col className="w-[9%]" />
                    <col className="w-[13%]" />
                    <col className="w-[7%]" />
                    <col className="w-[16%]" />
                    <col className="w-[9%]" />
                    <col className="w-[18%]" />
                  </colgroup>
                  <thead className="sticky top-0 z-10 bg-[hsl(var(--muted))]/95 text-left text-xs font-semibold uppercase tracking-wider text-[hsl(var(--muted-foreground))] backdrop-blur">
                    <tr>
                      <th className="px-4 py-3">Ticket</th>
                      <th className="px-4 py-3">Test</th>
                      <th className="px-4 py-3">Kind</th>
                      <th className="px-4 py-3">Target</th>
                      <th className="px-4 py-3">Files</th>
                      <th className="px-4 py-3">Quality</th>
                      <th className="px-4 py-3">Created</th>
                      <th className="px-4 py-3 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {runs.map((r) => {
                      const lines = (r.summary?.['lines'] as number | undefined) ?? null;
                      const errors = (r.summary?.['validator_errors'] as number | undefined) ?? 0;
                      const warns = (r.summary?.['validator_warnings'] as number | undefined) ?? 0;
                      const base = shortBaseUrl(r.base_url);
                      const codegenHref = r.jira_key ? (`/tickets/${r.jira_key}/codegen` as const) : null;
                      return (
                        <tr key={r.id} className="hover:bg-[hsl(var(--muted))]/30">
                          {/* Ticket */}
                          <td className="px-4 py-3 align-middle">
                            {r.jira_key ? (
                              <Link
                                href={`/tickets/${r.jira_key}`}
                                className="inline-block rounded bg-[hsl(var(--muted))] px-2 py-1 font-mono text-sm text-[hsl(var(--muted-foreground))] hover:underline"
                              >
                                {r.jira_key}
                              </Link>
                            ) : (
                              <span className="text-sm text-[hsl(var(--muted-foreground))]">—</span>
                            )}
                          </td>

                          {/* Test — title, links back to the codegen page it came from */}
                          <td className="px-4 py-3 align-middle">
                            {codegenHref ? (
                              <Link href={codegenHref} className="block truncate text-[15px] font-semibold hover:underline">
                                {r.title ?? '—'}
                              </Link>
                            ) : (
                              <span className="block truncate text-[15px] font-semibold">{r.title ?? '—'}</span>
                            )}
                          </td>

                          {/* Kind */}
                          <td className="px-4 py-3 align-middle">
                            <StatusBadge tone="gray">{r.kind}</StatusBadge>
                          </td>

                          {/* Target */}
                          <td className="px-4 py-3 align-middle text-sm">
                            {base ? (
                              <span className="block truncate font-mono text-[hsl(var(--muted-foreground))]" title={r.base_url ?? ''}>
                                {base}
                              </span>
                            ) : (
                              <span className="text-[hsl(var(--muted-foreground))]">—</span>
                            )}
                          </td>

                          {/* Files */}
                          <td className="px-4 py-3 align-middle text-sm">
                            <StatusBadge tone="sky">
                              {r.file_count} {r.file_count === 1 ? 'file' : 'files'}
                            </StatusBadge>
                          </td>

                          {/* Quality */}
                          <td className="px-4 py-3 align-middle text-sm">
                            <div className="flex flex-wrap items-center gap-1.5">
                              {lines !== null && (
                                <span className="text-[hsl(var(--muted-foreground))]">{lines} lines</span>
                              )}
                              {errors > 0 && (
                                <StatusBadge tone="red">
                                  {errors} error{errors === 1 ? '' : 's'}
                                </StatusBadge>
                              )}
                              {warns > 0 && (
                                <StatusBadge tone="amber">
                                  {warns} warning{warns === 1 ? '' : 's'}
                                </StatusBadge>
                              )}
                              {errors === 0 && warns === 0 && lines !== null && (
                                <StatusBadge tone="green">clean</StatusBadge>
                              )}
                            </div>
                          </td>

                          {/* Created */}
                          <td
                            className="px-4 py-3 align-middle text-sm text-[hsl(var(--muted-foreground))]"
                            title={new Date(r.created_at).toLocaleString()}
                          >
                            {formatRelative(r.created_at)}
                          </td>

                          {/* Actions */}
                          <td className="px-4 py-3 align-middle">
                            <div className="flex flex-wrap items-center justify-end gap-1.5">
                              <RunOnMachineButton runId={r.id} ticketKey={r.jira_key} />
                              <ZipDownload runId={r.id} ticketKey={r.jira_key ?? 'run'} />
                              <DeleteButton
                                iconOnly
                                id={r.id}
                                action={deleteRunAction}
                                confirmLabel="Delete this run?"
                                successMessage="Run deleted."
                              />
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}
