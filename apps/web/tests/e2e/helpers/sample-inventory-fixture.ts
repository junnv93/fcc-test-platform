import type { components } from '@/api/generated/platform-api.types';
import type { Page, Route } from '@playwright/test';

/**
 * Chamber start requires an **active sample**, and both chamber-start specs
 * must say so the same way.
 *
 * `9bc09370` made `MeasurementStarter` gate its submit button on
 * `sampleSelectionReady` — a successful `GET /platform/sample-inventory` whose
 * page contains at least one `status: 'active'` item, one of which is selected.
 * Neither `chambers-workflow.spec.ts` nor the chamber-start leg of
 * `test-plans-workflow.spec.ts` mocked that endpoint, so the button stayed
 * disabled, the POST never fired, and both specs failed on `main` with a
 * `waitForRequest` timeout that named the POST rather than its cause.
 *
 * ⚠️ **This lives in one file on purpose.** The two specs exercise the same
 * production precondition; if each grew its own inline mock, the same contract
 * would acquire two shapes and the next change to the sample list would have to
 * find both. The shape is bound to the generated `SampleInventoryPage`, so a
 * backend field change breaks the type here rather than silently producing a
 * fixture the real screen would reject.
 */
type SampleInventoryPage = components['schemas']['SampleInventoryPage'];
type SampleInventoryItem = components['schemas']['SampleInventoryItem'];

/** Matches `platformClient.GET('/platform/sample-inventory')` with or without a query. */
const SAMPLE_INVENTORY_GLOB = '**/platform/sample-inventory';
const SAMPLE_INVENTORY_GLOB_Q = '**/platform/sample-inventory?*';

/** The sample the chamber-start specs select. Exported so a spec can assert on it. */
export const ACTIVE_SAMPLE_ID = 'sample-active-1';
export const ACTIVE_SAMPLE_NUMBER = 'SM-A-001';

function sample(over: Partial<SampleInventoryItem> = {}): SampleInventoryItem {
  return {
    sample_id: ACTIVE_SAMPLE_ID,
    project_id: '22222222-2222-4222-8222-222222222222',
    status: 'active',
    row_version: 1,
    latest_intake: null,
    intake_count: 0,
    sample_number: ACTIVE_SAMPLE_NUMBER,
    label_number: null,
    sample_code: null,
    ...over,
  };
}

/**
 * Serve one page holding a single active sample for `projectId`.
 *
 * A non-active row is included deliberately: `MeasurementStarter` filters the
 * page down to `status === 'active'` before offering options, and a fixture with
 * only active rows cannot tell a working filter from an absent one. The status
 * vocabulary is `'active' | 'deleted'` — taken from the generated schema, not
 * guessed; an invented `'retired'` failed `tsc` here rather than in a browser.
 */
export async function mockActiveSampleInventory(page: Page, projectId: string): Promise<void> {
  const body: SampleInventoryPage = {
    items: [
      sample({ project_id: projectId }),
      sample({
        project_id: projectId,
        sample_id: 'sample-deleted-1',
        sample_number: 'SM-D-009',
        status: 'deleted',
      }),
    ],
    next_cursor: null,
    filters: { project_id: projectId, status: 'active' },
  };
  const handle = async (route: Route): Promise<void> => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  };
  // Register both the bare and the query-string form: the bare glob does not
  // match a URL carrying `?project_id=…&status=active&limit=100`.
  await page.route(SAMPLE_INVENTORY_GLOB, handle);
  await page.route(SAMPLE_INVENTORY_GLOB_Q, handle);
}
