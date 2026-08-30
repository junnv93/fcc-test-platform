import { expect, test } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';

/**
 * Frontend smoke — split into two IdP-independent suites (FE-P7-E2E, 2026-05-29).
 *
 * The whole app is gated behind `<RequireAuth>` (src/app.tsx), so a bare
 * `goto('/')` no longer lands on Overview — without a session it redirects to
 * the IdP. These smokes therefore separate the two concerns:
 *
 *   1. unauthenticated boot — the auth gate engages and surfaces the IdP
 *      failure UX (no real IdP needed; the discovery fetch is mocked
 *      unreachable so the path is deterministic).
 *   2. authenticated shell — a synthetic, structurally-valid session is
 *      injected before boot (see helpers/auth-fixture.ts) so the shell + nav
 *      render. No real Keycloak dependency.
 *
 * The real OIDC PKCE round-trip lives in `auth-flow.spec.ts` /
 * `oidc-conformance.spec.ts` (opt-in, real Keycloak) — deliberately NOT mixed
 * into these shell-rendering smokes.
 */

const OIDC_DISCOVERY_GLOB = '**/.well-known/openid-configuration';

test.describe('unauthenticated boot', () => {
  test('engages the auth gate and surfaces the IdP failure UX', async ({ page }) => {
    // Make OIDC discovery deterministically unreachable so startLogin fails
    // fast → translateFlowError → 'idp_unreachable' failure view, instead of
    // depending on whether the configured issuer is reachable from CI.
    await page.route(OIDC_DISCOVERY_GLOB, (route) => route.abort());

    await page.goto('/');

    // The protected Overview must NOT render without a session.
    await expect(page.getByRole('heading', { name: '개요', level: 1 })).toHaveCount(0);
    // The auth failure view (failure-ui.tsx idp_unreachable) is shown instead.
    await expect(
      page.getByRole('heading', { name: '인증 서버에 접속할 수 없습니다' }),
    ).toBeVisible();
  });
});

test.describe('authenticated shell', () => {
  test('renders Home + primary nav with an injected session', async ({ page }) => {
    await injectAuthenticatedSession(page);

    await page.goto('/');

    // Auth gate passes → the Home ("오늘 할 일") renders (tester-ux Phase H).
    await expect(page.getByRole('heading', { name: '홈', level: 1 })).toBeVisible();
    await expect(page.getByTestId('home-steps')).toBeVisible();

    // The runtime/diagnostics surface moved to [설정]>진단 (Phase S-diag).
    await page.goto('/diagnostics');
    await expect(page.getByRole('heading', { name: '진단', level: 1 })).toBeVisible();
    await expect(page.getByTestId('env-name')).toHaveText(/development|test|staging|production/);
    await expect(page.getByTestId('build-version')).toBeVisible();
    await expect(page.getByTestId('api-base-url')).toBeVisible();

    // Back to the landing for the nav assertions (the shell nav is global).
    await page.goto('/');

    // Primary nav uses the real labels from routes/_layout.tsx NAV_GROUPS
    // (tester-ux redesign Phase N task-flow grouping: 홈 / 시험하기 / 결과 /
    // 설정). Group captions frame the link clusters; the links themselves carry
    // the tester-facing labels.
    const nav = page.getByRole('navigation', { name: '메뉴' });
    await expect(nav).toBeVisible();
    for (const groupLabel of ['홈', '시험하기', '결과', '설정']) {
      await expect(nav.locator('.app-nav__group-label', { hasText: groupLabel })).toBeVisible();
    }
    for (const label of ['홈', '원격 측정', '측정 작업', '측정 이력', '측정 현황', '성적서 생성']) {
      await expect(nav.getByRole('link', { name: label })).toBeVisible();
    }
  });
});
