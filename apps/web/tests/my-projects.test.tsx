import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PERMISSION_PLATFORM_ADMIN } from '@/api/permissions';
import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { MyProjectsRoute } from '@/routes/my-projects';
import { SEARCH_DEBOUNCE_MS } from '@/shared/search-debounce';

import type { ReactElement } from 'react';

/**
 * ADR-0017 Phase 1 (2026-06-22) — "내 프로젝트" 진입층 화면 테스트.
 *
 * 화면은 `@/api/platform-client` 의 fetchProjectsPage/createProject/updateProject 를
 * 통해 중앙 projects 를 소비한다(여기서 mock — 와이어 shape 는 platform-client.test.ts
 * 가 커버). 목록 카드 + **서버측 검색** + keyset 이어 읽기 + [새 프로젝트] 생성(동명
 * 멱등) + 표지 메타 편집 + 컨텍스트 전파 링크를 봉인.
 *
 * W3-B M-B (2026-07-30): 읽기가 무한정 `fetchProjects` + 클라이언트 `filterProjects`
 * 에서 `fetchProjectsPage`(서버 `q` + `limit`/`cursor`)로 옮겨졌다. 그래서 옛
 * `describe('filterProjects')`(4 케이스)와 클라이언트 필터 렌더 케이스(1)는 **삭제**
 * 되고 서버측 등가(S9·S10·S11·S12·S13, 6 케이스)로 교체됐다 — 조용한 삭제가 아니다.
 */

const platformApi = vi.hoisted(() => ({
  fetchProjectsPage: vi.fn(),
  createProject: vi.fn(),
  completeProject: vi.fn(),
  reopenProject: vi.fn(),
  updateProject: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

/**
 * Call history must not leak between cases.
 *
 * `tests/setup.ts` unmounts the tree (`cleanup()`) but nothing resets the
 * `vi.fn()` spies, so a `toHaveBeenCalledTimes(n)` assertion would count every
 * earlier case's calls too. The pre-W3-B cases all used `toHaveBeenCalledWith`,
 * which is accumulation-insensitive — and that is exactly why the weaker matcher
 * was survivable. W3-B needs *count* assertions (a double-submit, a spurious
 * empty-body PATCH, or one central read per keystroke are real defects the
 * `…With` matcher cannot see), so the history is cleared here. `mockClear` only
 * drops calls, never implementations, so each case's own `mockResolvedValue*`
 * wiring is unaffected.
 */
beforeEach(() => {
  vi.clearAllMocks();
});

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

function project(over: Record<string, unknown>): Record<string, unknown> {
  return {
    project_id: PROJECT_ID,
    project_code: 'SM-S921U',
    model_name: 'SM-S921U',
    customer: null,
    manufacturer: null,
    management_number: null,
    status: 'active',
    sample_count: 0,
    ...over,
  };
}

/**
 * One keyset page as `fetchProjectsPage` returns it.
 *
 * `nextCursor === null` IS the last page — it is what `nextCursorFromResponse`
 * yields when the `X-Next-Cursor` response header is absent. Defaulting to
 * `null` keeps every pre-existing single-page case reading like the unbounded
 * list it used to be.
 */
function page(
  items: readonly Record<string, unknown>[],
  nextCursor: string | null = null,
): { items: readonly Record<string, unknown>[]; nextCursor: string | null } {
  return { items, nextCursor };
}

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
    accessToken: makeJwt({ sub: 'admin@corp', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

function renderRoute(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/my-projects']}>
        <MyProjectsRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

describe('MyProjectsRoute', () => {
  it('uses the project workspace as the primary card entry and preserves scoped next actions', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([project({ sample_count: 3 })]));
    renderRoute();

    const link = await screen.findByTestId('project-card-link');
    expect(screen.getByTestId('my-projects-workbench-overview')).toBeInTheDocument();
    expect(screen.getByTestId('my-projects-workbench')).toBeInTheDocument();
    expect(screen.getByTestId('project-card-model')).toHaveTextContent('SM-S921U');
    expect(screen.getByTestId('project-card-samples')).toHaveTextContent('3');
    // The primary card entry is the canonical project workspace; project-level
    // work then fans out from there rather than forcing the tester to enter via
    // one downstream surface.
    expect(link).toHaveAttribute('href', `/projects?project=${PROJECT_ID}`);
    expect(screen.getByTestId('project-card-fields')).toHaveAttribute(
      'href',
      `/fields?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('project-card-coverage')).toHaveAttribute(
      'href',
      `/projects?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('project-card-progress')).toHaveAttribute(
      'href',
      `/progress?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('project-card-inventory')).toHaveAttribute(
      'href',
      `/inventory?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('project-card-test-plans')).toHaveAttribute(
      'href',
      `/test-plans?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('project-card-chambers')).toHaveAttribute(
      'href',
      `/chambers?project=${PROJECT_ID}`,
    );
    // Phase A — the project status badge is rendered (active → 진행중 label).
    expect(screen.getByTestId('project-card-status')).toHaveTextContent('진행중');
  });

  it('drops ?project= when the server-sent project_id is not a resolvable id', async () => {
    // fe-honesty-debt M1 (2026-07-31) — INTENDED BEHAVIOUR CHANGE, locked here.
    //
    // 이 라우트의 로컬 `projectScopedHref` 사본은 **id 게이트가 없었다**. SSOT
    // (`@/shared/route-links`)로 통합하면서 `isValidProjectId` 를 획득하므로,
    // 비-uuid `project_id` 에서 링크가 `?project=<garbage>` → bare path 로 바뀐다.
    // 이것은 회귀가 아니라 교정이다: 대상 라우트들은 전부 같은 `isValidProjectId`
    // 로 조회를 게이트하므로(`projects.tsx:377` · `inventory/index.tsx:90` ·
    // `TestPlansWorkbench.tsx:146` · `progress.tsx:48`), malformed `?project=` 는
    // "고른 것처럼 보이는데 아무것도 안 뜨는" dead-end 로 착지한다. bare path 는
    // 각 라우트의 프로젝트 선택 EmptyState 로 착지한다.
    //
    // 조용한 변경 금지(계약 §3-6) — 이 케이스가 그 변경의 판정식이다.
    platformApi.fetchProjectsPage.mockResolvedValue(page([project({ project_id: 'not-a-uuid' })]));
    renderRoute();

    expect(await screen.findByTestId('project-card-link')).toHaveAttribute('href', '/projects');
    for (const [testId, path] of [
      ['project-card-fields', '/fields'],
      ['project-card-coverage', '/projects'],
      ['project-card-progress', '/progress'],
      ['project-card-inventory', '/inventory'],
      ['project-card-test-plans', '/test-plans'],
      ['project-card-chambers', '/chambers'],
    ] as const) {
      expect(screen.getByTestId(testId)).toHaveAttribute('href', path);
    }
  });

  it('renders the management number on the card when present (Phase A)', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(
      page([project({ management_number: '4792232056', status: 'completed' })]),
    );
    renderRoute();

    expect(await screen.findByTestId('project-card-mgmt')).toHaveTextContent('4792232056');
    // completed → 완료 status label.
    expect(screen.getByTestId('project-card-status')).toHaveTextContent('완료');
  });

  it('passes the management number to createProject when supplied (Phase A)', async () => {
    // W3-B M2 — the dedicated `new-project-mgmt` input was replaced by inputs
    // generated from `EDITABLE_PROJECT_FIELDS` (one SSOT shared with the edit
    // form), so the test id is now derived from the wire field name.
    platformApi.fetchProjectsPage.mockResolvedValue(page([]));
    platformApi.createProject.mockResolvedValue(project({ model_name: 'SM-S921U' }));
    renderRoute();

    await screen.findByTestId('new-project-submit');
    await userEvent.type(screen.getByTestId('new-project-model'), 'SM-S921U');
    await userEvent.type(screen.getByTestId('new-project-management_number'), '4792232056');
    await userEvent.click(screen.getByTestId('new-project-submit'));

    // 2겹 단정. `toHaveBeenCalledWith` 는 값이 `undefined` 인 **실존 키**를
    // 통과시키므로(W3-B M1 실측) 빈 칸이 `customer: undefined` 로 실려도 green 이
    // 된다 — 그게 정확히 막아야 하는 것이다.
    await waitFor(() => expect(platformApi.createProject).toHaveBeenCalledTimes(1));
    const [body] = platformApi.createProject.mock.calls[0] as [Record<string, unknown>];
    expect(body).toStrictEqual({
      model_name: 'SM-S921U',
      management_number: '4792232056',
    });
    expect(Object.keys(body).sort()).toStrictEqual(['management_number', 'model_name']);
  });

  it('shows an empty state with a description when the tester has no projects', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([]));
    renderRoute();
    const empty = await screen.findByTestId('projects-empty');
    expect(empty).toBeInTheDocument();
    // §6.3 — empty state explains how to populate it.
    expect(empty).toHaveTextContent('새 프로젝트');
  });

  it('shows an error state when the project list fails', async () => {
    platformApi.fetchProjectsPage.mockRejectedValue(
      Object.assign(new Error('projects lookup failed'), { status: 503 }),
    );
    renderRoute();
    expect(await screen.findByTestId('projects-error')).toBeInTheDocument();
  });

  it('creates a project from the model name and surfaces success', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([]));
    platformApi.createProject.mockResolvedValue(project({ model_name: 'SM-S921U' }));
    renderRoute();

    await screen.findByTestId('new-project-submit');
    await userEvent.type(screen.getByTestId('new-project-model'), 'SM-S921U');
    await userEvent.click(screen.getByTestId('new-project-submit'));

    // S4 — blank 표지 메타 칸은 **키 자체가 붙지 않는다**(백엔드는 `''` 와 NULL 을
    // 구분한다). 키 집합 단정이 그것을 증명하는 유일한 방법이다.
    await waitFor(() => expect(platformApi.createProject).toHaveBeenCalledTimes(1));
    const [body] = platformApi.createProject.mock.calls[0] as [Record<string, unknown>];
    expect(body).toStrictEqual({ model_name: 'SM-S921U' });
    expect(Object.keys(body)).toStrictEqual(['model_name']);
    expect(await screen.findByTestId('new-project-success')).toBeInTheDocument();
  });

  it('disables create until a model name is entered', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([]));
    renderRoute();
    expect(await screen.findByTestId('new-project-submit')).toBeDisabled();
  });
});

/**
 * W3-B M-B (2026-07-30) — 서버측 검색 + keyset 이어 읽기 봉인 (S9 · S10 · S11 · S12 · S13).
 *
 * 이 describe 가 삭제된 클라이언트 필터 케이스들을 대체한다. 옛 케이스들은 "로드된
 * 배열이 올바르게 걸러지는가"를 물었는데, 그것은 애초에 물어야 할 질문이 아니었다 —
 * 사용자가 알고 싶은 것은 **중앙에 그 프로젝트가 있는가**이고, 로드된 배열은 그 답을
 * 줄 수 없다. 여기 단정들은 검색이 서버까지 도달하는지, 요청이 유계인지, 잔여가
 * 사용자에게 드러나는지를 본다.
 */
describe('MyProjectsRoute 서버측 검색 + keyset (W3-B M-B)', () => {
  it('S11 — the first read is bounded and sends NO q (a cleared box is "no filter")', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([]));
    renderRoute();

    await waitFor(() => expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(1));
    // 위치 인자 3개 전량 단정 — `('active')` 도 `('active', '', undefined)` 도 아니다.
    // 빈 문자열은 **다른 요청**이고(`?q=` 리터럴), 와이어 쿼리에서 `q` 키가 실제로
    // 빠지는지는 `tests/api/platform-client.test.ts` 가 층을 나눠 봉인한다.
    expect(platformApi.fetchProjectsPage.mock.calls[0]).toStrictEqual([
      'active',
      undefined,
      undefined,
    ]);
  });

  it('S11 — a whitespace-only search still sends no q (no extra read at all)', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([]));
    renderRoute();
    await waitFor(() => expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(1));

    await userEvent.type(screen.getByTestId('project-search'), '   ');
    // 디바운스 창을 **넘겨서** 기다린다 — "요청이 나가지 않는다"는 음성 단정이라
    // `waitFor`(성공까지 재시도)로는 표현할 수 없다.
    await new Promise((resolve) => {
      window.setTimeout(resolve, SEARCH_DEBOUNCE_MS * 2);
    });

    expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(1);
    for (const call of platformApi.fetchProjectsPage.mock.calls) {
      expect((call as unknown[])[1]).toBeUndefined();
    }
  });

  it('S9 — renders the SERVER-narrowed page verbatim (no client re-filter)', async () => {
    platformApi.fetchProjectsPage
      .mockResolvedValueOnce(page([project({ project_id: 'a', model_name: 'SM-S921U' })]))
      // 서버가 (예컨대 고객명으로) 매칭해서 준 행. 이 카드에 렌더되는 어떤 문자열도
      // 검색어 'globex' 를 포함하지 않으므로, 클라이언트 재필터가 남아 있으면 이 행이
      // 사라진다 — 그것이 이 단정을 비-공허하게 만든다.
      .mockResolvedValue(page([project({ project_id: 'b', model_name: 'GALAXY-Z' })]));
    renderRoute();
    await waitFor(() =>
      expect(screen.getByTestId('project-card-model')).toHaveTextContent('SM-S921U'),
    );

    await userEvent.type(screen.getByTestId('project-search'), 'globex');

    await waitFor(() =>
      expect(screen.getByTestId('project-card-model')).toHaveTextContent('GALAXY-Z'),
    );
    expect(platformApi.fetchProjectsPage.mock.calls.at(-1)).toStrictEqual([
      'active',
      'globex',
      undefined,
    ]);
  });

  it('S9 — "no results" claims exactly the scope the request actually had (status-filtered central search)', async () => {
    platformApi.fetchProjectsPage
      .mockResolvedValueOnce(page([project({})]))
      .mockResolvedValue(page([]));
    renderRoute();
    await waitFor(() => expect(screen.getByTestId('project-card')).toBeInTheDocument());

    await userEvent.type(screen.getByTestId('project-search'), 'nope');

    const empty = await screen.findByTestId('projects-empty');
    expect(empty).toHaveTextContent('nope');
    // 이 빈 상태를 만든 **그 요청**이 status 를 실어 보냈다는 사실이 문구가 주장할 수
    // 있는 범위의 상한이다. 이 단정이 없으면 아래 문구 단정은 "무엇에 대해 정직한지"를
    // 모르는 문자열 비교로 전락한다.
    expect(platformApi.fetchProjectsPage.mock.calls.at(-1)).toStrictEqual([
      'active',
      'nope',
      undefined,
    ]);
    // 옛 문구("검색어를 바꾸거나 지워서 **전체 목록**을 보세요")는 "내가 받아온 배열"
    // 프레이밍이었고, 그 다음 문구("중앙 프로젝트 **전체**를 검색한 결과")는 반대 방향의
    // 과장이었다 — 요청이 status 로 좁혀져 있으므로 completed 프로젝트는 애초에 조회 대상이
    // 아니다. 정직한 진술은 "현재 상태 필터 범위 안에서의 중앙 검색"이고, 사용자가 그
    // 범위를 넓힐 방법([전체])까지 알려야 D3 의 거짓말이 재발하지 않는다.
    expect(empty).toHaveTextContent('상태 필터 범위 안에서 중앙 프로젝트를 검색한 결과');
    expect(empty).toHaveTextContent('[전체]');
    expect(empty).not.toHaveTextContent('중앙 프로젝트 전체를 검색');
  });

  it('S12 — consumes X-Next-Cursor and sends it back on 더보기', async () => {
    platformApi.fetchProjectsPage
      .mockResolvedValueOnce(page([project({ project_id: 'a', model_name: 'PAGE-1' })], 'cursor-1'))
      .mockResolvedValueOnce(page([project({ project_id: 'b', model_name: 'PAGE-2' })]));
    renderRoute();

    await waitFor(() => expect(screen.getAllByTestId('project-card')).toHaveLength(1));
    await userEvent.click(screen.getByTestId('project-load-more'));

    await waitFor(() => expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(2));
    // 3번째 위치 인자가 1페이지가 준 커서 그대로여야 한다(불투명 토큰 — 가공 금지).
    expect(platformApi.fetchProjectsPage.mock.calls[1]).toStrictEqual([
      'active',
      undefined,
      'cursor-1',
    ]);
    // 페이지가 누적된다(교체 아님).
    await waitFor(() => expect(screen.getAllByTestId('project-card')).toHaveLength(2));
    // 마지막 페이지(`nextCursor === null`) → 버튼 부재.
    expect(screen.queryByTestId('project-load-more')).toBeNull();
  });

  it('S13 — an empty first page WITH a cursor never claims "no projects"', async () => {
    // 병리적이지만 정확히 봉인해야 하는 경우: 서버가 이 페이지에서 0행을 주면서도
    // 커서를 남겼다. "없습니다"를 띄우면 클라이언트 필터가 하던 거짓말이 페이지
    // 경계에서 그대로 재발한다.
    platformApi.fetchProjectsPage
      .mockResolvedValueOnce(page([], 'cursor-1'))
      .mockResolvedValueOnce(page([project({})]));
    renderRoute();

    expect(await screen.findByTestId('project-load-more')).toBeInTheDocument();
    expect(screen.queryByTestId('projects-empty')).toBeNull();
  });

  it('S10 — coalesces a typing burst into ONE extra server read', async () => {
    // `fireEvent`, not `userEvent`: 이 케이스는 디바운스를 fake timer 위에 결정적으로
    // 착지시켜야 하고, userEvent 의 내부 지연은 fake timer 에 대해 resolve 되지 않는다
    // (`chambers.test.tsx` 가 같은 이유로 같은 선택을 했다 — RISK-1).
    vi.useFakeTimers();
    try {
      platformApi.fetchProjectsPage.mockResolvedValue(page([]));
      renderRoute();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(1);

      const input = screen.getByTestId('project-search');
      for (const value of ['g', 'ga', 'gal']) {
        act(() => {
          fireEvent.change(input, { target: { value } });
        });
        // 창이 닫히기 **1ms 전**까지 전진한다. 누적 경과는 창을 훌쩍 넘지만
        // 매 키스트로크가 타이머를 재예약하므로 요청은 한 건도 나가지 않는다 —
        // 이것이 "글자당 1요청 아님"을 실제로 탐지하는 형태다.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS - 1);
        });
        expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(1);
      }

      await act(async () => {
        await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS);
      });
      expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(2);
      // 커밋된 값은 **최종** 값 하나뿐이다(중간 상태 'g'/'ga' 는 서버에 가지 않는다).
      expect(platformApi.fetchProjectsPage.mock.calls[1]).toStrictEqual([
        'active',
        'gal',
        undefined,
      ]);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('MyProjectsRoute status filter + lifecycle (project-status-visibility)', () => {
  afterEach(() => {
    __resetAuthStateForTests();
  });

  it('requests active by default and swaps the RENDERED list on the status toggle', async () => {
    platformApi.fetchProjectsPage.mockImplementation((status?: string) =>
      Promise.resolve(
        status === 'completed'
          ? page([project({ project_id: 'c', model_name: 'DONE-1', status: 'completed' })])
          : page([project({ project_id: 'a', model_name: 'WIP-1', status: 'active' })]),
      ),
    );
    renderRoute();
    await waitFor(() =>
      expect(platformApi.fetchProjectsPage).toHaveBeenCalledWith('active', undefined, undefined),
    );
    // 요청이 나간 것과 페이지가 착지한 것은 다른 시점이다(옛 `useQuery` 경로에서는
    // 한 틱에 겹쳐 보였다). keyset 은 페이지를 flatten 한 뒤 렌더한다.
    expect(await screen.findByTestId('project-card-model')).toHaveTextContent('WIP-1');
    await userEvent.click(screen.getByTestId('project-status-completed'));
    await waitFor(() =>
      expect(screen.getByTestId('project-card-model')).toHaveTextContent('DONE-1'),
    );
  });

  it('hides the lifecycle control for an unauthenticated visitor', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([project({})]));
    renderRoute();
    await waitFor(() => expect(screen.getByTestId('project-card')).toBeInTheDocument());
    expect(screen.queryByTestId('project-card-lifecycle')).toBeNull();
  });

  it('shows the lifecycle control to ANY authenticated user (membership-admin is invisible client-side — backend is the authority)', async () => {
    // No platform:admin token: a project-membership admin looks like this to the
    // client, yet the backend may authorize them. The button must be offered.
    authenticateAs([]);
    platformApi.fetchProjectsPage.mockResolvedValue(page([project({ status: 'active' })]));
    renderRoute();
    expect(await screen.findByTestId('project-card-lifecycle')).toHaveTextContent('완료 처리');
  });

  it('completes an active project and drops it from the active view on refetch', async () => {
    authenticateAs([PERMISSION_PLATFORM_ADMIN]);
    platformApi.fetchProjectsPage
      .mockResolvedValueOnce(page([project({ status: 'active' })])) // initial active list
      .mockResolvedValue(page([])); // after lists() invalidation it has left the active view
    platformApi.completeProject.mockResolvedValue(project({ status: 'completed' }));
    renderRoute();
    await userEvent.click(await screen.findByTestId('project-card-lifecycle'));
    await waitFor(() => expect(platformApi.completeProject).toHaveBeenCalledWith(PROJECT_ID));
    await waitFor(() => expect(screen.queryByTestId('project-card')).toBeNull());
  });

  it('reopens a completed project', async () => {
    authenticateAs([PERMISSION_PLATFORM_ADMIN]);
    platformApi.fetchProjectsPage.mockResolvedValue(page([project({ status: 'completed' })]));
    platformApi.reopenProject.mockResolvedValue(project({ status: 'active' }));
    renderRoute();
    const button = await screen.findByTestId('project-card-lifecycle');
    expect(button).toHaveTextContent('재개');
    await userEvent.click(button);
    await waitFor(() => expect(platformApi.reopenProject).toHaveBeenCalledWith(PROJECT_ID));
  });

  it('surfaces a lifecycle error when the action fails (e.g. backend 403/404)', async () => {
    authenticateAs([PERMISSION_PLATFORM_ADMIN]);
    platformApi.fetchProjectsPage.mockResolvedValue(page([project({ status: 'active' })]));
    platformApi.completeProject.mockRejectedValue(new Error('boom'));
    renderRoute();
    await userEvent.click(await screen.findByTestId('project-card-lifecycle'));
    await waitFor(() => expect(screen.getByTestId('project-lifecycle-error')).toBeInTheDocument());
  });
});

/**
 * W3-B M2 (2026-07-30) — 표지 메타 편집 폼 봉인 (S3 · S4 · S5 · S6 · S7).
 *
 * 백엔드 PATCH 는 **키의 존재 여부가 명령**이다(없으면 불변, `null` 이면 삭제). 여기
 * 단정들은 그 계약이 UI 를 통과해 실제 요청까지 살아남는지를 본다 — 순수 diff 자체는
 * `tests/shared/project-meta-patch.test.ts` 가 커버하고, 이 파일은 배선을 본다:
 * baseline 이 언제 포획되는지, dirty 0 이 요청을 막는지, 409 가 어느 칸에 붙는지.
 */
describe('MyProjectsRoute 표지 메타 편집 (W3-B M2)', () => {
  afterEach(() => {
    __resetAuthStateForTests();
  });

  /** 편집 폼이 보이는 상태로 카드 1장을 렌더한다. */
  async function renderEditableCard(over: Record<string, unknown> = {}): Promise<HTMLElement> {
    // 인증만 요구한다 — 토큰 권한으로 게이트하지 않는다(멤버십-admin false negative).
    authenticateAs([]);
    platformApi.fetchProjectsPage.mockResolvedValue(page([project(over)]));
    renderRoute();
    return screen.findByTestId('project-meta-form');
  }

  it('S3 — binds ONLY the eight editable cover fields (no identity/lifecycle input)', async () => {
    await renderEditableCard();

    // 있어야 하는 칸: 편집 가능한 8개.
    for (const field of [
      'management_number',
      'customer',
      'applicant_name',
      'applicant_address',
      'manufacturer',
      'fcc_grantee_code',
      'eut_description',
      'test_standard',
    ]) {
      expect(screen.getByTestId(`project-meta-${field}`)).toBeInTheDocument();
    }

    // 없어야 하는 칸: model_name/project_code 는 re-key(ADR-0005), status 는
    // complete/reopen 하위자원, fcc_id 는 서버 파생값 — PATCH 는 전부 400 으로
    // 거절하므로 폼에 실리면 저장이 통째로 실패한다.
    for (const forbidden of ['model_name', 'project_code', 'status', 'fcc_id']) {
      expect(screen.queryByTestId(`project-meta-${forbidden}`)).toBeNull();
    }

    // 파생값 미리보기 금지 — grantee code 를 타이핑해도 FCC ID 는 나타나지 않는다
    // (서버가 준 값만 카드에 뜬다; 여기서는 서버가 주지 않았으므로 없다).
    await userEvent.type(screen.getByTestId('project-meta-fcc_grantee_code'), 'A3L');
    expect(screen.queryByTestId('project-card-fcc')).toBeNull();
  });

  it('S4 — PATCHes ONLY the dirty field and invalidates the list + detail reads', async () => {
    await renderEditableCard();
    platformApi.updateProject.mockResolvedValue(project({ customer: 'ACME2' }));
    const listReadsBeforeSave = platformApi.fetchProjectsPage.mock.calls.length;

    await userEvent.type(screen.getByTestId('project-meta-customer'), 'ACME2');
    await userEvent.click(screen.getByTestId('project-meta-save'));

    await waitFor(() => expect(platformApi.updateProject).toHaveBeenCalledTimes(1));
    const call = platformApi.updateProject.mock.calls[0] as [string, Record<string, unknown>];
    expect(call[0]).toBe(PROJECT_ID);
    // 2겹 — 값 + 키 집합. 나머지 7칸의 키가 실리면 그 칸들이 lost-update 표면이 된다.
    expect(call[1]).toStrictEqual({ customer: 'ACME2' });
    expect(Object.keys(call[1])).toStrictEqual(['customer']);

    // 성공 후 로컬 편집이 버려지고 행이 다시 서버를 따라간다 → 목록 재조회.
    // 절대 횟수가 아니라 **증가**를 본다: 포커스/윈도 refetch 전략이 바뀌면 절대
    // 횟수는 흔들리지만 "쓰기 후 읽기가 한 번 더 일어난다" 는 계약은 그대로다.
    // 무효화 키가 `lists()` 프리픽스이므로 keyset `directory` 리프도 덮인다 —
    // 리프를 프리픽스 밖으로 옮기면 이 단정이 red 가 된다(CP-2).
    await waitFor(() =>
      expect(platformApi.fetchProjectsPage.mock.calls.length).toBeGreaterThan(listReadsBeforeSave),
    );
    expect(await screen.findByTestId('project-meta-success')).toBeInTheDocument();
  });

  it('S4 — clearing a populated field sends null (delete), not an absent key', async () => {
    await renderEditableCard({ applicant_name: 'ACME Corp.' });
    platformApi.updateProject.mockResolvedValue(project({ applicant_name: null }));

    await userEvent.clear(screen.getByTestId('project-meta-applicant_name'));
    await userEvent.click(screen.getByTestId('project-meta-save'));

    await waitFor(() => expect(platformApi.updateProject).toHaveBeenCalledTimes(1));
    const [, body] = platformApi.updateProject.mock.calls[0] as [string, Record<string, unknown>];
    expect(body).toStrictEqual({ applicant_name: null });
    expect(Object.keys(body)).toStrictEqual(['applicant_name']);
  });

  it('S5 — save is disabled while pristine and never sends an empty body (400)', async () => {
    await renderEditableCard({ customer: 'ACME' });

    // 백엔드는 편집 가능한 키가 하나도 없는 body 를 no-op 이 아니라 400 으로
    // 거절한다 → dirty 0 게이트는 UX 취향이 아니라 계약이다.
    expect(screen.getByTestId('project-meta-save')).toBeDisabled();
    expect(screen.getByTestId('project-meta-discard')).toBeDisabled();

    await userEvent.type(screen.getByTestId('project-meta-customer'), '2');
    expect(screen.getByTestId('project-meta-save')).toBeEnabled();
    expect(screen.getByTestId('project-meta-unsaved')).toBeInTheDocument();

    // 되돌리기 → 다시 pristine, 요청 0건.
    await userEvent.click(screen.getByTestId('project-meta-discard'));
    await waitFor(() => expect(screen.getByTestId('project-meta-save')).toBeDisabled());
    expect(platformApi.updateProject).not.toHaveBeenCalled();
  });

  it('S5 — a whitespace-only edit does not enable save (trim ⇒ no change)', async () => {
    await renderEditableCard({ customer: 'ACME' });
    await userEvent.type(screen.getByTestId('project-meta-customer'), '   ');
    expect(screen.getByTestId('project-meta-save')).toBeDisabled();
  });

  it('S6 — typing then undoing back to the server value returns to pristine', async () => {
    // 이것이 "baseline 은 첫 키스트로크에 1회 포획" 을 실제로 탐지하는 단정이다.
    // baseline 이 키스트로크마다 **직전 draft** 로 갱신되면, 되돌린 뒤에도 diff 가
    // 직전 draft(='ACME2') 와 비교되어 비지 않는다 → 사용자가 아무것도 바꾸지
    // 않았는데 저장이 활성이고, 누르면 불필요한 쓰기가 나간다(그 칸이 그 사이 다른
    // 사람에 의해 바뀌었다면 lost update).
    //
    // 아래 "최종 값 1회 전송" 케이스만으로는 이 결함을 못 잡는다: baseline 이
    // 움직여도 마지막 문자가 다르므로 patch 값 자체는 동일하게 나온다.
    await renderEditableCard({ customer: 'ACME' });
    const input = screen.getByTestId('project-meta-customer');

    await userEvent.type(input, '2');
    expect(screen.getByTestId('project-meta-save')).toBeEnabled();

    await userEvent.type(input, '{backspace}');
    expect(input).toHaveValue('ACME');
    await waitFor(() => expect(screen.getByTestId('project-meta-save')).toBeDisabled());
    expect(platformApi.updateProject).not.toHaveBeenCalled();
  });

  it('S6 — the baseline is captured ONCE, at the first keystroke', async () => {
    await renderEditableCard({ customer: 'ACME' });
    platformApi.updateProject.mockResolvedValue(project({ customer: 'ACMEXYZ' }));

    // 세 번의 키스트로크. baseline 이 키스트로크마다 다시 포획되면 diff 는 매번
    // 자기 자신과 비교되어 비고, 저장 버튼은 영원히 비활성으로 남는다.
    await userEvent.type(screen.getByTestId('project-meta-customer'), 'XYZ');
    expect(screen.getByTestId('project-meta-save')).toBeEnabled();

    await userEvent.click(screen.getByTestId('project-meta-save'));
    await waitFor(() => expect(platformApi.updateProject).toHaveBeenCalledTimes(1));
    const [, body] = platformApi.updateProject.mock.calls[0] as [string, Record<string, unknown>];
    // 최종 값 1회 전송 — 중간 상태('ACMEX'/'ACMEXY')가 아니라 마지막 값.
    expect(body).toStrictEqual({ customer: 'ACMEXYZ' });
  });

  it('S6 — an untouched card reads straight from the server (no state mirror)', async () => {
    await renderEditableCard({ customer: 'ACME', manufacturer: 'ACME Mfg.' });
    // 편집 상태는 **건드린 프로젝트만** 담는다. 미편집 칸은 매 렌더 서버 값을
    // 파생하므로 동기화 effect 가 필요 없다(있으면 폴링이 타이핑을 지운다).
    expect(screen.getByTestId('project-meta-customer')).toHaveValue('ACME');
    expect(screen.getByTestId('project-meta-manufacturer')).toHaveValue('ACME Mfg.');
    expect(screen.getByTestId('project-meta-applicant_name')).toHaveValue('');
  });

  it('S6 — the search debounce does NOT disturb an in-flight edit (M-B × M2)', async () => {
    // M-B 가 이 라우트에 처음으로 `useEffect` 를 들여왔다. 그 effect 가 `setEdits`
    // 를 만지면 M2 가 없앤 서버-상태 미러 결함이 되돌아온다 — 타이핑 중 목록이
    // 다시 읽히면서 미저장 편집이 날아가는 형태로. 검색을 커밋해 새 읽기를
    // 발생시킨 뒤에도 편집 draft 가 남아 있는지 본다.
    //
    // 검색어 커밋은 queryKey 를 바꾸므로 캐시 없는 새 쿼리가 시작되고, 그동안
    // 목록(=카드, =편집 폼)이 언마운트된다. 편집 draft 가 살아남는 것은 `edits`
    // 상태가 **라우트**에 있고 `projectId` 로 키잉돼 있기 때문이다 — 카드 안의
    // `useState` 였다면 검색 한 번에 미저장 편집이 사라진다.
    await renderEditableCard({ customer: 'ACME' });
    await userEvent.type(screen.getByTestId('project-meta-customer'), '2');
    expect(screen.getByTestId('project-meta-save')).toBeEnabled();

    const readsBefore = platformApi.fetchProjectsPage.mock.calls.length;
    await userEvent.type(screen.getByTestId('project-search'), 'ACME');
    await waitFor(() =>
      expect(platformApi.fetchProjectsPage.mock.calls.length).toBeGreaterThan(readsBefore),
    );

    // 새 페이지가 착지해 카드가 다시 마운트된 뒤에도 draft 가 그대로다.
    await waitFor(() => expect(screen.getByTestId('project-meta-customer')).toHaveValue('ACME2'));
    expect(screen.getByTestId('project-meta-save')).toBeEnabled();
  });

  it('S7 — a 409 attributes to the offending input when the field is on this form', async () => {
    await renderEditableCard({ management_number: '4792232056' });
    platformApi.updateProject.mockRejectedValue(
      Object.assign(new Error('conflict'), {
        status: 409,
        code: 'PROJECT_IDENTIFIER_CONFLICT',
        params: { field: 'management_number' },
      }),
    );

    await userEvent.type(screen.getByTestId('project-meta-management_number'), '9');
    await userEvent.click(screen.getByTestId('project-meta-save'));

    await waitFor(() =>
      expect(screen.getByTestId('project-meta-management_number')).toHaveAttribute(
        'aria-invalid',
        'true',
      ),
    );
    // 다른 칸에는 붙지 않는다 — 붙으면 사용자가 엉뚱한 칸을 고친다.
    expect(screen.getByTestId('project-meta-customer')).not.toHaveAttribute('aria-invalid');
    expect(screen.getByTestId('project-meta-error')).toBeInTheDocument();
  });

  it('S7 — a 409 for a field NOT on this form falls back to the generic message', async () => {
    // `PROJECT_IDENTIFIER_CONFLICT` 는 폼에 없는 `project_code` 충돌로도 발생한다.
    // 그 이름을 억지로 어딘가에 붙이면 안 되고, 일반 충돌 문구로 폴백해야 한다.
    // (이 케이스가 있어야 S7 의 allowlist 가 비-공허해진다.)
    await renderEditableCard({ customer: 'ACME' });
    platformApi.updateProject.mockRejectedValue(
      Object.assign(new Error('conflict'), {
        status: 409,
        code: 'PROJECT_IDENTIFIER_CONFLICT',
        params: { field: 'project_code' },
      }),
    );

    await userEvent.type(screen.getByTestId('project-meta-customer'), '2');
    await userEvent.click(screen.getByTestId('project-meta-save'));

    const error = await screen.findByTestId('project-meta-error');
    expect(error).toHaveTextContent('같은 번호를 쓰는 프로젝트가 이미 있습니다.');
    for (const field of ['management_number', 'customer', 'fcc_grantee_code']) {
      expect(screen.getByTestId(`project-meta-${field}`)).not.toHaveAttribute('aria-invalid');
    }
  });

  it('S7 — a 409 WITHOUT params falls back too (params is optional on the wire)', async () => {
    await renderEditableCard({ customer: 'ACME' });
    platformApi.updateProject.mockRejectedValue(
      Object.assign(new Error('conflict'), { status: 409 }),
    );

    await userEvent.type(screen.getByTestId('project-meta-customer'), '2');
    await userEvent.click(screen.getByTestId('project-meta-save'));

    expect(await screen.findByTestId('project-meta-error')).toBeInTheDocument();
    expect(screen.getByTestId('project-meta-customer')).not.toHaveAttribute('aria-invalid');
  });

  it('hides the edit form from an unauthenticated visitor', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([project({})]));
    renderRoute();
    await waitFor(() => expect(screen.getByTestId('project-card')).toBeInTheDocument());
    expect(screen.queryByTestId('project-meta-form')).toBeNull();
  });
});
