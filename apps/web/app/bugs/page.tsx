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
import { Bug, Ticket } from 'lucide-react';

export const metadata = { title: 'Bugs · findings' };

type BugRow = {
  id: string;
  created_at: string;
  severity: 'critical' | 'high' | 'medium' | 'low';
  title: string;
  description: string;
  status: string;
  ticket_key: string | null;
  ticket_title: string | null;
  run_id: string;
};

function formatRelative(iso: string): string {
  const diff = Math.max(0, Date.now() - new Date(iso).getTime());
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  const mo = Math.floor(d / 30);
  return `${mo}mo ago`;
}

function severityTone(severity: BugRow['severity']): 'red' | 'amber' | 'sky' | 'gray' {
  if (severity === 'critical') return 'red';
  if (severity === 'high') return 'amber';
  if (severity === 'medium') return 'sky';
  return 'gray';
}

export default async function BugsPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in');
  const workspace = await bootstrapWorkspace(user.id, user.email ?? null);

  let bugs: BugRow[] = [];
  let loadError: string | null = null;
  try {
    bugs = await apiFetch<BugRow[]>('/api/v1/bugs?limit=100');
  } catch (e) {
    loadError = e instanceof ApiError ? `Could not load bugs (API ${e.status}).` : String(e);
  }

  return (
    <AppShell
      user={{ email: user.email, id: user.id }}
      workspaceName={workspace.name}
      breadcrumb={['Bugs']}
      title="Bug findings"
      subtitle="Every static bug scan across your tickets, newest first."
      actions={
        <Link href="/tickets">
          <Button>
            <Ticket className="mr-1.5 h-4 w-4" />
            Open a ticket
          </Button>
        </Link>
      }
    >
      <Card>
        <SectionHeader
          icon={<Bug className="h-4 w-4" />}
          tone="amber"
          title="All findings"
          description="Sorted newest first."
          badge={
            <span className="inline-flex items-center rounded-full border border-[hsl(var(--border))] bg-[hsl(var(--muted))] px-2 py-0.5 text-[10px] font-medium text-[hsl(var(--muted-foreground))]">
              {bugs.length}
            </span>
          }
        />
        <CardContent>
          {loadError ? (
            <Alert variant="error">{loadError}</Alert>
          ) : bugs.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-[hsl(var(--border))] bg-[hsl(var(--muted))]/20 px-6 py-10 text-center">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10 text-brand">
                <Bug className="h-5 w-5" />
              </div>
              <div>
                <div className="text-sm font-semibold">No bug findings yet</div>
                <p className="mx-auto mt-1 max-w-md text-xs text-[hsl(var(--muted-foreground))]">
                  Open a ticket and run <strong>Find bugs</strong> to scan it against your indexed
                  repo code.
                </p>
              </div>
              <Link href="/tickets">
                <Button size="sm">Open a ticket</Button>
              </Link>
            </div>
          ) : (
            <div className="overflow-hidden rounded-md border">
              <table className="w-full table-fixed text-sm">
                <colgroup>
                  <col className="w-[12%]" />
                  <col className="w-[42%]" />
                  <col className="w-[16%]" />
                  <col className="w-[14%]" />
                  <col className="w-[16%]" />
                </colgroup>
                <thead className="bg-[hsl(var(--muted))]/40 text-left text-[10px] font-semibold uppercase tracking-wider text-[hsl(var(--muted-foreground))]">
                  <tr>
                    <th className="px-3 py-2.5">Severity</th>
                    <th className="px-3 py-2.5">Finding</th>
                    <th className="px-3 py-2.5">Ticket</th>
                    <th className="px-3 py-2.5">Status</th>
                    <th className="px-3 py-2.5">Found</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {bugs.map((b) => (
                    <tr key={b.id} className="align-top hover:bg-[hsl(var(--muted))]/30">
                      <td className="px-3 py-3">
                        <StatusBadge tone={severityTone(b.severity)}>{b.severity}</StatusBadge>
                      </td>
                      <td className="px-3 py-3">
                        <Link href={`/bugs/${b.id}`} className="font-medium hover:underline">
                          {b.title}
                        </Link>
                        <div className="mt-0.5 truncate text-xs text-[hsl(var(--muted-foreground))]">
                          {b.description}
                        </div>
                      </td>
                      <td className="px-3 py-3 text-xs">
                        {b.ticket_key ? (
                          <Link href={`/tickets/${b.ticket_key}`} className="font-mono hover:underline">
                            {b.ticket_key}
                          </Link>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="px-3 py-3">
                        <StatusBadge tone={b.status === 'resolved' ? 'green' : 'gray'}>
                          {b.status}
                        </StatusBadge>
                      </td>
                      <td
                        className="px-3 py-3 text-xs text-[hsl(var(--muted-foreground))]"
                        title={new Date(b.created_at).toLocaleString()}
                      >
                        {formatRelative(b.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}
