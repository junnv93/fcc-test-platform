import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import koMessages from '@/locales/ko.json';
import { queryKeys } from '@/api/query-config';
import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import {
  absentReportNumberReason,
  buildCreateReportRequest,
  citationFieldState,
  TestReportsRoute,
} from '@/routes/test-reports';

import { tableView } from './helpers/responsive-table';

import type { BoundFunctions, queries } from '@testing-library/dom';
import type { ReactElement } from 'react';

/**
 * 성적서 대장 화면 봉인 (W3-A, 2026-07-29) — 계약 §4 S1·S3~S8·S11.
 *
 * 여기가 **런타임 동작**의 봉인이다. pytest 쪽 텍스트/AST 스캔은 "금지 패턴 부재"
 * 축만 담당한다(계약 §4 서두: 봉인이 통과했다 ≠ 봉인이 무언가를 검사했다).
 *
 * 각 케이스는 화면이 **거짓말하지 않는지**를 본다:
 *  - S1 서버가 준 성적서 번호를 그대로 보이는가(클라이언트 재조립 아님)
 *  - S3 비운 칸이 요청 본문에서 키째 사라지는가(빈 문자열 전송 0)
 *  - S4 생성 성공이 목록을 실제로 다시 읽게 하는가
 *  - S5 edition 중복(409)이 일반 실패와 다른 문구인가
 *  - S6 토큰 권한 미보유만으로 버튼이 잠기지 않는가
 *  - S7 null 과 빈 문자열과 값이 서로 구분되는가
 *  - S8 성적서 번호 공란에 이유가 붙는가
 *  - S11 쿼리 키가 queryKeys 팩토리 경유인가
 */

const platformApi = vi.hoisted(() => ({
  fetchProjectsPage: vi.fn(),
  fetchProjectReports: vi.fn(),
  createProjectReport: vi.fn(),
  fetchReportCitation: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '22222222-2222-4222-8222-222222222222';
const ENTRY = `/test-reports?project=${PROJECT_ID}`;

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/u, '');
  const body = btoa(JSON.stringify(payload))
    .replace(/=+$/u, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return `${header}.${body}.sig`;
}

/** 로그인 상태를 만든다. 기본은 **권한 토큰 0개** — S6 의 조건이다. */
function authenticateAs(permissions: readonly string[] = []): void {
  applyTokenSet({
    accessToken: makeJwt({ sub: 'tester@corp', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

function report(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    report_id: 'r-1',
    project_id: PROJECT_ID,
    edition: '1',
    report_number: 'S-KTL-2026-0001-1',
    date_tested_start: '2026-07-01',
    date_tested_end: '2026-07-10',
    date_of_issue: '2026-07-20',
    prepared_by: '홍길동',
    prepared_site: '수원',
    created_at: '2026-07-20T09:00:00Z',
    ...over,
  };
}

function citation(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    project_id: PROJECT_ID,
    report_number: 'S-KTL-2026-0001-1',
    management_number: 'KTL-2026-0001',
    fcc_id: 'A3LSM-X100',
    applicant_name: 'Samsung Electronics',
    applicant_address: null,
    eut_description: '',
    test_standard: 'FCC Part 15.247',
    samples: [],
    ...over,
  };
}

function renderRoute(entry = ENTRY): QueryClient {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <TestReportsRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
  return queryClient;
}

/** DataTable 은 같은 행을 표/카드 두 표면으로 렌더한다 — 표 쪽으로 좁힌다.
 *  범위 축소는 기존 공용 헬퍼(`tests/helpers/responsive-table`)의 계약을 그대로 쓴다. */
function reportTable(): BoundFunctions<typeof queries> {
  return tableView('test-reports-table');
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  platformApi.fetchProjectsPage.mockReset();
  platformApi.fetchProjectReports.mockReset();
  platformApi.createProjectReport.mockReset();
  platformApi.fetchReportCitation.mockReset();
  platformApi.fetchProjectsPage.mockResolvedValue({ items: [], nextCursor: null });
  platformApi.fetchProjectReports.mockResolvedValue([]);
  platformApi.fetchReportCitation.mockResolvedValue(citation());
  authenticateAs();
});

// ── 순수 함수 ────────────────────────────────────────────────────────────────

describe('citationFieldState / absentReportNumberReason (순수 판정)', () => {
  it('값 / null / 빈 문자열을 서로 다른 상태로 분류한다', () => {
    expect(citationFieldState('A3LSM')).toBe('value');
    expect(citationFieldState(null)).toBe('absent');
    expect(citationFieldState(undefined)).toBe('absent');
    expect(citationFieldState('')).toBe('blank');
  });

  it('성적서 번호 공란의 원인을 edition 유무로 역산한다', () => {
    expect(absentReportNumberReason('')).toBe('edition');
    expect(absentReportNumberReason(null)).toBe('edition');
    expect(absentReportNumberReason('2')).toBe('managementNumber');
  });
});

// ── S3 — 빈 칸은 키째 생략 ───────────────────────────────────────────────────

describe('S3 buildCreateReportRequest', () => {
  it('비운 선택 칸의 키 자체를 만들지 않는다 (빈 문자열 전송 0)', () => {
    const body = buildCreateReportRequest({
      edition: ' 1 ',
      dateTestedStart: '',
      dateTestedEnd: '   ',
      dateOfIssue: '',
      preparedBy: '',
      preparedSite: '',
    });
    expect(body).toEqual({ edition: '1' });
    expect(Object.keys(body)).toEqual(['edition']);
  });

  it('채운 칸만 키로 실린다', () => {
    const body = buildCreateReportRequest({
      edition: '2',
      dateTestedStart: '2026-07-01',
      dateTestedEnd: '',
      dateOfIssue: '',
      preparedBy: '홍길동',
      preparedSite: '',
    });
    expect(body).toEqual({
      edition: '2',
      date_tested_start: '2026-07-01',
      prepared_by: '홍길동',
    });
  });
});

// ── S1 · S8 · S11 — 목록 ────────────────────────────────────────────────────

/** 성적서 번호가 비는 **두 사유**의 문구 SSOT.
 *
 * ⚠️ 문구를 시험에 박지 않는다. 실측 2026-09-05: 이 자리에 `'관리번호'` 가 박혀
 * 있었고, 접수 개편이 그 용어를 「프로젝트 번호」로 통일하자 **표현만 바뀌었는데**
 * 시험 2건이 깨졌다. 시험이 재려던 것은 표현이 아니라 *어느 사유를 지목하는가* 다.
 */
const REPORT_NUMBER_ABSENT = koMessages.routes.testReports.reportNumberAbsent;

describe('S1 성적서 목록', () => {
  it('서버가 준 report_number 를 그대로 렌더한다', async () => {
    platformApi.fetchProjectReports.mockResolvedValue([report()]);
    renderRoute();
    await waitFor(() => expect(screen.getByTestId('test-reports-table')).toBeInTheDocument());
    expect(reportTable().getByTestId('test-report-number')).toHaveTextContent('S-KTL-2026-0001-1');
    // 서버 응답을 그대로 읽었다는 증거: 프론트가 조합했다면 관리번호를 알아야 하는데
    // 목록 응답에는 관리번호가 없다.
    expect(platformApi.fetchProjectReports).toHaveBeenCalledWith(PROJECT_ID);
  });

  it('S11 목록을 queryKeys 팩토리 키로 캐시한다', async () => {
    platformApi.fetchProjectReports.mockResolvedValue([report()]);
    const client = renderRoute();
    await waitFor(() =>
      expect(client.getQueryData(queryKeys.project.reports(PROJECT_ID))).toHaveLength(1),
    );
  });

  it('S8 report_number 가 비면 이유를 함께 보인다 (무근거 공란 금지)', async () => {
    platformApi.fetchProjectReports.mockResolvedValue([
      report({ report_number: null, edition: '3' }),
    ]);
    renderRoute();
    await waitFor(() => expect(screen.getByTestId('test-reports-table')).toBeInTheDocument());
    const cell = reportTable().getByTestId('test-report-number-absent');
    // edition 은 있으므로 원인은 **프로젝트 번호 부재**다 — 두 사유 중 어느 쪽을
    // 지목하는지가 이 시험이 재는 것이므로, 나머지 사유가 아님도 함께 못박는다.
    expect(cell).toHaveTextContent(REPORT_NUMBER_ABSENT.managementNumber);
    expect(cell).not.toHaveTextContent(REPORT_NUMBER_ABSENT.edition);
  });

  it('로딩 · 빈 목록 · 에러가 서로 다른 표면으로 나타난다', async () => {
    platformApi.fetchProjectReports.mockResolvedValue([]);
    renderRoute();
    expect(screen.getByTestId('data-table-skeleton')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('test-reports-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('test-reports-error')).toBeNull();
    expect(screen.queryByTestId('data-table-skeleton')).toBeNull();
  });

  it('에러는 빈 목록으로 위장되지 않는다', async () => {
    platformApi.fetchProjectReports.mockRejectedValue(
      Object.assign(new Error('boom'), { status: 503 }),
    );
    renderRoute();
    await waitFor(() => expect(screen.getByTestId('test-reports-error')).toBeInTheDocument());
    expect(screen.queryByTestId('test-reports-empty')).toBeNull();
  });

  it('프로젝트 미선택은 조회하지 않고 안내한다', async () => {
    renderRoute('/test-reports');
    await waitFor(() => expect(screen.getByTestId('test-reports-no-project')).toBeInTheDocument());
    expect(platformApi.fetchProjectReports).not.toHaveBeenCalled();
  });
});

// ── S3 · S4 · S5 · S6 — 생성 ────────────────────────────────────────────────

describe('성적서 생성', () => {
  it('S6 토큰 권한 미보유만을 이유로 버튼을 잠그지 않는다', async () => {
    renderRoute();
    const user = userEvent.setup();
    const submit = await screen.findByTestId('new-report-submit');
    // 권한 토큰 0개로 로그인한 상태. edition 만 채우면 제출 가능해야 한다 —
    // 백엔드는 토큰 ∪ 멤버십 UNION 으로 판정하므로 토큰만 보고 막으면 멤버십으로
    // 권한을 받은 사용자를 부당하게 차단하는 false negative 가 된다.
    await user.type(screen.getByTestId('new-report-edition'), '1');
    expect(submit).toBeEnabled();
    // 대신 비차단 안내가 뜬다 — 막지는 않되 알려는 준다(프로젝트 멤버십 선례).
    expect(screen.getByTestId('new-report-permission-hint')).toBeInTheDocument();
  });

  it('S6 권한 토큰을 가진 경우 안내는 사라지고 버튼은 그대로 열려 있다', async () => {
    authenticateAs(['platform:admin']);
    renderRoute();
    const user = userEvent.setup();
    await user.type(await screen.findByTestId('new-report-edition'), '1');
    expect(screen.getByTestId('new-report-submit')).toBeEnabled();
    expect(screen.queryByTestId('new-report-permission-hint')).toBeNull();
  });

  it('S3 비운 칸을 요청 본문에서 생략한다', async () => {
    platformApi.createProjectReport.mockResolvedValue(report({ edition: '5' }));
    renderRoute();
    const user = userEvent.setup();
    await user.type(await screen.findByTestId('new-report-edition'), '5');
    await user.click(screen.getByTestId('new-report-submit'));
    await waitFor(() => expect(platformApi.createProjectReport).toHaveBeenCalled());
    const [, body] = platformApi.createProjectReport.mock.calls[0] as [string, object];
    expect(body).toEqual({ edition: '5' });
    for (const key of Object.keys(body)) {
      expect((body as Record<string, unknown>)[key]).not.toBe('');
    }
  });

  it('S4 생성 성공이 목록을 다시 읽게 한다 (수동 새로고침 불필요)', async () => {
    platformApi.fetchProjectReports.mockResolvedValue([]);
    platformApi.createProjectReport.mockResolvedValue(report({ edition: '5' }));
    renderRoute();
    const user = userEvent.setup();
    await waitFor(() => expect(platformApi.fetchProjectReports).toHaveBeenCalledTimes(1));
    await user.type(await screen.findByTestId('new-report-edition'), '5');
    await user.click(screen.getByTestId('new-report-submit'));
    await waitFor(() => expect(platformApi.fetchProjectReports).toHaveBeenCalledTimes(2));
    expect(await screen.findByTestId('new-report-success')).toBeInTheDocument();
  });

  it('S5 edition 중복(409)이 일반 실패와 다른 문구로 나온다', async () => {
    platformApi.createProjectReport.mockRejectedValue(
      Object.assign(new Error('conflict'), { status: 409 }),
    );
    renderRoute();
    const user = userEvent.setup();
    await user.type(await screen.findByTestId('new-report-edition'), '1');
    await user.click(screen.getByTestId('new-report-submit'));
    const conflict = await screen.findByTestId('new-report-error');
    const conflictText = conflict.textContent ?? '';
    expect(conflictText).toContain('이미');

    // 같은 표면이 500 에서는 다른 문구를 낸다 — 409 특화가 실제로 갈라지는지의 증거.
    platformApi.createProjectReport.mockRejectedValue(
      Object.assign(new Error('boom'), { status: 500 }),
    );
    await user.click(screen.getByTestId('new-report-submit'));
    await waitFor(() =>
      expect(screen.getByTestId('new-report-error').textContent).not.toBe(conflictText),
    );
  });
});

// ── S7 · S8 — 인용 패널 ─────────────────────────────────────────────────────

describe('S7 자동 인용', () => {
  it('값 / 미기재(null) / 빈 값("")을 서로 다르게 표시한다', async () => {
    platformApi.fetchReportCitation.mockResolvedValue(
      citation({ applicant_address: null, eut_description: '' }),
    );
    renderRoute();
    await waitFor(() => expect(screen.getByTestId('citation-body')).toBeInTheDocument());

    expect(screen.getByTestId('citation-fccId')).toHaveTextContent('A3LSM-X100');
    expect(screen.getByTestId('citation-fccId')).toHaveAttribute('data-state', 'value');

    const absent = screen.getByTestId('citation-applicantAddress');
    expect(absent).toHaveAttribute('data-state', 'absent');
    expect(absent).toHaveTextContent('미기재');

    const blank = screen.getByTestId('citation-eutDescription');
    expect(blank).toHaveAttribute('data-state', 'blank');
    // 빈 문자열은 "미기재"와 섞이지 않는다.
    expect(blank.textContent).not.toBe(absent.textContent);
    expect(blank.textContent ?? '').not.toBe('');
  });

  it('S8 edition 미선택으로 report_number 가 비면 그 이유를 밝힌다', async () => {
    platformApi.fetchReportCitation.mockResolvedValue(citation({ report_number: null }));
    renderRoute();
    const absent = await screen.findByTestId('citation-report-number-absent');
    expect(absent).toHaveTextContent('edition');
    expect(screen.queryByTestId('citation-report-number')).toBeNull();
  });

  it('S8 edition 을 골랐는데도 비면 관리번호 부재를 지목한다', async () => {
    platformApi.fetchReportCitation.mockResolvedValue(
      citation({ report_number: null, management_number: null }),
    );
    renderRoute(`${ENTRY}&edition=2`);
    const absent = await screen.findByTestId('citation-report-number-absent');
    expect(absent).toHaveTextContent(REPORT_NUMBER_ABSENT.managementNumber);
    expect(absent).not.toHaveTextContent(REPORT_NUMBER_ABSENT.edition);
  });

  it('선택한 edition 을 인용 조회에 싣는다 (report_number 도달 경로)', async () => {
    platformApi.fetchProjectReports.mockResolvedValue([report({ edition: '7' })]);
    renderRoute();
    await waitFor(() =>
      expect(platformApi.fetchReportCitation).toHaveBeenCalledWith(PROJECT_ID, ''),
    );
    const user = userEvent.setup();
    await user.click(reportTable().getByTestId('test-report-cite'));
    await waitFor(() =>
      expect(platformApi.fetchReportCitation).toHaveBeenCalledWith(PROJECT_ID, '7'),
    );
  });

  it('시료 배열이 비어도 에러가 아니라 정상 상태로 표시한다', async () => {
    platformApi.fetchReportCitation.mockResolvedValue(citation({ samples: [] }));
    renderRoute();
    expect(await screen.findByTestId('citation-samples-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('citation-error')).toBeNull();
  });

  it('시료의 SN 과 최신 펌웨어를 함께 보인다', async () => {
    platformApi.fetchReportCitation.mockResolvedValue(
      citation({
        samples: [
          {
            sample_number: '#1',
            serial_number: 'SN-0001',
            latest_firmware: {
              bl: 'BL1',
              ap: 'AP1',
              cp: null,
              csc: null,
              rf_cal: null,
              hw_rev: null,
            },
          },
        ],
      }),
    );
    renderRoute();
    const sample = await screen.findByTestId('citation-sample');
    expect(within(sample).getByTestId('citation-sample-number-0')).toHaveTextContent('#1');
    expect(within(sample).getByTestId('citation-sample-serial-0')).toHaveTextContent('SN-0001');
    expect(sample).toHaveTextContent('BL1');
    expect(sample).toHaveTextContent('AP1');
    // 기록이 없는 펌웨어 칸은 공란이 아니라 "미기재"로 말한다.
    expect(sample).toHaveTextContent('미기재');
  });
});
