/**
 * Mock data SSOT for the project-workspace training capture.
 *
 * The training screenshots should demonstrate the IA changes from this session
 * without depending on Keycloak, PostgreSQL, or live chamber/headless nodes.
 * This module owns one deterministic demo project and the exact HTTP payloads
 * the browser capture harness fulfills for the three changed operator screens:
 *   - /my-projects
 *   - /projects?project=...
 *   - /reports?project=...
 *
 * Keeping the fixture in one module avoids re-hardcoding ids/labels/links
 * across the capture script and the education document.
 */
export const DEMO_PROJECT_ID = '11111111-1111-4111-8111-111111111111';
export const DEMO_PROVIDER_ID = 'fcc-unlicensed-conducted';
export const CAPTURE_ROUTE_PATTERNS = Object.freeze({
  coverage: /\/platform\/projects\/[^/]+\/coverage(?:\?.*)?$/,
  claims: /\/platform\/projects\/[^/]+\/claims(?:\?.*)?$/,
  syncStatus: /\/platform\/projects\/[^/]+\/sync-status(?:\?.*)?$/,
  reportSessions: /\/platform\/projects\/[^/]+\/report-sessions(?:\?.*)?$/,
  projectsList: /\/platform\/projects(?:\?.*)?$/,
  chamberProgress: /\/platform\/chambers\/[^/]+\/measurements\/progress(?:\?.*)?$/,
});

export const DEMO_PROJECT = Object.freeze({
  project_id: DEMO_PROJECT_ID,
  project_code: 'SM-S928N',
  model_name: 'SM-S928N',
  customer: 'Samsung MX',
  manufacturer: 'Samsung Electronics',
  management_number: '26-RF-0142',
  status: 'active',
  sample_count: 3,
});

const COVERAGE_ROWS = Object.freeze([
  {
    project_id: DEMO_PROJECT_ID,
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
    project_id: DEMO_PROJECT_ID,
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
  {
    project_id: DEMO_PROJECT_ID,
    condition_hash: 'cond-unii-02',
    technology: 'UNII',
    attempt_count: 1,
    distinct_operator_count: 1,
    latest_verdict: 'Fail',
    latest_attempt_number: 1,
    latest_measured_at: '2026-08-01T11:20:00+09:00',
    latest_operator: 'operator-c',
    latest_session_id: 'S-1003',
  },
]);

const CLAIM_ROWS = Object.freeze([
  {
    claim_id: 'claim-unii-01',
    project_id: DEMO_PROJECT_ID,
    technology: 'UNII',
    condition_hash: 'cond-unii-01',
    operator: 'operator-b',
    occurred_at: '2026-08-01T10:40:00+09:00',
    expires_at: '2026-08-01T18:40:00+09:00',
    session_id: 'S-1002',
  },
]);

const SYNC_STATUS = Object.freeze({
  project_id: DEMO_PROJECT_ID,
  server_time: '2026-08-02T09:00:00+09:00',
  last_ingested_at: '2026-08-02T08:46:00+09:00',
  age_seconds: 840,
  is_stale: false,
  stale_threshold_seconds: 3600,
  condition_count: 3,
  active_claim_count: 1,
});

const PROVIDER_DESCRIPTOR = Object.freeze({
  provider_id: DEMO_PROVIDER_ID,
  display_name: 'Unlicensed Conducted',
  ui_version: 1,
  features: [],
  test_plan_tables: [],
  equipment: [],
  reference_tables: [],
  correction_tables: [],
  workbench_area_technologies: {
    rf_conducted: ['BLE', 'BT', 'UNII'],
  },
});

const REPORT_SESSIONS = Object.freeze([
  {
    node_id: 'node-alpha',
    node_name: 'Alpha Chamber PC',
    node_base_url: 'http://node-alpha.local:8001',
    project_id: DEMO_PROJECT_ID,
    submit_session_id: 101,
    latest_measured_at: '2026-08-01T11:20:00+09:00',
    latest_verdict: 'Pass',
    completed_conditions: 24,
    technologies: ['BLE', 'UNII'],
  },
  {
    node_id: 'node-beta',
    node_name: 'Beta Chamber PC',
    node_base_url: 'http://node-beta.local:8001',
    project_id: DEMO_PROJECT_ID,
    submit_session_id: 202,
    latest_measured_at: '2026-08-01T13:45:00+09:00',
    latest_verdict: 'Pass',
    completed_conditions: 12,
    technologies: ['BT'],
  },
]);

const REPORT_STATS = Object.freeze({
  queued: 1,
  running: 1,
  completed: 18,
  failed: 0,
  cancelled: 0,
  oldest_queued_request_id: 9001,
});

export const TRAINING_SCREENS = Object.freeze([
  {
    file: 'project-workspace-01-my-projects',
    route: '/my-projects',
    waitFor: '[data-testid="project-card-list"]',
  },
  {
    file: 'project-workspace-02-workspace',
    route: `/projects?project=${DEMO_PROJECT_ID}&tech=UNII`,
    waitFor: '[data-testid="coverage-matrix"]',
  },
  {
    file: 'project-workspace-03-reports',
    route: `/reports?project=${DEMO_PROJECT_ID}&area=rf_conducted`,
    waitFor: '[data-testid="reports-project-context"]',
  },
]);

function json(route, body, init = {}) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
    ...init,
  });
}

export async function installProjectWorkspaceCaptureMocks(context) {
  await context.route(CAPTURE_ROUTE_PATTERNS.coverage, async (route) => {
    const url = new URL(route.request().url());
    console.log('[capture-mock] coverage', url.toString(), 'rows=', COVERAGE_ROWS.length);
    return json(route, COVERAGE_ROWS);
  });

  await context.route(CAPTURE_ROUTE_PATTERNS.claims, async (route) => {
    const url = new URL(route.request().url());
    console.log('[capture-mock] claims', url.toString(), 'rows=', CLAIM_ROWS.length);
    return json(route, CLAIM_ROWS);
  });

  await context.route(CAPTURE_ROUTE_PATTERNS.syncStatus, async (route) => {
    const url = new URL(route.request().url());
    console.log('[capture-mock] sync-status', url.toString());
    return json(route, SYNC_STATUS);
  });

  await context.route(CAPTURE_ROUTE_PATTERNS.reportSessions, async (route) => {
    const url = new URL(route.request().url());
    console.log('[capture-mock] report-sessions', url.toString(), 'rows=', REPORT_SESSIONS.length);
    return json(route, REPORT_SESSIONS);
  });

  await context.route(CAPTURE_ROUTE_PATTERNS.projectsList, async (route) => {
    const url = new URL(route.request().url());
    console.log('[capture-mock] projects', url.toString(), 'rows=1');
    return json(route, [DEMO_PROJECT]);
  });

  // The chambers workbench renders its running-chamber overview immediately
  // after the availability list. A progress response with only the envelope
  // (or a bare array fallback) leaves `snapshot.progress` undefined and the
  // old handbook capture crashed while reading `completed`. Keep the fixture
  // on the current central proxy wire shape so chambers captures are a real
  // ready state rather than an error-boundary screenshot.
  await context.route(CAPTURE_ROUTE_PATTERNS.chamberProgress, async (route) => {
    const url = new URL(route.request().url());
    const chamberId = url.pathname.split('/').at(-3) ?? 'chamber-b';
    return json(route, {
      chamber_id: chamberId,
      progress: {
        is_running: true,
        completed: 8,
        total: 12,
        ratio: 8 / 12,
      },
    });
  });

  await context.route('**/headless/projects/*/test-plan/publications', async (route) =>
    json(route, {
      publications: [
        {
          plan_id: 'published-plan-1',
          draft_id: 'draft-9',
          published_at: '2026-08-02T03:30:00Z',
        },
      ],
    }),
  );

  await context.route(`**/platform/providers/${DEMO_PROVIDER_ID}/ui-descriptor`, async (route) =>
    json(route, PROVIDER_DESCRIPTOR),
  );

  await context.route('**/report-automation/stats', async (route) => json(route, REPORT_STATS));
}
