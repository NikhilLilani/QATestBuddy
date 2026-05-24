'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Alert } from '@/components/ui/alert';
import { getAccessToken } from '@/lib/supabase/access-token';
import { parseSSE } from '@/lib/stream';
import {
  PlayCircle,
  CheckCircle2,
  XCircle,
  Clock,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  X,
} from 'lucide-react';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

type TestStatus =
  | 'pending'
  | 'running'
  | 'passed'
  | 'failed'
  | 'skipped'
  | 'timedOut'
  | 'interrupted';

type TestResult = {
  test_id: string;
  file: string;
  title: string;
  project?: string | null;
  status: TestStatus;
  duration_ms?: number | null;
  retry: number;
  error_message?: string | null;
  error_stack?: string | null;
  attachments?: unknown[];
  updated_at: string;
};

type JobStatus = 'pending' | 'claimed' | 'running' | 'done' | 'failed' | 'cancelled';
type JobUpdate = {
  status: JobStatus;
  summary?: { total?: number; passed?: number; failed?: number; skipped?: number } | null;
  error_message?: string | null;
};

/**
 * "Run on my machine" panel. Sits below the bundle viewer once codegen
 * succeeds. Phase 4e — kicks off a job and tails the per-test results live
 * via SSE.
 *
 * Two modes:
 *   - `runId` only        → user clicks "Run on my machine" to queue a new job.
 *   - `initialJobId`      → attach immediately to an existing job (used when
 *                           we land here from /runs after queueing).
 */
export function LiveRunPanel({
  runId,
  initialJobId = null,
  onDismiss,
}: {
  runId?: string;
  initialJobId?: string | null;
  onDismiss?: () => void;
}) {
  const [jobId, setJobId] = useState<string | null>(initialJobId);
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [summary, setSummary] = useState<JobUpdate['summary']>(null);
  const [results, setResults] = useState<Map<string, TestResult>>(new Map());
  const [busy, setBusy] = useState(false);
  const [bridgeWarning, setBridgeWarning] = useState<string | null>(null);
  const attachedRef = useRef<string | null>(null);

  // If we were given an initial job id, attach to its live stream right away
  // without making a new execute call.
  useEffect(() => {
    if (!initialJobId || attachedRef.current === initialJobId) return;
    attachedRef.current = initialJobId;
    (async () => {
      try {
        const token = await getAccessToken();
        if (!token) throw new Error('Not authenticated.');
        const wsResp = await fetch(`${API_URL}/api/v1/workspaces`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        const wsList = (await wsResp.json()) as { id: string }[];
        const workspaceId = wsList[0]?.id;
        if (!workspaceId) throw new Error('No workspace.');
        setJobStatus('pending');
        void streamLive(initialJobId, workspaceId, token, {
          onJob: (j) => {
            setJobStatus(j.status);
            if (j.summary) setSummary(j.summary);
            if (j.error_message) setJobError(j.error_message);
          },
          onResult: (r) => {
            setResults((prev) => {
              const next = new Map(prev);
              next.set(`${r.test_id}::${r.retry}`, r);
              return next;
            });
          },
          onError: (msg) => setJobError(msg),
        });
      } catch (e) {
        setJobError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [initialJobId]);

  const startRun = useCallback(async () => {
    if (!runId) return;
    setBusy(true);
    setJobError(null);
    setBridgeWarning(null);
    setResults(new Map());
    setSummary(null);
    setJobStatus(null);
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
      if (!resp.ok) {
        const text = await resp.text().catch(() => '');
        if (resp.status === 412) {
          setBridgeWarning(
            'No qa-bridge tokens for this workspace. Create one in Settings → qa-bridge, then run `qa-bridge listen` on your machine.',
          );
          return;
        }
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
      }
      const { job_id } = (await resp.json()) as { job_id: string };
      setJobId(job_id);
      setJobStatus('pending');
      toast.success('Job queued. Waiting for your qa-bridge CLI to pick it up…');

      // Open SSE for live updates.
      void streamLive(job_id, workspaceId, token, {
        onJob: (j) => {
          setJobStatus(j.status);
          if (j.summary) setSummary(j.summary);
          if (j.error_message) setJobError(j.error_message);
        },
        onResult: (r) => {
          setResults((prev) => {
            const next = new Map(prev);
            next.set(`${r.test_id}::${r.retry}`, r);
            return next;
          });
        },
        onError: (msg) => {
          setJobError(msg);
        },
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setJobError(msg);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }, [runId]);

  const cancelRun = useCallback(async () => {
    if (!jobId) return;
    try {
      const token = await getAccessToken();
      if (!token) throw new Error('Not authenticated.');
      const wsResp = await fetch(`${API_URL}/api/v1/workspaces`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const wsList = (await wsResp.json()) as { id: string }[];
      const workspaceId = wsList[0]?.id;
      if (!workspaceId) throw new Error('No workspace.');

      const resp = await fetch(`${API_URL}/api/v1/jobs/${jobId}/cancel`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'X-Workspace-Id': workspaceId,
        },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      toast.success('Cancelled.');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  }, [jobId]);

  const resultList = useMemo(() => Array.from(results.values()), [results]);
  const passed = resultList.filter((r) => r.status === 'passed').length;
  const failed = resultList.filter((r) => r.status === 'failed' || r.status === 'timedOut').length;
  const skipped = resultList.filter((r) => r.status === 'skipped').length;
  const inflight = resultList.filter((r) => r.status === 'running').length;

  const isTerminal =
    jobStatus === 'done' || jobStatus === 'failed' || jobStatus === 'cancelled';
  const isActive = jobStatus && !isTerminal;

  return (
    <div className="rounded-lg border border-brand/30 bg-gradient-to-br from-brand/5 to-transparent p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-brand/10 text-brand">
            <PlayCircle className="h-4 w-4" />
          </div>
          <div>
            <div className="text-sm font-semibold">Run on my machine</div>
            <p className="mt-0.5 max-w-xl text-xs text-[hsl(var(--muted-foreground))]">
              Skip the download / unzip step. If <code>qa-bridge listen</code> is running on your
              laptop, we&apos;ll queue this bundle and stream the results back here as Playwright
              executes them.
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          {runId && (!jobId || isTerminal) ? (
            <Button onClick={startRun} loading={busy} loadingText="Queuing…">
              <PlayCircle className="mr-1.5 h-4 w-4" />
              {jobId ? 'Run again' : 'Run on my machine'}
            </Button>
          ) : null}
          {jobId && !isTerminal && (
            <Button onClick={cancelRun} variant="danger" size="sm">
              Cancel run
            </Button>
          )}
          {onDismiss && (isTerminal || !jobId) && (
            <button
              type="button"
              onClick={onDismiss}
              title="Dismiss this panel"
              aria-label="Dismiss"
              className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-[hsl(var(--border))] bg-white text-[hsl(var(--muted-foreground))] transition-colors hover:bg-[hsl(var(--muted))] hover:text-[hsl(var(--foreground))]"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      {bridgeWarning && (
        <Alert variant="info" className="mt-3">
          <div className="text-xs">
            <strong>qa-bridge not connected.</strong> {bridgeWarning}
            <pre className="mt-2 rounded bg-black/80 p-2 font-mono text-[11px] text-white">
              npm install -g qa-bridge{'\n'}qa-bridge login --token &lt;your token&gt;{'\n'}qa-bridge listen
            </pre>
          </div>
        </Alert>
      )}

      {jobError && <Alert variant="error" className="mt-3">{jobError}</Alert>}

      {/* Status strip */}
      {jobStatus && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-[hsl(var(--border))] bg-white px-3 py-2 text-xs">
          <JobStatusPill status={jobStatus} />
          {summary?.total !== undefined && (
            <span className="text-[hsl(var(--muted-foreground))]">
              {resultList.length}/{summary.total} reported
            </span>
          )}
          <span className="ml-auto flex items-center gap-2">
            <SummaryChip tone="emerald" value={passed} label="passed" />
            <SummaryChip tone="red" value={failed} label="failed" />
            <SummaryChip tone="gray" value={skipped} label="skipped" />
            {inflight > 0 && <SummaryChip tone="brand" value={inflight} label="running" />}
          </span>
        </div>
      )}

      {/* Results table */}
      {resultList.length > 0 && (
        <div className="mt-3 overflow-hidden rounded-md border">
          <table className="w-full table-fixed text-xs">
            <colgroup>
              <col className="w-10" />
              <col className="w-[55%]" />
              <col className="w-20" />
              <col />
            </colgroup>
            <thead className="bg-[hsl(var(--muted))]/40 text-left text-[10px] font-semibold uppercase tracking-wider text-[hsl(var(--muted-foreground))]">
              <tr>
                <th className="px-3 py-2"></th>
                <th className="px-3 py-2">Test</th>
                <th className="px-3 py-2">Duration</th>
                <th className="px-3 py-2">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {resultList
                .sort((a, b) => a.file.localeCompare(b.file) || a.title.localeCompare(b.title))
                .map((r) => (
                  <ResultRow key={`${r.test_id}::${r.retry}`} r={r} />
                ))}
            </tbody>
          </table>
        </div>
      )}

      {isActive && resultList.length === 0 && (
        <div className="mt-3 rounded-md border border-dashed border-[hsl(var(--border))] bg-[hsl(var(--muted))]/20 px-3 py-3 text-xs">
          {jobStatus === 'pending' ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2 font-medium text-[hsl(var(--foreground))]">
                <Clock className="h-3.5 w-3.5 animate-pulse text-amber-600" />
                Waiting for a qa-bridge listener to pick this job up…
              </div>
              <p className="text-[hsl(var(--muted-foreground))]">
                The CLI on your machine isn&apos;t polling yet. Open a PowerShell or CMD window
                and run:
              </p>
              <pre className="rounded bg-black/85 p-2 font-mono text-[11px] text-white">
                qa-bridge login --token &lt;your-token-from-Settings&gt;{'\n'}qa-bridge listen
              </pre>
              <p className="text-[hsl(var(--muted-foreground))]">
                Once it prints <code>✓ Connected to workspace…</code> it will claim this job
                within ~1 second and the status above will move to <em>Picked up by qa-bridge</em>.
              </p>
            </div>
          ) : jobStatus === 'claimed' ? (
            <div className="flex items-center gap-2 text-[hsl(var(--muted-foreground))]">
              <Clock className="h-3.5 w-3.5 animate-pulse" />
              Listener has claimed the job — unpacking files & running{' '}
              <code>npm install</code>…
            </div>
          ) : (
            <div className="flex items-center gap-2 text-[hsl(var(--muted-foreground))]">
              <Clock className="h-3.5 w-3.5 animate-pulse" />
              Waiting for the first test to finish… (Playwright is launching)
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ────────────────── SSE consumer ────────────────── */

async function streamLive(
  jobId: string,
  workspaceId: string,
  token: string,
  cb: {
    onJob: (j: JobUpdate) => void;
    onResult: (r: TestResult) => void;
    onError: (msg: string) => void;
  },
): Promise<void> {
  try {
    const resp = await fetch(`${API_URL}/api/v1/jobs/${jobId}/live`, {
      headers: {
        Authorization: `Bearer ${token}`,
        'X-Workspace-Id': workspaceId,
        Accept: 'text/event-stream',
      },
    });
    if (!resp.ok || !resp.body) {
      cb.onError(`Live stream failed (HTTP ${resp.status}).`);
      return;
    }
    for await (const ev of parseSSE(resp.body)) {
      if (ev.event === 'job') {
        try {
          cb.onJob(JSON.parse(ev.data) as JobUpdate);
        } catch {
          /* ignore */
        }
      } else if (ev.event === 'result') {
        try {
          cb.onResult(JSON.parse(ev.data) as TestResult);
        } catch {
          /* ignore */
        }
      } else if (ev.event === 'done') {
        return;
      }
    }
  } catch (e) {
    cb.onError(e instanceof Error ? e.message : String(e));
  }
}

/* ────────────────── small UI bits ────────────────── */

function JobStatusPill({ status }: { status: JobStatus }) {
  const map: Record<JobStatus, { label: string; cls: string; icon: React.ReactNode }> = {
    pending: {
      label: 'Queued',
      cls: 'bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]',
      icon: <Clock className="h-3 w-3" />,
    },
    claimed: {
      label: 'Picked up by qa-bridge',
      cls: 'bg-sky-100 text-sky-800',
      icon: <Clock className="h-3 w-3 animate-pulse" />,
    },
    running: {
      label: 'Running on your machine',
      cls: 'bg-brand/10 text-brand-700',
      icon: <PlayCircle className="h-3 w-3 animate-pulse" />,
    },
    done: {
      label: 'Done',
      cls: 'bg-emerald-100 text-emerald-800',
      icon: <CheckCircle2 className="h-3 w-3" />,
    },
    failed: {
      label: 'Failed',
      cls: 'bg-red-100 text-red-800',
      icon: <XCircle className="h-3 w-3" />,
    },
    cancelled: {
      label: 'Cancelled',
      cls: 'bg-amber-100 text-amber-800',
      icon: <AlertCircle className="h-3 w-3" />,
    },
  };
  const m = map[status];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${m.cls}`}
    >
      {m.icon}
      {m.label}
    </span>
  );
}

function SummaryChip({
  tone,
  value,
  label,
}: {
  tone: 'emerald' | 'red' | 'gray' | 'brand';
  value: number;
  label: string;
}) {
  const cls =
    tone === 'emerald'
      ? 'bg-emerald-100 text-emerald-800'
      : tone === 'red'
        ? 'bg-red-100 text-red-800'
        : tone === 'brand'
          ? 'bg-brand/10 text-brand-700'
          : 'bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]';
  if (value === 0 && tone !== 'brand') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-[hsl(var(--muted))] px-2 py-0.5 text-[10px] font-medium text-[hsl(var(--muted-foreground))]">
        {value} {label}
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${cls}`}
    >
      {value} {label}
    </span>
  );
}

function ResultRow({ r }: { r: TestResult }) {
  const [open, setOpen] = useState(false);
  const hasDetails = r.error_message || r.error_stack;
  return (
    <>
      <tr className="hover:bg-[hsl(var(--muted))]/30">
        <td className="px-3 py-2 align-top">
          <ResultIcon status={r.status} />
        </td>
        <td className="px-3 py-2 align-top">
          <div className="truncate font-medium" title={r.title}>
            {r.title}
          </div>
          <div className="truncate text-[10px] text-[hsl(var(--muted-foreground))]" title={r.file}>
            {r.file}
            {r.project ? ` · ${r.project}` : ''}
            {r.retry > 0 ? ` · retry ${r.retry}` : ''}
          </div>
        </td>
        <td className="px-3 py-2 align-top text-[hsl(var(--muted-foreground))]">
          {typeof r.duration_ms === 'number' ? `${(r.duration_ms / 1000).toFixed(2)}s` : '—'}
        </td>
        <td className="px-3 py-2 align-top">
          {hasDetails ? (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="inline-flex items-center gap-1 text-brand hover:underline"
            >
              {open ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              {open ? 'Hide error' : 'Show error'}
            </button>
          ) : (
            <span className="text-[hsl(var(--muted-foreground))]">—</span>
          )}
        </td>
      </tr>
      {open && hasDetails && (
        <tr className="bg-red-50/30">
          <td />
          <td colSpan={3} className="px-3 py-2">
            {r.error_message && (
              <div className="mb-1 text-xs font-medium text-red-800">{r.error_message}</div>
            )}
            {r.error_stack && (
              <pre className="max-h-64 overflow-auto rounded bg-black/80 p-2 font-mono text-[10px] leading-relaxed text-white">
                {r.error_stack}
              </pre>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function ResultIcon({ status }: { status: TestStatus }) {
  switch (status) {
    case 'passed':
      return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
    case 'failed':
    case 'timedOut':
    case 'interrupted':
      return <XCircle className="h-4 w-4 text-red-600" />;
    case 'skipped':
      return <AlertCircle className="h-4 w-4 text-amber-500" />;
    case 'running':
      return <PlayCircle className="h-4 w-4 animate-pulse text-brand" />;
    default:
      return <Clock className="h-4 w-4 text-[hsl(var(--muted-foreground))]" />;
  }
}
