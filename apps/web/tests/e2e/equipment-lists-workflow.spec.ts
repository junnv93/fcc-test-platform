import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';
import { VISUAL_PROJECT_ID } from './helpers/visual-fixture';

import type {
  CreateTestEquipmentListRequest,
  ProjectEnvelope,
  ReplaceTestEquipmentListItemsRequest,
  TestEquipmentListEnvelope,
  TestEquipmentListItem,
  TestEquipmentListSummary,
  TestEquipmentTableSpec,
} from '../../src/api/platform-client';

const PROJECT_ID = VISUAL_PROJECT_ID;
const LIST_ID = 'equipment-list-dts';
const PROJECTS_PATH = '**/platform/projects?*';
const PROJECTS_BARE_PATH = '**/platform/projects';
const LISTS_PATH = `**/platform/projects/${PROJECT_ID}/equipment-lists`;
const DETAIL_PATH = `${LISTS_PATH}/${LIST_ID}`;
const ITEMS_PATH = `${DETAIL_PATH}/items`;
const CONFIRM_PATH = `${DETAIL_PATH}/confirm`;

const PROJECT: ProjectEnvelope = {
  project_id: PROJECT_ID,
  project_code: 'SM-TEST',
  model_name: 'SM-TEST',
  management_number: 'M-001',
  status: 'active',
  sample_count: 0,
};

const TABLES: TestEquipmentTableSpec[] = [
  { item_type: 'equipment', columns: ['manufacturer', 'model_name', 'serial_number'] },
  { item_type: 'test_software', columns: ['manufacturer', 'model_name', 'software_version'] },
];

function summary(status: 'draft' | 'confirmed' = 'draft', itemCount = 0): TestEquipmentListSummary {
  return {
    list_id: LIST_ID,
    project_id: PROJECT_ID,
    test_item_key: 'DTS',
    test_item_name: 'DTS WLAN',
    status,
    item_count: itemCount,
    confirmed_at: status === 'confirmed' ? '2026-08-17T01:00:00Z' : null,
    created_at: '2026-08-17T00:00:00Z',
    updated_at: '2026-08-17T00:00:00Z',
  };
}

function detail(
  status: 'draft' | 'confirmed' = 'draft',
  items: TestEquipmentListItem[] = [],
): TestEquipmentListEnvelope {
  return { list: summary(status, items.length), items, tables: TABLES };
}

async function mockEquipmentLists(page: Page): Promise<{
  readonly createRequests: CreateTestEquipmentListRequest[];
  readonly itemRequests: ReplaceTestEquipmentListItemsRequest[];
  readonly confirmRequests: number;
}> {
  const createRequests: CreateTestEquipmentListRequest[] = [];
  const itemRequests: ReplaceTestEquipmentListItemsRequest[] = [];
  let confirmRequests = 0;
  let created = false;
  let currentDetail = detail();

  const projectHandler = async (route: Route): Promise<void> => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([PROJECT]),
    });
  };
  await page.route(PROJECTS_PATH, projectHandler);
  await page.route(PROJECTS_BARE_PATH, projectHandler);

  await page.route(LISTS_PATH, async (route: Route) => {
    if (route.request().method() === 'POST') {
      created = true;
      createRequests.push(route.request().postDataJSON() as CreateTestEquipmentListRequest);
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(summary()),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        lists: created ? [currentDetail.list] : [],
        test_items: ['DTS', 'BLE'],
      }),
    });
  });

  await page.route(DETAIL_PATH, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(currentDetail),
    });
  });
  await page.route(ITEMS_PATH, async (route: Route) => {
    const body = route.request().postDataJSON() as ReplaceTestEquipmentListItemsRequest;
    itemRequests.push(body);
    currentDetail = detail(
      'draft',
      body.items.map((item, index) => ({
        ...item,
        item_id: `fixture-item-${index}`,
        sort_order: index,
      })),
    );
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ list_id: LIST_ID, item_count: body.items.length }),
    });
  });
  await page.route(CONFIRM_PATH, async (route: Route) => {
    confirmRequests += 1;
    currentDetail = detail('confirmed', currentDetail.items);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        list_id: LIST_ID,
        status: 'confirmed',
        confirmed_at: '2026-08-17T01:00:00Z',
      }),
    });
  });

  return {
    createRequests,
    itemRequests,
    get confirmRequests() {
      return confirmRequests;
    },
  };
}

test('equipment-lists creates, edits, saves, and freezes a server-shaped list', async ({
  page,
}) => {
  const requests = await mockEquipmentLists(page);
  await injectAuthenticatedSession(page);
  await page.goto(`/equipment-lists?project=${PROJECT_ID}`);

  await expect(page.getByRole('heading', { name: '시험 장비목록', level: 1 })).toBeVisible();

  await page.getByTestId('equipment-lists-test-item-key').selectOption('DTS');
  await page.getByTestId('equipment-lists-test-item-name').fill('DTS WLAN');
  await page.getByTestId('equipment-lists-create').click();
  const createdList = page.getByTestId(`equipment-list-select-${LIST_ID}`).first();
  await expect(createdList).toBeVisible();
  expect(requests.createRequests).toEqual([{ test_item_key: 'DTS', test_item_name: 'DTS WLAN' }]);

  await createdList.click();
  const equipmentBlock = page.getByTestId('equipment-lists-block-equipment');
  await equipmentBlock.getByTestId('equipment-lists-add-equipment').click();
  await equipmentBlock.locator('input').nth(1).fill('N9030B');
  await page.getByTestId('equipment-lists-save').click();

  await expect.poll(() => requests.itemRequests.length).toBe(1);
  expect(requests.itemRequests).toEqual([
    { items: [{ item_type: 'equipment', model_name: 'N9030B' }] },
  ]);

  await page.getByTestId('equipment-lists-confirm').click();
  await expect.poll(() => requests.confirmRequests).toBe(1);
  await expect(page.getByTestId('equipment-lists-frozen-note')).toBeVisible();
  await expect(page.getByTestId('equipment-lists-save')).not.toBeVisible();
  await expect(equipmentBlock.locator('input').first()).toHaveAttribute('readonly', '');
});
