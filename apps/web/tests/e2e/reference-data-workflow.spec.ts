import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';

/**
 * Reference-data workbench: provider selection → candidate revision →
 * server-owned cell edit and re-read.  The assertions consume the existing
 * production test ids and inspect the exact write request, so a rendered
 * button cannot hide a drifted endpoint or an over-broad payload.
 */

const PROVIDER = 'fcc-unlicensed-conducted';
const REVISION = 'rev-candidate';
const PROVIDERS_PATH = '**/platform/providers';
const CHAMBERS_PATH = '**/platform/chambers';
const FAMILIES_PATH = `**/platform/providers/${PROVIDER}/reference-families`;
const REVISIONS_PATH = `**/platform/providers/${PROVIDER}/reference-revisions*`;
const DETAIL_PATH = `**/platform/providers/${PROVIDER}/reference-revisions/${REVISION}`;
const EDIT_PATH = `${DETAIL_PATH}/entries`;
const EDIT_URL_PATH = `/platform/providers/${PROVIDER}/reference-revisions/${REVISION}/entries`;

const BASE_REVISION = {
  revision_id: REVISION,
  provider_id: PROVIDER,
  family: 'correction',
  profile_id: 'default',
  scope_kind: 'room',
  scope_id: 'room-1',
  revision_number: 2,
  state: 'CANDIDATE',
  version: 2,
  etag: 'etag-candidate',
  content_sha256: 'c'.repeat(64),
  source_snapshot_id: 'snapshot-1',
  source_manifest_sha256: 'm'.repeat(64),
  provenance_kind: 'FORK_EDIT',
  entry_count: 1,
  created_by: 'e2e-operator',
  created_at: '2026-08-17T00:00:00Z',
  updated_by: 'e2e-operator',
  updated_at: '2026-08-17T00:00:00Z',
  approval_reason: null,
  approved_at: null,
  approved_by: null,
};

function detail(correctionDb: number): Record<string, unknown> {
  return {
    revision: { ...BASE_REVISION, etag: correctionDb === 9.75 ? 'etag-saved' : 'etag-candidate' },
    entries: [
      {
        reference_id: 'ref-0',
        identity_key: 'correction|row=0',
        entry_order: 0,
        payload: { correction_index: 'A', frequency_hz: 2400, correction_db: correctionDb },
        content_sha256: 'd'.repeat(64),
      },
    ],
    payload_columns: ['correction_index', 'frequency_hz', 'correction_db'],
    identity_columns: ['correction_index', 'frequency_hz'],
    coupled_with: null,
  };
}

async function mockReferenceData(page: Page): Promise<{
  readonly editRequests: object[];
  readonly detailRequestCount: () => number;
}> {
  const editRequests: object[] = [];
  let detailRequests = 0;
  let saved = false;

  await page.route(PROVIDERS_PATH, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{ provider_id: PROVIDER }]),
    }),
  );
  await page.route(CHAMBERS_PATH, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [] }),
    }),
  );
  await page.route(FAMILIES_PATH, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          family: 'correction',
          scope_kind: 'room',
          payload_columns: ['correction_index', 'frequency_hz', 'correction_db'],
          identity_columns: ['correction_index', 'frequency_hz'],
          coupled_with: null,
          default_profile_id: 'default',
        },
      ]),
    }),
  );
  await page.route(REVISIONS_PATH, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{ ...BASE_REVISION, state: 'CANDIDATE' }]),
    }),
  );
  await page.route(DETAIL_PATH, async (route) => {
    detailRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(detail(saved ? 9.75 : 1.5)),
    });
  });
  await page.route(EDIT_PATH, async (route: Route) => {
    editRequests.push(route.request().postDataJSON() as object);
    saved = true;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(detail(9.75)),
    });
  });

  return { editRequests, detailRequestCount: () => detailRequests };
}

test('reference-data provider selection edits a candidate and reads the saved cell back', async ({
  page,
}) => {
  const { editRequests, detailRequestCount } = await mockReferenceData(page);
  await injectAuthenticatedSession(page);
  await page.goto('/reference-data');

  await expect(page.getByRole('heading', { name: '참조 데이터', level: 1 })).toBeVisible();
  await page.getByTestId('reference-data-provider-select').selectOption(PROVIDER);
  const openCandidate = page.getByTestId(`reference-open-${REVISION}`).first();
  await expect(openCandidate).toBeVisible();
  await openCandidate.click();

  const cell = page.getByTestId('reference-data-cell-ref-0-correction_db').first();
  await expect(cell).toBeVisible();
  await cell.fill('9.75');
  await expect(page.getByTestId('reference-data-save').first()).toBeEnabled();

  const [request] = await Promise.all([
    page.waitForRequest(
      (candidate) =>
        candidate.method() === 'PUT' && new URL(candidate.url()).pathname === EDIT_URL_PATH,
    ),
    page.getByTestId('reference-data-save').first().click(),
  ]);
  expect(editRequests).toEqual([
    {
      expected_etag: 'etag-candidate',
      edits: [
        {
          reference_id: 'ref-0',
          payload: { correction_index: 'A', frequency_hz: 2400, correction_db: 9.75 },
        },
      ],
    },
  ]);
  expect(new URL(request.url()).pathname).toBe(EDIT_URL_PATH);
  await expect.poll(detailRequestCount).toBeGreaterThan(1);
  await expect(cell).toHaveValue('9.75');
});
