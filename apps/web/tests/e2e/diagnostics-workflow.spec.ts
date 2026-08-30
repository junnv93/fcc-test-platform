import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';

const SESSION_INFO_PATH = '**/session/info';

async function mockSessionInfo(page: Page, status = 200): Promise<void> {
  await page.route(SESSION_INFO_PATH, async (route: Route) => {
    if (status !== 200) {
      await route.fulfill({
        status,
        contentType: 'application/problem+json',
        body: JSON.stringify({
          type: 'about:blank',
          title: 'Service Unavailable',
          status,
          detail: 'session runner is unavailable',
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        api_version: '2',
        operations: ['start_session', 'stop_session', 'get_session'],
      }),
    });
  });
}

test('diagnostics shows runtime identity and the session operation contract', async ({ page }) => {
  await mockSessionInfo(page);
  await injectAuthenticatedSession(page);
  await page.goto('/diagnostics');

  await expect(page.getByRole('heading', { name: '진단', level: 1 })).toBeVisible();
  await expect(page.getByTestId('diagnostics-workbench')).toBeVisible();
  await expect(page.getByTestId('env-name')).toBeVisible();
  await expect(page.getByTestId('build-version')).toBeVisible();
  await expect(page.getByTestId('api-base-url')).toBeVisible();
  await expect(page.getByTestId('session-api-version')).toHaveText('2');
  await expect(page.getByTestId('session-operations-count')).toHaveText('3');
  await expect(page.getByTestId('session-operations')).toContainText('start_session');
  await expect(page.getByTestId('session-operations')).toContainText('get_session');
  await expect(page.getByTestId('diagnostics-next-actions')).toBeVisible();
  await expect(page.getByTestId('diagnostics-next-jobs')).toHaveAttribute('href', '/jobs');
});

test('diagnostics surfaces an unavailable session API instead of fabricating status', async ({
  page,
}) => {
  await mockSessionInfo(page, 503);
  await injectAuthenticatedSession(page);
  await page.goto('/diagnostics');

  await expect(page.getByRole('heading', { name: '진단', level: 1 })).toBeVisible();
  await expect(page.getByTestId('session-error')).toBeVisible();
  await expect(page.getByTestId('session-error')).toContainText('503');
  await expect(page.getByTestId('session-api-version')).not.toBeVisible();
});
