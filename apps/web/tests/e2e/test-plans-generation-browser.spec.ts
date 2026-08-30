/**
 * Test-plan generation browser contract — MOCKED, deliberately.
 *
 * Split out of `test-plans-live.spec.ts` (2026-08-24). These tests intercept the
 * typed routes with a deterministic 16k-row server fixture so the 16k budgets
 * stay assertable without a live SQLite process. That is a legitimate thing to
 * test, but it is not a live lane, and while it lived in a file named `*-live`
 * the glob that derives the live lane set was counting it as one.
 *
 * If you are looking for the live smoke, it is in `test-plans-live.spec.ts`.
 */
import { AxeBuilder } from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession, TEST_OPERATOR_PERMISSIONS } from './helpers/auth-fixture';
import {
  GENERATION_BROWSER_CACHE_PAGE_LIMIT,
  GENERATION_BROWSER_CATALOGUE,
  GENERATION_BROWSER_DOM_ROW_LIMIT,
  GENERATION_BROWSER_IDLE_TIMEOUT_MS,
  GENERATION_BROWSER_LIMITS,
  GENERATION_BROWSER_PAGE_SIZE,
  GENERATION_BROWSER_SEEDED_ROWS,
} from './helpers/test-plan-generation-fixture';

// Current-generation browser contract. These tests use the same typed API
// routes as the live smoke, but keep a deterministic 16k-row server fixture so
// the assertions remain executable in the browser harness without a live
// SQLite process. The generic visual fixture is intentionally not involved.
const GENERATION_BROWSER_PROJECT_ID = '33333333-3333-4333-8333-333333333333';
const GENERATION_BROWSER_DRAFT_ID = 'browser-generated-draft';
const GENERATION_BROWSER_JOB_ID = 'browser-generation-job';

type BrowserGenerationStatus = 'queued' | 'succeeded' | 'failed';
type BrowserGenerationRow = Record<string, unknown>;

interface BrowserGenerationRowPage {
  draft_id: string;
  rows: BrowserGenerationRow[];
  next_after_draft_row_id: number | null;
}

interface BrowserGenerationState {
  status: BrowserGenerationStatus;
  completeOnSubmit: boolean;
  failOnSubmit: boolean;
  submitted: number;
  previewRequests: Record<string, unknown>[];
  rowLimitRequests: number[];
  rowAfterRequests: number[];
  payloadRowCounts: number[];
  draftStatus: 'draft' | 'published';
  validationCalls: number;
  publishCalls: number;
  readonly seededRows: number;
}

function browserPreview(stage = 'base'): Record<string, unknown> {
  return {
    request_sha256: 'a'.repeat(64),
    production_matrix: {
      purpose: 'production',
      revision: 'db-browser-16k',
      sha256: 'b'.repeat(64),
    },
    production_estimate: {
      exact_count: GENERATION_BROWSER_SEEDED_ROWS,
      lower_bound: GENERATION_BROWSER_SEEDED_ROWS,
      exceeds_limit: false,
      direct_count: GENERATION_BROWSER_SEEDED_ROWS,
      derived_count: 0,
    },
    representative_matrix: {
      purpose: 'representative',
      revision: `representative:${stage}`,
      sha256: 'c'.repeat(64),
    },
    representative_sample: [],
    catalogue_revision: stage === 'base' ? 'catalogue:browser-bt' : 'catalogue:browser-wlan',
    catalogue_sha256: 'd'.repeat(64),
    policy_revision: 'policy:browser',
    policy_sha256: 'e'.repeat(64),
    fingerprint: 'f'.repeat(64),
  };
}

function browserRowPage(after: number | undefined): BrowserGenerationRowPage {
  const start = after === undefined ? 1 : after + 1;
  return {
    draft_id: GENERATION_BROWSER_DRAFT_ID,
    rows: Array.from({ length: GENERATION_BROWSER_PAGE_SIZE }, (_unused, index) => ({
      draft_row_id: start + index,
      row_seq: start + index - 1,
      capability_path: ['BT', 'BR', '2.4G', `BT|2.4GHz|1MHz|${start + index}`, '1M'],
      origin: 'generated',
      generation_key: `generation-key-${start + index}`,
      generated_from_capability: 'BT/BR',
      antenna: 'ANT1',
      location: null,
      mode_family: 'SISO',
      test_type: 'Pk power',
      tone: null,
      derived_kind: null,
      scope_revision: 1,
      condition_hash: null,
    })),
    next_after_draft_row_id: after === undefined ? GENERATION_BROWSER_PAGE_SIZE : null,
  };
}

function newBrowserGenerationState(
  options: { completeOnSubmit?: boolean; failOnSubmit?: boolean } = {},
): BrowserGenerationState {
  return {
    status: 'queued',
    completeOnSubmit: options.completeOnSubmit === true,
    failOnSubmit: options.failOnSubmit === true,
    submitted: 0,
    previewRequests: [],
    rowLimitRequests: [],
    rowAfterRequests: [],
    payloadRowCounts: [],
    draftStatus: 'draft',
    validationCalls: 0,
    publishCalls: 0,
    seededRows: GENERATION_BROWSER_SEEDED_ROWS,
  };
}

async function mockGenerationBrowser(page: Page, state: BrowserGenerationState): Promise<void> {
  await page.addInitScript(() => {
    const supported = PerformanceObserver.supportedEntryTypes.includes('longtask');
    (
      window as Window & { __fccLongTaskObserverInstalled?: boolean }
    ).__fccLongTaskObserverInstalled = supported;
    if (!supported) return;
    const observer = new PerformanceObserver(() => undefined);
    observer.observe({ type: 'longtask', buffered: true });
  });

  await page.route('**/platform/projects**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          project_id: GENERATION_BROWSER_PROJECT_ID,
          project_code: 'BROWSER-16K',
          model_name: 'BROWSER-16K',
          customer: null,
          manufacturer: null,
          management_number: 'M-16K',
          status: 'active',
          sample_count: 0,
        },
      ]),
    });
  });

  await page.route('**/headless/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const jsonBody = (): Record<string, unknown> => {
      const raw = route.request().postData();
      return raw === null ? {} : (JSON.parse(raw) as Record<string, unknown>);
    };
    const json = (status: number, body: unknown): Promise<void> =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

    if (path === '/headless/test-plan/generation/catalogue') {
      await json(200, GENERATION_BROWSER_CATALOGUE);
      return;
    }
    if (path.endsWith('/test-plan/generation/preview') && method === 'POST') {
      const request = jsonBody();
      state.previewRequests.push(request);
      const stage = typeof request.stage === 'string' ? request.stage : 'base';
      await json(200, browserPreview(stage));
      return;
    }
    if (path.endsWith('/test-plan/generations') && method === 'POST') {
      state.submitted += 1;
      if (state.failOnSubmit) state.status = 'failed';
      else if (state.completeOnSubmit) state.status = 'succeeded';
      const body = {
        job_id: GENERATION_BROWSER_JOB_ID,
        project_id: GENERATION_BROWSER_PROJECT_ID,
        status: state.status,
        request_sha256: 'a'.repeat(64),
        matrix_revision: 'db-browser-16k',
      };
      state.payloadRowCounts.push('rows' in body ? 1 : 0);
      await json(202, body);
      return;
    }
    if (path.endsWith(`/test-plan/generations/${GENERATION_BROWSER_JOB_ID}`) && method === 'GET') {
      const body = {
        job_id: GENERATION_BROWSER_JOB_ID,
        project_id: GENERATION_BROWSER_PROJECT_ID,
        status: state.status,
        draft_id: state.status === 'succeeded' ? GENERATION_BROWSER_DRAFT_ID : null,
        request_sha256: 'a'.repeat(64),
        matrix_revision: 'db-browser-16k',
        matrix_sha256: 'b'.repeat(64),
        error_code: state.status === 'failed' ? 'materialization_failed' : null,
        error_message: state.status === 'failed' ? 'seeded terminal failure' : null,
        created_at: null,
        updated_at: null,
      };
      state.payloadRowCounts.push('rows' in body ? 1 : 0);
      await json(200, body);
      return;
    }
    if (path.endsWith('/test-plan/drafts') && method === 'GET') {
      await json(200, {
        drafts:
          state.status === 'succeeded'
            ? [
                {
                  draft_id: GENERATION_BROWSER_DRAFT_ID,
                  project_id: GENERATION_BROWSER_PROJECT_ID,
                  status: state.draftStatus,
                  row_count: state.seededRows,
                  created_at: null,
                  updated_at: null,
                },
              ]
            : [],
        next_cursor: null,
      });
      return;
    }
    if (
      path.endsWith(`/test-plan/drafts/${GENERATION_BROWSER_DRAFT_ID}/rows`) &&
      method === 'GET'
    ) {
      const limit = Number(url.searchParams.get('limit'));
      const afterValue = url.searchParams.get('after_draft_row_id');
      const after = afterValue === null ? undefined : Number(afterValue);
      state.rowLimitRequests.push(limit);
      if (after !== undefined) state.rowAfterRequests.push(after);
      await json(200, browserRowPage(after));
      return;
    }
    if (path.endsWith(`/test-plan/drafts/${GENERATION_BROWSER_DRAFT_ID}`) && method === 'GET') {
      await json(200, {
        draft_id: GENERATION_BROWSER_DRAFT_ID,
        project_id: GENERATION_BROWSER_PROJECT_ID,
        status: state.draftStatus,
        created_at: null,
        created_by: 'browser-operator',
        scope_revision: 1,
        generation_metadata_json: null,
        rows: [browserRowPage(undefined).rows[0]],
      });
      return;
    }
    if (path.endsWith('/generation-metadata') && method === 'GET') {
      await json(200, {
        job_id: GENERATION_BROWSER_JOB_ID,
        draft_id: GENERATION_BROWSER_DRAFT_ID,
        status: state.status,
        metadata: { output_digest: 'd'.repeat(64), generation_key: 'generation-key-1' },
      });
      return;
    }
    if (path.endsWith('/validate') && method === 'POST') {
      state.validationCalls += 1;
      await json(200, {
        draft_id: GENERATION_BROWSER_DRAFT_ID,
        error_count: 0,
        warning_count: 0,
        issues: [],
      });
      return;
    }
    if (path.endsWith('/publish') && method === 'POST') {
      state.publishCalls += 1;
      state.draftStatus = 'published';
      await json(200, {
        plan_id: 'browser-published-plan',
        draft_id: GENERATION_BROWSER_DRAFT_ID,
        status: 'published',
        rows: [],
      });
      return;
    }
    if (path.endsWith('/publications') && method === 'GET') {
      await json(200, { publications: [], next_cursor: null });
      return;
    }
    await json(200, {});
  });
}

async function openGenerationBrowser(page: Page, state: BrowserGenerationState): Promise<void> {
  await mockGenerationBrowser(page, state);
  await injectAuthenticatedSession(page, {
    permissions: [...TEST_OPERATOR_PERMISSIONS, 'test_plan:read', 'test_plan:author'],
  });
  await page.goto(`/test-plans?project=${GENERATION_BROWSER_PROJECT_ID}`);
  await expect(page.getByRole('heading', { name: '테스트 플랜', level: 1 })).toBeVisible();
  await expect(page.getByTestId('test-plans-generator-form')).toBeVisible();
}

test.describe('Current test-plan generation browser contract', () => {
  test('reloads before completion, keeps initial payloads empty, and stays within 16k budgets', async ({
    page,
  }) => {
    const state = newBrowserGenerationState();
    await openGenerationBrowser(page, state);
    const budgets = page.getByTestId('test-plans-generator-budgets');
    await expect(budgets).toHaveAttribute('data-page-size', String(GENERATION_BROWSER_PAGE_SIZE));
    await expect(budgets).toHaveAttribute(
      'data-browser-cache-page-limit',
      String(GENERATION_BROWSER_CACHE_PAGE_LIMIT),
    );
    await expect(budgets).toHaveAttribute(
      'data-dom-row-limit',
      String(GENERATION_BROWSER_DOM_ROW_LIMIT),
    );
    await expect(budgets).toHaveAttribute('data-initial-payload-row-limit', '0');
    expect(state.seededRows).toBe(16_000);

    await page.getByTestId('test-plans-generator-submit').click();
    await expect(page.getByTestId('test-plans-generator-preview')).toBeVisible();
    await page.getByTestId('test-plans-generator-submit').click();
    await expect(page.getByTestId('test-plans-generator-job-status')).toHaveText('대기');
    await expect(page.getByTestId('test-plans-generator-rows')).toHaveCount(0);
    expect(state.submitted).toBe(1);
    expect(state.payloadRowCounts.every((count) => count === 0)).toBe(true);

    await page.reload();
    await expect(page.getByTestId('test-plans-generator-job-status')).toHaveText('대기');
    expect(state.submitted).toBe(1);

    state.status = 'succeeded';
    await page.reload();
    await expect(page.getByTestId('test-plans-generator-job-status')).toHaveText('완료');
    await expect(page.getByTestId('test-plans-generator-row')).toHaveCount(
      GENERATION_BROWSER_PAGE_SIZE,
    );
    const domRows = await page.getByTestId('test-plans-generator-row').count();
    expect(domRows).toBeLessThanOrEqual(GENERATION_BROWSER_DOM_ROW_LIMIT);
    const cachedPages = Number(await budgets.getAttribute('data-cached-generation-pages'));
    expect(cachedPages).toBeLessThanOrEqual(GENERATION_BROWSER_CACHE_PAGE_LIMIT);
    expect(state.rowLimitRequests.every((limit) => limit === GENERATION_BROWSER_PAGE_SIZE)).toBe(
      true,
    );

    const performance = await page.evaluate(
      async (
        idleTimeoutMs,
      ): Promise<{
        longTaskObserverInstalled: boolean;
        longTaskSupported: boolean;
        maxLongTaskDuration: number;
        idleDuration: number;
        idleCallbackFired: boolean;
      }> => {
        const perf = window.performance;
        const longTaskSupported = PerformanceObserver.supportedEntryTypes.includes('longtask');
        const longTaskDurations = perf.getEntriesByType('longtask').map((entry) => entry.duration);
        const idleResult = await new Promise<{ duration: number; fired: boolean }>((resolve) => {
          const start = perf.now();
          const idleCallback = (
            window as Window & {
              requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
            }
          ).requestIdleCallback;
          if (idleCallback !== undefined) {
            idleCallback(() => resolve({ duration: perf.now() - start, fired: true }), {
              timeout: idleTimeoutMs,
            });
          } else {
            window.setTimeout(() => resolve({ duration: perf.now() - start, fired: true }), 0);
          }
        });
        return {
          longTaskObserverInstalled:
            (window as Window & { __fccLongTaskObserverInstalled?: boolean })
              .__fccLongTaskObserverInstalled === true,
          longTaskSupported,
          maxLongTaskDuration: Math.max(0, ...longTaskDurations),
          idleDuration: idleResult.duration,
          idleCallbackFired: idleResult.fired,
        };
      },
      GENERATION_BROWSER_IDLE_TIMEOUT_MS,
    );
    expect(performance.longTaskObserverInstalled).toBe(true);
    expect(performance.longTaskSupported).toBe(true);
    expect(performance.maxLongTaskDuration).toBeLessThanOrEqual(
      GENERATION_BROWSER_LIMITS.idle_fold_p95_ms,
    );
    expect(performance.idleCallbackFired).toBe(true);
    expect(Number.isFinite(performance.idleDuration)).toBe(true);
    expect(performance.idleDuration).toBeLessThanOrEqual(GENERATION_BROWSER_IDLE_TIMEOUT_MS + 100);
  });

  test('renders terminal failure and never fabricates generated rows', async ({ page }) => {
    const state = newBrowserGenerationState({ failOnSubmit: true });
    await openGenerationBrowser(page, state);
    await page.getByTestId('test-plans-generator-submit').click();
    await page.getByTestId('test-plans-generator-submit').click();
    await expect(page.getByTestId('test-plans-generator-job-status')).toHaveText('실패');
    await expect(page.getByTestId('test-plans-generator-error')).toBeVisible();
    await expect(page.getByTestId('test-plans-generator-rows')).toHaveCount(0);
    expect(state.payloadRowCounts.every((count) => count === 0)).toBe(true);
  });

  test('advances the keyset page while keeping cache and DOM bounded', async ({ page }) => {
    const state = newBrowserGenerationState({ completeOnSubmit: true });
    await openGenerationBrowser(page, state);
    await page.getByTestId('test-plans-generator-submit').click();
    await page.getByTestId('test-plans-generator-submit').click();
    await expect(page.getByTestId('test-plans-generator-job-status')).toHaveText('완료');
    await expect(page.getByTestId('test-plans-generator-next-page')).toBeVisible();
    await page.getByTestId('test-plans-generator-next-page').click();
    await expect(page.getByTestId('test-plans-generator-previous-page')).toBeVisible();
    expect(state.rowAfterRequests).toContain(GENERATION_BROWSER_PAGE_SIZE);
    await expect(page.getByTestId('test-plans-generator-row').first()).toContainText('251:');
    const budget = page.getByTestId('test-plans-generator-budgets');
    expect(Number(await budget.getAttribute('data-cached-generation-pages'))).toBeLessThanOrEqual(
      GENERATION_BROWSER_CACHE_PAGE_LIMIT,
    );
    expect(await page.getByTestId('test-plans-generator-row').count()).toBeLessThanOrEqual(
      GENERATION_BROWSER_DOM_ROW_LIMIT,
    );
  });

  test('validates and publishes a generated draft through the current routes', async ({ page }) => {
    const state = newBrowserGenerationState({ completeOnSubmit: true });
    await openGenerationBrowser(page, state);
    await page.getByTestId('test-plans-generator-submit').click();
    await page.getByTestId('test-plans-generator-submit').click();
    await expect(page.getByTestId('test-plans-generator-job-status')).toHaveText('완료');
    await expect(page.getByTestId('test-plans-readiness')).toBeVisible();
    await page.getByTestId('test-plans-validate').click();
    await expect(page.getByTestId('test-plans-validate-clean')).toBeVisible();
    await page.getByTestId('test-plans-publish').click();
    await expect(page.getByTestId('test-plans-publish-success')).toContainText(
      'browser-published-plan',
    );
    expect(state.validationCalls).toBe(1);
    expect(state.publishCalls).toBe(1);
  });

  test('sends typed WLAN main-test source fields and passes an accessibility audit', async ({
    page,
  }) => {
    const state = newBrowserGenerationState();
    await openGenerationBrowser(page, state);
    await page.getByTestId('test-plans-generator-technology').selectOption('WLAN');
    await expect(page.getByTestId('test-plans-generator-stage')).toHaveAttribute(
      'data-label-state',
      'localized',
    );
    await expect(page.getByTestId('test-plans-generator-stage').locator('..')).toContainText(
      '생성 단계',
    );
    await expect(
      page.getByTestId('test-plans-generator-stage').locator('option').nth(0),
    ).toHaveText('기본 생성');
    await expect(
      page.getByTestId('test-plans-generator-stage').locator('option').nth(1),
    ).toHaveText('사전 시험');
    await expect(
      page.getByTestId('test-plans-generator-stage').locator('option').nth(2),
    ).toHaveText('본 시험');
    for (const axis of [
      'technologies',
      'bands',
      'bandwidths',
      'channels',
      'modulations',
      'tests',
      'antennas',
    ]) {
      await expect(page.getByTestId(`test-plans-generator-axis-${axis}`)).toHaveAttribute(
        'data-label-state',
        'localized',
      );
    }
    await page.getByTestId('test-plans-generator-submit').click();
    await expect(page.getByTestId('test-plans-generator-preview')).toBeVisible();
    const baseRequest = state.previewRequests.at(-1);
    expect(baseRequest?.technology).toBe('WLAN');
    expect(baseRequest?.stage).toBe('base');

    await page.getByTestId('test-plans-generator-stage').selectOption('main_test');
    await expect(page.getByTestId('test-plans-generator-main-source')).toHaveAttribute(
      'data-label-state',
      'localized',
    );
    await expect(page.getByTestId('test-plans-generator-main-source')).toContainText(
      '본 시험 출처',
    );
    for (const label of ['출처 세션', '선택 채널', '최악 조건 판정 리비전']) {
      await expect(page.getByTestId('test-plans-generator-main-source')).toContainText(label);
    }
    const sourceFields = page.getByTestId('test-plans-generator-main-source').locator('input');
    await sourceFields.nth(0).fill('42');
    await sourceFields.nth(1).fill('1');
    await sourceFields.nth(2).fill('a'.repeat(64));
    await page.getByTestId('test-plans-generator-submit').click();
    await expect(page.getByTestId('test-plans-generator-preview')).toBeVisible();
    const mainRequest = state.previewRequests.at(-1);
    expect(mainRequest?.technology).toBe('WLAN');
    expect(mainRequest?.stage).toBe('main_test');
    expect(mainRequest?.source_session_id).toBe('42');
    expect(mainRequest?.selected_channels).toEqual(['1']);
    expect(mainRequest?.worst_decision_snapshot_revision).toBe('a'.repeat(64));
    expect(mainRequest?.bands_per_subfamily).toEqual({ '802.11ax_2.4': ['2.4G'] });
    expect(mainRequest).not.toHaveProperty('scope_selection');
    expect(mainRequest).not.toHaveProperty('scope_profile');

    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);

    await expect(page.locator('main')).toHaveCount(1);
    const nestedMain = await page
      .locator('main')
      .evaluate((main) =>
        main.parentElement?.closest(
          'main, [role="banner"], [role="complementary"], [role="contentinfo"], ' +
            '[role="navigation"], [role="region"], section[aria-label], section[aria-labelledby]',
        ),
      );
    expect(nestedMain).toBeNull();

    const skipLink = page.getByRole('link', { name: '본문으로 건너뛰기' });
    await expect(skipLink).toHaveAttribute('href', '#content');
    await skipLink.focus();
    await skipLink.press('Enter');
    await expect(page.locator('#content')).toBeFocused();
  });
});
