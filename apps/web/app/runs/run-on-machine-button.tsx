'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import { PlayCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { getAccessToken } from '@/lib/supabase/access-token';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

/**
 * Tiny button on the /runs row. Queues a bridge job for a past run and
 * routes the user back to the codegen page so they can watch the live
 * results stream in via the LiveRunPanel.
 */
export function RunOnMachineButton({
  runId,
  ticketKey,
}: {
  runId: string;
  ticketKey: string | null;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      size="sm"
      variant="secondary"
      disabled={busy}
      title="Queue a job for your qa-bridge CLI"
      onClick={async () => {
        setBusy(true);
        try {
          const token = await getAccessToken();
          if (!token) throw new Error('Not authenticated.');
          const wsResp = await fetch(`${API_URL}/api/v1/workspaces`, {
            headers: { Authorization: `Bearer ${token}` },
          });
          const wsList = (await wsResp.json()) as { id: string }[];
          const workspaceId = wsList[0]?.id;
          if (!workspaceId) throw new Error('No workspace.');

          const resp = await fetch(`${API_URL}/api/v1/runs/${runId}/execute`, {
            method: 'POST',
            headers: {
              Authorization: `Bearer ${token}`,
              'X-Workspace-Id': workspaceId,
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({}),
          });
          if (resp.status === 412) {
            toast.error(
              'No qa-bridge tokens. Create one in Settings → qa-bridge and run `qa-bridge listen`.',
            );
            return;
          }
          if (!resp.ok) {
            const text = await resp.text().catch(() => '');
            throw new Error(`HTTP ${resp.status}: ${text.slice(0, 160)}`);
          }
          const data = (await resp.json()) as { job_id: string };
          toast.success('Job queued. Live results loading…');
          if (ticketKey) {
            // Carry the job ID so the codegen page can attach the LiveRunPanel
            // to this specific job immediately.
            window.location.href = `/tickets/${ticketKey}/codegen?job=${data.job_id}`;
          }
        } catch (e) {
          toast.error(e instanceof Error ? e.message : String(e));
        } finally {
          setBusy(false);
        }
      }}
    >
      <PlayCircle className="mr-1 h-3.5 w-3.5" />
      {busy ? 'Queuing…' : 'Run'}
    </Button>
  );
}
