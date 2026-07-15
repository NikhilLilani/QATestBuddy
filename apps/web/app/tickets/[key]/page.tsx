import Link from 'next/link';
import { redirect } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { bootstrapWorkspace, getCurrentWorkspace } from '@/lib/workspace';
import { apiFetch, ApiError } from '@/lib/api';
import { AppShell } from '@/components/app-shell';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/ui/status-dot';
import { ExpandableText } from '@/components/expandable-text';
import { Bug, ExternalLink, FileText } from 'lucide-react';
import { PlanWorkflow } from './plan-workflow';

type Ticket = {
  key: string;
  title: string;
  description: string | null;
  status: string | null;
  issuetype: string | null;
  priority: string | null;
  labels: string[];
  url: string;
};

export async function generateMetadata({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  return { title: `${key.toUpperCase()} · Tickets` };
}

export default async function TicketPage({ params }: { params: Promise<{ key: string }> }) {
  const { key: raw } = await params;
  const key = raw.toUpperCase();

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in');
  const workspace = await bootstrapWorkspace(user.id, user.email ?? null);
  const ws = await getCurrentWorkspace(user.id);

  let ticket: Ticket | null = null;
  let loadError: string | null = null;
  try {
    ticket = await apiFetch<Ticket>(`/api/v1/jira/issues/${key}`);
  } catch (e) {
    if (e instanceof ApiError) {
      const body = e.body as { detail?: string } | null;
      loadError = body?.detail ?? `API ${e.status}`;
    } else {
      loadError = String(e);
    }
  }

  return (
    <AppShell
      user={{ email: user.email, id: user.id }}
      workspaceName={workspace.name}
      breadcrumb={['Tickets', key]}
      title={ticket?.title ?? key}
      subtitle={
        ticket
          ? `${ticket.issuetype ?? 'Issue'} · ${ticket.status ?? '—'}${
              ticket.priority ? ` · ${ticket.priority}` : ''
            }`
          : undefined
      }
      actions={
        ticket && (
          <div className="flex items-center gap-2">
            <Link href={`/tickets/${ticket.key}/bugs`}>
              <Button size="sm" variant="secondary">
                <Bug className="mr-1.5 h-3.5 w-3.5" />
                Find bugs
              </Button>
            </Link>
            <a
              href={ticket.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 rounded-md border border-[hsl(var(--border))] px-3 py-1.5 text-sm font-medium hover:bg-[hsl(var(--muted))]"
            >
              <span className="font-mono text-xs">{ticket.key}</span>
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>
        )
      }
    >
      {loadError ? (
        <Alert variant="error">
          <div className="space-y-2">
            <div className="font-medium">Could not load {key}</div>
            <div className="text-xs">{loadError}</div>
            <div className="text-xs">
              Check that Jira is connected in{' '}
              <Link href="/settings/integrations" className="underline">
                Settings
              </Link>{' '}
              and that the ticket key is correct.
            </div>
          </div>
        </Alert>
      ) : ticket && ws ? (
        <div className="space-y-6">
          {(ticket.description || ticket.labels.length > 0) && (
            <section className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--muted))]/30 p-4">
              <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
                <FileText className="h-3.5 w-3.5" />
                Ticket context
              </div>
              {ticket.description ? (
                <ExpandableText text={ticket.description} previewLines={6} />
              ) : (
                <p className="text-sm italic text-[hsl(var(--muted-foreground))]">
                  No description on this ticket.
                </p>
              )}
              {ticket.labels.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {ticket.labels.map((l) => (
                    <StatusBadge key={l} tone="gray">
                      {l}
                    </StatusBadge>
                  ))}
                </div>
              )}
            </section>
          )}

          <PlanWorkflow workspaceId={ws.id} ticketKey={key} />
        </div>
      ) : (
        <Alert variant="error">No active workspace.</Alert>
      )}
    </AppShell>
  );
}
