import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';

const PROJECT_ID = '33333333-3333-4333-8333-333333333333';
const SAMPLE_ID = '44444444-4444-4444-8444-444444444444';

const sample = {
  sample_id: SAMPLE_ID,
  project_id: PROJECT_ID,
  sample_number: 'S-001',
  sample_code: 'CODE-001',
  test_category: 'Main Conduction',
  label_number: 'LBL-001',
  smsn: 'SMSN-001',
  serial_number: 'SN-001',
  intake_cert: 'CERT-001',
  assigned_team: 'RF',
  sender: 'sender',
  receiver: 'receiver',
  received_date: '2026-08-01',
  released_date: null,
  note: null,
  status: 'active',
  row_version: 1,
  intake_count: 1,
  latest_intake: null,
};

async function fulfillJson(route: Route, body: unknown): Promise<void> {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function mockInventoryApi(page: Page): Promise<void> {
  await page.route('**/runtime-config.js', async (route) => {
    const response = await route.fetch();
    const source = await response.text();
    await route.fulfill({
      response,
      body: source
        .replaceAll('http://localhost:5173', 'http://localhost:5174')
        .replaceAll('ws://localhost:5173', 'ws://localhost:5174')
        .replace("authMode: 'oidc'", "authMode: 'local'")
        .replace('insecureTransportAllowed: false', 'insecureTransportAllowed: true'),
    });
  });
  const projects = [
    {
      project_id: PROJECT_ID,
      project_code: 'INV-E2E',
      model_name: 'Inventory E2E',
      manufacturer: null,
      management_number: 'M-001',
      status: 'active',
      sample_count: 1,
    },
  ];
  const fulfillProjects = (route: Route): Promise<void> => fulfillJson(route, projects);
  await page.route('**/platform/projects**', fulfillProjects);
  await page.route('**/platform/sample-inventory*', (route) =>
    fulfillJson(route, { items: [sample], next_cursor: null, as_of: null, filters: {} }),
  );
  await page.route('**/platform/projects/*/sample-inventory/exports/*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      headers: { 'content-disposition': 'attachment; filename="sample-inventory.xlsx"' },
      body: Buffer.from('xlsx'),
    }),
  );
}

async function openInventory(page: Page): Promise<void> {
  await injectAuthenticatedSession(page);
  await page.goto(`/inventory?project=${PROJECT_ID}`);
  await expect(page.getByTestId('inventory-workbench')).toBeVisible();
  await expect(page.getByTestId('inventory-sample-list')).toBeVisible();
}

test.describe('Sample inventory current platform workflow', () => {
  test('lists the authoritative sample and applies URL-backed filters', async ({ page }) => {
    await mockInventoryApi(page);
    await openInventory(page);

    await expect(page.getByTestId('inventory-sample-S-001')).toContainText('S-001');
    await expect(page.getByTestId('inventory-sample-S-001')).toContainText('RF');

    await page.getByTestId('inventory-team-filter').fill('RF');
    await page.getByTestId('inventory-status-filter').selectOption('all');
    await expect(page).toHaveURL(/team=RF/);
    await expect(page).toHaveURL(/status=all/);
  });

  test('uses the current project-scoped export endpoints', async ({ page }) => {
    await mockInventoryApi(page);
    const exportPaths: string[] = [];
    await page.route('**/platform/projects/*/sample-inventory/exports/*', async (route) => {
      exportPaths.push(new URL(route.request().url()).pathname);
      await route.fulfill({
        status: 200,
        contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers: { 'content-disposition': 'attachment; filename="sample-inventory.xlsx"' },
        body: Buffer.from('xlsx'),
      });
    });

    await openInventory(page);
    await page.getByTestId('sample-export-pm').click();
    await page.getByTestId('sample-export-rf').click();
    await expect.poll(() => exportPaths).toHaveLength(2);
    expect(exportPaths).toEqual([
      `/platform/projects/${PROJECT_ID}/sample-inventory/exports/pm-status`,
      `/platform/projects/${PROJECT_ID}/sample-inventory/exports/rf-data`,
    ]);
  });
});
