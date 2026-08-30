import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';
import { tableView } from './helpers/responsive-table';

/**
 * Phase 2 §8.3 — Reports route (Artifact & Download Console) workflow smoke.
 *
 * Covers:
 *   1. queue stats rendered as a MetricStrip (5 metrics)
 *   2. output available vs missing — both badges rendered + the disabled
 *      download button surfaces the missing-cause copy
 *   3. signed download grant 409 (integrity conflict) → operator-facing copy
 *   4. signed download grant 410 (expired grant) → operator-facing copy
 *   5. raw filesystem path is NOT exposed in the rendered DOM (security seal —
 *      FE never composes storage_root or absolute paths)
 *   6. request cancel — arm → confirm → the EXACT contract URL leaves the app
 *
 * On (6) and what a mocked spec can and cannot prove
 * (dev-environment-contract-parity, 2026-08-01): these specs intercept at the
 * network layer, so they can never discover that a dev gateway fails to forward
 * a prefix — `page.route` answers before any proxy is consulted. That is exactly
 * how the `/report-automation/*` 404 passed CI while the cancel button was
 * structurally unable to succeed in dev.
 *
 * So the coverage is split, and neither half is sufficient alone:
 *   - THIS spec pins WHICH URL the app emits (`request.url()`, not "a button
 *     exists" — the earlier reports specs only asserted rendering).
 *   - `tests/test_apps_web_scaffold.py::TestDevStackProxyCoversEveryBackendPrefix`
 *     pins that every prefix a backend exposes is reachable through the dev
 *     gateway.
 * Together they close the loop: the app emits a URL, and that URL is routable.
 */

const STATS_GLOB = '**/report-automation/stats*';
const REQUEST_GLOB = '**/headless/reports/42';
const OUTPUTS_GLOB = '**/headless/reports/42/outputs*';
const DOWNLOAD_GLOB = '**/headless/reports/42/outputs/download';
const CANCEL_GLOB = '**/report-automation/requests/42/cancel';
/** The path `POST /report-automation/requests/{request_id}/cancel` resolves to
 *  for request 42 — asserted verbatim so a client-side path change is a test
 *  failure rather than a dev-only 404 nobody notices. */
const CANCEL_PATH = '/report-automation/requests/42/cancel';

async function mockStats(page: Page, body: Record<string, unknown>): Promise<void> {
  await page.route(STATS_GLOB, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
  );
}

async function open(page: Page): Promise<void> {
  await injectAuthenticatedSession(page);
  await page.goto('/reports');
  await expect(page.getByRole('heading', { name: '성적서 생성 / 산출물', level: 1 })).toBeVisible();
  await expect(page.getByTestId('reports-workbench-overview')).toBeVisible();
  await expect(page.getByTestId('reports-workbench')).toBeVisible();
}

test.describe('Reports route — Artifact & Download Console', () => {
  test('renders the queue MetricStrip with 5 metrics', async ({ page }) => {
    await mockStats(page, { queued: 3, running: 1, completed: 12, failed: 0, cancelled: 2 });
    await open(page);
    await expect(page.getByTestId('stat-queued')).toHaveText('3');
    await expect(page.getByTestId('stat-running')).toHaveText('1');
    await expect(page.getByTestId('stat-completed')).toHaveText('12');
    await expect(page.getByTestId('stat-failed')).toHaveText('0');
    await expect(page.getByTestId('stat-cancelled')).toHaveText('2');
  });

  test('renders both available and missing output badges + missing alert', async ({ page }) => {
    await mockStats(page, { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 });
    await page.route(REQUEST_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ request_id: 42, status: 'completed', session_id: 7 }),
      }),
    );
    await page.route(OUTPUTS_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { relative_path: 'a.docx', file_name: 'a.docx', byte_size: 1024, exists: true },
          { relative_path: 'b.pdf', file_name: 'b.pdf', byte_size: null, exists: false },
        ]),
      }),
    );
    await open(page);
    await page.getByTestId('request-id-input').fill('42');
    await page.getByTestId('request-lookup').click();
    await expect(tableView(page, 'outputs-table').getByTestId('output-available')).toBeVisible();
    await expect(tableView(page, 'outputs-table').getByTestId('output-missing')).toBeVisible();
    await expect(tableView(page, 'outputs-table').getByTestId('output-missing')).toContainText(
      /누락/u,
    );
  });

  test('surfaces the 409 integrity-conflict copy on download failure', async ({ page }) => {
    await mockStats(page, { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 });
    await page.route(REQUEST_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ request_id: 42, status: 'completed', session_id: 7 }),
      }),
    );
    await page.route(OUTPUTS_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { relative_path: 'a.docx', file_name: 'a.docx', byte_size: 1024, exists: true },
        ]),
      }),
    );
    await page.route(DOWNLOAD_GLOB, (route) =>
      route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: '{"detail":"conflict"}',
      }),
    );
    await open(page);
    await page.getByTestId('request-id-input').fill('42');
    await page.getByTestId('request-lookup').click();
    await tableView(page, 'outputs-table').getByTestId('output-download').click();
    await expect(page.getByTestId('download-error')).toContainText(/파일이 변경/u);
  });

  test('surfaces the 410 expired-grant copy on download failure', async ({ page }) => {
    await mockStats(page, { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 });
    await page.route(REQUEST_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ request_id: 42, status: 'completed', session_id: 7 }),
      }),
    );
    await page.route(OUTPUTS_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { relative_path: 'a.docx', file_name: 'a.docx', byte_size: 1024, exists: true },
        ]),
      }),
    );
    await page.route(DOWNLOAD_GLOB, (route) =>
      route.fulfill({ status: 410, contentType: 'application/json', body: '{"detail":"gone"}' }),
    );
    await open(page);
    await page.getByTestId('request-id-input').fill('42');
    await page.getByTestId('request-lookup').click();
    await tableView(page, 'outputs-table').getByTestId('output-download').click();
    await expect(page.getByTestId('download-error')).toContainText(/만료/u);
  });

  test('cancels a queued request and emits the contract cancel URL', async ({ page }) => {
    await mockStats(page, { queued: 1, running: 0, completed: 0, failed: 0, cancelled: 0 });
    await page.route(REQUEST_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ request_id: 42, status: 'queued', session_id: 7 }),
      }),
    );
    await page.route(OUTPUTS_GLOB, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
    );
    // Every cancel attempt is recorded at the interception point rather than
    // merely fulfilled: `route.request()` is the request the app emitted, read
    // before the mock answers it. So the assertions below are about traffic
    // that left the app — including the "not yet" assertion after arming,
    // which a rendering check could never make.
    const cancelCalls: { method: string; path: string }[] = [];
    await page.route(CANCEL_GLOB, async (route: Route) => {
      const request = route.request();
      cancelCalls.push({ method: request.method(), path: new URL(request.url()).pathname });
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ request_id: 42, status: 'cancelled' }),
      });
    });
    await open(page);
    await page.getByTestId('request-id-input').fill('42');
    await page.getByTestId('request-lookup').click();

    // A queued request is cancellable, so the control is offered at all.
    await expect(page.getByTestId('request-cancel')).toBeVisible();
    await page.getByTestId('request-cancel').click();
    // Arming alone must NOT fire the irreversible call — the confirm does.
    await expect(page.getByTestId('request-cancel-confirm')).toBeVisible();
    expect(cancelCalls).toHaveLength(0);

    await Promise.all([
      page.waitForRequest((r) => r.url().includes(CANCEL_PATH)),
      page.getByTestId('request-cancel-confirm-button').click(),
    ]);
    // Exactly one call, with the verbatim contract method + path. A client-side
    // path change is a test failure here rather than a dev-only 404.
    expect(cancelCalls).toEqual([{ method: 'POST', path: CANCEL_PATH }]);
    await expect(page.getByTestId('request-cancel-error')).toBeHidden();
  });

  test('does NOT expose a raw filesystem path (security — signed grant only)', async ({ page }) => {
    await mockStats(page, { queued: 0, running: 0, completed: 0, failed: 0, cancelled: 0 });
    await open(page);
    const dom = await page.content();
    // No absolute Unix path, no Windows drive path. `relative_path` strings
    // remain (e.g. "a.docx") — those are *not* raw filesystem paths.
    expect(dom).not.toMatch(/(?:href|src)=["']\/(?:home|var|opt|tmp|srv|root)\//u);
    expect(dom).not.toMatch(/[A-Za-z]:\\\\/u);
  });
});
