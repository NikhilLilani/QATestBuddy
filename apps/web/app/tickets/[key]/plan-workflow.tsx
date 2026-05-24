'use client';

import { useCallback, useState, useTransition } from 'react';
import { toast } from 'sonner';
import { Checkbox } from '@/components/ui/checkbox';
import { bulkDeleteCasesAction } from '@/app/plans/actions';
import * as XLSX from 'xlsx';
import {
  AlignmentType,
  Document,
  HeadingLevel,
  Packer,
  Paragraph,
  TextRun,
} from 'docx';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert } from '@/components/ui/alert';
import { CitationList } from '@/components/citation-pill';
import { WorkflowStepper, type Step as StepperStep } from '@/components/workflow-stepper';
import { ClipboardList, ListChecks, Sparkles, PlayCircle } from 'lucide-react';
import { getAccessToken } from '@/lib/supabase/access-token';
import { parseSSE } from '@/lib/stream';

const TABLE_PREVIEW_LIMIT = 20;

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

type Citation =
  | { kind: 'jira'; key: string; field: string }
  | { kind: 'rag_chunk'; chunk_id: string }
  | { kind: 'repo_file'; repo: string; path: string; line_start: number; line_end: number };

type PlanData = {
  scope: string;
  in_scope: string[];
  out_of_scope: string[];
  risks: string[];
  environments: string[];
  data_needs: string[];
  exit_criteria: string[];
};

type TicketSummary = {
  key: string;
  title?: string | null;
  description?: string | null;
  status?: string | null;
  issuetype?: string | null;
  priority?: string | null;
  url?: string;
};

type PlanResult = {
  id: string;
  ticket: TicketSummary;
  data: PlanData;
  citations: Citation[];
  confidence: number;
  clarifications_used: number;
  tokens: { in: number; out: number };
  created_at: string;
};

type Step = { action: string; expected: string };
type TestCase = {
  id?: string;
  ord?: number;
  title: string;
  preconditions: string[];
  steps: Step[];
  priority: string;
  type: string;
  data: Record<string, string>;
  tags: string[];
  citations?: Citation[];
  automation_candidate?: 'yes' | 'partial' | 'no';
  automation_reason?: string;
};

type CasesResult = {
  // /api/v1/plans/{id}/cases returns only cases.
  // /api/v1/cases (direct) returns plan_id + cases (plan was auto-created).
  plan_id?: string;
  cases: TestCase[];
  confidence: number;
  clarifications_used: number;
  clarifications?: string[];
  tokens: { in: number; out: number };
};

type CodegenResult = {
  code: string;
  file_name: string;
  notes: string[];
  run_id: string;
  citations: Citation[];
  tokens: { in: number; out: number };
};

type StepEvent = { name: string; status: 'start' | 'done'; [k: string]: unknown };

/* eslint-disable no-console */
async function streamPost<TResult>(opts: {
  path: string;
  body?: unknown;
  onStep: (s: StepEvent) => void;
  onResult: (r: TResult) => void;
  onError: (msg: string) => void;
}) {
  console.log('[qatb] streamPost START', opts.path);
  const token = await getAccessToken();
  console.log('[qatb] token =', token ? `${token.slice(0, 12)}…(${token.length}ch)` : 'MISSING');
  if (!token) {
    opts.onError('Not authenticated.');
    return;
  }
  const wsResp = await fetch(`${API_URL}/api/v1/workspaces`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  console.log('[qatb] /workspaces ->', wsResp.status);
  if (!wsResp.ok) {
    opts.onError(`Could not load workspace (HTTP ${wsResp.status}).`);
    return;
  }
  const wsList = (await wsResp.json()) as { id: string }[];
  const workspaceId = wsList[0]?.id;
  console.log('[qatb] workspaceId =', workspaceId);
  if (!workspaceId) {
    opts.onError('No workspace.');
    return;
  }

  console.log('[qatb] POST', opts.path, 'body =', opts.body);
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
  console.log('[qatb] POST resp', opts.path, 'status =', resp.status, 'body? =', !!resp.body, 'content-type =', resp.headers.get('content-type'));
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => '');
    console.error('[qatb] POST failed text =', text);
    opts.onError(`API ${resp.status}: ${text}`);
    return;
  }

  let eventCount = 0;
  for await (const ev of parseSSE(resp.body)) {
    eventCount += 1;
    console.log(`[qatb][SSE #${eventCount}]`, ev.event, ev.data.slice(0, 200) + (ev.data.length > 200 ? '…' : ''));
    try {
      if (ev.event === 'step') opts.onStep(JSON.parse(ev.data) as StepEvent);
      else if (ev.event === 'result') {
        const parsed = JSON.parse(ev.data) as TResult;
        console.log('[qatb] calling onResult with keys =', Object.keys(parsed as object));
        opts.onResult(parsed);
      } else if (ev.event === 'error') {
        const e = JSON.parse(ev.data) as { message: string };
        opts.onError(e.message);
      } else if (ev.event === 'done') {
        console.log(`[qatb] done received after ${eventCount} events`);
        return;
      }
    } catch (err) {
      console.error('[qatb][SSE parse error]', ev.event, err, 'raw len =', ev.data.length);
      opts.onError(`Could not parse ${ev.event} event: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
  console.log(`[qatb] stream ended after ${eventCount} events (no explicit done)`);
}

export function PlanWorkflow({ workspaceId, ticketKey }: { workspaceId: string; ticketKey: string }) {
  void workspaceId;
  const [steps, setSteps] = useState<StepEvent[]>([]);
  const [plan, setPlan] = useState<PlanResult | null>(null);
  const [cases, setCases] = useState<TestCase[]>([]);
  const [caseClarifications, setCaseClarifications] = useState<string[]>([]);
  const [codegen, setCodegen] = useState<CodegenResult | null>(null);
  const [planRunning, setPlanRunning] = useState(false);
  const [casesRunning, setCasesRunning] = useState(false);
  const [codegenRunning, setCodegenRunning] = useState(false);
  const [baseUrl, setBaseUrl] = useState('https://staging.example.com');
  const [frameworkHints, setFrameworkHints] = useState('');
  const [error, setError] = useState<string | null>(null);

  const runPlan = useCallback(async () => {
    console.log('[qatb] runPlan clicked');
    setPlanRunning(true);
    setError(null);
    setSteps([]);
    setPlan(null);
    setCases([]);
    try {
      await streamPost<PlanResult>({
        path: '/api/v1/plans',
        body: { ticket_key: ticketKey },
        onStep: (s) => setSteps((prev) => [...prev, s]),
        onResult: (r) => {
          console.log('[qatb] setPlan called', r);
          setPlan(r);
        },
        onError: (msg) => {
          setError(msg);
          toast.error(msg);
        },
      });
    } catch (e) {
      console.error('[qatb] streamPost threw', e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPlanRunning(false);
    }
  }, [ticketKey]);

  const runCases = useCallback(async () => {
    setCasesRunning(true);
    setError(null);
    // If a plan exists, use the plan-based endpoint. Otherwise generate
    // cases directly from the ticket via the standalone /cases endpoint
    // (which auto-creates a stub plan on the backend).
    const path = plan ? `/api/v1/plans/${plan.id}/cases` : '/api/v1/cases';
    const body = plan ? undefined : { ticket_key: ticketKey };
    await streamPost<CasesResult>({
      path,
      body,
      onStep: (s) => setSteps((prev) => [...prev, s]),
      onResult: (r) => {
        setCases(r.cases);
        setCaseClarifications(r.clarifications ?? []);
        // If the backend auto-created a stub plan (direct cases flow), surface
        // a minimal plan object so the Playwright automation card unlocks.
        if (!plan && r.plan_id) {
          setPlan({
            id: r.plan_id,
            ticket: { key: ticketKey },
            data: {
              scope: '(Cases generated directly from ticket — no separate plan)',
              in_scope: [],
              out_of_scope: [],
              risks: [],
              environments: [],
              data_needs: [],
              exit_criteria: [],
            },
            citations: [{ kind: 'jira', key: ticketKey, field: 'description' }],
            confidence: 1,
            clarifications_used: 0,
            tokens: r.tokens,
            created_at: new Date().toISOString(),
          });
        }
      },
      onError: (msg) => {
        setError(msg);
        toast.error(msg);
      },
    });
    setCasesRunning(false);
  }, [plan, ticketKey]);

  const runCodegen = useCallback(async () => {
    if (!plan || cases.length === 0) return;
    if (!baseUrl.trim()) {
      toast.error('Base URL is required.');
      return;
    }
    setCodegenRunning(true);
    setError(null);
    setCodegen(null);
    await streamPost<CodegenResult>({
      path: `/api/v1/plans/${plan.id}/codegen`,
      body: {
        base_url: baseUrl.trim(),
        framework_hints: frameworkHints,
        test_file_name: `${ticketKey.toLowerCase()}.spec.ts`,
      },
      onStep: (s) => setSteps((prev) => [...prev, s]),
      onResult: (r) => setCodegen(r),
      onError: (msg) => {
        setError(msg);
        toast.error(msg);
      },
    });
    setCodegenRunning(false);
  }, [plan, cases.length, baseUrl, frameworkHints, ticketKey]);

  // Derive workflow stage states for the stepper at the top.
  const planState: StepperStep['state'] = planRunning
    ? 'current'
    : plan
      ? 'done'
      : 'current';
  const casesState: StepperStep['state'] = casesRunning
    ? 'current'
    : cases.length > 0
      ? 'done'
      : plan
        ? 'current'
        : 'pending';
  const autoState: StepperStep['state'] = cases.length > 0 ? 'current' : 'pending';

  const stepperSteps: StepperStep[] = [
    {
      label: 'Plan',
      hint: plan ? `${(plan.confidence * 100).toFixed(0)}% confidence` : 'Grounded in Jira',
      state: planState,
    },
    {
      label: 'Test cases',
      hint: cases.length > 0 ? `${cases.length} case${cases.length === 1 ? '' : 's'}` : 'Derived from plan',
      state: casesState,
    },
    {
      label: 'Automation',
      hint: 'Playwright spec',
      state: autoState,
    },
  ];

  const planBadge =
    planState === 'done' ? (
      <StageBadge tone="success">Ready</StageBadge>
    ) : planRunning ? (
      <StageBadge tone="brand">Generating…</StageBadge>
    ) : (
      <StageBadge tone="muted">Not started</StageBadge>
    );

  const casesBadge =
    cases.length > 0 ? (
      <StageBadge tone="success">{cases.length} case{cases.length === 1 ? '' : 's'}</StageBadge>
    ) : casesRunning ? (
      <StageBadge tone="brand">Generating…</StageBadge>
    ) : (
      <StageBadge tone="muted">Not started</StageBadge>
    );

  return (
    <div className="space-y-6">
      {/* Workflow stepper — gives at-a-glance progress */}
      <div className="rounded-lg border border-[hsl(var(--border))] bg-white px-4 py-3">
        <WorkflowStepper steps={stepperSteps} />
      </div>

      {/* Live step stream (shared across stages) */}
      {steps.length > 0 && (planRunning || casesRunning || codegenRunning) && (
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

      {/* ----------------------- Stage 1: Plan ----------------------- */}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand/10 text-brand">
                <ClipboardList className="h-4 w-4" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <CardTitle>Test plan</CardTitle>
                  {planBadge}
                </div>
                <CardDescription>
                  Grounded in this ticket. Citations link back to the Jira fields they cover.
                </CardDescription>
              </div>
            </div>
            {plan && (
              <Button size="sm" variant="secondary" onClick={() => downloadPlanDOCX(plan, ticketKey)}>
                Download .docx
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {!plan && !planRunning ? (
            <EmptyState
              icon={<Sparkles className="h-5 w-5" />}
              title="No plan yet"
              description="Generate a grounded test plan from this ticket. The agent cites the Jira fields it used so you can verify nothing was hallucinated."
              cta={
                <Button onClick={runPlan} loading={planRunning} loadingText="Generating…">
                  Generate plan
                </Button>
              }
            />
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={runPlan} loading={planRunning} loadingText="Generating…">
                {plan ? 'Regenerate plan' : 'Generate plan'}
              </Button>
              {plan && (
                <span className="text-xs text-[hsl(var(--muted-foreground))]">
                  tokens in {plan.tokens.in} / out {plan.tokens.out}
                  {plan.clarifications_used > 0
                    ? ` · ${plan.clarifications_used} clarification(s)`
                    : ''}
                </span>
              )}
            </div>
          )}

          {plan && (
            <div className="space-y-4 text-sm">
              <section>
                <h3 className="font-semibold">Scope</h3>
                <p className="text-[hsl(var(--muted-foreground))]">{plan.data.scope}</p>
                <CitationList citations={plan.citations} />
              </section>
              <PlanList title="In scope" items={plan.data.in_scope} />
              <PlanList title="Out of scope" items={plan.data.out_of_scope} />
              <PlanList title="Risks" items={plan.data.risks} />
              <PlanList title="Environments" items={plan.data.environments} />
              <PlanList title="Data needs" items={plan.data.data_needs} />
              <PlanList title="Exit criteria" items={plan.data.exit_criteria} />
            </div>
          )}
        </CardContent>
      </Card>

      {/* ----------------------- Stage 2: Cases ----------------------- */}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand/10 text-brand">
                <ListChecks className="h-4 w-4" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <CardTitle>Test cases</CardTitle>
                  {casesBadge}
                </div>
                <CardDescription>
                  {plan
                    ? 'Derived from the plan above. '
                    : 'Generated directly from the Jira ticket (no plan required). '}
                  The preview shows the first {TABLE_PREVIEW_LIMIT}; the downloaded file contains
                  every case.
                </CardDescription>
              </div>
            </div>
            {cases.length > 0 && (
              <div className="flex gap-2">
                <Button size="sm" variant="secondary" onClick={() => downloadCSV(cases, ticketKey)}>
                  Download .csv
                </Button>
                <Button size="sm" onClick={() => downloadXLSX(cases, ticketKey)}>
                  Download .xlsx
                </Button>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {cases.length === 0 && !casesRunning ? (
            <EmptyState
              icon={<ListChecks className="h-5 w-5" />}
              title="No test cases yet"
              description={
                plan
                  ? 'Generate concrete cases from the plan above. Each case includes preconditions, steps and expected results.'
                  : 'Skip the plan and generate cases directly from the ticket. A stub plan will be created automatically.'
              }
              cta={
                <Button onClick={runCases} loading={casesRunning} loadingText="Generating…">
                  {plan ? 'Generate cases' : 'Generate cases (skip plan)'}
                </Button>
              }
            />
          ) : (
            <Button onClick={runCases} loading={casesRunning} loadingText="Generating…">
              {cases.length > 0
                ? 'Regenerate cases'
                : plan
                  ? 'Generate cases'
                  : 'Generate cases (skip plan)'}
            </Button>
          )}

            {caseClarifications.length > 0 && (
              <Alert variant="info">
                <div className="space-y-1">
                  <div className="text-sm font-semibold text-amber-900">
                    The agent has {caseClarifications.length} question
                    {caseClarifications.length !== 1 ? 's' : ''} about this ticket:
                  </div>
                  <ul className="ml-5 list-disc space-y-0.5 text-xs text-amber-900">
                    {caseClarifications.map((q, i) => (
                      <li key={i}>{q}</li>
                    ))}
                  </ul>
                  <p className="mt-1 text-[11px] text-[hsl(var(--muted-foreground))]">
                    Tip: add the answers to your ticket description (or the{' '}
                    <em>Extra guidance</em> field on the codegen page) and click{' '}
                    <strong>Regenerate cases</strong> for tighter, more accurate coverage.
                  </p>
                </div>
              </Alert>
            )}
            {cases.length > 0 && (
              <>
                <CasesTable
                  cases={cases.slice(0, TABLE_PREVIEW_LIMIT)}
                  onBulkDeleted={(deletedIds) =>
                    setCases((prev) => prev.filter((c) => !c.id || !deletedIds.has(c.id)))
                  }
                />
                {cases.length > TABLE_PREVIEW_LIMIT && (
                  <Alert variant="info">
                    Showing the first <strong>{TABLE_PREVIEW_LIMIT}</strong> of{' '}
                    <strong>{cases.length}</strong> test cases. Open the full set in Excel — click{' '}
                    <strong>Download .xlsx</strong> at the top right of this card.
                  </Alert>
                )}
                {cases.length <= TABLE_PREVIEW_LIMIT && (
                  <p className="text-xs text-[hsl(var(--muted-foreground))]">
                    Showing all <strong>{cases.length}</strong> cases. Use{' '}
                    <strong>Download .xlsx</strong> for an offline copy.
                  </p>
                )}
              </>
            )}
          </CardContent>
        </Card>

      {cases.length > 0 && (() => {
        const automatable = cases.filter(
          (c) => (c.automation_candidate ?? 'yes') !== 'no',
        ).length;
        const manual = cases.length - automatable;
        return (
          <Card className="border-brand/40 bg-gradient-to-br from-brand/5 to-transparent">
            <CardContent className="flex flex-wrap items-center justify-between gap-3 py-5">
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-brand text-white">
                  <PlayCircle className="h-5 w-5" />
                </div>
                <div>
                  <div className="text-base font-semibold">
                    Ready to generate automation?
                  </div>
                  <p className="mt-1 text-sm text-[hsl(var(--muted-foreground))]">
                    <strong>{automatable}</strong> of {cases.length} cases will be turned
                    into Playwright code.{' '}
                    {manual > 0 && (
                      <span>
                        {manual} {manual === 1 ? 'case is' : 'cases are'} marked manual
                        — left as documentation, not generated.
                      </span>
                    )}
                  </p>
                </div>
              </div>
              <a href={`/tickets/${ticketKey}/codegen`}>
                <Button>Next: Generate automation →</Button>
              </a>
            </CardContent>
          </Card>
        );
      })()}
    </div>
  );
}

/* ---------------------------- Cases table view --------------------------- */

function CasesTable({
  cases,
  onBulkDeleted,
}: {
  cases: TestCase[];
  onBulkDeleted: (ids: Set<string>) => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [pending, startTransition] = useTransition();

  // Only cases that have a server-side id can be bulk-deleted
  const deletable = cases.filter((c) => !!c.id);
  const allChecked = deletable.length > 0 && selected.size === deletable.length;
  const someChecked = selected.size > 0 && !allChecked;

  const toggleOne = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleAll = () =>
    setSelected((prev) =>
      prev.size === deletable.length ? new Set() : new Set(deletable.map((c) => c.id!)),
    );

  return (
    <div className="space-y-2">
      {selected.size > 0 && (
        <div className="animate-fade-in flex flex-wrap items-center justify-between gap-2 rounded-md border border-brand/30 bg-brand/5 px-4 py-2 text-sm">
          <div>
            <strong>{selected.size}</strong> of {deletable.length} selected
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setSelected(new Set())}
              disabled={pending}
            >
              Clear
            </Button>
            {confirming ? (
              <>
                <span className="text-xs text-red-700">
                  Delete {selected.size} test case{selected.size !== 1 ? 's' : ''}?
                </span>
                <Button
                  size="sm"
                  variant="danger"
                  loading={pending}
                  loadingText="Deleting…"
                  onClick={() =>
                    startTransition(async () => {
                      const ids = Array.from(selected);
                      const r = await bulkDeleteCasesAction(ids);
                      if (r.ok) {
                        toast.success(`Deleted ${r.data.deleted} test case(s).`);
                        onBulkDeleted(new Set(ids));
                        setSelected(new Set());
                        setConfirming(false);
                      } else {
                        toast.error(r.error);
                      }
                    })
                  }
                >
                  Yes, delete
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setConfirming(false)}
                  disabled={pending}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <Button size="sm" variant="danger" onClick={() => setConfirming(true)}>
                Delete selected
              </Button>
            )}
          </div>
        </div>
      )}

      <div className="overflow-x-auto rounded-md border">
        <table className="min-w-full text-sm">
          <thead className="bg-[hsl(var(--muted))]/50 text-left text-xs uppercase tracking-wide">
            <tr>
              <th className="w-10 px-3 py-2">
                <Checkbox
                  checked={allChecked}
                  indeterminate={someChecked}
                  onChange={toggleAll}
                  aria-label="Select all visible cases"
                  disabled={deletable.length === 0}
                />
              </th>
              <th className="w-16 px-3 py-2">TC No.</th>
              <th className="px-3 py-2">Test Description / Scenario</th>
              <th className="px-3 py-2">Test Steps</th>
              <th className="px-3 py-2">Expected Result</th>
              <th className="w-20 px-3 py-2">Priority</th>
              <th className="w-24 px-3 py-2">Type</th>
              <th className="w-32 px-3 py-2">Automate?</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {cases.map((c, i) => {
              const isSel = c.id ? selected.has(c.id) : false;
              return (
                <tr
                  key={c.id ?? i}
                  className={
                    isSel
                      ? 'align-top bg-brand/5 hover:bg-brand/10'
                      : 'align-top hover:bg-[hsl(var(--muted))]/30'
                  }
                >
                  <td className="px-3 py-2">
                    <Checkbox
                      checked={isSel}
                      disabled={!c.id}
                      onChange={() => c.id && toggleOne(c.id)}
                      aria-label={`Select case ${i + 1}`}
                    />
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">
                    TC-{String(i + 1).padStart(3, '0')}
                  </td>
                  <td className="px-3 py-2">
                    <div className="font-medium">{c.title}</div>
                    {c.preconditions.length > 0 && (
                      <div className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">
                        <span className="font-semibold">Pre:</span> {c.preconditions.join('; ')}
                      </div>
                    )}
                    {c.tags.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {c.tags.map((t) => (
                          <span
                            key={t}
                            className="rounded bg-[hsl(var(--muted))] px-1 text-[10px]"
                          >
                            #{t}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <ol className="ml-4 list-decimal space-y-0.5">
                      {c.steps.map((s, j) => (
                        <li key={j}>{s.action}</li>
                      ))}
                    </ol>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <ol className="ml-4 list-decimal space-y-0.5">
                      {c.steps.map((s, j) => (
                        <li key={j}>{s.expected}</li>
                      ))}
                    </ol>
                  </td>
                  <td className="px-3 py-2">
                    <span className="rounded bg-brand/10 px-1.5 py-0.5 font-mono text-[10px] text-brand-700">
                      {c.priority}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <span className="rounded bg-[hsl(var(--muted))] px-1.5 py-0.5 font-mono text-[10px]">
                      {c.type}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <AutomationBadge
                      candidate={c.automation_candidate}
                      reason={c.automation_reason}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ---------------------------- CSV / Excel export ------------------------- */

function csvEscape(s: string): string {
  if (s == null) return '';
  // Wrap in double-quotes if contains comma, quote, newline; escape internal quotes
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function buildCSV(cases: TestCase[]): string {
  const headers = ['TC No.', 'Test Description / Scenario', 'Preconditions', 'Test Steps', 'Expected Result', 'Priority', 'Type', 'Tags'];
  const rows = cases.map((c, i) => [
    `TC-${String(i + 1).padStart(3, '0')}`,
    c.title,
    c.preconditions.join('; '),
    c.steps.map((s, j) => `${j + 1}. ${s.action}`).join('\n'),
    c.steps.map((s, j) => `${j + 1}. ${s.expected}`).join('\n'),
    c.priority,
    c.type,
    c.tags.join(', '),
  ]);
  return [headers, ...rows].map((row) => row.map((cell) => csvEscape(String(cell))).join(',')).join('\r\n');
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function copyText(text: string, successMessage: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(successMessage);
  } catch {
    toast.error('Could not copy to clipboard (browser permission denied).');
  }
}

function downloadText(text: string, filename: string): void {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  downloadBlob(blob, filename);
}

function downloadCSV(cases: TestCase[], ticketKey: string): void {
  const csv = buildCSV(cases);
  // Prepend BOM so Excel opens UTF-8 correctly
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });
  downloadBlob(blob, `${ticketKey}-test-cases.csv`);
}

/* ---------------------------- Word .docx export ------------------------- */

function planSection(heading: string, items: string[]): Paragraph[] {
  if (!items || items.length === 0) return [];
  const out: Paragraph[] = [
    new Paragraph({
      heading: HeadingLevel.HEADING_2,
      children: [new TextRun({ text: heading, bold: true })],
      spacing: { before: 240, after: 120 },
    }),
  ];
  for (const item of items) {
    out.push(
      new Paragraph({
        bullet: { level: 0 },
        children: [new TextRun(item)],
        spacing: { after: 60 },
      }),
    );
  }
  return out;
}

function citationLine(citations: Citation[]): string {
  return citations
    .map((c) => {
      if (c.kind === 'jira') return `${c.key}·${c.field}`;
      if (c.kind === 'rag_chunk') return `doc·${c.chunk_id.slice(0, 8)}`;
      return `${c.path}:${c.line_start}`;
    })
    .join(', ');
}

async function downloadPlanDOCX(plan: PlanResult, ticketKey: string): Promise<void> {
  const created = new Date().toLocaleString();
  const children: Paragraph[] = [
    // Cover header
    new Paragraph({
      heading: HeadingLevel.TITLE,
      alignment: AlignmentType.LEFT,
      children: [new TextRun({ text: `Test Plan — ${ticketKey}`, bold: true })],
    }),
    new Paragraph({
      children: [
        new TextRun({ text: plan.ticket.title ?? '', italics: true, size: 22 }),
      ],
      spacing: { after: 120 },
    }),
    new Paragraph({
      children: [
        new TextRun({ text: 'Generated by QAtestbuddy', size: 18, color: '666666' }),
        new TextRun({ text: '  ·  ', size: 18, color: '666666' }),
        new TextRun({ text: created, size: 18, color: '666666' }),
        new TextRun({ text: '  ·  confidence ', size: 18, color: '666666' }),
        new TextRun({
          text: `${(plan.confidence * 100).toFixed(0)}%`,
          size: 18,
          color: '666666',
        }),
      ],
      spacing: { after: 240 },
    }),
    new Paragraph({
      children: [
        new TextRun({
          text: `Sources: ${citationLine(plan.citations)}`,
          size: 18,
          color: '666666',
        }),
      ],
      spacing: { after: 360 },
    }),

    // Scope
    new Paragraph({
      heading: HeadingLevel.HEADING_2,
      children: [new TextRun({ text: 'Scope', bold: true })],
      spacing: { before: 240, after: 120 },
    }),
    new Paragraph({
      children: [new TextRun(plan.data.scope)],
      spacing: { after: 120 },
    }),

    ...planSection('In scope', plan.data.in_scope),
    ...planSection('Out of scope', plan.data.out_of_scope),
    ...planSection('Risks', plan.data.risks),
    ...planSection('Environments', plan.data.environments),
    ...planSection('Data needs', plan.data.data_needs),
    ...planSection('Exit criteria', plan.data.exit_criteria),
  ];

  const doc = new Document({
    creator: 'QAtestbuddy',
    title: `${ticketKey} test plan`,
    description: `Test plan generated for Jira ticket ${ticketKey}`,
    styles: {
      default: {
        document: {
          run: { font: 'Calibri', size: 22 },
        },
      },
    },
    sections: [{ children }],
  });

  const blob = await Packer.toBlob(doc);
  downloadBlob(blob, `${ticketKey}-test-plan.docx`);
}

function downloadXLSX(cases: TestCase[], ticketKey: string): void {
  // Real .xlsx using SheetJS — no Excel "format mismatch" warning.
  const headers = [
    'TC No.',
    'Test Description / Scenario',
    'Preconditions',
    'Test Steps',
    'Expected Result',
    'Priority',
    'Type',
    'Tags',
  ];
  const rows = cases.map((c, i) => [
    `TC-${String(i + 1).padStart(3, '0')}`,
    c.title,
    c.preconditions.join('; '),
    c.steps.map((s, j) => `${j + 1}. ${s.action}`).join('\n'),
    c.steps.map((s, j) => `${j + 1}. ${s.expected}`).join('\n'),
    c.priority,
    c.type,
    c.tags.join(', '),
  ]);

  const ws = XLSX.utils.aoa_to_sheet([headers, ...rows]);

  // Column widths (wch ≈ characters)
  ws['!cols'] = [
    { wch: 10 }, // TC No.
    { wch: 40 }, // Description
    { wch: 25 }, // Preconditions
    { wch: 50 }, // Steps
    { wch: 50 }, // Expected
    { wch: 10 }, // Priority
    { wch: 14 }, // Type
    { wch: 20 }, // Tags
  ];

  // Enable text wrapping on the body rows so multi-line steps render properly
  const range = XLSX.utils.decode_range(ws['!ref'] ?? 'A1');
  for (let R = 0; R <= range.e.r; R++) {
    for (let C = 0; C <= range.e.c; C++) {
      const cellRef = XLSX.utils.encode_cell({ r: R, c: C });
      const cell = ws[cellRef];
      if (!cell) continue;
      cell.s = cell.s ?? {};
      // SheetJS community build ignores styles, but harmless to set
      cell.s.alignment = { vertical: 'top', wrapText: true };
    }
  }

  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'Test Cases');

  // Write as binary .xlsx
  XLSX.writeFile(wb, `${ticketKey}-test-cases.xlsx`, { bookType: 'xlsx', compression: true });
}

function EmptyState({
  icon,
  title,
  description,
  cta,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  cta: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-[hsl(var(--border))] bg-[hsl(var(--muted))]/20 px-6 py-8 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10 text-brand">
        {icon}
      </div>
      <div>
        <div className="text-sm font-semibold">{title}</div>
        <p className="mx-auto mt-1 max-w-md text-xs text-[hsl(var(--muted-foreground))]">
          {description}
        </p>
      </div>
      {cta}
    </div>
  );
}

function StageBadge({
  tone,
  children,
}: {
  tone: 'success' | 'brand' | 'muted';
  children: React.ReactNode;
}) {
  const cls =
    tone === 'success'
      ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
      : tone === 'brand'
        ? 'border-brand/40 bg-brand/10 text-brand-700'
        : 'border-[hsl(var(--border))] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]';
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${cls}`}
    >
      {children}
    </span>
  );
}

function AutomationBadge({
  candidate,
  reason,
}: {
  candidate?: 'yes' | 'partial' | 'no';
  reason?: string;
}) {
  const value = candidate ?? 'yes';
  const cls =
    value === 'yes'
      ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
      : value === 'partial'
        ? 'border-amber-300 bg-amber-50 text-amber-800'
        : 'border-[hsl(var(--border))] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]';
  const label =
    value === 'yes' ? '✓ Playwright' : value === 'partial' ? '~ Partial' : '✗ Manual';
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium ${cls}`}
      title={reason || ''}
    >
      {label}
    </span>
  );
}

function PlanList({ title, items }: { title: string; items: string[] }) {
  if (!items || items.length === 0) return null;
  return (
    <section>
      <h3 className="font-semibold">{title}</h3>
      <ul className="ml-5 list-disc space-y-0.5 text-[hsl(var(--muted-foreground))]">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </section>
  );
}
