import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { TestPlansRoute } from '@/routes/test-plans';

import { headlessOk } from './helpers/headless-contract';
import { spyHeadlessTransport } from './helpers/headless-transport';
import { TEST_PLAN_GENERATION_LIMITS } from './helpers/test-plan-generation-limits';

import type { HeadlessOkBody } from './helpers/headless-contract';
import type * as ReactQueryModule from '@tanstack/react-query';
import type { ReactElement } from 'react';

/**
 * W2-C M4 — the draft detail must not scale its DOM or its hook count with the
 * row count.
 *
 * A published FCC test plan runs to 16,000+ items. The prior implementation
 * mapped every row to a `<tr>` AND to its own `useMutation`, so opening a real
 * plan meant ~144,000 table cells plus 16,000 mutation observers — each with a
 * cache subscription — before the operator did anything at all.
 *
 * `useMutation` is wrapped here rather than inferred from the DOM because the
 * hook count is the actual defect: a route could virtualize its rendering and
 * still create one observer per row.
 */
const mutationHook = vi.hoisted(() => ({ calls: 0 }));
vi.mock('@tanstack/react-query', async (importOriginal) => {
  const actual = await importOriginal<typeof ReactQueryModule>();
  return {
    ...actual,
    useMutation: ((options: unknown) => {
      mutationHook.calls += 1;
      return (actual.useMutation as (o: unknown) => unknown)(options);
    }) as typeof actual.useMutation,
  };
});

const headlessClient = spyHeadlessTransport();

const platformApi = vi.hoisted(() => ({ fetchProjectsPage: vi.fn() }));
vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '22222222-2222-4222-8222-222222222222';
const DRAFTS_PATH = '/headless/projects/{project_id}/test-plan/drafts';
const DETAIL_PATH = '/headless/projects/{project_id}/test-plan/drafts/{draft_id}';
const PUBLICATIONS_PATH = '/headless/projects/{project_id}/test-plan/publications';
const GENERATION_CATALOGUE_PATH = '/headless/test-plan/generation/catalogue';
const GENERATION_CATALOGUE: HeadlessOkBody<'get', typeof GENERATION_CATALOGUE_PATH> = {
  catalogues: {
    BT: {
      technology: 'BT',
      stages: [],
      axes: [
        { name: 'packets', values: ['DH5'] },
        { name: 'sub_families', values: ['BR'] },
        { name: 'modes', values: ['SISO'] },
        { name: 'test_types', values: ['Pk power'] },
        { name: 'antennas', values: ['ANT1'] },
      ],
      bands_per_subfamily: { BR: ['2.4G'] },
      revision: 'catalogue:bt',
      sha256: 'b'.repeat(64),
      limits: TEST_PLAN_GENERATION_LIMITS,
    },
  },
};

/** Comfortably above `VIRTUAL_DRAFT_ROW_THRESHOLD` (500) without making the
 *  jsdom render itself the slow part of the suite. */
const LARGE_ROW_COUNT = 600;

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/u, '');
  const body = btoa(JSON.stringify(payload))
    .replace(/=+$/u, '')
    .replace(/\+/gu, '-')
    .replace(/\//gu, '_');
  return `${header}.${body}.sig`;
}

type DraftDetail = HeadlessOkBody<'get', typeof DETAIL_PATH>;

function rowsOfSize(count: number): DraftDetail['rows'] {
  return Array.from({ length: count }).map((_, index) => ({
    draft_row_id: index + 1,
    capability_path: ['WLAN', '11ax'],
    origin: 'generated',
    antenna: 'ANT1',
    location: `CH${index}`,
    mode_family: 'HE20',
    test_type: 'PSD',
    tone: null,
    derived_kind: null,
    generation_key: null,
    scope_revision: 1,
  }));
}

/**
 * jsdom has no layout engine, so `@tanstack/react-virtual` measures every
 * element as 0×0 and windows down to ZERO rows. Without this stub an assertion
 * like "rendered rows < loaded rows" passes even when nothing is virtualized —
 * and would keep passing if virtualization were removed again. Stubbing a
 * viewport is what makes the seal mean something (`sessions.test.tsx` documents
 * the same trap).
 */
function stubViewport(): () => void {
  const originalHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
  const originalWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth');
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true,
    get: () => 600,
  });
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
    configurable: true,
    get: () => 1200,
  });
  return () => {
    if (originalHeight) {
      Object.defineProperty(HTMLElement.prototype, 'offsetHeight', originalHeight);
    }
    if (originalWidth) {
      Object.defineProperty(HTMLElement.prototype, 'offsetWidth', originalWidth);
    }
  };
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  localStorage.clear();
  mutationHook.calls = 0;
  headlessClient.GET.mockReset();
  headlessClient.POST.mockReset();
  headlessClient.PUT.mockReset();
  headlessClient.DELETE.mockReset();
  platformApi.fetchProjectsPage.mockReset();
  platformApi.fetchProjectsPage.mockResolvedValue({
    items: [
      {
        project_id: PROJECT_ID,
        project_code: 'SM-TEST',
        model_name: 'SM-TEST',
        management_number: null,
        status: 'active',
      },
    ],
    nextCursor: null,
  });
  applyTokenSet({
    accessToken: makeJwt({
      sub: 'author@corp',
      [CLAIM_PERMISSIONS]: ['test_plan:read', 'test_plan:author'],
    }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
});

afterEach(() => {
  __resetAuthStateForTests();
});

async function openLargeDraft(): Promise<void> {
  headlessClient.routes({
    [DETAIL_PATH]: {
      get: () =>
        headlessOk('get', DETAIL_PATH, {
          draft_id: 'd-1',
          project_id: PROJECT_ID,
          status: 'draft',
          created_at: '2026-06-16T00:00:00+00:00',
          created_by: 'author@corp',
          scope_revision: 3,
          generation_metadata_json: null,
          rows: rowsOfSize(LARGE_ROW_COUNT),
        }),
    },
    [PUBLICATIONS_PATH]: {
      get: () => headlessOk('get', PUBLICATIONS_PATH, { publications: [] }),
    },
    // Current generation catalogue — declared explicitly so this suite's stub
    // answers the surface the route actually calls.
    [GENERATION_CATALOGUE_PATH]: {
      get: () => headlessOk('get', GENERATION_CATALOGUE_PATH, GENERATION_CATALOGUE),
    },
    [DRAFTS_PATH]: {
      get: () =>
        headlessOk('get', DRAFTS_PATH, {
          drafts: [
            {
              draft_id: 'd-1',
              project_id: PROJECT_ID,
              status: 'draft',
              row_count: LARGE_ROW_COUNT,
              created_at: '2026-06-16T00:00:00+00:00',
              updated_at: '2026-06-16T01:00:00+00:00',
            },
          ],
          next_cursor: null,
        }),
    },
  });

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/test-plans?project=${PROJECT_ID}&draft=d-1`]}>
        <TestPlansRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
  await screen.findByTestId('test-plans-detail-virtual-table');
}

describe('W2-C M4 — large draft rendering', () => {
  it('windows the rows instead of mounting all of them (S8)', async () => {
    const restore = stubViewport();
    try {
      await openLargeDraft();

      const rendered = screen.getAllByTestId('test-plans-detail-row').length;
      // Upper bound: the DOM is bounded by the viewport, not by the data.
      expect(rendered).toBeLessThan(LARGE_ROW_COUNT);
      // Lower bound: and it is a real window, not an empty list. Without this
      // half the assertion, a measurement failure (or a removal of the feature)
      // reads as a pass.
      expect(rendered).toBeGreaterThan(0);
      // The full row count stays announced to assistive tech even though only a
      // window is mounted — windowing must not hide how much data exists.
      expect(screen.getByTestId('test-plans-detail-virtual-table')).toHaveAttribute(
        'aria-rowcount',
        String(LARGE_ROW_COUNT),
      );
      expect(screen.getByTestId('test-plans-detail-row-count')).toHaveTextContent(
        String(LARGE_ROW_COUNT),
      );
    } finally {
      restore();
    }
  });

  it('does not create a mutation hook per row (S9)', async () => {
    const restore = stubViewport();
    try {
      await openLargeDraft();

      // The whole workbench owns a handful of mutations (create draft, add row,
      // bulk replace, remove row, validate, publish, …) and re-runs them once
      // per render. What must NOT happen is growth with the row count.
      expect(mutationHook.calls).toBeLessThan(LARGE_ROW_COUNT);
      expect(mutationHook.calls).toBeGreaterThan(0);
    } finally {
      restore();
    }
  });
});
