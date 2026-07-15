'use client';

import { useCallback, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert } from '@/components/ui/alert';
import { CitationList } from '@/components/citation-pill';
import { Bug, ShieldAlert } from 'lucide-react';
import { getAccessToken } from '@/lib/supabase/access-token';
import { parseSSE } from '@/lib/stream';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

type Citation =
  | { kind: 'jira'; key: string; field: string }
  | { kind: 'rag_chunk'; chunk_id: string }
  | { kind: 'repo_file'; repo: string; path: string; line_start: number; line_end: number };

type BugFinding = {
  id: string;
  created_at: string;
  severity: 'critical' | 'high' | 'medium' | 'low';
  title: string;
  description: string;
  file_refs: string[];
  citations: Citation[];
  status: string;
};

type HuntResult = {
  run_id: string;
  bugs: BugFinding[];
  citations: Citation[];
  confidence: number;
  clarifications: string[];
  tokens: { in: number; out: number };
  created_at: string;
};

type StepEvent = { name: string; status: 'start' | 'done'; [k: string]: unknown };

async function streamPost<TResult>(opts: {
  path: string;
  body?: unknown;
  onStep: (s: StepEvent) => void;
  onResult: (r: TResult) => void;
  onError: (msg: string) => void;
}) {
  const token = await getAccessToken();
  if (!token) {
    opts.onError('Not authenticated.');
    return;
  }
  const wsResp = await fetch(`${API_URL}/api/v1/workspaces`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!wsResp.ok) {
    opts.onError(`Could not load workspace (HTTP ${wsResp.status}).`);
    return;
  }
  const wsList = (await wsResp.json()) as { id: string }[];
  const workspaceId = wsList[0]?.id;
  if (!workspaceId) {
    opts.onError('No workspace.');
    return;
  }

  const resp = await fetch(`${API_URL}${opts.path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'X-Workspace-Id': workspaceId,
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: opts.body ? JSON.stringify(opts.body) : null,
  });
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => '');
    opts.onError(`API ${resp.status}: ${text}`);
    return;
  }

  for await (const ev of parseSSE(resp.body)) {
    try {
      if (ev.event === 'step') opts.onStep(JSON.parse(ev.data) as StepEvent);
      else if (ev.event === 'result') opts.onResult(JSON.parse(ev.data) as TResult);
      else if (ev.event === 'error') {
        const e = JSON.parse(ev.data) as { message: string };
        opts.onError(e.message);
      } else if (ev.event === 'done') return;
    } catch (err) {
      opts.onError(`Could not parse ${ev.event} event: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
}

function SeverityBadge({ severity }: { severity: BugFinding['severity'] }) {
  const cls =
    severity === 'critical'
      ? 'border-red-300 bg-red-50 text-red-800'
      : severity === 'high'
        ? 'border-amber-300 bg-amber-50 text-amber-800'
        : severity === 'medium'
          ? 'border-sky-300 bg-sky-50 text-sky-800'
          : 'border-[hsl(var(--border))] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]';
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase ${cls}`}>
      {severity}
    </span>
  );
}

export function BugHuntWorkflow({ ticketKey }: { ticketKey: string }) {
  const [steps, setSteps] = useState<StepEvent[]>([]);
  const [result, setResult] = useState<HuntResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runHunt = useCallback(async () => {
    setRunning(true);
    setError(null);
    setSteps([]);
    setResult(null);
    try {
      await streamPost<HuntResult>({
        path: '/api/v1/bugs/static',
        body: { ticket_key: ticketKey },
        onStep: (s) => setSteps((prev) => [...prev, s]),
        onResult: (r) => setResult(r),
        onError: (msg) => {
          setError(msg);
          toast.error(msg);
        },
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }, [ticketKey]);

  return (
    <div className="space-y-6">
      {steps.length > 0 && running && (
        <div className="rounded-md border border-brand/30 bg-brand/5 px-3 py-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-brand-700">
            Live activity
          </div>
          <ol className="space-y-0.5 text-xs text-[hsl(var(--muted-foreground))]">
            {steps.slice(-5).map((s, i) => (
              <li key={i}>
                <span className="font-mono">{s.name}</span> — {s.status}
              </li>
            ))}
          </ol>
        </div>
      )}

      {error && <Alert variant="error">{error}</Alert>}

      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand/10 text-brand">
              <Bug className="h-4 w-4" />
            </div>
            <div>
              <CardTitle>Static bug scan</CardTitle>
              <CardDescription>
                Cites the exact files it checked. If nothing is grounded in code, findings stay
                ticket-only and confidence reflects that.
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {!result && !running ? (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-[hsl(var(--border))] bg-[hsl(var(--muted))]/20 px-6 py-8 text-center">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10 text-brand">
                <ShieldAlert className="h-5 w-5" />
              </div>
              <p className="max-w-md text-xs text-[hsl(var(--muted-foreground))]">
                Scan this ticket against your indexed dev repo for concrete bugs and risks, each
                one cited back to a real file.
              </p>
              <Button onClick={runHunt} loading={running} loadingText="Scanning…">
                Find bugs
              </Button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={runHunt} loading={running} loadingText="Scanning…">
                {result ? 'Re-scan' : 'Find bugs'}
              </Button>
              {result && (
                <span className="text-xs text-[hsl(var(--muted-foreground))]">
                  tokens in {result.tokens.in} / out {result.tokens.out} · confidence{' '}
                  {(result.confidence * 100).toFixed(0)}%
                </span>
              )}
            </div>
          )}

          {result && result.clarifications.length > 0 && (
            <Alert variant="info">
              <div className="space-y-1">
                <div className="text-sm font-semibold text-amber-900">Notes</div>
                <ul className="ml-5 list-disc space-y-0.5 text-xs text-amber-900">
                  {result.clarifications.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </div>
            </Alert>
          )}

          {result && result.bugs.length === 0 && (
            <Alert variant="success">No findings — nothing worth flagging surfaced in this scan.</Alert>
          )}

          {result && result.bugs.length > 0 && (
            <div className="space-y-3">
              {result.bugs.map((bug) => (
                <div key={bug.id} className="rounded-lg border border-[hsl(var(--border))] p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <SeverityBadge severity={bug.severity} />
                    <span className="font-medium">{bug.title}</span>
                  </div>
                  <p className="mt-1 text-sm text-[hsl(var(--muted-foreground))]">{bug.description}</p>
                  <CitationList citations={bug.citations} />
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
