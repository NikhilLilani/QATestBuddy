'use client';

import { useCallback, useMemo, useState } from 'react';
import { toast } from 'sonner';
import JSZip from 'jszip';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert } from '@/components/ui/alert';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { getAccessToken } from '@/lib/supabase/access-token';
import { parseSSE } from '@/lib/stream';
import { LiveRunPanel } from './live-run-panel';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

type Framework = {
  id: string;
  name: string;
  language: string;
  source_kind: 'git' | 'local' | 'zip' | 'new';
  git_url: string | null;
  git_branch: string | null;
  local_path: string | null;
  conventions: string;
};

type Verdict = {
  related: boolean;
  confidence: number;
  reasoning: string;
  evidence: string[];
  suggestion: string;
};

type CheckOut = { verdict: Verdict; source: string };

type GeneratedFile = {
  path: string;
  operation: 'create' | 'update' | 'merge';
  content: string;
  summary?: string;
  reason?: string;
};

type CodegenBundle = {
  files: GeneratedFile[];
  install_commands: string[];
  run_commands: string[];
  notes: string[];
};

type ValidatorIssue = {
  file?: string;
  line?: number | null;
  severity: 'error' | 'warning' | 'info';
  message: string;
  fix_hint?: string;
};

type ValidatorReport = {
  issues: ValidatorIssue[];
  summary?: string;
};

type Citation =
  | { kind: 'jira'; key: string; field: string }
  | { kind: 'rag_chunk'; chunk_id: string }
  | { kind: 'repo_file'; repo: string; path: string; line_start: number; line_end: number };

type FixerInfo = {
  iterations: number;
  errors_auto_fixed: number;
  notes: string[];
  unresolved: string[];
};

type CodegenResult = {
  run_id: string;
  bundle: CodegenBundle;
  validator: ValidatorReport;
  fixer?: FixerInfo;
  citations: Citation[];
  tokens: { in: number; out: number };
  stats: {
    files: number;
    lines: number;
    validator_errors: number;
    validator_warnings: number;
    fix_iterations?: number;
    errors_auto_fixed?: number;
  };
};

type StepEvent = { name: string; status: 'start' | 'done'; [k: string]: unknown };

type DataNeed = {
  key: string;
  label: string;
  type: 'text' | 'secret' | 'email' | 'url' | 'number' | 'select' | 'textarea';
  placeholder?: string;
  default?: string;
  required: boolean;
  reason?: string;
  options?: Array<{ value: string; label?: string }>;
  env_var?: string;
  dynamic?: boolean;
  faker_method?: string;
  codegen_hint?: string;
};

export function CodegenWorkflow({
  ticketKey,
  frameworks,
  activeJobId = null,
}: {
  ticketKey: string;
  frameworks: Framework[];
  activeJobId?: string | null;
}) {
  const [frameworkId, setFrameworkId] = useState<string>(frameworks[0]?.id ?? '');
  const [baseUrl, setBaseUrl] = useState('https://staging.example.com');
  const [localPath, setLocalPath] = useState('');
  // Extra guidance is OPTIONAL — the ticket already carries the main flow.
  // This is just for nuggets the LLM can't infer (OTP arrives via network
  // response, only Chromium is supported, this app uses Firebase auth, …).
  const [flowGuidance, setFlowGuidance] = useState('');

  const [checking, setChecking] = useState(false);
  const [check, setCheck] = useState<CheckOut | null>(null);
  const [overrideApproved, setOverrideApproved] = useState(false);

  // Sample-data step: agent inspects ticket → returns inputs → user fills them
  // → values get passed into codegen so the generated tests use real values.
  const [dataNeeds, setDataNeeds] = useState<DataNeed[] | null>(null);
  const [dataValues, setDataValues] = useState<Record<string, string>>({});
  const [detectingData, setDetectingData] = useState(false);

  const [steps, setSteps] = useState<StepEvent[]>([]);
  const [codegen, setCodegen] = useState<CodegenResult | null>(null);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Stale job from URL (?job=...) — user can dismiss the panel; we also
  // auto-dismiss as soon as a new codegen starts so the old run doesn't
  // hog the top of the page.
  const [stalePanelJobId, setStalePanelJobId] = useState<string | null>(activeJobId);

  const selected = frameworks.find((f) => f.id === frameworkId) ?? null;

  const runCheck = useCallback(async () => {
    if (!frameworkId) return;
    setChecking(true);
    setError(null);
    setCheck(null);
    setOverrideApproved(false);
    try {
      const token = await getAccessToken();
      if (!token) throw new Error('Not authenticated.');
      const wsResp = await fetch(`${API_URL}/api/v1/workspaces`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const wsList = (await wsResp.json()) as { id: string }[];
      const workspaceId = wsList[0]?.id;
      if (!workspaceId) throw new Error('No workspace.');

      const resp = await fetch(`${API_URL}/api/v1/frameworks/${frameworkId}/check`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'X-Workspace-Id': workspaceId,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ticket_key: ticketKey,
          flow_guidance: flowGuidance.trim(),
        }),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => '');
        throw new Error(`API ${resp.status}: ${text || 'check failed'}`);
      }
      const data = (await resp.json()) as CheckOut;
      setCheck(data);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      toast.error(msg);
    } finally {
      setChecking(false);
    }
  }, [frameworkId, ticketKey, flowGuidance]);

  const detectDataNeeds = useCallback(async () => {
    setDetectingData(true);
    setError(null);
    try {
      const token = await getAccessToken();
      if (!token) throw new Error('Not authenticated.');
      const wsResp = await fetch(`${API_URL}/api/v1/workspaces`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const wsList = (await wsResp.json()) as { id: string }[];
      const workspaceId = wsList[0]?.id;
      if (!workspaceId) throw new Error('No workspace.');

      const resp = await fetch(`${API_URL}/api/v1/data-needs`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'X-Workspace-Id': workspaceId,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ticket_key: ticketKey,
          framework_id: frameworkId || undefined,
          flow_guidance: flowGuidance.trim(),
        }),
      });
      if (!resp.ok) {
        const t = await resp.text().catch(() => '');
        throw new Error(`HTTP ${resp.status}: ${t.slice(0, 200)}`);
      }
      const data = (await resp.json()) as { inputs: DataNeed[]; notes: string[] };
      setDataNeeds(data.inputs);
      // Seed values with any defaults the agent suggested.
      const seeded: Record<string, string> = {};
      for (const inp of data.inputs) {
        if (inp.default) seeded[inp.key] = inp.default;
      }
      setDataValues((prev) => ({ ...seeded, ...prev }));
      if (data.inputs.length === 0) {
        toast.info('No extra test data needed — you can generate now.');
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      toast.error(msg);
    } finally {
      setDetectingData(false);
    }
  }, [ticketKey, frameworkId, flowGuidance]);

  const dataReady = useMemo(() => {
    if (!dataNeeds) return false;
    for (const n of dataNeeds) {
      // Dynamic fields are auto-generated at runtime — user never provides them.
      if (n.dynamic) continue;
      if (n.required && !(dataValues[n.key] ?? '').trim()) return false;
    }
    return true;
  }, [dataNeeds, dataValues]);

  const findOrCreatePlanThenCodegen = useCallback(async () => {
    setGenerating(true);
    setError(null);
    setCodegen(null);
    setSteps([]);
    // The user is starting a new generation — hide the stale ?job=... panel
    // so the new BundleViewer can take the spotlight.
    setStalePanelJobId(null);
    try {
      const token = await getAccessToken();
      if (!token) throw new Error('Not authenticated.');
      const wsResp = await fetch(`${API_URL}/api/v1/workspaces`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const wsList = (await wsResp.json()) as { id: string }[];
      const workspaceId = wsList[0]?.id;
      if (!workspaceId) throw new Error('No workspace.');

      // Get the latest plan for this ticket. If none, generate cases (which
      // auto-creates a stub plan), then codegen against it.
      const ticketResp = await fetch(`${API_URL}/api/v1/jira/issues/${ticketKey}`, {
        headers: {
          Authorization: `Bearer ${token}`,
          'X-Workspace-Id': workspaceId,
        },
      });
      if (!ticketResp.ok) throw new Error(`Could not fetch ticket ${ticketKey}.`);

      // Try cases-direct first to ensure we have a plan + cases.
      const casesResp = await fetch(`${API_URL}/api/v1/cases`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'X-Workspace-Id': workspaceId,
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify({ ticket_key: ticketKey }),
      });
      if (!casesResp.ok || !casesResp.body)
        throw new Error(`Cases generation failed (HTTP ${casesResp.status}).`);

      let planId = '';
      for await (const ev of parseSSE(casesResp.body)) {
        if (ev.event === 'step') {
          const step = JSON.parse(ev.data) as StepEvent;
          setSteps((prev) => [...prev, step]);
        } else if (ev.event === 'result') {
          const r = JSON.parse(ev.data) as { plan_id?: string };
          if (r.plan_id) planId = r.plan_id;
        } else if (ev.event === 'error') {
          const e = JSON.parse(ev.data) as { message: string };
          throw new Error(e.message);
        } else if (ev.event === 'done') {
          break;
        }
      }

      if (!planId) throw new Error('No plan returned from cases generation.');

      // Now codegen
      const codegenResp = await fetch(`${API_URL}/api/v1/plans/${planId}/codegen`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'X-Workspace-Id': workspaceId,
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify({
          base_url: baseUrl.trim(),
          framework_id: frameworkId,
          framework_hints: flowGuidance.trim(),
          local_path: localPath.trim(),
          test_file_name: `${ticketKey.toLowerCase()}.spec.ts`,
          data_values: dataValues,
          data_needs: dataNeeds ?? [],
        }),
      });
      if (!codegenResp.ok || !codegenResp.body)
        throw new Error(`Codegen failed (HTTP ${codegenResp.status}).`);

      for await (const ev of parseSSE(codegenResp.body)) {
        if (ev.event === 'step') {
          setSteps((prev) => [...prev, JSON.parse(ev.data) as StepEvent]);
        } else if (ev.event === 'result') {
          setCodegen(JSON.parse(ev.data) as CodegenResult);
        } else if (ev.event === 'error') {
          const e = JSON.parse(ev.data) as { message: string };
          throw new Error(e.message);
        } else if (ev.event === 'done') {
          break;
        }
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      toast.error(msg);
    } finally {
      setGenerating(false);
    }
  }, [baseUrl, flowGuidance, frameworkId, localPath, ticketKey, dataValues, dataNeeds]);

  const canGenerate =
    !!frameworkId &&
    !!baseUrl.trim() &&
    (check?.verdict.related || overrideApproved) &&
    // If we detected data needs, require all required fields to be filled.
    // If the user hasn't run detection at all, allow generate (it's optional).
    (dataNeeds === null || dataReady);

  return (
    <div className="space-y-6">
      {/* Active job from a /runs row — surface the live panel at the top so
          the user lands here and immediately sees Playwright executing.
          Dismissable + auto-hides when the user starts a fresh codegen. */}
      {stalePanelJobId && (
        <LiveRunPanel
          initialJobId={stalePanelJobId}
          onDismiss={() => setStalePanelJobId(null)}
        />
      )}

      {/* 1. Framework + target */}
      <Card>
        <CardHeader>
          <CardTitle>1. Pick a framework</CardTitle>
          <CardDescription>
            Multiple frameworks per workspace; one used per generation. Manage them at{' '}
            <a href="/settings/frameworks" className="text-brand hover:underline">
              Settings → Frameworks
            </a>
            .
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="framework">Framework</Label>
              <select
                id="framework"
                value={frameworkId}
                onChange={(e) => {
                  setFrameworkId(e.target.value);
                  setCheck(null);
                  setOverrideApproved(false);
                }}
                className="h-10 w-full rounded-md border border-[hsl(var(--border))] bg-transparent px-3 text-sm"
              >
                {frameworks.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name} ({f.source_kind})
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="base_url">Base URL</Label>
              <Input
                id="base_url"
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://staging.example.com"
              />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="local_path">Local path (where to save the file)</Label>
              <Input
                id="local_path"
                value={localPath}
                onChange={(e) => setLocalPath(e.target.value)}
                placeholder="C:\Users\you\projects\my-app\tests"
              />
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                Used to compute relative import paths in the generated file. Auto-write to this
                path needs <strong>qa-bridge</strong> (coming) — for now, save with Download below.
              </p>
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="flow_guidance">
                Extra guidance{' '}
                <span className="text-[hsl(var(--muted-foreground))] font-normal">(optional)</span>
              </Label>
              <textarea
                id="flow_guidance"
                value={flowGuidance}
                onChange={(e) => setFlowGuidance(e.target.value)}
                rows={4}
                className="w-full rounded-md border border-[hsl(var(--border))] bg-transparent px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
                placeholder={`The ticket is already the main source. Use this for nuggets the LLM can't infer from the Jira description.

Examples:
  • "OTP arrives via the /api/otp/request network response — read it from there instead of polling the UI."
  • "Use the loggedInPage fixture from src/fixtures — don't create a fresh page."
  • "This app uses data-testid attributes — prefer getByTestId over text selectors."`}
              />
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[hsl(var(--muted-foreground))]">
                <p>
                  {flowGuidance.trim().length === 0
                    ? 'Leave empty if the ticket already says everything.'
                    : `${flowGuidance.trim().length} characters of guidance.`}
                </p>
                <p>Added to ticket context for match check AND codegen</p>
              </div>
            </div>
          </div>

          {selected && (
            <div className="rounded-md bg-[hsl(var(--muted))]/30 p-3 text-xs">
              <div className="font-medium">{selected.name}</div>
              <div className="mt-0.5 text-[hsl(var(--muted-foreground))]">
                Source: {selected.source_kind}
                {selected.source_kind === 'git' && ` · ${selected.git_url}`}
                {selected.source_kind === 'local' && ` · ${selected.local_path}`}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 2. Relatedness check */}
      <Card>
        <CardHeader>
          <CardTitle>2. Verify the framework matches this ticket</CardTitle>
          <CardDescription>
            The match percentage is computed from the Jira ticket, your{' '}
            <strong>extra guidance</strong> (step 1, optional), and the framework&apos;s source code.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button
            onClick={runCheck}
            disabled={checking || !frameworkId}
            variant="secondary"
          >
            {checking ? 'Checking…' : check ? 'Re-check' : 'Check framework match'}
          </Button>

          {check && check.verdict.related && (
            <Alert variant="success">
              <div className="space-y-1">
                <div className="font-medium">
                  ✓ Looks like a match — confidence{' '}
                  {(check.verdict.confidence * 100).toFixed(0)}%
                </div>
                <p className="text-xs">{check.verdict.reasoning}</p>
                <p className="text-xs text-[hsl(var(--muted-foreground))]">Source: {check.source}</p>
              </div>
            </Alert>
          )}

          {check && !check.verdict.related && !overrideApproved && (
            <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
              <div className="font-semibold">Are you sure? This may not be the right framework.</div>
              <p className="mt-1 text-xs">
                Confidence: {(check.verdict.confidence * 100).toFixed(0)}% ·{' '}
                <span className="font-mono">{check.source}</span>
              </p>
              <p className="mt-2 text-xs">{check.verdict.reasoning}</p>
              {check.verdict.evidence.length > 0 && (
                <div className="mt-2 text-xs">
                  <div className="font-semibold">How we detected this:</div>
                  <ul className="ml-5 list-disc space-y-0.5">
                    {check.verdict.evidence.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </div>
              )}
              {check.verdict.suggestion && (
                <p className="mt-2 text-xs">
                  <span className="font-semibold">Suggested fix:</span> {check.verdict.suggestion}
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" variant="secondary" onClick={() => setCheck(null)}>
                  Pick a different framework
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => {
                    if (confirm('Generate Playwright tests anyway despite the mismatch?')) {
                      setOverrideApproved(true);
                    }
                  }}
                >
                  Continue anyway
                </Button>
              </div>
            </div>
          )}

          {overrideApproved && (
            <Alert variant="info">
              Match check overridden — codegen will run with the selected framework.
            </Alert>
          )}
        </CardContent>
      </Card>

      {/* 3. Sample test data */}
      <Card>
        <CardHeader>
          <CardTitle>3. Sample test data</CardTitle>
          <CardDescription>
            We scan the ticket and propose the concrete inputs your tests will need
            (mobile number, OTP source, credentials, etc.) so the generated code ships with
            real values — not placeholders.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              onClick={detectDataNeeds}
              loading={detectingData}
              loadingText="Analyzing…"
              variant="secondary"
              disabled={!frameworkId}
            >
              {dataNeeds ? 'Re-analyze data needs' : 'Detect sample data needed'}
            </Button>
            {dataNeeds && dataNeeds.length === 0 && (
              <span className="text-xs text-emerald-700">
                ✓ No extra test data required for this ticket.
              </span>
            )}
            {dataNeeds && dataNeeds.length > 0 && (
              <span className="text-xs text-[hsl(var(--muted-foreground))]">
                {dataNeeds.length} input{dataNeeds.length !== 1 ? 's' : ''}{' '}
                {dataReady
                  ? '— all required fields filled.'
                  : '— fill required fields below to enable Generate.'}
              </span>
            )}
          </div>

          {dataNeeds && dataNeeds.length > 0 && (
            <div className="grid gap-3 sm:grid-cols-2">
              {dataNeeds.map((n) => {
                const value = dataValues[n.key] ?? '';
                const setVal = (v: string) =>
                  setDataValues((prev) => ({ ...prev, [n.key]: v }));
                const missing = !n.dynamic && n.required && !value.trim();

                // Dynamic fields render as a read-only "auto-generated" row.
                if (n.dynamic) {
                  return (
                    <div key={n.key} className="space-y-1.5 sm:col-span-2">
                      <Label>
                        {n.label}
                        <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-800">
                          ✨ auto-generated
                        </span>
                      </Label>
                      <div className="rounded-md border border-dashed border-violet-300 bg-violet-50/40 px-3 py-2 text-xs text-violet-900">
                        Generated at runtime via{' '}
                        <code className="font-mono">
                          faker.{n.faker_method || 'lorem.word'}()
                        </code>{' '}
                        — each test run gets a unique value, so no manual input is needed.
                      </div>
                      {n.reason && (
                        <p className="text-[11px] text-[hsl(var(--muted-foreground))]">
                          {n.reason}
                        </p>
                      )}
                    </div>
                  );
                }

                return (
                  <div key={n.key} className="space-y-1.5 sm:col-span-2">
                    <Label htmlFor={`dn-${n.key}`}>
                      {n.label}
                      {n.required && <span className="ml-0.5 text-red-600">*</span>}
                      {n.env_var && (
                        <span className="ml-2 rounded bg-[hsl(var(--muted))] px-1.5 py-0.5 font-mono text-[10px] text-[hsl(var(--muted-foreground))]">
                          → process.env.{n.env_var}
                        </span>
                      )}
                    </Label>

                    {n.type === 'select' ? (
                      <select
                        id={`dn-${n.key}`}
                        value={value}
                        onChange={(e) => setVal(e.target.value)}
                        className={`h-10 w-full rounded-md border bg-transparent px-3 text-sm ${
                          missing ? 'border-red-400' : 'border-[hsl(var(--border))]'
                        }`}
                      >
                        <option value="">— pick one —</option>
                        {(n.options ?? []).map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label || o.value}
                          </option>
                        ))}
                      </select>
                    ) : n.type === 'textarea' ? (
                      <textarea
                        id={`dn-${n.key}`}
                        value={value}
                        onChange={(e) => setVal(e.target.value)}
                        rows={3}
                        placeholder={n.placeholder}
                        className={`w-full rounded-md border bg-transparent px-3 py-2 text-sm ${
                          missing ? 'border-red-400' : 'border-[hsl(var(--border))]'
                        }`}
                      />
                    ) : (
                      <Input
                        id={`dn-${n.key}`}
                        type={
                          n.type === 'secret'
                            ? 'password'
                            : n.type === 'email'
                              ? 'email'
                              : n.type === 'url'
                                ? 'url'
                                : n.type === 'number'
                                  ? 'number'
                                  : 'text'
                        }
                        value={value}
                        onChange={(e) => setVal(e.target.value)}
                        placeholder={n.placeholder}
                        className={missing ? 'border-red-400' : ''}
                      />
                    )}

                    {n.reason && (
                      <p className="text-[11px] text-[hsl(var(--muted-foreground))]">
                        {n.reason}
                      </p>
                    )}
                    {n.codegen_hint && (
                      <p className="rounded bg-emerald-50 px-2 py-1 text-[11px] text-emerald-900">
                        <span className="font-semibold">How tests use it:</span>{' '}
                        {n.codegen_hint}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 4. Generate */}
      <Card>
        <CardHeader>
          <CardTitle>4. Generate Playwright spec</CardTitle>
          <CardDescription>
            Runs cases generation (auto-creates a stub plan if none) and then codegen using the
            selected framework as grounding — with the sample data above baked in. Only cases
            tagged <strong>Playwright</strong> or <strong>Partial</strong> are sent to codegen;
            manual cases stay as documentation in the Test Cases table.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button
            onClick={findOrCreatePlanThenCodegen}
            disabled={!canGenerate || generating}
          >
            {generating ? 'Generating…' : 'Generate Playwright tests'}
          </Button>

          {!canGenerate && !generating && (
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              {!frameworkId
                ? 'Pick a framework first.'
                : !baseUrl.trim()
                  ? 'Base URL is required.'
                  : !check?.verdict.related && !overrideApproved
                    ? 'Run the framework match check (step 2) before generating.'
                    : dataNeeds && !dataReady
                      ? 'Fill the required test data fields in step 3.'
                      : ''}
            </p>
          )}

          {steps.length > 0 && (
            <ol className="space-y-1 text-xs text-[hsl(var(--muted-foreground))]">
              {steps.map((s, i) => {
                const iter = typeof s.iteration === 'number' ? s.iteration : null;
                const isFix = s.name === 'fix';
                const isValidate = s.name === 'validate';
                const errs = typeof s.errors === 'number' ? s.errors : null;
                const fixed = typeof s.files_patched === 'number' ? s.files_patched : null;
                return (
                  <li key={i} className="flex flex-wrap items-center gap-1.5">
                    <span
                      className={
                        isFix
                          ? 'rounded bg-emerald-100 px-1 py-0.5 font-mono text-[10px] text-emerald-800'
                          : isValidate
                            ? 'rounded bg-sky-100 px-1 py-0.5 font-mono text-[10px] text-sky-800'
                            : 'font-mono'
                      }
                    >
                      {s.name}
                      {iter ? ` #${iter}` : ''}
                    </span>
                    <span>— {s.status}</span>
                    {isValidate && s.status === 'done' && errs !== null && (
                      <span className="text-[hsl(var(--muted-foreground))]">
                        ({errs} error{errs !== 1 ? 's' : ''}
                        {typeof s.warnings === 'number' ? `, ${s.warnings} warn` : ''})
                      </span>
                    )}
                    {isFix && s.status === 'done' && fixed !== null && (
                      <span className="text-emerald-700">
                        — patched {fixed} file{fixed !== 1 ? 's' : ''}
                      </span>
                    )}
                  </li>
                );
              })}
            </ol>
          )}

          {error && <Alert variant="error">{error}</Alert>}

          {codegen && (
            <BundleViewer
              codegen={codegen}
              ticketKey={ticketKey}
              localPath={localPath}
              onCodegenUpdate={setCodegen}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/* ============================== BundleViewer ============================== */

function opBadge(op: GeneratedFile['operation']): { label: string; cls: string } {
  if (op === 'create') return { label: '✚ create', cls: 'bg-emerald-100 text-emerald-800' };
  if (op === 'update') return { label: '~ update', cls: 'bg-amber-100 text-amber-800' };
  return { label: '⊕ merge', cls: 'bg-sky-100 text-sky-800' };
}

function sevBadge(sev: ValidatorIssue['severity']): { label: string; cls: string } {
  if (sev === 'error') return { label: 'ERROR', cls: 'bg-red-100 text-red-800 border-red-300' };
  if (sev === 'warning')
    return { label: 'WARN', cls: 'bg-amber-100 text-amber-800 border-amber-300' };
  return { label: 'INFO', cls: 'bg-sky-100 text-sky-800 border-sky-300' };
}

function BundleViewer({
  codegen,
  ticketKey,
  localPath,
  onCodegenUpdate,
}: {
  codegen: CodegenResult;
  ticketKey: string;
  localPath: string;
  onCodegenUpdate?: (next: CodegenResult) => void;
}) {
  const files = codegen.bundle.files;
  const [selected, setSelected] = useState<string>(files[0]?.path ?? '');
  const [fixing, setFixing] = useState(false);

  const runManualFix = useCallback(async () => {
    setFixing(true);
    try {
      const token = await getAccessToken();
      if (!token) throw new Error('Not authenticated.');
      const wsResp = await fetch(`${API_URL}/api/v1/workspaces`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const wsList = (await wsResp.json()) as { id: string }[];
      const workspaceId = wsList[0]?.id;
      if (!workspaceId) throw new Error('No workspace.');

      const resp = await fetch(`${API_URL}/api/v1/runs/${codegen.run_id}/fix-issues`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'X-Workspace-Id': workspaceId,
        },
      });
      if (!resp.ok) {
        const t = await resp.text().catch(() => '');
        throw new Error(`HTTP ${resp.status}: ${t.slice(0, 200)}`);
      }
      const data = (await resp.json()) as {
        ok: boolean;
        errors_before: number;
        errors_after: number;
        patched: number;
        notes?: string[];
        unresolved?: string[];
        files?: GeneratedFile[];
        validator?: ValidatorReport;
      };

      if (data.errors_before === 0) {
        toast.success('No errors to fix — the bundle is already clean.');
        return;
      }
      if (data.patched === 0) {
        toast.error('Fixer ran but produced no patches. Check the validator notes.');
        return;
      }

      // Merge new files back into the bundle so the viewer updates.
      if (onCodegenUpdate && data.files && data.validator) {
        onCodegenUpdate({
          ...codegen,
          bundle: { ...codegen.bundle, files: data.files },
          validator: data.validator,
          fixer: {
            iterations: (codegen.fixer?.iterations ?? 0) + 1,
            errors_auto_fixed:
              (codegen.fixer?.errors_auto_fixed ?? 0) +
              Math.max(0, data.errors_before - data.errors_after),
            notes: [...(codegen.fixer?.notes ?? []), ...(data.notes ?? [])],
            unresolved: data.unresolved ?? [],
          },
          stats: {
            ...codegen.stats,
            validator_errors: data.errors_after,
            fix_iterations: (codegen.stats.fix_iterations ?? 0) + 1,
            errors_auto_fixed:
              (codegen.stats.errors_auto_fixed ?? 0) +
              Math.max(0, data.errors_before - data.errors_after),
          },
        });
      }
      toast.success(
        `Fixed ${Math.max(0, data.errors_before - data.errors_after)} of ${data.errors_before} errors (patched ${data.patched} file${data.patched !== 1 ? 's' : ''}).`,
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setFixing(false);
    }
  }, [codegen, onCodegenUpdate]);
  const currentFile = useMemo(
    () => files.find((f) => f.path === selected) ?? files[0],
    [files, selected],
  );

  const downloadZip = useCallback(async () => {
    const zip = new JSZip();
    for (const f of codegen.bundle.files) zip.file(f.path, f.content);
    if (codegen.bundle.install_commands.length || codegen.bundle.run_commands.length) {
      const setup = [
        '# How to run this bundle',
        '',
        '## Install',
        ...codegen.bundle.install_commands.map((c) => `    ${c}`),
        '',
        '## Run',
        ...codegen.bundle.run_commands.map((c) => `    ${c}`),
        '',
        '## Notes',
        ...codegen.bundle.notes.map((n) => `- ${n}`),
      ].join('\n');
      zip.file('SETUP.md', setup);
    }
    const blob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${ticketKey.toLowerCase()}-bundle.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, [codegen, ticketKey]);

  const copyCurrent = useCallback(async () => {
    if (!currentFile) return;
    try {
      await navigator.clipboard.writeText(currentFile.content);
      toast.success(`Copied ${currentFile.path}`);
    } catch {
      toast.error('Could not copy — clipboard permission denied.');
    }
  }, [currentFile]);

  const downloadCurrent = useCallback(() => {
    if (!currentFile) return;
    const blob = new Blob([currentFile.content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = currentFile.path.split('/').pop() ?? 'file.ts';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, [currentFile]);

  const errors = codegen.validator.issues.filter((i) => i.severity === 'error');
  const warnings = codegen.validator.issues.filter((i) => i.severity === 'warning');
  const infos = codegen.validator.issues.filter((i) => i.severity === 'info');

  return (
    <div className="space-y-3">
      {/* Header bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-[hsl(var(--muted))]/30 px-3 py-2 text-xs">
        <div className="text-[hsl(var(--muted-foreground))]">
          📦 <strong>{codegen.stats.files}</strong> files · {codegen.stats.lines} lines · tokens in{' '}
          {codegen.tokens.in} / out {codegen.tokens.out}
          {errors.length > 0 && (
            <span className="ml-2 rounded bg-red-100 px-1.5 py-0.5 font-mono text-[10px] text-red-800">
              {errors.length} error{errors.length !== 1 ? 's' : ''}
            </span>
          )}
          {warnings.length > 0 && (
            <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 font-mono text-[10px] text-amber-800">
              {warnings.length} warning{warnings.length !== 1 ? 's' : ''}
            </span>
          )}
          {codegen.fixer && codegen.fixer.errors_auto_fixed > 0 && (
            <span
              className="ml-1 inline-flex items-center gap-1 rounded bg-emerald-100 px-1.5 py-0.5 font-mono text-[10px] text-emerald-800"
              title={`Fix-with-AI ran ${codegen.fixer.iterations} pass${codegen.fixer.iterations !== 1 ? 'es' : ''}, repairing ${codegen.fixer.errors_auto_fixed} validator error${codegen.fixer.errors_auto_fixed !== 1 ? 's' : ''}.`}
            >
              ✨ auto-fixed {codegen.fixer.errors_auto_fixed}
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" onClick={copyCurrent}>
            Copy this file
          </Button>
          <Button size="sm" onClick={downloadZip}>
            Download bundle .zip
          </Button>
        </div>
      </div>

      {/* Bundle notes */}
      {codegen.bundle.notes.length > 0 && (
        <Alert variant="info">
          <div className="space-y-1">
            <div className="font-medium">Before you run:</div>
            <ul className="ml-5 list-disc space-y-0.5 text-xs">
              {codegen.bundle.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          </div>
        </Alert>
      )}

      {/* Validator panel */}
      {codegen.validator.issues.length > 0 ? (
        <div
          className={`rounded-md border p-3 text-sm ${
            errors.length > 0
              ? 'border-red-300 bg-red-50'
              : warnings.length > 0
                ? 'border-amber-300 bg-amber-50'
                : 'border-sky-300 bg-sky-50'
          }`}
        >
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="font-semibold">
              Validator: {codegen.validator.summary || `${codegen.validator.issues.length} issues`}
            </div>
            {errors.length > 0 && onCodegenUpdate && (
              <Button
                size="sm"
                variant="secondary"
                loading={fixing}
                loadingText="Fixing…"
                onClick={runManualFix}
                title="Run the FixerAgent on the current bundle to auto-resolve errors"
              >
                ✨ Fix {errors.length} error{errors.length !== 1 ? 's' : ''} with AI
              </Button>
            )}
          </div>
          <ul className="mt-2 space-y-1.5 text-xs">
            {[...errors, ...warnings, ...infos].map((iss, i) => {
              const b = sevBadge(iss.severity);
              return (
                <li key={i} className="flex gap-2">
                  <span className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] ${b.cls}`}>
                    {b.label}
                  </span>
                  <div className="min-w-0">
                    {iss.file && (
                      <button
                        className="font-mono text-brand hover:underline"
                        onClick={() => iss.file && setSelected(iss.file)}
                      >
                        {iss.file}
                        {iss.line ? `:${iss.line}` : ''}
                      </button>
                    )}
                    <span className="ml-1">{iss.message}</span>
                    {iss.fix_hint && (
                      <span className="ml-1 text-[hsl(var(--muted-foreground))]">
                        — {iss.fix_hint}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <Alert variant="success">Validator: no issues found. Bundle looks clean.</Alert>
      )}

      {/* File tree + content */}
      <div className="grid gap-3 md:grid-cols-[280px_1fr]">
        <div className="rounded-md border">
          <div className="border-b bg-[hsl(var(--muted))]/40 px-3 py-2 text-xs font-semibold">
            Files
          </div>
          <ul className="max-h-[480px] divide-y overflow-auto text-xs">
            {files.map((f) => {
              const b = opBadge(f.operation);
              const isSel = f.path === selected;
              return (
                <li key={f.path}>
                  <button
                    onClick={() => setSelected(f.path)}
                    className={`flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-[hsl(var(--muted))]/30 ${
                      isSel ? 'bg-brand/10' : ''
                    }`}
                  >
                    <span
                      className={`mt-0.5 shrink-0 rounded px-1 py-0.5 font-mono text-[9px] ${b.cls}`}
                    >
                      {b.label}
                    </span>
                    <span className="min-w-0 break-all font-mono">{f.path}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="rounded-md border">
          <div className="flex items-center justify-between border-b bg-[hsl(var(--muted))]/40 px-3 py-2 text-xs">
            <span className="font-mono">{currentFile?.path ?? ''}</span>
            <div className="flex gap-1">
              <button
                onClick={copyCurrent}
                className="rounded border px-2 py-0.5 hover:bg-[hsl(var(--muted))]"
              >
                Copy
              </button>
              <button
                onClick={downloadCurrent}
                className="rounded border px-2 py-0.5 hover:bg-[hsl(var(--muted))]"
              >
                Download
              </button>
            </div>
          </div>
          {currentFile?.summary && (
            <div className="border-b bg-[hsl(var(--muted))]/20 px-3 py-1.5 text-xs text-[hsl(var(--muted-foreground))]">
              <strong>{currentFile.summary}</strong>
              {currentFile.reason && ` · ${currentFile.reason}`}
            </div>
          )}
          <pre className="max-h-[480px] overflow-auto p-3 text-xs leading-relaxed">
            <code className="font-mono">{currentFile?.content ?? ''}</code>
          </pre>
        </div>
      </div>

      {/* Live run on user's machine (Phase 4e) */}
      <LiveRunPanel runId={codegen.run_id} />

      {/* Save instructions */}
      {(codegen.bundle.install_commands.length > 0 || codegen.bundle.run_commands.length > 0) && (
        <Alert variant="info">
          <div className="space-y-1 text-xs">
            <div className="font-medium">
              How to run this bundle{localPath.trim() ? ` from ${localPath}` : ''}
            </div>
            {codegen.bundle.install_commands.length > 0 && (
              <>
                <div className="text-[hsl(var(--muted-foreground))]">Install:</div>
                {codegen.bundle.install_commands.map((c, i) => (
                  <code key={i} className="block font-mono">
                    {c}
                  </code>
                ))}
              </>
            )}
            {codegen.bundle.run_commands.length > 0 && (
              <>
                <div className="mt-1 text-[hsl(var(--muted-foreground))]">Run:</div>
                {codegen.bundle.run_commands.map((c, i) => (
                  <code key={i} className="block font-mono">
                    {c}
                  </code>
                ))}
              </>
            )}
          </div>
        </Alert>
      )}
    </div>
  );
}
