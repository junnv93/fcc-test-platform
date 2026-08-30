import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { t } from '@/i18n';
import ReferenceDataRoute from '@/routes/reference-data';

import type { ReactElement } from 'react';

/**
 * 참조 데이터 저작 — 시험원이 화면만으로 완주할 수 있는가 (2026-08-09).
 *
 * 백엔드가 fork·편집·게시를 전부 갖춰도, 화면이 그것을 부르지 않으면 시험원 앞에서는
 * 아무 일도 일어나지 않는다. 이 저장소는 그 형태로 값을 치른 적이 있다 — 참조 표면
 * 4 operation 이 **합성되지 않아** 전부 런타임 `RuntimeError` 인 동안, 조각을 검증하는
 * 테스트는 전부 green 이었다. 그래서 여기서는 조각이 아니라 **연결**을 본다.
 *
 * 함께 잠그는 정직성 규칙 셋:
 *
 * * **식별 열은 서버가 말한다.** 화면은 `identity_columns` 를 읽기만 하고 다시
 *   선언하지 않는다. 재선언하면 같은 규칙이 두 언어로 갈라지고, 그 드리프트는
 *   시험원이 식별 칸을 고칠 수 있게 된 뒤에야 드러난다.
 * * **바뀐 행만 간다.** correction 곡선은 수천 점이고, 전량 재전송은 낭비이자
 *   lost-update 통로다.
 * * **저장 안 한 편집이 있으면 게시할 수 없다.** 게시는 저장된 내용을 싣는다 —
 *   막지 않으면 화면의 숫자가 아니라 그 이전 숫자가 장비 앞에 놓이고 화면은
 *   성공이라고 말한다.
 *
 * `DataTable` 은 반응형이라 같은 셀을 데스크톱 표와 모바일 카드 양쪽에 렌더한다.
 * 그래서 `getAllBy…[0]` 을 쓴다 — 둘은 같은 칸이고, 하나만 나오도록 프리미티브를
 * 바꾸는 것은 이 라우트가 공유 컴포넌트의 반응형 계약을 좁히는 일이 된다.
 * 문구 단언은 `t()` 를 통과시킨다: 리터럴을 적으면 기본 로케일이 바뀌는 날 깨진다.
 */

const platformApi = vi.hoisted(() => ({
  fetchProviderList: vi.fn(),
  fetchChambers: vi.fn(),
  fetchReferenceRevisions: vi.fn(),
  fetchReferenceRevision: vi.fn(),
  forkReferenceRevision: vi.fn(),
  updateReferenceRevisionEntries: vi.fn(),
  publishReferenceRevision: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

const PROVIDER = 'fcc-unlicensed-conducted';

/**
 * 반응형 `DataTable` 이 같은 칸을 두 번 렌더하므로 첫 번째를 집는다.
 *
 * `[0]` 을 그냥 쓰면 `noUncheckedIndexedAccess` 아래에서 `undefined` 가 섞여 온다.
 * 비-널 단언(`!`)으로 지우는 대신 여기서 **없으면 실패**시킨다 — 요소가 사라진 날
 * 타입이 아니라 테스트가 그 사실을 말하게 하려는 것이다.
 */
function first(elements: readonly HTMLElement[]): HTMLElement {
  const [element] = elements;
  if (element === undefined) {
    throw new Error('expected at least one matching element');
  }
  return element;
}

function summary(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    revision_id: 'rev-published',
    provider_id: PROVIDER,
    family: 'correction',
    profile_id: 'default',
    scope_kind: 'room',
    scope_id: 'room-1',
    revision_number: 1,
    state: 'PUBLISHED',
    version: 1,
    etag: 'etag-published',
    content_sha256: 'c'.repeat(64),
    source_snapshot_id: 'snap-1',
    source_manifest_sha256: 'm'.repeat(64),
    provenance_kind: 'WORKBOOK',
    entry_count: 1,
    created_by: 'importer',
    created_at: '2026-08-09T00:00:00Z',
    updated_by: 'importer',
    updated_at: '2026-08-09T00:00:00Z',
    ...overrides,
  };
}

function detail(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    revision: summary(),
    entries: [
      {
        reference_id: 'ref-0',
        identity_key: 'correction|row=0',
        entry_order: 0,
        payload: { correction_index: 'A', frequency_hz: 2400, correction_db: 1.5 },
        content_sha256: 'd'.repeat(64),
      },
    ],
    // 서버가 준다 — 화면은 읽기만 한다.
    payload_columns: ['correction_index', 'frequency_hz', 'correction_db'],
    identity_columns: ['correction_index', 'frequency_hz'],
    coupled_with: null,
    ...overrides,
  };
}

function renderRoute(): ReactElement {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[`/reference-data?provider=${PROVIDER}&revision=rev-published`]}
      >
        <ReferenceDataRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  platformApi.fetchProviderList.mockResolvedValue([{ provider_id: PROVIDER }]);
  platformApi.fetchChambers.mockResolvedValue([]);
  platformApi.fetchReferenceRevisions.mockResolvedValue({ items: [summary()] });
  platformApi.fetchReferenceRevision.mockResolvedValue(detail());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('reference data authoring', () => {
  it('offers a fork on a published revision and calls the operation', async () => {
    platformApi.forkReferenceRevision.mockResolvedValue(
      detail({ revision: summary({ revision_id: 'rev-fork', state: 'CANDIDATE' }) }),
    );
    render(renderRoute());

    const fork = first(await screen.findAllByTestId('reference-data-fork'));
    fireEvent.click(fork);

    await waitFor(() => {
      expect(platformApi.forkReferenceRevision).toHaveBeenCalledWith(PROVIDER, 'rev-published');
    });
  });

  it('does not offer a fork on a candidate', async () => {
    platformApi.fetchReferenceRevision.mockResolvedValue(
      detail({ revision: summary({ state: 'CANDIDATE', etag: 'etag-candidate' }) }),
    );
    render(renderRoute());

    await screen.findByTestId('reference-data-entries-table');
    expect(screen.queryAllByTestId('reference-data-fork')).toHaveLength(0);
  });

  it('renders identity columns as text and value columns as inputs', async () => {
    platformApi.fetchReferenceRevision.mockResolvedValue(
      detail({ revision: summary({ state: 'CANDIDATE', etag: 'etag-candidate' }) }),
    );
    render(renderRoute());

    await screen.findByTestId('reference-data-entries-table');
    // correction_db is the only non-identity column in the server's answer.
    expect(screen.getAllByTestId('reference-data-cell-ref-0-correction_db').length).toBeGreaterThan(
      0,
    );
    for (const identity of ['correction_index', 'frequency_hz']) {
      expect(screen.queryAllByTestId(`reference-data-cell-ref-0-${identity}`)).toHaveLength(0);
    }
  });

  it('takes the identity columns from the server, not from a local guess', async () => {
    // 봉인 공백으로 발견됐다(2026-08-09 적대적 리뷰): `identityColumns` 를 픽스처와
    // **같은** 리터럴로 하드코딩해도 아무 테스트가 red 가 되지 않았다 — 모든 픽스처가
    // 기본 집합을 썼기 때문이다. 서버가 **다른** 집합을 주는 케이스가 그 구별을 만든다.
    platformApi.fetchReferenceRevision.mockResolvedValue(
      detail({
        revision: summary({ state: 'CANDIDATE', etag: 'etag-candidate' }),
        // 서버가 식별 축을 다르게 말한다: correction_db 가 식별이고 frequency_hz 는 값.
        identity_columns: ['correction_index', 'correction_db'],
      }),
    );
    render(renderRoute());

    await screen.findByTestId('reference-data-entries-table');
    // 서버 말대로라면 값 칸은 frequency_hz 하나뿐이다.
    expect(screen.getAllByTestId('reference-data-cell-ref-0-frequency_hz').length).toBeGreaterThan(
      0,
    );
    for (const identity of ['correction_index', 'correction_db']) {
      expect(screen.queryAllByTestId(`reference-data-cell-ref-0-${identity}`)).toHaveLength(0);
    }
  });

  it('keeps published revisions read-only', async () => {
    render(renderRoute());
    await screen.findByTestId('reference-data-entries-table');
    expect(screen.queryAllByTestId('reference-data-cell-ref-0-correction_db')).toHaveLength(0);
  });

  it('sends only the changed row, with the server etag echoed back', async () => {
    platformApi.fetchReferenceRevision.mockResolvedValue(
      detail({ revision: summary({ state: 'CANDIDATE', etag: 'etag-candidate' }) }),
    );
    platformApi.updateReferenceRevisionEntries.mockResolvedValue(detail());
    render(renderRoute());

    const cell = first(await screen.findAllByTestId('reference-data-cell-ref-0-correction_db'));
    fireEvent.change(cell, { target: { value: '9.75' } });
    fireEvent.click(first(screen.getAllByTestId('reference-data-save')));

    await waitFor(() => {
      expect(platformApi.updateReferenceRevisionEntries).toHaveBeenCalledWith(
        PROVIDER,
        'rev-published',
        {
          expected_etag: 'etag-candidate',
          edits: [
            {
              reference_id: 'ref-0',
              payload: {
                correction_index: 'A',
                frequency_hz: 2400,
                // 원래가 숫자였으므로 숫자로 되돌아간다 — 문자열로 보내면 시험원이
                // 만지지도 않은 열의 표현이 편집 때문에 바뀐다.
                correction_db: 9.75,
              },
            },
          ],
        },
      );
    });
  });

  it('does not send anything when the typed value equals the stored one', async () => {
    platformApi.fetchReferenceRevision.mockResolvedValue(
      detail({ revision: summary({ state: 'CANDIDATE', etag: 'etag-candidate' }) }),
    );
    render(renderRoute());

    const cell = first(await screen.findAllByTestId('reference-data-cell-ref-0-correction_db'));
    fireEvent.change(cell, { target: { value: '9.75' } });
    fireEvent.change(cell, { target: { value: '1.5' } });

    expect(first(screen.getAllByTestId('reference-data-save')).hasAttribute('disabled')).toBe(true);
  });

  it('refuses to save a cell it could not read, and names it', async () => {
    // 적대적 리뷰가 실증한 입력. `Number('1,5')` 는 NaN 이라 초판은 원문 문자열을
    // 그대로 보냈고, 서버에도 투영에도 타입 검사가 없어 correction dB 가 문자열로
    // 저장됐다. 이제 화면이 보내지 않고 어느 칸인지 말한다.
    platformApi.fetchReferenceRevision.mockResolvedValue(
      detail({ revision: summary({ state: 'CANDIDATE', etag: 'etag-candidate' }) }),
    );
    render(renderRoute());

    fireEvent.change(
      first(await screen.findAllByTestId('reference-data-cell-ref-0-correction_db')),
      { target: { value: '1,5' } },
    );

    const note = await screen.findByTestId('reference-data-unreadable');
    expect(note.textContent).toContain('correction_db');
    expect(first(screen.getAllByTestId('reference-data-save')).hasAttribute('disabled')).toBe(true);
    expect(platformApi.updateReferenceRevisionEntries).not.toHaveBeenCalled();
  });

  it('does not read hex or binary literals as numbers', async () => {
    // `Number('0x10') === 16`. dB 칸에 그것을 친 사람이 16 을 의도했을 리 없다 —
    // 언어의 관용이 곧 이 도메인의 관용은 아니다.
    platformApi.fetchReferenceRevision.mockResolvedValue(
      detail({ revision: summary({ state: 'CANDIDATE', etag: 'etag-candidate' }) }),
    );
    render(renderRoute());

    fireEvent.change(
      first(await screen.findAllByTestId('reference-data-cell-ref-0-correction_db')),
      { target: { value: '0x10' } },
    );

    expect(screen.getByTestId('reference-data-unreadable')).toBeTruthy();
  });

  it('still accepts an ordinary decimal edit', async () => {
    // 비-공허성 — 위 둘이 "무엇이든 거부한다"가 되어 통과한 것이 아니다.
    platformApi.fetchReferenceRevision.mockResolvedValue(
      detail({ revision: summary({ state: 'CANDIDATE', etag: 'etag-candidate' }) }),
    );
    render(renderRoute());

    fireEvent.change(
      first(await screen.findAllByTestId('reference-data-cell-ref-0-correction_db')),
      { target: { value: '9.75' } },
    );

    expect(screen.queryAllByTestId('reference-data-unreadable')).toHaveLength(0);
    expect(first(screen.getAllByTestId('reference-data-save')).hasAttribute('disabled')).toBe(
      false,
    );
  });

  it('blocks publishing while an edit is unsaved', async () => {
    platformApi.fetchReferenceRevision.mockResolvedValue(
      detail({ revision: summary({ state: 'CANDIDATE', etag: 'etag-candidate' }) }),
    );
    render(renderRoute());

    const publish = first(await screen.findAllByTestId('reference-data-publish'));
    expect(publish.hasAttribute('disabled')).toBe(false);

    fireEvent.change(first(screen.getAllByTestId('reference-data-cell-ref-0-correction_db')), {
      target: { value: '9.75' },
    });

    expect(first(screen.getAllByTestId('reference-data-publish')).hasAttribute('disabled')).toBe(
      true,
    );
    expect(screen.getByTestId('reference-data-unsaved')).toBeTruthy();
  });

  it('states the provenance of the edition on screen', async () => {
    platformApi.fetchReferenceRevision.mockResolvedValue(
      detail({ revision: summary({ provenance_kind: 'FORK_EDIT' }) }),
    );
    render(renderRoute());

    const note = await screen.findByTestId('reference-data-provenance');
    expect(note.textContent).toContain(t('routes.referenceData.provenance.forkEdit'));
  });

  it('surfaces a stale-edit conflict instead of swallowing it', async () => {
    platformApi.fetchReferenceRevision.mockResolvedValue(
      detail({ revision: summary({ state: 'CANDIDATE', etag: 'etag-candidate' }) }),
    );
    platformApi.updateReferenceRevisionEntries.mockRejectedValue(
      Object.assign(new Error('conflict'), { status: 409 }),
    );
    render(renderRoute());

    fireEvent.change(
      first(await screen.findAllByTestId('reference-data-cell-ref-0-correction_db')),
      { target: { value: '9.75' } },
    );
    fireEvent.click(first(screen.getAllByTestId('reference-data-save')));

    expect(await screen.findByTestId('reference-data-write-error')).toBeTruthy();
  });
});
