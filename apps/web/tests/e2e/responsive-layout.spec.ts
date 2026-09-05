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

/**
 * The routes that render OUTSIDE the app shell — siblings of `/` in the router,
 * not children of `AppLayout` (`app.tsx`; the structural fact is
 * `RouteEntry.top_level` in `tests/support/frontend_route_registry.py`).
 *
 * ⚠️ 이 집합은 편의가 아니라 **이 게이트가 성립하기 위한 전제**다. 아래 스윕은
 * 셸 nav 를 「라우트 청크가 마운트됐다」의 표지로 쓰는데, 셸 밖 라우트에는 그
 * 랜드마크가 애초에 0개다 — 그래서 스윕은 `/login` 에서 15초를 기다리다 죽었고,
 * **오버플로를 한 번도 재지 못했다**(실측 2026-09-05: 여섯 폭 전부, 그리고 목록에서
 * `/login` 뒤에 오는 `/inventory`·`/membership`·`/providers`·`/chambers` 는 어느
 * 폭에서도 측정된 적이 없다). 위 목록의 주석은 이 사실을 이미 적고 있었다 —
 * "Outside the shell" — 그런데도 단언은 셸을 기다렸다. 빨간 게이트는 꺼진 게이트다.
 *
 * 이 집합이 라우터와 어긋나면 e2e 가 아니라
 * `TestResponsiveRouteCoverage::test_shell_less_routes_match_the_router` 가 빨개진다.
 * 새 라우트를 셸 밖에 등록한 사람이 그 사실을 여기서 다시 적기를 기억할 필요가 없다.
 */
const SHELL_LESS_ROUTES: ReadonlySet<string> = new Set(['/login', '/change-password']);

/**
 * Wait until the route chunk has mounted, then measure. There is no single
 * marker that works for every route — 실측 2026-09-05: `<main>` 은
 * `/equipment-lists`·`/reference-data` 에서 2개, `/test-plans` 에서 0개이고,
 * `<h1>` 은 `/test-plans` 에서 2개다. 그래서 표지는 **라우트가 셸 안이냐**라는
 * 구조 사실로 갈린다: 셸 안이면 셸 nav, 셸 밖이면 그 라우트 자신의 `<main>`
 * (두 화면 모두 자기 `<main>` 을 렌더한다 — `login.tsx` · `change-password.tsx`).
 */
async function awaitRouteMounted(page: Page, route: string): Promise<void> {
  const marker = SHELL_LESS_ROUTES.has(route)
    ? page.getByRole('main')
    : page.getByRole('navigation', { name: '메뉴' });
  await expect(marker, `${route}: route chunk did not mount`).toHaveCount(1, {
    timeout: 15_000,
  });
}

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
      // 이 테스트 하나가 라우트 **전부**를 직렬로 걷는다. 기본 30초 예산은 그
      // 걸음 수와 무관한 상수라 목록이 길어지면 조용히 모자란다 — 실측
      // 2026-09-05: 21개 라우트에 라우트당 ~2.5초로 ~53초가 든다.
      //
      // ⚠️ 이 예산을 올리는 것은 게이트를 느슨하게 하는 것이 **아니다**. 진짜
      // 행(hang) 방어는 걸음마다 이미 걸려 있다(`navigationTimeout: 15s` +
      // 아래 mount 표지 15s). 바깥 예산은 그 걸음들의 **합**이어야 하고,
      // 그러므로 상수가 아니라 목록 길이에서 파생돼야 한다.
      //
      // 이 결함은 전제 결함(셸 밖 라우트에 셸 nav 를 기다림)에 가려져 있었다:
      // 스윕이 16번째 라우트에서 예산 안에 죽었으므로 예산 부족은 관측될 수
      // 없었다. 하나를 고치면 다른 하나가 드러난다.
      test.setTimeout(ROUTES.length * 12_000);
      await page.setViewportSize({ width, height: 900 });
      await injectAuthenticatedSession(page);
      const offenders: string[] = [];
      for (const route of ROUTES) {
        await page.goto(route, { waitUntil: 'domcontentloaded' });
        // Data queries may still be pending or failing, which is fine — the
        // loading and error surfaces are part of what must fit.
        await awaitRouteMounted(page, route);
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

        // 계약은 **이름 붙은 랜드마크**이지 특정 CSS 클래스가 아니다. 이 매트릭스의
        // 라우트는 전부 자기 `<main>` 을 소유하고(`_layout.ROUTES_WITH_OWN_MAIN_LANDMARK`),
        // rail 은 선택이다 — `WorkbenchLayout` 은 그 계약을 만족시키는 **한 가지 방법**이지
        // 계약 자체가 아니다.
        //
        // ⚠️ 실측 2026-09-05: `my-projects` 가 rail 을 걷고 자기 `<main>` 을
        // `.my-projects-workbench__main` 으로 옮기자 이 블록만 빨갛게 됐다. 랜드마크도,
        // 그 라벨도, 오버플로도 전부 그대로였다 — 즉 게이트가 잡은 것은 **접근성 회귀가
        // 아니라 클래스 이름의 변경**이었다. 클래스에 묶인 단언은 같은 계약을 다른 조합으로
        // 만족시키는 라우트마다 거짓 빨강을 낸다. 그래서 role 로 내린다.
        const main = page.getByRole('main');
        await expect(main).toHaveCount(1);
        await expect(main).toBeVisible();
        // 워크벤치 컨테이너 = 랜드마크의 부모. 아래 표면·포커스·타겟 검사가 이 범위를 쓴다.
        const layout = main.locator('xpath=..');
        expect(((await main.getAttribute('aria-label')) ?? '').trim()).not.toBe('');

        // rail 은 main 의 **형제**여야 한다 — 페이지 아무 데나 있는 `<aside>` 가 아니라
        // 이 워크벤치의 보조 열이라는 것이 기하 단언의 전제다.
        const rail = main.locator('xpath=following-sibling::aside');
        const railCount = await rail.count();
        if (railCount > 0) {
          expect(((await rail.first().getAttribute('aria-label')) ?? '').trim()).not.toBe('');
        }

        const geometry = await main.evaluate((mainElement) => {
          const container = mainElement.parentElement;
          if (container === null) throw new Error('main landmark has no container');
          const railElement = container.querySelector<HTMLElement>(':scope > aside');
          const mainRect = mainElement.getBoundingClientRect();
          const railRect = railElement?.getBoundingClientRect() ?? null;
          return {
            columns: getComputedStyle(container).gridTemplateColumns,
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
              area: candidate.closest('aside') != null ? 'rail' : 'main',
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

        // ⚠️ 클래스로 고르면 `.workbench-layout` 을 쓰지 않는 라우트에서 **0개**가 잡히고,
        // 위반 목록이 비어 검사가 공허하게 통과한다. 랜드마크 범위에서 고른다.
        const targetViolations = await layout
          .locator('button, a[href], input, select, textarea')
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
          // 계약은 「키보드가 **main 랜드마크**에 닿는다」이지 특정 클래스가 아니다.
          // ⚠️ `?.` 와 `!== null` 을 함께 쓰면 activeElement 가 없을 때 `undefined !== null`
          //    이 참이 되어 **닿지 않았는데 닿았다고** 보고한다. `!= null` 로 둘 다 잡는다.
          focusTargetReached = await page.evaluate(
            () => document.activeElement?.closest('main') != null,
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
