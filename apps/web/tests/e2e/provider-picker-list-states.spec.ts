import { expect, test, type Page } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';

/**
 * Provider-debt follow-up — ProviderPicker list error / empty UX (e2e).
 *
 * The picker options come from `GET /platform/providers` (WEB-PROVIDER-UI-0).
 * The route is backend-first but the list is a *convenience* layer: a failed or
 * empty list must never hard-block the operator — the direct-entry `?provider=`
 * lookup form stays available and the select stays enabled (so a deep-linked /
 * typed id still resolves). These specs drive the list route to 503 (registry
 * down) and to an empty registry and assert the surfaced states end-to-end.
 *
 * The component-level equivalents live in `tests/provider-picker.test.tsx`
 * (vitest + RTL); this spec seals the same UX through the built preview app so
 * the wiring (auth gate → picker query → status note) is exercised in-browser.
 *
 * Korean copy is asserted because the Playwright config seeds `fcc-locale='ko'`
 * for the base origin (see `playwright.config.ts` `use.storageState`).
 */

// The list endpoint is exactly `/platform/providers` (no trailing segment), so a
// glob that ends there does NOT collide with the descriptor route
// `/platform/providers/{id}/ui-descriptor`.
const PROVIDER_LIST_GLOB = '**/platform/providers';

async function openProviders(page: Page): Promise<void> {
  await injectAuthenticatedSession(page);
  await page.goto('/providers');
  await expect(page.getByRole('heading', { name: '시험 종류', level: 1 })).toBeVisible();
}

test.describe('ProviderPicker — list error / empty states', () => {
  test('surfaces an unavailable note on a 503 list failure and keeps direct entry usable', async ({
    page,
  }) => {
    await page.route(PROVIDER_LIST_GLOB, async (route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'registry unavailable' }),
      });
    });
    await openProviders(page);

    const errorNote = page.getByTestId('provider-picker-error');
    await expect(errorNote).toBeVisible();
    await expect(errorNote).toContainText('일시적으로 사용할 수 없습니다');
    // A failed list is not a gate: the select stays enabled and the direct-entry
    // lookup form is still available so a typed / deep-linked id resolves.
    await expect(page.getByTestId('provider-picker')).toBeEnabled();
    await expect(page.getByTestId('provider-input')).toBeVisible();
    await expect(page.getByTestId('provider-picker-empty')).toHaveCount(0);
  });

  test('shows an empty note when the registry returns no providers', async ({ page }) => {
    await page.route(PROVIDER_LIST_GLOB, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });
    await openProviders(page);

    const emptyNote = page.getByTestId('provider-picker-empty');
    await expect(emptyNote).toBeVisible();
    await expect(emptyNote).toContainText('등록된 시험 종류가 없습니다');
    // Empty is distinct from error, and the direct-entry lookup still works.
    await expect(page.getByTestId('provider-picker-error')).toHaveCount(0);
    await expect(page.getByTestId('provider-input')).toBeVisible();
  });
});
