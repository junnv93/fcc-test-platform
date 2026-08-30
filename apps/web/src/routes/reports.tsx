import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import {
  cancelReportRequest,
  createHeadlessClientForBaseUrl,
  fetchReportAutomationStats,
  fetchReportOutputs,
  fetchReportPreflight,
  fetchReportRequestStatus,
  fetchSessionArtifacts,
  headlessClient,
  issueReportOutputDownloadGrant,
  lookupReportRequest,
  submitReportRequest,
} from '@/api/headless-client';
import {
  PERMISSION_HEADLESS_READ,
  PERMISSION_REPORT_AUTOMATION_CONTROL,
  PERMISSION_REPORT_AUTOMATION_READ,
} from '@/api/permissions';
import {
  fetchProjectReportSessions,
  fetchProviderUiDescriptor,
  type ProjectReportSessionEnvelope,
} from '@/api/platform-client';
import { errorBackoffPollInterval, queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { clientOriginatedApiError } from '@/api/to-api-error';
import { RequirePermission, useAuthSession } from '@/auth/route-guard';
import { t, useT } from '@/i18n';
import { type ApiError, type ErrorCode } from '@/shared/api-error';
import { parsePositiveId } from '@/shared/numeric-id';
import { isValidProjectId } from '@/shared/project-id';
import { projectWorkflowActions } from '@/shared/project-workflow';
import { ROUTE_PATHS } from '@/shared/route-links';
import {
  isGrantExpired,
  runSignedDownload,
  type SignedDownloadGrant,
} from '@/shared/signed-download';
import { DEPLOYED_PROVIDER_ID, technologiesForArea } from '@/shared/workbench-area-technologies';
import { findWorkbenchArea } from '@/shared/workbench-areas';
import {
  BlockSkeleton,
  Button,
  Card,
  DataTable,
  describeApiError,
  EmptyState,
  ErrorState,
  FieldGroup,
  jobStatusToStatusKind,
  MetricStrip,
  NumericLookupForm,
  PageHeader,
  QUEUE_STATUS_TOKENS,
  queueStatusLabelToken,
  SectionBand,
  StatusBadge,
  StatusMessage,
  type DataTableColumn,
  type MetricStripItem,
  type StatusKind,
  type Translate,
  WorkbenchLayout,
} from '@/ui';

import type { components } from '@/api/generated/headless-api.types';

// Report-automation request lifecycle terminal states — polling stops once the
// request reaches one of these (mirrors the backend queue status vocabulary).
const TERMINAL_REQUEST_STATES = new Set(['completed', 'failed', 'cancelled']);

/**
 * The states a cancel is meaningful for (W3-6 M3, 2026-07-31).
 *
 * DERIVED as "known vocabulary − terminal", not written out. `queued`/`running`
 * is what the backend's own summary names ("Cancel a queued/running
 * report-automation request"), but hardcoding that pair here would put the queue
 * vocabulary in a THIRD place — and the day a state is added, the three copies
 * disagree while each still looks internally consistent. Deriving it makes the
 * cancellable set and the terminal set provably complementary.
 *
 * Deriving over the KNOWN token list (rather than "anything not terminal") is
 * also what makes an unmodelled forward-compat status offer nothing instead of a
 * button that is guaranteed to 409.
 */
const CANCELLABLE_REQUEST_STATES: ReadonlySet<string> = new Set(
  QUEUE_STATUS_TOKENS.filter((token) => !TERMINAL_REQUEST_STATES.has(token)),
);

/**
 * Whether a report-automation request in `status` can still be cancelled.
 *
 * Normalisation goes through `queueStatusLabelToken` — the SAME function the
 * badge label uses — so casing/whitespace collapse identically on both, and an
 * unknown token maps to `'unknown'`, which is not in the cancellable set. A
 * request whose status has not loaded yet (`undefined`) is not cancellable:
 * offering the action before knowing the state is exactly the "button that
 * fails" this milestone removes.
 *
 * Exported for direct unit testing (the decision, not the rendering).
 */
export function canCancelReportRequest(status: string | undefined): boolean {
  if (status === undefined) return false;
  return CANCELLABLE_REQUEST_STATES.has(queueStatusLabelToken(status));
}
// Mirrors the backend `SubmitReportRequest.template_profile` schema default
// (api_contract_schemas.py). openapi-typescript renders a defaulted property as
// non-optional, so the web must send it; the provider owns the actual template
// mapping (ADR-0010) — this is only the default profile selector.
const DEFAULT_TEMPLATE_PROFILE = 'fcc-default';

// Pre-generation preflight (report-preflight-precheck B3, 2026-06-24) —
// per-technology completeness kind → StatusBadge palette. complete=pass,
// incomplete=missing (gap), unknown=stale (no published plan → denominator
// unknown; never fabricated as a failure). Domain SSOT for the vocabulary is
// reporting.domain.models.report_preflight_summary.CompletenessKind.
function preflightStatusKind(kind: string): StatusKind {
  if (kind === 'complete') return 'pass';
  if (kind === 'incomplete') return 'missing';
  return 'stale';
}

type ReportPreflightSummary = components['schemas']['ReportPreflightSummary'];
/** One generated artifact of a report request — the row type of the outputs
 *  table. Derived from the generated OpenAPI types so a backend field change
 *  surfaces here as a type error rather than as a silently empty cell. */
type ReportOutput = components['schemas']['ReportOutputMetadata'];
interface SubmitReportResult {
  request_id: number;
  session_id: number;
  status: string;
}
// Submit is keyed by an atomic target snapshot captured at click time (session +
// node route) — NOT the live component state. React Query reads mutationFn /
// onSuccess from the LATEST render's options, so if a submit is in-flight while
// the operator re-runs preflight against another node, reading `requestNodeBaseUrl`
// from the closure would let the POST target (captured earlier) and the recorded
// `submittedNodeBaseUrl` / status-poll target (read on resolve) diverge. Threading
// both through the mutation variable pins them to the same committed target.
interface SubmitReportVariables {
  sessionId: number;
  nodeBaseUrl: string | null;
}

/** One download click: which artifact to grant, and the name to save it under.
 *  `fileName` is the server-supplied `file_name`, never a path segment the
 *  frontend reconstructs. */
interface DownloadVariables {
  readonly relativePath: string;
  readonly fileName: string;
}

function reportSessionKey(session: ProjectReportSessionEnvelope): string {
  return `${session.node_base_url}::${session.submit_session_id}`;
}

function reportSessionLabel(session: ProjectReportSessionEnvelope): string {
  const bits = [
    session.node_name || session.node_id,
    `${session.completed_conditions} conditions`,
    session.technologies.join(', '),
    session.latest_measured_at ?? '',
  ].filter(Boolean);
  return bits.join(' / ');
}

function clientForNode(nodeBaseUrl: string | null) {
  return nodeBaseUrl === null ? headlessClient : createHeadlessClientForBaseUrl(nodeBaseUrl);
}

/**
 * Report / artifact view — Headless API (FE-P6, 2026-05-26).
 *
 * operator 가 리포트 자동화 요청 상태/산출물과 세션 artifact 를 조회하고 누락을
 * 진단하는 읽기 화면이다. 백엔드는 "전체 요청 list" 엔드포인트가 없으므로(현
 * Headless API: queue stats + request_id/session_id 단건 조회), 본 화면은
 * (1) 큐 통계 + (2) request_id 조회(상태 + 산출물 + `exists` 누락 진단) +
 * (3) session_id artifact 조회로 구성한다. 산출물 다운로드는 서명 grant 흐름
 * (FE-P6-DL): POST .../outputs/download 로 self-authorizing download_url 을
 * 발급받아 브라우저가 navigate(window.location.assign) — raw filesystem path 는
 * 노출하지 않는다.
 *
 * RBAC: 리포트 요청/산출물은 `report_automation:read`, 세션 artifact 는
 * `headless:read`(백엔드 HEADLESS_API_PERMISSIONS SSOT 미러).
 */

// Exported for direct unit testing — the full documented status set mirrors the
// backend download stream taxonomy (FE-P6, 2026-05-29): 409 integrity conflict /
// 410 expired grant are emitted by the stream endpoint (api_contracts.py) so the
// message map stays complete even though the presigned download navigates the
// browser directly.
//
// Phase 2 (2026-05-30): the SSOT now lives in `@/ui/errors::describeApiError`.
// This wrapper supplies the headless context + the per-arm overrides that keep
// the report download taxonomy's 5-state failure copy (grant fail / 409
// integrity conflict / 410 expired / file missing / network unreachable) verbatim.
//
// W2-A M3 (2026-07-28): a code-first arm sits in front of the status taxonomy.
// The status axis cannot distinguish a download-integrity 409 (the granted bytes
// changed on disk) from any other 409, nor a grant-expiry 410 from a resource
// that is simply gone — the RFC 9457 `code` extension can, and W1 already made
// the producer side emit it. Codes not listed here fall through to the existing
// status taxonomy unchanged, so this is one ladder with a sharper first rung,
// not a second ladder.
const REPORT_MESSAGE_KEY_BY_CODE: Partial<Record<ErrorCode, string>> = {
  DOWNLOAD_INTEGRITY_CONFLICT: 'routes.reports.error.downloadIntegrityConflict',
  DOWNLOAD_EXPIRED: 'routes.reports.error.downloadExpired',
};

export function describeError(error: unknown): string {
  const code = typeof error === 'object' && error !== null ? (error as ApiError).code : undefined;
  const codeKey = code === undefined ? undefined : REPORT_MESSAGE_KEY_BY_CODE[code];
  if (codeKey !== undefined) return t(codeKey);
  return describeApiError(error, 'headless', {
    forbidden: t('routes.reports.error.forbidden'),
    notFound: t('routes.reports.error.notFound'),
    conflict: t('routes.reports.error.conflict'),
    gone: t('routes.reports.error.gone'),
    network: t('routes.reports.error.network'),
    default: t('routes.reports.error.default'),
  });
}

// Numeric id validation is the shared `@/shared/numeric-id` SSOT; re-exported
// here so existing consumers (tests) keep importing it from this module.
export { parsePositiveId };

/**
 * Poll cadence for the report-request lifecycle query (M2, 2026-07-28).
 *
 * A pure decision rather than an inline closure so the seal can drive it
 * directly — the alternative (fake timers around a live React Query observer)
 * tests the scheduler more than the policy.
 *
 * Two ways the poll stops:
 *   - the request reached a terminal state (the pre-existing rule), or
 *   - the fetch has failed too many times in a row. Previously an erroring poll
 *     re-fired at the fixed cadence forever against a node that was not
 *     answering, and the surface said nothing about it. Backing off and then
 *     parking makes the rendered error the final word.
 *
 * The cadence and the failure budget both come from the `query-config.ts` SSOT;
 * this route owns no interval literal.
 */
export function reportRequestPollInterval(
  data: { status?: string } | undefined,
  fetchFailureCount: number,
): number | false {
  if (data?.status && TERMINAL_REQUEST_STATES.has(data.status)) return false;
  return errorBackoffPollInterval(REFETCH_STRATEGIES.CRITICAL.refetchInterval, fetchFailureCount);
}

export function ReportsRoute(): JSX.Element {
  const { t } = useT();
  const auth = useAuthSession();
  const [searchParams] = useSearchParams();
  const canReadReports =
    auth.kind === 'authenticated' &&
    auth.principal.permissions.includes(PERMISSION_REPORT_AUTOMATION_READ);
  // The generate panel's committed report target (session id, node route,
  // in-flight submit/status) is scoped to the *report context*: the legacy
  // numeric flow (no `?project=`) vs a specific project's node-routed selector.
  // Both live in the same React Router element, so without a context-identity
  // key React reuses one SubmitReportPanel instance across a
  // /reports → /reports?project=<uuid> transition and a legacy session +
  // `requestNodeBaseUrl=null` (→ clientForNode(null) = shared headlessClient)
  // would leak into the project flow, mis-routing preflight/submit/status away
  // from the selected session's node_base_url. Keying the panel to the context
  // remounts it on a switch, re-initializing every committed-target state var
  // (sessionId / requestNodeBaseUrl / requestId / submittedNodeBaseUrl /
  // selectedReportSessionKey) plus the submit/status query observers. A
  // pure-legacy session keeps a constant 'legacy' key → no remount, no reset →
  // the numeric fallback path stays byte-identical.
  const projectParam = (searchParams.get('project') ?? '').trim();
  const reportContextKey = isValidProjectId(projectParam) ? `project:${projectParam}` : 'legacy';
  return (
    <section className="reports" aria-labelledby="reports-heading">
      <PageHeader
        title={t('routes.reports.page.title')}
        titleId="reports-heading"
        description={t('routes.reports.page.description')}
      />
      {isValidProjectId(projectParam) && <ReportsProjectContext projectId={projectParam} />}
      <ReportsWorkbenchOverview />
      {/* Submit gate is inline (not a RequirePermission wrapper) so an operator
          without report_automation:control simply doesn't see the generate panel
          — avoiding a second auth-failure surface alongside the read panels. */}
      <WorkbenchLayout
        className="reports-workbench"
        mainLabel={t('routes.reports.page.title')}
        railLabel={t('routes.reports.workbench.railAria')}
        testId="reports-workbench"
        main={
          <div className="reports-workbench__main">
            <SubmitReportPanel key={reportContextKey} />
            <RequirePermission permission={PERMISSION_REPORT_AUTOMATION_READ}>
              <ReportRequestLookup />
            </RequirePermission>
          </div>
        }
        rail={
          <div className="reports-workbench__rail">
            {canReadReports && <QueueStatsPanel />}
            <RequirePermission permission={PERMISSION_HEADLESS_READ}>
              <SessionArtifactsLookup />
            </RequirePermission>
          </div>
        }
      />
    </section>
  );
}

function ReportsProjectContext({ projectId }: { readonly projectId: string }): JSX.Element {
  const { t } = useT();
  const actions = projectWorkflowActions(projectId, [
    'workspace',
    'progress',
    'inventory',
    'testPlans',
    'chambers',
    'testReports',
  ]);
  const actionById = new Map(actions.map((action) => [action.id, action.href]));
  return (
    <Card
      as="section"
      variant="summary"
      className="reports-project-context"
      aria-labelledby="reports-project-context-heading"
      testId="reports-project-context"
    >
      <SectionBand
        title={t('routes.projects.nextSection')}
        titleId="reports-project-context-heading"
      />
      <p className="section-hint" data-testid="reports-project-context-state">
        {t('routes.projects.selectedProject', { project: projectId })}
      </p>
      <div
        className="reports-project-context__actions"
        data-testid="reports-project-context-actions"
      >
        <Link
          to={actionById.get('workspace') ?? '/projects'}
          className="reports-project-context__action"
          data-testid="reports-project-context-workspace"
        >
          {t('routes.myProjects.list.nextCoverage')}
        </Link>
        <Link
          to={actionById.get('progress') ?? '/progress'}
          className="reports-project-context__action"
          data-testid="reports-project-context-progress"
        >
          {t('routes.projects.nextProgress')}
        </Link>
        <Link
          to={actionById.get('inventory') ?? '/inventory'}
          className="reports-project-context__action"
          data-testid="reports-project-context-inventory"
        >
          {t('routes.projects.nextInventory')}
        </Link>
        <Link
          to={actionById.get('testPlans') ?? '/test-plans'}
          className="reports-project-context__action"
          data-testid="reports-project-context-test-plans"
        >
          {t('routes.projects.nextTestPlans')}
        </Link>
        <Link
          to={actionById.get('chambers') ?? '/chambers'}
          className="reports-project-context__action"
          data-testid="reports-project-context-chambers"
        >
          {t('routes.progress.nextChambers')}
        </Link>
        <Link
          to={actionById.get('testReports') ?? ROUTE_PATHS.testReports}
          className="reports-project-context__action"
          data-testid="reports-project-context-test-reports"
        >
          {t('routes.testReports.title')}
        </Link>
      </div>
    </Card>
  );
}

function ReportsWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="reports-workbench-overview"
      aria-label={t('routes.reports.workbench.navAria')}
      data-testid="reports-workbench-overview"
    >
      <a className="reports-workbench-overview__item" href="#reports-submit-heading">
        <span className="reports-workbench-overview__label">
          {t('routes.reports.workbench.stepGenerate')}
        </span>
        <span className="reports-workbench-overview__detail">
          {t('routes.reports.workbench.stepGenerateDetail')}
        </span>
      </a>
      <a className="reports-workbench-overview__item" href="#reports-request-heading">
        <span className="reports-workbench-overview__label">
          {t('routes.reports.workbench.stepOutputs')}
        </span>
        <span className="reports-workbench-overview__detail">
          {t('routes.reports.workbench.stepOutputsDetail')}
        </span>
      </a>
      <a className="reports-workbench-overview__item" href="#reports-artifacts-heading">
        <span className="reports-workbench-overview__label">
          {t('routes.reports.workbench.stepArtifacts')}
        </span>
        <span className="reports-workbench-overview__detail">
          {t('routes.reports.workbench.stepArtifactsDetail')}
        </span>
      </a>
    </nav>
  );
}

/**
 * Submit a report-automation request for a session (Phase 5). The web only
 * *requests* generation — the provider's report-automation backend owns the
 * generation engine + template mapping (ADR-0010). On success the request_id is
 * shown and its lifecycle status is polled (queued → running → completed/failed/
 * cancelled) until terminal, so the operator sees progress without a manual
 * reload. The full output list + download lives in the read lookup panel below
 * (the operator pastes the surfaced request_id there).
 */
function SubmitReportPanel(): JSX.Element | null {
  const { t } = useT();
  const queryClient = useQueryClient();
  const auth = useAuthSession();
  // Inline permission gate (mirror of TestPlansWorkbench's `canAuthor`): only a
  // report_automation:control principal sees the generate panel; everyone else
  // gets null (no second auth-failure surface — the read panels own that).
  const canControl =
    auth.kind === 'authenticated' &&
    auth.principal.permissions.includes(PERMISSION_REPORT_AUTOMATION_CONTROL);
  // `generated_by` provenance is the authenticated principal (audit only — the
  // backend re-derives identity from the trusted header, never an authz input).
  const generatedBy = auth.kind === 'authenticated' ? auth.principal.subject : '';
  const [input, setInput] = useState('');
  // The session the operator has committed to a preflight (form submit) — also
  // the generation target. Distinct from `input` so the preflight does not
  // re-fire on every keystroke (only on an explicit "Run preflight").
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [requestId, setRequestId] = useState<number | null>(null);
  const invalid = input.trim() !== '' && parsePositiveId(input) === null;

  // Workbench-area context (`?area=`) the operator entered reports from. Its
  // provider-declared technologies scope the report to those types (P5-B);
  // absent / unrecognized area keeps the legacy full-scope payload.
  const [searchParams] = useSearchParams();
  const areaId = findWorkbenchArea((searchParams.get('area') ?? '').trim())?.id ?? null;
  const projectId = (searchParams.get('project') ?? '').trim();
  const hasProjectContext = isValidProjectId(projectId);
  const [selectedReportSessionKey, setSelectedReportSessionKey] = useState<string>('');
  const [requestNodeBaseUrl, setRequestNodeBaseUrl] = useState<string | null>(null);
  const [submittedNodeBaseUrl, setSubmittedNodeBaseUrl] = useState<string | null>(null);

  // Read the deployed provider's descriptor to resolve the area → technologies
  // mapping (provider-owned, ADR-0010 — the frontend never hardcodes report-type
  // values). Fetched only when a recognized area is present AND the principal can
  // actually generate reports: the panel is RBAC-hidden (returns null below) for
  // a non-`report_automation:control` principal, so gating the query on
  // `canControl` keeps a hidden panel from issuing a provider descriptor fetch.
  // The bare `/reports` route (no area) still runs no descriptor query (no
  // waterfall).
  const descriptor = useQuery({
    queryKey: queryKeys.provider.uiDescriptor(DEPLOYED_PROVIDER_ID),
    enabled: canControl && areaId !== null,
    queryFn: () => fetchProviderUiDescriptor(DEPLOYED_PROVIDER_ID),
  });

  // Report types = the provider-declared technologies for the entered area, or
  // [] when there is no area / descriptor / mapping (→ legacy full-scope
  // request). Memoized over primitive `areaId` + the stable react-query data
  // reference so the derivation does not re-run each keystroke.
  const reportTypes = useMemo(
    () => (areaId === null ? [] : technologiesForArea(descriptor.data, areaId)),
    [areaId, descriptor.data],
  );

  const reportSessions = useQuery({
    queryKey: queryKeys.project.reportSessions(hasProjectContext ? projectId : null),
    enabled: canControl && hasProjectContext,
    queryFn: () => fetchProjectReportSessions(projectId),
  });

  const selectedReportSession = useMemo(() => {
    const sessions = reportSessions.data ?? [];
    if (sessions.length === 0) return null;
    return (
      sessions.find((session) => reportSessionKey(session) === selectedReportSessionKey) ??
      sessions[0] ??
      null
    );
  }, [reportSessions.data, selectedReportSessionKey]);

  // Pre-generation preflight (report-preflight-precheck B3) — read-only dry-run
  // of per-technology completeness + data-quality. Advisory ONLY: a failure /
  // gaps never block generation (the generate button stays enabled regardless).
  const preflight = useQuery({
    queryKey: queryKeys.report.preflight(
      sessionId,
      hasProjectContext ? requestNodeBaseUrl : undefined,
    ),
    enabled: sessionId !== null,
    queryFn: async () => {
      if (sessionId === null) throw new Error('session id required'); // enabled-gated
      return fetchReportPreflight(clientForNode(requestNodeBaseUrl), sessionId);
    },
  });

  const submit = useMutation<SubmitReportResult, ApiError, SubmitReportVariables>({
    mutationFn: async ({ sessionId, nodeBaseUrl }) => {
      // Include report_types only when the area context resolved a non-empty
      // technology scope; otherwise the body stays byte-identical to the legacy
      // full-scope request ({ generated_by, template_profile }).
      const body: components['schemas']['SubmitReportRequest'] =
        reportTypes.length > 0
          ? {
              generated_by: generatedBy,
              template_profile: DEFAULT_TEMPLATE_PROFILE,
              report_types: [...reportTypes],
            }
          : { generated_by: generatedBy, template_profile: DEFAULT_TEMPLATE_PROFILE };
      // Route to the node captured in the submit variable (legacy → null → shared
      // client), never the live `requestNodeBaseUrl` state — so a concurrent
      // node-B preflight cannot redirect this in-flight POST.
      return submitReportRequest(clientForNode(nodeBaseUrl), sessionId, body);
    },
    onSuccess: (data, variables) => {
      setRequestId(data.request_id);
      // Record the node captured at submit time (the mutation variable), so the
      // status poll follows the SAME node this request was POSTed to even if the
      // selector has since moved to another node.
      setSubmittedNodeBaseUrl(variables.nodeBaseUrl);
      // The queue stats strip should reflect the newly queued request.
      void queryClient.invalidateQueries({ queryKey: queryKeys.report.stats() });
    },
  });

  // Poll the submitted request's lifecycle status until it reaches a terminal
  // state. Shares the report.request key with the lookup panel (same resource).
  //
  // Bound to a named value because the cancel control must invalidate THIS key:
  // the node-routed variant here and the plain variant in the lookup panel are
  // different cache entries, so a control that rebuilt the key itself would
  // refresh the wrong one (M3).
  const statusQueryKey = queryKeys.report.request(requestId, submittedNodeBaseUrl ?? undefined);
  const status = useQuery({
    queryKey: statusQueryKey,
    enabled: requestId !== null,
    refetchInterval: (query) =>
      reportRequestPollInterval(
        query.state.data as { status?: string } | undefined,
        query.state.fetchFailureCount,
      ),
    queryFn: async () => {
      if (requestId === null) throw new Error('request id required'); // enabled-gated
      return fetchReportRequestStatus(clientForNode(submittedNodeBaseUrl), requestId);
    },
  });

  useEffect(() => {
    setSessionId(null);
    setRequestNodeBaseUrl(null);
    setRequestId(null);
    setSubmittedNodeBaseUrl(null);
    setSelectedReportSessionKey('');
    submit.reset();
  }, [hasProjectContext, projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Guard placed after all hooks (rules-of-hooks): an unauthorized principal
  // renders nothing rather than a denied surface.
  if (!canControl) return null;

  return (
    <section
      className="reports-workbench-panel"
      aria-labelledby="reports-submit-heading"
      data-testid="reports-submit"
    >
      <SectionBand title={t('routes.reports.submit.bandTitle')} titleId="reports-submit-heading" />
      <p className="section-hint">{t('routes.reports.submit.description')}</p>
      {hasProjectContext ? (
        <div className="reports-session-picker" data-testid="report-session-picker">
          <FieldGroup
            label={t('routes.reports.submit.sessionLabel')}
            htmlFor="report-session-select"
          >
            <select
              id="report-session-select"
              data-testid="report-session-select"
              value={selectedReportSession ? reportSessionKey(selectedReportSession) : ''}
              disabled={reportSessions.isLoading || (reportSessions.data ?? []).length === 0}
              onChange={(event) => setSelectedReportSessionKey(event.currentTarget.value)}
            >
              {(reportSessions.data ?? []).length === 0 ? (
                <option value="">{t('routes.reports.submit.noReportSessions')}</option>
              ) : (
                (reportSessions.data ?? []).map((session) => (
                  <option key={reportSessionKey(session)} value={reportSessionKey(session)}>
                    {reportSessionLabel(session)}
                  </option>
                ))
              )}
            </select>
          </FieldGroup>
          {reportSessions.isError && (
            <ErrorState
              testId="report-sessions-error"
              message={describeError(reportSessions.error)}
            />
          )}
          <Button
            type="button"
            variant="primary"
            data-testid="report-preflight-check"
            disabled={selectedReportSession === null}
            onClick={() => {
              if (selectedReportSession !== null) {
                setRequestNodeBaseUrl(selectedReportSession.node_base_url);
                setSessionId(selectedReportSession.submit_session_id);
              }
            }}
          >
            {preflight.isFetching && sessionId !== null
              ? t('routes.reports.preflight.checking')
              : t('routes.reports.preflight.checkButton')}
          </Button>
        </div>
      ) : (
        <NumericLookupForm
          label={t('routes.reports.submit.sessionLabel')}
          inputId="report-submit-session-id"
          inputTestId="submit-session-input"
          value={input}
          onChange={setInput}
          onSubmit={() => {
            const id = parsePositiveId(input);
            if (id !== null) {
              setRequestNodeBaseUrl(null);
              setSessionId(id);
            }
          }}
          buttonLabel={
            preflight.isFetching && sessionId !== null
              ? t('routes.reports.preflight.checking')
              : t('routes.reports.preflight.checkButton')
          }
          submitTestId="report-preflight-check"
          submitDisabled={parsePositiveId(input) === null}
          invalid={invalid}
          invalidMessage={t('routes.reports.submit.invalidId')}
          invalidTestId="submit-session-invalid"
        />
      )}

      {/* Stage 2 — preflight summary + the generate action. The generate button
          is ALWAYS enabled once a session is committed (advisory not blocking):
          gaps/warnings inform but never gate generation (wireframe ④). */}
      {sessionId !== null && (
        <div className="reports-preflight-panel" data-testid="reports-preflight">
          <p className="section-hint">{t('routes.reports.preflight.description')}</p>
          {preflight.isLoading && <BlockSkeleton lines={3} testId="preflight-loading" />}
          {preflight.isError && (
            <StatusMessage
              testId="preflight-error"
              tone="info"
              message={t('routes.reports.preflight.error')}
            />
          )}
          {preflight.isSuccess && preflight.data && <PreflightSummaryView data={preflight.data} />}
          {/* Honest scope feedback (P5-B): when an area context resolves a
              technology scope, the operator sees which technologies the report
              will cover — the report_types body field is never a hidden change. */}
          {reportTypes.length > 0 && (
            <p className="section-hint" data-testid="report-types-scope">
              {t('routes.reports.submit.scopeNote', { types: reportTypes.join(', ') })}
            </p>
          )}
          <Button
            type="button"
            variant="primary"
            data-testid="report-submit"
            disabled={submit.isPending}
            onClick={() =>
              // Capture the committed target (session + node route) atomically at
              // click time so the submit variable — not later component state —
              // drives both the POST and the status-poll routing.
              submit.mutate({ sessionId, nodeBaseUrl: requestNodeBaseUrl })
            }
          >
            {submit.isPending
              ? t('routes.reports.submit.busy')
              : t('routes.reports.preflight.generateButton')}
          </Button>
        </div>
      )}

      {submit.isError && <ErrorState testId="submit-error" message={describeError(submit.error)} />}

      {submit.isSuccess && requestId !== null && (
        <div data-testid="submit-result">
          <StatusMessage
            testId="submit-success"
            tone="success"
            message={t('routes.reports.submit.successLabel', { id: String(requestId) })}
          />
          {/* M2: the lifecycle poll failing is a fact about the operator's
              request — staying silent (the pre-M2 behaviour) reads as "no status
              yet" and invites a duplicate generate. Same ErrorState idiom the
              sibling queries in this file already use. */}
          {status.isError && (
            <ErrorState testId="submit-status-fetch-error" message={describeError(status.error)} />
          )}
          {status.isSuccess && status.data && (
            <dl className="report-request" data-testid="submit-status-detail">
              <dt>{t('routes.reports.request.statusLabel')}</dt>
              <dd data-testid="submit-status">
                <StatusBadge
                  status={jobStatusToStatusKind(status.data.status)}
                  label={t(`routes.reports.stats.${queueStatusLabelToken(status.data.status)}`)}
                />
              </dd>
              {status.data.error_message && (
                <>
                  <dt>{t('routes.reports.request.errorLabel')}</dt>
                  <dd data-testid="submit-status-error">{status.data.error_message}</dd>
                </>
              )}
            </dl>
          )}
          {status.isSuccess && status.data && (
            <ReportCancelControl
              requestId={requestId}
              status={status.data.status}
              nodeBaseUrl={submittedNodeBaseUrl}
              requestQueryKey={statusQueryKey}
            />
          )}
        </div>
      )}
    </section>
  );
}

/**
 * Cancel control for one report-automation request (W3-6 M3, 2026-07-31).
 *
 * Before this, `TERMINAL_REQUEST_STATES` listed `'cancelled'` and the badge
 * rendered it — the screen displayed the outcome of an action the operator had
 * no way to take. Wiring `POST /report-automation/requests/{id}/cancel` makes
 * the displayed vocabulary and the available actions describe the same system.
 *
 * Two host panels render this, and they read the request through DIFFERENT
 * query keys: the submit panel's poll is node-routed
 * (`report.request(id, nodeBaseUrl)`) while the lookup panel's is not
 * (`report.request(id)`). So the key is INJECTED by the host rather than rebuilt
 * here — a control that composed its own key would invalidate the other panel's
 * cache entry, and the failure mode is the quiet one: the cancel succeeds and
 * the status on screen never moves.
 *
 * The node route is threaded for the same reason the submit/poll pair threads
 * it: the POST must reach the node the request actually lives on.
 *
 * RBAC: `report_automation:control` (backend `x-fcc-permission`). The lookup
 * panel is only `report_automation:read`-gated, so the check lives here rather
 * than at either call site — a read-only operator sees the status and no
 * control, with no second auth-failure surface.
 */
function ReportCancelControl({
  requestId,
  status,
  nodeBaseUrl,
  requestQueryKey,
}: {
  readonly requestId: number;
  readonly status: string | undefined;
  readonly nodeBaseUrl: string | null;
  readonly requestQueryKey: readonly unknown[];
}): JSX.Element | null {
  const { t } = useT();
  const queryClient = useQueryClient();
  const auth = useAuthSession();
  const canControl =
    auth.kind === 'authenticated' &&
    auth.principal.permissions.includes(PERMISSION_REPORT_AUTOMATION_CONTROL);
  // Arming is keyed by the request it was granted for, not a bare boolean, so a
  // host that swaps to another request can never carry a stale confirmation
  // into it (the same discipline `BulkRowsEditor` uses for its acknowledgements).
  const [armedFor, setArmedFor] = useState<number | null>(null);

  const cancel = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      // The request body is optional in the contract (`message` defaults to
      // `''` server-side) and this screen collects no reason, so it is omitted
      // rather than sent empty — no invented field.
      await cancelReportRequest(clientForNode(nodeBaseUrl), requestId);
    },
    onSuccess: () => {
      // The host's OWN key — so the panel that rendered this control is the one
      // that refetches and re-renders the new (cancelled) status.
      void queryClient.invalidateQueries({ queryKey: requestQueryKey });
      // The queue counts move too (running/queued → cancelled).
      void queryClient.invalidateQueries({ queryKey: queryKeys.report.stats() });
      setArmedFor(null);
    },
  });

  // Hooks are all above this guard (rules-of-hooks). A terminal or unknown
  // status renders nothing at all: the operator is not offered an action whose
  // only possible outcome is a server rejection.
  if (!canControl) return null;
  if (!canCancelReportRequest(status)) return null;

  return (
    <div data-testid="request-cancel-section">
      {armedFor === requestId ? (
        <div className="report-cancel-confirm" data-testid="request-cancel-confirm">
          <p className="report-cancel-confirm__warning" data-testid="request-cancel-warning">
            {t('routes.reports.cancel.warning')}
          </p>
          <Button
            type="button"
            variant="danger"
            data-testid="request-cancel-confirm-button"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate()}
          >
            {cancel.isPending
              ? t('routes.reports.cancel.busy')
              : t('routes.reports.cancel.confirm')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            data-testid="request-cancel-keep"
            disabled={cancel.isPending}
            onClick={() => setArmedFor(null)}
          >
            {t('routes.reports.cancel.keep')}
          </Button>
        </div>
      ) : (
        <Button
          type="button"
          variant="secondary"
          data-testid="request-cancel"
          onClick={() => setArmedFor(requestId)}
        >
          {t('routes.reports.cancel.button')}
        </Button>
      )}
      {cancel.isError && (
        <ErrorState testId="request-cancel-error" message={describeError(cancel.error)} />
      )}
    </div>
  );
}

/**
 * Render a report preflight summary (report-preflight-precheck B3) — per-tech
 * completeness badges + measured/planned counts + data-quality warnings. No
 * fabricated metrics: `planned_total === null` (no published plan) renders an
 * explicit "planned total unknown", never a 0 denominator. Advisory only — the
 * generate button (owned by SubmitReportPanel) stays enabled regardless.
 */
function PreflightSummaryView({ data }: { data: ReportPreflightSummary }): JSX.Element {
  const { t } = useT();
  return (
    <div data-testid="preflight-summary">
      <h3 className="preflight-subheading">{t('routes.reports.preflight.completenessTitle')}</h3>
      {data.per_tech.length === 0 ? (
        <p data-testid="preflight-no-tech">{t('routes.reports.preflight.noTech')}</p>
      ) : (
        <ul className="preflight-per-tech" data-testid="preflight-per-tech">
          {data.per_tech.map((tech) => (
            <li key={tech.technology} data-testid="preflight-tech-row">
              <span className="preflight-tech-name">{tech.technology}</span>
              <StatusBadge
                status={preflightStatusKind(tech.kind)}
                label={t(`routes.reports.preflight.status_${tech.kind}`)}
              />
              <span className="preflight-tech-count">
                {tech.planned_total === null
                  ? t('routes.reports.preflight.measuredUnknownTotal', {
                      measured: String(tech.measured_count),
                    })
                  : t('routes.reports.preflight.measured', {
                      measured: String(tech.measured_count),
                      total: String(tech.planned_total),
                    })}
              </span>
            </li>
          ))}
        </ul>
      )}

      {data.data_quality.length > 0 && (
        <>
          <h3 className="preflight-subheading">{t('routes.reports.preflight.dataQualityTitle')}</h3>
          <ul className="preflight-data-quality" data-testid="preflight-data-quality">
            {data.data_quality.map((warning, index) => (
              <li key={`${warning.code}-${warning.row_order ?? 'x'}-${index}`}>
                <StatusMessage tone="info" message={warning.message} />
              </li>
            ))}
          </ul>
        </>
      )}

      {data.missing_sources.length > 0 && (
        <>
          <h3 className="preflight-subheading">
            {t('routes.reports.preflight.missingSourcesTitle')}
          </h3>
          <ul className="preflight-missing-sources" data-testid="preflight-missing-sources">
            {data.missing_sources.map((source, index) => (
              <li key={`${source.technology}-${source.section}-${source.table_name}-${index}`}>
                <StatusMessage
                  tone="info"
                  message={`${source.technology} · ${source.section} · ${source.table_name}${
                    source.channel ? ` (CH ${source.channel})` : ''
                  } — ${t(`routes.reports.preflight.missingReason_${source.reason}`)}`}
                />
              </li>
            ))}
          </ul>
        </>
      )}

      {!data.has_incomplete && !data.has_data_quality_warnings && !data.has_missing_sources && (
        <StatusMessage
          testId="preflight-all-clear"
          tone="success"
          message={t('routes.reports.preflight.allClear')}
        />
      )}

      <p className="section-hint" data-testid="preflight-advisory">
        {t('routes.reports.preflight.advisory')}
      </p>
    </div>
  );
}

function QueueStatsPanel(): JSX.Element {
  const { t } = useT();
  const stats = useQuery({
    queryKey: queryKeys.report.stats(),
    queryFn: async () => {
      return fetchReportAutomationStats();
    },
  });

  const items: readonly MetricStripItem[] =
    stats.isSuccess && stats.data
      ? [
          {
            key: 'queued',
            label: t('routes.reports.stats.queued'),
            value: stats.data.queued,
            valueTestId: 'stat-queued',
          },
          {
            key: 'running',
            label: t('routes.reports.stats.running'),
            value: stats.data.running,
            valueTestId: 'stat-running',
          },
          {
            key: 'completed',
            label: t('routes.reports.stats.completed'),
            value: stats.data.completed,
            valueTestId: 'stat-completed',
          },
          {
            key: 'failed',
            label: t('routes.reports.stats.failed'),
            value: stats.data.failed,
            valueTestId: 'stat-failed',
          },
          {
            key: 'cancelled',
            label: t('routes.reports.stats.cancelled'),
            value: stats.data.cancelled,
            valueTestId: 'stat-cancelled',
          },
        ]
      : [];

  return (
    <section className="reports-workbench-panel" aria-labelledby="reports-stats-heading">
      <SectionBand title={t('routes.reports.stats.bandTitle')} titleId="reports-stats-heading" />
      {stats.isLoading && <BlockSkeleton variant="metric" lines={3} testId="stats-loading" />}
      {stats.isError && <ErrorState testId="stats-error" message={describeError(stats.error)} />}
      {stats.isSuccess && stats.data && (
        <div data-testid="queue-stats">
          <MetricStrip ariaLabel={t('routes.reports.stats.metricStripAria')} items={items} />
        </div>
      )}
    </section>
  );
}

function ReportRequestLookup(): JSX.Element {
  const { t } = useT();
  const [input, setInput] = useState('');
  const [requestId, setRequestId] = useState<number | null>(null);
  const invalid = input.trim() !== '' && parsePositiveId(input) === null;

  // Named for the same reason as the submit panel's poll key: the cancel control
  // invalidates the key of the panel that rendered it, and this panel's read is
  // NOT node-routed (it goes through the shared client), so its cache entry
  // differs from the submit panel's.
  const requestQueryKey = queryKeys.report.request(requestId);
  const request = useQuery({
    queryKey: requestQueryKey,
    enabled: requestId !== null,
    queryFn: async () => {
      if (requestId === null) throw new Error('request id required'); // narrows; enabled-gated
      return lookupReportRequest(requestId);
    },
  });

  const outputs = useQuery({
    queryKey: queryKeys.report.outputs(requestId),
    enabled: requestId !== null,
    queryFn: async () => {
      if (requestId === null) throw new Error('request id required'); // narrows; enabled-gated
      return fetchReportOutputs(requestId);
    },
  });

  // FE-P6-DL: request a signed, short-TTL download grant, then spend it INSIDE
  // the page (M3). The grant stays self-authorizing (no RBAC header) and the raw
  // filesystem path is still never exposed — only the execution changed: the old
  // `window.location.assign(url)` handed a 409/410/404 to the browser as a
  // top-level navigation, which replaced the SPA with raw problem+json and threw
  // away the looked-up request id and the loaded output list. Now the failure
  // taxonomy comes back as an ApiError (status + RFC 9457 `code`) and renders in
  // place, and the received `expires_at` is actually honoured instead of ignored.
  const download = useMutation<void, ApiError, DownloadVariables>({
    mutationFn: async ({ relativePath, fileName }) => {
      if (requestId === null) throw new Error('request id required'); // enabled-gated by button
      const issueGrant = async (): Promise<SignedDownloadGrant> => {
        return issueReportOutputDownloadGrant(requestId, relativePath);
      };
      let grant = await issueGrant();
      if (isGrantExpired(grant.expires_at, Date.now())) {
        // The short TTL elapsed between issuance and use (slow round-trip, a
        // resumed laptop). Spending it is a guaranteed 410 — re-issue once.
        grant = await issueGrant();
        if (isGrantExpired(grant.expires_at, Date.now())) {
          // No fabricated HTTP status: the server never answered 410 here. The
          // machine-readable code carries the fact, and describeError maps it.
          throw clientOriginatedApiError('download grant expired before use', {
            code: 'DOWNLOAD_EXPIRED',
          });
        }
      }
      await runSignedDownload(grant, fileName);
    },
  });

  return (
    <section className="reports-workbench-panel" aria-labelledby="reports-request-heading">
      <SectionBand
        title={t('routes.reports.request.bandTitle')}
        titleId="reports-request-heading"
      />
      <NumericLookupForm
        label={t('routes.reports.request.idLabel')}
        inputId="report-request-id"
        inputTestId="request-id-input"
        value={input}
        onChange={setInput}
        onSubmit={() => setRequestId(parsePositiveId(input))}
        buttonLabel={t('routes.reports.lookupButton')}
        submitTestId="request-lookup"
        submitDisabled={parsePositiveId(input) === null}
        invalid={invalid}
        invalidMessage={t('routes.reports.request.invalidId')}
        invalidTestId="request-id-invalid"
      />

      {request.isError && (
        <ErrorState testId="request-error" message={describeError(request.error)} />
      )}
      {request.isSuccess && request.data && (
        <dl className="report-request" data-testid="request-detail">
          <dt>{t('routes.reports.request.statusLabel')}</dt>
          {/* Report-automation request status shares the measurement-job queue
              vocabulary (queued/running/completed/failed/cancelled — same tokens
              as the stats strip above), so it reuses BOTH the
              jobStatusToStatusKind SSOT (badge color) and the same
              `routes.reports.stats.*` i18n keys the metric strip uses (badge
              label) via queueStatusLabelToken — the raw backend token never
              renders (R5). */}
          <dd data-testid="request-status">
            <StatusBadge
              status={jobStatusToStatusKind(request.data.status)}
              label={t(`routes.reports.stats.${queueStatusLabelToken(request.data.status)}`)}
            />
          </dd>
          <dt>{t('routes.reports.request.sessionLabel')}</dt>
          <dd>{request.data.session_id ?? '—'}</dd>
          {request.data.error_message && (
            <>
              <dt>{t('routes.reports.request.errorLabel')}</dt>
              <dd data-testid="request-error-message">{request.data.error_message}</dd>
            </>
          )}
        </dl>
      )}
      {request.isSuccess && request.data && requestId !== null && (
        <ReportCancelControl
          requestId={requestId}
          status={request.data.status}
          nodeBaseUrl={null}
          requestQueryKey={requestQueryKey}
        />
      )}

      {/* M2 (D2): an outputs lookup that FAILED used to render nothing at all —
          every branch below is gated on `isSuccess`, so a 403/503/network error
          left the panel looking like a request with no artifacts, and the
          operator re-ran generation to fix a problem that was never generation's.
          Same ErrorState idiom as `request`/`stats`/`submit` above. */}
      {outputs.isError && (
        <ErrorState testId="outputs-error" message={describeError(outputs.error)} />
      )}
      {outputs.isSuccess && outputs.data?.length === 0 && (
        <EmptyState
          testId="outputs-empty"
          title={t('routes.reports.outputs.empty')}
          description={t('routes.reports.outputs.emptyDescription')}
        />
      )}
      {outputs.isSuccess && outputs.data && outputs.data.length > 0 && (
        <DataTable<ReportOutput>
          testId="outputs-table"
          caption={t('routes.reports.outputs.caption')}
          columns={outputColumns(t, download)}
          rows={outputs.data}
          rowKey={(output) => output.relative_path}
          rowTestId="output-row"
        />
      )}
      {download.isError && (
        <ErrorState testId="download-error" message={describeError(download.error)} />
      )}
    </section>
  );
}

/**
 * Column descriptor for the report outputs table (§M7.2).
 *
 * The file name identifies the row and the download control is the reason the
 * operator is on this screen, so both stay primary. Availability is what makes
 * the download meaningful. Byte size is diagnostic detail — it folds into the
 * per-row overflow line on compact and into the card body on a phone.
 */
function outputColumns(
  t: Translate,
  download: {
    readonly isPending: boolean;
    readonly mutate: (variables: DownloadVariables) => void;
  },
): readonly DataTableColumn<ReportOutput>[] {
  return [
    {
      key: 'file',
      header: t('routes.reports.outputs.colFile'),
      priority: 'primary',
      rowHeader: true,
      cell: (output) => output.file_name,
    },
    {
      key: 'size',
      header: t('routes.reports.outputs.colSize'),
      priority: 'detail',
      className: 'data-cell-numeric',
      cell: (output) => output.byte_size ?? '—',
    },
    {
      key: 'status',
      header: t('routes.reports.outputs.colStatus'),
      priority: 'primary',
      cell: (output) =>
        output.exists ? (
          <StatusBadge
            status="pass"
            label={t('routes.reports.outputs.available')}
            testId="output-available"
          />
        ) : (
          <StatusBadge
            status="missing"
            label={t('routes.reports.outputs.missing')}
            testId="output-missing"
            title={t('routes.reports.outputs.missingTitle')}
          />
        ),
    },
    {
      key: 'download',
      header: t('routes.reports.outputs.colDownload'),
      priority: 'secondary',
      cell: (output) => (
        <Button
          type="button"
          variant="secondary"
          data-testid="output-download"
          disabled={!output.exists || download.isPending}
          onClick={() => {
            download.mutate({
              relativePath: output.relative_path,
              fileName: output.file_name,
            });
          }}
        >
          {t('routes.reports.outputs.download')}
        </Button>
      ),
    },
  ];
}

function SessionArtifactsLookup(): JSX.Element {
  const { t } = useT();
  const [input, setInput] = useState('');
  const [sessionId, setSessionId] = useState<number | null>(null);
  const invalid = input.trim() !== '' && parsePositiveId(input) === null;

  const artifacts = useQuery({
    queryKey: queryKeys.report.sessionArtifacts(sessionId),
    enabled: sessionId !== null,
    queryFn: async () => {
      if (sessionId === null) throw new Error('session id required'); // narrows; enabled-gated
      return fetchSessionArtifacts(sessionId);
    },
  });

  return (
    <section className="reports-workbench-panel" aria-labelledby="reports-artifacts-heading">
      <SectionBand
        title={t('routes.reports.artifacts.bandTitle')}
        titleId="reports-artifacts-heading"
      />
      <NumericLookupForm
        label={t('routes.reports.artifacts.idLabel')}
        inputId="artifact-session-id"
        inputTestId="session-id-input"
        value={input}
        onChange={setInput}
        onSubmit={() => setSessionId(parsePositiveId(input))}
        buttonLabel={t('routes.reports.lookupButton')}
        submitTestId="artifacts-lookup"
        submitDisabled={parsePositiveId(input) === null}
        invalid={invalid}
        invalidMessage={t('routes.reports.artifacts.invalidId')}
        invalidTestId="session-id-invalid"
      />

      {artifacts.isError && (
        <ErrorState testId="artifacts-error" message={describeError(artifacts.error)} />
      )}
      {artifacts.isSuccess && artifacts.data && artifacts.data.length > 0 && (
        <ul className="session-artifacts" data-testid="artifacts-list">
          {artifacts.data.map((artifact) => (
            <li key={artifact.relative_path} data-testid="artifact-item">
              <strong>{artifact.artifact_type}</strong> — {artifact.original_filename} (
              {artifact.relative_path})
            </li>
          ))}
        </ul>
      )}
      {/* Single empty-state path (no inline empty <li> + EmptyState double
          render): the EmptyState primitive owns the empty branch. */}
      {artifacts.isSuccess && artifacts.data?.length === 0 && (
        <EmptyState
          testId="artifacts-empty"
          title={t('routes.reports.artifacts.emptyStateTitle')}
          description={t('routes.reports.artifacts.emptyStateDescription')}
        />
      )}
    </section>
  );
}

export default ReportsRoute;
