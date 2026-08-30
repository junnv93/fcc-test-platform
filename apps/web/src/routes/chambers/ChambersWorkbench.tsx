import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

import { useChamberProgressStream } from '@/api/chamber-progress-stream';
import { PERMISSION_PLATFORM_ADMIN, PERMISSION_PLATFORM_CLAIM } from '@/api/permissions';
import { fetchChambers } from '@/api/platform-client';
import { queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { RequirePermission } from '@/auth/route-guard';
import { hasPermission } from '@/auth/session';
import { useT } from '@/i18n';
import { summarizeChamberFleet } from '@/shared/chamber-fleet';
import {
  formatHeartbeatAge,
  heartbeatAgeSeconds,
  useClockTick,
  type ServerClockAnchor,
} from '@/shared/heartbeat-age';
import { isValidProjectId } from '@/shared/project-id';
import {
  chamberStatusKind,
  Card,
  DataTable,
  describeApiError,
  EmptyState,
  ErrorState,
  SectionBand,
  StatusBadge,
  WorkbenchLayout,
} from '@/ui';

import { ChamberAdminPanel } from './ChamberAdminPanel';
import { ChamberFleetSummaryStrip } from './ChamberFleetSummaryStrip';
import { ChamberRunOverview } from './ChamberRunOverview';
import { EquipmentConfigPanel } from './EquipmentConfigPanel';
import { MeasurementStarter } from './MeasurementStarter';
import { ChamberNextActionLinks } from './next-actions';
import {
  chamberModeVerdictLabel,
  chamberStatusLabel,
  chamberUnavailableReasonLabel,
  isChamberModeBreach,
} from './status';
import { orDash } from './util';

export function ChambersWorkbench(): JSX.Element {
  const { t } = useT();
  const [searchParams] = useSearchParams();
  const projectId = (searchParams.get('project') ?? '').trim();
  // B4 central progress relay: stream heartbeat-carried progress into the same
  // query cache keys used by the polling fallback. M3 — the hook's connection
  // status used to be discarded here, so a relay that had given up (policy
  // rejection / retry budget spent) looked identical to a healthy one and the
  // screen silently degraded to poll-only with no symptom.
  const { status: streamStatus } = useChamberProgressStream();
  const chambers = useQuery({
    queryKey: queryKeys.chambers.list(),
    queryFn: fetchChambers,
    // M4 — availability is a supervision view: it must not sit frozen on the
    // snapshot taken when the page opened. The previous tier (IMPORTANT) never
    // polls, which is what made a chamber that went offline keep reading "idle"
    // on a wall monitor until somebody happened to refocus the tab. The cadence
    // itself is owned by query-config.ts — this route names a tier, not a number.
    ...REFETCH_STRATEGIES.MONITORED,
  });

  const rows = useMemo(() => chambers.data?.items ?? [], [chambers.data]);
  const fleet = useMemo(() => summarizeChamberFleet(rows), [rows]);
  const canAdmin = hasPermission(PERMISSION_PLATFORM_ADMIN);

  // M4 — ONE ticking clock for the whole route, read by every heartbeat-age cell
  // below (a hook per row would put N intervals on a fleet table to produce the
  // same instant). The anchor pins the payload's server instant to the client
  // reading taken when that payload landed, so the ages advance with the wall
  // clock without trusting the workstation's absolute clock.
  const nowMs = useClockTick();
  const clockAnchor = useMemo<ServerClockAnchor>(
    () => ({
      serverTime: chambers.data?.server_time ?? '',
      observedAtMs: chambers.dataUpdatedAt,
    }),
    [chambers.data?.server_time, chambers.dataUpdatedAt],
  );

  return (
    <WorkbenchLayout
      className="chambers-workbench"
      mainLabel={t('routes.chambers.sectionAvailability')}
      railLabel={t('routes.chambers.nextSection')}
      testId="chambers-workbench"
      main={
        <div className="chambers-workbench__main">
          {chambers.isSuccess && <ChamberFleetSummaryStrip summary={fleet} />}
          {chambers.isSuccess && (
            <ChamberRunOverview running={fleet.running} streamStatus={streamStatus} />
          )}

          <RequirePermission permission={PERMISSION_PLATFORM_CLAIM}>
            <section
              className="chambers-workbench-panel chambers-workbench-action-panel"
              aria-labelledby="chambers-start-heading"
              data-testid="chambers-start-panel"
            >
              <MeasurementStarter chambers={rows} />
            </section>
          </RequirePermission>

          <Card
            as="section"
            className="chambers-workbench-panel"
            aria-labelledby="chambers-availability-heading"
          >
            <SectionBand
              title={t('routes.chambers.sectionAvailability')}
              titleId="chambers-availability-heading"
            />
            {chambers.isPending && <p aria-busy="true">{t('routes.chambers.loading')}</p>}
            {chambers.isError && (
              <ErrorState
                testId="chambers-error"
                message={describeApiError(chambers.error, 'platform', {
                  forbidden: t('routes.chambers.readForbidden'),
                  network: t('routes.chambers.readNetwork'),
                  default: t('routes.chambers.readFailed'),
                })}
              />
            )}
            {chambers.isSuccess && rows.length === 0 && (
              <EmptyState
                testId="chambers-empty"
                title={t('routes.chambers.emptyTitle')}
                description={t('routes.chambers.emptyDescription')}
              />
            )}
            {chambers.isSuccess && rows.length > 0 && (
              <DataTable
                testId="chambers-table"
                caption={t('routes.chambers.tableCaption')}
                head={
                  <thead>
                    <tr>
                      <th scope="col">{t('routes.chambers.colName')}</th>
                      <th scope="col">{t('routes.chambers.colStatus')}</th>
                      <th scope="col">{t('routes.chambers.colLastHeartbeat')}</th>
                      <th scope="col">{t('routes.chambers.colSession')}</th>
                      {/* 챔버 모드 축 (2026-08-16) — 승인(중앙 선언)과 관측(heartbeat
                        파생)의 **대조 결과**. 서버가 판정한 토큰을 읽기만 한다:
                        여기서 다시 계산하면 게이트가 보는 것과 화면이 보여주는 것이
                        갈라지고, 갈라지는 쪽은 언제나 화면이다. */}
                      <th scope="col">{t('routes.chambers.colMode')}</th>
                    </tr>
                  </thead>
                }
                body={
                  <tbody>
                    {rows.map((chamber) => (
                      <tr key={chamber.chamber_id} data-testid="chambers-row">
                        <th scope="row">{orDash(chamber.name)}</th>
                        <td>
                          <StatusBadge
                            status={chamberStatusKind(chamber.status)}
                            label={chamberStatusLabel(t, chamber.status)}
                            testId="chambers-status"
                          />
                          {/* M2 — WHY it is unavailable, next to the badge that
                            says THAT it is. The reason used to render only in
                            the platform:admin panel, so the operator picking a
                            chamber (who has read, not admin) saw "offline" with
                            no way to tell a disabled chamber from a silent one. */}
                          {chamber.unavailable_reason !== null &&
                            chamber.unavailable_reason !== undefined && (
                              <small data-testid="chambers-unavailable-reason">
                                {chamberUnavailableReasonLabel(t, chamber.unavailable_reason)}
                              </small>
                            )}
                        </td>
                        {/* M4 — the elapsed age, not the raw instant: "how long
                          since we heard from it" is the question this column
                          answers, and it is the one an operator can act on. The
                          verbatim timestamp stays reachable as the cell title
                          for forensics. */}
                        <td
                          data-testid="chambers-heartbeat-age"
                          title={orDash(chamber.last_heartbeat_at)}
                        >
                          {formatHeartbeatAge(
                            heartbeatAgeSeconds(clockAnchor, chamber.last_heartbeat_at, nowMs),
                          )}
                        </td>
                        <td>{orDash(chamber.session_id)}</td>
                        <td data-testid="chambers-mode-verdict">
                          {isChamberModeBreach(chamber.mode_verdict) ? (
                            <StatusBadge
                              status="fail"
                              label={chamberModeVerdictLabel(t, chamber.mode_verdict)}
                              testId="chambers-mode-breach"
                            />
                          ) : (
                            chamberModeVerdictLabel(t, chamber.mode_verdict)
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                }
              />
            )}
          </Card>

          {canAdmin && chambers.isSuccess && (
            <ChamberAdminPanel chambers={rows} clockAnchor={clockAnchor} nowMs={nowMs} />
          )}

          {/* SPLIT-6 ③ — 계측기 연결 설정. platform:read 로 보이고 쓰기는 패널이
              platform:equipment-write 로 스스로 게이트한다. 관리자 패널과 달리
              canAdmin 에 묶지 않는 이유는 이 축의 행위자가 시험원이기 때문이다. */}
          {chambers.isSuccess && <EquipmentConfigPanel chambers={rows} />}
        </div>
      }
      rail={
        <div
          className="chambers-workbench__rail"
          aria-labelledby="chambers-next-heading"
          data-testid="chambers-next-actions"
        >
          <ChambersNextActions projectId={projectId} />
        </div>
      }
    />
  );
}

function ChambersNextActions({ projectId }: { readonly projectId: string }): JSX.Element {
  const { t } = useT();
  return (
    <section className="chambers-next">
      <SectionBand title={t('routes.chambers.nextSection')} titleId="chambers-next-heading" />
      <p className="chambers-next__state" data-testid="chambers-next-state">
        {isValidProjectId(projectId)
          ? t('routes.chambers.selectedProject', { project: projectId })
          : t('routes.chambers.noProjectSelected')}
      </p>
      <div className="chambers-next__actions">
        <ChamberNextActionLinks
          keys={['sessions', 'jobs', 'reports', 'diagnostics']}
          projectId={projectId}
        />
      </div>
      <p className="section-hint">{t('routes.chambers.nextHint')}</p>
    </section>
  );
}
