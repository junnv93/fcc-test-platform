import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { PERMISSION_SESSION_CONTROL, PERMISSION_SESSION_EVENTS } from '@/api/permissions';
import { queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { fetchSessionProgress, stopSession } from '@/api/session-client';
import {
  createSessionEventStream,
  type SessionEvent,
  type SessionStreamStatus,
} from '@/api/session-events';
import { RequirePermission } from '@/auth/route-guard';
import { getRuntimeConfig } from '@/config/runtime';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { formatPercent } from '@/shared/percent-display';
import {
  BlockSkeleton,
  Button,
  describeApiError,
  ErrorState,
  MetricStrip,
  PageHeader,
  RunProgress,
  SectionBand,
  StatusBadge,
  streamStatusKind,
  streamStatusLabelToken,
  type MetricStripItem,
} from '@/ui';

/**
 * Remote measurement control — Session API (FE-P5, 2026-05-26).
 *
 * 사용자 결정상 웹은 "읽기 + 원격 제어"다. 측정 장비는 엔지니어 옆 로컬 PC 에 있고
 * 그 PC 가 Session API(HTTP `/session/stop|progress|is-running|info` +
 * WS `/session/events`)를 노출한다. 신규 측정 시작은 중앙 chamber workflow가
 * project/sample과 서버 생성 snapshot을 함께 전달하며, 이 화면은 진행을 관찰하고
 * 중지하는 표면으로 남는다.
 *
 * `/control`은 더 이상 browser-direct start를 제공하지 않는다. 이 경로를 남겨
 * 두면 중앙 project/sample 검증과 immutable sample snapshot을 우회할 수 있기
 * 때문이다.
 *
 * ⚠️ 모든 session 호출은 `@/api/session-client` 헬퍼를 지난다. 이 파일이 직접
 * `Object.assign(new Error(…), { status })` 로 실패를 만들던 동안, 이 표면이
 * 발행하는 session-scoped `ErrorCode` 6종이 **한 번도 화면에 도달하지 않았다**.
 *
 * RBAC: 제어(stop)는 `session:control`, 실시간 이벤트 구독은
 * `session:events` 권한으로 각각 게이트한다(클라이언트측 가드 — 서버는
 * 배포 계층 trusted-header 로 재차 강제). 권한/단위 vocabulary 는 백엔드
 * `SESSION_PERMISSION_*` SSOT(`src/application/session/api_contracts.py`)를
 * 미러한다(프론트 enum 박기 금지 — 단일 지점 상수).
 */

/** 5-state failure taxonomy for a control operation outcome. */
export type ControlOutcome = 'idle' | 'pending' | 'forbidden' | 'unreachable' | 'error';

/**
 * Translation KEYS (not translated strings) per failure outcome. Storing keys —
 * not `t(...)` results — at module scope is deliberate: a module-load `t()` call
 * would freeze the copy to whatever locale was active at import time, so a later
 * `setLocale('en')` would leave these strings stale (the bug Codex flagged in
 * iter-02). The keys are resolved through the render-time `t()` from `useT()`
 * below, so the copy is live-locale. Sealed by `tests/test_frontend_i18n_parity.py`
 * `TestNoModuleScopeTranslatedString`.
 */
const OUTCOME_COPY_KEYS: Readonly<Record<Exclude<ControlOutcome, 'idle' | 'pending'>, string>> = {
  forbidden: 'routes.control.outcomeForbidden',
  unreachable: 'routes.control.outcomeUnreachable',
  error: 'routes.control.outcomeError',
};

/** Map an HTTP status to the failure taxonomy. 403 → forbidden, 503/network
 *  → unreachable (agent-side Session API down), else → error. */
export function classifyControlOutcome(status: number | undefined): ControlOutcome {
  if (status === 403) return 'forbidden';
  // 503 (bridge/session not ready) or no status (fetch/network failure —
  // agent unreachable) both degrade gracefully to "unreachable".
  if (status === 503 || status === undefined) return 'unreachable';
  return 'error';
}

/**
 * What the SERVER says about the session, in three states (M6, 2026-07-28).
 *
 * The screen used to hold `progress.data?.is_running ?? false`, which folds two
 * genuinely different situations into one:
 *
 *   - the server reported `is_running: false` — the session is idle, and
 *   - the server has not reported at all (first load, or the read failed) —
 *     nothing is known.
 *
 * Collapsing them is not a nuance: an emergency-stop affordance derived from
 * that boolean stays locked during exactly the window in which the operator
 * cannot see what the node is doing, and the screen offers no account of why.
 * The `unknown` arm is what makes "we do not know" expressible.
 */
export type SessionRunState = 'running' | 'idle' | 'unknown';

/**
 * Derive the run state from the progress read.
 *
 * Pure and exported so the three-way decision is unit-testable and lives in one
 * place: `is_running` is authoritative WHEN PRESENT (which is what lets a run
 * started elsewhere be observed here), and absence is reported as absence.
 *
 * `hasServerReport` is "has the server EVER reported", not "is the latest poll
 * healthy". The distinction matters in exactly the case this milestone exists
 * for: a live run whose next poll fails would, under the stricter predicate,
 * flip to `unknown` and re-lock the emergency stop — reintroducing the lock at
 * the moment the operator is most likely to need it. A retained snapshot is
 * still a server report; its freshness is a separate axis, and the surface says
 * so through the error state rendered alongside.
 */
export function deriveRunState(
  isRunning: boolean | undefined,
  hasServerReport: boolean,
): SessionRunState {
  if (!hasServerReport || isRunning === undefined) return 'unknown';
  return isRunning ? 'running' : 'idle';
}

/** i18n key explaining why stop is unavailable, per non-running state. The two
 *  arms MUST NOT share copy — telling an operator "the session is idle" when the
 *  truth is "we cannot reach the node" is the collapse this milestone undoes. */
const STOP_UNAVAILABLE_COPY_KEYS: Readonly<Record<Exclude<SessionRunState, 'running'>, string>> = {
  idle: 'routes.control.stopUnavailableIdle',
  unknown: 'routes.control.stopUnavailableUnknown',
};

/** Subscribe to the live `/session/events` WS stream while `enabled`. Bounds
 *  the buffer so a long-running session does not grow memory unbounded. */
const MAX_BUFFERED_EVENTS = 200;

function useSessionEventStream(enabled: boolean): {
  events: readonly SessionEvent[];
  status: SessionStreamStatus;
} {
  const [events, setEvents] = useState<readonly SessionEvent[]>([]);
  const [status, setStatus] = useState<SessionStreamStatus>('connecting');

  useEffect(() => {
    if (!enabled) return undefined;
    // Reset buffer + status when (re)enabling so a new run does not inherit the
    // previous run's events or a stale 'closed' status (P0-1/P2-3 fix).
    setEvents([]);
    setStatus('connecting');
    const handle = createSessionEventStream({
      onEvent: (event) =>
        setEvents((prev) => {
          const next = prev.length >= MAX_BUFFERED_EVENTS ? prev.slice(1) : prev.slice();
          next.push(event);
          return next;
        }),
      onStatus: setStatus,
    });
    return () => handle.close();
  }, [enabled]);

  return { events, status };
}

export function ControlRoute(): JSX.Element {
  const { t } = useT();
  // Remote control drives the single-chamber-node Session API. A topology that
  // does not serve `/session` (the central hub — runtime-config
  // `sessionApiEnabled: false`) must not mount the panel at all: its queries
  // would hit the gateway and 404. Render an unavailable notice instead so no
  // session call is issued. Sealed by tests/control.test.tsx.
  const sessionApiEnabled = getRuntimeConfig().sessionApiEnabled;
  return (
    <section className="control" aria-labelledby="control-heading">
      <PageHeader
        title={t('routes.control.pageTitle')}
        titleId="control-heading"
        description={t('routes.control.pageDescription')}
      />
      {sessionApiEnabled ? (
        <RequirePermission permission={PERMISSION_SESSION_CONTROL}>
          <ControlWorkbenchOverview />
          <ControlPanel />
        </RequirePermission>
      ) : (
        <p data-testid="control-unavailable">{t('routes.control.unavailable')}</p>
      )}
    </section>
  );
}

function ControlPanel(): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();

  const progress = useQuery({
    queryKey: queryKeys.session.progress(),
    queryFn: fetchSessionProgress,
    // M6 — the cadence steps DOWN when idle; it never stops. The previous
    // `: false` parked the poll permanently on the first not-running snapshot,
    // which is why a run started anywhere else was never seen here. MONITORED is
    // the base (so `refetchOnWindowFocus` and `refetchIntervalInBackground:
    // false` come with it — a parked tab does not poll), and an active run is
    // tracked at the CRITICAL cadence as before. Both numbers stay in the
    // strategy SSOT; this route names tiers, not milliseconds.
    ...REFETCH_STRATEGIES.MONITORED,
    refetchInterval: (query) =>
      query.state.data?.is_running
        ? REFETCH_STRATEGIES.CRITICAL.refetchInterval
        : REFETCH_STRATEGIES.MONITORED.refetchInterval,
  });

  const invalidateProgress = (): void => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.session.progress() });
  };

  const stopMutation = useMutation<void, ApiError>({
    mutationFn: stopSession,
    onSettled: invalidateProgress,
  });

  // Server-reported, three-state. The "has reported" predicate is the presence
  // of a snapshot, NOT query health: React Query retains the last payload across
  // a failed poll, and that retained report is the best knowledge the screen
  // has. Only a session the server has never described is `unknown`.
  const runState = deriveRunState(progress.data?.is_running, progress.data !== undefined);
  const isRunning = runState === 'running';
  const activeMutation = stopMutation.isPending ? stopMutation : null;
  const lastError = stopMutation.error;
  const outcome: ControlOutcome = activeMutation
    ? 'pending'
    : lastError
      ? classifyControlOutcome(lastError.status)
      : 'idle';

  const progressItems: readonly MetricStripItem[] =
    progress.isSuccess && progress.data
      ? [
          {
            key: 'running',
            label: t('routes.control.metricRunning'),
            value: progress.data.is_running
              ? t('routes.control.metricRunningYes')
              : t('routes.control.metricRunningNo'),
            valueTestId: 'progress-running',
          },
          {
            key: 'completed',
            label: t('routes.control.metricCompleted'),
            value: `${progress.data.completed} / ${progress.data.total}`,
            valueTestId: 'progress-completed',
          },
          {
            key: 'ratio',
            label: t('routes.control.metricRatio'),
            // M7 — same SSOT the RunProgress bar above it uses, so the strip
            // and the bar can never disagree about whether a run is finished.
            value: formatPercent(progress.data.ratio * 100),
            valueTestId: 'progress-ratio',
          },
        ]
      : [];

  return (
    <div className="control-workbench" data-testid="control-workbench">
      <div className="control-workbench__main">
        <section className="control-workbench-panel" aria-labelledby="control-actions-heading">
          <SectionBand
            title={t('routes.control.sectionActions')}
            titleId="control-actions-heading"
          />
          <div className="control-actions">
            <Button
              type="button"
              variant="danger"
              data-testid="control-stop"
              disabled={stopMutation.isPending || !isRunning}
              onClick={() => stopMutation.mutate()}
            >
              {t('routes.control.stopButton')}
            </Button>
          </div>
          {/* M6 — a disabled emergency control must account for itself. "Idle" and
            "we cannot tell" get different sentences: the first says nothing is
            running, the second says this screen does not know, which is the
            operator's cue to check the node directly. */}
          {runState !== 'running' && (
            <p className="section-hint" data-testid="control-stop-reason">
              {t(STOP_UNAVAILABLE_COPY_KEYS[runState])}
            </p>
          )}
          {outcome === 'pending' && (
            // Reserves the line the outcome message lands on, so a completed
            // command does not shove the controls (§M8.2).
            <BlockSkeleton lines={1} testId="control-pending" />
          )}
          {outcome !== 'idle' && outcome !== 'pending' && (
            <ErrorState
              testId={`control-outcome-${outcome}`}
              message={t(OUTCOME_COPY_KEYS[outcome])}
            />
          )}
        </section>

        <section className="control-workbench-panel" aria-labelledby="control-progress-heading">
          <SectionBand
            title={t('routes.control.sectionProgress')}
            titleId="control-progress-heading"
          />
          {progress.isPending && <BlockSkeleton lines={3} testId="control-progress-loading" />}
          {progress.isError && (
            <ErrorState
              testId="progress-error"
              message={describeApiError(progress.error, 'session', {
                forbidden: t('routes.control.progressErrorPrefix', {
                  reason: t(OUTCOME_COPY_KEYS.forbidden),
                }),
                network: t('routes.control.progressErrorPrefix', {
                  reason: t(OUTCOME_COPY_KEYS.unreachable),
                }),
                default: t('routes.control.progressErrorPrefix', {
                  reason: t(OUTCOME_COPY_KEYS.error),
                }),
              })}
            />
          )}
          {progress.isSuccess && progress.data && (
            <div data-testid="progress">
              {/* §M8.7 third loading state. A run lasts tens of seconds to
                minutes; a spinner would say "something is happening" for the
                whole of it. The operator gets completion ratio + the step they
                are on instead. Rendered only while the run is live — an idle
                session keeps the plain metric readout. */}
              {progress.data.is_running && (
                <RunProgress
                  label={t('routes.control.runProgressLabel')}
                  percent={progress.data.ratio * 100}
                  step={t('routes.control.runProgressStep', {
                    completed: progress.data.completed,
                    total: progress.data.total,
                  })}
                />
              )}
              <MetricStrip ariaLabel={t('routes.control.metricStripLabel')} items={progressItems} />
            </div>
          )}
        </section>
      </div>

      <aside
        className="control-workbench__rail"
        aria-labelledby="control-events-heading"
        data-testid="control-events-panel"
      >
        <RequirePermission permission={PERMISSION_SESSION_EVENTS}>
          <LiveEventLog enabled={isRunning} />
        </RequirePermission>
      </aside>
    </div>
  );
}

function ControlWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="control-workbench-overview"
      aria-label={t('routes.control.workbenchNavAria')}
      data-testid="control-workbench-overview"
    >
      <a className="control-workbench-overview__item" href="#control-actions-heading">
        <span className="control-workbench-overview__label">{t('routes.control.stepControl')}</span>
        <span className="control-workbench-overview__detail">
          {t('routes.control.stepControlDetail')}
        </span>
      </a>
      <a className="control-workbench-overview__item" href="#control-progress-heading">
        <span className="control-workbench-overview__label">
          {t('routes.control.stepProgress')}
        </span>
        <span className="control-workbench-overview__detail">
          {t('routes.control.stepProgressDetail')}
        </span>
      </a>
      <a className="control-workbench-overview__item" href="#control-events-heading">
        <span className="control-workbench-overview__label">{t('routes.control.stepEvents')}</span>
        <span className="control-workbench-overview__detail">
          {t('routes.control.stepEventsDetail')}
        </span>
      </a>
    </nav>
  );
}

function LiveEventLog({ enabled }: { readonly enabled: boolean }): JSX.Element {
  const { t } = useT();
  const { events, status } = useSessionEventStream(enabled);
  return (
    <section aria-labelledby="control-events-heading">
      <SectionBand title={t('routes.control.sectionEvents')} titleId="control-events-heading" />
      <p data-testid="events-status">
        {t('routes.control.connectionLabel')}{' '}
        {/* M3 — the label used to be the RAW status token ("reconnecting") on an
            otherwise Korean screen. It now resolves through the same leaf-token
            SSOT the chamber relay badge uses, so one connection state has one
            name on both screens. */}
        <StatusBadge
          status={streamStatusKind(status)}
          label={t(`ui.streamStatus.${streamStatusLabelToken(status)}`)}
          testId="events-status-badge"
        />
      </p>
      <ul className="control-events" data-testid="events-log" aria-live="polite">
        {events.map((event, index) => (
          <li key={`${event.kind}-${index}`} data-testid="event-item">
            <strong>{event.kind}</strong>
            {event.payload.length > 0 && <span> — {JSON.stringify(event.payload)}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default ControlRoute;
