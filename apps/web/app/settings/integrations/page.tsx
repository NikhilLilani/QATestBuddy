import { apiFetch, ApiError } from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Alert } from '@/components/ui/alert';
import { SectionHeader } from '@/components/section-header';
import { Github } from 'lucide-react';
import { JiraSection } from './jira-section';
import { GithubSection } from './github-section';

type JiraStatus = { connected: boolean; base_url?: string; status?: string };

export default async function IntegrationsPage() {
  let jira: JiraStatus = { connected: false };
  let loadError: string | null = null;
  try {
    jira = await apiFetch<JiraStatus>('/api/v1/integrations/jira');
  } catch (e) {
    loadError = e instanceof ApiError ? `Could not load integrations (API ${e.status}).` : String(e);
  }

  return (
    <div className="space-y-6">
      <Card>
        <SectionHeader
          icon={<JiraGlyph />}
          tone="sky"
          title="Jira"
          description="Cloud or Server. Uses your API token (PAT)."
          badge={
            jira.connected ? (
              <span className="inline-flex items-center rounded-full border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-800">
                Connected
              </span>
            ) : (
              <span className="inline-flex items-center rounded-full border border-[hsl(var(--border))] bg-[hsl(var(--muted))] px-2 py-0.5 text-[10px] font-medium text-[hsl(var(--muted-foreground))]">
                Not connected
              </span>
            )
          }
        />
        <CardContent>
          {loadError ? (
            <Alert variant="error">{loadError}</Alert>
          ) : (
            <JiraSection initial={jira} />
          )}
        </CardContent>
      </Card>

      <Card>
        <SectionHeader
          icon={<Github className="h-4 w-4" />}
          tone="violet"
          title="GitHub"
          description="OAuth — used to read repo code for test generation (Phase 3)."
        />
        <CardContent>
          <GithubSection />
        </CardContent>
      </Card>
    </div>
  );
}

function JiraGlyph() {
  // Simple monogram fallback so we don't pull in another icon set
  return <span className="text-xs font-bold">J</span>;
}
