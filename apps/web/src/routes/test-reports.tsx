import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { PERMISSION_PLATFORM_ADMIN } from '@/api/permissions';
import {
  createProjectReport,
  type CreateReportRequest,
  fetchProjectReports,
  fetchReportCitation,
  type ReportCitationEnvelope,
  type ReportEnvelope,
  type ReportSampleCitation,
} from '@/api/platform-client';
import { queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { useAuthSession } from '@/auth/route-guard';
import { useT } from '@/i18n';
import { isValidProjectId } from '@/shared/project-id';
import { ProjectSelectField } from '@/shared/ProjectSelectField';
import { ROUTE_PATHS } from '@/shared/route-links';
import {
  BlockSkeleton,
  Button,
  Card,
  DataTable,
  type DataTableColumn,
  DataTableSkeleton,
  describeApiError,
  EmptyState,
  ErrorState,
  FieldGroup,
  PageHeader,
  SectionBand,
  StatusMessage,
  Toolbar,
  WorkbenchLayout,
} from '@/ui';

import type { ApiError } from '@/shared/api-error';
import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query';

/**
 * 성적서 대장 — 중앙 `test_reports` 인스턴스 조회·생성 + 자동 인용 (Phase G 배선,
 * 2026-07-29).
 *
 * FCC 시험의 최종 산출물은 성적서다. 백엔드 Phase G 가 성적서 인스턴스(프로젝트
 * 1:N, edition 단위)와 자동 인용 조립을 이미 배포했는데 화면이 없어 시험원이
 * SN·펌웨어·관리번호·FCC ID 를 수기로 옮겨 적고 있었다. 이 화면이 그 능력을 잇는다.
 *
 * **`/reports` 와 다른 도메인이다.** `/reports` 는 headless 의 성적서 **파일 생성
 * 요청 큐**(`report_automation:*`, `/headless/reports/*`)이고, 여기는 중앙
 * platform 의 **성적서 인스턴스 대장**(`platform:*`, `test_reports`)이다. 파일·
 * 라우트·쿼리 키·권한이 전부 분리돼 있으며, 두 화면 사이 이동 링크를 둬서 시험원이
 * 어느 쪽에 있는지 알 수 있게 한다.
 *
 * ## 이 화면이 지키는 정직성 규칙
 *
 * 1. **`report_number` 는 서버 파생값** — `S-{관리번호}-{edition}` 규칙은 백엔드
 *    도메인 SSOT(`report_number_policy.py`)이고 DB 에 저장조차 하지 않는다. 여기서
 *    조합하면 규칙이 두 언어로 쪼개진다. **응답 필드를 읽기만 한다.**
 * 2. **null 은 만들어내지 않는다** — 인용 필드는 대부분 nullable 이다. "값이 없다"
 *    (null) 와 "빈 문자열이 저장돼 있다"('') 를 구분해 표시하고, `'-'` 로 뭉개지
 *    않는다. `report_number` 가 비면 **왜 비었는지**(관리번호 부재 / edition 미선택)
 *    를 함께 보인다.
 * 3. **빈 문자열을 서버에 보내지 않는다** — 생성 폼에서 비운 선택 칸은 요청 본문에서
 *    **키 자체를 생략**한다. 빈 문자열과 미기재는 다른 값이다.
 * 4. **토큰으로 잠그지 않는다** — 백엔드 `authorize` 는 토큰 ∪ 프로젝트-멤버십
 *    UNION 이다(read/create 양쪽). 브라우저는 토큰만 보므로 토큰 미보유를 근거로
 *    막으면 멤버십으로 권한을 받은 사용자가 부당하게 차단된다. 안내만 하고 백엔드를
 *    최종 권위로 둔다(403 은 에러로 표면화).
 *
 * 목록은 페이지네이션이 없다 — 백엔드가 keyset 파라미터를 노출하지 않고
 * (edition 단위의 작은 집합) 없는 페이지네이션을 흉내 내면 거짓말이 된다.
 * 수정/삭제도 없다 — 백엔드에 해당 operation 이 없으므로 버튼을 두면 없는 능력을
 * 약속하는 것이다.
 */

/** 선택된 프로젝트를 싣는 URL 파라미터 (저장소 관례). */
const PROJECT_PARAM = 'project';

/**
 * 인용 정제 축. `GET .../report-citation` 에는 `report_id` 가 없다 — 프로젝트 스코프
 * 조회이고, `edition` 은 파생 `report_number` 만 좌우하는 **선택적** 정제다. URL 에
 * 실어 두면 인용 화면을 그대로 공유할 수 있다.
 */
const EDITION_PARAM = 'edition';

/**
 * 인용 필드 한 칸의 상태. 세 가지는 서로 다른 사실이므로 화면에서도 달라야 한다:
 * 서버가 값을 준 경우 / 값이 없는 경우(null) / 빈 문자열이 저장돼 있는 경우.
 */
export type CitationFieldState = 'value' | 'absent' | 'blank';

/** 순수 분류기 — 표시 계층이 세 상태를 뭉개지 못하게 하는 단일 판정점. */
export function citationFieldState(value: string | null | undefined): CitationFieldState {
  if (value === null || value === undefined) return 'absent';
  if (value === '') return 'blank';
  return 'value';
}

/**
 * `report_number` 가 비었을 때의 **원인** 토큰.
 *
 * 백엔드 정책상 파생값은 관리번호와 edition 이 **둘 다** 있어야 만들어진다. 따라서
 * edition 이 비어 있으면 원인은 edition, edition 이 있는데도 비었으면 원인은
 * 관리번호다 — 원인을 이렇게 **역산**할 수 있으므로 무근거 공란을 남길 이유가 없다.
 * (번호 자체를 조합하지는 않는다. 여기서 나오는 것은 원인 토큰뿐이다.)
 */
export type AbsentReportNumberReason = 'edition' | 'managementNumber';

export function absentReportNumberReason(
  edition: string | null | undefined,
): AbsentReportNumberReason {
  return citationFieldState(edition) === 'value' ? 'managementNumber' : 'edition';
}

/** 생성 폼의 사용자 입력(모두 문자열). 서버 전송 직전에 정규화한다. */
interface ReportDraft {
  readonly edition: string;
  readonly dateTestedStart: string;
  readonly dateTestedEnd: string;
  readonly dateOfIssue: string;
  readonly preparedBy: string;
  readonly preparedSite: string;
}

const EMPTY_DRAFT: ReportDraft = {
  edition: '',
  dateTestedStart: '',
  dateTestedEnd: '',
  dateOfIssue: '',
  preparedBy: '',
  preparedSite: '',
};

/**
 * 폼 초안 → 요청 본문. **비운 칸은 키를 생략한다** — `''` 를 보내면 서버에 빈
 * 문자열이 저장돼 "미기재"와 구분되지 않는다(`createProject` 의 `management_number`
 * 처리와 같은 규칙). 순수 함수로 분리해 봉인이 본문을 직접 검사할 수 있게 한다.
 */
export function buildCreateReportRequest(draft: ReportDraft): CreateReportRequest {
  const body: CreateReportRequest = { edition: draft.edition.trim() };
  const optional: readonly (readonly [keyof CreateReportRequest, string])[] = [
    ['date_tested_start', draft.dateTestedStart],
    ['date_tested_end', draft.dateTestedEnd],
    ['date_of_issue', draft.dateOfIssue],
    ['prepared_by', draft.preparedBy],
    ['prepared_site', draft.preparedSite],
  ];
  for (const [key, raw] of optional) {
    const trimmed = raw.trim();
    if (trimmed !== '') {
      // 키별 대입 — 넓은 인덱스 시그니처를 만들지 않고도 선택 필드를 채운다.
      Object.assign(body, { [key]: trimmed });
    }
  }
  return body;
}

function TestReportsRoute(): JSX.Element {
  const { t } = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const projectId = (searchParams.get(PROJECT_PARAM) ?? '').trim();
  const selectedEdition = (searchParams.get(EDITION_PARAM) ?? '').trim();
  const hasProject = isValidProjectId(projectId);

  return (
    <section className="test-reports" aria-labelledby="test-reports-heading">
      <PageHeader
        title={t('routes.testReports.title')}
        titleId="test-reports-heading"
        description={t('routes.testReports.description')}
      />

      <p className="section-hint" data-testid="test-reports-domain-note">
        {t('routes.testReports.domainNote')}{' '}
        <Link to={ROUTE_PATHS.reports} data-testid="test-reports-generation-link">
          {t('routes.testReports.generationLink')}
        </Link>
      </p>

      <WorkbenchLayout
        className="test-reports-workbench"
        mainLabel={t('routes.testReports.title')}
        testId="test-reports-workbench"
        main={
          <>
            <Card
              as="section"
              variant="summary"
              aria-labelledby="test-reports-project-heading"
              testId="test-reports-project-card"
            >
              <SectionBand
                title={t('routes.testReports.projectSection')}
                titleId="test-reports-project-heading"
              />
              <Toolbar ariaLabel={t('routes.testReports.projectSection')}>
                <ProjectSelectField
                  value={projectId}
                  onChange={(value) => {
                    // 프로젝트가 바뀌면 edition 정제는 의미를 잃는다(다른 프로젝트의
                    // edition 이므로) → 함께 비운다.
                    if (value === '') setSearchParams({});
                    else setSearchParams({ [PROJECT_PARAM]: value });
                  }}
                  selectId="test-reports-project-select"
                  selectTestId="test-reports-project-select"
                  statusTestId="test-reports-project-status"
                />
              </Toolbar>
            </Card>

            {!hasProject ? (
              <EmptyState
                testId="test-reports-no-project"
                title={t('routes.testReports.noProjectTitle')}
                description={t('routes.testReports.noProjectDescription')}
                action={
                  <Link to={ROUTE_PATHS.myProjects} data-testid="test-reports-pick-project">
                    {t('routes.testReports.pickProject')}
                  </Link>
                }
              />
            ) : (
              <ProjectReportRegistry
                projectId={projectId}
                selectedEdition={selectedEdition}
                onSelectEdition={(edition) => {
                  const next: Record<string, string> = { [PROJECT_PARAM]: projectId };
                  if (edition !== '') next[EDITION_PARAM] = edition;
                  setSearchParams(next);
                }}
              />
            )}
          </>
        }
      />
    </section>
  );
}

function ProjectReportRegistry({
  projectId,
  selectedEdition,
  onSelectEdition,
}: {
  readonly projectId: string;
  readonly selectedEdition: string;
  readonly onSelectEdition: (edition: string) => void;
}): JSX.Element {
  const queryClient = useQueryClient();

  const reports = useQuery<ReportEnvelope[], ApiError>({
    queryKey: queryKeys.project.reports(projectId),
    queryFn: () => fetchProjectReports(projectId),
    ...REFETCH_STRATEGIES.NORMAL,
  });

  const createMutation = useMutation<ReportEnvelope, ApiError, ReportDraft>({
    mutationFn: (draft: ReportDraft) =>
      createProjectReport(projectId, buildCreateReportRequest(draft)),
    onSuccess: () => {
      // 읽기와 같은 팩토리로 무효화 → 키가 드리프트할 수 없다. 인용도 함께 무효화:
      // 새 edition 이 생기면 그 edition 의 파생 report_number 가 조회 가능해진다.
      void queryClient.invalidateQueries({ queryKey: queryKeys.project.reports(projectId) });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.project.reportCitation(projectId, selectedEdition),
      });
    },
  });

  return (
    <>
      <ReportListPanel
        reports={reports}
        selectedEdition={selectedEdition}
        onSelectEdition={onSelectEdition}
      />
      <CreateReportPanel mutation={createMutation} />
      <CitationPanel
        projectId={projectId}
        selectedEdition={selectedEdition}
        editions={(reports.data ?? []).map((report) => report.edition ?? '')}
        onSelectEdition={onSelectEdition}
      />
    </>
  );
}

/** 성적서 목록 — 서버가 준 값을 그대로 렌더한다(파생 0). */
function ReportListPanel({
  reports,
  selectedEdition,
  onSelectEdition,
}: {
  readonly reports: UseQueryResult<ReportEnvelope[], ApiError>;
  readonly selectedEdition: string;
  readonly onSelectEdition: (edition: string) => void;
}): JSX.Element {
  const { t } = useT();

  const columns = useMemo<readonly DataTableColumn<ReportEnvelope>[]>(
    () => [
      {
        key: 'edition',
        header: t('routes.testReports.column.edition'),
        priority: 'primary',
        rowHeader: true,
        // testid 는 'row' 표면에만 찍는다 — 카드/overflow 는 같은 값의 또 다른
        // 렌더이므로 양쪽에 찍으면 `getByTestId` 가 모호해진다(DataTable 계약).
        cell: (row, surface) => (
          <FieldValue
            value={row.edition}
            {...(surface === 'row' ? { testId: 'test-report-edition' } : {})}
          />
        ),
      },
      {
        key: 'reportNumber',
        header: t('routes.testReports.column.reportNumber'),
        priority: 'primary',
        // 서버 파생값. 클라이언트에서 조립하지 않고 응답 필드를 그대로 읽는다.
        cell: (row, surface) =>
          citationFieldState(row.report_number) === 'value' ? (
            <span {...(surface === 'row' ? { 'data-testid': 'test-report-number' } : {})}>
              {row.report_number}
            </span>
          ) : (
            <span
              className="citation-value citation-value--absent"
              {...(surface === 'row' ? { 'data-testid': 'test-report-number-absent' } : {})}
            >
              {t(
                absentReportNumberReason(row.edition) === 'managementNumber'
                  ? 'routes.testReports.reportNumberAbsent.managementNumber'
                  : 'routes.testReports.reportNumberAbsent.edition',
              )}
            </span>
          ),
      },
      {
        key: 'dateTested',
        header: t('routes.testReports.column.dateTested'),
        priority: 'secondary',
        cell: (row) => (
          <span>
            <FieldValue value={row.date_tested_start} />
            {' ~ '}
            <FieldValue value={row.date_tested_end} />
          </span>
        ),
      },
      {
        key: 'dateOfIssue',
        header: t('routes.testReports.column.dateOfIssue'),
        priority: 'secondary',
        cell: (row) => <FieldValue value={row.date_of_issue} />,
      },
      {
        key: 'preparedBy',
        header: t('routes.testReports.column.preparedBy'),
        priority: 'detail',
        cell: (row) => <FieldValue value={row.prepared_by} />,
      },
      {
        key: 'preparedSite',
        header: t('routes.testReports.column.preparedSite'),
        priority: 'detail',
        cell: (row) => <FieldValue value={row.prepared_site} />,
      },
      {
        key: 'createdAt',
        header: t('routes.testReports.column.createdAt'),
        priority: 'detail',
        cell: (row) => <span>{row.created_at}</span>,
      },
      {
        key: 'cite',
        header: t('routes.testReports.column.cite'),
        priority: 'secondary',
        cell: (row, surface) => {
          const edition = row.edition ?? '';
          const isSelected = edition !== '' && edition === selectedEdition;
          return (
            <Button
              type="button"
              variant="ghost"
              disabled={edition === ''}
              aria-pressed={isSelected}
              {...(surface === 'row' ? { 'data-testid': 'test-report-cite' } : {})}
              onClick={() => onSelectEdition(isSelected ? '' : edition)}
            >
              {t(isSelected ? 'routes.testReports.citeSelected' : 'routes.testReports.cite')}
            </Button>
          );
        },
      },
    ],
    [t, selectedEdition, onSelectEdition],
  );

  const rows = reports.data ?? [];

  return (
    <section aria-labelledby="test-reports-list-heading">
      <SectionBand
        title={t('routes.testReports.listSection')}
        titleId="test-reports-list-heading"
      />
      {reports.isPending && <DataTableSkeleton columns={columns.length} rows={4} />}
      {reports.isError && (
        <ErrorState
          testId="test-reports-error"
          message={describeApiError(reports.error, 'platform', {
            forbidden: t('routes.testReports.list.forbidden'),
            notFound: t('routes.testReports.list.notFound'),
            serviceUnavailable: t('routes.testReports.list.unavailable'),
            network: t('routes.testReports.list.network'),
            default: t('routes.testReports.list.failed'),
          })}
        />
      )}
      {reports.isSuccess && rows.length === 0 && (
        <EmptyState
          testId="test-reports-empty"
          title={t('routes.testReports.emptyTitle')}
          description={t('routes.testReports.emptyDescription')}
        />
      )}
      {reports.isSuccess && rows.length > 0 && (
        <DataTable<ReportEnvelope>
          caption={t('routes.testReports.tableCaption')}
          testId="test-reports-table"
          columns={columns}
          rows={rows}
          rowKey={(row) => row.report_id}
          rowTestId="test-report-row"
        />
      )}
    </section>
  );
}

/** 성적서 생성 — edition 필수, 나머지는 선택(비우면 키 생략). */
function CreateReportPanel({
  mutation,
}: {
  readonly mutation: UseMutationResult<ReportEnvelope, ApiError, ReportDraft>;
}): JSX.Element {
  const { t } = useT();
  const [draft, setDraft] = useState<ReportDraft>(EMPTY_DRAFT);
  const auth = useAuthSession();

  // 인가 안내는 **비차단**이다. 백엔드는 `platform:admin` 토큰 ∪ 프로젝트-멤버십
  // admin 의 UNION 으로 판정하는데 브라우저는 토큰만 본다 → 토큰 미보유를 근거로
  // 버튼을 잠그면 멤버십-admin 이 정당한 권한으로도 막히는 false negative 가 된다.
  // 기존 프로젝트 멤버십 편집 화면의 선례를 따른다: 토큰에 권한이
  // 없으면 **안내만** 띄우고 제출은 열어 두며, 최종 권위는 백엔드(403 → 에러
  // 표면화)에 둔다. 이 불리언은 문구 표시에만 쓰인다 — 아래 `canSubmit` 이 이것을
  // 참조하는 순간 그 false negative 가 다시 열린다.
  const hasTokenPermission =
    auth.kind === 'authenticated' && auth.principal.permissions.includes(PERMISSION_PLATFORM_ADMIN);

  const canSubmit = draft.edition.trim() !== '' && !mutation.isPending;

  const fields: readonly (readonly [keyof ReportDraft, string, 'text' | 'date'])[] = [
    ['dateTestedStart', 'dateTestedStart', 'date'],
    ['dateTestedEnd', 'dateTestedEnd', 'date'],
    ['dateOfIssue', 'dateOfIssue', 'date'],
    ['preparedBy', 'preparedBy', 'text'],
    ['preparedSite', 'preparedSite', 'text'],
  ];

  return (
    <section aria-labelledby="test-reports-create-heading">
      <SectionBand
        title={t('routes.testReports.createSection')}
        titleId="test-reports-create-heading"
      />
      <form
        aria-label={t('routes.testReports.createSection')}
        data-testid="test-report-create-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit) mutation.mutate(draft);
        }}
      >
        <Toolbar ariaLabel={t('routes.testReports.createSection')}>
          <FieldGroup label={t('routes.testReports.field.edition')} htmlFor="new-report-edition">
            <input
              id="new-report-edition"
              data-testid="new-report-edition"
              required
              value={draft.edition}
              placeholder={t('routes.testReports.field.editionPlaceholder')}
              onChange={(event) => setDraft((prev) => ({ ...prev, edition: event.target.value }))}
            />
          </FieldGroup>
          {fields.map(([key, token, inputType]) => (
            <FieldGroup
              key={key}
              label={t(`routes.testReports.field.${token}`)}
              htmlFor={`new-report-${token}`}
            >
              <input
                id={`new-report-${token}`}
                data-testid={`new-report-${token}`}
                type={inputType}
                value={draft[key]}
                onChange={(event) => setDraft((prev) => ({ ...prev, [key]: event.target.value }))}
              />
            </FieldGroup>
          ))}
          <Button
            type="submit"
            variant="primary"
            data-testid="new-report-submit"
            disabled={!canSubmit}
          >
            {mutation.isPending
              ? t('routes.testReports.create.submitting')
              : t('routes.testReports.create.submit')}
          </Button>
        </Toolbar>
      </form>

      {!hasTokenPermission && (
        <p className="section-hint" data-testid="new-report-permission-hint">
          {t('routes.testReports.create.permissionHint')}
        </p>
      )}
      <p className="section-hint" data-testid="new-report-optional-hint">
        {t('routes.testReports.create.optionalHint')}
      </p>

      {mutation.isError && (
        <ErrorState
          testId="new-report-error"
          message={describeApiError(mutation.error, 'platform', {
            // 409 = edition 중복. 백엔드는 이를 제네릭 ErrorCode.CONFLICT 로 매핑하므로
            // 성적서 전용 코드를 발명하지 않고 기존 오버라이드 축만 특화한다.
            conflict: t('routes.testReports.create.editionConflict'),
            forbidden: t('routes.testReports.create.forbidden'),
            notFound: t('routes.testReports.create.notFound'),
            badRequest: t('routes.testReports.create.invalid'),
            serviceUnavailable: t('routes.testReports.create.unavailable'),
            network: t('routes.testReports.create.network'),
            default: t('routes.testReports.create.failed'),
          })}
        />
      )}
      {mutation.isSuccess && (
        <StatusMessage
          tone="success"
          testId="new-report-success"
          message={t('routes.testReports.create.success', {
            edition: mutation.data?.edition ?? '',
          })}
        />
      )}
    </section>
  );
}

/** 자동 인용 — 프로젝트 스코프 조회. edition 은 파생 번호만 좌우하는 선택적 정제축. */
function CitationPanel({
  projectId,
  selectedEdition,
  editions,
  onSelectEdition,
}: {
  readonly projectId: string;
  readonly selectedEdition: string;
  readonly editions: readonly string[];
  readonly onSelectEdition: (edition: string) => void;
}): JSX.Element {
  const { t } = useT();
  const citation = useQuery<ReportCitationEnvelope, ApiError>({
    queryKey: queryKeys.project.reportCitation(projectId, selectedEdition),
    queryFn: () => fetchReportCitation(projectId, selectedEdition),
    ...REFETCH_STRATEGIES.NORMAL,
  });

  const editionOptions = useMemo(
    () => Array.from(new Set(editions.filter((edition) => edition !== ''))),
    [editions],
  );

  return (
    <section aria-labelledby="test-reports-citation-heading">
      <SectionBand
        title={t('routes.testReports.citationSection')}
        titleId="test-reports-citation-heading"
        {...(selectedEdition === ''
          ? {}
          : { meta: t('routes.testReports.citationEditionMeta', { edition: selectedEdition }) })}
      />
      <Toolbar ariaLabel={t('routes.testReports.citationEditionLabel')}>
        <FieldGroup label={t('routes.testReports.citationEditionLabel')} htmlFor="citation-edition">
          <select
            id="citation-edition"
            data-testid="citation-edition"
            value={selectedEdition}
            onChange={(event) => onSelectEdition(event.target.value)}
          >
            <option value="">{t('routes.testReports.citationEditionNone')}</option>
            {editionOptions.map((edition) => (
              <option key={edition} value={edition}>
                {edition}
              </option>
            ))}
          </select>
        </FieldGroup>
      </Toolbar>
      <p className="section-hint">{t('routes.testReports.citationEditionHint')}</p>

      {citation.isPending && (
        <BlockSkeleton
          lines={5}
          label={t('routes.testReports.citationLoading')}
          testId="citation-loading"
        />
      )}
      {citation.isError && (
        <ErrorState
          testId="citation-error"
          message={describeApiError(citation.error, 'platform', {
            forbidden: t('routes.testReports.citation.forbidden'),
            notFound: t('routes.testReports.citation.notFound'),
            serviceUnavailable: t('routes.testReports.citation.unavailable'),
            network: t('routes.testReports.citation.network'),
            default: t('routes.testReports.citation.failed'),
          })}
        />
      )}
      {citation.isSuccess && (
        <CitationBody citation={citation.data} selectedEdition={selectedEdition} />
      )}
    </section>
  );
}

function CitationBody({
  citation,
  selectedEdition,
}: {
  readonly citation: ReportCitationEnvelope;
  readonly selectedEdition: string;
}): JSX.Element {
  const { t } = useT();
  const headerFields: readonly (readonly [string, string | null | undefined])[] = [
    ['managementNumber', citation.management_number],
    ['fccId', citation.fcc_id],
    ['applicantName', citation.applicant_name],
    ['applicantAddress', citation.applicant_address],
    ['eutDescription', citation.eut_description],
    ['testStandard', citation.test_standard],
  ];

  return (
    <div data-testid="citation-body">
      <dl className="citation-list">
        <div className="citation-list__entry">
          <dt>{t('routes.testReports.citation.reportNumber')}</dt>
          <dd>
            {citationFieldState(citation.report_number) === 'value' ? (
              <span data-testid="citation-report-number">{citation.report_number}</span>
            ) : (
              // 무근거 공란 금지 — 파생값이 비는 원인은 둘뿐이므로 역산해 밝힌다.
              <span
                className="citation-value citation-value--absent"
                data-testid="citation-report-number-absent"
              >
                {t(
                  absentReportNumberReason(selectedEdition) === 'managementNumber'
                    ? 'routes.testReports.reportNumberAbsent.managementNumber'
                    : 'routes.testReports.reportNumberAbsent.edition',
                )}
              </span>
            )}
          </dd>
        </div>
        {headerFields.map(([token, value]) => (
          <div className="citation-list__entry" key={token}>
            <dt>{t(`routes.testReports.citation.${token}`)}</dt>
            <dd>
              <FieldValue value={value} testId={`citation-${token}`} />
            </dd>
          </div>
        ))}
      </dl>

      <SectionBand
        title={t('routes.testReports.citationSamplesSection')}
        titleId="test-reports-citation-samples-heading"
        meta={t('routes.testReports.citationSampleCount', {
          count: citation.samples.length,
        })}
      />
      {citation.samples.length === 0 ? (
        // 시료 배열이 빈 것은 정상 상태다(에러 아님).
        <EmptyState
          testId="citation-samples-empty"
          title={t('routes.testReports.citationSamplesEmptyTitle')}
          description={t('routes.testReports.citationSamplesEmptyDescription')}
        />
      ) : (
        <ul className="citation-sample-list" data-testid="citation-sample-list">
          {citation.samples.map((sample, index) => (
            <CitationSample
              key={sample.sample_number ?? String(index)}
              sample={sample}
              index={index}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function CitationSample({
  sample,
  index,
}: {
  readonly sample: ReportSampleCitation;
  readonly index: number;
}): JSX.Element {
  const { t } = useT();
  const firmware = sample.latest_firmware;
  const firmwareFields: readonly (readonly [string, string | null | undefined])[] = [
    ['bl', firmware?.bl],
    ['ap', firmware?.ap],
    ['cp', firmware?.cp],
    ['csc', firmware?.csc],
    ['rfCal', firmware?.rf_cal],
    ['hwRev', firmware?.hw_rev],
  ];

  return (
    <li className="citation-sample" data-testid="citation-sample">
      <p className="citation-sample__title">
        <FieldValue value={sample.sample_number} testId={`citation-sample-number-${index}`} />
      </p>
      <dl className="citation-list">
        <div className="citation-list__entry">
          <dt>{t('routes.testReports.citation.serialNumber')}</dt>
          <dd>
            <FieldValue value={sample.serial_number} testId={`citation-sample-serial-${index}`} />
          </dd>
        </div>
        {firmwareFields.map(([token, value]) => (
          <div className="citation-list__entry" key={token}>
            <dt>{t(`routes.testReports.citation.firmware.${token}`)}</dt>
            <dd>
              <FieldValue value={value} />
            </dd>
          </div>
        ))}
      </dl>
    </li>
  );
}

/**
 * 한 칸의 값 표시 — "값 있음 / 미기재(null) / 빈 문자열로 저장됨('')" 세 상태를
 * 뭉개지 않는 유일한 표시점. `'-'` 로 대체하면 세 사실이 한 글자로 붕괴한다.
 */
function FieldValue({
  value,
  testId,
}: {
  readonly value: string | null | undefined;
  readonly testId?: string;
}): JSX.Element {
  const { t } = useT();
  const state = citationFieldState(value);
  if (state === 'value') {
    return (
      <span
        className="citation-value"
        data-state={state}
        {...(testId === undefined ? {} : { 'data-testid': testId })}
      >
        {value}
      </span>
    );
  }
  return (
    <span
      className="citation-value citation-value--absent"
      data-state={state}
      {...(testId === undefined ? {} : { 'data-testid': testId })}
    >
      {t(state === 'blank' ? 'routes.testReports.value.blank' : 'routes.testReports.value.absent')}
    </span>
  );
}

export default TestReportsRoute;
export { TestReportsRoute };
