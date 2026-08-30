import { AxeBuilder } from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

/**
 * Phase 1 (fe-phase1-ui-foundation, 2026-05-30) — UI foundation smoke.
 *
 * The Phase 1 plan §8.2 calls for axe-core + dark/light + keyboard focus +
 * primitive render smoke. Auth-gated routes are not reachable in CI without
 * an IdP, so we exercise the `/auth/callback?error=...` failure surface
 * which renders OidcFailureView synchronously (the same surface
 * `tests/e2e/a11y.spec.ts` already targets — that gives us a real, rendered
 * page that consumes the new tokens without needing a Keycloak compose).
 *
 * The contract:
 *  1. light default — without an explicit `[data-theme]` on a light-OS
 *     client, the surface resolves to the light base token (`#ffffff`).
 *  2. explicit `data-theme="dark"` switches to the dark surface
 *     (`#0b0d10`) without re-layout.
 *  3. axe-core finds zero `critical`/`serious` WCAG violations on both
 *     themes — the colour-contrast rule is the relevant check (WCAG 1.4.3).
 *  4. keyboard focus is visible on the failure view's retry control
 *     (`:focus-visible` outline survives the token cascade).
 */

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

async function gotoAuthCallback(page: Page): Promise<void> {
  await page.goto('/auth/callback?error=access_denied', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('alert')).toBeVisible({ timeout: 10_000 });
}

async function runAxeBlocking(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  const critical = results.violations.filter((v) => v.impact === 'critical');
  const serious = results.violations.filter((v) => v.impact === 'serious');
  if (critical.length > 0 || serious.length > 0) {
    const summary = [...critical, ...serious]
      .map(
        (v) =>
          `[${v.impact ?? 'unknown'}] ${v.id}: ${v.description} (${v.nodes.length} node${
            v.nodes.length === 1 ? '' : 's'
          })`,
      )
      .join('\n');
    console.error(`a11y violations (${label}):\n${summary}`);
  }
  expect(critical, `${label}: WCAG critical violations`).toEqual([]);
  expect(serious, `${label}: WCAG serious violations`).toEqual([]);
}

test.describe('Phase 1 UI foundation smoke', () => {
  test('light default — surface resolves to light token', async ({ page }) => {
    await gotoAuthCallback(page);
    await page.evaluate(() => {
      document.documentElement.removeAttribute('data-theme');
    });
    await page.emulateMedia({ colorScheme: 'light' });
    const surface = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    // light token #ffffff = rgb(255, 255, 255).
    expect(surface).toBe('rgb(255, 255, 255)');
    await page.screenshot({ path: 'test-results/ui-foundation-light.png', fullPage: true });
  });

  test('explicit data-theme=dark switches to the dark surface', async ({ page }) => {
    await gotoAuthCallback(page);
    await page.evaluate(() => {
      document.documentElement.setAttribute('data-theme', 'dark');
    });
    const surface = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    // dark token #0b0d10 = rgb(11, 13, 16).
    expect(surface).toBe('rgb(11, 13, 16)');
    await page.screenshot({ path: 'test-results/ui-foundation-dark.png', fullPage: true });
  });

  test('axe-core finds zero critical/serious on dark theme', async ({ page }) => {
    await gotoAuthCallback(page);
    await page.evaluate(() => {
      document.documentElement.setAttribute('data-theme', 'dark');
    });
    await runAxeBlocking(page, 'auth-callback dark theme');
  });

  test('axe-core finds zero critical/serious on light theme', async ({ page }) => {
    await gotoAuthCallback(page);
    await page.evaluate(() => {
      document.documentElement.setAttribute('data-theme', 'light');
    });
    await runAxeBlocking(page, 'auth-callback light theme');
  });

  test('keyboard Tab lands on a focusable element with a visible outline', async ({ page }) => {
    await gotoAuthCallback(page);
    // Tab from the document to the first focusable descendant. The failure
    // surface renders either a retry button (some variants) or a sign-in
    // link; either is fine — we only assert that focus-visible outline
    // survives the token cascade (a regression would zero it out).
    //
    // We ignore TanStack Query devtools (`.tsqd-*`) and Vite's HMR overlay,
    // which only render in `npm run dev` and are not part of the production
    // failure surface contract.
    await page.keyboard.press('Tab');
    const active = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (el === null || el === document.body) return null;
      if (el.closest('.tsqd-parent-container, vite-error-overlay') !== null) return 'devtools';
      const style = getComputedStyle(el);
      return {
        tag: el.tagName,
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth,
      };
    });
    // If the failure surface renders no focusable controls at all, or the
    // first focus landed on a dev-only devtools button, the assertion is
    // vacuous — skip rather than false-fail.
    test.skip(active === null, 'failure surface has no focusable elements');
    test.skip(active === 'devtools', 'first focus landed on TanStack devtools (dev-only)');
    expect((active as { outlineStyle: string }).outlineStyle).not.toBe('none');
  });
});
