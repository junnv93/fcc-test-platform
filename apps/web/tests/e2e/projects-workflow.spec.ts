import { expect, test, type Page } from '@playwright/test';

import { injectAuthenticatedSession, TEST_OPERATOR_PERMISSIONS } from './helpers/auth-fixture';

/**
 * Phase 2 §8.3 — Projects route (Duplicate Prevention Workbench) workflow
 * smoke. Asserts the architecture plan acceptance criteria end-to-end via the
 * platform read API mocked at the network layer.
 *
 * The smoke covers:
 *   1. distinct_operator_count > 1 → cross-engineer duplicate badge
 *   2. attempt_count > 1 + distinct_operator_count = 1 → same-engineer
 *      re-measure (separated from the duplicate signal)
 *   3. stale central data warning is not hidden
 *   4. offline guard disables claim writes (and surfaces an alert)
 */

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const COVERAGE_GLOB = `**/platform/projects/${PROJECT_ID}/coverage*`;
const CLAIMS_GLOB = `**/platform/projects/${PROJECT_ID}/claims*`;
const SYNC_GLOB = `**/platform/projects/${PROJECT_ID}/sync-status*`;

interface MockPage {
  readonly items: readonly Record<string, unknown>[];
  /** Opaque `X-Next-Cursor` response header. `null` / undefined = last page. */
  readonly nextCursor?: string | null;
}

function coverageRow(over: Record<string, unknown>): Record<string, unknown> {
  return {
    project_id: PROJECT_ID,
    condition_hash: 'h-base',
    technology: 'UNII',
    attempt_count: 1,
    distinct_operator_count: 1,
    distinct_session_count: 1,
    latest_verdict: 'Pass',
    latest_attempt_number: 1,
    latest_measured_at: '2026-05-30T00:00:00',
    latest_operator: 'alice',
    latest_session_id: 's1',
    ...over,
  };
}

/** Build the JSON body + `X-Next-Cursor` header for one keyset page — mirrors
 *  the wire shape fetchCoveragePage / fetchClaimsPage / fetchActiveMembers
 *  read (flat array body + opaque header cursor, per `nextCursorFromResponse`). */
function pageResponse(page: MockPage): {
  body: string;
  headers: Record<string, string>;
} {
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (page.nextCursor !== undefined && page.nextCursor !== null) {
    headers['X-Next-Cursor'] = page.nextCursor;
  }
  return { body: JSON.stringify(page.items), headers };
}

async function mockPlatformReads(
  page: Page,
  opts: {
    coverage?: MockPage;
    claims?: MockPage;
    syncStatus?: Record<string, unknown>;
  } = {},
): Promise<void> {
  const coverage = opts.coverage ?? { items: [] };
  const claims = opts.claims ?? { items: [] };
  const sync = opts.syncStatus ?? {
    project_id: PROJECT_ID,
    last_ingested_at: null,
    age_seconds: null,
    is_stale: false,
    active_claim_count: 0,
    condition_count: 0,
    server_time: '2026-05-30T00:00:00',
    stale_threshold_seconds: 3600,
  };
  await page.route(COVERAGE_GLOB, async (route) => {
    const resp = pageResponse(coverage);
    await route.fulfill({ status: 200, headers: resp.headers, body: resp.body });
  });
  await page.route(CLAIMS_GLOB, async (route) => {
    const resp = pageResponse(claims);
    await route.fulfill({ status: 200, headers: resp.headers, body: resp.body });
  });
  await page.route(SYNC_GLOB, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sync) }),
  );
}

async function open(
  page: Page,
  opts: { projectId?: string; expandTech?: string; permissions?: readonly string[] } = {},
): Promise<void> {
  const projectId = opts.projectId ?? PROJECT_ID;
  const params = new URLSearchParams({ project: projectId });
  if (opts.expandTech !== undefined) params.set('tech', opts.expandTech);
  await injectAuthenticatedSession(
    page,
    opts.permissions !== undefined ? { permissions: opts.permissions } : {},
  );
  await page.goto(`/projects?${params.toString()}`);
  await expect(page.getByRole('heading', { name: '측정 현황', level: 1 })).toBeVisible();
  await expect(page.getByTestId('projects-workbench-overview')).toBeVisible();
  await expect(page.getByTestId('projects-workbench')).toBeVisible();
  await expect(page.getByTestId('projects-next-state')).toContainText(projectId);
  await expect(page.getByTestId('projects-next-membership')).toHaveAttribute(
    'href',
    `/membership?project=${projectId}`,
  );
}

/** A read-only token: the base operator set MINUS platform:claim. Models a user
 *  who may hold claim only through project membership (token ∪ membership). */
const READ_ONLY_PERMISSIONS = TEST_OPERATOR_PERMISSIONS.filter((p) => p !== 'platform:claim');

test.describe('Projects route — Duplicate Prevention Workbench', () => {
  test('renders the cross-engineer duplicate badge when distinct_operator_count > 1', async ({
    page,
  }) => {
    await mockPlatformReads(page, {
      coverage: {
        items: [
          coverageRow({
            condition_hash: 'dupe-1',
            attempt_count: 3,
            distinct_operator_count: 2,
            latest_operator: 'bob',
          }),
        ],
        nextCursor: null,
      },
    });
    await open(page, { expandTech: 'UNII' });
    await expect(page.getByTestId('tech-cross-duplicate')).toBeVisible();
    await expect(page.getByTestId('condition-cross-duplicate')).toBeVisible();
    // Phase L tester-language copy — the condition badge reads "다른 시험원과 중복".
    await expect(page.getByTestId('condition-cross-duplicate')).toContainText(
      /다른 시험원과 중복/u,
    );
  });

  test('renders re-measure (NOT duplicate) when same engineer measured twice', async ({ page }) => {
    await mockPlatformReads(page, {
      coverage: {
        items: [
          coverageRow({
            condition_hash: 'remeasure-1',
            attempt_count: 2,
            distinct_operator_count: 1,
          }),
        ],
        nextCursor: null,
      },
    });
    await open(page, { expandTech: 'UNII' });
    await expect(page.getByTestId('tech-repeated')).toBeVisible();
    await expect(page.getByTestId('condition-repeated')).toBeVisible();
    await expect(page.getByTestId('condition-cross-duplicate')).toHaveCount(0);
  });

  test('surfaces the stale central data warning without hiding it', async ({ page }) => {
    await mockPlatformReads(page, {
      coverage: { items: [coverageRow({})], nextCursor: null },
      syncStatus: {
        project_id: PROJECT_ID,
        last_ingested_at: '2026-05-29T00:00:00',
        age_seconds: 7200,
        is_stale: true,
      },
    });
    await open(page);
    const status = page.getByTestId('sync-status');
    await expect(status).toBeVisible();
    await expect(status).toHaveAttribute('data-stale', 'true');
    await expect(status).toContainText(/최신성/u);
  });

  test('membership-effective: a read-only token still gets the claim control + advisory (no false block)', async ({
    page,
  }) => {
    // The backend authorizes token ∪ project-membership effective permissions, so
    // a user without the platform:claim token may still hold claim via membership.
    // The UI must offer the control (not hard-hide it) and only show a
    // non-blocking advisory; the backend stays the authority.
    await mockPlatformReads(page, {
      coverage: { items: [coverageRow({ condition_hash: 'free-1' })], nextCursor: null },
    });
    await open(page, { expandTech: 'UNII', permissions: READ_ONLY_PERMISSIONS });
    await expect(page.getByTestId('condition-claim-acquire')).toBeVisible();
    await expect(page.getByTestId('condition-claim-acquire')).toBeEnabled();
    await expect(page.getByTestId('claim-token-hint')).toBeVisible();
  });

  test('membership-effective: a read-only token surfaces a backend 403 as the forbidden claim error', async ({
    page,
  }) => {
    await mockPlatformReads(page, {
      coverage: { items: [coverageRow({ condition_hash: 'free-2' })], nextCursor: null },
    });
    // The acquire write (POST to the same /claims path the GET reads) is rejected
    // by the backend (no token AND no membership grant). Registered last so it
    // wins over the read mock; GET still returns the empty claims page.
    await page.route(CLAIMS_GLOB, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 403,
          contentType: 'application/problem+json',
          body: JSON.stringify({ title: 'forbidden', status: 403, code: 'forbidden' }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify([]),
      });
    });
    await open(page, { expandTech: 'UNII', permissions: READ_ONLY_PERMISSIONS });
    await page.getByTestId('condition-claim-acquire').click();
    await expect(page.getByTestId('claim-error')).toBeVisible();
    await expect(page.getByTestId('claim-error')).toContainText(/권한/u);
  });

  test('shows the offline guard for claim writes when navigator goes offline', async ({ page }) => {
    await mockPlatformReads(page, {
      coverage: { items: [coverageRow({})], nextCursor: null },
    });
    await open(page);
    // Force-offline via the BOM event; the route subscribes via useOnlineStatus.
    await page.evaluate(() => {
      Object.defineProperty(window.navigator, 'onLine', { value: false, configurable: true });
      window.dispatchEvent(new Event('offline'));
    });
    await expect(page.getByTestId('claim-offline')).toBeVisible();
    await expect(page.getByTestId('claim-offline')).toContainText(/오프라인/u);
  });
});
