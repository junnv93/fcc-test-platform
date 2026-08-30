import { readFile } from 'node:fs/promises';

import { expect, test } from '@playwright/test';

import { TEST_OPERATOR_PERMISSIONS } from './helpers/auth-fixture';
import { openLiveLane, requireProject } from './helpers/live-stack-fixture';

const AUTHOR_PERMISSIONS = [
  ...TEST_OPERATOR_PERMISSIONS,
  'test_plan:read',
  'test_plan:author',
] as const;

test.describe('Test plans live stack smoke', () => {
  // Neither a skip nor a per-lane environment variable. The gate is one
  // definition (`scripts/live-lane-registry.mjs`) and the project id comes from
  // the seed manifest, so an operator can no longer arm the lane with a stale or
  // absent identifier and get a green run that quietly tested nothing.
  test('selects a real project, generates a draft, then adds and removes a row', async ({
    page,
    context,
  }) => {
    const ids = await openLiveLane('test-plans', page, context, {
      permissions: AUTHOR_PERMISSIONS,
    });
    const liveProjectId = requireProject(ids, 'activeProject').id;
    const baseUrl = test.info().project.use.baseURL;
    if (baseUrl && new URL(baseUrl).hostname === 'hostmachine') {
      const runtimeConfig = await readFile(
        new URL('../../public/runtime-config.js', import.meta.url),
        'utf8',
      );
      const browserOrigin = new URL(baseUrl).origin;
      await page.route('**/runtime-config.js', (route) =>
        route.fulfill({
          contentType: 'application/javascript',
          body: runtimeConfig
            .replaceAll('http://localhost:5173', browserOrigin)
            .replaceAll('ws://localhost:5173', browserOrigin.replace(/^http/, 'ws')),
        }),
      );
    }

    await page.goto('/test-plans');
    await expect(page.getByRole('heading', { name: '테스트 플랜', level: 1 })).toBeVisible();

    const projectSelect = page.getByTestId('test-plans-project-select');
    await expect(projectSelect).toBeVisible();
    await projectSelect.selectOption(liveProjectId);
    await expect(page.getByTestId('test-plans-context-project')).toHaveText(liveProjectId);

    await expect(page.getByTestId('test-plans-generator-form')).toBeVisible();
    await page.getByTestId('test-plans-generator-submit').click();
    await expect(page.getByTestId('test-plans-generator-preview')).toBeVisible();
    await page.getByTestId('test-plans-generator-submit').click();

    await expect(page.getByTestId('test-plans-generator-job-status')).toHaveText('완료');
    await expect(page.getByTestId('test-plans-generator-row').first()).toBeVisible();
    await expect(page.getByTestId('test-plans-context-draft')).not.toHaveText('');
    await expect(page.getByTestId('test-plans-detail')).toBeVisible();
    await expect(page.getByTestId('test-plans-detail-row-count')).not.toHaveText('0');

    const beforeAdd = await page.getByTestId('test-plans-detail-row').count();
    await page.getByTestId('test-plans-add-row-path').fill('BLE / DTM / 1M');
    await page.getByTestId('test-plans-add-row-test-type').fill('PSD');
    await page.getByTestId('test-plans-add-row-submit').click();

    const addedRow = page.getByTestId('test-plans-detail-row').last();
    await expect(addedRow.getByRole('cell', { name: 'BLE / DTM / 1M', exact: true })).toBeVisible();
    await expect(addedRow.getByRole('cell', { name: 'PSD', exact: true })).toBeVisible();
    await expect
      .poll(async () => page.getByTestId('test-plans-detail-row').count())
      .toBe(beforeAdd + 1);

    await page.getByTestId('test-plans-remove-row').last().click();
    await expect
      .poll(async () => page.getByTestId('test-plans-detail-row').count())
      .toBe(beforeAdd);
  });
});
