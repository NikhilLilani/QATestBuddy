'use client';

import { useTransition } from 'react';
import { toast } from 'sonner';
import { deleteApiKey, testApiKey } from '../actions';
import { Button } from '@/components/ui/button';

type ApiKey = {
  id: string;
  provider: string;
  name: string;
  key_hint: string;
  last_used_at: string | null;
  created_at: string;
};

export function ApiKeysList({ keys }: { keys: ApiKey[] }) {
  const [pending, startTransition] = useTransition();

  if (keys.length === 0) {
    return <p className="text-sm text-[hsl(var(--muted-foreground))]">No keys yet.</p>;
  }

  return (
    <ul className="divide-y">
      {keys.map((k) => (
        <li key={k.id} className="flex items-center justify-between py-3">
          <div>
            <div className="text-sm font-medium">
              {k.name}{' '}
              <span className="text-[hsl(var(--muted-foreground))]">— {k.provider}</span>
            </div>
            <div className="text-xs font-mono text-[hsl(var(--muted-foreground))]">{k.key_hint}</div>
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={pending}
              onClick={() =>
                startTransition(async () => {
                  const r = await testApiKey(k.id);
                  if (r.ok) {
                    const d = r.data as { ok: boolean; status: number };
                    if (d.ok) toast.success(`${k.provider} key works (HTTP ${d.status}).`);
                    else toast.error(`${k.provider} responded HTTP ${d.status}.`);
                  } else {
                    toast.error(r.error);
                  }
                })
              }
            >
              Test
            </Button>
            <Button
              size="sm"
              variant="danger"
              disabled={pending}
              onClick={() =>
                startTransition(async () => {
                  if (!confirm(`Delete ${k.name}?`)) return;
                  const r = await deleteApiKey(k.id);
                  if (!r.ok) toast.error(r.error);
                  else toast.success('Deleted.');
                })
              }
            >
              Delete
            </Button>
          </div>
        </li>
      ))}
    </ul>
  );
}
