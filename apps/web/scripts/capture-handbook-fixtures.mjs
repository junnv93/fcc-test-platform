import { chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  DEMO_PROJECT_ID,
  installProjectWorkspaceCaptureMocks,
} from './capture-fixtures/project-workspace-demo.mjs';
import { launchHarnessBrowser } from './harness-browser-runtime.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = resolve(HERE, '..');
const REPO_ROOT = resolve(APP_ROOT, '..', '..');
const OUT = resolve(REPO_ROOT, 'docs', 'education', 'images');
const BASE = process.env.FCC_WEB_BASE ?? 'http://127.0.0.1:5173';
const VITE = resolve(APP_ROOT, 'node_modules', 'vite', 'bin', 'vite.js');
const PROJECT_QUERY = `?project=${DEMO_PROJECT_ID}`;
const LOCAL_HOSTNAMES = new Set(['127.0.0.1', 'localhost', 'hostmachine']);
const API_PREFIXES = ['/session/', '/headless/', '/platform/', '/report-automation/'];
const API_PATH_ALLOWLIST = [
  /^\/session\/(?:info|progress)$/u,
  /^\/headless\/(?:status|jobs|projects\/[^/]+\/.*|sessions\/[^/]+\/.*|reports\/.*)$/u,
  /^\/platform\/(?:projects(?:\/[^/]+(?:\/.*)?)?|providers(?:\/.*)?|chambers(?:\/.*)?)$/u,
  /^\/report-automation\/stats$/u,
];
const READY_TIMEOUT_MS = 30_000;
const LOADING_SURFACE_SELECTOR = '[aria-busy="true"], .block-skeleton, .data-table-skeleton';
const ERROR_SURFACE_SELECTOR =
  '.error-fallback, .error-state, [data-testid="route-error-fallback"], [data-testid="shell-error-fallback"]';

const SCREENS = [
  ['01-overview', '/', '[data-testid="home-workbench"]'],
  ['02-my-projects', '/my-projects', '[data-testid="my-projects-workbench"]'],
  ['03-fields', `/fields${PROJECT_QUERY}`, '[data-testid="fields-workbench"]'],
  ['04-inventory', `/inventory${PROJECT_QUERY}`, '[data-testid="inventory-workbench"]'],
  ['05-test-plans', `/test-plans${PROJECT_QUERY}`, '[data-testid="test-plans-workbench-overview"]'],
  ['06-chambers', '/chambers', '[data-testid="chambers-workbench-overview"]'],
  ['07-control', '/control', '[data-testid="control-workbench"]'],
  ['08-progress', `/progress${PROJECT_QUERY}`, '[data-testid="progress-workbench"]'],
  ['09-projects', `/projects${PROJECT_QUERY}&tech=UNII`, '[data-testid="coverage-matrix"]'],
  ['10-jobs', '/jobs', '[data-testid="jobs-workbench"]'],
  ['11-sessions', '/sessions?session=3', '[data-testid="sessions-workbench"]'],
  ['12-reports', `/reports${PROJECT_QUERY}`, '[data-testid="reports-workbench"]'],
  ['13-test-reports', `/test-reports${PROJECT_QUERY}`, '[data-testid="test-reports-workbench"]'],
  ['14-membership', `/membership${PROJECT_QUERY}`, '[data-testid="membership-workbench"]'],
  ['15-providers', '/providers', '[data-testid="providers-workbench"]'],
  ['16-diagnostics', '/diagnostics', '[data-testid="diagnostics-workbench"]'],
];

const json = (route, body, status = 200) =>
  route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });

function tokenSet() {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString('base64url');
  const accessToken = [
    encode({ alg: 'RS256', typ: 'JWT' }),
    encode({
      sub: 'handbook-operator',
      name: '교육용 시험원',
      email: 'operator@fcc.test',
      permissions: [
        'session:read',
        'session:control',
        'session:events',
        'headless:read',
        'headless:control',
        'report_automation:read',
        'report_automation:control',
        'platform:read',
        'platform:claim',
        'platform:admin',
        'platform:sample-write',
        'test_plan:read',
        'test_plan:author',
      ],
      roles: ['operator'],
      scope: 'openid profile',
    }),
    'handbook-signature',
  ].join('.');
  return {
    accessToken,
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 3600,
    issuedAt: Date.now(),
  };
}

function isApiPath(pathname) {
  return API_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function isApprovedApiRequest(request) {
  return (
    request.method() === 'GET' &&
    API_PATH_ALLOWLIST.some((pattern) => pattern.test(new URL(request.url()).pathname))
  );
}

async function waitForBase() {
  for (let tries = 0; tries < 90; tries += 1) {
    try {
      const response = await fetch(BASE);
      if (response.ok) return;
    } catch {
      /* Vite is still starting. */
    }
    await new Promise((done) => setTimeout(done, 500));
  }
  throw new Error(`Vite did not become ready at ${BASE}`);
}

async function installHandbookMocks(context) {
  // Fallback first: specific project-workspace handlers are installed last and win.
  await context.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (
      !path.startsWith('/session/') &&
      !path.startsWith('/headless/') &&
      !path.startsWith('/platform/') &&
      !path.startsWith('/report-automation/')
    ) {
      return route.continue();
    }
    if (path === '/session/progress') {
      return json(route, { is_running: true, completed: 7, total: 12, ratio: 0.583 });
    }
    if (path === '/session/info') {
      return json(route, {
        api_version: '2',
        operations: ['start_session', 'stop_session', 'read_measurements'],
        routes: [
          { operation: 'start_session', method: 'POST', path: '/session/start' },
          { operation: 'stop_session', method: 'POST', path: '/session/stop' },
        ],
      });
    }
    if (path === '/headless/status') {
      return json(route, {
        measurement_jobs: {
          counts: { queued: 2, running: 1, completed: 12, failed: 0, cancelled: 1 },
          recent: [],
        },
        workers: [],
        report_automation: { queued: 2, running: 1, completed: 12, failed: 0, cancelled: 1 },
      });
    }
    if (path === '/headless/jobs') {
      return json(route, [
        {
          id: 7,
          job_uuid: 'job-7',
          status: 'running',
          excel_path: 'C:\\plans\\fcc-demo.xlsx',
          requested_by: '교육용 시험원',
          assigned_worker_id: 'worker-1',
          status_message: '측정 준비 중',
          stop_requested: false,
          payload: {},
          options: {},
          created_at: '2026-08-02T04:00:00Z',
          updated_at: '2026-08-02T04:30:00Z',
        },
      ]);
    }
    if (path === '/platform/chambers') {
      return json(route, {
        items: [
          {
            chamber_id: 'chamber-a',
            name: 'Alpha Chamber',
            base_url: 'http://node-a',
            enabled: true,
            status: 'idle',
            heartbeat_ttl_seconds: 30,
            last_heartbeat_at: '2026-08-02T04:30:00Z',
            reported_status: 'idle',
            session_id: null,
          },
          {
            chamber_id: 'chamber-b',
            name: 'Beta Chamber',
            base_url: 'http://node-b',
            enabled: true,
            status: 'in_use',
            heartbeat_ttl_seconds: 30,
            last_heartbeat_at: '2026-08-02T04:29:50Z',
            reported_status: 'in_use',
            session_id: '3',
          },
        ],
        server_time: '2026-08-02T04:30:00Z',
      });
    }
    if (path.endsWith('/progress') && path.includes('/platform/projects/')) {
      return json(route, [
        {
          progress_area: 'unlicensed_conducted',
          progress_bucket_id: null,
          planned_minutes: 100,
          completed_minutes: 58,
          percent: 58,
          total_conditions: 12,
          priced_conditions: 12,
          unpriced_conditions: 0,
          unbucketable_conditions: 0,
        },
      ]);
    }
    if (path.includes('/test-plan/drafts')) {
      return json(route, {
        drafts: [
          {
            draft_id: 'draft-9',
            project_id: DEMO_PROJECT_ID,
            status: 'draft',
            row_count: 12,
            created_at: '2026-08-02T02:00:00Z',
            updated_at: '2026-08-02T03:00:00Z',
          },
        ],
        next_cursor: null,
      });
    }
    if (path.endsWith('/attempts')) {
      return json(route, {
        items: [
          {
            provider_id: 'fcc',
            session_id: '3',
            attempt_id: 'attempt-1',
            condition_hash: 'demo-condition',
            sheet_name: 'Test Plan',
            row_order: 1,
            technology: 'UNII',
            attempt_number: 1,
            result: { result1: '10 dBm', result2: '', result_sum: '', margin: '1.2', dccf: '' },
            verdict: 'Pass',
            status: 'completed',
            recorded_by: 'operator',
            measured_at: '2026-08-02T04:00:00Z',
          },
        ],
        next_cursor: null,
      });
    }
    if (path.endsWith('/report-automation/stats')) {
      return json(route, { queued: 2, running: 1, completed: 12, failed: 0, cancelled: 1 });
    }
    if (path === '/platform/providers') {
      return json(route, [{ provider_id: 'fcc', display_name: 'FCC 시험 제공자', ui_version: 1 }]);
    }
    if (path.includes('/jobs')) return json(route, { items: [], next_cursor: null });
    if (path.includes('/providers')) return json(route, { items: [], next_cursor: null });
    if (path.includes('/members') || path.includes('/users')) return json(route, []);
    if (path.includes('/sample-inventory')) return json(route, { items: [], samples: [] });
    if (path.endsWith('/report-citation')) {
      return json(route, {
        management_number: '26-RF-0142',
        fcc_id: 'A3LSMS928N',
        applicant_name: 'Samsung Electronics',
        applicant_address: 'Suwon, Korea',
        eut_description: 'Mobile Device',
        test_standard: 'FCC Part 15',
        report_number: null,
        samples: [],
        firmware: [],
      });
    }
    if (/\/platform\/projects\/[^/]+\/reports$/.test(path)) {
      return json(route, [
        {
          report_id: 'report-1',
          project_id: DEMO_PROJECT_ID,
          edition: '1',
          report_number: 'S-26-RF-0142-1',
          date_tested_start: '2026-08-01',
          date_tested_end: '2026-08-02',
          date_of_issue: '2026-08-02',
          prepared_by: '교육용 시험원',
          prepared_site: 'FCC Lab',
          created_at: '2026-08-02T04:00:00Z',
        },
      ]);
    }
    if (path.includes('/diagnostics')) return json(route, { status: 'ok', checks: [] });
    return json(route, request.method() === 'GET' ? [] : {});
  });
  await installProjectWorkspaceCaptureMocks(context);
}

async function assertDocumentationReady(page, label, ready, state) {
  await page.locator(ready).waitFor({ state: 'visible', timeout: READY_TIMEOUT_MS });
  await page.evaluate(() => globalThis.document.fonts.ready);
  await page
    .locator(LOADING_SURFACE_SELECTOR)
    .waitFor({ state: 'detached', timeout: READY_TIMEOUT_MS });
  const errorSurfaces = page.locator(ERROR_SURFACE_SELECTOR);
  const loadingSurfaces = page.locator(LOADING_SURFACE_SELECTOR);
  const errorSurfaceCount = await errorSurfaces.count();
  const loadingSurfaceCount = await loadingSurfaces.count();
  const headingCount = await page.getByRole('heading', { level: 1 }).count();
  if (headingCount !== 1) {
    throw new Error(`${label} did not render exactly one route heading (count=${headingCount})`);
  }
  if (state.pageErrors.length > 0 || state.consoleErrors.length > 0) {
    throw new Error(
      `${label} emitted browser errors: ${JSON.stringify({
        pageErrors: state.pageErrors,
        consoleErrors: state.consoleErrors,
      })}`,
    );
  }
  if (state.unexpectedRequests.length > 0 || state.unexpectedApiRequests.length > 0) {
    throw new Error(
      `${label} violated the request policy: ${JSON.stringify({
        external: state.unexpectedRequests,
        api: state.unexpectedApiRequests,
      })}`,
    );
  }
  if (errorSurfaceCount > 0 || loadingSurfaceCount > 0) {
    throw new Error(
      `${label} is not a ready documentation state: ${JSON.stringify({
        errorSurfaceCount,
        loadingSurfaceCount,
      })}`,
    );
  }
}

async function main() {
  await mkdir(OUT, { recursive: true });
  const server = spawn(
    process.execPath,
    [VITE, '--host', '127.0.0.1', '--port', '5173', '--strictPort'],
    {
      cwd: APP_ROOT,
      stdio: 'pipe',
      env: process.env,
    },
  );
  let browser;
  try {
    await waitForBase();
    browser = await launchHarnessBrowser(chromium);
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 2,
    });
    await installHandbookMocks(context);
    await context.addInitScript(
      ({ tokens }) => {
        globalThis.window.sessionStorage.setItem('fcc-oidc:tokens', JSON.stringify(tokens));
        globalThis.window.localStorage.setItem('fcc-locale', 'ko');
        globalThis.window.localStorage.setItem('fcc-density', 'comfortable');
        globalThis.window.localStorage.setItem('fcc-theme', 'light');
      },
      { tokens: tokenSet() },
    );
    await context.addInitScript(() => {
      // `/control` normally opens the live session-event stream. Documentation
      // captures are intentionally server-independent, so provide a silent
      // deterministic socket that reaches OPEN without opening a network
      // connection and closes cleanly when the route unmounts.
      class DocumentationWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;

        constructor(url) {
          this.url = url;
          this.readyState = DocumentationWebSocket.CONNECTING;
          this.listeners = new Map();
          queueMicrotask(() => {
            if (this.readyState !== DocumentationWebSocket.CONNECTING) return;
            this.readyState = DocumentationWebSocket.OPEN;
            this.onopen?.(new Event('open'));
            this.dispatch('open', new Event('open'));
          });
        }

        addEventListener(type, listener) {
          const entries = this.listeners.get(type) ?? [];
          entries.push(listener);
          this.listeners.set(type, entries);
        }

        removeEventListener(type, listener) {
          this.listeners.set(
            type,
            (this.listeners.get(type) ?? []).filter((entry) => entry !== listener),
          );
        }

        dispatch(type, event) {
          for (const listener of this.listeners.get(type) ?? []) listener(event);
        }

        send() {
          // The documentation socket intentionally emits no live events.
        }

        close() {
          if (this.readyState === DocumentationWebSocket.CLOSED) return;
          this.readyState = DocumentationWebSocket.CLOSED;
          const event = new CloseEvent('close', { code: 1000 });
          this.onclose?.(event);
          this.dispatch('close', event);
        }
      }

      globalThis.window.WebSocket = DocumentationWebSocket;
    });
    const page = await context.newPage();
    const state = {
      pageErrors: [],
      consoleErrors: [],
      unexpectedRequests: [],
      unexpectedApiRequests: [],
    };
    page.on('pageerror', (error) => state.pageErrors.push(error.stack ?? error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') state.consoleErrors.push(message.text());
    });
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (!LOCAL_HOSTNAMES.has(url.hostname)) {
        state.unexpectedRequests.push(`${request.method()} ${request.url()}`);
      } else if (isApiPath(url.pathname) && !isApprovedApiRequest(request)) {
        state.unexpectedApiRequests.push(`${request.method()} ${url.pathname}`);
      }
    });
    const first = Number.parseInt(process.env.FCC_CAPTURE_FIRST ?? '0', 10);
    const count = Number.parseInt(process.env.FCC_CAPTURE_COUNT ?? String(SCREENS.length), 10);
    for (const [file, route, ready] of SCREENS.slice(first, first + count)) {
      state.pageErrors.length = 0;
      state.consoleErrors.length = 0;
      state.unexpectedRequests.length = 0;
      state.unexpectedApiRequests.length = 0;
      // Several workbenches poll operational state. `networkidle` can therefore
      // never settle even though the rendered UI is ready for documentation.
      await page.goto(new URL(route, BASE).toString(), { waitUntil: 'domcontentloaded' });
      await assertDocumentationReady(page, file, ready, state);
      await page.screenshot({ path: join(OUT, `${file}.png`), fullPage: false });
      console.log(`captured ${file}.png (${route})`);
    }
    await context.close();
  } finally {
    await browser?.close();
    server.kill('SIGINT');
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
