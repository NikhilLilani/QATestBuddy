'use client';

import { useActionState, useState, useTransition } from 'react';
import { toast } from 'sonner';
import { createBridgeToken, revokeBridgeToken } from '../actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert } from '@/components/ui/alert';
import { Copy, Check } from 'lucide-react';

type Token = {
  id: string;
  label: string;
  last_seen_at: string | null;
  revoked_at: string | null;
  created_at: string;
};

export function BridgeTokensSection({ initial }: { initial: Token[] }) {
  const [state, action, pending] = useActionState(createBridgeToken, null);
  const [busy, startTransition] = useTransition();
  const [revealedToken, setRevealedToken] = useState<string | null>(null);
  const [copied, setCopied] = useState<'token' | 'cmd' | null>(null);

  // Surface the freshly created token once.
  if (state?.ok && state.data.token && revealedToken !== state.data.token) {
    setRevealedToken(state.data.token);
  }

  const copyToClipboard = async (text: string, which: 'token' | 'cmd') => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(which);
      toast.success(which === 'token' ? 'Token copied.' : 'Login command copied.');
      setTimeout(() => setCopied(null), 1500);
    } catch {
      toast.error('Could not copy — browser permission denied.');
    }
  };

  return (
    <div className="space-y-6">
      <form action={action} className="grid gap-3 sm:grid-cols-[1fr_auto]">
        <div className="space-y-1.5">
          <Label htmlFor="label">Label</Label>
          <Input id="label" name="label" placeholder="e.g. work laptop" required />
        </div>
        <div className="flex items-end">
          <Button type="submit" disabled={pending}>
            {pending ? 'Creating…' : 'Create token'}
          </Button>
        </div>
        {state && !state.ok && (
          <div className="sm:col-span-2">
            <Alert variant="error">{state.error}</Alert>
          </div>
        )}
      </form>

      {revealedToken && (
        <Alert variant="success">
          <div className="space-y-2">
            <div className="font-medium">Your new token (shown once):</div>

            {/* Token + copy button */}
            <div className="flex items-stretch gap-2">
              <code className="min-w-0 flex-1 break-all rounded bg-white/70 p-2 font-mono text-xs">
                {revealedToken}
              </code>
              <button
                type="button"
                onClick={() => copyToClipboard(revealedToken, 'token')}
                className="inline-flex shrink-0 items-center gap-1 rounded border border-emerald-300 bg-white/80 px-2.5 text-xs font-medium text-emerald-800 transition-colors hover:bg-emerald-50"
                aria-label="Copy token"
              >
                {copied === 'token' ? (
                  <>
                    <Check className="h-3.5 w-3.5" /> Copied
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5" /> Copy
                  </>
                )}
              </button>
            </div>

            {/* One-line login command + copy */}
            <div className="space-y-1 pt-1">
              <div className="text-xs text-[hsl(var(--foreground))]/80">
                Use it locally (PowerShell or CMD):
              </div>
              <div className="flex items-stretch gap-2">
                <code className="min-w-0 flex-1 break-all rounded bg-black/85 p-2 font-mono text-[11px] text-white">
                  qa-bridge login --token {revealedToken}
                </code>
                <button
                  type="button"
                  onClick={() =>
                    copyToClipboard(`qa-bridge login --token ${revealedToken}`, 'cmd')
                  }
                  className="inline-flex shrink-0 items-center gap-1 rounded border border-emerald-300 bg-white/80 px-2.5 text-xs font-medium text-emerald-800 transition-colors hover:bg-emerald-50"
                  aria-label="Copy login command"
                >
                  {copied === 'cmd' ? (
                    <>
                      <Check className="h-3.5 w-3.5" /> Copied
                    </>
                  ) : (
                    <>
                      <Copy className="h-3.5 w-3.5" /> Copy
                    </>
                  )}
                </button>
              </div>
              <div className="text-[11px] text-[hsl(var(--muted-foreground))]">
                Then run <code className="font-mono">qa-bridge listen</code> in the same window.
              </div>
            </div>
          </div>
        </Alert>
      )}

      <div>
        <h3 className="mb-2 text-sm font-medium">Existing tokens</h3>
        {initial.length === 0 ? (
          <p className="text-sm text-[hsl(var(--muted-foreground))]">No tokens yet.</p>
        ) : (
          <ul className="divide-y">
            {initial.map((t) => (
              <li key={t.id} className="flex items-center justify-between py-3">
                <div>
                  <div className="text-sm font-medium">{t.label}</div>
                  <div className="text-xs text-[hsl(var(--muted-foreground))]">
                    {t.revoked_at ? 'revoked' : t.last_seen_at ? `last seen ${t.last_seen_at}` : 'never used'}
                  </div>
                </div>
                {!t.revoked_at && (
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={busy}
                    onClick={() =>
                      startTransition(async () => {
                        if (!confirm(`Revoke ${t.label}?`)) return;
                        const r = await revokeBridgeToken(t.id);
                        if (!r.ok) toast.error(r.error);
                        else toast.success('Revoked.');
                      })
                    }
                  >
                    Revoke
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
