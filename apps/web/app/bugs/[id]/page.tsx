import Link from 'next/link';
import { redirect } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { bootstrapWorkspace } from '@/lib/workspace';
import { apiFetch, ApiError } from '@/lib/api';
import { AppShell } from '@/components/app-shell';
import { Card, CardContent } from '@/components/ui/card';
import { Alert } from '@/components/ui/alert';
import { StatusBadge } from '@/components/ui/status-dot';
import { CitationList } from '@/components/citation-pill';
import { StatusToggleButton } from './status-toggle-button';

type Citation =
  | { kind: 'jira'; key: string; field: string }
  | { kind: 'rag_chunk'; chunk_id: string }
  | { kind: 'repo_file'; repo: string; path: string; line_start: number; line_end: number };

type BugDetail = {
  id: string;
  created_at: string;
  severity: 'critical' | 'high' | 'medium' | 'low';
  title: string;
  description: string;
  status: string;
  file_refs: Citation[];
  run_id: string;
  run_kind: string;
  repo_ref: string | null;
  ticket_key: string | null;
  ticket_title: string | null;
};

function severityTone(severity: BugDetail['severity']): 'red' | 'amber' | 'sky' | 'gray' {
  if (severity === 'critical') return 'red';
  if (severity === 'high') return 'amber';
  if (severity === 'medium') return 'sky';
  return 'gray';
}

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return { title: `Bug ${id.slice(0, 8)}` };
}

export default async function BugDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in');
  const workspace = await bootstrapWorkspace(user.id, user.email ?? null);

  let bug: BugDetail | null = null;
  let loadError: string | null = null;
  try {
    bug = await apiFetch<BugDetail>(`/api/v1/bugs/${id}`);
  } catch (e) {
    loadError = e instanceof ApiError ? `Could not load bug (API ${e.status}).` : String(e);
  }

  return (
    <AppShell
      user={{ email: user.email, id: user.id }}
      workspaceName={workspace.name}
      breadcrumb={['Bugs', bug?.title ?? id]}
      title={bug?.title ?? 'Bug finding'}
      subtitle={bug?.ticket_key ? `From ticket ${bug.ticket_key}` : undefined}
      actions={bug && <StatusToggleButton id={bug.id} status={bug.status} />}
    >
      {loadError ? (
        <Alert variant="error">{loadError}</Alert>
      ) : bug ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge tone={severityTone(bug.severity)}>{bug.severity}</StatusBadge>
            <StatusBadge tone={bug.status === 'resolved' ? 'green' : 'gray'}>
              {bug.status}
            </StatusBadge>
            {bug.ticket_key && (
              <Link href={`/tickets/${bug.ticket_key}`} className="text-xs font-mono hover:underline">
                {bug.ticket_key}
              </Link>
            )}
          </div>

          <Card>
            <CardContent className="space-y-4 py-5 text-sm">
              <section>
                <h3 className="font-semibold">Description</h3>
                <p className="text-[hsl(var(--muted-foreground))]">{bug.description}</p>
              </section>
              <section>
                <h3 className="font-semibold">Citations</h3>
                {bug.file_refs.length > 0 ? (
                  <CitationList citations={bug.file_refs} />
                ) : (
                  <p className="text-xs italic text-[hsl(var(--muted-foreground))]">
                    Ticket-only finding — no code citation.
                  </p>
                )}
              </section>
              <section className="text-xs text-[hsl(var(--muted-foreground))]">
                Found {new Date(bug.created_at).toLocaleString()} · run {bug.run_id.slice(0, 8)} ·{' '}
                {bug.run_kind}
              </section>
            </CardContent>
          </Card>
        </div>
      ) : (
        <Alert variant="error">Bug not found.</Alert>
      )}
    </AppShell>
  );
}
