import Link from 'next/link';
import { redirect } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { bootstrapWorkspace, getCurrentWorkspace } from '@/lib/workspace';
import { apiFetch, ApiError } from '@/lib/api';
import { AppShell } from '@/components/app-shell';
import { Card, CardContent } from '@/components/ui/card';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { SectionHeader } from '@/components/section-header';
import { Boxes } from 'lucide-react';
import { CodegenWorkflow } from './codegen-workflow';

type Framework = {
  id: string;
  name: string;
  language: string;
  source_kind: 'git' | 'local' | 'zip' | 'new';
  git_url: string | null;
  git_branch: string | null;
  local_path: string | null;
  conventions: string;
};

export async function generateMetadata({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  return { title: `${key.toUpperCase()} · Codegen` };
}

export default async function CodegenPage({
  params,
  searchParams,
}: {
  params: Promise<{ key: string }>;
  searchParams: Promise<{ job?: string }>;
}) {
  const { key: raw } = await params;
  const key = raw.toUpperCase();
  const { job: activeJobId } = await searchParams;

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/sign-in');
  const workspace = await bootstrapWorkspace(user.id, user.email ?? null);
  const ws = await getCurrentWorkspace(user.id);

  let frameworks: Framework[] = [];
  let loadError: string | null = null;
  try {
    frameworks = await apiFetch<Framework[]>('/api/v1/frameworks');
  } catch (e) {
    loadError = e instanceof ApiError ? `Could not load frameworks (API ${e.status}).` : String(e);
  }

  return (
    <AppShell
      user={{ email: user.email, id: user.id }}
      workspaceName={workspace.name}
      breadcrumb={['Tickets', key, 'Generate automation']}
      title={`Generate automation for ${key}`}
      subtitle="Pick a framework, verify it matches this ticket + your flow guidance, then generate a multi-file Playwright bundle."
    >
      {loadError && <Alert variant="error">{loadError}</Alert>}

      {!loadError && frameworks.length === 0 && (
        <Card>
          <SectionHeader
            icon={<Boxes className="h-4 w-4" />}
            tone="sky"
            title="No frameworks saved yet"
            description="Codegen needs at least one framework profile so it knows how to write tests that fit your repo (or it can scaffold a new framework from scratch)."
          />
          <CardContent>
            <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-[hsl(var(--border))] bg-[hsl(var(--muted))]/20 px-6 py-8 text-center">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10 text-brand">
                <Boxes className="h-5 w-5" />
              </div>
              <p className="max-w-md text-xs text-[hsl(var(--muted-foreground))]">
                Add a Git URL, a local path, a zip upload, or scaffold from scratch — takes about
                a minute.
              </p>
              <Link href="/settings/frameworks">
                <Button>Add a framework →</Button>
              </Link>
            </div>
          </CardContent>
        </Card>
      )}

      {!loadError && frameworks.length > 0 && ws && (
        <CodegenWorkflow
          ticketKey={key}
          frameworks={frameworks}
          activeJobId={activeJobId ?? null}
        />
      )}
    </AppShell>
  );
}
