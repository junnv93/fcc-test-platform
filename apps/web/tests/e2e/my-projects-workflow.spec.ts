import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession, TEST_OPERATOR_PERMISSIONS } from './helpers/auth-fixture';

/**
 * W4-9 (부분, 2026-08-01) — `/my-projects` 워크플로 e2e.
 *
 * `/my-projects` 는 비전 로드맵의 진입 흐름 첫 단계(로그인 → 프로젝트 선택/생성 →
 * 분야 → 플랜 → 측정)인데도 워크플로 e2e 가 없었다. a11y 스캔(`a11y.spec.ts`)이
 * 이 화면을 열긴 하지만 그것은 접근성 축이지 "흐름이 동작한다"의 증거가 아니다.
 *
 * `projects-workflow.spec.ts` / `chambers-workflow.spec.ts` 와 같은 형태:
 * `injectAuthenticatedSession` + `page.route` 네트워크 목, 실측 `data-testid` 재사용.
 *
 * 덮는 흐름 (계약 M1 최소 4종):
 *   1. 목록 렌더 — 목 항목 수 == 카드 수.
 *   2. 검색 필터링 — 서버측 `q` 요청 + 불일치 시 빈 상태(`projects-empty`, testid 기반).
 *   3. 생성 흐름 — 제출 시 실제 POST 요청(바디까지 확인).
 *   4. 오류 경로 — 5xx 목 응답이 `projects-error` 로 보인다(빈 화면 침묵 금지).
 */

const PROJECTS_GLOB = '**/platform/projects*';

function projectRow(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    project_id: 'proj-1',
    project_code: 'SM-S921U',
    model_name: 'SM-S921U',
    status: 'active',
    sample_count: 2,
    management_number: null,
    fcc_id: null,
    applicant_name: null,
    applicant_address: null,
    manufacturer: null,
    fcc_grantee_code: null,
    eut_description: null,
    test_standard: null,
    ...over,
  };
}

async function open(page: Page): Promise<void> {
  await injectAuthenticatedSession(page, { permissions: TEST_OPERATOR_PERMISSIONS });
  await page.goto('/my-projects');
  await expect(page.getByRole('heading', { name: '내 프로젝트', level: 1 })).toBeVisible();
  await expect(page.getByTestId('my-projects-workbench-overview')).toBeVisible();
  await expect(page.getByTestId('my-projects-workbench')).toBeVisible();
}

test.describe('My Projects route — model entry workbench', () => {
  test('renders project cards from the mock directory response', async ({ page }) => {
    const items = [
      projectRow({}),
      projectRow({ project_id: 'proj-2', project_code: 'SM-A556E', model_name: 'SM-A556E' }),
    ];
    await page.route(PROJECTS_GLOB, async (route: Route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(items),
      });
    });

    await open(page);
    await expect(page.getByTestId('project-card-list')).toBeVisible();
    await expect(page.getByTestId('project-card')).toHaveCount(items.length);
  });

  test('search narrows via the server-side `q` param and shows the filtered empty state on no match', async ({
    page,
  }) => {
    let sawSearchRequest = false;
    await page.route(PROJECTS_GLOB, async (route: Route) => {
      const request = route.request();
      if (request.method() !== 'GET') {
        await route.fallback();
        return;
      }
      const q = new URL(request.url()).searchParams.get('q');
      if (q === 'no-such-model') {
        sawSearchRequest = true;
        await route.fulfill({
          status: 200,
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify([]),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify([projectRow({})]),
      });
    });

    await open(page);
    await expect(page.getByTestId('project-card')).toHaveCount(1);

    await page.getByTestId('project-search').fill('no-such-model');
    // Server round-trip is the source of truth for "no match" — assert the
    // filtered empty state (testid, NOT the literal copy — a concurrent wave
    // edits that string) and that the debounced `q` request actually fired.
    await expect(page.getByTestId('projects-empty')).toBeVisible();
    await expect(page.getByTestId('project-card')).toHaveCount(0);
    await expect.poll(() => sawSearchRequest).toBe(true);
  });

  test('submitting the create panel sends a real POST create request', async ({ page }) => {
    // A boxed object (not a reassigned `let`) sidesteps a TypeScript control-flow
    // narrowing gap across closures (microsoft/TypeScript#9998) — reassigning a
    // `let` only from inside the route callback narrows the read below to `never`.
    const captured: { create: { method: string; body: Record<string, unknown> } | null } = {
      create: null,
    };
    await page.route(PROJECTS_GLOB, async (route: Route) => {
      const request = route.request();
      if (request.method() === 'POST') {
        captured.create = {
          method: request.method(),
          body: (request.postDataJSON() ?? {}) as Record<string, unknown>,
        };
        await route.fulfill({
          status: 201,
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(
            projectRow({ project_id: 'proj-new', project_code: 'SM-NEW1', model_name: 'SM-NEW1' }),
          ),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify([]),
      });
    });

    await open(page);
    await expect(page.getByTestId('my-projects-create-panel')).toBeVisible();
    // 폼은 기본으로 접혀 있다(2026-09-04) — 목록이 전폭을 쓰도록.
    await page.getByTestId('new-project-toggle').click();
    await page.getByTestId('new-project-model').fill('SM-NEW1');
    await page.getByTestId('new-project-management_number').fill('MGMT-NEW1');
    await page.getByTestId('new-project-applicant_name').fill('ACME Corp.');
    await page.getByTestId('new-project-submit').click();

    // "버튼이 있다"가 아니라 "요청이 성공한다" — success 표면 + 실제 전송된 요청을 함께 본다.
    await expect(page.getByTestId('new-project-success')).toBeVisible();
    expect(captured.create?.method).toBe('POST');
    expect(captured.create?.body.model_name).toBe('SM-NEW1');
    // 필수 3칸이 실제로 실려 나간다 — 화면의 필수 표시가 계약과 같은 것을 말한다.
    expect(captured.create?.body.management_number).toBe('MGMT-NEW1');
    expect(captured.create?.body.applicant_name).toBe('ACME Corp.');
  });

  test('surfaces a backend 5xx as a visible error, not a silent blank list', async ({ page }) => {
    await page.route(PROJECTS_GLOB, async (route: Route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 503,
        contentType: 'application/problem+json',
        body: JSON.stringify({ title: 'unavailable', status: 503, code: 'service_unavailable' }),
      });
    });

    await open(page);
    await expect(page.getByTestId('projects-error')).toBeVisible();
    await expect(page.getByTestId('project-card-list')).toHaveCount(0);
  });
});
