'use client';

import { useActionState, useTransition } from 'react';
import { toast } from 'sonner';
import { connectJira, disconnectJira, testJira } from '../actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert } from '@/components/ui/alert';

type JiraStatus = { connected: boolean; base_url?: string; status?: string };

export function JiraSection({ initial }: { initial: JiraStatus }) {
  const [state, action, pending] = useActionState(connectJira, null);
  const [busy, startTransition] = useTransition();

  if (initial.connected && !state?.ok) {
    return (
      <div className="space-y-3">
        <p className="text-sm">
          Connected to <span className="font-mono">{initial.base_url}</span>
        </p>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={busy}
            onClick={() =>
              startTransition(async () => {
                const r = await testJira();
                if (r.ok) {
                  const d = r.data as { ok: boolean; status: number };
                  if (d.ok) toast.success(`Jira works (HTTP ${d.status}).`);
                  else toast.error(`Jira responded HTTP ${d.status}.`);
                } else toast.error(r.error);
              })
            }
          >
            Test connection
          </Button>
          <Button
            variant="danger"
            size="sm"
            disabled={busy}
            onClick={() =>
              startTransition(async () => {
                if (!confirm('Disconnect Jira?')) return;
                const r = await disconnectJira();
                if (!r.ok) toast.error(r.error);
                else toast.success('Disconnected.');
              })
            }
          >
            Disconnect
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form action={action} className="grid gap-3 sm:grid-cols-2">
      <div className="space-y-1.5 sm:col-span-2">
        <Label htmlFor="base_url">Base URL</Label>
        <Input id="base_url" name="base_url" placeholder="https://yourorg.atlassian.net" required />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="email">Email</Label>
        <Input id="email" name="email" type="email" required />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="pat">API token</Label>
        <Input id="pat" name="pat" type="password" required />
      </div>
      {state && !state.ok && (
        <div className="sm:col-span-2">
          <Alert variant="error">{state.error}</Alert>
        </div>
      )}
      <div className="sm:col-span-2">
        <Button type="submit" disabled={pending}>
          {pending ? 'Saving…' : 'Connect Jira'}
        </Button>
      </div>
    </form>
  );
}
