import { expect, test, type Page } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';

/**
 * Phase 2 §8.3 — Providers route ("Test Types" operator screen) workflow smoke.
 *
 * Realigned to the tester-UX hardening R2 policy (2026-06-21): the screen is an
 * operator-facing "시험 종류" (Test Types) view, NOT an internal descriptor
 * viewer. Rendered strings are asserted in Korean like every other e2e spec —
 * the production default is English (`DEFAULT_LOCALE = 'en'`), but the Playwright
 * config seeds `fcc-locale='ko'` into localStorage for the base origin so every
 * spec renders Korean (see playwright.config.ts `use.storageState`).
 *
 * Covers:
 *   1. operator summary = human display name; internal provider id / UI version
 *      live behind the collapsed admin/diagnostics <details> (R2 isolation)
 *   2. internal descriptor identifiers (row identity source / Col.* token) are
 *      NOT exposed in the operator tables
 *   3. read-only banner is shown — "읽기 전용 … 편집 기능은 추후 제공" policy copy
 *   4. NO edit / save / publish buttons exist anywhere on the surface (DOM-wide
 *      seal — the Phase 1 invariant complements this with an AST check on the
 *      source file)
 */

const PROVIDER_ID = 'fcc-unlicensed-conducted';
const DESCRIPTOR_GLOB = `**/platform/providers/${PROVIDER_ID}/ui-descriptor*`;

function fixture(): Record<string, unknown> {
  return {
    provider_id: PROVIDER_ID,
    display_name: 'unlicensed-conducted',
    ui_version: 1,
    features: [
      { feature_id: 'test_plan_edit', label: 'test plan edit', status: 'planned' },
      { feature_id: 'job_submission', label: 'job submission', status: 'supported' },
    ],
    test_plan_tables: [
      {
        table_id: 'test_plan',
        label: 'Test Plan',
        sheet_name: 'Test Plan',
        row_identity_source: 'Col.HISTORY_CONDITION',
        row_identity_columns: ['Test'],
        columns: [{ column_id: 'Test', label: 'Test', editable: false }],
      },
    ],
    equipment: [
      {
        group_id: 'chamber_config',
        label: 'Chamber Config',
        sheet_name: 'Chamber Config',
        fields: [],
      },
    ],
    reference_tables: [
      {
        table_id: 'analyzer_settings',
        label: 'Analyzer Settings',
        sheet_name: 'Analyzer Settings',
        columns: [],
      },
    ],
    correction_tables: [],
  };
}

async function open(page: Page): Promise<void> {
  await injectAuthenticatedSession(page);
  // page.route matches by URL only — not Request init — so a glob across hosts
  // is sufficient. The platform client base URL comes from runtime-config
  // (`platformApiBaseUrl ?? apiBaseUrl`, dev stub points at 127.0.0.1:8000).
  await page.route(DESCRIPTOR_GLOB, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(fixture()),
    });
  });
  await page.goto(`/providers?provider=${PROVIDER_ID}`);
  // R2: page title is the operator-facing "시험 종류" (pageTitleBrand), not the
  // old internal "Provider UI Descriptor" debug heading.
  await expect(page.getByRole('heading', { name: '시험 종류', level: 1 })).toBeVisible();
  await expect(page.getByTestId('providers-workbench-overview')).toBeVisible();
  await expect(page.getByTestId('providers-workbench')).toBeVisible();
  await expect(page.getByTestId('providers-next-test-plans')).toHaveAttribute(
    'href',
    '/test-plans',
  );
}

test.describe('Providers route — Test Types (operator screen)', () => {
  test('renders the descriptor summary (display name + collapsed diagnostics)', async ({
    page,
  }) => {
    await open(page);
    // R2: display name is the operator identity; the internal provider id + UI
    // version live in the collapsed admin/diagnostics <details>.
    await expect(page.getByTestId('descriptor-display-name')).toHaveText('unlicensed-conducted');
    await expect(page.getByTestId('providers-next-state')).toContainText(PROVIDER_ID);
    await page.getByTestId('descriptor-diagnostics').locator('summary').click();
    await expect(page.getByTestId('descriptor-provider-id')).toHaveText(PROVIDER_ID);
    await expect(page.getByTestId('descriptor-ui-version')).toHaveText('1');
  });

  test('does NOT expose internal descriptor identifiers in the operator tables', async ({
    page,
  }) => {
    await open(page);
    // R2: row-identity source / internal ids / sheet names are dropped from the
    // operator view (the descriptor payload still carries them).
    await expect(page.getByTestId('row-identity-source')).toHaveCount(0);
    await expect(page.getByText('Col.HISTORY_CONDITION')).toHaveCount(0);
  });

  test('shows the read-only banner so an editable affordance is never implied', async ({
    page,
  }) => {
    await open(page);
    const banner = page.getByTestId('readonly-banner');
    await expect(banner).toBeVisible();
    // Current i18n readonly banner: "ⓘ 본 화면은 읽기 전용 입니다. 편집 기능은
    // 추후 제공됩니다." — the stale "stable row identity / re-keying" copy is
    // gone; the policy is now "편집 기능은 추후 제공".
    await expect(banner).toContainText(/읽기 전용/u);
    await expect(banner).toContainText(/편집 기능은 추후 제공/u);
  });

  test('has NO edit / save / publish buttons (DOM-wide seal)', async ({ page }) => {
    await open(page);
    // Korean and English label variants — the ban is regardless of copy.
    const FORBIDDEN_LABELS = [
      /^편집$/u,
      /^저장$/u,
      /^발행$/u,
      /^Edit$/iu,
      /^Save$/iu,
      /^Publish$/iu,
    ];
    for (const pattern of FORBIDDEN_LABELS) {
      await expect(page.getByRole('button', { name: pattern })).toHaveCount(0);
    }
  });
});
