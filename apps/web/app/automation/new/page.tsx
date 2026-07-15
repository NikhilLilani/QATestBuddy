import { redirect } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { bootstrapWorkspace } from '@/lib/workspace';
import { AppShell } from '@/components/app-shell';
import { DirectAutomationForm } from './direct-automation-form';

export const metadata = { title: 'Direct automation' };

/**
 * Flow 2 entry point — no Jira ticket required.
 *
 * The user can describe the flow in plain text (and/or upload PRD/BRD context
 * + point at a dev repo for locator grounding), pick a framework, and go
 * straight to test cases + Playwright codegen. Everything routes through the
 * same /cases and /codegen endpoints as Flow 1, but with `flow_text` instead
 * of a Jira ticket key — the backend creates a synthetic ticket row so the
 * downstream pipeline doesn't have to special-case the no-Jira path.
 */
export default async function DirectAutomationPage() {
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
      breadcrumb={['Direct automation']}
      title="Generate Playwright tests directly"
      subtitle="No Jira ticket needed. Describe the flow, optionally point at a dev repo for grounded selectors, and we'll generate cases + runnable Playwright code in one shot."
    >
      <DirectAutomationForm />
    </AppShell>
  );
}
