import { AxeBuilder } from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { injectAuthenticatedSession, TEST_ADMIN_PERMISSIONS } from './helpers/auth-fixture';
import { messageAt } from './helpers/locale-messages';
import {
  assertVisualRouteReady,
  installVisualFixture,
  VISUAL_ROUTE_DEFINITIONS,
  WAVE_1_VISUAL_ROUTE_DEFINITIONS,
} from './helpers/visual-fixture';

/**
 * Sprint S2-EXT-1 — first external-audit-roadmap.md tool actually wired
 * into CI gates. axe-core + Playwright is the industry-standard a11y
 * scan for SPAs (used by GitHub, Stripe, Linear, etc.).
 *
 * Ratchet policy: zero `critical` AND zero `serious` violations.
 * Moderate / minor violations are tracked in the report but do not
 * block the PR — they accumulate into tech-debt that future sprints
 * pay down.
 *
 * W4-A T6 (2026-07-31) — the scan used to cover ONE address
 * (`/auth/callback`), on the since-invalidated premise that the authenticated
 * shell needs a live Keycloak. `helpers/auth-fixture::injectAuthenticatedSession`
 * removed that premise (13 specs already boot the shell with it), so every
 * operator route is scanned here. A route that is never scanned cannot support
 * an accessibility claim.
 *
 * To run locally: `cd apps/web && npm run build && npm run test:e2e -- a11y.spec.ts`
 * (`npm run build` is NOT optional — the Playwright webServer serves `dist/`,
 * so skipping it scans a stale bundle and passes for the wrong reason.)
 */

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

/**
 * Every operator address the axe scan covers, mapped to the i18n key of the
 * `<h1>` that the route's OWN module renders (`<PageHeader title={t(key)}>`).
 * This is the ONE hand-written route list in this file.
 *
 * Two things are sealed against `src/app.tsx` by
 * `tests/test_frontend_architecture_conformance.py::TestAxeScanCoversEveryRegisteredRoute`:
 *  1. the key SET equals the router registration set — a new route cannot land
 *     unscanned;
 *  2. each VALUE equals that route's `handle.titleKey` AND the key its module
 *     actually passes to `PageHeader` — so the sentinel below is that screen's
 *     real heading, not a string that merely looks plausible.
 *
 * Why a heading and not the shell nav: the nav belongs to `AppLayout`, which
 * renders for every address. Waiting on it proves the SHELL booted, which is
 * true even when the `<Outlet/>` slot is still a Suspense fallback, an error
 * boundary, or a redirect — and axe would then scan a screen that is not the
 * one under test and report zero violations for the wrong reason. The `<h1>`
 * ships inside the lazily-loaded route chunk, so its presence is the first
 * moment the route's own content is in the document.
 *
 * ── Deliberate exclusions (recorded here, next to the list, so an omission is
 *    a decision rather than an oversight; the seal above names each one):
 *  · `grid-poc` — dev-only ADR-0008 PoC behind `VITE_GRID_POC=1` and its own
 *    Playwright config (`playwright.grid-poc.config.ts`), which runs its own
 *    axe assertion. It is not reachable in the build this project serves.
 *  · `*` (NotFound) — a wildcard, not an address. Any unrouted URL renders it;
 *    it carries no unique surface beyond the shell already scanned at `/`.
 */
const OPERATOR_ROUTE_HEADINGS: Readonly<Record<string, string>> = {
  '/': 'routes.home.title',
  '/chambers': 'routes.chambers.pageTitle',
  '/my-projects': 'routes.myProjects.title',
  '/inventory': 'routes.sampleInventory.pageTitle',
  '/fields': 'routes.fields.title',
  '/progress': 'routes.progress.title',
  '/test-plans': 'routes.testPlans.pageTitle',
  '/projects': 'routes.projects.title',
  '/providers': 'routes.providers.pageTitleBrand',
  '/membership': 'routes.membership.pageTitle',
  '/control': 'routes.control.pageTitle',
  '/reports': 'routes.reports.page.title',
  '/test-reports': 'routes.testReports.title',
  '/equipment-lists': 'routes.equipmentLists.title',
  '/reference-data': 'routes.referenceData.title',
  '/artifact-custody': 'routes.artifactCustody.title',
  '/sessions': 'routes.sessions.title',
  '/jobs': 'routes.jobs.page.title',
  '/diagnostics': 'routes.diagnostics.title',
};

/**
 * `/auth/callback` renders OUTSIDE the app shell (no primary nav, no session,
 * no `PageHeader`), so it is scanned by the two failure-view tests below rather
 * than the operator sweep — its reach evidence is the `role="alert"` those
 * tests already wait for. It is still counted as covered by the seal.
 */
const AUTH_CALLBACK_ROUTE = '/auth/callback';

/**
 * 신원 축 EMS 정합 (2026-08-21) — the two local-auth screens.
 *
 * Same shape as `/auth/callback`: they render OUTSIDE the app shell (no primary
 * nav, no `PageHeader`), so the operator sweep's reach evidence — a `PageHeader`
 * `<h1>` inside the route chunk — does not apply. They are scanned by the
 * dedicated tests below instead, each waiting on its own heading before axe runs.
 *
 * ⚠️ Scanning them is not optional. `/login` is the ONLY screen on a
 * plaintext-HTTP deployment, and `/change-password` is the only screen a forced
 * password change can reach. An unscanned accessibility claim about those two is
 * a claim about the screens nobody can get past.
 */
const LOCAL_AUTH_ROUTES: readonly string[] = ['/login', '/change-password'];

/**
 * Rule-scoped, route-scoped exemptions. NOT a way to stop scanning something —
 * the route stays in the sweep and every other rule still blocks on it
 * (contract §3.4: shrinking the scan is concealment, not coverage).
 *
 * Two mechanical properties keep this honest:
 *  1. Self-cleaning ratchet — an entry whose rule no longer fires on its route
 *     FAILS the test ("delete me"). The ratchet is a property of the run, not a
 *     baseline number somebody has to remember to lower.
 *  2. Every entry carries a concrete reason, sealed by
 *     `tests/test_frontend_architecture_conformance.py::TestA11yAllowlistIsAJustifiedRatchet`.
 */
interface A11yAllowlistEntry {
  readonly route: string;
  readonly ruleId: string;
  readonly reason: string;
}

const A11Y_ALLOWLIST: readonly A11yAllowlistEntry[] = [];

/**
 * A violation message has to name the OFFENDING NODES, not just the rule. A
 * rule id alone ("color-contrast on 2 nodes") tells whoever reads the CI log
 * nothing about where to look, and the reflex is then to allowlist rather than
 * fix — the message shape decides which of the two is cheaper.
 */
function describeViolation(violation: {
  id: string;
  impact?: string | null;
  description: string;
  nodes: readonly { target: readonly unknown[]; failureSummary?: string }[];
}): string {
  const targets = violation.nodes
    .map(
      (node) =>
        `\n    · ${node.target.join(' ')}${node.failureSummary ? ` — ${node.failureSummary.replace(/\s+/g, ' ')}` : ''}`,
    )
    .join('');
  return `[${violation.impact ?? 'unknown'}] ${violation.id}: ${violation.description} (${
    violation.nodes.length
  } node${violation.nodes.length === 1 ? '' : 's'})${targets}`;
}

/**
 * Scan the CURRENT page and assert the blocking ratchet for `route`.
 *
 * `label` is what shows up in the failure message; `route` is the allowlist
 * key, so the two stay distinguishable (the callback address has two labels).
 */
async function runAxeAndAssertBlocking(page: Page, route: string, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  const critical = results.violations.filter((v) => v.impact === 'critical');
  const serious = results.violations.filter((v) => v.impact === 'serious');
  const blocking = [...critical, ...serious];
  if (blocking.length > 0) {
    console.error(
      `a11y violations (${label}) — block on critical+serious:\n${blocking
        .map(describeViolation)
        .join('\n')}`,
    );
  }

  const allowedForRoute = A11Y_ALLOWLIST.filter((entry) => entry.route === route);
  const allowedRuleIds = new Set(allowedForRoute.map((entry) => entry.ruleId));
  const firedRuleIds = new Set(blocking.map((v) => v.id));

  expect(
    blocking.filter((v) => !allowedRuleIds.has(v.id)).map(describeViolation),
    `${label}: WCAG critical/serious violations`,
  ).toEqual([]);

  // Self-cleaning ratchet — an exemption that no longer applies must be removed
  // so the allowlist can only shrink on its own.
  expect(
    allowedForRoute.filter((entry) => !firedRuleIds.has(entry.ruleId)).map((entry) => entry.ruleId),
    `${label}: allowlisted rules that no longer fire — delete these entries`,
  ).toEqual([]);
}

test.describe('local-auth screens — a11y (identity axis, 2026-08-21)', () => {
  // ⚠️ DERIVED from LOCAL_AUTH_ROUTES, not a second hand-written list. The Python
  // seal reads that same const to decide which routes may be exempt from the
  // operator sweep; if this loop enumerated its own paths, adding a route to the
  // const would exempt it from the sweep AND leave it unscanned here — a
  // coverage hole that both lists would report as fine.
  for (const route of LOCAL_AUTH_ROUTES) {
    test(`${route} — zero critical/serious violations`, async ({ page }) => {
      await page.goto(route, { waitUntil: 'domcontentloaded' });
      // Reach evidence: the route's OWN heading, not the shell's. These screens
      // render outside the shell, so there is no nav to mistake for arrival.
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible({
        timeout: 10_000,
      });
      await runAxeAndAssertBlocking(page, route, `local auth ${route}`);
    });
  }
});

test.describe('a11y axe-core scan (Sprint S2-EXT-1)', () => {
  // Sprint S2-ε ε-1 — `/auth/callback?error=…` bypasses RequireAuth
  // entirely and renders OidcFailureView synchronously. This is the
  // most stable surface for unauthenticated a11y assertion (no React
  // effect race, no IdP discovery dependency).

  test('Auth-callback IdP error failure view — zero critical/serious violations', async ({
    page,
  }) => {
    await page.goto(`${AUTH_CALLBACK_ROUTE}?error=access_denied`, {
      waitUntil: 'domcontentloaded',
    });
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 10_000 });
    await runAxeAndAssertBlocking(page, AUTH_CALLBACK_ROUTE, 'auth-callback IdP error');
  });

  test('Auth-callback state-mismatch failure view — zero critical/serious violations', async ({
    page,
  }) => {
    // No state in sessionStorage → OidcFlowError('state_mismatch') →
    // AuthCallbackRoute renders OidcFailureView (kind=token_expired).
    await page.goto(`${AUTH_CALLBACK_ROUTE}?code=stub&state=mismatch`, {
      waitUntil: 'domcontentloaded',
    });
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 10_000 });
    await runAxeAndAssertBlocking(page, AUTH_CALLBACK_ROUTE, 'auth-callback state-mismatch');
  });
});

test.describe('a11y axe-core scan — operator routes (W4-A T6)', () => {
  for (const [route, titleKey] of Object.entries(OPERATOR_ROUTE_HEADINGS)) {
    test(`${route} — zero critical/serious violations`, async ({ page }) => {
      // Admin permissions so permission-gated surfaces render their INTENDED
      // screen rather than a denial state (a denial page is a different, and
      // much smaller, thing to scan).
      await injectAuthenticatedSession(page, { permissions: TEST_ADMIN_PERMISSIONS });
      await page.goto(route, { waitUntil: 'domcontentloaded' });
      // Shell boot — separated from route reach below so a failure says WHICH
      // of the two did not happen (auth/layout vs. the route chunk itself).
      await expect(
        page.getByRole('navigation', { name: messageAt('routes.layout.navMenuToggle') }),
      ).toHaveCount(1, {
        timeout: 15_000,
      });
      // Route reach — this route module's OWN <h1>. Scanning before it exists
      // would measure the Suspense fallback, an error boundary or a redirect
      // and call the result "0 violations" for this address.
      await expect(
        page.getByRole('heading', { level: 1, name: messageAt(titleKey), exact: true }),
      ).toBeVisible({ timeout: 15_000 });
      await runAxeAndAssertBlocking(page, route, `operator route ${route}`);
    });
  }
});

test.describe('a11y axe-core scan — deterministic representative routes', () => {
  const VISUAL_A11Y_PERMISSIONS = [
    ...TEST_ADMIN_PERMISSIONS,
    'test_plan:read',
    'test_plan:author',
  ] as const;

  for (const route of VISUAL_ROUTE_DEFINITIONS) {
    test(`${route.key} — ready-state zero critical/serious violations`, async ({
      context,
      page,
    }) => {
      await installVisualFixture(context);
      await injectAuthenticatedSession(page, { permissions: VISUAL_A11Y_PERMISSIONS });
      await page.goto(route.path, { waitUntil: 'domcontentloaded' });
      await expect(
        page.getByRole('navigation', { name: messageAt('routes.layout.navMenuToggle') }),
      ).toHaveCount(1, {
        timeout: 15_000,
      });
      await assertVisualRouteReady(page, route);
      await runAxeAndAssertBlocking(page, route.path, `representative route ${route.key}`);
    });
  }
});

test.describe('a11y axe-core scan — Wave 1 bilingual canonical routes', () => {
  const VISUAL_A11Y_PERMISSIONS = [
    ...TEST_ADMIN_PERMISSIONS,
    'test_plan:read',
    'test_plan:author',
  ] as const;

  for (const route of WAVE_1_VISUAL_ROUTE_DEFINITIONS) {
    for (const locale of ['ko', 'en'] as const) {
      test(`${route.key} / ${locale} — zero critical/serious violations`, async ({
        context,
        page,
      }) => {
        await installVisualFixture(context);
        await page.addInitScript((selectedLocale) => {
          window.localStorage.setItem('fcc-locale', selectedLocale);
        }, locale);
        await injectAuthenticatedSession(page, { permissions: VISUAL_A11Y_PERMISSIONS });
        await page.goto(route.path, { waitUntil: 'domcontentloaded' });
        await expect(
          page.getByRole('navigation', {
            name: messageAt('routes.layout.navMenuToggle', locale),
          }),
        ).toHaveCount(1, {
          timeout: 15_000,
        });
        await assertVisualRouteReady(page, route);
        await expect(page.locator('html')).toHaveAttribute('lang', locale);
        await expect(
          page.getByRole('heading', {
            level: 1,
            name: messageAt(route.titleKey, locale),
            exact: true,
          }),
        ).toBeVisible({ timeout: 15_000 });
        await runAxeAndAssertBlocking(page, route.path, `Wave 1 ${route.key}/${locale}`);
      });
    }
  }
});
