import { createClient } from '@/lib/supabase/server';
import { Card, CardContent } from '@/components/ui/card';
import { SectionHeader } from '@/components/section-header';
import { User as UserIcon } from 'lucide-react';

export default async function ProfileSettingsPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  const initial = (user?.email ?? '?').slice(0, 1).toUpperCase();

  return (
    <Card>
      <SectionHeader
        icon={<UserIcon className="h-4 w-4" />}
        title="Profile"
        description="Account info from your auth provider."
      />
      <CardContent>
        <div className="mb-5 flex items-center gap-3 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--muted))]/30 p-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-brand text-base font-semibold text-white">
            {initial}
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{user?.email ?? '—'}</div>
            <div className="text-xs text-[hsl(var(--muted-foreground))]">
              Signed in via {user?.app_metadata?.provider ?? 'email'}
            </div>
          </div>
        </div>

        <dl className="divide-y text-sm">
          <Row label="Email" value={user?.email ?? '—'} />
          <Row label="User ID" value={user?.id ?? '—'} mono />
          <Row label="Signed in via" value={user?.app_metadata?.provider ?? 'email'} />
          <Row label="Created" value={user?.created_at ?? '—'} mono />
        </dl>
      </CardContent>
    </Card>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="grid grid-cols-3 gap-4 py-2.5">
      <dt className="text-[hsl(var(--muted-foreground))]">{label}</dt>
      <dd className={`col-span-2 truncate ${mono ? 'font-mono text-xs' : ''}`}>{value}</dd>
    </div>
  );
}
