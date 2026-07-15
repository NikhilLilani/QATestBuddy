import { redirect } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { bootstrapWorkspace, getCurrentWorkspace } from '@/lib/workspace';
import { AppShell } from '@/components/app-shell';
import { Alert } from '@/components/ui/alert';
import { BugHuntWorkflow } from './bug-hunt-workflow';

export async function generateMetadata({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  return { title: `${key.toUpperCase()} · Bugs` };
}

export default async function TicketBugsPage({
  params,
}: {
  params: Promise<{ key: string }>;
}) {
  const { key: raw } = await params;
  const key = raw.toUpperCase();

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in');
  const workspace = await bootstrapWorkspace(user.id, user.email ?? null);
  const ws = await getCurrentWorkspace(user.id);

  return (
    <AppShell
      user={{ email: user.email, id: user.id }}
      workspaceName={workspace.name}
      breadcrumb={['Tickets', key, 'Bug scan']}
      title={`Static bug scan for ${key}`}
      subtitle="Cross-checks this ticket against your indexed repo code and cites the exact files behind every finding."
    >
      {ws ? (
        <BugHuntWorkflow ticketKey={key} />
      ) : (
        <Alert variant="error">No active workspace.</Alert>
      )}
    </AppShell>
  );
}
