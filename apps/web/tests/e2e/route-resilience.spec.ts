import { expect, test } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';

/**
 * W4-A T6 follow-through (2026-07-31) — the two claims iterations 2 and 3 could
 * NOT verify in jsdom and explicitly deferred to this milestone.
 *
 * Both are real-browser-only. vitest asserts the boundary/focus wiring against
 * a synthetic throw and a synthetic `activeElement`; neither of those exercises
 * the mechanism that actually fails in production:
 *
 *  1. A code-split chunk that 404s after a deploy. `React.lazy` rejects during
 *     render — the failure arrives on a network path jsdom does not have, and
 *     "the route boundary catches a thrown error" is a strictly weaker claim
 *     than "the route boundary catches a chunk that never arrived".
 *  2. `:focus-visible`. jsdom implements neither the pseudo-class nor the
 *     heuristic behind it, so the assertion "moving focus does not draw a ring
 *     for mouse users" was unverifiable there — and it is exactly the assertion
 *     that decides whether route-change focus management is invisible (correct)
 *     or a flashing outline on every navigation (a regression users notice).
 */

test.describe('route resilience — a chunk that never arrives (iter 2 §6)', () => {
  test('a failed route chunk surfaces in the route boundary and the shell survives', async ({
    page,
  }) => {
    await injectAuthenticatedSession(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('navigation', { name: '메뉴' })).toHaveCount(1);

    // Fail one route's chunk specifically — it has not been requested yet, so
    // this reproduces the post-deploy case (the tab holds a stale index, the
    // chunk it names is gone) rather than a route that was already resident.
    await page.route('**/assets/jobs-*.js', (route) => route.abort('failed'));

    await page
      .getByTestId('nav-panel')
      .getByRole('link', { name: '측정 작업', exact: true })
      .click();

    // ROUTE layer caught it …
    await expect(page.getByTestId('route-error-fallback')).toBeVisible({ timeout: 15_000 });
    // … and NOT the shell layer (that one replaces the whole chrome).
    await expect(page.getByTestId('shell-error-fallback')).toHaveCount(0);
    // … so the operator still has the app around the hole. This is the entire
    // point of the three-layer split: one broken screen must not take the nav.
    await expect(page.getByRole('navigation', { name: '메뉴' })).toBeVisible();

    // No stack trace leaked into the page (contract M2 / §3.7).
    await expect(page.getByTestId('route-error-fallback')).not.toContainText('at ');

    // Recovery is navigation, not reload: react-router's RenderErrorBoundary
    // clears the error on the next location change.
    await page
      .getByTestId('nav-panel')
      .getByRole('link', { name: '성적서 생성', exact: true })
      .click();
    await expect(page.getByTestId('route-error-fallback')).toHaveCount(0);
    await expect(page).toHaveURL(/\/reports$/);
  });
});

test.describe('route resilience — focus after a route change (iter 3 §4)', () => {
  test('focus lands on the main column without drawing a focus ring', async ({ page }) => {
    await injectAuthenticatedSession(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('navigation', { name: '메뉴' })).toHaveCount(1);

    // Click (pointer input) — the interaction mode that makes `:focus-visible`
    // meaningful. After a keyboard-driven navigation a ring WOULD be correct.
    await page
      .getByTestId('nav-panel')
      .getByRole('link', { name: '측정 이력', exact: true })
      .click();
    await expect(page).toHaveURL(/\/sessions$/);

    const focus = await page.evaluate(() => {
      const active = document.activeElement as HTMLElement | null;
      const main = document.getElementById('content');
      return {
        activeIsMain: active !== null && active === main,
        activeId: active?.id ?? null,
        activeTag: active?.tagName.toLowerCase() ?? null,
        // The heuristic itself — programmatic focus on a `tabIndex={-1}`
        // container after a pointer interaction must NOT match.
        matchesFocusVisible: main?.matches(':focus-visible') ?? null,
        outlineStyle: main ? getComputedStyle(main).outlineStyle : null,
      };
    });

    expect(focus.activeIsMain, `activeElement was ${focus.activeTag}#${focus.activeId}`).toBe(true);
    expect(
      focus.matchesFocusVisible,
      'the main column matched :focus-visible after a pointer navigation — every ' +
        'click on a nav link would flash an outline around the whole page',
    ).toBe(false);
    expect(focus.outlineStyle, 'a static outline is drawn regardless of :focus-visible').toBe(
      'none',
    );
  });
});
