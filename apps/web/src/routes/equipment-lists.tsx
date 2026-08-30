import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import {
  confirmEquipmentList,
  createProjectEquipmentList,
  type CreateTestEquipmentListRequest,
  fetchEquipmentList,
  fetchProjectEquipmentLists,
  replaceEquipmentListItems,
  type TestEquipmentListEnvelope,
  type TestEquipmentListItem,
  type TestEquipmentListItemInput,
  type TestEquipmentListCollection,
  type TestEquipmentListSummary,
  type TestItemKey,
} from '@/api/platform-client';
import { queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { useT } from '@/i18n';
import { isValidProjectId } from '@/shared/project-id';
import { ProjectSelectField } from '@/shared/ProjectSelectField';
import { ROUTE_PATHS } from '@/shared/route-links';
import {
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

/**
 * 성적서 §6 장비목록 — 프로젝트가 **실제로 사용한** 계측기/시험용 소프트웨어를
 * 시험원이 고르고 확정하는 화면 (2026-08-07).
 *
 * FCC 성적서의 `6. TEST AND MEASUREMENT EQUIPMENT` 절은 표 두 개(장비 5열 /
 * UL Software 4열)로 이루어진다. EMS(사내 장비관리시스템)가 팀 × 시험항목 × 챔버별
 * **표준** 리스트의 SSOT 이고, 이 화면은 그 목록에서 출발해 **이번 프로젝트가 쓴 것**
 * 으로 편집·확정한 기록을 만든다. 시험원은 시험이 끝났거나 ~90% 진행된 성적서 작성
 * 시점에 이 작업을 한다 — 측정 중 자동 캡처가 아니다.
 *
 * ## 이 화면이 지키는 정직성 규칙
 *
 * 1. **두 표의 열 순서는 서버가 준다** — 응답 `tables[]` 가 `{item_type, columns}`
 *    로 열 순서를 싣는다(백엔드 도메인 SSOT `test_equipment_list_policy.py`).
 *    여기서 열 배열을 다시 선언하면 같은 순서가 TS/Python 두 곳으로 쪼개지고, 그
 *    드리프트는 **제출된 성적서에서만** 드러난다. 화면은 `tables` 를 읽기만 한다.
 * 2. **`sort_order` 를 보내지 않는다** — 배열 위치가 곧 순서이고 서버가 부여한다.
 *    요청 스키마에 그 필드가 아예 없다.
 * 3. **서버 상태를 state 로 복사하지 않는다** — 편집 중인 행은 매 렌더
 *    `localEdit ?? serverRows` 로 파생한다. `useEffect` 로 미러링하면 서버가
 *    갱신될 때 편집이 조용히 덮이거나, 반대로 낡은 편집이 새 서버 값을 가린다.
 *    로컬 편집은 **자기 목록 id 를 함께 들고** 있어서 목록을 바꿔도 다른 목록에
 *    적용될 수 없다(전량 교체 PUT 이라 그것은 원클릭 덮어쓰기가 된다).
 * 4. **확정본은 읽기 전용이고 이유를 말한다** — 확정된 목록은 성적서가 렌더된
 *    스냅샷이다. 편집을 막되 "왜 막혔는지"를 문구로 보인다.
 * 5. **토큰으로 잠그지 않는다** — 백엔드 `authorize` 는 토큰 ∪ 프로젝트 멤버십
 *    UNION 이다. 토큰 미보유를 근거로 버튼을 비활성화하면 멤버십으로 권한을 받은
 *    시험원이 부당하게 차단된다. 백엔드를 최종 권위로 두고 403 을 표면화한다.
 * 6. **시험항목 어휘도 서버가 준다** — 목록 응답 `test_items` 가 고를 수 있는
 *    시험항목(DTS/BLE/BT/UNII, 각각 성적서 한 편)을 싣는다. 규칙 1과 같은 이유이며
 *    한 겹 더 있다: 생성된 TS 타입은 **타입 레벨 union** 이라 런타임 배열을 주지
 *    못하므로, 프론트가 선택지를 만들려면 배열을 적는 수밖에 없고 그 순간 어휘가
 *    두 곳이 된다. locale 에서 얻는 것은 서버가 준 키의 **번역**뿐이다.
 *
 * 목록에 페이지네이션이 없다 — 백엔드가 keyset 파라미터를 노출하지 않는다(프로젝트당
 * 시험항목 수만큼의 작은 집합). 없는 페이지네이션을 흉내 내면 거짓말이 된다.
 * 행 단위 추가/삭제 API 도 없다 — 저장은 전량 교체 PUT 한 번이다.
 */

/** 선택된 프로젝트를 싣는 URL 파라미터 (저장소 관례). */
const PROJECT_PARAM = 'project';

/** 선택된 장비목록. URL 에 실어 두면 편집 중인 목록을 그대로 공유할 수 있다. */
const LIST_PARAM = 'list';

/** 편집기의 한 행 — 서버 항목에서 파생하거나 사용자가 새로 만든다. */
type EditableRow = TestEquipmentListItemInput;

/**
 * 서버가 소유해 요청에 실어 보내지 않는 항목 필드.
 *
 * `sort_order` 는 배열 위치이고 `item_id` 는 행 정체성이다 — 둘 다 요청 스키마에
 * 없다. 나머지 필드는 **서버가 준 그대로** 왕복하므로 열 목록을 프론트가 알 필요가
 * 없다(열 순서는 응답 `tables[]` 가, 필드 집합은 서버 응답 자체가 정한다).
 */
const SERVER_OWNED_ITEM_FIELDS: ReadonlySet<string> = new Set(['item_id', 'sort_order']);

/**
 * 서버 항목 → 편집 행. `item_id` / `sort_order` 는 서버 소유라 옮기지 않는다
 * (요청 스키마에도 없다).
 */
function toEditableRows(items: readonly TestEquipmentListItem[]): EditableRow[] {
  return items.map((item) => {
    const row: EditableRow = { item_type: item.item_type };
    // 서버 항목의 키를 그대로 순회한다 — 열 이름을 여기 나열하면 §6 표의 열
    // 집합 사본이 하나 더 생기고, 서버가 필드를 추가해도 이 함수만 조용히
    // 뒤처진다. 서버 소유 필드(item_id / sort_order)만 제외한다.
    for (const [key, value] of Object.entries(item)) {
      if (SERVER_OWNED_ITEM_FIELDS.has(key)) continue;
      if (value === null || value === undefined) continue;
      Object.assign(row, { [key]: value });
    }
    return row;
  });
}

/**
 * 폼 초안 → 생성 요청 본문. **비운 칸은 키를 생략한다** — `''` 를 보내면 빈 문자열이
 * 저장돼 "미기재"와 구분되지 않는다. 순수 함수로 분리해 봉인이 본문을 직접 검사할
 * 수 있게 한다(`buildCreateReportRequest` 와 같은 규칙).
 */
export function buildCreateEquipmentListRequest(
  testItemKey: TestItemKey,
  testItemName: string,
): CreateTestEquipmentListRequest {
  const body: CreateTestEquipmentListRequest = { test_item_key: testItemKey };
  const name = testItemName.trim();
  if (name !== '') Object.assign(body, { test_item_name: name });
  return body;
}

/** 저장 본문. 빈 행(모든 칸이 공백)은 보내지 않는다 — 빈 행은 표의 빈 줄이 된다. */
export function buildReplaceItemsRequest(rows: readonly EditableRow[]): {
  items: EditableRow[];
} {
  const items = rows.filter((row) =>
    Object.entries(row).some(
      ([key, value]) => key !== 'item_type' && typeof value === 'string' && value.trim() !== '',
    ),
  );
  return { items };
}

function EquipmentListsRoute(): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const projectId = (searchParams.get(PROJECT_PARAM) ?? '').trim();
  const selectedListId = (searchParams.get(LIST_PARAM) ?? '').trim();
  const hasProject = isValidProjectId(projectId);

  /**
   * 고른 시험항목 — `''` 는 "아직 안 골랐다"이고, 나머지는 **서버가 준 어휘의
   * 멤버**다. 타입이 union 이라 자유 문자열이 여기 들어올 수 없다.
   */
  const [testItemKey, setTestItemKey] = useState<TestItemKey | ''>('');
  const [testItemName, setTestItemName] = useState('');
  /**
   * 편집 중인 행 — **어느 목록의 것인지 함께** 들고 있다.
   *
   * 행 배열만 들면 목록을 바꿀 때 A 의 미저장 편집이 B 밑에 살아남는다. 저장이
   * 전량 교체 PUT 이므로 그것은 곧 **원클릭 덮어쓰기**다. `selectList` 에서
   * `setLocalRows(null)` 을 부르는 것만으로 막을 수도 있지만, 그건 호출을 하나
   * 빠뜨리면 무너지는 lucky guard 다(형제 봉인
   * `TestDraftPanelsAreKeyedByDraftId` 의 docstring 이 명시적으로 거부한 형태).
   * 편집이 자기 목록 id 를 들고 있으면 다른 목록에 적용되는 것이 **구조적으로**
   * 불가능하다.
   */
  const [localEdit, setLocalEdit] = useState<{
    readonly listId: string;
    readonly rows: EditableRow[];
  } | null>(null);

  const listsQuery = useQuery<TestEquipmentListCollection>({
    queryKey: queryKeys.project.equipmentLists(projectId),
    queryFn: () => fetchProjectEquipmentLists(projectId),
    enabled: hasProject,
    ...REFETCH_STRATEGIES.NORMAL,
  });

  const detailQuery = useQuery<TestEquipmentListEnvelope>({
    queryKey: queryKeys.project.equipmentList(projectId, selectedListId),
    queryFn: () => fetchEquipmentList(projectId, selectedListId),
    enabled: hasProject && selectedListId !== '',
    ...REFETCH_STRATEGIES.NORMAL,
  });

  /** 고를 수 있는 시험항목 — **서버 소유**. 아직 못 받았으면 빈 목록이다. */
  const testItemOptions: readonly TestItemKey[] = listsQuery.data?.test_items ?? [];

  const detail: TestEquipmentListEnvelope | undefined = detailQuery.data;
  const isConfirmed = detail?.list.status === 'confirmed';
  /**
   * 편집 행은 **이 목록의** 로컬 편집이 있으면 그것, 없으면 서버 항목에서 파생한다.
   * id 가 어긋난 편집은 자동으로 무시되므로 목록 전환이 편집을 옮기지 못한다.
   */
  const rows: EditableRow[] = useMemo(
    () =>
      localEdit?.listId === selectedListId ? localEdit.rows : toEditableRows(detail?.items ?? []),
    [localEdit, selectedListId, detail],
  );

  /** 이 목록의 편집을 기록한다 — 다른 목록 것으로 오인될 수 없다. */
  function setRows(next: EditableRow[]): void {
    setLocalEdit({ listId: selectedListId, rows: next });
  }

  function selectProject(next: string): void {
    const params = new URLSearchParams(searchParams);
    if (next === '') params.delete(PROJECT_PARAM);
    else params.set(PROJECT_PARAM, next);
    params.delete(LIST_PARAM);
    setLocalEdit(null);
    setSearchParams(params, { replace: true });
  }

  function selectList(next: string): void {
    const params = new URLSearchParams(searchParams);
    if (next === '') params.delete(LIST_PARAM);
    else params.set(LIST_PARAM, next);
    setLocalEdit(null);
    setSearchParams(params, { replace: true });
  }

  const createMutation = useMutation({
    mutationFn: () => {
      // 버튼이 이미 비활성이지만 타입은 그것을 모른다. 빈 키를 조용히 보내
      // 서버에서 400 을 받는 대신 여기서 멈춘다 — 서버가 어휘를 집행하므로
      // 빈 키는 어차피 거절되고, 그 왕복은 순수한 낭비다.
      if (testItemKey === '') {
        return Promise.reject(new Error('test item not selected'));
      }
      return createProjectEquipmentList(
        projectId,
        buildCreateEquipmentListRequest(testItemKey, testItemName),
      );
    },
    onSuccess: (created) => {
      setTestItemKey('');
      setTestItemName('');
      void queryClient.invalidateQueries({
        queryKey: queryKeys.project.equipmentLists(projectId),
      });
      selectList(created.list_id);
    },
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      replaceEquipmentListItems(projectId, selectedListId, buildReplaceItemsRequest(rows)),
    onSuccess: () => {
      setLocalEdit(null);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.project.equipmentList(projectId, selectedListId),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.project.equipmentLists(projectId),
      });
    },
  });

  const confirmMutation = useMutation({
    mutationFn: () => confirmEquipmentList(projectId, selectedListId),
    onSuccess: () => {
      setLocalEdit(null);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.project.equipmentList(projectId, selectedListId),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.project.equipmentLists(projectId),
      });
    },
  });

  const listColumns: DataTableColumn<TestEquipmentListSummary>[] = [
    {
      key: 'test_item_key',
      header: t('routes.equipmentLists.columns.testItem'),
      priority: 'primary',
      cell: (row) => (
        <Button
          variant="ghost"
          onClick={() => selectList(row.list_id)}
          data-testid={`equipment-list-select-${row.list_id}`}
        >
          {row.test_item_name ?? row.test_item_key}
        </Button>
      ),
    },
    {
      key: 'status',
      header: t('routes.equipmentLists.columns.status'),
      priority: 'primary',
      cell: (row) =>
        row.status === 'confirmed'
          ? t('routes.equipmentLists.status.confirmed')
          : t('routes.equipmentLists.status.draft'),
    },
    {
      key: 'item_count',
      header: t('routes.equipmentLists.columns.itemCount'),
      priority: 'detail',
      cell: (row) => String(row.item_count),
    },
  ];

  return (
    <section className="equipment-lists" aria-labelledby="equipment-lists-heading">
      <PageHeader
        title={t('routes.equipmentLists.title')}
        titleId="equipment-lists-heading"
        description={t('routes.equipmentLists.description')}
      />

      <p className="section-hint" data-testid="equipment-lists-domain-note">
        {t('routes.equipmentLists.domainNote')}{' '}
        <Link to={ROUTE_PATHS.testReports} data-testid="equipment-lists-reports-link">
          {t('routes.equipmentLists.reportsLink')}
        </Link>
      </p>

      <WorkbenchLayout
        className="equipment-lists-workbench"
        mainLabel={t('routes.equipmentLists.title')}
        testId="equipment-lists-workbench"
        main={
          <>
            <Card
              as="section"
              variant="summary"
              aria-labelledby="equipment-lists-project-heading"
              testId="equipment-lists-project-card"
            >
              <SectionBand
                title={t('routes.equipmentLists.projectSection')}
                titleId="equipment-lists-project-heading"
              />
              <Toolbar ariaLabel={t('routes.equipmentLists.projectSection')}>
                <ProjectSelectField
                  value={projectId}
                  onChange={selectProject}
                  selectId="equipment-lists-project"
                  selectTestId="equipment-lists-project-select"
                />
              </Toolbar>
            </Card>

            {!hasProject ? (
              <EmptyState
                title={t('routes.equipmentLists.empty.noProject')}
                description={t('routes.equipmentLists.empty.noProjectHint')}
                testId="equipment-lists-no-project"
              />
            ) : null}

            {hasProject && listsQuery.isPending ? (
              <DataTableSkeleton rows={3} columns={listColumns.length} />
            ) : null}

            {hasProject && listsQuery.isError ? (
              <ErrorState
                message={describeApiError(listsQuery.error, 'platform', {
                  forbidden: t('routes.equipmentLists.error.forbidden'),
                  notFound: t('routes.equipmentLists.error.projectNotFound'),
                  serviceUnavailable: t('routes.equipmentLists.error.unavailable'),
                  network: t('routes.equipmentLists.error.network'),
                  default: t('routes.equipmentLists.error.generic'),
                })}
                testId="equipment-lists-error"
              />
            ) : null}

            {hasProject && listsQuery.isSuccess ? (
              listsQuery.data.lists.length === 0 ? (
                <EmptyState
                  title={t('routes.equipmentLists.empty.noLists')}
                  description={t('routes.equipmentLists.empty.noListsHint')}
                  testId="equipment-lists-empty"
                />
              ) : (
                <DataTable
                  caption={t('routes.equipmentLists.listCaption')}
                  columns={listColumns}
                  rows={listsQuery.data.lists}
                  rowKey={(row) => row.list_id}
                  testId="equipment-lists-table"
                />
              )
            ) : null}

            {detail ? (
              <Card
                as="section"
                variant="summary"
                aria-labelledby="equipment-lists-editor-heading"
                testId="equipment-lists-editor-card"
              >
                <SectionBand
                  title={t('routes.equipmentLists.editorSection')}
                  titleId="equipment-lists-editor-heading"
                />

                {isConfirmed ? (
                  <StatusMessage
                    tone="info"
                    testId="equipment-lists-frozen-note"
                    message={t('routes.equipmentLists.frozenNote')}
                  />
                ) : null}

                {/*
                  두 표를 서버가 준 열 순서대로 그린다. 열 이름을 여기서 다시
                  적지 않는 것이 이 화면의 핵심 규칙이다.
                */}
                {detail.tables.map((table) => {
                  const tableRows = rows
                    .map((row, index) => ({ row, index }))
                    .filter((entry) => entry.row.item_type === table.item_type);
                  return (
                    <div
                      key={table.item_type}
                      className="equipment-lists-table-block"
                      data-testid={`equipment-lists-block-${table.item_type}`}
                    >
                      <h3 className="equipment-lists-table-title">
                        {t(`routes.equipmentLists.tables.${table.item_type}`)}
                      </h3>
                      <table className="equipment-lists-editor-table">
                        <thead>
                          <tr>
                            {table.columns.map((column) => (
                              <th key={column} scope="col">
                                {t(`routes.equipmentLists.fields.${column}`)}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {tableRows.map((entry) => (
                            <tr key={`${table.item_type}-${entry.index}`}>
                              {table.columns.map((column) => (
                                <td key={column}>
                                  <input
                                    className="equipment-lists-cell-input"
                                    aria-label={t(`routes.equipmentLists.fields.${column}`)}
                                    value={
                                      (entry.row as Record<string, string | undefined>)[column] ??
                                      ''
                                    }
                                    readOnly={isConfirmed}
                                    onChange={(event) => {
                                      const next = [...rows];
                                      next[entry.index] = {
                                        ...entry.row,
                                        [column]: event.target.value,
                                      };
                                      setRows(next);
                                    }}
                                  />
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {isConfirmed ? null : (
                        <Button
                          variant="secondary"
                          onClick={() => setRows([...rows, { item_type: table.item_type }])}
                          data-testid={`equipment-lists-add-${table.item_type}`}
                        >
                          {t('routes.equipmentLists.addRow')}
                        </Button>
                      )}
                    </div>
                  );
                })}

                {isConfirmed ? null : (
                  <Toolbar ariaLabel={t('routes.equipmentLists.editorSection')}>
                    <Button
                      variant="primary"
                      onClick={() => saveMutation.mutate()}
                      disabled={saveMutation.isPending}
                      data-testid="equipment-lists-save"
                    >
                      {t('routes.equipmentLists.save')}
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => confirmMutation.mutate()}
                      disabled={confirmMutation.isPending || rows.length === 0}
                      data-testid="equipment-lists-confirm"
                    >
                      {t('routes.equipmentLists.confirm')}
                    </Button>
                  </Toolbar>
                )}

                {saveMutation.isError ? (
                  <ErrorState
                    testId="equipment-lists-save-error"
                    message={describeApiError(saveMutation.error, 'platform', {
                      forbidden: t('routes.equipmentLists.error.forbidden'),
                      notFound: t('routes.equipmentLists.error.listNotFound'),
                      conflict: t('routes.equipmentLists.error.frozen'),
                      serviceUnavailable: t('routes.equipmentLists.error.unavailable'),
                      network: t('routes.equipmentLists.error.network'),
                      default: t('routes.equipmentLists.error.generic'),
                    })}
                  />
                ) : null}

                {confirmMutation.isError ? (
                  <ErrorState
                    testId="equipment-lists-confirm-error"
                    message={describeApiError(confirmMutation.error, 'platform', {
                      forbidden: t('routes.equipmentLists.error.forbidden'),
                      notFound: t('routes.equipmentLists.error.listNotFound'),
                      conflict: t('routes.equipmentLists.error.notConfirmable'),
                      serviceUnavailable: t('routes.equipmentLists.error.unavailable'),
                      network: t('routes.equipmentLists.error.network'),
                      default: t('routes.equipmentLists.error.generic'),
                    })}
                  />
                ) : null}
              </Card>
            ) : null}
          </>
        }
        rail={
          hasProject ? (
            <Card
              as="section"
              variant="summary"
              aria-labelledby="equipment-lists-create-heading"
              testId="equipment-lists-create-card"
            >
              <SectionBand
                title={t('routes.equipmentLists.createSection')}
                titleId="equipment-lists-create-heading"
              />
              <p className="section-hint">{t('routes.equipmentLists.createHint')}</p>
              {/*
                시험항목은 **닫힌 어휘**다 — 성적서 한 편(DTS/BLE/BT/UNII)에
                대응하므로 자유 입력이면 성적서 어느 편에도 대응하지 않는 목록이
                생긴다. 선택지는 서버가 준 `test_items` 를 그대로 순회한다. 여기에
                배열을 적으면 어휘가 TS/Python 두 곳으로 쪼개진다(라벨만 locale
                에서 얻는데, 그것은 서버가 준 키의 번역이지 어휘 재선언이 아니다 —
                `tables.${item_type}` / `fields.${column}` 과 같은 관용구).
              */}
              <FieldGroup
                label={t('routes.equipmentLists.fields.testItemKey')}
                htmlFor="equipment-lists-test-item-key"
              >
                <select
                  id="equipment-lists-test-item-key"
                  value={testItemKey}
                  /*
                    DOM 이 주는 값은 `string` 이다. 캐스팅하는 대신 **서버가 준
                    배열에서 되찾아** 좁힌다 — 그 배열의 원소 타입이 곧 어휘이므로
                    캐스트 없이 union 이 되고, 어휘 밖 값은 구조적으로 상태에
                    들어올 수 없다.
                  */
                  onChange={(event) =>
                    setTestItemKey(testItemOptions.find((key) => key === event.target.value) ?? '')
                  }
                  data-testid="equipment-lists-test-item-key"
                >
                  <option value="">{t('routes.equipmentLists.testItemPlaceholder')}</option>
                  {testItemOptions.map((key) => (
                    <option key={key} value={key}>
                      {t(`routes.equipmentLists.testItems.${key}`)}
                    </option>
                  ))}
                </select>
              </FieldGroup>
              <FieldGroup
                label={t('routes.equipmentLists.fields.testItemName')}
                htmlFor="equipment-lists-test-item-name"
              >
                <input
                  id="equipment-lists-test-item-name"
                  value={testItemName}
                  onChange={(event) => setTestItemName(event.target.value)}
                  data-testid="equipment-lists-test-item-name"
                />
              </FieldGroup>
              <Button
                variant="primary"
                onClick={() => createMutation.mutate()}
                disabled={createMutation.isPending || testItemKey === ''}
                data-testid="equipment-lists-create"
              >
                {t('routes.equipmentLists.create')}
              </Button>
              {createMutation.isError ? (
                <ErrorState
                  testId="equipment-lists-create-error"
                  message={describeApiError(createMutation.error, 'platform', {
                    forbidden: t('routes.equipmentLists.error.forbidden'),
                    notFound: t('routes.equipmentLists.error.projectNotFound'),
                    conflict: t('routes.equipmentLists.error.duplicate'),
                    serviceUnavailable: t('routes.equipmentLists.error.unavailable'),
                    network: t('routes.equipmentLists.error.network'),
                    default: t('routes.equipmentLists.error.generic'),
                  })}
                />
              ) : null}
            </Card>
          ) : null
        }
      />
    </section>
  );
}

export default EquipmentListsRoute;
