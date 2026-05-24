'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import JSZip from 'jszip';
import { Button } from '@/components/ui/button';
import { getAccessToken } from '@/lib/supabase/access-token';
import { Download } from 'lucide-react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

type RunDetails = {
  id: string;
  jira_key: string | null;
  title: string | null;
  files: Array<{ path: string; content: string }>;
};

export function ZipDownload({ runId, ticketKey }: { runId: string; ticketKey: string }) {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      size="sm"
      variant="secondary"
      disabled={busy}
      title="Re-download .zip bundle"
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

          const resp = await fetch(`${API_URL}/api/v1/runs/${runId}/files`, {
            headers: {
              Authorization: `Bearer ${token}`,
              'X-Workspace-Id': workspaceId,
            },
          });
          if (!resp.ok) {
            throw new Error(`API ${resp.status}: ${await resp.text().catch(() => '')}`);
          }
          const details = (await resp.json()) as RunDetails;
          if (details.files.length === 0) {
            toast.error('This run has no files (older single-file run?).');
            return;
          }
          const zip = new JSZip();
          for (const f of details.files) zip.file(f.path, f.content);
          const blob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `${ticketKey.toLowerCase()}-bundle.zip`;
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          toast.success(`Downloaded ${details.files.length} files.`);
        } catch (e) {
          toast.error(e instanceof Error ? e.message : String(e));
        } finally {
          setBusy(false);
        }
      }}
    >
      <Download className="mr-1 h-3.5 w-3.5" />
      {busy ? 'Building…' : '.zip'}
    </Button>
  );
}
