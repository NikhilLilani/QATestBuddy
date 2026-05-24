import { apiFetch, ApiError } from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Alert } from '@/components/ui/alert';
import { SectionHeader } from '@/components/section-header';
import { KeyRound, Plus, ShieldCheck } from 'lucide-react';
import { ApiKeysList } from './api-keys-list';
import { AddApiKeyForm } from './add-form';

type ApiKey = {
  id: string;
  provider: 'anthropic' | 'openai' | 'gemini' | 'openrouter';
  name: string;
  key_hint: string;
  last_used_at: string | null;
  created_at: string;
};

export default async function ApiKeysPage() {
  let keys: ApiKey[] = [];
  let loadError: string | null = null;
  try {
    keys = await apiFetch<ApiKey[]>('/api/v1/settings/api-keys');
  } catch (e) {
    loadError = e instanceof ApiError ? `Could not load keys (API ${e.status}). Is the backend running?` : String(e);
  }

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-emerald-200 bg-emerald-50/50 p-3">
        <div className="flex items-start gap-2 text-xs text-emerald-900">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700" />
          <span>
            <strong>BYOK</strong> — your keys are encrypted at rest with libsodium. Only the last
            4 characters are ever displayed. We never bill you for LLM usage.
          </span>
        </div>
      </section>

      <Card>
        <SectionHeader
          icon={<Plus className="h-4 w-4" />}
          title="Add an LLM key"
          description="Pick a provider, paste the key, give it a label. The key is hashed and encrypted before it touches disk."
        />
        <CardContent>
          <AddApiKeyForm />
        </CardContent>
      </Card>

      <Card>
        <SectionHeader
          icon={<KeyRound className="h-4 w-4" />}
          tone="amber"
          title="Your keys"
          description={`${keys.length} active.`}
          badge={
            <span className="inline-flex items-center rounded-full border border-[hsl(var(--border))] bg-[hsl(var(--muted))] px-2 py-0.5 text-[10px] font-medium text-[hsl(var(--muted-foreground))]">
              {keys.length}
            </span>
          }
        />
        <CardContent>
          {loadError ? (
            <Alert variant="error">{loadError}</Alert>
          ) : (
            <ApiKeysList keys={keys} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
