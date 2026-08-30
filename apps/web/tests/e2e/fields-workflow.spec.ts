import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession, TEST_OPERATOR_PERMISSIONS } from './helpers/auth-fixture';

/**
 * W4-9 (부분, 2026-08-01) — `/fields` 워크플로 e2e. `my-projects-workflow.spec.ts` 와
 * 형태를 맞춘 대칭 스펙(같은 진입 흐름의 2단계 — 프로젝트 선택 다음이 분야 선택).
 *
 * 계약 노트(M2.2 조정): 실측 결과 `fields-available-count`/`fields-planned-count` 는 정적
 * `WORKBENCH_AREAS`(1/3) 파생이라 네트워크와 무관하다 — "카운트가 목 데이터에서 파생"이라는
 * 원 표현은 이 라우트엔 적용되지 않는다. 네트워크에서 실제로 파생되는 값은 `field-progress`
 * 배지(진행률 %)뿐이므로 그 표면으로 단언을 옮겼다.
 *
 * 계약 정정(M2.4, 2026-08-01 감독 세션): iter1 은 `fields.tsx` 가 `progress.isError` 를
 * 읽지 않아 오류 표면이 없다고 관측하고 4번 흐름을 축소했다. 감독 세션이 그 관측을
 * "검증할 것이 없으니 조항을 지운다"가 아니라 "표면을 만든다"로 정정 — `fields.tsx` 에
 * `@/ui/ErrorState` 재사용 배선을 추가했으므로(`field-progress-error` testid) 4번 흐름은
 * 이제 원래 계약대로 "오류가 실제로 보인다"를 단언한다.
 */

const PROJECT_ID = '22222222-2222-4222-8222-222222222222';
const PROGRESS_GLOB = `**/platform/projects/${PROJECT_ID}/progress`;

function progressRow(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    progress_area: 'unlicensed_conducted',
    progress_bucket_id: null,
    planned_minutes: 100,
    completed_minutes: 50,
    percent: 50,
    total_conditions: 4,
    priced_conditions: 4,
    unpriced_conditions: 0,
    unbucketable_conditions: 0,
    ...over,
  };
}

async function openWithoutProject(page: Page): Promise<void> {
  await injectAuthenticatedSession(page, { permissions: TEST_OPERATOR_PERMISSIONS });
  await page.goto('/fields');
  await expect(page.getByRole('heading', { name: '시험 분야 선택', level: 1 })).toBeVisible();
  await expect(page.getByTestId('fields-workbench-overview')).toBeVisible();
}

async function openWithProject(page: Page): Promise<void> {
  await injectAuthenticatedSession(page, { permissions: TEST_OPERATOR_PERMISSIONS });
  await page.goto(`/fields?project=${PROJECT_ID}`);
  await expect(page.getByRole('heading', { name: '시험 분야 선택', level: 1 })).toBeVisible();
  await expect(page.getByTestId('fields-workbench')).toBeVisible();
}

test.describe('Fields route — workbench area selection', () => {
  test('without a project context, guides to /my-projects instead of blocking', async ({
    page,
  }) => {
    await openWithoutProject(page);
    await expect(page.getByTestId('fields-no-project')).toBeVisible();
    const pick = page.getByTestId('fields-pick-project');
    await expect(pick).toBeVisible();
    await expect(pick).toHaveAttribute('href', '/my-projects');
    // The gated workbench must not render at all — no broken/empty card list
    // instead of the "pick a project" guidance.
    await expect(page.getByTestId('fields-workbench')).toHaveCount(0);
  });

  test('after picking a project, context + area cards render with a network-derived progress badge', async ({
    page,
  }) => {
    let sawProgressRequest = false;
    await page.route(PROGRESS_GLOB, async (route: Route) => {
      sawProgressRequest = true;
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify([progressRow({})]),
      });
    });

    await openWithProject(page);
    await expect(page.getByTestId('fields-project-context')).toHaveText(PROJECT_ID);
    await expect(page.getByTestId('field-card')).toHaveCount(4);
    // 정적 SSOT(WORKBENCH_AREAS) 파생 — 네트워크와 무관(위 계약 노트 참조).
    await expect(page.getByTestId('fields-available-count')).toHaveText('1');
    await expect(page.getByTestId('fields-planned-count')).toHaveText('3');
    // 실제로 fetch 된 값이 배지에 반영된다 — 목 응답 percent=50 → "50%" (formatPercent SSOT,
    // 경계 밴드 밖의 내부값이라 반올림 표시가 그대로 "50%"다).
    await expect(page.getByTestId('field-progress')).toContainText('50%');
    await expect.poll(() => sawProgressRequest).toBe(true);
  });

  test('a disabled (planned) field card is not interactive, not just visually muted', async ({
    page,
  }) => {
    await page.route(PROGRESS_GLOB, async (route: Route) => {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify([]),
      });
    });
    await openWithProject(page);

    const disabled = page.getByTestId('field-card-disabled').first();
    await expect(disabled).toBeVisible();
    await expect(disabled).toHaveAttribute('aria-disabled', 'true');
    // Structurally inert — no focusable/clickable link nested inside, not just
    // a styled-to-look-disabled control.
    await expect(disabled.locator('a')).toHaveCount(0);

    await disabled.click({ force: true });
    // Even a forced click must not navigate away — there is no handler to fire.
    await expect(page).toHaveURL(/\/fields/);
  });

  test('a progress-fetch failure does not blank the workbench or fabricate a percentage', async ({
    page,
  }) => {
    await page.route(PROGRESS_GLOB, async (route: Route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/problem+json',
        body: JSON.stringify({ title: 'unavailable', status: 503, code: 'service_unavailable' }),
      });
    });
    await openWithProject(page);
    // The card list still renders — no error-boundary blank screen ...
    await expect(page.getByTestId('field-card')).toHaveCount(4);
    // ... and the available card falls back to the pre-fetch availability
    // badge rather than fabricating a percentage from a failed read.
    await expect(page.getByTestId('field-status').first()).toBeVisible();
    await expect(page.getByTestId('field-progress')).toHaveCount(0);
    // ... and the failure itself is visible, not silent (M2.4 correction —
    // `fields.tsx` now reads `progress.isError` and renders `ErrorState`).
    await expect(page.getByTestId('field-progress-error')).toBeVisible();
  });
});
