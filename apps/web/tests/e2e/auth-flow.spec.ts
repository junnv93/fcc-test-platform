import { AxeBuilder } from '@axe-core/playwright';
import { expect, test, type Frame, type Page, type TestInfo } from '@playwright/test';

import {
  ALL_STORAGE_KEYS,
  STORAGE_KEY_NONCE,
  STORAGE_KEY_STATE,
  STORAGE_KEY_VERIFIER,
} from '../../src/auth/storage-keys';

import {
  AUTH_PATH,
  REAL_AUTH_EVIDENCE_BOUNDARY,
  loginWithRealKeycloak,
} from './helpers/real-auth-fixture';
import { assertVisualRouteReady, VISUAL_ROUTE_DEFINITIONS } from './helpers/visual-fixture';

const ROUTES = VISUAL_ROUTE_DEFINITIONS.filter(({ key }) =>
  ['home', 'my-projects', 'projects'].includes(key),
);

const CONDITIONS = [
  { name: 'mobile-light-comfortable', width: 390, theme: 'light', density: 'comfortable' },
  { name: 'tablet-dark-compact', width: 768, theme: 'dark', density: 'compact' },
  { name: 'desktop-light-comfortable', width: 1280, theme: 'light', density: 'comfortable' },
  { name: 'wide-desktop-dark-compact', width: 1440, theme: 'dark', density: 'compact' },
] as const;

const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

interface LogoutStorageWitness {
  readonly phase: 'pre-app-bootstrap' | 'fresh-auth-transaction';
  readonly pathname: string;
  readonly values: Readonly<Record<string, string | null>>;
}

async function applyConditionThroughVisibleControls(
  page: Page,
  condition: (typeof CONDITIONS)[number],
): Promise<void> {
  const themeToggle = page.getByRole('button', { name: /화면 테마|display theme/iu });
  const navMenuToggle = page.getByTestId('nav-menu-toggle');
  if (!(await themeToggle.isVisible()) && (await navMenuToggle.isVisible())) {
    // At the mobile/tablet breakpoints the shell moves the condition controls
    // into the disclosure nav. Open that user-facing surface before locating
    // its controls; the test must exercise the same responsive workflow as an
    // operator instead of reaching into hidden DOM.
    await navMenuToggle.click();
  }
  await expect(themeToggle).toBeVisible();
  const wantsDark = condition.theme === 'dark';
  if ((await themeToggle.getAttribute('aria-pressed')) !== String(wantsDark)) {
    await themeToggle.click();
  }
  await expect(themeToggle).toHaveAttribute('aria-pressed', String(wantsDark));

  const densityGroup = page.getByRole('group', { name: /화면 밀도|display density/iu });
  const densityButton = densityGroup.getByRole('button', {
    name: condition.density === 'compact' ? /압축|compact/iu : /기본|comfortable/iu,
  });
  await expect(densityButton).toBeVisible();
  if ((await densityButton.getAttribute('aria-pressed')) !== 'true') {
    await densityButton.click();
  }
  await expect(densityButton).toHaveAttribute('aria-pressed', 'true');

  await expect
    .poll(async () =>
      page.evaluate(() => ({
        themeAttribute: document.documentElement.getAttribute('data-theme'),
        densityAttribute: document.documentElement.getAttribute('data-density') ?? 'comfortable',
        themeStorage: window.localStorage.getItem('fcc-theme'),
        densityStorage: window.localStorage.getItem('fcc-density'),
      })),
    )
    .toEqual({
      themeAttribute: condition.theme,
      densityAttribute: condition.density,
      themeStorage: condition.theme,
      densityStorage: condition.density,
    });
}

async function assertProtectedRouteEvidence(
  page: Page,
  route: (typeof ROUTES)[number],
  condition: (typeof CONDITIONS)[number],
  testInfo: TestInfo,
): Promise<void> {
  await assertVisualRouteReady(page, route);
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
  const navMenuToggle = page.getByTestId('nav-menu-toggle');
  const primaryCurrent = page.locator(
    '.app-nav__group:not(.app-nav__group--project) [aria-current="page"]:visible',
  );
  if ((await primaryCurrent.count()) === 0 && (await navMenuToggle.isVisible())) {
    await navMenuToggle.click();
  }
  await expect(primaryCurrent).toHaveCount(1);

  const focusVisible = await page.evaluate(() => {
    const active = document.activeElement;
    return active instanceof HTMLElement && active.matches(':focus-visible');
  });
  if (!focusVisible) await page.keyboard.press('Tab');
  await expect
    .poll(async () =>
      page.evaluate(() => {
        const active = document.activeElement;
        return active instanceof HTMLElement && active.matches(':focus-visible');
      }),
    )
    .toBe(true);

  const axeResults = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  const blockingViolations = axeResults.violations.filter(
    (violation) => violation.impact === 'critical' || violation.impact === 'serious',
  );
  expect(
    blockingViolations.map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      nodes: violation.nodes.map((node) => node.target),
    })),
    `${condition.name}/${route.key}: Axe critical/serious violations`,
  ).toEqual([]);

  const browserEvidence = await page.evaluate(async () => {
    await document.fonts.ready;
    const images = await Promise.all(
      Array.from(document.images).map(async (image) => {
        if (!image.currentSrc) return true;
        if (!image.complete || image.naturalWidth === 0) {
          try {
            await image.decode();
          } catch {
            return false;
          }
        }
        return image.complete && image.naturalWidth > 0;
      }),
    );
    const bodyFamily = getComputedStyle(document.body).fontFamily;
    const headingFamily = document.querySelector('h1')
      ? getComputedStyle(document.querySelector('h1') as HTMLElement).fontFamily
      : '';
    const loadedKoreanFont = Array.from(document.fonts).some(
      (font) => font.status === 'loaded' && /Noto Sans KR/iu.test(font.family),
    );
    return {
      fontSettled: document.fonts.status === 'loaded',
      bodyFamily,
      headingFamily,
      loadedKoreanFont,
      imageFailures: images.filter((decoded) => !decoded).length,
      theme: window.localStorage.getItem('fcc-theme'),
      density: window.localStorage.getItem('fcc-density'),
      themeAttribute: document.documentElement.getAttribute('data-theme'),
      densityAttribute: document.documentElement.dataset.density ?? 'comfortable',
      hasOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
  });

  expect(browserEvidence.fontSettled, `${condition.name}/${route.key}: fonts.ready`).toBe(true);
  expect(browserEvidence.loadedKoreanFont, `${condition.name}/${route.key}: Korean font`).toBe(
    true,
  );
  expect(browserEvidence.bodyFamily, `${condition.name}/${route.key}: body font`).toMatch(
    /Noto Sans KR/iu,
  );
  expect(browserEvidence.headingFamily, `${condition.name}/${route.key}: heading font`).toMatch(
    /Noto Sans KR/iu,
  );
  expect(browserEvidence.imageFailures, `${condition.name}/${route.key}: image decoding`).toBe(0);
  expect(browserEvidence.theme).toBe(condition.theme);
  expect(browserEvidence.density).toBe(condition.density);
  expect(browserEvidence.themeAttribute).toBe(condition.theme);
  expect(browserEvidence.densityAttribute).toBe(condition.density);
  expect(browserEvidence.hasOverflow, `${condition.name}/${route.key}: horizontal overflow`).toBe(
    false,
  );

  await page.screenshot({
    path: testInfo.outputPath(`real-auth-${condition.name}-${route.key}.png`),
    fullPage: true,
  });
}

function assertAuthNetworkEvidence(
  session: Awaited<ReturnType<typeof loginWithRealKeycloak>>,
): void {
  expect(session.authorization.statePresent).toBe(true);
  expect(session.authorization.codeChallengePresent).toBe(true);
  expect(session.authorization.codeChallengeMethod).toBe('S256');
  expect(session.authorization.callbackObserved).toBe(true);
  expect(REAL_AUTH_EVIDENCE_BOUNDARY).toBe(
    'real IdP and SPA callback/route guard proven; backend authorization not claimed.',
  );

  const keycloakPaths = session.network.requests
    .filter((request) => request.kind === 'keycloak')
    .map((request) => request.pathname);
  expect(keycloakPaths).toContain('/realms/fcc-dev/.well-known/openid-configuration');
  expect(keycloakPaths).toContain('/realms/fcc-dev/protocol/openid-connect/auth');
  expect(keycloakPaths).toContain('/realms/fcc-dev/protocol/openid-connect/token');
  const families = new Set(
    session.network.requests
      .filter((request) => request.kind === 'keycloak' && request.family !== null)
      .map((request) => request.family),
  );
  expect([...families]).toEqual(
    expect.arrayContaining(['discovery', 'authorization', 'token', 'credential-submit']),
  );
  expect(
    session.network.keycloakResponses.every(
      (response) => response.status >= 200 && response.status < 400,
    ),
    'every observed Keycloak response must be accepted by the explicit taxonomy',
  ).toBe(true);
  expect(session.network.tokenEndpointRequests).toBeGreaterThan(0);
  expect(session.network.authorizationCodePkceRequests).toBeGreaterThan(0);
}

test.describe('OIDC PKCE login flow', () => {
  test.skip(
    process.env['E2E_OIDC'] !== '1',
    'E2E_OIDC=1 not set — real Keycloak coverage runs in the dedicated OIDC lane',
  );
  test.describe.configure({ mode: 'serial' });

  test('real Keycloak login/callback protects preview routes across the evidence matrix', async ({
    page,
    context,
  }, testInfo) => {
    // This is an intentionally serial 4-condition × 3-route evidence matrix:
    // every cell performs Axe, font/image settlement, and a full-page capture.
    // The default 30s Playwright budget is below the observed CI cost even
    // though the application and fixture are healthy, so keep the integration
    // budget explicit instead of letting a slow runner report a false flow
    // failure from inside the browser-evidence evaluation.
    test.setTimeout(120_000);
    const session = await loginWithRealKeycloak(page, context, {
      username: process.env['E2E_OIDC_USERNAME'] ?? 'operator',
      installDeterministicFixture: true,
    });
    assertAuthNetworkEvidence(session);
    const expectedAudience = process.env['E2E_OIDC_CLIENT_ID'] ?? 'fcc-platform-frontend';
    expect(session.claims.subject).toEqual(expect.any(String));
    expect(session.claims.audience).toContain(expectedAudience);
    expect(session.claims.roles.length).toBeGreaterThan(0);
    expect(session.claims.permissions.length).toBeGreaterThan(0);
    expect(session.fixture).not.toBeNull();

    for (const condition of CONDITIONS) {
      await page.setViewportSize({ width: condition.width, height: 900 });
      await page.emulateMedia({ colorScheme: condition.theme, reducedMotion: 'reduce' });

      for (const route of ROUTES) {
        await page.goto(route.path, { waitUntil: 'domcontentloaded' });
        await applyConditionThroughVisibleControls(page, condition);
        await assertProtectedRouteEvidence(page, route, condition, testInfo);

        const fixture = session.fixture;
        if (fixture === null) throw new Error('deterministic route fixture was not installed');
        expect(fixture.pageErrors, `${condition.name}/${route.key}: pageerror`).toEqual([]);
        expect(
          fixture.unexpectedConsoleErrors,
          `${condition.name}/${route.key}: console.error`,
        ).toEqual([]);
        expect(fixture.unexpectedRequests, `${condition.name}/${route.key}: network`).toEqual([]);
      }
    }

    await session.reconcileDeterministicFixtureNetwork();
    expect(session.network.unexpectedRequests, 'unexpected external requests').toEqual([]);
    expect(session.network.failedResponses, 'failed network responses').toEqual([]);
    expect(session.network.consoleErrors, 'unexpected console.error').toEqual([]);
    expect(session.network.pageErrors, 'unexpected pageerror').toEqual([]);
    expect(session.network.invalidFontResponses, 'non-app or non-WOFF2 app fonts').toEqual([]);
    const uniqueAppFontResponses = new Set(session.network.appFontResponses);
    expect(uniqueAppFontResponses.size).toBeGreaterThanOrEqual(1);
    // This ledger intentionally spans every route/theme/viewport in the auth
    // evidence matrix. The representative-route transfer budget is owned by
    // web-font-loading.spec.ts; applying that per-route budget to this
    // aggregate would reject legitimate unicode-range subsets loaded by later
    // routes.
    expect(
      session.network.apiResponses.length,
      'deterministic API response evidence',
    ).toBeGreaterThan(0);
    expect(
      session.network.apiResponses.every(
        (response) =>
          response.stage === 'post-fixture' && response.status >= 200 && response.status < 300,
      ),
      'all API responses must be successful post-fixture responses',
    ).toBe(true);
  });

  test('real Keycloak logout clears the browser session', async ({ page, context }) => {
    const session = await loginWithRealKeycloak(page, context, {
      username: process.env['E2E_OIDC_USERNAME'] ?? 'viewer',
      installDeterministicFixture: true,
    });
    await expect(page.getByTestId('header-user-menu')).toBeVisible();
    const fixture = session.fixture;
    if (fixture === null) throw new Error('deterministic route fixture was not installed');
    await session.reconcileDeterministicFixtureNetwork();
    expect(fixture.pageErrors, 'unexpected logout setup pageerror').toEqual([]);
    expect(fixture.unexpectedConsoleErrors, 'unexpected logout setup console.error').toEqual([]);
    expect(fixture.unexpectedRequests, 'unexpected logout setup request').toEqual([]);
    await page.evaluate(async () => {
      await document.fonts.ready;
    });
    await session.releaseDeterministicNetworkGuard();
    const appOrigin = new URL(page.url()).origin;
    const keycloakOrigin = new URL(session.authorization.issuer).origin;

    const preLogoutStorage = await page.evaluate((storageKeys) => {
      return Object.fromEntries(
        storageKeys.map((key) => [key, window.sessionStorage.getItem(key)]),
      );
    }, ALL_STORAGE_KEYS);
    const logoutWitnesses: LogoutStorageWitness[] = [];
    const logoutWitnessNavigationMarker = '__fcc-logout-witness-armed__';
    await context.exposeFunction(
      '__fccRecordLogoutStorageWitness',
      (witness: LogoutStorageWitness) => {
        logoutWitnesses.push(witness);
      },
    );
    await context.addInitScript({
      content: `
        (() => {
          const appOrigin = ${JSON.stringify(appOrigin)};
          const storageKeys = ${JSON.stringify(ALL_STORAGE_KEYS)};
          const transactionKeys = ${JSON.stringify([
            STORAGE_KEY_STATE,
            STORAGE_KEY_VERIFIER,
            STORAGE_KEY_NONCE,
          ])};
          const navigationMarker = ${JSON.stringify(logoutWitnessNavigationMarker)};
          const armedAtDocumentStart = window.name === navigationMarker;
          if (!armedAtDocumentStart) return;
          if (window.location.origin !== appOrigin || window.location.pathname !== '/') return;
          window.name = '';
          let boundaryRecorded = false;
          let freshRecorded = false;
          const capture = () => {
            const values = Object.fromEntries(
              storageKeys.map((key) => [key, window.sessionStorage.getItem(key)]),
            );
            const recorder = globalThis['__fccRecordLogoutStorageWitness'];
            if (typeof recorder !== 'function') return;
            if (!boundaryRecorded) {
              boundaryRecorded = true;
              void recorder({ phase: 'pre-app-bootstrap', pathname: window.location.pathname, values });
            }
            if (
              !freshRecorded &&
              transactionKeys.every((key) => values[key] !== null)
            ) {
              freshRecorded = true;
              void recorder({ phase: 'fresh-auth-transaction', pathname: window.location.pathname, values });
            }
          };
          const storageSetItem = Storage.prototype.setItem;
          Storage.prototype.setItem = function (key, value) {
            storageSetItem.call(this, key, value);
            if (this === window.sessionStorage && transactionKeys.includes(key)) capture();
          };
          capture();
          queueMicrotask(capture);
          const timer = window.setInterval(capture, 25);
          window.setTimeout(() => window.clearInterval(timer), 5_000);
        })();
      `,
    });

    const mainFrameNavigations: { origin: string; pathname: string }[] = [];
    const recordMainFrameNavigation = (frame: Frame): void => {
      if (frame !== page.mainFrame()) return;
      const url = new URL(frame.url());
      mainFrameNavigations.push({ origin: url.origin, pathname: url.pathname });
    };
    page.on('framenavigated', recordMainFrameNavigation);
    await page.evaluate((marker) => {
      window.name = marker;
    }, logoutWitnessNavigationMarker);
    await page.getByRole('button', { name: '로그아웃' }).click();
    // RP-Initiated Logout uses the captured id_token_hint, so Keycloak can
    // invalidate the correct IdP session and return without a re-auth screen.
    await expect
      .poll(() => mainFrameNavigations.some(({ origin }) => origin !== appOrigin), {
        timeout: 15_000,
      })
      .toBe(true);
    await expect
      .poll(
        () =>
          mainFrameNavigations.some(
            ({ origin, pathname }) => origin === appOrigin && pathname === '/',
          ),
        { timeout: 15_000 },
      )
      .toBe(true);
    // The protected root is the configured post-logout target and immediately
    // starts a fresh authorization transaction. Wait for that transaction to
    // settle at the IdP before judging the network ledger; otherwise the
    // still-pending discovery fetch is reported as a navigation-aborted
    // failure even though the requested route is taxonomy-allowed.
    await expect
      .poll(
        () =>
          mainFrameNavigations.some(
            ({ origin, pathname }) => origin === keycloakOrigin && pathname === AUTH_PATH,
          ),
        { timeout: 15_000 },
      )
      .toBe(true);
    page.off('framenavigated', recordMainFrameNavigation);
    // The protected root may immediately begin a fresh authorization-code
    // transaction after the explicit logout; its state/verifier/nonce are
    // transaction state, not an authenticated session. The persisted token is
    // the authoritative session boundary and must remain absent. The init
    // script observed the storage atomically before route-guard bootstrap, so
    // a later fresh state/verifier/nonce transaction cannot be mistaken for
    // stale pre-logout state.
    await expect.poll(() => logoutWitnesses.length).toBeGreaterThan(0);
    const boundary = logoutWitnesses.find((witness) => witness.phase === 'pre-app-bootstrap');
    expect(boundary, 'missing race-safe pre-reauth storage witness').toBeDefined();
    if (boundary === undefined) throw new Error('logout storage boundary witness missing');
    for (const key of ALL_STORAGE_KEYS) {
      expect(boundary.values[key], `stale ${key} survived RP logout`).toBeNull();
    }
    const freshTransaction = logoutWitnesses.find(
      (witness) => witness.phase === 'fresh-auth-transaction',
    );
    const authorizationStates = session.network.authorizationRequestStates;
    if (freshTransaction !== undefined) {
      for (const key of [STORAGE_KEY_STATE, STORAGE_KEY_VERIFIER, STORAGE_KEY_NONCE]) {
        const value = freshTransaction.values[key];
        expect(value, `fresh transaction key ${key} was not observed`).not.toBeNull();
        expect(value).not.toBe(preLogoutStorage[key]);
      }
      expect(authorizationStates.length).toBeGreaterThan(1);
      expect(authorizationStates.slice(1).some((state) => state !== authorizationStates[0])).toBe(
        true,
      );
    } else {
      expect(authorizationStates.length).toBe(1);
    }
    expect(session.network.unexpectedRequests, 'unexpected logout request').toEqual([]);
    expect(session.network.failedResponses, 'failed logout responses').toEqual([]);
    expect(session.network.consoleErrors, 'unexpected logout console.error').toEqual([]);
    expect(session.network.pageErrors, 'unexpected logout pageerror').toEqual([]);
  });
});
