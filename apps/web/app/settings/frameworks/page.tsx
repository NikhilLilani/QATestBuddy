import { apiFetch, ApiError } from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Alert } from '@/components/ui/alert';
import { SectionHeader } from '@/components/section-header';
import { Boxes, Plus } from 'lucide-react';
import { AddFrameworkForm } from './add-form';
import { FrameworksList } from './list';

type Framework = {
  id: string;
  name: string;
  language: 'playwright_ts' | 'cypress' | 'selenium' | 'other';
  source_kind: 'git' | 'local' | 'zip' | 'new';
  git_url: string | null;
  git_branch: string | null;
  local_path: string | null;
  zip_storage_key: string | null;
  conventions: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export default async function FrameworksPage() {
  let frameworks: Framework[] = [];
  let loadError: string | null = null;
  try {
    frameworks = await apiFetch<Framework[]>('/api/v1/frameworks');
  } catch (e) {
    loadError = e instanceof ApiError ? `Could not load frameworks (API ${e.status}).` : String(e);
  }

  return (
    <div className="space-y-6">
      <Card>
        <SectionHeader
          icon={<Plus className="h-4 w-4" />}
          title="Add a framework"
          description="A framework profile tells the Playwright codegen how to write tests that fit your existing repo. Pick one source: Git URL (best — we can read the code), local path, zip upload, or scaffold a new framework from scratch."
        />
        <CardContent>
          <AddFrameworkForm />
        </CardContent>
      </Card>

      <Card>
        <SectionHeader
          icon={<Boxes className="h-4 w-4" />}
          tone="sky"
          title="Your frameworks"
          description={`${frameworks.length} ${frameworks.length === 1 ? 'profile' : 'profiles'} saved.`}
          badge={
            <span className="inline-flex items-center rounded-full border border-[hsl(var(--border))] bg-[hsl(var(--muted))] px-2 py-0.5 text-[10px] font-medium text-[hsl(var(--muted-foreground))]">
              {frameworks.length}
            </span>
          }
        />
        <CardContent>
          {loadError ? (
            <Alert variant="error">{loadError}</Alert>
          ) : (
            <FrameworksList items={frameworks} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
