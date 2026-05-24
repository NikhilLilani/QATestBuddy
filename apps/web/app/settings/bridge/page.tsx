import { apiFetch, ApiError } from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Alert } from '@/components/ui/alert';
import { SectionHeader } from '@/components/section-header';
import { TerminalSquare, AlertTriangle } from 'lucide-react';
import { BridgeTokensSection } from './bridge-section';

type Token = {
  id: string;
  label: string;
  last_seen_at: string | null;
  revoked_at: string | null;
  created_at: string;
};

export default async function BridgePage() {
  let tokens: Token[] = [];
  let loadError: string | null = null;
  try {
    tokens = await apiFetch<Token[]>('/api/v1/bridge/tokens');
  } catch (e) {
    loadError = e instanceof ApiError ? `Could not load tokens (API ${e.status}).` : String(e);
  }

  const active = tokens.filter((t) => !t.revoked_at).length;

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-amber-200 bg-amber-50/50 p-3">
        <div className="flex items-start gap-2 text-xs text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
          <span>
            A bridge token is displayed <strong>once</strong> on creation — copy it immediately,
            it cannot be recovered. Revoke a token any time to disable that local CLI session.
          </span>
        </div>
      </section>

      <Card>
        <SectionHeader
          icon={<TerminalSquare className="h-4 w-4" />}
          tone="violet"
          title="qa-bridge tokens"
          description="Tokens authorize the local CLI to run Playwright on this workspace's behalf."
          badge={
            <span className="inline-flex items-center rounded-full border border-[hsl(var(--border))] bg-[hsl(var(--muted))] px-2 py-0.5 text-[10px] font-medium text-[hsl(var(--muted-foreground))]">
              {active} active
            </span>
          }
        />
        <CardContent>
          {loadError ? (
            <Alert variant="error">{loadError}</Alert>
          ) : (
            <BridgeTokensSection initial={tokens} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
