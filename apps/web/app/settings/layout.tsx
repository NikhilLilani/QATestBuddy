import { redirect } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { bootstrapWorkspace } from '@/lib/workspace';
import { AppShell } from '@/components/app-shell';
import { NavTabs } from '@/components/ui/tabs';

export const metadata = { title: 'Settings' };

export default async function SettingsLayout({ children }: { children: React.ReactNode }) {
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
      breadcrumb={['Settings']}
      title="Settings"
      subtitle="API keys, integrations, frameworks, and the qa-bridge CLI."
    >
      <NavTabs
        items={[
          { href: '/settings', label: 'Profile' },
          { href: '/settings/api-keys', label: 'API keys' },
          { href: '/settings/integrations', label: 'Integrations' },
          { href: '/settings/frameworks', label: 'Frameworks' },
          { href: '/settings/bridge', label: 'qa-bridge' },
        ]}
      />
      <div className="mt-6">{children}</div>
    </AppShell>
  );
}
