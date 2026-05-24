import { Command } from 'commander';
import chalk from 'chalk';
import { promises as fs } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { readConfig } from '../config.js';

/**
 * qa-bridge listen
 *
 * Long-polls the QAtestbuddy backend for queued execution jobs, runs them
 * with Playwright, and streams per-test results back. This is the runtime
 * for Phase 4e — it turns "we generate code" into "we run your tests".
 *
 * Flow per job:
 *   1. POST /bridge/poll        → claim a job (long-poll up to 25s)
 *   2. Unpack `payload.files` into a fresh temp dir
 *   3. npm install               (best-effort)
 *   4. POST /bridge/jobs/:id/status  { status: "running" }
 *   5. npx playwright test --reporter=json   (stream stdout)
 *   6. Parse JSON results → POST /bridge/jobs/:id/results in batches
 *   7. POST /bridge/jobs/:id/status  { status: "done" | "failed" }
 *   8. Loop
 */
type Json = unknown;

type PollResponse = {
  job_id: string | null;
  run_id: string | null;
  payload: {
    run_id: string;
    ticket_key?: string;
    ticket_title?: string;
    base_url?: string;
    files: Array<{ path: string; content: string }>;
  } | null;
};

type PWTestResult = {
  test_id: string;
  file: string;
  title: string;
  project?: string | null;
  status: 'passed' | 'failed' | 'skipped' | 'timedOut' | 'interrupted' | 'running';
  duration_ms?: number;
  retry?: number;
  error_message?: string | null;
  error_stack?: string | null;
  attachments?: Array<{ name: string; path?: string; contentType?: string }>;
};

export const listenCommand = new Command('listen')
  .description('Long-poll for jobs and run Playwright tests locally')
  .option('--once', 'Process one job then exit (useful for testing)')
  .option('--poll-interval <seconds>', 'Idle delay between polls', '2')
  .action(async (opts: { once?: boolean; pollInterval?: string }) => {
    const cfg = await readConfig();
    if (!cfg.token) {
      console.error(chalk.red('Not logged in. Run: qa-bridge login --token <t>'));
      process.exit(1);
    }
    const idleDelaySec = Math.max(1, parseInt(opts.pollInterval ?? '2', 10) || 2);

    // Sanity check: whoami also touches last_seen_at on the backend.
    try {
      const me = await api<{ workspace_name: string }>(cfg.apiUrl, cfg.token, 'GET', '/api/v1/bridge/whoami');
      console.log(chalk.green('✓ Connected to workspace:'), chalk.bold(me.workspace_name));
    } catch (e) {
      console.error(chalk.red('Authentication failed:'), errMsg(e));
      process.exit(1);
    }

    console.log(chalk.dim(`Listening on ${cfg.apiUrl}. Ctrl+C to stop.`));
    let consecutiveErrors = 0;

    while (true) {
      try {
        const job = await api<PollResponse>(cfg.apiUrl, cfg.token, 'POST', '/api/v1/bridge/poll');
        consecutiveErrors = 0;

        if (!job.job_id || !job.payload) {
          // No job ready — backend already long-polled, just loop.
          continue;
        }

        await runJob(cfg.apiUrl, cfg.token, job.job_id, job.payload);
        if (opts.once) return;
      } catch (e) {
        consecutiveErrors += 1;
        const backoff = Math.min(30, idleDelaySec * 2 ** Math.min(consecutiveErrors, 5));
        console.error(chalk.yellow(`Poll failed (${errMsg(e)}). Retrying in ${backoff}s.`));
        await sleep(backoff * 1000);
      }
    }
  });

async function runJob(
  apiUrl: string,
  token: string,
  jobId: string,
  payload: NonNullable<PollResponse['payload']>,
): Promise<void> {
  const label = chalk.cyan(`[${payload.ticket_key ?? 'run'}]`);
  console.log(`${label} Claimed job ${jobId.slice(0, 8)}`);

  // 1) Unpack to a fresh temp dir
  const workDir = join(tmpdir(), `qatb-job-${randomBytes(4).toString('hex')}`);
  await fs.mkdir(workDir, { recursive: true });
  console.log(`${label} Unpacking ${payload.files.length} files → ${workDir}`);
  for (const f of payload.files) {
    const dest = join(workDir, f.path);
    await fs.mkdir(dirname(dest), { recursive: true });
    await fs.writeFile(dest, f.content, 'utf8');
  }

  // 1b) Scaffold missing essentials so thin bundles (just a spec file) still
  // run. Generated codegen bundles SHOULD include these, but older runs and
  // simple flows often don't.
  await ensureScaffold(workDir, payload.files, label);

  // 2) npm install
  try {
    console.log(`${label} Running npm install (this may take a minute)…`);
    await runStreamed('npm', ['install', '--no-audit', '--no-fund', '--prefer-offline'], workDir);
  } catch (e) {
    console.warn(chalk.yellow(`${label} npm install failed: ${errMsg(e)} — continuing anyway`));
  }

  // 2b) Ensure the Chromium binary is on disk. The first time the user runs
  // a bundle their `~/AppData/Local/ms-playwright/` is empty and Playwright
  // launches will hard-fail with "Executable doesn't exist…". This is fast
  // when already installed (Playwright checks the cache).
  try {
    console.log(`${label} Ensuring Playwright browsers are installed (chromium)…`);
    await runStreamed('npx', ['playwright', 'install', 'chromium'], workDir);
  } catch (e) {
    console.warn(chalk.yellow(`${label} playwright install failed: ${errMsg(e)} — continuing anyway`));
  }

  // 3) Mark running
  await api(apiUrl, token, 'POST', `/api/v1/bridge/jobs/${jobId}/status`, {
    status: 'running',
  });

  // 4) Execute playwright with the JSON reporter to a file we can parse.
  const reportPath = join(workDir, 'qatb-report.json');
  let runFailed = false;
  let errorMessage: string | null = null;
  try {
    console.log(`${label} npx playwright test --reporter=json`);
    await runStreamed(
      'npx',
      ['playwright', 'test', `--reporter=json`, `--output=${join(workDir, 'test-results')}`],
      workDir,
      { PLAYWRIGHT_JSON_OUTPUT_NAME: reportPath },
    );
  } catch (e) {
    // Playwright returns non-zero on any test failure; that's not a job failure.
    // We treat absence-of-report as the real failure signal.
    const exists = await fileExists(reportPath);
    if (!exists) {
      runFailed = true;
      errorMessage = `Playwright did not produce a report: ${errMsg(e)}`;
    }
  }

  // 5) Parse + post results
  if (await fileExists(reportPath)) {
    try {
      const raw = JSON.parse(await fs.readFile(reportPath, 'utf8')) as Json;
      const results = flattenPlaywrightReport(raw);
      console.log(`${label} Parsed ${results.length} test results`);
      // Post in chunks of 25 so a giant suite doesn't make one giant request.
      for (let i = 0; i < results.length; i += 25) {
        const chunk = results.slice(i, i + 25);
        await api(apiUrl, token, 'POST', `/api/v1/bridge/jobs/${jobId}/results`, {
          results: chunk,
        });
      }
      const passed = results.filter((r) => r.status === 'passed').length;
      const failed = results.filter((r) => r.status === 'failed' || r.status === 'timedOut').length;
      const skipped = results.filter((r) => r.status === 'skipped').length;
      await api(apiUrl, token, 'POST', `/api/v1/bridge/jobs/${jobId}/status`, {
        status: runFailed ? 'failed' : 'done',
        summary: { total: results.length, passed, failed, skipped },
        error_message: errorMessage,
      });
      console.log(
        `${label} ${chalk.green(`${passed} passed`)}, ${chalk.red(`${failed} failed`)}, ${chalk.dim(`${skipped} skipped`)}`,
      );
    } catch (e) {
      await api(apiUrl, token, 'POST', `/api/v1/bridge/jobs/${jobId}/status`, {
        status: 'failed',
        error_message: `Could not parse Playwright report: ${errMsg(e)}`,
      });
      console.error(chalk.red(`${label} Failed: could not parse report`));
    }
  } else {
    await api(apiUrl, token, 'POST', `/api/v1/bridge/jobs/${jobId}/status`, {
      status: 'failed',
      error_message: errorMessage ?? 'No Playwright report produced.',
    });
    console.error(chalk.red(`${label} Failed: no report produced`));
  }

  // 6) Best-effort cleanup. Keep dir on failure so user can inspect.
  if (!runFailed) {
    fs.rm(workDir, { recursive: true, force: true }).catch(() => undefined);
  } else {
    console.log(`${label} Left workdir for inspection: ${workDir}`);
  }
}

/**
 * Walk Playwright's JSON reporter output and flatten into our result rows.
 * The shape: { suites: [ { specs: [ { tests: [ { results: [ ... ] } ] } ], suites: [...] } ] }
 */
function flattenPlaywrightReport(raw: Json): PWTestResult[] {
  const out: PWTestResult[] = [];
  const root = raw as { suites?: PWSuite[] };
  walkSuites(root.suites ?? [], '', out);
  return out;
}

type PWSuite = {
  title: string;
  file?: string;
  suites?: PWSuite[];
  specs?: PWSpec[];
};
type PWSpec = {
  title: string;
  file?: string;
  tests?: PWTest[];
};
type PWTest = {
  projectName?: string;
  results?: PWResult[];
};
type PWResult = {
  status: 'passed' | 'failed' | 'skipped' | 'timedOut' | 'interrupted';
  duration?: number;
  retry?: number;
  error?: { message?: string; stack?: string };
  attachments?: Array<{ name: string; path?: string; contentType?: string }>;
};

function walkSuites(suites: PWSuite[], parentPath: string, out: PWTestResult[]): void {
  for (const s of suites) {
    const here = parentPath ? `${parentPath} > ${s.title}` : s.title;
    for (const spec of s.specs ?? []) {
      const file = spec.file ?? s.file ?? 'unknown.spec.ts';
      const titlePath = here ? `${here} > ${spec.title}` : spec.title;
      for (const t of spec.tests ?? []) {
        const proj = t.projectName ?? null;
        for (const r of t.results ?? []) {
          const row: PWTestResult = {
            test_id: `${file}::${titlePath}${proj ? `::${proj}` : ''}`,
            file,
            title: titlePath,
            project: proj,
            status: r.status,
            retry: typeof r.retry === 'number' ? r.retry : 0,
            error_message: r.error?.message ?? null,
            error_stack: r.error?.stack ?? null,
            attachments: (r.attachments ?? []).map((a) => {
              const att: { name: string; path?: string; contentType?: string } = {
                name: a.name,
              };
              if (a.path !== undefined) att.path = a.path;
              if (a.contentType !== undefined) att.contentType = a.contentType;
              return att;
            }),
          };
          if (typeof r.duration === 'number') row.duration_ms = Math.round(r.duration);
          out.push(row);
        }
      }
    }
    if (s.suites) walkSuites(s.suites, here, out);
  }
}

/**
 * If the bundle didn't include a package.json / playwright.config / tsconfig,
 * scaffold minimal ones so `npx playwright test` can actually run.
 *
 * This is the difference between "bundle won't run" and "bundle just runs".
 * Generated code from the LLM frequently emits just the .spec.ts file because
 * that's the only file the user explicitly asked about.
 */
async function ensureScaffold(
  workDir: string,
  bundleFiles: Array<{ path: string }>,
  label: string,
): Promise<void> {
  const have = new Set(bundleFiles.map((f) => f.path.replace(/\\/g, '/').toLowerCase()));
  const wrote: string[] = [];

  if (!have.has('package.json')) {
    const pkg = {
      name: 'qatb-job',
      private: true,
      type: 'module',
      scripts: { test: 'playwright test' },
      devDependencies: {
        '@playwright/test': '^1.49.0',
        typescript: '^5.6.0',
      },
    };
    await fs.writeFile(join(workDir, 'package.json'), JSON.stringify(pkg, null, 2), 'utf8');
    wrote.push('package.json');
  }

  if (!have.has('playwright.config.ts') && !have.has('playwright.config.js')) {
    const cfg = `import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: ['**/*.spec.ts', '**/*.spec.js'],
  fullyParallel: false,
  retries: 0,
  reporter: [['json', { outputFile: 'qatb-report.json' }], ['list']],
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
`;
    await fs.writeFile(join(workDir, 'playwright.config.ts'), cfg, 'utf8');
    wrote.push('playwright.config.ts');
  }

  if (!have.has('tsconfig.json')) {
    const ts = {
      compilerOptions: {
        target: 'ES2022',
        module: 'ESNext',
        moduleResolution: 'Bundler',
        esModuleInterop: true,
        strict: false,
        skipLibCheck: true,
      },
    };
    await fs.writeFile(join(workDir, 'tsconfig.json'), JSON.stringify(ts, null, 2), 'utf8');
    wrote.push('tsconfig.json');
  }

  if (wrote.length > 0) {
    console.log(`${label} Scaffolded missing: ${wrote.join(', ')}`);
  }
}

/* ------------------------------- helpers ------------------------------- */

async function api<T = unknown>(
  baseUrl: string,
  token: string,
  method: 'GET' | 'POST',
  path: string,
  body?: unknown,
): Promise<T> {
  const init: RequestInit = {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(`${baseUrl}${path}`, init);
  if (!resp.ok) {
    const txt = await resp.text().catch(() => '');
    throw new Error(`${method} ${path} → HTTP ${resp.status}: ${txt.slice(0, 200)}`);
  }
  const ct = resp.headers.get('content-type') ?? '';
  if (ct.includes('application/json')) return (await resp.json()) as T;
  return undefined as T;
}

function runStreamed(
  cmd: string,
  args: string[],
  cwd: string,
  extraEnv: Record<string, string> = {},
): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      cwd,
      env: { ...process.env, ...extraEnv },
      stdio: ['ignore', 'inherit', 'inherit'],
      shell: process.platform === 'win32',
    });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${cmd} exited with code ${code}`));
    });
  });
}

async function fileExists(p: string): Promise<boolean> {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
