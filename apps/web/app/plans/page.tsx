import Link from 'next/link';
import { redirect } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { bootstrapWorkspace } from '@/lib/workspace';
import { apiFetch, ApiError } from '@/lib/api';
import { AppShell } from '@/components/app-shell';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Alert } from '@/components/ui/alert';
import { SectionHeader } from '@/components/section-header';
import { ClipboardList, Ticket } from 'lucide-react';
import { PlansTable, type PlanRow } from './plans-table';

export const metadata = { title: 'Plans · history' };

export default async function PlansHistoryPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in');
  const workspace = await bootstrapWorkspace(user.id, user.email ?? null);

  let plans: PlanRow[] = [];
  let loadError: string | null = null;
  try {
    plans = await apiFetch<PlanRow[]>('/api/v1/plans?limit=100');
  } catch (e) {
    loadError = e instanceof ApiError ? `Could not load plans (API ${e.status}).` : String(e);
  }

  return (
    <AppShell
      user={{ email: user.email, id: user.id }}
      workspaceName={workspace.name}
      breadcrumb={['Plans']}
      title="Plans history"
      subtitle="Every test plan you've generated. Tick rows to bulk-delete; click Open to reopen the ticket."
      actions={
        <Link href="/tickets">
          <Button>
            <Ticket className="mr-1.5 h-4 w-4" />
            New plan
          </Button>
        </Link>
      }
    >
      <Card>
        <SectionHeader
          icon={<ClipboardList className="h-4 w-4" />}
          title="All plans"
          description="Sorted newest first."
          badge={
            <span className="inline-flex items-center rounded-full border border-[hsl(var(--border))] bg-[hsl(var(--muted))] px-2 py-0.5 text-[10px] font-medium text-[hsl(var(--muted-foreground))]">
              {plans.length}
            </span>
          }
        />
        <CardContent>
          {loadError ? (
            <Alert variant="error">{loadError}</Alert>
          ) : plans.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-[hsl(var(--border))] bg-[hsl(var(--muted))]/20 px-6 py-10 text-center">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10 text-brand">
                <ClipboardList className="h-5 w-5" />
              </div>
              <div>
                <div className="text-sm font-semibold">No plans yet</div>
                <p className="mx-auto mt-1 max-w-md text-xs text-[hsl(var(--muted-foreground))]">
                  Open a Jira ticket to generate your first grounded test plan.
                </p>
              </div>
              <Link href="/tickets">
                <Button size="sm">Open a ticket</Button>
              </Link>
            </div>
          ) : (
            <PlansTable plans={plans} />
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}
