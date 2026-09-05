import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import {
  CLAIMS_MAX_AUTO_PAGES,
  ProjectsRoute,
  classifyCoverage,
  isValidProjectId,
  summarizeByTechnology,
} from '@/routes/projects';
import { SEARCH_DEBOUNCE_MS } from '@/shared/search-debounce';

import type { ReactElement } from 'react';

/**
 * FE-P2 (2026-05-27) — project coverage dashboard tests. ★1순위.
 *
 * The dashboard consumes the FE-P0d platform read API through the keyset page
 * helpers (`fetchCoveragePage` / `fetchClaimsPage`, mocked here — they own the
 * typed limit/cursor wire shape, covered in platform-client.test.ts). Covers id
 * validation, verdict classification, technology summarization (the hierarchical
 * matrix), RBAC, URL-driven lookup, drilldown, the repeated-measure (duplicate)
 * signal, and keyset pagination (user-driven "더보기" + claim auto-advance).
 */

const platformApi = vi.hoisted(() => ({
  fetchProjectsPage: vi.fn(),
  fetchCoveragePage: vi.fn(),
  fetchClaimsPage: vi.fn(),
  fetchSyncStatus: vi.fn(),
  fetchProviderList: vi.fn(),
  fetchResultSelections: vi.fn(),
  fetchResultAttempts: vi.fn(),
  selectResult: vi.fn(),
  clearResultSelection: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/u, '');
  const body = btoa(JSON.stringify(payload))
    .replace(/=+$/u, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return `${header}.${body}.sig`;
}

function authenticateAs(permissions: readonly string[]): void {
  applyTokenSet({
    accessToken: makeJwt({ sub: 'op-1', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

function coverage(over: Record<string, unknown>): Record<string, unknown> {
  return {
    project_id: PROJECT_ID,
    condition_hash: 'h1',
    technology: 'UNII',
    attempt_count: 1,
    latest_verdict: 'Pass',
    latest_attempt_number: 1,
    latest_measured_at: '2026-05-27T00:00:00',
    latest_operator: 'alice',
    latest_session_id: 's1',
    ...over,
  };
}

/** One keyset page: items + the next-page cursor (null on the last page) —
 *  mirrors the `PlatformPage<T>` shape returned by the fetch helpers. */
interface Page {
  items: unknown[];
  nextCursor: string | null;
}
function page(items: unknown[], nextCursor: string | null = null): Page {
  return { items, nextCursor };
}
const EMPTY_PAGE: Page = page([]);

/** Resolve the coverage + claims page helpers (single page each by default). */
function routePlatform(coveragePage: Page = EMPTY_PAGE, claimsPage: Page = EMPTY_PAGE): void {
  platformApi.fetchCoveragePage.mockResolvedValue(coveragePage);
  platformApi.fetchClaimsPage.mockResolvedValue(claimsPage);
}

/** A coverage lookup that rejects with the given HTTP status (claims empty). */
function routeCoverageError(status: number): void {
  platformApi.fetchCoveragePage.mockRejectedValue(
    Object.assign(new Error('coverage lookup failed'), { status }),
  );
  platformApi.fetchClaimsPage.mockResolvedValue(EMPTY_PAGE);
}

function renderProjects(entry: string): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ProjectsRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  platformApi.fetchProjectsPage.mockReset();
  platformApi.fetchCoveragePage.mockReset();
  platformApi.fetchClaimsPage.mockReset();
  platformApi.fetchSyncStatus.mockReset();
  platformApi.fetchProviderList.mockReset();
  platformApi.fetchResultSelections.mockReset();
  platformApi.fetchResultAttempts.mockReset();
  platformApi.selectResult.mockReset();
  platformApi.clearResultSelection.mockReset();
  platformApi.fetchProviderList.mockResolvedValue([]);
  platformApi.fetchProjectsPage.mockResolvedValue({
    items: [
      {
        project_id: PROJECT_ID,
        project_code: 'SM-TEST',
        model_name: 'SM-TEST',
        manufacturer: null,
        management_number: 'M-001',
        status: 'active',
        sample_count: 0,
      },
    ],
    nextCursor: null,
  });
  // FE-SYNC freshness probe — default to an empty (no-measurement) status so the
  // coverage-focused tests here don't depend on its content.
  platformApi.fetchSyncStatus.mockResolvedValue({
    project_id: PROJECT_ID,
    server_time: '2026-05-27T00:00:00+00:00',
    last_ingested_at: null,
    age_seconds: null,
    is_stale: false,
    stale_threshold_seconds: 3600,
    condition_count: 0,
    active_claim_count: 0,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('isValidProjectId', () => {
  it('accepts a uuid and rejects non-uuid', () => {
    expect(isValidProjectId(PROJECT_ID)).toBe(true);
    expect(isValidProjectId('  ' + PROJECT_ID + ' ')).toBe(true);
    expect(isValidProjectId('not-a-uuid')).toBe(false);
    expect(isValidProjectId('123')).toBe(false);
    expect(isValidProjectId('')).toBe(false);
  });
});

describe('classifyCoverage', () => {
  it('Pass/Fail are done; anything else is in_progress', () => {
    expect(classifyCoverage('Pass')).toBe('done');
    expect(classifyCoverage('fail')).toBe('done');
    expect(classifyCoverage('')).toBe('in_progress');
    expect(classifyCoverage('running')).toBe('in_progress');
  });
});

describe('summarizeByTechnology', () => {
  it('groups by technology, counts done/in-progress/repeated, dedups operators', () => {
    const summaries = summarizeByTechnology([
      coverage({
        technology: 'UNII',
        condition_hash: 'a',
        latest_verdict: 'Pass',
        latest_operator: 'alice',
      }),
      coverage({
        technology: 'UNII',
        condition_hash: 'b',
        latest_verdict: 'running',
        attempt_count: 3,
        latest_operator: 'bob',
      }),
      coverage({
        technology: 'BT',
        condition_hash: 'c',
        latest_verdict: 'Fail',
        latest_operator: 'alice',
      }),
    ] as never);
    expect(summaries.map((s) => s.technology)).toEqual(['BT', 'UNII']); // sorted
    const unii = summaries.find((s) => s.technology === 'UNII');
    expect(unii).toMatchObject({ total: 2, done: 1, inProgress: 1, repeated: 1 });
    expect(unii?.operators.sort()).toEqual(['alice', 'bob']);
  });
});

describe('ProjectsRoute RBAC', () => {
  it('denies the dashboard without platform:read', async () => {
    authenticateAs(['headless:read']);
    routePlatform();
    renderProjects(`/projects?project=${PROJECT_ID}`);
    expect(await screen.findByTestId('auth-failure-permission_denied')).toBeInTheDocument();
    expect(screen.queryByTestId('project-select')).not.toBeInTheDocument();
  });
});

describe('ProjectsRoute coverage matrix + drilldown + duplicate signal', () => {
  it('renders the hierarchical matrix and drills into conditions', async () => {
    authenticateAs(['platform:read']);
    routePlatform(
      page([
        coverage({ technology: 'UNII', condition_hash: 'aaaaaaaaaaaa1', latest_verdict: 'Pass' }),
        coverage({
          technology: 'UNII',
          condition_hash: 'bbbbbbbbbbbb2',
          latest_verdict: 'running',
          attempt_count: 4,
        }),
      ]),
    );
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);

    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument());
    expect(screen.getByTestId('projects-workbench-overview')).toBeInTheDocument();
    expect(screen.getByTestId('projects-workbench')).toBeInTheDocument();
    expect(screen.getByTestId('projects-next-membership')).toHaveAttribute(
      'href',
      `/membership?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('projects-next-fields')).toHaveAttribute(
      'href',
      `/fields?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('projects-next-progress')).toHaveAttribute(
      'href',
      `/progress?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('projects-next-chambers')).toHaveAttribute(
      'href',
      `/chambers?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('projects-next-test-reports')).toHaveAttribute(
      'href',
      `/test-reports?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('tech-total')).toHaveTextContent('2');
    // repeated-measure (duplicate) signal: one condition with attempt_count 4
    expect(screen.getByTestId('tech-repeated')).toHaveTextContent('1');
    // drilldown expanded (?tech=UNII) → condition rows
    expect(screen.getAllByTestId('condition-row')).toHaveLength(2);
    expect(screen.getByTestId('condition-repeated')).toBeInTheDocument();
    // first page requested with no cursor and no technology facet (typed helper,
    // not a raw client call). The unfiltered read passes technology undefined.
    expect(platformApi.fetchCoveragePage).toHaveBeenCalledWith(PROJECT_ID, undefined, undefined);
    // single page → no "더보기"
    expect(screen.queryByTestId('coverage-load-more')).not.toBeInTheDocument();
  });

  it('projects the matrix into a phone card list without losing a condition (§M7.2)', async () => {
    authenticateAs(['platform:read']);
    routePlatform(
      page([
        coverage({ technology: 'UNII', condition_hash: 'aaaaaaaaaaaa1', latest_verdict: 'Pass' }),
        coverage({ technology: 'UNII', condition_hash: 'bbbbbbbbbbbb2', latest_verdict: 'Fail' }),
      ]),
    );
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);
    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument());

    // The matrix is on the descriptor form, so it gains the card projection the
    // phone band swaps to. The technology summary is the card title.
    const cards = screen.getAllByTestId('data-table-card');
    expect(cards).toHaveLength(1);
    expect(cards[0]).toHaveTextContent('UNII');

    // 1:N — the card re-offers the condition rows through a native <details>,
    // which is the ONLY place they exist once the table is display:none.
    const disclosure = screen.getByTestId('data-table-card-expansion');
    expect(disclosure.querySelectorAll('.condition-list__item')).toHaveLength(2);
    expect(disclosure).toHaveTextContent('Pass');
    expect(disclosure).toHaveTextContent('Fail');

    // Route testids stay single-surface, so `getByTestId` is still unambiguous
    // even though every value is now rendered twice.
    expect(screen.getAllByTestId('condition-row')).toHaveLength(2);
    expect(screen.getByTestId('tech-total')).toBeInTheDocument();
  });

  it('does not query coverage until a project is selected', async () => {
    authenticateAs(['platform:read']);
    routePlatform();
    renderProjects('/projects');
    await screen.findByTestId('project-select');
    expect(screen.getByTestId('projects-project-empty')).toBeInTheDocument();
    expect(platformApi.fetchCoveragePage).not.toHaveBeenCalled();
    await userEvent.selectOptions(screen.getByTestId('project-select'), PROJECT_ID);
    await waitFor(() => expect(platformApi.fetchCoveragePage).toHaveBeenCalled());
  });

  it('surfaces a permission_denied coverage error (403)', async () => {
    authenticateAs(['platform:read']);
    routeCoverageError(403);
    renderProjects(`/projects?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('coverage-error')).toHaveTextContent('권한'));
  });

  it('shows an empty state when the project has no coverage', async () => {
    authenticateAs(['platform:read']);
    routePlatform();
    renderProjects(`/projects?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('coverage-empty')).toBeInTheDocument());
  });

  it('overlays an active claim lock on a claimed condition (FE-P3)', async () => {
    authenticateAs(['platform:read']);
    routePlatform(
      page([coverage({ technology: 'UNII', condition_hash: 'aaaaaaaaaaaa1' })]),
      page([
        {
          claim_id: 'c1',
          project_id: PROJECT_ID,
          condition_hash: 'aaaaaaaaaaaa1',
          technology: 'UNII',
          operator: 'bob',
          session_id: 's2',
          occurred_at: '2026-05-27T00:00:00',
          expires_at: '2026-05-27T01:00:00',
        },
      ]),
    );
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);
    await waitFor(() => expect(screen.getByTestId('condition-claimed')).toHaveTextContent('bob'));
  });

  it('shows the online-only duplicate-prevention boundary note (FE-SYNC)', async () => {
    authenticateAs(['platform:read']);
    routePlatform();
    renderProjects(`/projects?project=${PROJECT_ID}`);
    expect(await screen.findByTestId('dedup-boundary')).toBeInTheDocument();
  });
});

describe('ProjectsRoute keyset pagination (FE-P2 adoption)', () => {
  it('loads more coverage pages on demand and stops at the last page', async () => {
    authenticateAs(['platform:read']);
    // First page carries a next-cursor; the second (continued) page is the last.
    platformApi.fetchClaimsPage.mockResolvedValue(EMPTY_PAGE);
    platformApi.fetchCoveragePage.mockImplementation((_projectId: string, cursor?: string) =>
      Promise.resolve(
        cursor === undefined
          ? page([coverage({ technology: 'UNII', condition_hash: 'aaaaaaaaaaaa1' })], 'cursor-2')
          : page([coverage({ technology: 'BT', condition_hash: 'bbbbbbbbbbbb2' })], null),
      ),
    );
    renderProjects(`/projects?project=${PROJECT_ID}`);

    // First page rendered → one technology row + a "더보기" affordance.
    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument());
    expect(screen.getAllByTestId('tech-row')).toHaveLength(1);
    const loadMore = await screen.findByTestId('coverage-load-more');

    // Advancing requests the next page with the carried cursor and appends it.
    await userEvent.click(loadMore);
    await waitFor(() => expect(screen.getAllByTestId('tech-row')).toHaveLength(2));
    expect(platformApi.fetchCoveragePage).toHaveBeenLastCalledWith(
      PROJECT_ID,
      'cursor-2',
      undefined,
    );
    // Last page (null cursor) → the affordance disappears.
    expect(screen.queryByTestId('coverage-load-more')).not.toBeInTheDocument();
  });

  it('auto-advances claim pages so the lock overlay stays complete', async () => {
    authenticateAs(['platform:read']);
    routePlatform(page([coverage({ technology: 'UNII', condition_hash: 'aaaaaaaaaaaa1' })]));
    // Claims span two pages; the second holds the lock for the visible condition.
    platformApi.fetchClaimsPage.mockImplementation((_projectId: string, cursor?: string) =>
      Promise.resolve(
        cursor === undefined
          ? page([], 'claims-2')
          : page(
              [
                {
                  claim_id: 'c9',
                  project_id: PROJECT_ID,
                  condition_hash: 'aaaaaaaaaaaa1',
                  technology: 'UNII',
                  operator: 'carol',
                  session_id: 's9',
                  occurred_at: '2026-05-27T00:00:00',
                  expires_at: null,
                },
              ],
              null,
            ),
      ),
    );
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);

    // The dashboard auto-fetched the second claim page → lock overlay appears.
    await waitFor(() => expect(screen.getByTestId('condition-claimed')).toHaveTextContent('carol'));
    expect(platformApi.fetchClaimsPage).toHaveBeenCalledWith(PROJECT_ID, undefined, undefined);
    expect(platformApi.fetchClaimsPage).toHaveBeenCalledWith(PROJECT_ID, 'claims-2', undefined);
  });

  it('halts claim auto-advance at the page cap when the backend cursor never terminates', async () => {
    authenticateAs(['platform:read']);
    routePlatform(page([coverage({ technology: 'UNII', condition_hash: 'a' })]));
    // Broken backend: every claims page returns a (non-null) cursor → the keyset
    // never terminates. The circuit breaker must stop the loop, not hang.
    platformApi.fetchClaimsPage.mockResolvedValue(page([], 'never-ending'));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    renderProjects(`/projects?project=${PROJECT_ID}`);

    await waitFor(() =>
      expect(warn).toHaveBeenCalledWith(expect.stringContaining('auto-advance halted')),
    );
    // Bounded: the initial query + auto-advance pages never exceed the cap.
    expect(platformApi.fetchClaimsPage.mock.calls.length).toBeLessThanOrEqual(
      CLAIMS_MAX_AUTO_PAGES,
    );
    warn.mockRestore();
  });
});

describe('ProjectsRoute technology facet filter (Phase B adoption)', () => {
  it('forwards the typed technology facet to coverage + claims and resets pagination', async () => {
    authenticateAs(['platform:read']);
    routePlatform(
      page([
        coverage({ technology: 'UNII', condition_hash: 'aaaaaaaaaaaa1' }),
        coverage({ technology: 'BT', condition_hash: 'bbbbbbbbbbbb2' }),
      ]),
    );
    renderProjects(`/projects?project=${PROJECT_ID}`);

    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument());
    // Unfiltered first read carries technology undefined (full project).
    expect(platformApi.fetchCoveragePage).toHaveBeenCalledWith(PROJECT_ID, undefined, undefined);

    // Selecting/entering a technology (one change with the complete value, as a
    // datalist pick does) narrows the read server-side.
    fireEvent.change(screen.getByTestId('tech-filter-input'), { target: { value: 'BT' } });

    // Facet change → a fresh first page (cursor undefined) scoped to the facet
    // (queryKey includes the technology → React Query resets pagination).
    await waitFor(() =>
      expect(platformApi.fetchCoveragePage).toHaveBeenLastCalledWith(PROJECT_ID, undefined, 'BT'),
    );
    // Claims get the same facet so the lock overlay stays scoped + complete.
    expect(platformApi.fetchClaimsPage).toHaveBeenLastCalledWith(PROJECT_ID, undefined, 'BT');
  });

  it('derives the datalist suggestions from the loaded coverage (no hardcoded enum)', async () => {
    authenticateAs(['platform:read']);
    routePlatform(
      page([
        coverage({ technology: 'UNII', condition_hash: 'a' }),
        coverage({ technology: 'BT', condition_hash: 'b' }),
        // blank technology → summarized as (unknown) → not a selectable facet.
        coverage({ technology: '', condition_hash: 'c' }),
      ]),
    );
    renderProjects(`/projects?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument());

    const datalist = screen.getByTestId('tech-filter-options');
    const values = Array.from(datalist.querySelectorAll('option')).map((o) =>
      o.getAttribute('value'),
    );
    // Exactly the technologies present in the data (sorted), (unknown) excluded —
    // proves the suggestions are data-derived, not a hardcoded BT/BLE/UNII/DTS list.
    expect(values).toEqual(['BT', 'UNII']);
  });

  it('debounces rapid typing into a single facet read (no per-keystroke fetch)', async () => {
    authenticateAs(['platform:read']);
    routePlatform(page([coverage({ technology: 'BT', condition_hash: 'aaaaaaaaaaaa1' })]));
    renderProjects(`/projects?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument());

    // Type two characters in quick succession (before the debounce window).
    const input = screen.getByTestId('tech-filter-input');
    fireEvent.change(input, { target: { value: 'B' } });
    fireEvent.change(input, { target: { value: 'BT' } });

    // Only the final value reaches the server (debounced).
    await waitFor(() =>
      expect(platformApi.fetchCoveragePage).toHaveBeenLastCalledWith(PROJECT_ID, undefined, 'BT'),
    );
    // The intermediate keystroke 'B' never issued a central read.
    expect(platformApi.fetchCoveragePage).not.toHaveBeenCalledWith(PROJECT_ID, undefined, 'B');
    expect(platformApi.fetchClaimsPage).not.toHaveBeenCalledWith(PROJECT_ID, undefined, 'B');
  });

  it('commits the facet on the shared debounce window, not a route-local one', async () => {
    // fe-honesty-debt M1 (2026-07-31) — S5. 위 케이스는 "디바운스가 있다"만 본다:
    // 라우트가 자기 리터럴로 되돌아가도(예: 400ms) 여전히 green 이다. 여기서는
    // 창의 **경계**를 SSOT 상수로 잡아, 값이 갈라지는 순간 red 가 되게 한다.
    vi.useFakeTimers();
    try {
      authenticateAs(['platform:read']);
      routePlatform(page([coverage({ technology: 'BT', condition_hash: 'aaaaaaaaaaaa1' })]));
      renderProjects(`/projects?project=${PROJECT_ID}`);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      const before = platformApi.fetchCoveragePage.mock.calls.length;

      act(() => {
        fireEvent.change(screen.getByTestId('tech-filter-input'), { target: { value: 'BT' } });
      });
      // 창이 닫히기 1ms 전 — 아직 아무것도 커밋되지 않았다.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS - 1);
      });
      expect(platformApi.fetchCoveragePage).toHaveBeenCalledTimes(before);

      // 창이 닫히는 순간 — 좁히기 읽기가 나간다. `waitFor` 는 쓰지 않는다:
      // fake timer 위에서 그 폴링은 실시간 5s 를 태우고 타임아웃한다(my-projects
      // S10 이 같은 이유로 같은 선택을 했다). 타이머 전진이 곧 flush 다.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(platformApi.fetchCoveragePage).toHaveBeenLastCalledWith(PROJECT_ID, undefined, 'BT');
    } finally {
      vi.useRealTimers();
    }
  });

  it('reads the facet from the URL and clears it via the clear button', async () => {
    authenticateAs(['platform:read']);
    routePlatform(page([coverage({ technology: 'UNII', condition_hash: 'aaaaaaaaaaaa1' })]));
    renderProjects(`/projects?project=${PROJECT_ID}&techFilter=UNII`);

    // URL-driven: the initial read is already scoped to ?techFilter=UNII.
    await waitFor(() =>
      expect(platformApi.fetchCoveragePage).toHaveBeenCalledWith(PROJECT_ID, undefined, 'UNII'),
    );

    await userEvent.click(screen.getByTestId('tech-filter-clear'));
    // Clearing reads the full project again (technology undefined).
    await waitFor(() =>
      expect(platformApi.fetchCoveragePage).toHaveBeenLastCalledWith(
        PROJECT_ID,
        undefined,
        undefined,
      ),
    );
  });
});

/**
 * S9 — partial coverage totals are labelled as partial (fe-w2-a M5).
 *
 * D6: `summarizeByTechnology` folds only the pages loaded so far, but the matrix
 * presented those sums with no qualifier. A project carries 16k+ conditions and
 * the first page is a small slice, so "완료 1 / 조건 1" read as a finished
 * technology while the rest sat unloaded. `sessions.tsx` already solved exactly
 * this with a truncated pill — the asymmetry was the bug.
 *
 * The badge is keyed on `hasNextPage`, so it must also DISAPPEAR once everything
 * is loaded: a permanent "these numbers may be incomplete" warning is its own
 * kind of dishonesty (and trains the operator to ignore it).
 */
describe('coverage matrix truncation badge (S9)', () => {
  it('marks the totals as partial while more pages remain', async () => {
    authenticateAs(['platform:read']);
    routePlatform(page([coverage({ technology: 'UNII' })], 'cursor-2'));
    renderProjects(`/projects?project=${PROJECT_ID}`);

    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument());
    expect(screen.getByTestId('coverage-truncated-pill')).toBeInTheDocument();
    expect(screen.getByTestId('coverage-truncated-note')).toHaveTextContent(/불러온 조건만 반영/);
  });

  it('shows no badge when everything is loaded (no false warning)', async () => {
    authenticateAs(['platform:read']);
    routePlatform(page([coverage({ technology: 'UNII' })], null));
    renderProjects(`/projects?project=${PROJECT_ID}`);

    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument());
    expect(screen.queryByTestId('coverage-truncated-pill')).toBeNull();
    expect(screen.queryByTestId('coverage-load-more')).toBeNull();
  });

  it('drops the badge after the last page is loaded', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchClaimsPage.mockResolvedValue(EMPTY_PAGE);
    platformApi.fetchCoveragePage.mockImplementation((_projectId: string, cursor?: string) =>
      Promise.resolve(
        cursor === undefined
          ? page([coverage({ technology: 'UNII', condition_hash: 'aaaaaaaaaaaa1' })], 'cursor-2')
          : page([coverage({ technology: 'BT', condition_hash: 'bbbbbbbbbbbb2' })], null),
      ),
    );
    renderProjects(`/projects?project=${PROJECT_ID}`);

    await waitFor(() => expect(screen.getByTestId('coverage-truncated-pill')).toBeInTheDocument());
    await userEvent.click(await screen.findByTestId('coverage-load-more'));
    await waitFor(() => expect(screen.getAllByTestId('tech-row')).toHaveLength(2));
    expect(screen.queryByTestId('coverage-truncated-pill')).toBeNull();
  });
});
