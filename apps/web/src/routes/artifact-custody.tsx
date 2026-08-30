import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { fetchArtifactCustodySnapshot, fetchProjectArtifactCustody } from '@/api/platform-client';
import { queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { useT } from '@/i18n';
import { isValidProjectId } from '@/shared/project-id';
import {
  BlockSkeleton,
  DataTable,
  describeApiError,
  EmptyState,
  ErrorState,
  MetricStrip,
  PageHeader,
  SectionBand,
  StatusBadge,
} from '@/ui';

import type { ArtifactCustodySessionSummary, ProjectArtifactCustody } from '@/api/platform-client';
import type { DataTableColumn, StatusKind } from '@/ui';

/**
 * 플롯 보관 현황 — 심사 증거가 보관소에 실재하는가 (plot-custody ①, 2026-08-09).
 *
 * 플롯은 FCC 심사 증거이고 **원본이 권위**다(심사관이 보는 것은 회사 파일서버다).
 * 중앙은 그 보관소를 열 수 없으므로 **판정하지 않는다** — 챔버 노드가 직접 디스크를
 * 열어 판정하고, 중앙은 그것을 받아 보관한다. 이 화면은 그 저장된 판정을 읽는다.
 *
 * SSOT: 데이터 = `GET /platform/projects/{id}/artifact-custody`(platform:read, typed
 * generated client). **파생값은 전부 서버가 준다** — 상태 토큰, 집계, 차단 여부
 * (`is_blocking`), 신선도(`oldest_observed_at`). 프론트가 상태 토큰으로 차단 여부를
 * 다시 계산하면 규칙이 두 언어로 쪼개지고, 갈라진 쪽이 화면이면 **발행 게이트가 막는
 * 세션이 초록으로 보인다**(Derived-Value No-Client-Recompute SSOT).
 *
 * 두 가지를 화면이 반드시 말해야 한다:
 *
 * 1. **미귀속 세션 수** — 조용히 빼면 "이 프로젝트는 이상 없음"이 "이 프로젝트에서
 *    우리가 볼 수 있는 것은 이상 없음"과 구분되지 않는다.
 * 2. **스냅샷이 없는 세션 수** — 미귀속 snapshot과 합치지 않는다. 보고 자체가 없으면
 *    서버 rollup은 기존 blocking이 없을 때 UNKNOWN을 내려준다.
 * 3. **가장 낡은 관측 시각** — 초록이어도 근거가 3주 전이면 "지금 괜찮다"가 아니다.
 *
 * 인가: platform:read. RequireAuth 하위 + 백엔드 403 → ErrorState 가 처리.
 */

/** 서버 상태 토큰 → 배지 종류. 라벨/판정이 아니라 **표시**만의 매핑이다. */
const BADGE_BY_STATUS: Readonly<Record<string, StatusKind>> = {
  verified: 'pass',
  missing: 'missing',
  diverged: 'fail',
  unknown: 'stale',
};

function statusBadgeKind(status: string): StatusKind {
  return BADGE_BY_STATUS[status] ?? 'stale';
}

function ArtifactCustodyRoute(): JSX.Element {
  const { t } = useT();
  const [searchParams] = useSearchParams();
  const projectId = (searchParams.get('project') ?? '').trim();
  const hasProject = isValidProjectId(projectId);
  const [openSnapshotId, setOpenSnapshotId] = useState<string | null>(null);

  const custody = useQuery({
    queryKey: queryKeys.project.artifactCustody(projectId),
    queryFn: () => fetchProjectArtifactCustody(projectId),
    enabled: hasProject,
    ...REFETCH_STRATEGIES.NORMAL,
  });

  if (!hasProject) {
    return (
      <div className="route route-artifact-custody">
        <PageHeader
          title={t('routes.artifactCustody.title')}
          description={t('routes.artifactCustody.description')}
        />
        <EmptyState
          testId="artifact-custody-no-project"
          title={t('routes.artifactCustody.noProjectTitle')}
          description={t('routes.artifactCustody.noProjectBody')}
          action={
            <Link to="/my-projects" data-testid="artifact-custody-pick-project">
              {t('routes.artifactCustody.pickProject')}
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className="route route-artifact-custody" data-testid="artifact-custody-route">
      <PageHeader
        title={t('routes.artifactCustody.title')}
        description={t('routes.artifactCustody.description')}
      />

      {custody.isError ? (
        <ErrorState
          testId="artifact-custody-error"
          message={describeApiError(custody.error, 'platform')}
        />
      ) : custody.isPending ? (
        // Loading and empty are different facts and must not share a surface
        // (§M8.1) — a skeleton, not an EmptyState, while the request is in flight.
        <BlockSkeleton lines={4} testId="artifact-custody-loading" />
      ) : custody.data.session_count === 0 &&
        custody.data.unresolved_session_count === 0 &&
        custody.data.missing_snapshot_session_count === 0 ? (
        <EmptyState
          testId="artifact-custody-empty"
          title={t('routes.artifactCustody.emptyTitle')}
          description={t('routes.artifactCustody.emptyBody')}
        />
      ) : (
        <CustodyBody
          data={custody.data}
          projectId={projectId}
          openSnapshotId={openSnapshotId}
          onOpen={setOpenSnapshotId}
        />
      )}
    </div>
  );
}

interface CustodyBodyProps {
  readonly data: ProjectArtifactCustody;
  readonly projectId: string;
  readonly openSnapshotId: string | null;
  readonly onOpen: (snapshotId: string | null) => void;
}

function CustodyBody({ data, projectId, openSnapshotId, onOpen }: CustodyBodyProps): JSX.Element {
  const { t } = useT();
  const counts = data.counts ?? {};

  const metricItems = [
    {
      key: 'sessions',
      label: t('routes.artifactCustody.sessionCount'),
      value: String(data.session_count),
      valueTestId: 'artifact-custody-session-count',
    },
    {
      key: 'blocking',
      label: t('routes.artifactCustody.blockingSessions'),
      value: String(data.blocking_session_count),
      valueTestId: 'artifact-custody-blocking-count',
    },
    {
      key: 'unresolved',
      label: t('routes.artifactCustody.unresolvedSessions'),
      value: String(data.unresolved_session_count),
      valueTestId: 'artifact-custody-unresolved-count',
    },
    {
      key: 'missing-snapshot',
      label: t('routes.artifactCustody.missingSnapshotSessions'),
      value: String(data.missing_snapshot_session_count),
      valueTestId: 'artifact-custody-missing-snapshot-count',
    },
    {
      key: 'oldest',
      // 초록이어도 근거가 오래되면 "지금 괜찮다"가 아니다.
      label: t('routes.artifactCustody.oldestObserved'),
      value: data.oldest_observed_at ?? '—',
      valueTestId: 'artifact-custody-oldest-observed',
    },
  ];

  const columns: readonly DataTableColumn<ArtifactCustodySessionSummary>[] = [
    {
      key: 'session',
      header: t('routes.artifactCustody.session'),
      priority: 'primary',
      rowHeader: true,
      cell: (row) => {
        // `??` 만으로는 빈 문자열 라벨이 그대로 렌더된다 — 라벨이 없는 세션은
        // 노드-로컬 id 로 식별돼야 한다.
        const label = (row.session_label ?? '').trim();
        return label.length > 0 ? label : row.provider_session_id;
      },
    },
    {
      key: 'chamber',
      header: t('routes.artifactCustody.chamber'),
      priority: 'secondary',
      cell: (row) => row.chamber_id,
    },
    {
      key: 'status',
      header: t('routes.artifactCustody.statusLabel'),
      priority: 'primary',
      className: 'data-cell-status',
      cell: (row) => (
        <StatusBadge
          status={statusBadgeKind(row.status)}
          label={t(`routes.artifactCustody.${row.status}`)}
          testId={`artifact-custody-status-${row.snapshot_id}`}
        />
      ),
    },
    {
      key: 'blocking',
      header: t('routes.artifactCustody.blockingBadge'),
      priority: 'secondary',
      // ⚠️ `is_blocking` 은 **서버가 판정한 값**이다. 여기서 상태 토큰으로 다시
      // 계산하면 발행 게이트와 화면이 갈라질 수 있다.
      cell: (row) =>
        row.is_blocking
          ? t('routes.artifactCustody.blockingBadge')
          : t('routes.artifactCustody.okBadge'),
    },
    {
      key: 'observed',
      header: t('routes.artifactCustody.observedAt'),
      priority: 'detail',
      cell: (row) => row.observed_at,
    },
    {
      key: 'detail',
      header: t('routes.artifactCustody.viewDetail'),
      priority: 'detail',
      cell: (row) => (
        <button
          type="button"
          onClick={() => onOpen(row.snapshot_id === openSnapshotId ? null : row.snapshot_id)}
          data-testid={`artifact-custody-detail-${row.snapshot_id}`}
        >
          {row.snapshot_id === openSnapshotId
            ? t('routes.artifactCustody.close')
            : t('routes.artifactCustody.viewDetail')}
        </button>
      ),
    },
  ];

  return (
    <>
      <section aria-labelledby="artifact-custody-summary-heading">
        <SectionBand
          title={t('routes.artifactCustody.summaryTitle')}
          titleId="artifact-custody-summary-heading"
        />
        <p className="section-hint" data-testid="artifact-custody-project-status">
          <StatusBadge
            status={statusBadgeKind(data.status)}
            label={t(`routes.artifactCustody.${data.status}`)}
            size="lg"
          />
        </p>
        <MetricStrip items={metricItems} ariaLabel={t('routes.artifactCustody.summaryTitle')} />
        <dl className="artifact-custody-counts" data-testid="artifact-custody-counts">
          {(['verified', 'missing', 'diverged', 'unknown'] as const).map((token) => (
            <div key={token}>
              <dt>{t(`routes.artifactCustody.${token}`)}</dt>
              <dd data-testid={`artifact-custody-count-${token}`}>{counts[token] ?? 0}</dd>
            </div>
          ))}
        </dl>
        {data.unresolved_session_count > 0 && (
          <p className="section-hint" role="note" data-testid="artifact-custody-unresolved-hint">
            {t('routes.artifactCustody.unresolvedHint')}
          </p>
        )}
        {data.missing_snapshot_session_count > 0 && (
          <p
            className="section-hint"
            role="note"
            data-testid="artifact-custody-missing-snapshot-hint"
          >
            {t('routes.artifactCustody.missingSnapshotHint')}
          </p>
        )}
        <p className="section-hint" role="note">
          {t('routes.artifactCustody.blockingHelp')}
        </p>
        <p className="section-hint" role="note">
          {t('routes.artifactCustody.stalenessHelp')}
        </p>
      </section>

      <section aria-labelledby="artifact-custody-sessions-heading">
        <SectionBand
          title={t('routes.artifactCustody.sessionsTitle')}
          titleId="artifact-custody-sessions-heading"
        />
        <DataTable
          columns={columns}
          rows={data.sessions}
          rowKey={(row) => row.snapshot_id}
          testId="artifact-custody-sessions"
          caption={t('routes.artifactCustody.sessionsTitle')}
        />
      </section>

      {openSnapshotId !== null && (
        <SnapshotDetail projectId={projectId} snapshotId={openSnapshotId} />
      )}
    </>
  );
}

function SnapshotDetail({
  projectId,
  snapshotId,
}: {
  readonly projectId: string;
  readonly snapshotId: string;
}): JSX.Element {
  const { t } = useT();
  const detail = useQuery({
    queryKey: queryKeys.project.artifactCustodySnapshot(projectId, snapshotId),
    queryFn: () => fetchArtifactCustodySnapshot(projectId, snapshotId),
    ...REFETCH_STRATEGIES.NORMAL,
  });

  return (
    <section aria-labelledby="artifact-custody-detail-heading">
      <SectionBand
        title={t('routes.artifactCustody.detailTitle')}
        titleId="artifact-custody-detail-heading"
      />
      {detail.isError ? (
        <ErrorState
          testId="artifact-custody-detail-error"
          message={describeApiError(detail.error, 'platform')}
        />
      ) : detail.isPending ? (
        <BlockSkeleton lines={3} testId="artifact-custody-detail-loading" />
      ) : (
        <>
          <p className="section-hint" data-testid="artifact-custody-detail-roots">
            {t('routes.artifactCustody.roots')}:{' '}
            {detail.data.roots && detail.data.roots.length > 0
              ? detail.data.roots.join(' · ')
              : t('routes.artifactCustody.rootsEmpty')}
          </p>
          <p className="section-hint" role="note">
            {t('routes.artifactCustody.detailHint')}
          </p>
          {detail.data.findings.length === 0 ? (
            <EmptyState
              testId="artifact-custody-detail-empty"
              title={t('routes.artifactCustody.detailEmpty')}
              description={t('routes.artifactCustody.detailHint')}
            />
          ) : (
            <ul data-testid="artifact-custody-findings">
              {detail.data.findings.map((finding) => (
                <li key={finding.relative_path} data-status={finding.status}>
                  <StatusBadge
                    status={statusBadgeKind(finding.status)}
                    label={t(`routes.artifactCustody.${finding.status}`)}
                  />{' '}
                  <code>{finding.relative_path}</code>
                  {finding.reason ? <> — {finding.reason}</> : null}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

export default ArtifactCustodyRoute;
