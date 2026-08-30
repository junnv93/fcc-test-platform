import { GENERATION_BROWSER_CATALOGUE } from './test-plan-generation-fixture';

import type { components } from '../../../src/api/generated/platform-api.types';
import type { BrowserContext, Page, Route } from '@playwright/test';

/** One deterministic operator context used by the visual matrix. */
// Keep the browser test collection side-effect free. Playwright 1.48's TS
// transformer can block while statically linking an imported `.mjs` fixture;
// the capture module is loaded at test runtime below instead. The id remains
// the same SSOT value as scripts/capture-fixtures/project-workspace-demo.mjs.
export const VISUAL_PROJECT_ID = '11111111-1111-4111-8111-111111111111';

const VISUAL_ROUTE_PATTERNS = Object.freeze({
  coverage: /\/platform\/projects\/[^/]+\/coverage(?:\?.*)?$/u,
  claims: /\/platform\/projects\/[^/]+\/claims(?:\?.*)?$/u,
  syncStatus: /\/platform\/projects\/[^/]+\/sync-status(?:\?.*)?$/u,
  projectProgress: /\/platform\/projects\/[^/]+\/progress(?:\?.*)?$/u,
  projectDetail: /\/platform\/projects\/[^/]+$/u,
  reportSessions: /\/platform\/projects\/[^/]+\/report-sessions(?:\?.*)?$/u,
  projectsList: /\/platform\/projects(?:\?.*)?$/u,
  providersList: /\/platform\/providers(?:\?.*)?$/u,
  resultSelections: /\/platform\/projects\/[^/]+\/providers\/[^/]+\/result-selections(?:\?.*)?$/u,
  sampleInventory: /\/platform\/sample-inventory(?:\?.*)?$/u,
  chamberProgress: /\/platform\/chambers\/[^/]+\/measurements\/progress(?:\?.*)?$/u,
});

export const VISUAL_ROUTE_DEFINITIONS = [
  {
    key: 'home',
    path: '/',
    ready: '[data-testid="home-workbench"]',
  },
  {
    key: 'my-projects',
    path: '/my-projects',
    ready: '[data-testid="my-projects-workbench"]',
  },
  {
    key: 'projects',
    path: `/projects?project=${VISUAL_PROJECT_ID}&tech=UNII`,
    ready: '[data-testid="coverage-matrix"]',
  },
  {
    key: 'test-plans',
    path: `/test-plans?project=${VISUAL_PROJECT_ID}`,
    ready: '[data-testid="test-plans-table"]',
  },
  {
    key: 'chambers',
    path: `/chambers?project=${VISUAL_PROJECT_ID}`,
    ready: '[data-testid="chambers-table"]',
  },
  {
    key: 'reports',
    path: `/reports?project=${VISUAL_PROJECT_ID}`,
    ready: '[data-testid="report-session-picker"]',
  },
  {
    key: 'test-reports',
    path: `/test-reports?project=${VISUAL_PROJECT_ID}`,
    ready: '[data-testid="test-reports-table"]',
  },
] as const;

/** The amended Wave 1 bilingual capture set; the seven-route matrix above is unchanged. */
export const WAVE_1_VISUAL_ROUTE_DEFINITIONS = [
  {
    key: 'home',
    path: '/',
    ready: '[data-testid="home-workbench"]',
    titleKey: 'routes.home.title',
  },
  {
    key: 'my-projects',
    path: '/my-projects',
    ready: '[data-testid="my-projects-workbench"]',
    titleKey: 'routes.myProjects.title',
  },
  {
    key: 'fields',
    path: `/fields?project=${VISUAL_PROJECT_ID}`,
    ready: '[data-testid="fields-workbench"]',
    titleKey: 'routes.fields.title',
  },
  {
    key: 'inventory',
    path: `/inventory?project=${VISUAL_PROJECT_ID}`,
    ready: '[data-testid="inventory-workbench"]',
    titleKey: 'routes.sampleInventory.pageTitle',
  },
  {
    key: 'test-plans',
    path: `/test-plans?project=${VISUAL_PROJECT_ID}`,
    ready: '[data-testid="test-plans-table"]',
    titleKey: 'routes.testPlans.pageTitle',
  },
] as const;

const COMPACT_CARD_ROUTE_KEYS = new Set(['projects', 'test-reports']);

export type VisualRouteKey = (typeof VISUAL_ROUTE_DEFINITIONS)[number]['key'];
export type Wave1VisualRouteKey = (typeof WAVE_1_VISUAL_ROUTE_DEFINITIONS)[number]['key'];
export type VisualTheme = 'light' | 'dark';
export type VisualDensity = 'comfortable' | 'compact';
export type VisualLocale = 'ko' | 'en';
export interface VisualRouteDefinition {
  readonly key: string;
  readonly path: string;
  readonly ready: string;
}

export type ExternalRequestStatusClass = '2xx' | '3xx' | '304';

/** A narrow policy decision for a non-app-origin request. */
export interface ExternalRequestPolicyDecision {
  readonly family: string;
  readonly acceptedStatusClasses: readonly ExternalRequestStatusClass[];
}

export type ExternalRequestPolicy = (
  requestUrl: string,
  method: string,
) => ExternalRequestPolicyDecision | null;

export interface VisualFixtureHandle {
  readonly unexpectedRequests: string[];
  readonly unexpectedConsoleErrors: string[];
  readonly pageErrors: string[];
}

const FIXED_NOW = '2026-08-02T09:00:00+09:00';
const LOCAL_HOSTNAMES = new Set(['127.0.0.1', 'localhost', 'hostmachine']);
const API_PREFIXES = ['/headless/', '/platform/', '/report-automation/', '/session/'];

const CHAMBERS = [
  {
    chamber_id: 'chamber-a',
    name: 'Alpha Chamber',
    base_url: 'http://node-a.local:8001',
    enabled: true,
    status: 'idle',
    heartbeat_ttl_seconds: 30,
    last_heartbeat_at: '2026-08-02T08:58:30+09:00',
    reported_status: 'idle',
    session_id: null,
    unavailable_reason: null,
    last_error: null,
    last_error_at: null,
  },
  {
    chamber_id: 'chamber-b',
    name: 'Beta Chamber',
    base_url: 'http://node-b.local:8001',
    enabled: true,
    status: 'in_use',
    heartbeat_ttl_seconds: 30,
    last_heartbeat_at: '2026-08-02T08:59:00+09:00',
    reported_status: 'in_use',
    session_id: 'session-204',
    unavailable_reason: null,
    last_error: null,
    last_error_at: null,
  },
] as const;

const FIXED_CHAMBER_RESPONSE = {
  items: CHAMBERS,
  server_time: FIXED_NOW,
};

const FIXED_PROGRESS = {
  is_running: true,
  completed: 8,
  total: 12,
  ratio: 8 / 12,
};

const FIXED_PROJECT = {
  project_id: VISUAL_PROJECT_ID,
  project_code: 'SM-S928N',
  model_name: 'SM-S928N',
  customer: 'Samsung MX',
  manufacturer: 'Samsung Electronics',
  management_number: '26-RF-0142',
  status: 'active',
  sample_count: 3,
};

const FIXED_PROJECT_DETAIL = {
  ...FIXED_PROJECT,
  samples: [],
};

const FIXED_PROVIDER_ID = 'fcc-unlicensed-conducted';

type ProviderSummaryList = components['schemas']['ProviderSummaryList'];
type ResultSelectionList = components['schemas']['ResultSelectionList'];

const FIXED_PROVIDERS = [
  {
    provider_id: FIXED_PROVIDER_ID,
    display_name: 'Unlicensed Conducted',
    ui_version: 1,
  },
] satisfies ProviderSummaryList;

const FIXED_RESULT_SELECTIONS = [] satisfies ResultSelectionList;

type SampleInventoryPage = components['schemas']['SampleInventoryPage'];

const FIXED_SAMPLE_INVENTORY = {
  items: [
    {
      sample_id: '22222222-2222-4222-8222-222222222222',
      project_id: VISUAL_PROJECT_ID,
      sample_number: 'S-001',
      sample_code: 'CODE-001',
      test_category: 'Main Conduction',
      label_number: 'LBL-001',
      smsn: 'SMSN-001',
      serial_number: 'SN-001',
      intake_cert: 'CERT-001',
      assigned_team: 'RF',
      sender: 'sender',
      receiver: 'receiver',
      received_date: '2026-08-01',
      released_date: null,
      note: null,
      status: 'active',
      row_version: 1,
      intake_count: 1,
      latest_intake: null,
    },
  ],
  next_cursor: null,
  as_of: null,
  filters: {},
} satisfies SampleInventoryPage;

const FIXED_PROJECT_PROGRESS = [
  {
    progress_area: 'unlicensed_conducted',
    progress_bucket_id: null,
    planned_minutes: 100,
    completed_minutes: 50,
    percent: 50,
    total_conditions: 4,
    priced_conditions: 4,
    unpriced_conditions: 0,
    unbucketable_conditions: 0,
  },
] as const;

const FIXED_COVERAGE = [
  {
    project_id: VISUAL_PROJECT_ID,
    condition_hash: 'cond-ble-01',
    technology: 'BLE',
    attempt_count: 1,
    distinct_operator_count: 1,
    latest_verdict: 'Pass',
    latest_attempt_number: 1,
    latest_measured_at: '2026-08-01T09:10:00+09:00',
    latest_operator: 'operator-a',
    latest_session_id: 'S-1001',
  },
  {
    project_id: VISUAL_PROJECT_ID,
    condition_hash: 'cond-unii-01',
    technology: 'UNII',
    attempt_count: 4,
    distinct_operator_count: 2,
    latest_verdict: 'running',
    latest_attempt_number: 4,
    latest_measured_at: '2026-08-01T10:35:00+09:00',
    latest_operator: 'operator-b',
    latest_session_id: 'S-1002',
  },
] as const;

const FIXED_DRAFT = {
  draft_id: 'draft-9',
  project_id: VISUAL_PROJECT_ID,
  status: 'draft',
  row_count: 12,
  created_at: '2026-08-02T02:00:00+09:00',
  updated_at: '2026-08-02T03:00:00+09:00',
};

const FIXED_REPORT = {
  report_id: 'report-1',
  project_id: VISUAL_PROJECT_ID,
  edition: '1',
  report_number: 'S-26-RF-0142-1',
  date_tested_start: '2026-08-01',
  date_tested_end: '2026-08-02',
  date_of_issue: '2026-08-02',
  prepared_by: '교육용 시험원',
  prepared_site: 'FCC Lab',
  created_at: '2026-08-02T04:00:00+09:00',
};

const FIXED_REPORT_SESSIONS = [
  {
    node_id: 'node-alpha',
    node_name: 'Alpha Chamber PC',
    node_base_url: 'http://node-alpha.local:8001',
    project_id: VISUAL_PROJECT_ID,
    submit_session_id: 101,
    latest_measured_at: '2026-08-01T11:20:00+09:00',
    latest_verdict: 'Pass',
    completed_conditions: 24,
    technologies: ['BLE', 'UNII'],
  },
] as const;

const FIXED_DESCRIPTOR = {
  provider_id: FIXED_PROVIDER_ID,
  display_name: 'Unlicensed Conducted',
  ui_version: 1,
  features: [],
  test_plan_tables: [],
  equipment: [],
  reference_tables: [],
  correction_tables: [],
  workbench_area_technologies: { rf_conducted: ['BLE', 'BT', 'UNII'] },
};

function json(route: Route, body: unknown): Promise<void> {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

function isApiPath(pathname: string): boolean {
  return API_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

export function isExternalResponseStatusAllowed(
  decision: ExternalRequestPolicyDecision,
  status: number,
): boolean {
  return decision.acceptedStatusClasses.some((statusClass) => {
    if (statusClass === '2xx') return status >= 200 && status < 300;
    if (statusClass === '3xx') return status >= 300 && status < 400;
    return status === 304;
  });
}

async function installVisualApiMocks(context: BrowserContext): Promise<void> {
  await context.route(VISUAL_ROUTE_PATTERNS.coverage, async (route) => json(route, FIXED_COVERAGE));
  await context.route(VISUAL_ROUTE_PATTERNS.claims, async (route) => json(route, []));
  await context.route(VISUAL_ROUTE_PATTERNS.syncStatus, async (route) =>
    json(route, {
      project_id: VISUAL_PROJECT_ID,
      server_time: FIXED_NOW,
      last_ingested_at: '2026-08-02T08:46:00+09:00',
      age_seconds: 840,
      is_stale: false,
      stale_threshold_seconds: 3600,
      condition_count: FIXED_COVERAGE.length,
      active_claim_count: 0,
    }),
  );
  await context.route(VISUAL_ROUTE_PATTERNS.projectsList, async (route) =>
    json(route, [FIXED_PROJECT]),
  );
  await context.route(VISUAL_ROUTE_PATTERNS.providersList, async (route) =>
    json(route, FIXED_PROVIDERS),
  );
  await context.route(VISUAL_ROUTE_PATTERNS.resultSelections, async (route) =>
    json(route, FIXED_RESULT_SELECTIONS),
  );
  await context.route(VISUAL_ROUTE_PATTERNS.sampleInventory, async (route) =>
    json(route, FIXED_SAMPLE_INVENTORY),
  );
  await context.route(VISUAL_ROUTE_PATTERNS.projectDetail, async (route) =>
    json(route, FIXED_PROJECT_DETAIL),
  );
  await context.route(VISUAL_ROUTE_PATTERNS.projectProgress, async (route) =>
    json(route, FIXED_PROJECT_PROGRESS),
  );
  await context.route(/\/platform\/projects\/[^/]+\/reports(?:\?.*)?$/u, async (route) =>
    json(route, [FIXED_REPORT]),
  );
  await context.route(/\/platform\/projects\/[^/]+\/report-citation(?:\?.*)?$/u, async (route) =>
    json(route, {
      project_id: VISUAL_PROJECT_ID,
      management_number: FIXED_PROJECT.management_number,
      report_number: FIXED_REPORT.report_number,
      samples: [],
    }),
  );
  await context.route(VISUAL_ROUTE_PATTERNS.reportSessions, async (route) =>
    json(route, FIXED_REPORT_SESSIONS),
  );
  await context.route(
    /\/platform\/providers\/fcc-unlicensed-conducted\/ui-descriptor$/u,
    async (route) => json(route, FIXED_DESCRIPTOR),
  );
  await context.route(/\/platform\/chambers$/u, async (route) =>
    json(route, FIXED_CHAMBER_RESPONSE),
  );
  await context.route(VISUAL_ROUTE_PATTERNS.chamberProgress, async (route) =>
    json(route, { chamber_id: 'chamber-b', progress: FIXED_PROGRESS }),
  );
  await context.route(/\/headless\/projects\/[^/]+\/test-plan\/drafts$/u, async (route) =>
    json(route, { drafts: [FIXED_DRAFT], next_cursor: null }),
  );
  await context.route(/\/headless\/test-plan\/generation\/catalogue(?:\?.*)?$/u, async (route) =>
    json(route, GENERATION_BROWSER_CATALOGUE),
  );
  await context.route(
    /\/headless\/projects\/[^/]+\/test-plan\/publications(?:\?.*)?$/u,
    async (route) =>
      json(route, {
        publications: [
          {
            plan_id: 'published-plan-1',
            draft_id: FIXED_DRAFT.draft_id,
            published_at: '2026-08-02T08:30:00+09:00',
          },
        ],
      }),
  );
  await context.route(/\/report-automation\/stats$/u, async (route) =>
    json(route, { queued: 1, running: 1, completed: 18, failed: 0, cancelled: 0 }),
  );
}

/** Install the fixed API and browser invariants used by every visual capture. */
export async function installVisualFixture(
  context: BrowserContext,
  baseOrigin = process.env['E2E_BASE_URL'] ?? 'http://localhost:5173',
  externalRequestPolicy?: ExternalRequestPolicy,
): Promise<VisualFixtureHandle> {
  const unexpectedRequests: string[] = [];
  const unexpectedConsoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const origin = new URL(baseOrigin).origin;

  await context.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const sameOrigin = url.origin === origin;
    const isLocalAsset = LOCAL_HOSTNAMES.has(url.hostname);
    if (!sameOrigin) {
      const decision = externalRequestPolicy?.(request.url(), request.method()) ?? null;
      if (decision === null) {
        unexpectedRequests.push(
          `${request.method()} ${request.url()} (unclassified external request)`,
        );
        await route.abort();
        return;
      }
      try {
        const response = await route.fetch({ maxRedirects: 0 });
        if (!isExternalResponseStatusAllowed(decision, response.status())) {
          unexpectedRequests.push(
            `${request.method()} ${request.url()} (${decision.family}) returned HTTP ${response.status()}`,
          );
          await route.abort();
          return;
        }
        await route.fulfill({ response });
      } catch (error) {
        unexpectedRequests.push(
          `${request.method()} ${request.url()} (${decision.family}) failed: ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
        await route.abort();
      }
      return;
    }
    if (!sameOrigin || !isLocalAsset) {
      unexpectedRequests.push(`${request.method()} ${url.toString()}`);
      await route.abort();
      return;
    }
    if (isApiPath(url.pathname)) {
      unexpectedRequests.push(`${request.method()} ${url.pathname}`);
      await route.abort();
      return;
    }
    await route.continue();
  });

  // Specific handlers are registered after the fail-closed fallback so
  // Playwright gives them precedence. This TS-side matrix fixture keeps test
  // collection independent from Node's `.mjs` loader while the capture scripts
  // continue to use their own shared fixture module.
  await installVisualApiMocks(context);

  await context.addInitScript({
    content: `
      (() => {
        const RealDate = Date;
        const fixedNow = RealDate.parse(${JSON.stringify(FIXED_NOW)});
        class FixedDate extends RealDate {
          constructor(...args) {
            super(...(args.length === 0 ? [fixedNow] : args));
          }
          static now() {
            return fixedNow;
          }
        }
        globalThis.Date = FixedDate;
        Math.random = () => 0.42;
        window.localStorage.setItem('fcc-locale', 'ko');
        window.localStorage.setItem('fcc-density', 'comfortable');
        window.localStorage.setItem('fcc-theme', 'light');

        // Production runtime-config points WebSocket clients at the deployed
        // API origin. The visual fixture must remain server-independent, so
        // open a deterministic in-memory socket instead of attempting a real
        // chamber/session event connection.
        class FixtureWebSocket {
          static CONNECTING = 0;
          static OPEN = 1;
          static CLOSING = 2;
          static CLOSED = 3;

          constructor(url) {
            this.url = url;
            this.readyState = FixtureWebSocket.CONNECTING;
            this.listeners = new Map();
            queueMicrotask(() => {
              if (this.readyState !== FixtureWebSocket.CONNECTING) return;
              this.readyState = FixtureWebSocket.OPEN;
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
            // The fixed fixture emits no live events.
          }

          close() {
            if (this.readyState === FixtureWebSocket.CLOSED) return;
            this.readyState = FixtureWebSocket.CLOSED;
            const event = new CloseEvent('close', { code: 1000 });
            this.onclose?.(event);
            this.dispatch('close', event);
          }
        }

        globalThis.WebSocket = FixtureWebSocket;
      })();
    `,
  });

  const observePage = (page: Page): void => {
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') unexpectedConsoleErrors.push(message.text());
    });
  };
  for (const page of context.pages()) observePage(page);
  context.on('page', observePage);

  return { unexpectedRequests, unexpectedConsoleErrors, pageErrors };
}

/** Wait for the route's own ready sentinel and reject error/skeleton captures. */
export async function assertVisualRouteReady(
  page: Page,
  route: VisualRouteDefinition,
): Promise<void> {
  // The coverage table intentionally becomes a card list below --bp-sm. The
  // semantic route sentinel remains attached for information-preservation
  // checks, but the visible ready surface is the card list in that band.
  const compactReadySelector =
    COMPACT_CARD_ROUTE_KEYS.has(route.key) && (await page.evaluate(() => window.innerWidth < 640))
      ? '.data-table-cards'
      : route.ready;
  await page.locator(compactReadySelector).waitFor({ state: 'visible', timeout: 15_000 });
  await page.evaluate(() => document.fonts.ready);
  await page.locator('[aria-busy="true"]').waitFor({ state: 'detached', timeout: 15_000 });
  await page.locator('.block-skeleton, .data-table-skeleton').waitFor({
    state: 'detached',
    timeout: 15_000,
  });
  const errorSurfaces = page.locator('.error-fallback, .error-state');
  if ((await errorSurfaces.count()) > 0) {
    const messages = await errorSurfaces.allTextContents();
    throw new Error(
      `visual fixture route ${route.key} rendered an error surface: ${JSON.stringify(messages)}`,
    );
  }
}

export function visualSnapshotName(
  route: VisualRouteKey,
  theme: VisualTheme,
  density: VisualDensity,
  width: number,
): string {
  return `${route}-${theme}-${density}-${width}.png`;
}

export function wave1VisualSnapshotName(
  route: Wave1VisualRouteKey,
  locale: VisualLocale,
  theme: VisualTheme,
  density: VisualDensity,
  width: number,
): string {
  return `${route}-${locale}-${theme}-${density}-${width}.png`;
}

export const VISUAL_FIXED_NOW = FIXED_NOW;
export { VISUAL_ROUTE_PATTERNS };
