import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ProjectSelectField } from '@/shared/ProjectSelectField';
import { SEARCH_DEBOUNCE_MS } from '@/shared/search-debounce';

import type { ReactElement } from 'react';

/**
 * ProjectSelectField container test (project-picker-ssot, 2026-06-26).
 *
 * The field binds the central project directory to the presentational
 * `<ProjectPicker>`. The suite seals: a tester picks by model · management
 * number (never types a UUID) and the resolved `project_id` is committed; and
 * that loading / error / empty each surface a distinct status note (no silent
 * empty list). Rendered copy is Korean (the suite locale is pinned to `ko`).
 *
 * W3-B M-C (2026-07-30): the read moved from the unbounded `fetchProjects` to
 * ONE keyset page (`fetchProjectsPage`) narrowed server-side by `q`. The new
 * cases below seal the honesty properties that move is for — a search term
 * reaches the server verbatim, a blank one sends no `q` at all, a burst is
 * debounced into one read, and a page that clipped its matches SAYS so instead
 * of quietly showing a short list.
 */
const platformApi = vi.hoisted(() => ({ fetchProjectsPage: vi.fn() }));
vi.mock('@/api/platform-client', () => platformApi);

function project(over: Record<string, unknown>): Record<string, unknown> {
  return {
    project_id: '11111111-1111-4111-8111-111111111111',
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

/** One keyset page as `fetchProjectsPage` returns it. */
function page(
  items: readonly Record<string, unknown>[],
  nextCursor: string | null = null,
): { items: readonly Record<string, unknown>[]; nextCursor: string | null } {
  return { items, nextCursor };
}

function Harness(): ReactElement {
  const [value, setValue] = useState('');
  return (
    <>
      <ProjectSelectField
        value={value}
        onChange={setValue}
        selectId="pick"
        selectTestId="pick-select"
        statusTestId="pick-status"
        searchTestId="pick-search"
      />
      {/* `<output>` 는 암묵 role="status" 라 picker 의 라이브 리전 음성 단정과
          충돌한다 — 하네스는 역할 없는 노드로 값을 노출한다. */}
      <span data-testid="picked">{value}</span>
    </>
  );
}

function renderField(): QueryClient {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  );
  return queryClient;
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('ProjectSelectField', () => {
  it('lists projects by model · management number and commits the project_id', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(
      page([
        project({ project_id: 'p-1', model_name: 'SM-S921U', management_number: 'M-001' }),
        project({ project_id: 'p-2', model_name: 'SM-A546', management_number: 'M-002' }),
      ]),
    );
    renderField();

    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'SM-S921U · M-001' })).toHaveValue('p-1'),
    );
    expect(screen.getByRole('option', { name: 'SM-A546 · M-002' })).toHaveValue('p-2');

    await userEvent.selectOptions(screen.getByTestId('pick-select'), 'p-2');
    expect(screen.getByTestId('picked')).toHaveTextContent('p-2');
  });

  it('reads ONE active page with no q on first mount (never the unbounded list)', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([]));
    renderField();
    await waitFor(() => expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(1));
    // 위치 인자 (status, q, cursor). 3번째 인자는 **전달되지 않는다** — 선택기는
    // 페이지 2+ 를 읽지 않는다(그 사실이 잔여 고지의 존재 이유다). `toStrictEqual`
    // 은 arity 까지 비교하므로 커서를 넘기기 시작하면 이 단정이 깨진다.
    expect(platformApi.fetchProjectsPage.mock.calls[0]).toStrictEqual(['active', undefined]);
  });

  it('AC-7 — derives a search id DISTINCT from the select id (labels stay unambiguous)', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([project({ project_id: 'p-1' })]));
    renderField();
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'SM-S921U' })).toBeInTheDocument(),
    );

    // 이 단정은 **컨테이너의 id 파생**을 봉인한다. `ProjectPicker.test.tsx` 의
    // 같은 이름 단정은 프리미티브에 하드코딩된 props 를 볼 뿐이라, 컨테이너가
    // `inputId: selectId` 로 충돌시켜도 영원히 green 이다(red→green 실증 R4 가
    // 그 공백을 실제로 드러냈다). 두 label 이 같은 id 를 가리키면 아래 조회가
    // 뒤바뀌거나 모호해진다 — 그것이 관측 가능한 접근성 손상이다.
    const search = screen.getByLabelText('프로젝트 검색');
    const select = screen.getByLabelText('프로젝트');
    expect(search.tagName).toBe('INPUT');
    expect(select.tagName).toBe('SELECT');
    expect(select).toHaveAttribute('id', 'pick');
    expect(search.getAttribute('id')).not.toBe(select.getAttribute('id'));
  });

  // ── status note 5분기 (AC-8 ③) ────────────────────────────────────────────

  it('status 1/5 — pending: loading note + select disabled', async () => {
    // 영원히 settle 하지 않는 promise → 쿼리를 `isPending` 에 고정한다.
    platformApi.fetchProjectsPage.mockReturnValue(new Promise<never>(() => undefined));
    renderField();
    await waitFor(() =>
      expect(screen.getByTestId('pick-status')).toHaveTextContent('프로젝트 불러오는 중…'),
    );
    expect(screen.getByTestId('pick-select')).toBeDisabled();
  });

  it('status 2/5 — error: error note + select disabled', async () => {
    platformApi.fetchProjectsPage.mockRejectedValue(new Error('boom'));
    renderField();
    await waitFor(() =>
      expect(screen.getByTestId('pick-status')).toHaveTextContent(
        '프로젝트를 불러오지 못했습니다.',
      ),
    );
    expect(screen.getByTestId('pick-select')).toBeDisabled();
  });

  it('status 3/5 — empty with NO search: "create one" is the right advice', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([]));
    renderField();
    await waitFor(() =>
      expect(screen.getByTestId('pick-status')).toHaveTextContent(
        '프로젝트가 없습니다 — 내 프로젝트에서 생성하세요.',
      ),
    );
  });

  it('status 4/5 — empty WITH a search: no-match, not "no projects exist"', async () => {
    platformApi.fetchProjectsPage
      .mockResolvedValueOnce(page([project({ project_id: 'p-1' })]))
      .mockResolvedValue(page([]));
    renderField();
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'SM-S921U' })).toBeInTheDocument(),
    );

    await userEvent.type(screen.getByTestId('pick-search'), 'zzz');

    const note = await screen.findByTestId('pick-status');
    await waitFor(() => expect(note).toHaveTextContent('검색과 일치하는 활성 프로젝트가 없습니다'));

    // ★ 이 빈 상태를 만든 **요청의 범위**를 함께 고정한다. status 축이 'active'
    // 로 고정돼 있으므로 완료된 프로젝트는 아예 조회 대상이 아니다 — 문구가
    // "중앙 디렉토리 전체를 검색했다" 고 주장하면 그것은 과장이며, D4 가 지운
    // 거짓말("로드된 것만 훑음")을 반대 방향으로 되살린다. 이 인자 단정이
    // 아래 음성 단정을 비-공허하게 유지한다: status 축을 넓히거나 좁히는 변경이
    // 오면 문구를 함께 고치도록 여기서 red 가 난다. (`at(-1)` — S9 와 동일한
    // 이유로, 타이핑 중 중간 커밋이 섞여도 마지막 커밋이 화면의 근거다.)
    expect(platformApi.fetchProjectsPage.mock.calls.at(-1)).toStrictEqual(['active', 'zzz']);

    // 검색 중일 때 "생성하세요" 조언이 나오면 그것은 틀린 조언이다.
    expect(note).not.toHaveTextContent('내 프로젝트에서 생성하세요');
    // 그리고 조회 범위를 실제보다 넓게 주장해서도 안 된다.
    expect(note).not.toHaveTextContent('중앙 디렉토리 전체');
    // 대신 좁혀진 범위와 그 밖을 보는 법이 함께 드러난다.
    expect(note).toHaveTextContent('활성 프로젝트 전체를 중앙에서 검색');
    expect(note).toHaveTextContent('완료된 프로젝트는 제외');
  });

  it('status 5/5 — a full list with no leftovers shows NO note at all', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([project({ project_id: 'p-1' })], null));
    renderField();
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'SM-S921U' })).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('pick-status')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
  });

  // ── S13 · 잔여 고지 (AC-8 ②) ──────────────────────────────────────────────

  it('S13 — a clipped page SAYS so in the polite live region (D4 의 거짓말 제거)', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(
      page(
        [
          project({ project_id: 'p-1', model_name: 'SM-S921U' }),
          project({ project_id: 'p-2', model_name: 'SM-A546' }),
        ],
        'cursor-1',
      ),
    );
    renderField();

    const note = await screen.findByTestId('pick-status');
    expect(note).toHaveAttribute('role', 'status');
    expect(note).toHaveTextContent('일치하는 프로젝트가 더 있습니다');
    // 표시 개수는 실제 렌더된 옵션 수여야 한다(고정 문자열이 아님).
    expect(note).toHaveTextContent('2개만 표시');
    // 그러나 페이지 2+ 를 읽지는 않는다 — `<select>` 안의 [더보기]는 오답이다.
    expect(screen.queryByText('더보기')).toBeNull();
    expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(1);
  });

  // ── S9 / S11 · 서버측 검색 ────────────────────────────────────────────────

  it('S9 — the search term reaches the server verbatim, and the page renders as given', async () => {
    platformApi.fetchProjectsPage
      .mockResolvedValueOnce(page([project({ project_id: 'a', model_name: 'SM-S921U' })]))
      // 서버가 (예컨대 고객명으로) 매칭한 행. 렌더되는 어떤 문자열도 'globex' 를
      // 포함하지 않으므로 클라이언트 재필터가 남아 있으면 이 옵션이 사라진다.
      .mockResolvedValue(page([project({ project_id: 'b', model_name: 'GALAXY-Z' })]));
    renderField();
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'SM-S921U' })).toBeInTheDocument(),
    );

    await userEvent.type(screen.getByTestId('pick-search'), 'globex');

    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'GALAXY-Z' })).toBeInTheDocument(),
    );
    expect(platformApi.fetchProjectsPage.mock.calls.at(-1)).toStrictEqual(['active', 'globex']);
  });

  it('S11 — a whitespace-only search sends no q key at all (and no extra read)', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([]));
    renderField();
    await waitFor(() => expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(1));

    await userEvent.type(screen.getByTestId('pick-search'), '   ');
    // "요청이 나가지 않는다"는 음성 단정이라 `waitFor` 로는 표현할 수 없다 —
    // 디바운스 창을 넘겨 기다린 뒤 세야 한다.
    await new Promise((resolve) => {
      window.setTimeout(resolve, SEARCH_DEBOUNCE_MS * 2);
    });

    expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(1);
    for (const call of platformApi.fetchProjectsPage.mock.calls) {
      expect((call as unknown[])[1]).toBeUndefined();
    }
  });

  // ── S10 · 디바운스 ────────────────────────────────────────────────────────

  it('S10 — coalesces a typing burst into ONE extra server read', async () => {
    // `fireEvent`, not `userEvent`: 디바운스를 fake timer 위에 결정적으로
    // 착지시켜야 하는데 userEvent 의 내부 지연은 fake timer 로 resolve 되지 않는다.
    vi.useFakeTimers();
    try {
      platformApi.fetchProjectsPage.mockResolvedValue(page([]));
      renderField();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(1);

      const input = screen.getByTestId('pick-search');
      for (const value of ['g', 'ga', 'gal']) {
        act(() => {
          fireEvent.change(input, { target: { value } });
        });
        // 창이 닫히기 1ms 전까지만 전진한다. 누적 경과는 창을 훌쩍 넘지만 매
        // 키스트로크가 타이머를 재예약하므로 요청은 한 건도 나가지 않는다.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS - 1);
        });
        expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(1);
      }

      await act(async () => {
        await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS);
      });
      expect(platformApi.fetchProjectsPage).toHaveBeenCalledTimes(2);
      // 커밋된 것은 **최종** 값 하나뿐 — 'g'/'ga' 는 서버에 가지 않는다.
      expect(platformApi.fetchProjectsPage.mock.calls[1]).toStrictEqual(['active', 'gal']);
    } finally {
      vi.useRealTimers();
    }
  });

  // ── AC-8 ① · 배경 refetch 중에도 검색은 살아 있다 ─────────────────────────

  it('keeps the search box enabled and its text intact across a background refetch', async () => {
    platformApi.fetchProjectsPage.mockResolvedValue(page([project({ project_id: 'p-1' })]));
    const queryClient = renderField();
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'SM-S921U' })).toBeInTheDocument(),
    );

    const input = screen.getByTestId('pick-search');
    await userEvent.type(input, 'SM');
    expect(input).toHaveValue('SM');

    // 배경 refetch: 캐시가 이미 있으므로 `isPending` 은 false, `isFetching` 만 true.
    await act(async () => {
      await queryClient.refetchQueries();
    });

    // 이 순간 입력이 비활성화되거나 값이 리셋되면 사용자는 타이핑 도중 컨트롤을
    // 빼앗긴다 — 그것이 이 단정이 막는 결함이다.
    expect(screen.getByTestId('pick-search')).toBeEnabled();
    expect(screen.getByTestId('pick-search')).toHaveValue('SM');
    expect(screen.getByTestId('pick-select')).toBeEnabled();
  });
});
