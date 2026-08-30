import { expect, test, type Page } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';

/**
 * Phase 2 §8.3 — Control route (Remote Measurement Console) workflow smoke.
 *
 * Covers:
 *   1. running progress is rendered via MetricStrip (3 metrics)
 *   2. emergency stop through the remaining control surface is unavailable
 *      when the node is unreachable → severity-coded outcome
 *      (`control-outcome-unreachable`)
 *   3. event stream status badge surfaces the connection state
 */

const PROGRESS_GLOB = '**/session/progress*';
const STOP_GLOB = '**/session/stop*';

async function open(page: Page): Promise<void> {
  await injectAuthenticatedSession(page);
  await page.goto('/control');
  await expect(page.getByRole('heading', { name: '원격 측정 제어', level: 1 })).toBeVisible();
  await expect(page.getByTestId('control-workbench-overview')).toBeVisible();
  await expect(page.getByTestId('control-workbench')).toBeVisible();
}

test.describe('Control route — Remote Measurement Console', () => {
  test('renders running progress as a MetricStrip', async ({ page }) => {
    await page.route(PROGRESS_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ is_running: true, completed: 7, total: 12, ratio: 0.583 }),
      }),
    );
    await open(page);
    await expect(page.getByTestId('progress-running')).toHaveText('예');
    await expect(page.getByTestId('progress-completed')).toHaveText('7 / 12');
    await expect(page.getByTestId('progress-ratio')).toHaveText('58%');
  });

  test('surfaces the unreachable severity when stop fails with no network', async ({ page }) => {
    await page.route(PROGRESS_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ is_running: true, completed: 0, total: 0, ratio: 0 }),
      }),
    );
    await page.route(STOP_GLOB, (route) => route.abort('failed'));
    await open(page);
    await page.getByTestId('control-stop').click();
    await expect(page.getByTestId('control-outcome-unreachable')).toBeVisible();
    await expect(page.getByTestId('control-outcome-unreachable')).toContainText(
      /연결할 수 없|오프라인|준비 안/u,
    );
  });

  test('surfaces the event stream connection status badge', async ({ page }) => {
    await page.route(PROGRESS_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ is_running: true, completed: 0, total: 1, ratio: 0 }),
      }),
    );
    await open(page);
    await expect(page.getByTestId('events-status')).toBeVisible();
    // The badge is rendered inside the events-status paragraph — Phase 1
    // StatusBadge surfaces a `data-status` attribute carrying the kind.
    await expect(page.getByTestId('events-status-badge')).toBeVisible();
  });
});
