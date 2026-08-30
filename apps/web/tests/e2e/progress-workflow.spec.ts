import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';
import { VISUAL_PROJECT_ID } from './helpers/visual-fixture';

import type { ProjectProgressList } from '../../src/api/platform-client';

const PROJECT_ID = VISUAL_PROJECT_ID;
const PROGRESS_PATH = `**/platform/projects/${PROJECT_ID}/progress`;

const PROGRESS: ProjectProgressList = [
  {
    progress_area: 'unlicensed_conducted',
    progress_bucket_id: 'unii_1',
    planned_minutes: 120,
    completed_minutes: 60,
    percent: 50,
    total_conditions: 3,
    priced_conditions: 2,
    unpriced_conditions: 1,
    unbucketable_conditions: 0,
  },
  {
    progress_area: 'unlicensed_radiated',
    progress_bucket_id: null,
    planned_minutes: 0,
    completed_minutes: 0,
    percent: null,
    total_conditions: 1,
    priced_conditions: 0,
    unpriced_conditions: 0,
    unbucketable_conditions: 1,
  },
];

async function mockProgress(page: Page): Promise<void> {
  await page.route(PROGRESS_PATH, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(PROGRESS),
    });
  });
}

test('progress filters by area, surfaces quality counts, and restores the full rollup', async ({
  page,
}) => {
  await mockProgress(page);
  await injectAuthenticatedSession(page);
  await page.goto(`/progress?project=${PROJECT_ID}&area=unlicensed_conducted`);

  await expect(page.getByTestId('progress-route')).toBeVisible();
  await expect(page.getByTestId('progress-area-filter')).toContainText('Unlicensed 전도');
  await expect(page.getByTestId('progress-metric-percent')).toContainText('50');
  await expect(page.getByTestId('progress-metric-unpriced')).toHaveText('1');
  await expect(page.getByTestId('progress-quality-warning')).toBeVisible();
  await expect(page.getByTestId('progress-area')).toHaveCount(1);
  await expect(page.getByTestId('progress-bucket')).toHaveAttribute('data-bucket', 'unii_1');
  await expect(page.getByTestId('progress-next-actions')).toBeVisible();

  await page.getByTestId('progress-area-filter-clear').click();
  await expect(page).toHaveURL(new RegExp(`/progress\\?project=${PROJECT_ID}$`));
  await expect(page.getByTestId('progress-area-filter')).not.toBeVisible();
  await expect(page.getByTestId('progress-area')).toHaveCount(2);
  await expect(page.getByTestId('progress-project-context')).toHaveText(PROJECT_ID);
});
