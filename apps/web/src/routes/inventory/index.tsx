import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import {
  PERMISSION_PLATFORM_READ,
  PERMISSION_PLATFORM_SAMPLE_HARD_DELETE,
  PERMISSION_PLATFORM_SAMPLE_WRITE,
} from '@/api/permissions';
import {
  changeSampleStatus,
  fetchSample,
  fetchSampleInventory,
  hardDeleteSample,
  softDeleteSample,
  type SampleInventoryItem,
  type SampleInventoryStatusFilter,
} from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { useAuthSession } from '@/auth/route-guard';
import { RequirePermission } from '@/auth/route-guard';
import { useT } from '@/i18n';
import { isValidProjectId } from '@/shared/project-id';
import { ProjectSelectField } from '@/shared/ProjectSelectField';
import { projectScopedHref, ROUTE_PATHS } from '@/shared/route-links';
import {
  BlockSkeleton,
  Button,
  describeApiError,
  EmptyState,
  ErrorState,
  FieldGroup,
  PageHeader,
  SectionBand,
} from '@/ui';

import SampleCustodyPanel from './SampleCustodyPanel';
import SampleEditor from './SampleEditor';
import SampleExportActions from './SampleExportActions';
import SampleHistory from './SampleHistory';
import SampleIntakeHistory from './SampleIntakeHistory';

const VALID_STATUSES: readonly SampleInventoryStatusFilter[] = ['active', 'deleted', 'all'];

function readStatus(value: string | null): SampleInventoryStatusFilter | undefined {
  return value !== null && (VALID_STATUSES as readonly string[]).includes(value)
    ? (value as SampleInventoryStatusFilter)
    : undefined;
}

function asOfInputValue(value: string | undefined): string {
  if (value === undefined) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  const local = new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

/** 보유 상태 라벨 — 규칙 자체는 서버(커널 `custody_state`)가 정한다.
 *
 * ⚠️ 여기서 `event_type` 을 보고 판단하지 않는다. 그러면 같은 규칙이 SQL · 커널 ·
 * 화면 세 곳에 살게 되고, 서로 다르게 틀릴 수 있다. 화면은 결과를 옮겨 적을 뿐이다.
 */
function custodyStateLabel(
  t: (key: string) => string,
  state: SampleInventoryItem['custody_state'],
): string {
  if (state === 'in_custody') return t('routes.sampleInventory.custodyStateInCustody');
  if (state === 'released') return t('routes.sampleInventory.custodyStateReleased');
  return t('routes.sampleInventory.custodyStateUnknown');
}

export function InventoryRoute(): JSX.Element {
  const { t } = useT();
  return (
    <section className="inventory" aria-labelledby="inventory-heading">
      <PageHeader
        title={t('routes.sampleInventory.pageTitle')}
        titleId="inventory-heading"
        description={t('routes.sampleInventory.pageDescription')}
      />
      <RequirePermission permission={PERMISSION_PLATFORM_READ}>
        <SampleInventoryWorkbench />
      </RequirePermission>
    </section>
  );
}

function SampleInventoryWorkbench(): JSX.Element {
  const { t } = useT();
  const auth = useAuthSession();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const projectId = searchParams.get('project')?.trim() ?? '';
  const sampleId = searchParams.get('sample')?.trim() ?? '';
  const team = searchParams.get('team')?.trim() ?? undefined;
  const status = readStatus(searchParams.get('status'));
  const asOf = searchParams.get('as_of')?.trim() ?? undefined;
  const includeDeleted = searchParams.get('include_deleted') === 'true';
  // 등록 모드를 URL 에 두는 이유: 이 화면의 다른 상태(프로젝트·필터·선택)가 전부
  // 거기 있고, 새로고침이나 링크 공유로 폼이 조용히 닫히면 입력 중이던 사람이
  // 무엇을 잃었는지 알 수 없다.
  const creating = searchParams.get('create') === '1';
  const historical = asOf !== undefined;
  const isAuthenticated = auth.kind === 'authenticated';
  const canWrite =
    isAuthenticated && auth.principal.permissions.includes(PERMISSION_PLATFORM_SAMPLE_WRITE);
  const canHardDelete =
    isAuthenticated && auth.principal.permissions.includes(PERMISSION_PLATFORM_SAMPLE_HARD_DELETE);

  const inventoryFilters = useMemo(
    () => ({
      projectId,
      includeDeleted,
      limit: 100,
      ...(team === undefined ? {} : { team }),
      ...(status === undefined ? {} : { status }),
      ...(asOf === undefined ? {} : { asOf }),
    }),
    [asOf, includeDeleted, projectId, status, team],
  );
  const inventory = useInfiniteQuery({
    queryKey: queryKeys.sampleInventory.list(inventoryFilters),
    queryFn: ({ pageParam }) =>
      fetchSampleInventory({
        ...inventoryFilters,
        ...(pageParam === null ? {} : { after: pageParam }),
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: isValidProjectId(projectId),
  });

  const selected = useQuery({
    queryKey: queryKeys.sampleInventory.detail(projectId, sampleId, asOf),
    queryFn: () => fetchSample(projectId, sampleId, asOf),
    enabled: isValidProjectId(projectId) && sampleId !== '',
  });

  const selectedSample = selected.data;
  const updateSearch = (changes: Record<string, string | null>): void => {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === '') next.delete(key);
      else next.set(key, value);
    }
    setSearchParams(next);
  };

  const invalidateSample = async (updated?: SampleInventoryItem): Promise<void> => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.sampleInventory.all });
    if (updated !== undefined) {
      updateSearch({ sample: updated.sample_id });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.sampleInventory.detail(projectId, updated.sample_id, asOf),
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.sampleInventory.history(projectId, updated.sample_id),
      });
    }
  };

  const statusMutation = useMutation({
    mutationFn: ({
      nextStatus,
      expectedVersion,
    }: {
      nextStatus: 'active' | 'deleted';
      expectedVersion: number;
    }) =>
      changeSampleStatus(projectId, sampleId, {
        status: nextStatus,
        expected_version: expectedVersion,
      }),
    onSuccess: (updated) => void invalidateSample(updated),
  });
  const deleteMutation = useMutation({
    mutationFn: (expectedVersion: number) =>
      softDeleteSample(projectId, sampleId, { expected_version: expectedVersion }),
    onSuccess: (updated) => void invalidateSample(updated),
  });
  const hardDeleteMutation = useMutation({
    mutationFn: () => hardDeleteSample(sampleId),
    onSuccess: () => {
      updateSearch({ sample: null });
      void queryClient.invalidateQueries({ queryKey: queryKeys.sampleInventory.all });
    },
  });

  const editorSample = selectedSample ?? undefined;
  const listItems = inventory.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="inventory-workbench" data-testid="inventory-workbench">
      <section className="inventory-workbench-panel" aria-labelledby="inventory-project-heading">
        <SectionBand
          title={t('routes.sampleInventory.projectSection')}
          titleId="inventory-project-heading"
        />
        <ProjectSelectField
          value={projectId}
          onChange={(value) => setSearchParams(value === '' ? {} : { project: value })}
          selectId="inventory-project-select"
          selectTestId="inventory-project-select"
        />
      </section>

      <section className="inventory-workbench-panel" aria-labelledby="inventory-filter-heading">
        <SectionBand
          title={t('routes.sampleInventory.filters.title')}
          titleId="inventory-filter-heading"
        />
        <div className="sample-inventory-filters">
          <FieldGroup
            label={t('routes.sampleInventory.filters.team')}
            htmlFor="inventory-team-filter"
          >
            <input
              id="inventory-team-filter"
              data-testid="inventory-team-filter"
              value={team ?? ''}
              onChange={(event) => updateSearch({ team: event.target.value })}
            />
          </FieldGroup>
          <FieldGroup
            label={t('routes.sampleInventory.filters.status')}
            htmlFor="inventory-status-filter"
          >
            <select
              id="inventory-status-filter"
              data-testid="inventory-status-filter"
              value={status ?? 'active'}
              onChange={(event) => updateSearch({ status: event.target.value })}
            >
              <option value="active">{t('routes.sampleInventory.filters.active')}</option>
              <option value="deleted">{t('routes.sampleInventory.filters.deleted')}</option>
              <option value="all">{t('routes.sampleInventory.filters.all')}</option>
            </select>
          </FieldGroup>
          <FieldGroup
            label={t('routes.sampleInventory.filters.asOf')}
            htmlFor="inventory-as-of-filter"
          >
            <input
              id="inventory-as-of-filter"
              data-testid="inventory-as-of-filter"
              type="datetime-local"
              value={asOfInputValue(asOf)}
              onChange={(event) => {
                if (event.target.value === '') updateSearch({ as_of: null });
                else updateSearch({ as_of: new Date(event.target.value).toISOString() });
              }}
            />
          </FieldGroup>
          <label className="checkbox-field" htmlFor="inventory-include-deleted">
            <input
              id="inventory-include-deleted"
              data-testid="inventory-include-deleted"
              type="checkbox"
              checked={includeDeleted || status === 'all'}
              onChange={(event) =>
                updateSearch({ include_deleted: event.target.checked ? 'true' : null })
              }
            />
            {t('routes.sampleInventory.filters.includeDeleted')}
          </label>
        </div>
        {historical && (
          <p className="inventory-as-of-notice" data-testid="inventory-as-of-notice">
            {t('routes.sampleInventory.filters.asOfReadonly', { asOf })}
          </p>
        )}
      </section>

      <section className="inventory-workbench-panel" aria-labelledby="inventory-list-heading">
        <div className="sample-inventory-list__header">
          <SectionBand
            title={t('routes.sampleInventory.listSection')}
            titleId="inventory-list-heading"
          />
          {/* 등록 폼은 사이드에 늘 펼쳐져 있지 않고 이 버튼이 연다 (운영자 요구,
              2026-09-04). 열면 목록 선택은 해제된다 — 편집과 등록이 동시에 열려
              있으면 어느 폼에 적고 있는지 화면이 말해주지 못한다. */}
          {isValidProjectId(projectId) && !historical && canWrite && (
            <Button
              type="button"
              variant={creating ? 'secondary' : 'primary'}
              onClick={() =>
                updateSearch(creating ? { create: null } : { create: '1', sample: null })
              }
              data-testid="inventory-create-toggle"
            >
              {creating
                ? t('routes.sampleInventory.closeCreate')
                : t('routes.sampleInventory.openCreate')}
            </Button>
          )}
        </div>
        {!isValidProjectId(projectId) && (
          <EmptyState
            testId="inventory-project-empty"
            title={t('routes.sampleInventory.selectProjectTitle')}
            description={t('routes.sampleInventory.selectProjectDescription')}
          />
        )}
        {isValidProjectId(projectId) && inventory.isPending && (
          <BlockSkeleton lines={5} testId="inventory-loading" />
        )}
        {isValidProjectId(projectId) && inventory.isError && (
          <ErrorState
            testId="inventory-error"
            message={describeApiError(inventory.error, 'platform', {
              default: t('routes.sampleInventory.loadFailed'),
            })}
          />
        )}
        {isValidProjectId(projectId) && inventory.isSuccess && listItems.length === 0 && (
          <EmptyState
            testId="inventory-empty"
            title={t('routes.sampleInventory.emptyTitle')}
            description={t('routes.sampleInventory.emptyDescription')}
          />
        )}
        {listItems.length > 0 && (
          <ul className="sample-inventory-list" data-testid="inventory-sample-list">
            {listItems.map((item) => (
              <li key={item.sample_id}>
                <button
                  type="button"
                  className="sample-inventory-list__item"
                  aria-pressed={item.sample_id === sampleId}
                  data-testid={`inventory-sample-${item.sample_number ?? item.sample_id}`}
                  onClick={() => updateSearch({ sample: item.sample_id })}
                >
                  <strong>{item.sample_number ?? item.sample_id}</strong>
                  <span>{item.assigned_team ?? t('routes.sampleInventory.noTeam')}</span>
                  <span data-status={item.status}>{item.status}</span>
                  {/* 「지금 우리가 갖고 있나」 — 이 질문에 답할 수 없던 것이
                      custody 사건 표를 만든 이유다. */}
                  <span
                    data-custody-state={item.custody_state ?? 'unknown'}
                    data-testid={`inventory-custody-${item.sample_number ?? item.sample_id}`}
                  >
                    {custodyStateLabel(t, item.custody_state)}
                  </span>
                  <span>{t('routes.sampleInventory.version', { version: item.row_version })}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {inventory.hasNextPage && (
          <Button
            type="button"
            variant="secondary"
            disabled={inventory.isFetchingNextPage}
            loading={inventory.isFetchingNextPage}
            onClick={() => void inventory.fetchNextPage()}
            data-testid="inventory-load-more"
          >
            {inventory.isFetchingNextPage
              ? t('routes.sampleInventory.loadingMore')
              : t('routes.sampleInventory.loadMore')}
          </Button>
        )}
      </section>

      {isValidProjectId(projectId) && !historical && canWrite && creating && (
        <SampleEditor
          projectId={projectId}
          readOnly={false}
          onSaved={(updated) => {
            updateSearch({ create: null });
            void invalidateSample(updated);
          }}
        />
      )}

      {isValidProjectId(projectId) && sampleId !== '' && selected.isPending && (
        <BlockSkeleton lines={10} testId="sample-detail-loading" />
      )}
      {isValidProjectId(projectId) && sampleId !== '' && selected.isError && (
        <ErrorState
          testId="sample-detail-error"
          message={describeApiError(selected.error, 'platform', {
            default: t('routes.sampleInventory.detail.loadFailed'),
          })}
        />
      )}
      {isValidProjectId(projectId) && editorSample !== undefined && (
        <>
          <SampleEditor
            projectId={projectId}
            sample={editorSample}
            readOnly={historical || !canWrite}
            onSaved={(updated) => void invalidateSample(updated)}
            onConflict={() => void selected.refetch()}
          />
          {!historical && (
            <SampleStatusActions
              sample={editorSample}
              canWrite={canWrite}
              canHardDelete={canHardDelete}
              statusMutation={statusMutation}
              deleteMutation={deleteMutation}
              hardDeleteMutation={hardDeleteMutation}
            />
          )}
          {/* PM 축과 시험 실무자 축을 나란히 둔다. 아래 SampleHistory 는 이 둘이
              아니라 감사 리비전(sample_inventory_revisions)이다 — 이름이 비슷하니
              읽는 사람이 헷갈리지 않도록 순서로 구분해 둔다. */}
          {!historical && (
            <SampleCustodyPanel
              projectId={projectId}
              sampleId={editorSample.sample_id}
              canWrite={canWrite}
            />
          )}
          <SampleIntakeHistory projectId={projectId} sampleId={editorSample.sample_id} />
          <SampleHistory projectId={projectId} sampleId={editorSample.sample_id} />
        </>
      )}

      {isValidProjectId(projectId) && (
        <SampleExportActions
          projectId={projectId}
          {...(team === undefined ? {} : { team })}
          status={status ?? 'active'}
          {...(asOf === undefined ? {} : { asOf })}
          includeDeleted={includeDeleted || status === 'all'}
        />
      )}

      <aside className="inventory-next" aria-labelledby="inventory-next-heading">
        <SectionBand
          title={t('routes.sampleInventory.nextSection')}
          titleId="inventory-next-heading"
        />
        <p className="inventory-next__state">
          {isValidProjectId(projectId)
            ? t('routes.sampleInventory.selectedProject', { project: projectId })
            : t('routes.sampleInventory.noProjectSelected')}
        </p>
        <div className="inventory-next__actions">
          <Link to={projectScopedHref(ROUTE_PATHS.projects, projectId)}>
            {t('routes.sampleInventory.nextProjects')}
          </Link>
          <Link to={projectScopedHref(ROUTE_PATHS.testPlans, projectId)}>
            {t('routes.sampleInventory.nextTestPlans')}
          </Link>
          <Link to={projectScopedHref(ROUTE_PATHS.membership, projectId)}>
            {t('routes.sampleInventory.nextMembership')}
          </Link>
        </div>
      </aside>
    </div>
  );
}

interface SampleStatusActionsProps {
  readonly sample: SampleInventoryItem;
  readonly canWrite: boolean;
  readonly canHardDelete: boolean;
  readonly statusMutation: ReturnType<
    typeof useMutation<
      SampleInventoryItem,
      Error,
      { nextStatus: 'active' | 'deleted'; expectedVersion: number }
    >
  >;
  readonly deleteMutation: ReturnType<typeof useMutation<SampleInventoryItem, Error, number>>;
  readonly hardDeleteMutation: ReturnType<
    typeof useMutation<{ hard_deleted: true; sample_id: string }, Error, void>
  >;
}

function SampleStatusActions({
  sample,
  canWrite,
  canHardDelete,
  statusMutation,
  deleteMutation,
  hardDeleteMutation,
}: SampleStatusActionsProps): JSX.Element {
  const { t } = useT();
  const busy = statusMutation.isPending || deleteMutation.isPending || hardDeleteMutation.isPending;
  const actionError = statusMutation.error ?? deleteMutation.error ?? hardDeleteMutation.error;
  return (
    <section className="sample-status-actions" aria-labelledby="sample-status-heading">
      <SectionBand
        title={t('routes.sampleInventory.status.title')}
        titleId="sample-status-heading"
      />
      {canWrite && (
        <div className="sample-status-actions__buttons">
          {sample.status === 'active' ? (
            <Button
              type="button"
              variant="danger"
              disabled={busy}
              onClick={() => deleteMutation.mutate(sample.row_version)}
              data-testid="sample-soft-delete"
            >
              {t('routes.sampleInventory.status.delete')}
            </Button>
          ) : (
            <Button
              type="button"
              disabled={busy}
              onClick={() =>
                statusMutation.mutate({ nextStatus: 'active', expectedVersion: sample.row_version })
              }
              data-testid="sample-restore"
            >
              {t('routes.sampleInventory.status.restore')}
            </Button>
          )}
        </div>
      )}
      {canHardDelete && (
        <Button
          type="button"
          variant="danger"
          disabled={busy}
          onClick={() => {
            if (window.confirm(t('routes.sampleInventory.status.hardDeleteConfirm'))) {
              hardDeleteMutation.mutate();
            }
          }}
          data-testid="sample-hard-delete"
        >
          {t('routes.sampleInventory.status.hardDelete')}
        </Button>
      )}
      {actionError && (
        <ErrorState
          testId="sample-status-error"
          message={describeApiError(actionError, 'platform', {
            default: t('routes.sampleInventory.status.failed'),
            conflict: t('routes.sampleInventory.status.conflict'),
          })}
        />
      )}
    </section>
  );
}

export default InventoryRoute;
