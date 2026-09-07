import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { t } from '@/i18n';
import { InventoryRoute } from '@/routes/inventory';

import type { ReactElement } from 'react';

/**
 * 시료 도메인 재설계의 화면 절반 (ADR-0002, 2026-09-04).
 *
 * 여기서 지키는 명제 넷:
 *  ① 등록 폼은 사이드에 늘 펼쳐져 있지 않고 목록 옆 버튼이 연다 (운영자 요구).
 *  ② PM 축의 반입/반출이 화면에서 보이고 추가·삭제된다 (예전엔 TEXT 한 칸이었다).
 *  ③ 시험 실무자 축의 1:N 이 보인다 (예전엔 최신 1건만 보였다).
 *  ④ Accessory 는 Conducted/Radiated 칸을 갖지 않는다.
 *  ⑤ **저장 안 한 편집이 서버 재조회에서 살아남는다** (2026-09-07 추가).
 *
 * ⚠️ ⑤ 가 뒤늦게 붙은 이유가 이 파일의 교훈이다. ①~④ 는 「보이나」를 묻고 전부 초록이었는데,
 *    그 초록 아래에서 `SampleEditor` 가 `useEffect(() => setValues(initialValues(sample)),
 *    [sample])` 로 **입력 중이던 값을 조용히 날리고 있었다.** 「그려지나」를 묻는 시험은
 *    「입력한 것이 남아 있나」를 묻지 않는다 — 둘은 다른 명제다.
 */
const platformApi = vi.hoisted(() => ({
  fetchProjectsPage: vi.fn(),
  fetchSampleInventory: vi.fn(),
  fetchSample: vi.fn(),
  createSample: vi.fn(),
  patchSample: vi.fn(),
  changeSampleStatus: vi.fn(),
  softDeleteSample: vi.fn(),
  hardDeleteSample: vi.fn(),
  fetchSampleHistory: vi.fn(),
  fetchSampleIntakes: vi.fn(),
  fetchSampleCustodyEvents: vi.fn(),
  appendSampleCustodyEvent: vi.fn(),
  deleteSampleCustodyEvent: vi.fn(),
  exportSampleInventory: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const SAMPLE_ID = '22222222-2222-4222-8222-222222222222';
const EVENT_ID = '33333333-3333-4333-8333-333333333333';
const OTHER_SAMPLE_ID = '44444444-4444-4444-8444-444444444444';

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
    accessToken: makeJwt({ sub: 'pm@corp', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

function sample(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    sample_id: SAMPLE_ID,
    project_id: PROJECT_ID,
    sample_number: '#2',
    sample_kind: 'Device',
    sample_description: 'SM-F968U1_Main Conduction #2',
    test_category: 'Conduction',
    label_number: 'YIP1252M',
    serial_number: 'R3CY20KCHJM',
    assigned_team: 'RF',
    status: 'active',
    row_version: 1,
    intake_count: 3,
    custody_state: 'in_custody',
    custody_event_count: 6,
    latest_intake: null,
    ...over,
  };
}

function renderRoute(entry: string): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <InventoryRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  for (const mock of Object.values(platformApi)) mock.mockReset();
  authenticateAs(['platform:read', 'platform:sample-write']);
  platformApi.fetchProjectsPage.mockResolvedValue({ items: [], nextCursor: null });
  platformApi.fetchSampleInventory.mockResolvedValue({
    items: [sample()],
    next_cursor: null,
    as_of: null,
    filters: {},
  });
  platformApi.fetchSample.mockResolvedValue(sample());
  platformApi.fetchSampleHistory.mockResolvedValue({ items: [], next_cursor: null });
  platformApi.fetchSampleIntakes.mockResolvedValue({ items: [] });
  platformApi.fetchSampleCustodyEvents.mockResolvedValue({ items: [] });
});

describe('① 등록 폼은 목록 옆 버튼이 연다', () => {
  it('does not show the create form until the register button is pressed', async () => {
    renderRoute(`/inventory?project=${PROJECT_ID}`);
    expect(await screen.findByTestId('inventory-create-toggle')).toBeInTheDocument();
    expect(screen.queryByTestId('sample-editor-form')).toBeNull();

    await userEvent.setup().click(screen.getByTestId('inventory-create-toggle'));
    expect(await screen.findByTestId('sample-editor-form')).toBeInTheDocument();
  });

  it('closes the form again and keeps the state in the URL', async () => {
    const user = userEvent.setup();
    renderRoute(`/inventory?project=${PROJECT_ID}&create=1`);
    // 새로고침·링크 공유로 폼이 조용히 닫히면 안 되므로 URL 이 상태를 갖는다.
    expect(await screen.findByTestId('sample-editor-form')).toBeInTheDocument();
    await user.click(screen.getByTestId('inventory-create-toggle'));
    await waitFor(() => expect(screen.queryByTestId('sample-editor-form')).toBeNull());
  });

  it('offers no register button to a viewer who cannot write', async () => {
    authenticateAs(['platform:read']);
    renderRoute(`/inventory?project=${PROJECT_ID}`);
    await screen.findByTestId('inventory-sample-#2');
    expect(screen.queryByTestId('inventory-create-toggle')).toBeNull();
  });
});

describe('② PM 축 반입/반출이 1급 사건으로 보인다', () => {
  const events = [
    {
      custody_event_id: EVENT_ID,
      sample_id: SAMPLE_ID,
      project_id: PROJECT_ID,
      event_type: 'released',
      occurred_on: '2025-10-23',
      counterparty: '김용태 프로님',
      intake_cert_number: null,
      reason: 'NR n41/48 CEM 디버깅건으로 임시 반출',
      note: null,
      actor_subject: 'pm@corp',
      created_at: '2025-10-23T00:00:00Z',
      updated_at: '2025-10-23T00:00:00Z',
    },
  ];

  it('shows each event with its own date, counterparty and reason', async () => {
    platformApi.fetchSampleCustodyEvents.mockResolvedValue({ items: events });
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);

    expect(await screen.findByTestId('sample-custody-list')).toBeInTheDocument();
    expect(screen.getByTestId(`sample-custody-type-${EVENT_ID}`)).toHaveTextContent(
      t('routes.sampleInventory.custodyReleased'),
    );
    // 반출 사유가 자유 텍스트에 묻히지 않고 자기 칸을 갖는다.
    expect(screen.getByText('NR n41/48 CEM 디버깅건으로 임시 반출')).toBeInTheDocument();
    expect(screen.getByText('김용태 프로님')).toBeInTheDocument();
    expect(screen.getByText('2025-10-23')).toBeInTheDocument();
  });

  it('appends an event with only the fields the operator actually knows', async () => {
    const user = userEvent.setup();
    platformApi.appendSampleCustodyEvent.mockResolvedValue(events[0]);
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);

    await user.selectOptions(await screen.findByTestId('sample-custody-event-type'), 'received');
    await user.type(screen.getByTestId('sample-custody-occurred-on'), '2025-11-04');
    await user.type(screen.getByTestId('sample-custody-intake-cert'), '20251104-1432333773');
    await user.click(screen.getByTestId('sample-custody-add'));

    await waitFor(() => expect(platformApi.appendSampleCustodyEvent).toHaveBeenCalled());
    expect(platformApi.appendSampleCustodyEvent.mock.calls[0]?.[2]).toEqual({
      event_type: 'received',
      occurred_on: '2025-11-04',
      intake_cert_number: '20251104-1432333773',
      // 적지 않은 칸은 null 로 간다 — 빈 문자열을 값으로 저장하지 않는다.
      counterparty: null,
      reason: null,
      note: null,
    });
  });

  it('corrects a wrong entry by deleting it, not by editing it', async () => {
    const user = userEvent.setup();
    platformApi.fetchSampleCustodyEvents.mockResolvedValue({ items: events });
    platformApi.deleteSampleCustodyEvent.mockResolvedValue(undefined);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);

    await user.click(await screen.findByTestId(`sample-custody-delete-${EVENT_ID}`));
    await waitFor(() =>
      expect(platformApi.deleteSampleCustodyEvent).toHaveBeenCalledWith(
        PROJECT_ID,
        SAMPLE_ID,
        EVENT_ID,
      ),
    );
  });

  it('shows the derived custody state in the list without deciding it locally', async () => {
    renderRoute(`/inventory?project=${PROJECT_ID}`);
    // 서버가 계산한 값을 옮겨 적을 뿐이다 — 화면이 event_type 을 해석하지 않는다.
    expect(await screen.findByTestId('inventory-custody-#2')).toHaveTextContent(
      t('routes.sampleInventory.custodyStateInCustody'),
    );
  });

  it('reports a sample with no events as not recorded, never as released', async () => {
    platformApi.fetchSampleInventory.mockResolvedValue({
      items: [sample({ custody_state: null, custody_event_count: 0 })],
      next_cursor: null,
      as_of: null,
      filters: {},
    });
    renderRoute(`/inventory?project=${PROJECT_ID}`);
    expect(await screen.findByTestId('inventory-custody-#2')).toHaveTextContent(
      t('routes.sampleInventory.custodyStateUnknown'),
    );
  });
});

describe('③ 시험 실무자 축의 1:N 이 화면에 나온다', () => {
  it('lists every intake observation, not only the latest', async () => {
    platformApi.fetchSampleIntakes.mockResolvedValue({
      items: [
        {
          intake_id: 'i-1',
          sample_id: SAMPLE_ID,
          project_id: PROJECT_ID,
          intake_date: '2025-09-30',
          bl: 'BL-1',
          tech_group: 'RF',
        },
        {
          intake_id: 'i-2',
          sample_id: SAMPLE_ID,
          project_id: PROJECT_ID,
          intake_date: '2025-10-21',
          bl: 'BL-2',
          tech_group: 'RF',
        },
      ],
    });
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);

    expect(await screen.findByTestId('sample-intake-history-table')).toBeInTheDocument();
    expect(screen.getByTestId('sample-intake-row-i-1')).toHaveTextContent('BL-1');
    expect(screen.getByTestId('sample-intake-row-i-2')).toHaveTextContent('BL-2');
  });
});

describe('④ 분류가 폼에 있다', () => {
  it('hides the Conducted/Radiated field for an Accessory', async () => {
    const user = userEvent.setup();
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);
    expect(await screen.findByTestId('sample-editor-test_category')).toBeInTheDocument();

    await user.selectOptions(screen.getByTestId('sample-editor-sample_kind'), 'Accessory');
    await waitFor(() => expect(screen.queryByTestId('sample-editor-test_category')).toBeNull());
  });

  it('clears a stale test category when the kind becomes Accessory', async () => {
    const user = userEvent.setup();
    platformApi.patchSample.mockResolvedValue(sample());
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);

    await user.selectOptions(await screen.findByTestId('sample-editor-sample_kind'), 'Accessory');
    await user.click(screen.getByTestId('sample-editor-save'));
    await waitFor(() => expect(platformApi.patchSample).toHaveBeenCalled());
    expect(platformApi.patchSample.mock.calls[0]?.[2]).toMatchObject({
      sample_kind: 'Accessory',
      test_category: null,
    });
  });

  it('keeps a legacy value that is outside the dropdown vocabulary', async () => {
    // DB CHECK 가 없으므로 기존 행은 목록 밖의 값을 가질 수 있다. 드롭다운이 그것을
    // 조용히 다른 값으로 바꾸면 편집하려던 사람이 데이터를 잃는다.
    platformApi.fetchSample.mockResolvedValue(sample({ test_category: 'Main Conduction' }));
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);
    const select = await screen.findByTestId('sample-editor-test_category');
    expect((select as HTMLSelectElement).value).toBe('Main Conduction');
  });

  it('no longer asks the operator for the unused sample_code', async () => {
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);
    await screen.findByTestId('sample-editor-sample_number');
    expect(screen.queryByTestId('sample-editor-sample_code')).toBeNull();
  });

  it('keeps the legacy spreadsheet text visible and editable', async () => {
    platformApi.fetchSample.mockResolvedValue(
      sample({
        note: '11/4일 재 반입\n10/28일 재 반입',
        intake_cert: '20251104-1432333773\n20251027-1724065293',
      }),
    );
    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);
    // 결정 9 — 자동 변환하지 않는다. 원문이 화면에서 사라지면 옮길 수도 없다.
    expect(await screen.findByTestId('sample-editor-note')).toHaveValue(
      '11/4일 재 반입\n10/28일 재 반입',
    );
    expect(screen.getByTestId('sample-editor-intake_cert')).toHaveValue(
      '20251104-1432333773\n20251027-1724065293',
    );
  });
});

describe('⑤ 저장 안 한 편집이 서버 재조회에서 살아남는다', () => {
  /**
   * 이 결함의 도달 경로 — 실측(2026-09-07):
   *
   *   PM 이 시료 칸을 입력 중 (아직 저장 안 함)
   *     → 같은 화면 아래 custody 패널에서 반입/반출 사건 추가
   *     → SampleCustodyPanel 이 invalidateQueries(['sample-inventory'])
   *     → detail 키 ['sample-inventory','detail',…] 가 그 접두사에 걸려 재조회
   *     → custody_event_count · custody_state 가 «반드시» 바뀌므로 sample 신원이 갈림
   *     → 옛 useEffect 발화 → 입력 중이던 값 전부 폐기. 경고 없음.
   *
   * `SampleEditor` 는 이제 표시값을 `서버 위에 override` 로 파생하므로 동기화할 것이
   * 없고, 따라서 덮어쓸 것도 없다.
   */
  it('keeps a typed-but-unsaved field when a custody event refetches the sample', async () => {
    const user = userEvent.setup();
    // 사건 추가가 시료 payload 를 «실제로» 바꾸는 것을 재현한다 — 안 바꾸면
    // react-query 의 structural sharing 이 신원을 유지해 옛 코드도 통과한다.
    let custodyEventCount = 6;
    platformApi.fetchSample.mockImplementation(async () =>
      sample({ custody_event_count: custodyEventCount }),
    );
    platformApi.appendSampleCustodyEvent.mockImplementation(async () => {
      custodyEventCount = 7;
      return { custody_event_id: EVENT_ID };
    });

    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);

    const label = await screen.findByTestId('sample-editor-label_number');
    await user.clear(label);
    await user.type(label, 'YIP-EDITED-999');
    expect(label).toHaveValue('YIP-EDITED-999');

    // 아래 패널에서 사건 하나를 추가한다 — 저장 버튼은 «누르지 않는다».
    await user.click(await screen.findByTestId('sample-custody-add'));
    await waitFor(() => expect(platformApi.appendSampleCustodyEvent).toHaveBeenCalled());
    // 재조회가 실제로 일어났는지부터 확인한다 — 안 일어났으면 이 시험은 공허하다.
    await waitFor(() => expect(platformApi.fetchSample.mock.calls.length).toBeGreaterThan(1));

    expect(await screen.findByTestId('sample-editor-label_number')).toHaveValue('YIP-EDITED-999');
  });

  /**
   * ⚠️ **이 팔은 회귀 시험이 아니다.** 옛 코드(매 `sample` 변경마다 초기화)에서도
   * 통과한다 — 실측했다. 이것이 막는 것은 «수정이 새로 만든» 위험이다: override 를
   * 시료 id 로 키 달지 않으면 앞 시료의 편집이 다음 시료 위로 샌다. 위 팔이 옛 코드에서
   * 빨갛고 이 팔은 초록인 것이 정상이고, 둘의 «역할이 다르다»는 것을 적어 둔다.
   */
  it('does not carry one sample\'s unsaved edit onto another sample', async () => {
    const user = userEvent.setup();
    platformApi.fetchSampleInventory.mockResolvedValue({
      items: [sample(), sample({ sample_id: OTHER_SAMPLE_ID, sample_number: '#9' })],
      next_cursor: null,
      as_of: null,
      filters: {},
    });
    platformApi.fetchSample.mockImplementation(async (_project: string, sampleId: string) =>
      sampleId === OTHER_SAMPLE_ID
        ? sample({ sample_id: OTHER_SAMPLE_ID, sample_number: '#9', label_number: 'ORIGINAL-9' })
        : sample(),
    );

    renderRoute(`/inventory?project=${PROJECT_ID}&sample=${SAMPLE_ID}`);
    const label = await screen.findByTestId('sample-editor-label_number');
    await user.clear(label);
    await user.type(label, 'BELONGS-TO-2');

    // 다른 시료로 옮긴다. override 는 시료 id 로 키가 달려 있어야 한다.
    await user.click(await screen.findByTestId('inventory-sample-#9'));

    await waitFor(() =>
      expect(screen.getByTestId('sample-editor-label_number')).toHaveValue('ORIGINAL-9'),
    );
  });
});
