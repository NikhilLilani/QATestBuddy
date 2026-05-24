import Link from 'next/link';
import { redirect } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { bootstrapWorkspace } from '@/lib/workspace';
import { AppShell } from '@/components/app-shell';
import { Card, CardContent } from '@/components/ui/card';
import { SectionHeader } from '@/components/section-header';
import { Ticket, Plug, Sparkles } from 'lucide-react';
import { TicketKeyForm } from './ticket-key-form';

export const metadata = { title: 'Tickets' };

export default async function TicketsPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in');
  const workspace = await bootstrapWorkspace(user.id, user.email ?? null);

  return (
    <AppShell
      user={{ email: user.email, id: user.id }}
      workspaceName={workspace.name}
      breadcrumb={['Tickets']}
      title="Open a Jira ticket"
      subtitle="Enter an issue key (e.g. PROJ-123). We'll fetch it via your saved Jira PAT and use it to generate a test plan."
    >
      <div className="space-y-6">
        <Card>
          <SectionHeader
            icon={<Ticket className="h-4 w-4" />}
            title="Ticket key"
            description={
              <>
                Must match the pattern{' '}
                <code className="font-mono text-xs">PROJECT-NUMBER</code>.
              </>
            }
          />
          <CardContent>
            <TicketKeyForm />
            <p className="mt-3 inline-flex items-center gap-1 text-xs text-[hsl(var(--muted-foreground))]">
              <Plug className="h-3 w-3" />
              Need to connect Jira first?{' '}
              <Link href="/settings/integrations" className="text-brand hover:underline">
                Add a PAT in Settings
              </Link>
              .
            </p>
          </CardContent>
        </Card>

        <section className="rounded-lg border border-brand/30 bg-gradient-to-br from-brand/5 to-transparent p-4">
          <div className="flex items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-brand/10 text-brand">
              <Sparkles className="h-4 w-4" />
            </div>
            <div>
              <div className="text-sm font-semibold">What happens next</div>
              <p className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">
                Once you submit a key, we fetch the ticket and walk you through three grounded
                stages — Plan, Test cases, and Automation. Every output cites the Jira fields it
                drew from, so nothing is hallucinated.
              </p>
            </div>
          </div>
        </section>
      </div>
    </AppShell>
  );
}
