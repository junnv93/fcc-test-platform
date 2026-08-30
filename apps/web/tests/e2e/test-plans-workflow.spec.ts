import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession, TEST_OPERATOR_PERMISSIONS } from './helpers/auth-fixture';
import { messageAt } from './helpers/locale-messages';
import { ACTIVE_SAMPLE_ID, mockActiveSampleInventory } from './helpers/sample-inventory-fixture';

/**
 * 멀티챔버 P6 §test-plans — 테스트 플랜(test-plan draft/publish) workflow smoke.
 * Headless API 를 네트워크 계층에서 mock 한다(라이브 백엔드 없음).
 *
 * Covers:
 *   1. test_plan:read → 프로젝트 조회 → draft 목록 렌더
 *   2. test_plan:read → draft 선택 → 상세 GET 소비 → 행/메타 렌더
 *   3. test_plan:author → DRAFT 발행 → 성공 + plan_id 노출
 */

const PROJECT_ID = '22222222-2222-4222-8222-222222222222';
const PROJECTS_GLOB = '**/platform/projects';
// ProjectSelectField sources ACTIVE projects → `/platform/projects?status=active`
// (project-status-visibility). The bare glob does not match a query string, so the
// status-filtered request also needs the `?*` variant (mirrors DRAFTS_GLOB_Q).
const PROJECTS_GLOB_Q = '**/platform/projects?*';
const DRAFTS_GLOB = `**/headless/projects/${PROJECT_ID}/test-plan/drafts`;
const DRAFTS_GLOB_Q = `**/headless/projects/${PROJECT_ID}/test-plan/drafts?*`;
const DETAIL_GLOB = `**/headless/projects/${PROJECT_ID}/test-plan/drafts/draft-9`;
const PUBLISH_GLOB = `**/headless/projects/${PROJECT_ID}/test-plan/drafts/draft-9/publish`;
const EXPORT_GLOB = `**/headless/projects/${PROJECT_ID}/test-plan/drafts/draft-9/export`;
// Pathnames the create/edit flow must emit (dev-environment-contract-parity,
// 2026-08-01). Asserted verbatim: a spec that only checks "the button exists"
// cannot notice a client-side path change, and a wrong path fails silently as a
// 404 in whichever environment does not proxy it.
// The import control's label and its status text are the same string in the
// component, so one resolution serves every assertion about either.
const IMPORT_LABEL = messageAt('routes.testPlans.sectionImport');
const DRAFTS_PATH = `/headless/projects/${PROJECT_ID}/test-plan/drafts`;
const ADD_ROW_PATH = `${DRAFTS_PATH}/draft-9/rows`;

// test_plan:read + test_plan:author are not in the base operator set — add them
// for this authoring workflow (derived, no hardcoded re-list of the base set).
const AUTHOR_PERMISSIONS = [
  ...TEST_OPERATOR_PERMISSIONS,
  'test_plan:read',
  'test_plan:author',
] as const;

async function mockProjects(page: Page): Promise<void> {
  const handleProjects = async (route: Route): Promise<void> => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          project_id: PROJECT_ID,
          project_code: 'SM-TEST',
          model_name: 'SM-TEST',
          customer: null,
          manufacturer: null,
          management_number: 'M-001',
          status: 'active',
          sample_count: 0,
        },
      ]),
    });
  };
  // Register both the bare and the status-filtered (`?status=active`) URL so the
  // ProjectSelectField active-projects query is intercepted in every view.
  await page.route(PROJECTS_GLOB, handleProjects);
  await page.route(PROJECTS_GLOB_Q, handleProjects);
}

async function mockDrafts(page: Page): Promise<void> {
  await mockProjects(page);
  // Route registration order matters: Playwright tries the LAST-registered
  // handler FIRST. The list glob `**/.../drafts?*` also matches the more
  // specific detail (`.../drafts/draft-9`) and publish (`.../drafts/draft-9/
  // publish`) URLs (`?` matches the `/`), so the specific handlers MUST be
  // registered after the list handlers to take precedence.
  const handleDrafts = async (route: Route): Promise<void> => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        drafts: [
          {
            draft_id: 'draft-9',
            project_id: PROJECT_ID,
            status: 'draft',
            row_count: 12,
            created_at: '2026-06-16T00:00:00+00:00',
            updated_at: '2026-06-16T01:00:00+00:00',
          },
        ],
        next_cursor: null,
      }),
    });
  };
  await page.route(DRAFTS_GLOB, handleDrafts);
  await page.route(DRAFTS_GLOB_Q, handleDrafts);
  await page.route(DETAIL_GLOB, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        draft_id: 'draft-9',
        project_id: PROJECT_ID,
        status: 'draft',
        created_at: '2026-06-16T00:00:00+00:00',
        created_by: 'author@corp',
        scope_revision: 3,
        generation_metadata_json: null,
        rows: [
          {
            draft_row_id: 7,
            capability_path: ['BLE', 'DTM'],
            origin: 'generated',
            antenna: 'ANT1',
            location: 'CH0',
            mode_family: '1M',
            test_type: 'OBW',
            tone: null,
            derived_kind: null,
            generation_key: null,
            scope_revision: 1,
          },
        ],
      }),
    });
  });
  await page.route(PUBLISH_GLOB, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        plan_id: 'plan-xyz',
        draft_id: 'draft-9',
        status: 'published',
        rows: [],
      }),
    });
  });
  await page.route(EXPORT_GLOB, async (route: Route) => {
    // Mock the binary .xlsx download (octet-stream attachment). The button's
    // Blob download path only needs a non-empty body + Content-Disposition.
    await route.fulfill({
      status: 200,
      contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      headers: { 'content-disposition': 'attachment; filename="test-plan-draft-9.xlsx"' },
      body: Buffer.from('PK\x03\x04 fake workbook bytes'),
    });
  });
}

async function open(page: Page): Promise<void> {
  await injectAuthenticatedSession(page, { permissions: AUTHOR_PERMISSIONS });
  await page.goto(`/test-plans?project=${PROJECT_ID}`);
  await expect(
    page.getByRole('heading', { name: messageAt('routes.testPlans.pageTitle'), level: 1 }),
  ).toBeVisible();
  await expect(page.getByTestId('test-plans-workbench-overview')).toBeVisible();
  await expect(page.getByTestId('test-plans-next-state')).toContainText(PROJECT_ID);
  await expect(page.getByTestId('test-plans-next-inventory')).toHaveAttribute(
    'href',
    `/inventory?project=${PROJECT_ID}`,
  );
}

test.describe('Test plans route — draft list + publish', () => {
  test('lists drafts for a valid project', async ({ page }) => {
    await mockDrafts(page);
    await open(page);
    await expect(page.getByTestId('test-plans-table')).toBeVisible();
    await expect(page.getByText('draft-9')).toBeVisible();
  });

  test('opens a draft detail and renders its rows', async ({ page }) => {
    await mockDrafts(page);
    await open(page);
    await expect(page.getByTestId('test-plans-detail-button')).toBeVisible();
    await page.getByTestId('test-plans-detail-button').click();
    const detailTable = page.getByTestId('test-plans-detail-table');
    await expect(detailTable).toBeVisible();
    await expect(detailTable.getByRole('cell', { name: 'BLE / DTM' })).toBeVisible();
    await expect(detailTable.getByRole('cell', { name: 'OBW' })).toBeVisible();
  });

  test('exports the open draft as an Excel download', async ({ page }) => {
    await mockDrafts(page);
    await open(page);
    await page.getByTestId('test-plans-detail-button').click();
    const exportButton = page.getByTestId('test-plans-export');
    await expect(exportButton).toBeVisible();
    // Clicking fires the gated GET .../export request and triggers a download.
    const [request, download] = await Promise.all([
      page.waitForRequest(`**/test-plan/drafts/draft-9/export`),
      page.waitForEvent('download'),
      exportButton.click(),
    ]);
    expect(request.method()).toBe('GET');
    expect(download.suggestedFilename()).toBe('test-plan-draft-9.xlsx');
  });

  test('publishes a DRAFT and surfaces the published plan id', async ({ page }) => {
    await mockDrafts(page);
    await open(page);
    await page.getByTestId('test-plans-detail-button').click();
    await expect(page.getByTestId('test-plans-publish')).toBeVisible();
    await page.getByTestId('test-plans-publish').click();
    const success = page.getByTestId('test-plans-publish-success');
    await expect(success).toBeVisible();
    await expect(success).toContainText('plan-xyz');
  });
});

/**
 * Track B.2 — Test Plan Lifecycle E2E.
 *
 * The per-stage controls (create / add-row / validate / publish) and the
 * published-plan → measurement handoff each have unit/route coverage, but no
 * single spec seals the *continuous* happy path the operator actually walks:
 *
 *   draft 생성 → 행 편집 → 검증 → 발행(published_plan_id) → 측정 시작 → 진행/결과 추적
 *
 * These two tests close that gap end-to-end against the real router + typed
 * generated client, with the headless/platform APIs mocked at the network layer.
 */

const PUBLISHED_PLAN_ID = 'plan-xyz';
const PUBLICATIONS_GLOB = `**/headless/projects/${PROJECT_ID}/test-plan/publications`;
const ROWS_GLOB = `**/headless/projects/${PROJECT_ID}/test-plan/drafts/draft-9/rows`;
const VALIDATE_GLOB = `**/headless/projects/${PROJECT_ID}/test-plan/drafts/draft-9/validate`;
const IMPORTS_GLOB = `**/headless/projects/${PROJECT_ID}/test-plan/imports`;

/**
 * Stateful authoring mocks. A closure-held `rows` array lets the drafts list
 * row_count and the detail rows grow as the operator adds a row — so the publish
 * control (gated on `row_count > 0`) genuinely unlocks only after editing, the
 * same causal chain the production UI enforces.
 */
async function mockAuthoringLifecycle(page: Page): Promise<void> {
  const rows: Record<string, unknown>[] = [];

  // Publications read fired by the open detail's draft-vs-published diff panel.
  // Empty until publish; the handoff test owns the post-publish list.
  await page.route(PUBLICATIONS_GLOB, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ publications: [], next_cursor: null }),
    });
  });

  // List GET + create POST share the `.../drafts` path → branch on method. The
  // `?*` variant also matches the more specific draft-9 sub-paths, so the
  // specific handlers below are registered AFTER to take precedence.
  const handleDrafts = async (route: Route): Promise<void> => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          draft_id: 'draft-9',
          project_id: PROJECT_ID,
          status: 'draft',
          created_at: '2026-06-24T00:00:00+00:00',
          created_by: 'author@corp',
          scope_revision: 1,
          rows: [],
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        drafts: [
          {
            draft_id: 'draft-9',
            project_id: PROJECT_ID,
            status: 'draft',
            row_count: rows.length,
            created_at: '2026-06-24T00:00:00+00:00',
            updated_at: '2026-06-24T01:00:00+00:00',
          },
        ],
        next_cursor: null,
      }),
    });
  };
  await page.route(DRAFTS_GLOB, handleDrafts);
  await page.route(DRAFTS_GLOB_Q, handleDrafts);

  await page.route(DETAIL_GLOB, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        draft_id: 'draft-9',
        project_id: PROJECT_ID,
        status: 'draft',
        created_at: '2026-06-24T00:00:00+00:00',
        created_by: 'author@corp',
        scope_revision: 1,
        generation_metadata_json: null,
        rows,
      }),
    });
  });
  await page.route(ROWS_GLOB, async (route: Route) => {
    rows.push({
      draft_row_id: rows.length + 1,
      capability_path: ['BLE', 'DTM'],
      origin: 'manual',
      antenna: 'ANT1',
      location: 'CH0',
      mode_family: '1M',
      test_type: 'OBW',
      tone: null,
      derived_kind: null,
      generation_key: null,
      scope_revision: 1,
    });
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ draft_row_id: rows.length, status: 'draft' }),
    });
  });
  await page.route(VALIDATE_GLOB, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ error_count: 0, warning_count: 0, issues: [] }),
    });
  });
  await page.route(PUBLISH_GLOB, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        plan_id: PUBLISHED_PLAN_ID,
        draft_id: 'draft-9',
        status: 'published',
        published_at: '2026-06-24T02:00:00+00:00',
        rows: [],
      }),
    });
  });
  // Export the in-progress draft as an .xlsx download — a read-only side step
  // the operator can take mid-authoring (before publish). Same octet-stream
  // attachment contract the standalone export test exercises, folded into the
  // lifecycle so create→edit→export→validate→publish share one router/client.
  await page.route(EXPORT_GLOB, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      headers: { 'content-disposition': 'attachment; filename="test-plan-draft-9.xlsx"' },
      body: Buffer.from('PK\x03\x04 fake workbook bytes'),
    });
  });
}

/** Chamber availability + publications datalist + measurement start/progress
 *  mocks for the published-plan → measurement handoff (the chambers route). */
async function mockMeasurementHandoff(page: Page): Promise<void> {
  await mockProjects(page);
  await page.route(`**/platform/chambers/cham-idle/measurements/progress`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        chamber_id: 'cham-idle',
        progress: { is_running: false, completed: 5, total: 5, ratio: 1 },
      }),
    });
  });
  await page.route(`**/platform/chambers/cham-idle/measurements`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        chamber_id: 'cham-idle',
        progress: { is_running: true, completed: 0, total: 5, ratio: 0 },
      }),
    });
  });
  await page.route(`**/platform/chambers`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [
          {
            chamber_id: 'cham-idle',
            name: 'Alpha',
            base_url: 'http://node-1:8000',
            enabled: true,
            status: 'idle',
            heartbeat_ttl_seconds: 30,
            last_heartbeat_at: '2026-06-24T00:00:00+00:00',
            reported_status: 'idle',
            session_id: null,
          },
        ],
        server_time: '2026-06-24T00:00:00+00:00',
      }),
    });
  });
  // The freshly published plan surfaces in the chamber starter's datalist
  // (G2 server SSOT — the same publications read the publish mutation refreshes).
  await page.route(PUBLICATIONS_GLOB, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        publications: [
          {
            plan_id: PUBLISHED_PLAN_ID,
            draft_id: 'draft-9',
            project_id: PROJECT_ID,
            published_at: '2026-06-24T02:00:00+00:00',
            row_count: 1,
          },
        ],
        next_cursor: null,
      }),
    });
  });
}

/**
 * Import-path mocks. The author-gated import section (`ImportExcelForm`) uploads
 * an Excel workbook to the multipart POST `.../test-plan/imports` route; the
 * platform records the outcome (ADR-0010 — provider owns the parse) and returns
 * an honest audit + blocking issues + observable exclusions. The drafts list
 * read keeps the create-bar section (which hosts the import form) mounted.
 */
async function mockImportLifecycle(page: Page): Promise<void> {
  const handleDrafts = async (route: Route): Promise<void> => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ drafts: [], next_cursor: null }),
    });
  };
  await page.route(DRAFTS_GLOB, handleDrafts);
  await page.route(DRAFTS_GLOB_Q, handleDrafts);
  await page.route(IMPORTS_GLOB, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        import_id: 'imp-1',
        draft_id: 'draft-imported-1',
        audit: {
          raw_row_count: 10,
          accepted_count: 8,
          issue_count: 1,
          excluded_count: 1,
          legend_skipped_count: 0,
          parser_version: '1.0.0',
          sheet_name: 'Test Plan',
          workbook_filename: 'plan.xlsx',
          workbook_sha256: 'deadbeef',
          by_technology: [{ technology: 'BLE', accepted: 8, excluded: 1, issues: 1 }],
        },
        issues: [
          { row_number: 3, field: 'channel', message: 'unknown channel', severity: 'error' },
        ],
        excluded: [{ row_number: 5, reason: 'legend-row', detail: 'header legend skipped' }],
      }),
    });
  });
}

test.describe('Test plan lifecycle E2E (Track B.2)', () => {
  test('import path: upload an Excel workbook → multipart route → audit renders', async ({
    page,
  }) => {
    await mockImportLifecycle(page);
    await open(page);

    // The author-gated import section is mounted with the create bar.
    const importForm = page.getByTestId('test-plans-import-form');
    await expect(importForm).toBeVisible();

    // Pick a workbook through the real <input type=file> — the production UI's
    // only entry point. Playwright's setInputFiles drives the change handler.
    const fileInput = page.getByTestId('test-plans-import-file');
    const fileStatus = page.getByTestId('test-plans-import-file-status');
    const filePicker = page.getByTestId('test-plans-import-file-picker');
    await expect(fileInput).toHaveClass('sr-only');
    await expect(filePicker).toBeVisible();
    // ⚠️ The expected text is resolved from the same key the component renders
    // (`ImportExcelForm.tsx` → `t('routes.testPlans.sectionImport')`), not typed
    // out. The proposition under test is *the accessible name equals the visible
    // label*, and that is what survives a copy change; which exact words the
    // label carries is judged by the locale gates, which read the bundle itself.
    // Round 3 renamed one word here and turned these four assertions red — the
    // coupling was real, and this is the derivation that removes it without
    // weakening what the spec actually proves.
    await expect(fileInput).toHaveAccessibleName(IMPORT_LABEL);
    await expect(filePicker).toHaveAccessibleName(IMPORT_LABEL);
    await expect(fileInput).toHaveAttribute('aria-describedby', 'test-plans-import-file-help');
    await expect(fileStatus).toHaveText(IMPORT_LABEL);
    await expect(filePicker).toHaveAttribute('data-file-state', 'empty');
    const workbookBuffer = Buffer.from('PK\x03\x04 fake workbook bytes');
    await fileInput.setInputFiles({
      name: 'plan.xlsx',
      mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      buffer: workbookBuffer,
    });
    await expect(fileStatus).toHaveText(`${IMPORT_LABEL}: plan.xlsx (${workbookBuffer.length} B)`);
    await expect(filePicker).toHaveAttribute('data-file-state', 'selected');
    await expect(fileStatus).not.toContainText('Choose File');

    // Submitting fires the multipart/form-data POST to the imports route. Assert
    // the request actually rides the multipart contract (file input → FormData
    // bodySerializer → multipart POST), not a JSON body.
    const [importRequest] = await Promise.all([
      page.waitForRequest(
        (req) => req.url().includes('/test-plan/imports') && req.method() === 'POST',
      ),
      page.getByTestId('test-plans-import-submit').click(),
    ]);
    expect(importRequest.headers()['content-type'] ?? '').toContain('multipart/form-data');

    // The import result surfaces: success status (a draft was created) + the
    // honest audit accounting + the blocking issue and the excluded row, so the
    // operator sees exactly what was / wasn't imported (no proxy completion).
    await expect(page.getByTestId('test-plans-import-result')).toBeVisible();
    await expect(page.getByTestId('test-plans-import-status')).toContainText('draft-imported-1');
    const audit = page.getByTestId('test-plans-import-audit');
    await expect(audit).toContainText('10');
    await expect(audit).toContainText('8');
    await expect(page.getByTestId('test-plans-import-issues')).toBeVisible();
    await expect(page.getByTestId('test-plans-import-excluded')).toBeVisible();
  });

  test('authoring happy path: create → add row → validate → publish', async ({ page }) => {
    await mockAuthoringLifecycle(page);
    await open(page);

    // Stage 1 — create a fresh DRAFT; it auto-opens the detail panel.
    // The emitted URL is pinned, not just the resulting render
    // (dev-environment-contract-parity, 2026-08-01): a client-side path change
    // is invisible to a render-only assertion and shows up later as a 404 in
    // whichever environment does not proxy the path it moved to.
    const [createRequest] = await Promise.all([
      page.waitForRequest(
        (r) => r.method() === 'POST' && new URL(r.url()).pathname === DRAFTS_PATH,
      ),
      page.getByTestId('test-plans-create').click(),
    ]);
    expect(createRequest.method()).toBe('POST');
    await expect(page.getByTestId('test-plans-detail')).toBeVisible();
    // Newly created draft has no rows → publish stays disabled (422 guard).
    await expect(page.getByTestId('test-plans-publish')).toBeDisabled();

    // Stage 2 — add a manual test-item row; detail + list refetch and grow.
    await page.getByTestId('test-plans-add-row-path').fill('BLE/DTM');
    const [addRowRequest] = await Promise.all([
      page.waitForRequest(
        (r) => r.method() === 'POST' && new URL(r.url()).pathname === ADD_ROW_PATH,
      ),
      page.getByTestId('test-plans-add-row-submit').click(),
    ]);
    expect(addRowRequest.postDataJSON()).toMatchObject({ capability_path: ['BLE', 'DTM'] });
    await expect(
      page.getByTestId('test-plans-detail-table').getByRole('cell', { name: 'BLE / DTM' }),
    ).toBeVisible();

    // Stage 2b — export the work-in-progress draft (read-only side step). The
    // gated GET .../export fires and the browser receives the .xlsx download;
    // this must not disturb the create/edit/validate/publish chain below.
    const exportButton = page.getByTestId('test-plans-export');
    await expect(exportButton).toBeVisible();
    const [exportRequest, download] = await Promise.all([
      page.waitForRequest(`**/test-plan/drafts/draft-9/export`),
      page.waitForEvent('download'),
      exportButton.click(),
    ]);
    expect(exportRequest.method()).toBe('GET');
    expect(download.suggestedFilename()).toBe('test-plan-draft-9.xlsx');

    // Stage 3 — validate the draft; a clean recompute clears the publish gate.
    await page.getByTestId('test-plans-validate').click();
    await expect(page.getByTestId('test-plans-validate-clean')).toBeVisible();

    // Stage 4 — publish; the published plan_id surfaces for the measurement step.
    await expect(page.getByTestId('test-plans-publish')).toBeEnabled();
    await page.getByTestId('test-plans-publish').click();
    const success = page.getByTestId('test-plans-publish-success');
    await expect(success).toBeVisible();
    await expect(success).toContainText(PUBLISHED_PLAN_ID);
  });

  test('published_plan_id handoff: starts a distributed measurement and tracks progress', async ({
    page,
  }) => {
    await mockMeasurementHandoff(page);
    await mockActiveSampleInventory(page, PROJECT_ID);
    await injectAuthenticatedSession(page, { permissions: AUTHOR_PERMISSIONS });
    await page.goto('/chambers');
    await expect(
      page.getByRole('heading', { name: messageAt('routes.chambers.pageTitle'), level: 1 }),
    ).toBeVisible();

    // The published plan is offered as a datalist suggestion for the project.
    await page.getByTestId('chambers-start-project-select').selectOption(PROJECT_ID);
    await expect(
      page
        .getByTestId('chambers-start-plan-options')
        .locator(`option[value="${PUBLISHED_PLAN_ID}"]`),
    ).toBeAttached();

    // Start the measurement with the published_plan_id and assert the request
    // body carries it (the literal "발행 → published_plan_id로 측정 시작" seal).
    await page.getByTestId('chambers-start-plan').fill(PUBLISHED_PLAN_ID);
    // Chamber start requires an active sample since `9bc09370`; the same shared
    // fixture the chambers spec uses supplies it, so one production precondition
    // has one mock shape.
    await page.getByTestId('chambers-start-sample').selectOption(ACTIVE_SAMPLE_ID);
    await page.getByTestId('chambers-start-select').selectOption('cham-idle');
    await expect(page.getByTestId('chambers-start-submit')).toBeEnabled();
    const [startRequest] = await Promise.all([
      page.waitForRequest(
        (req) =>
          req.url().includes('/platform/chambers/cham-idle/measurements') &&
          req.method() === 'POST',
      ),
      page.getByTestId('chambers-start-submit').click(),
    ]);
    expect(startRequest.postData() ?? '').toContain(PUBLISHED_PLAN_ID);

    // Stage 6 — the live progress surface tracks the session to done. Assert the
    // actual metric values (not just panel visibility): the /progress GET mock
    // reports 5/5 complete (ratio 1, not running), so the progress panel must
    // render running=아니오, completed "5 / 5", ratio "100%" — proving the
    // snapshot is wired through, not merely that the section mounted.
    await expect(page.getByTestId('chambers-start-success')).toBeVisible();
    await expect(page.getByTestId('chambers-progress')).toBeVisible();
    await expect(page.getByTestId('chambers-progress-completed')).toHaveText('5 / 5');
    await expect(page.getByTestId('chambers-progress-ratio')).toHaveText('100%');
    await expect(page.getByTestId('chambers-progress-running')).toHaveText(
      messageAt('routes.chambers.metricRunningNo'),
    );
  });
});
