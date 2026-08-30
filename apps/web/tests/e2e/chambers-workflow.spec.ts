import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession, TEST_OPERATOR_PERMISSIONS } from './helpers/auth-fixture';
import { ACTIVE_SAMPLE_ID, mockActiveSampleInventory } from './helpers/sample-inventory-fixture';

/**
 * 멀티챔버 P6 §chambers — 시험 챔버 가용성 + 분산 측정 시작/진행 workflow smoke.
 * Platform API 를 네트워크 계층에서 mock 한다(라이브 백엔드 없음), membership/projects
 * workflow spec 미러.
 *
 * Covers:
 *   1. operator(platform:read) → 가용성 테이블 + status 배지 렌더
 *   2. operator(platform:claim) → idle 챔버만 시작 옵션 + 측정 시작 → 성공 + 진행 폴링
 */

const CHAMBERS_GLOB = '**/platform/chambers';
const PROJECTS_GLOB = '**/platform/projects';
const PROJECTS_GLOB_Q = '**/platform/projects?*';
const PROJECT_ID = '22222222-2222-4222-8222-222222222222';
const START_GLOB = '**/platform/chambers/cham-idle/measurements';
const PROGRESS_GLOB = '**/platform/chambers/cham-idle/measurements/progress';

function chamber(over: Record<string, unknown>): Record<string, unknown> {
  return {
    chamber_id: 'cham-idle',
    name: 'Alpha',
    base_url: 'http://node-1:8000',
    enabled: true,
    status: 'idle',
    heartbeat_ttl_seconds: 30,
    last_heartbeat_at: '2026-06-16T00:00:00+00:00',
    reported_status: 'idle',
    session_id: null,
    ...over,
  };
}

async function mockChambers(page: Page): Promise<void> {
  // GET /platform/chambers/{id}/measurements/progress is more specific — register
  // it first so it wins over the start route's prefix.
  await page.route(PROGRESS_GLOB, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        chamber_id: 'cham-idle',
        progress: { is_running: false, completed: 5, total: 5, ratio: 1 },
      }),
    });
  });
  await page.route(START_GLOB, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        chamber_id: 'cham-idle',
        progress: { is_running: true, completed: 0, total: 5, ratio: 0 },
      }),
    });
  });
  await page.route(CHAMBERS_GLOB, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [chamber({}), chamber({ chamber_id: 'cham-busy', name: 'Beta', status: 'in_use' })],
        server_time: '2026-06-16T00:00:00+00:00',
      }),
    });
  });
}

// Chamber start requires a project **and** an active sample since `9bc09370`.
// The availability tests do not need either, so the setup is a separate helper
// rather than folded into `mockChambers` — a fixture that supplies more than the
// test under it needs makes it impossible to see which precondition a failure is
// actually about.
async function mockStartPreconditions(page: Page): Promise<void> {
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
          sample_count: 1,
        },
      ]),
    });
  };
  await page.route(PROJECTS_GLOB, handleProjects);
  await page.route(PROJECTS_GLOB_Q, handleProjects);
  await mockActiveSampleInventory(page, PROJECT_ID);
}

async function open(page: Page): Promise<void> {
  await injectAuthenticatedSession(page, { permissions: TEST_OPERATOR_PERMISSIONS });
  await page.goto('/chambers');
  await expect(page.getByRole('heading', { name: '시험 챔버', level: 1 })).toBeVisible();
  await expect(page.getByTestId('chambers-workbench-overview')).toBeVisible();
  await expect(page.getByTestId('chambers-workbench')).toBeVisible();
  await expect(page.getByTestId('chambers-next-sessions')).toHaveAttribute('href', '/sessions');
}

test.describe('Chambers route — availability + distributed measurement', () => {
  test('renders chamber availability with status badges', async ({ page }) => {
    await mockChambers(page);
    await open(page);
    const table = page.getByTestId('chambers-table');
    await expect(table).toBeVisible();
    await expect(table.getByText('Alpha')).toBeVisible();
    await expect(table.getByText('Beta')).toBeVisible();
    await expect(page.getByTestId('chambers-status').first()).toBeVisible();
  });

  test('starts a measurement on an idle chamber and polls progress', async ({ page }) => {
    await mockChambers(page);
    await mockStartPreconditions(page);
    await open(page);
    await expect(page.getByTestId('chambers-start-form')).toBeVisible();
    await page.getByTestId('chambers-start-project-select').selectOption(PROJECT_ID);
    // The sample select only offers `status: 'active'` rows, so choosing the
    // active id also proves the retired row in the fixture was filtered out.
    await page.getByTestId('chambers-start-sample').selectOption(ACTIVE_SAMPLE_ID);
    await page.getByTestId('chambers-start-select').selectOption('cham-idle');
    await expect(page.getByTestId('chambers-start-submit')).toBeEnabled();
    await page.getByTestId('chambers-start-submit').click();
    await expect(page.getByTestId('chambers-start-success')).toBeVisible();
    await expect(page.getByTestId('chambers-progress')).toBeVisible();
  });
});
