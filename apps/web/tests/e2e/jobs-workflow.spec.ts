import { expect, test, type Page } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';
import { tableView } from './helpers/responsive-table';

const STATUS_GLOB = '**/headless/status*';
const JOBS_GLOB = '**/headless/jobs';
// ⚠️ contract v0.1.22 — the stop route is addressed by the OPAQUE handle, not the
// storage primary key. A glob that still names `7` simply never matches, and an
// unmatched `page.route` is silent: the click goes to the real origin and the
// assertion times out on a missing element rather than on a wrong URL.
const STOP_GLOB = '**/headless/jobs/job-7/stop';

async function mockJobs(page: Page): Promise<void> {
  let stopRequested = false;
  await page.route(STATUS_GLOB, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        measurement_jobs: {
          counts: { queued: 1, running: 1, completed: 2, failed: 0, cancelled: 0 },
          recent: [],
        },
        workers: { active: 0, stale: 0, workers: [] },
        report_automation: { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 },
      }),
    }),
  );
  await page.route(JOBS_GLOB, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          job_uuid: 'job-7',
          status: 'running',
          excel_path: 'C:\\plans\\jobs.xlsx',
          requested_by: 'operator',
          assigned_worker_id: 'worker-1',
          status_message: '',
          stop_requested: stopRequested,
          payload: {},
          options: {},
          created_at: '2026-06-14T00:00:00Z',
          updated_at: '2026-06-14T00:00:00Z',
        },
      ]),
    }),
  );
  await page.route(STOP_GLOB, (route) => {
    stopRequested = true;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_uuid: 'job-7', stop_requested: true }),
    });
  });
}

test.describe('Jobs route — measurement queue console', () => {
  test('renders counts, job rows, and control-gated stop action', async ({ page }) => {
    await injectAuthenticatedSession(page);
    await mockJobs(page);

    await page.goto('/jobs');

    await expect(page.getByRole('heading', { name: '측정 작업', level: 1 })).toBeVisible();
    await expect(page.getByTestId('jobs-workbench-overview')).toBeVisible();
    await expect(page.getByTestId('jobs-workbench')).toBeVisible();
    await expect(page.getByTestId('jobs-next-chambers')).toHaveAttribute('href', '/chambers');
    await expect(page.getByTestId('job-count-running')).toHaveText('1');
    await expect(page.getByTestId('jobs-table')).toBeVisible();
    await expect(tableView(page, 'jobs-table').getByText('jobs.xlsx')).toBeVisible();
    await tableView(page, 'jobs-table').getByTestId('job-stop').click();
    await expect(tableView(page, 'jobs-table').getByTestId('job-stop-requested')).toBeVisible();
  });

  /**
   * W3 §M7.2 — the three projections of one table, proved in a real engine.
   *
   * jsdom has no layout, so the unit tests can only assert that all three
   * projections EXIST in the markup. Which one is visible at which width is a
   * cascade fact, and the cascade only exists in a browser. This is the test
   * that would fail if a future edit moved a rule into the wrong band.
   */
  test('folds detail columns, then swaps to cards, purely by media query', async ({ page }) => {
    await injectAuthenticatedSession(page);
    await mockJobs(page);
    await page.goto('/jobs');
    await expect(page.getByTestId('jobs-table')).toBeVisible();

    const table = page.getByTestId('jobs-table');
    const cards = page.getByTestId('data-table-cards');
    const overflowRow = page.getByTestId('data-table-overflow-row').first();
    const detailCell = table.locator("td[data-priority='detail']").first();

    // Desktop: full row, no fold, no cards.
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(table).toBeVisible();
    await expect(detailCell).toBeVisible();
    await expect(overflowRow).toBeHidden();
    await expect(cards).toBeHidden();

    // Compact: detail columns leave the row grid and reappear underneath.
    await page.setViewportSize({ width: 700, height: 900 });
    await expect(table).toBeVisible();
    await expect(detailCell).toBeHidden();
    await expect(overflowRow).toBeVisible();
    await expect(cards).toBeHidden();

    // Phone: the table gives way to the card list, which carries every column.
    await page.setViewportSize({ width: 390, height: 900 });
    await expect(table).toBeHidden();
    await expect(cards).toBeVisible();
    await expect(page.getByTestId('data-table-card').first()).toContainText('jobs.xlsx');
  });
});
