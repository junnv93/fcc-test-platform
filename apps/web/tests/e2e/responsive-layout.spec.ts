import { expect, test, type Page } from '@playwright/test';

import { injectAuthenticatedSession, TEST_ADMIN_PERMISSIONS } from './helpers/auth-fixture';
import {
  assertVisualRouteReady,
  installVisualFixture,
  VISUAL_ROUTE_DEFINITIONS,
} from './helpers/visual-fixture';

/**
 * Responsive shell layout smoke (operator-ux-responsive-shell, 2026-06-23).
 *
 * Seals the class of regression the responsive-design plan targets: the header
 * navigation overflowing the document on tablet/mobile. Deterministic — an
 * injected synthetic session (no live Keycloak) and no backend dependency (the
 * shell + nav render regardless of whether the home data query resolves).
 *
 * Contract (plan Acceptance Criteria 2/3/7 + review-focus a11y):
 *  1. No document-level horizontal overflow at 390 / 900 / 1440 px
 *     (`scrollWidth <= clientWidth`) — closed AND, on mobile, with the menu open.
 *  2. Desktop (>= --bp-lg): grouped nav is inline; the compact menu toggle is
 *     hidden.
 *  3. Tablet + mobile (< --bp-lg): the nav collapses behind the menu toggle;
 *     the panel is a controlled disclosure (`aria-expanded`), keyboard
 *     reachable, and `Escape` closes it returning focus to the toggle.
 *  4. The breakpoint threshold is the documented `--bp-lg` token SSOT.
 */

/**
 * W3 §M7.6 — the six widths the contract fixes, one per implemented band
 * plus the two the bands are bounded by:
 *   390  phone    (< --bp-sm)
 *   640  compact  (== --bp-sm)
 *   768  tablet   (== --bp-md)
 *   1024 desktop  (== --bp-lg, the two-column workbench turns on)
 *   1280 desktop, 1440 wide
 * The nav-model tests below keep using 390/900/1440 — 900 is deliberately a
 * NON-boundary width, which is how a band that only works exactly at its edge
 * gets caught.
 */
const WIDTHS = [390, 640, 768, 1024, 1280, 1440] as const;

const RESPONSIVE_PERMISSIONS = [
  ...TEST_ADMIN_PERMISSIONS,
  'test_plan:read',
  'test_plan:author',
] as const;

/**
 * Every operator-facing route. Overflow is a DOCUMENT-level property, so it
 * has to be checked per route: one wide table or one unbreakable token is
 * enough to push the whole page sideways, and the shell smoke at `/` cannot
 * see that. Routes render their shell + empty/error state without a backend,
 * which is exactly the layout this assertion is about.
 */
const ROUTES = [
  '/',
  '/my-projects',
  '/fields',
  '/projects',
  '/jobs',
  '/sessions',
  '/control',
  '/progress',
  '/reports',
  '/test-reports',
  '/equipment-lists',
  '/reference-data',
  '/artifact-custody',
  '/test-plans',
  '/diagnostics',
  // 신원 축 EMS 정합 (2026-08-21). Outside the shell, but still addresses an
  // operator sees at every width — and `/login` is the FIRST thing they see on a
  // phone-width browser, so an overflow here is the worst possible first impression.
  '/login',
  '/change-password',
  '/inventory',
  '/membership',
  '/providers',
  '/chambers',
  // ── Deliberate exclusions (kept here, next to the list, so an omission is a
  //    recorded decision rather than an oversight; drift is sealed by
  //    `tests/test_frontend_visual_language.py::TestResponsiveRouteCoverage`):
  //  · `/grid-poc` — dev-only POC behind its own Playwright project and
  //    explicitly exempted from the responsive contract (`.grid-poc__table`
  //    keeps its 1640px scroll floor by supervisor ruling, M7.5).
  //  · `/auth/callback` — not an operator screen: it renders outside the app
  //    shell and immediately redirects, so there is no layout to measure.
  //  · `*` (NotFound) — a wildcard, not an address; its content is a single
  //    paragraph already covered by the shell smoke at `/`.
] as const;

async function gotoHome(page: Page): Promise<void> {
  await injectAuthenticatedSession(page);
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  // The global shell nav renders independent of the home data query. On
  // compact viewports the nav panel is intentionally hidden behind the menu
  // disclosure, so assert structure here and visibility in viewport-specific
  // tests below.
  await expect(page.getByRole('navigation', { name: '메뉴' })).toHaveCount(1, {
    timeout: 15_000,
  });
}

async function documentOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test.describe('responsive shell — no document overflow', () => {
  for (const width of WIDTHS) {
    test(`no horizontal document overflow at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await gotoHome(page);
      expect(await documentOverflow(page), `${width}px closed`).toBeLessThanOrEqual(0);
    });
  }
});

test.describe('responsive routes — no document overflow at any band (M7.6)', () => {
  for (const width of WIDTHS) {
    test(`every route fits the document at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await injectAuthenticatedSession(page);
      const offenders: string[] = [];
      for (const route of ROUTES) {
        await page.goto(route, { waitUntil: 'domcontentloaded' });
        // The shell nav is the marker that the route chunk mounted; data
        // queries may still be pending or failing, which is fine — the loading
        // and error surfaces are part of what must fit.
        await expect(page.getByRole('navigation', { name: '메뉴' })).toHaveCount(1, {
          timeout: 15_000,
        });
        const overflow = await documentOverflow(page);
        if (overflow > 0) offenders.push(`${route} (+${overflow}px)`);
      }
      expect(offenders, `horizontal overflow at ${width}px`).toEqual([]);
    });
  }
});

test.describe('responsive shell — nav model', () => {
  test('desktop (1440) shows the grouped nav inline, toggle hidden', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoHome(page);
    await expect(page.getByRole('navigation', { name: '메뉴' })).toBeVisible();
    await expect(page.getByTestId('nav-menu-toggle')).toBeHidden();
    // A representative link from a non-settings group is visible inline. Scoped
    // to the nav panel + exact name so it never collides with the home-page
    // onboarding step "⑥ 성적서".
    await expect(
      page.getByTestId('nav-panel').getByRole('link', { name: '성적서 생성', exact: true }),
    ).toBeVisible();
    expect(await documentOverflow(page)).toBeLessThanOrEqual(0);
  });

  for (const width of [900, 390] as const) {
    test(`mobile/tablet (${width}) collapses nav behind an accessible disclosure`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 900 });
      await gotoHome(page);

      const toggle = page.getByTestId('nav-menu-toggle');
      const panelLink = page
        .getByTestId('nav-panel')
        .getByRole('link', { name: '성적서 생성', exact: true });

      // Collapsed by default — the toggle is the only visible nav affordance.
      await expect(toggle).toBeVisible();
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
      await expect(panelLink).toBeHidden();
      expect(await documentOverflow(page), `${width}px collapsed`).toBeLessThanOrEqual(0);

      // Open → links reachable, still no document overflow (dropdown overlays).
      await toggle.click();
      await expect(toggle).toHaveAttribute('aria-expanded', 'true');
      await expect(panelLink).toBeVisible();
      expect(await documentOverflow(page), `${width}px open`).toBeLessThanOrEqual(0);

      // Escape closes and returns focus to the toggle (disclosure a11y).
      await page.keyboard.press('Escape');
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
      await expect(toggle).toBeFocused();
    });
  }

  test('mobile menu closes after following a nav link', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await gotoHome(page);
    const toggle = page.getByTestId('nav-menu-toggle');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await page
      .getByTestId('nav-panel')
      .getByRole('link', { name: '성적서 생성', exact: true })
      .click();
    // Navigation collapses the dropdown (close-on-navigation).
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });
});

test.describe('responsive shell — breakpoint token SSOT', () => {
  test('the three-rung breakpoint scale carries the documented thresholds', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await gotoHome(page);
    const scale = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      return {
        sm: style.getPropertyValue('--bp-sm').trim(),
        md: style.getPropertyValue('--bp-md').trim(),
        lg: style.getPropertyValue('--bp-lg').trim(),
      };
    });
    expect(scale).toEqual({ sm: '640px', md: '768px', lg: '1024px' });
  });

  test('touch-target rungs are declared for the compact bands (M7.3)', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await gotoHome(page);
    const rungs = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      return {
        min: style.getPropertyValue('--touch-target-min').trim(),
        glove: style.getPropertyValue('--touch-target-glove').trim(),
      };
    });
    expect(rungs).toEqual({ min: '48px', glove: '56px' });
  });

  test('the density toggle survives the compact band (M7.4)', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await gotoHome(page);
    // Density matters MOST where space is scarcest; it used to be hidden here.
    await expect(page.locator('.density-toggle')).toBeVisible();
  });
});

/**
 * Ready-state responsive contract for the representative visual routes.
 *
 * The shell smoke above intentionally remains backend-independent, but an
 * overflow-only check can pass while a real workbench is still loading or has
 * lost its rail/table semantics. This matrix uses the same fail-closed,
 * deterministic fixture as the visual goldens and checks the structure that
 * must survive every responsive band: named landmarks, main/rail ordering,
 * wrapping surfaces, focus visibility, and effective hit areas.
 */
test.describe('responsive representative routes — ready structure (M5)', () => {
  test.describe.configure({ mode: 'serial' });

  for (const width of WIDTHS) {
    for (const route of VISUAL_ROUTE_DEFINITIONS) {
      test(`${route.key} preserves ready structure at ${width}px`, async ({ context, page }) => {
        const fixture = await installVisualFixture(context);
        await page.setViewportSize({ width, height: 900 });
        await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
        await page.addInitScript(() => {
          window.localStorage.setItem('fcc-theme', 'light');
          window.localStorage.setItem('fcc-density', 'comfortable');
          window.localStorage.setItem('fcc-locale', 'ko');
        });
        await injectAuthenticatedSession(page, { permissions: RESPONSIVE_PERMISSIONS });

        await page.goto(route.path, { waitUntil: 'domcontentloaded' });
        await assertVisualRouteReady(page, route);

        expect(fixture.pageErrors, `${route.key}: pageerror`).toEqual([]);
        expect(fixture.unexpectedConsoleErrors, `${route.key}: console.error`).toEqual([]);
        expect(fixture.unexpectedRequests, `${route.key}: unexpected request`).toEqual([]);
        await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);

        if (width < 1024) {
          const menuToggle = page.getByTestId('nav-menu-toggle');
          await expect(menuToggle).toBeVisible();
          await menuToggle.click();
          await expect(menuToggle).toHaveAttribute('aria-expanded', 'true');
        }
        const currentNav = page.locator('.app-nav .nav-link[aria-current="page"]:visible');
        expect(await currentNav.count(), `${route.key}: visible current nav item`).toBeGreaterThan(
          0,
        );
        expect((await currentNav.first().innerText()).trim()).not.toBe('');

        const layout = page.locator('.workbench-layout').first();
        await expect(layout).toBeVisible();
        const main = layout.locator(':scope > .workbench-layout__main');
        const rail = layout.locator(':scope > .workbench-layout__rail');
        await expect(main).toHaveCount(1);
        expect(((await main.getAttribute('aria-label')) ?? '').trim()).not.toBe('');

        const railCount = await rail.count();
        if (railCount > 0) {
          expect(((await rail.getAttribute('aria-label')) ?? '').trim()).not.toBe('');
        }

        const geometry = await layout.evaluate((element) => {
          const mainElement = element.querySelector<HTMLElement>(
            ':scope > .workbench-layout__main',
          );
          const railElement = element.querySelector<HTMLElement>(
            ':scope > .workbench-layout__rail',
          );
          if (mainElement === null) throw new Error('workbench main landmark missing');
          const mainRect = mainElement.getBoundingClientRect();
          const railRect = railElement?.getBoundingClientRect() ?? null;
          return {
            columns: getComputedStyle(element).gridTemplateColumns,
            main: { x: mainRect.x, y: mainRect.y, right: mainRect.right, bottom: mainRect.bottom },
            rail: railRect
              ? { x: railRect.x, y: railRect.y, right: railRect.right, bottom: railRect.bottom }
              : null,
          };
        });

        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
          ),
          `${route.key} at ${width}px: document overflow`,
        ).toBe(true);

        if (geometry.rail !== null) {
          if (width >= 1024) {
            expect(
              geometry.rail.x,
              `${route.key}: rail must follow main horizontally`,
            ).toBeGreaterThan(geometry.main.x);
            expect(
              geometry.columns.split(' ').length,
              `${route.key}: desktop two-column grid`,
            ).toBeGreaterThanOrEqual(2);
          } else {
            expect(
              geometry.rail.y,
              `${route.key}: rail must stack after main`,
            ).toBeGreaterThanOrEqual(geometry.main.bottom - 1);
            expect(
              geometry.columns.split(' ').length,
              `${route.key}: compact one-column grid`,
            ).toBe(1);
          }
        }

        const surfaceState = await layout.evaluate((element) => ({
          toolbarsWrap: [...element.querySelectorAll<HTMLElement>('.toolbar')].every(
            (toolbar) => getComputedStyle(toolbar).flexWrap === 'wrap',
          ),
          cardsCanShrink: [...element.querySelectorAll<HTMLElement>('.card')].every(
            (card) => getComputedStyle(card).minWidth === '0px',
          ),
          tableContainersStayInFlow: [
            ...element.querySelectorAll<HTMLElement>('.data-table-overflow'),
          ].every((container) => {
            const containerRect = container.getBoundingClientRect();
            const layoutRect = element.getBoundingClientRect();
            const overflowX = getComputedStyle(container).overflowX;
            return (
              containerRect.width <= layoutRect.width + 1 &&
              (overflowX === 'auto' || overflowX === 'scroll')
            );
          }),
        }));
        expect(surfaceState.toolbarsWrap, `${route.key}: toolbar wrap`).toBe(true);
        expect(surfaceState.cardsCanShrink, `${route.key}: card min-width`).toBe(true);
        expect(surfaceState.tableContainersStayInFlow, `${route.key}: table flow`).toBe(true);

        const focusables = await layout.evaluate((element) => {
          const visible = (candidate: HTMLElement): boolean => {
            const style = getComputedStyle(candidate);
            const rect = candidate.getBoundingClientRect();
            return (
              style.display !== 'none' &&
              style.visibility !== 'hidden' &&
              rect.width > 0 &&
              rect.height > 0
            );
          };
          return [
            ...element.querySelectorAll<HTMLElement>(
              'a[href], button, input, select, textarea, [tabindex]',
            ),
          ]
            .filter(
              (candidate) => candidate.getAttribute('tabindex') !== '-1' && visible(candidate),
            )
            .map((candidate) => ({
              area: candidate.closest('.workbench-layout__rail') !== null ? 'rail' : 'main',
              x: candidate.getBoundingClientRect().x,
              y: candidate.getBoundingClientRect().y,
            }));
        });
        if (railCount > 0) {
          const firstRail = focusables.findIndex((candidate) => candidate.area === 'rail');
          const lastMain = focusables.reduce(
            (last, candidate, index) => (candidate.area === 'main' ? index : last),
            -1,
          );
          if (firstRail >= 0 && lastMain >= 0) {
            expect(
              firstRail,
              `${route.key}: keyboard order enters rail after main`,
            ).toBeGreaterThan(lastMain);
            if (width < 1024) {
              expect(
                focusables[firstRail]?.y,
                `${route.key}: stacked focus order`,
              ).toBeGreaterThanOrEqual(focusables[lastMain]?.y ?? 0);
            } else {
              expect(focusables[firstRail]?.x, `${route.key}: desktop focus order`).toBeGreaterThan(
                geometry.main.x,
              );
            }
          }
        }

        const targetViolations = await page
          .locator(
            '.workbench-layout button, .workbench-layout a[href], .workbench-layout input, .workbench-layout select, .workbench-layout textarea',
          )
          .evaluateAll((elements, viewportWidth) => {
            const offenders: string[] = [];
            for (const element of elements) {
              const candidate = element as HTMLElement;
              const style = getComputedStyle(candidate);
              const rect = candidate.getBoundingClientRect();
              if (
                style.display === 'none' ||
                style.visibility === 'hidden' ||
                rect.width === 0 ||
                rect.height === 0
              ) {
                continue;
              }
              const pseudoHeight = Number.parseFloat(getComputedStyle(candidate, '::after').height);
              const effectiveHeight = Math.max(
                rect.height,
                Number.isFinite(pseudoHeight) ? pseudoHeight : 0,
              );
              const isTouchTarget = candidate.classList.contains('touch-target');
              const requiredHeight = isTouchTarget && viewportWidth < 768 ? 48 : 24;
              const inlineException = candidate.closest('table, .data-table-cards') !== null;
              if (!inlineException && (rect.width < 24 || effectiveHeight < requiredHeight)) {
                offenders.push(
                  `${candidate.tagName.toLowerCase()}.${candidate.className} ${rect.width}x${effectiveHeight}`,
                );
              }
              if (isTouchTarget && viewportWidth < 768 && effectiveHeight < requiredHeight) {
                offenders.push(
                  `${candidate.tagName.toLowerCase()}.${candidate.className} touch-target ${effectiveHeight}`,
                );
              }
            }
            return offenders;
          }, width);
        expect(targetViolations, `${route.key} at ${width}px: target size`).toEqual([]);

        await page.locator('.app-main').focus();
        let focusTargetReached = false;
        for (let attempt = 0; attempt < 32; attempt += 1) {
          await page.keyboard.press('Tab');
          focusTargetReached = await page.evaluate(
            () => document.activeElement?.closest('.workbench-layout__main') !== null,
          );
          if (focusTargetReached) break;
        }
        expect(focusTargetReached, `${route.key}: keyboard reaches workbench main`).toBe(true);
        if (focusTargetReached) {
          const focusState = await page.evaluate(() => {
            const element = document.activeElement;
            if (!(element instanceof HTMLElement))
              throw new Error('workbench focus target missing');
            element.scrollIntoView({ block: 'center', inline: 'nearest' });
            const rect = element.getBoundingClientRect();
            // At the desktop breakpoint `.app-header` is the persistent
            // sidebar, not a horizontal header that can obscure main content.
            const headerRect =
              window.innerWidth < 1024
                ? document.querySelector<HTMLElement>('.app-header')?.getBoundingClientRect()
                : undefined;
            const headerOverlap = headerRect
              ? rect.top < headerRect.bottom && rect.bottom > headerRect.top
              : false;
            const style = getComputedStyle(element);
            return {
              outlineStyle: style.outlineStyle,
              outlineWidth: Number.parseFloat(style.outlineWidth),
              headerOverlap,
            };
          });
          expect(focusState.outlineStyle, `${route.key}: focus appearance`).not.toBe('none');
          expect(focusState.outlineWidth, `${route.key}: focus appearance`).toBeGreaterThanOrEqual(
            2,
          );
          expect(focusState.headerOverlap, `${route.key}: focus not obscured by header`).toBe(
            false,
          );
        }
      });
    }
  }
});
