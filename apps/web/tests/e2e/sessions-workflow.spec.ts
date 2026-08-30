import { expect, test, type Page } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';

/**
 * Phase 2 §8.3 — Sessions route (Result History Browser) workflow smoke.
 *
 * Covers:
 *   1. session id validation (invalid → role=alert + no lookup fire)
 *   2. row_order + condition_hash grouping (compound key keeps distinct rows
 *      separate even when the hash collides)
 *   3. keyset pagination "더 있음 (전체 미표시)" + "더 보기" textual cues
 *   4. partial-data warning vs the full-page baseline
 */

const ATTEMPTS_GLOB = '**/headless/sessions/3/attempts*';

function attempt(over: Record<string, unknown>): Record<string, unknown> {
  return {
    provider_id: 'p',
    session_id: '3',
    attempt_id: String(Math.random()),
    condition_hash: 'h1',
    sheet_name: 'Test Plan',
    row_order: 1,
    technology: 'BLE',
    attempt_number: 1,
    result: { result1: '10 dBm', result2: '', result_sum: '', margin: '1', dccf: '' },
    verdict: 'Pass',
    status: 'completed',
    recorded_by: 'alice',
    measured_at: '2026-05-30T00:00:00',
    ...over,
  };
}

async function open(page: Page, query = ''): Promise<void> {
  await injectAuthenticatedSession(page);
  await page.goto(`/sessions${query}`);
  await expect(page.getByRole('heading', { name: '측정 이력', level: 1 })).toBeVisible();
  await expect(page.getByTestId('sessions-workbench-overview')).toBeVisible();
  await expect(page.getByTestId('sessions-workbench')).toBeVisible();
}

test.describe('Sessions route — Result History Browser', () => {
  test('rejects a non-numeric session id with a role=alert (no API call)', async ({ page }) => {
    let called = false;
    await page.route(ATTEMPTS_GLOB, async (route) => {
      called = true;
      await route.fulfill({ status: 200, body: '{}' });
    });
    await open(page);
    await page.getByTestId('session-input').fill('abc');
    await expect(page.getByTestId('session-invalid')).toBeVisible();
    expect(called).toBe(false);
  });

  test('renders one condition group per (condition_hash, row_order) compound key', async ({
    page,
  }) => {
    await page.route(ATTEMPTS_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            attempt({ condition_hash: 'h1', row_order: 1, attempt_number: 1 }),
            attempt({ condition_hash: 'h1', row_order: 5, attempt_number: 1 }),
            attempt({ condition_hash: 'h1', row_order: 5, attempt_number: 2 }),
          ],
          next_cursor: null,
        }),
      }),
    );
    await open(page, '?session=3');
    await expect(page.getByTestId('sessions-next-state')).toContainText('3');
    await expect(page.getByTestId('sessions-next-reports')).toHaveAttribute('href', '/reports');
    await expect(page.getByTestId('attempts-summary')).toContainText(/조건 2개/u);
    await expect(page.getByTestId('condition-group')).toHaveCount(2);
    await expect(page.getByTestId('attempt-row')).toHaveCount(3);
  });

  test('surfaces "더 있음 (전체 미표시)" + "더 보기" when next_cursor is present', async ({
    page,
  }) => {
    await page.route(ATTEMPTS_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [attempt({ condition_hash: 'h1' })],
          next_cursor: 'CUR1',
        }),
      }),
    );
    await open(page, '?session=3');
    await expect(page.getByTestId('attempts-summary')).toContainText(/더 있음 \(전체 미표시\)/u);
    await expect(page.getByTestId('attempts-load-more')).toBeVisible();
    await expect(page.getByTestId('attempts-load-more')).toContainText(/더 보기/u);
  });

  test('does NOT surface the partial-data warning when next_cursor is null', async ({ page }) => {
    await page.route(ATTEMPTS_GLOB, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [attempt({ condition_hash: 'h1' })],
          next_cursor: null,
        }),
      }),
    );
    await open(page, '?session=3');
    await expect(page.getByTestId('attempts-summary')).not.toContainText(/더 있음/u);
    await expect(page.getByTestId('attempts-load-more')).toHaveCount(0);
  });
});
