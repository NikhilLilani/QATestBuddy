'use client';

import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert } from '@/components/ui/alert';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { getAccessToken } from '@/lib/supabase/access-token';
import { parseSSE } from '@/lib/stream';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

/** Mirrors what /api/v1/frameworks returns. */
type Framework = {
  id: string;
  name: string;
  language: string;
  source_kind: 'git' | 'local' | 'zip' | 'new';
};

/** Mirrors /api/v1/dev-repos. */
type DevRepo = {
  id: string;
  owner: string | null;
  name: string | null;
  source_kind: string;
  file_count: number | null;
  last_indexed_at: string | null;
};

type StepEvent = { name: string; status: 'start' | 'done'; [k: string]: unknown };
type GeneratedCase = { id?: string; ord?: number; title: string };

export function DirectAutomationForm() {
  // -------------------- form state --------------------
  const [flowTitle, setFlowTitle] = useState('');
  const [flowText, setFlowText] = useState('');
  const [promptExtra, setPromptExtra] = useState('');
  const [baseUrl, setBaseUrl] = useState('https://staging.example.com');

  const [frameworks, setFrameworks] = useState<Framework[]>([]);
  const [frameworkId, setFrameworkId] = useState<string>('');
  const [devRepos, setDevRepos] = useState<DevRepo[]>([]);
  const [devRepoId, setDevRepoId] = useState<string>('');

  const [projectState, setProjectState] = useState<
    'auto' | 'new_project' | 'existing_no_tests' | 'existing_same_fw' | 'existing_diff_fw'
  >('auto');

  // -------------------- run state --------------------
  const [steps, setSteps] = useState<StepEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cases, setCases] = useState<GeneratedCase[]>([]);
  const [planId, setPlanId] = useState<string>('');

  // -------------------- load picker data --------------------
  useEffect(() => {
    void (async () => {
      try {
        const token = await getAccessToken();
        if (!token) return;
        const wsResp = await fetch(`${API_URL}/api/v1/workspaces`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        const wsList = (await wsResp.json()) as { id: string }[];
        const workspaceId = wsList[0]?.id;
        if (!workspaceId) return;

        const [fwResp, repoResp] = await Promise.all([
          fetch(`${API_URL}/api/v1/frameworks`, {
            headers: { Authorization: `Bearer ${token}`, 'X-Workspace-Id': workspaceId },
          }),
          fetch(`${API_URL}/api/v1/dev-repos`, {
            headers: { Authorization: `Bearer ${token}`, 'X-Workspace-Id': workspaceId },
          }),
        ]);
        if (fwResp.ok) {
          const list = (await fwResp.json()) as Framework[];
          setFrameworks(list);
          if (list[0]) setFrameworkId(list[0].id);
        }
        if (repoResp.ok) {
          const list = (await repoResp.json()) as DevRepo[];
          setDevRepos(list);
        }
      } catch {
        // Picker data is optional — the user can still proceed without it.
      }
    })();
  }, []);

  // -------------------- the run --------------------
  const runFlow = useCallback(async () => {
    if (!flowText.trim()) {
      toast.error('Describe the flow before generating.');
      return;
    }
    setRunning(true);
    setError(null);
    setSteps([]);
    setCases([]);
    setPlanId('');
    try {
      const token = await getAccessToken();
      if (!token) throw new Error('Not authenticated.');
      const wsResp = await fetch(`${API_URL}/api/v1/workspaces`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const wsList = (await wsResp.json()) as { id: string }[];
      const workspaceId = wsList[0]?.id;
      if (!workspaceId) throw new Error('No workspace.');

      // Stage 1 — cases from the supplied flow_text. The backend creates a
      // synthetic ticket row keyed _flow_<hash> so the rest of the pipeline
      // (plan, cases, codegen) sees a normal ticket.
      const casesResp = await fetch(`${API_URL}/api/v1/cases`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'X-Workspace-Id': workspaceId,
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify({
          flow_text: flowText.trim(),
          flow_title: flowTitle.trim() || 'Direct automation flow',
        }),
      });
      if (!casesResp.ok || !casesResp.body)
        throw new Error(`Cases generation failed (HTTP ${casesResp.status}).`);

      let resolvedPlanId = '';
      for await (const ev of parseSSE(casesResp.body)) {
        if (ev.event === 'step') {
          setSteps((prev) => [...prev, JSON.parse(ev.data) as StepEvent]);
        } else if (ev.event === 'result') {
          const r = JSON.parse(ev.data) as {
            plan_id?: string;
            cases?: GeneratedCase[];
          };
          if (r.plan_id) resolvedPlanId = r.plan_id;
          if (r.cases) setCases(r.cases);
        } else if (ev.event === 'error') {
          const e = JSON.parse(ev.data) as { message: string };
          throw new Error(e.message);
        } else if (ev.event === 'done') {
          break;
        }
      }
      if (!resolvedPlanId) throw new Error('Backend did not return a plan id.');
      setPlanId(resolvedPlanId);
      toast.success(`Generated ${cases.length || '?'} cases from your flow.`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      toast.error(msg);
    } finally {
      setRunning(false);
    }
  }, [flowText, flowTitle, cases.length]);

  return (
    <div className="space-y-6">
      {/* Step 1 — describe the flow */}
      <Card>
        <CardHeader>
          <CardTitle>1. Describe the flow</CardTitle>
          <CardDescription>
            Plain English. The clearer the better. This replaces the Jira ticket as the source
            of truth — the LLM will use everything you write here, plus your PRD uploads and
            indexed dev repo, to generate cases and code.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="flow-title">Flow name</Label>
            <Input
              id="flow-title"
              value={flowTitle}
              onChange={(e) => setFlowTitle(e.target.value)}
              placeholder="e.g. OTP login — happy path + error states"
              maxLength={150}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="flow-text">Flow description *</Label>
            <textarea
              id="flow-text"
              value={flowText}
              onChange={(e) => setFlowText(e.target.value)}
              rows={10}
              className="w-full rounded-md border border-[hsl(var(--border))] bg-transparent px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand"
              placeholder={`Example:

User opens the app, taps Login, enters a 10-digit mobile number, taps "Get OTP".
A 6-digit OTP arrives via SMS. User enters it and taps Verify.
On success, user lands on the Home screen.

Cases to cover: happy path, wrong OTP (shows error toast), expired OTP after 60s,
resend OTP, mobile number with non-digits (button disabled).`}
            />
            <p className="text-[11px] text-[hsl(var(--muted-foreground))]">
              {flowText.trim().length === 0
                ? 'Required. The more detail, the fewer clarify questions later.'
                : `${flowText.trim().length} characters.`}
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Step 2 — grounding sources */}
      <Card>
        <CardHeader>
          <CardTitle>2. Grounding sources (optional but recommended)</CardTitle>
          <CardDescription>
            Pick a dev repo to ground selectors and routes. Pick a framework to fit the
            generated code into your existing setup. Both reduce hallucination dramatically.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="framework">Framework</Label>
              <select
                id="framework"
                value={frameworkId}
                onChange={(e) => setFrameworkId(e.target.value)}
                className="h-10 w-full rounded-md border border-[hsl(var(--border))] bg-transparent px-3 text-sm"
              >
                <option value="">— pick one (or skip for greenfield) —</option>
                {frameworks.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name} ({f.source_kind})
                  </option>
                ))}
              </select>
              {frameworks.length === 0 && (
                <p className="text-[11px] text-[hsl(var(--muted-foreground))]">
                  No frameworks yet — add one at{' '}
                  <a href="/settings/frameworks" className="text-brand hover:underline">
                    Settings → Frameworks
                  </a>
                  , or leave empty to generate a full new project.
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="devrepo">Dev repo (for selectors + routes)</Label>
              <select
                id="devrepo"
                value={devRepoId}
                onChange={(e) => setDevRepoId(e.target.value)}
                className="h-10 w-full rounded-md border border-[hsl(var(--border))] bg-transparent px-3 text-sm"
              >
                <option value="">— none (skip locator grounding) —</option>
                {devRepos.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.owner ? `${r.owner}/${r.name}` : r.name ?? r.id.slice(0, 8)} ·{' '}
                    {r.file_count ?? 0} files
                  </option>
                ))}
              </select>
              <p className="text-[11px] text-[hsl(var(--muted-foreground))]">
                Index a repo at <code className="font-mono">POST /api/v1/dev-repos/git</code> (UI
                coming in M5b follow-up).
              </p>
            </div>

            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="base-url">Base URL</Label>
              <Input
                id="base-url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://staging.example.com"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Step 3 — project state */}
      <Card>
        <CardHeader>
          <CardTitle>3. What does your project look like?</CardTitle>
          <CardDescription>
            Tells codegen what to emit — a full skeleton, or just spec files into an existing
            setup. Auto picks the best option from your framework + dev repo.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-2 sm:grid-cols-2">
            {[
              {
                v: 'auto',
                label: '🪄 Auto-detect',
                hint: 'Let the backend decide from your framework + dev repo.',
              },
              {
                v: 'new_project',
                label: '🆕 New project',
                hint: 'Brand new — emit a full Playwright skeleton with config, fixtures, CI.',
              },
              {
                v: 'existing_no_tests',
                label: '📁 Project, no tests yet',
                hint: 'Project exists but no test framework. Add tests, merge configs gently.',
              },
              {
                v: 'existing_same_fw',
                label: '🧩 Existing Playwright tests',
                hint: 'Extend existing Playwright structure. No package.json/config rewrites.',
              },
              {
                v: 'existing_diff_fw',
                label: '🔀 Existing tests, different framework',
                hint: 'Translate to Playwright in a parallel folder (tests-playwright/).',
              },
            ].map((opt) => {
              const checked = projectState === opt.v;
              return (
                <label
                  key={opt.v}
                  className={`cursor-pointer rounded-md border p-3 text-sm transition ${
                    checked
                      ? 'border-brand bg-brand/5'
                      : 'border-[hsl(var(--border))] hover:bg-[hsl(var(--muted))]/30'
                  }`}
                >
                  <input
                    type="radio"
                    name="project-state"
                    value={opt.v}
                    checked={checked}
                    onChange={() => setProjectState(opt.v as typeof projectState)}
                    className="sr-only"
                  />
                  <div className="font-medium">{opt.label}</div>
                  <div className="mt-0.5 text-[11px] text-[hsl(var(--muted-foreground))]">
                    {opt.hint}
                  </div>
                </label>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Step 4 — extra prompt */}
      <Card>
        <CardHeader>
          <CardTitle>4. Extra prompt for the LLM (optional)</CardTitle>
          <CardDescription>
            Nuggets that aren't in the flow description but matter for codegen — fixtures to
            reuse, network endpoints to read, auth quirks.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <textarea
            value={promptExtra}
            onChange={(e) => setPromptExtra(e.target.value)}
            rows={4}
            className="w-full rounded-md border border-[hsl(var(--border))] bg-transparent px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
            placeholder={`Examples:
  • "OTP arrives via the /api/otp/request network response — read it from there."
  • "Use the loggedInPage fixture from src/fixtures."
  • "This app uses data-testid attributes — prefer getByTestId."`}
          />
        </CardContent>
      </Card>

      {/* Step 5 — go */}
      <Card>
        <CardHeader>
          <CardTitle>5. Generate</CardTitle>
          <CardDescription>
            Generates cases from your flow, then routes you to the codegen workflow with all of
            the above pre-filled. The Playwright bundle (spec + framework files) appears there
            with the clarify loop if any selector/route can't be grounded.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button
            onClick={runFlow}
            disabled={!flowText.trim() || running}
            loading={running}
            loadingText="Generating cases…"
          >
            Generate cases from this flow
          </Button>

          {steps.length > 0 && (
            <ol className="space-y-1 text-xs text-[hsl(var(--muted-foreground))]">
              {steps.map((s, i) => (
                <li key={i} className="flex gap-2">
                  <span className="font-mono">{s.name}</span>
                  <span>— {s.status}</span>
                </li>
              ))}
            </ol>
          )}

          {error && <Alert variant="error">{error}</Alert>}

          {planId && cases.length > 0 && (
            <Alert variant="success">
              <div className="space-y-2">
                <div className="font-medium">
                  ✓ Generated {cases.length} test case{cases.length !== 1 ? 's' : ''}.
                </div>
                <ul className="ml-5 list-disc space-y-0.5 text-xs">
                  {cases.slice(0, 8).map((c, i) => (
                    <li key={c.id ?? i}>{c.title}</li>
                  ))}
                  {cases.length > 8 && <li>… and {cases.length - 8} more.</li>}
                </ul>
                <div className="pt-2">
                  <a
                    href={`/plans/${planId}`}
                    className="inline-flex items-center gap-1 rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white hover:bg-brand/90"
                  >
                    Open plan → run codegen →
                  </a>
                </div>
                <p className="text-[11px] text-[hsl(var(--muted-foreground))]">
                  On the plan page you'll pick which cases become code, set test data, and
                  generate the Playwright bundle. Your framework / dev repo / project state /
                  extra prompt selections above are saved as plan metadata for the codegen step.
                </p>
              </div>
            </Alert>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
