/**
 * status-mapping — domain enum → status SSOT (`StatusKind`) bridge.
 *
 * Phase 2 follow-up (fe-phase2-followup, 2026-05-30). Before this module
 * three operator routes carried near-identical mapping functions inline:
 *
 *   projects.tsx::verdictToStatusKind     (measurement verdict)
 *   sessions.tsx::attemptVerdictKind      (attempt verdict — identical body!)
 *   providers.tsx::featureStatusKind      (ProviderFeature.status)
 *   control.tsx::streamStatusKind         (SessionStreamStatus)
 *
 * Each was a hardcoded mapping in a different file with no cross-call
 * verification — a future maintainer adding a verdict value (e.g. `'inconclusive'`)
 * had to remember to update both projects + sessions, and a new
 * `SessionStreamStatus` arm had to be re-mapped on the route layer.
 *
 * This module consolidates them as the single bridge: the per-domain
 * function lives here, the routes import + consume. The four "no inline
 * mapping" properties are sealed by
 * `tests/test_fe_phase2_followup.py::TestStatusMappingSsot`.
 */
import type { StatusKind } from './StatusBadge';
import type { ChamberStreamStatus } from '@/api/chamber-events';
import type { SessionStreamStatus } from '@/api/session-events';

/**
 * Measurement verdict (`'Pass'` / `'Fail'` / 그 외) → status SSOT.
 *
 * Returns `null` for anything that does not normalize to `'pass'` / `'fail'`
 * — the caller falls back to a plain em-dash so an unknown verdict is
 * surfaced as "no data" rather than mis-categorised.
 *
 * Trim + lowercase the input so trailing whitespace from spreadsheet ingest
 * (`'Pass '`) and capitalisation variants (`'PASS'`) collapse to the same
 * canonical token.
 */
export function verdictToStatusKind(verdict: string | null | undefined): StatusKind | null {
  if (verdict === null || verdict === undefined) return null;
  const v = verdict.trim().toLowerCase();
  if (v === 'pass') return 'pass';
  if (v === 'fail') return 'fail';
  return null;
}

/**
 * `ProviderFeature.status` (`'read_only'` / `'supported'` / `'restricted'` /
 * `'planned'` / 등) → status SSOT.
 *
 * `read_only` reads as "absent" (the operator should understand "not editable
 * yet", not "broken"); `supported` is pass; `restricted` is the yellow
 * warning; any forward-compat status defaults to `'running'` (treat unknown
 * as in-flight rather than fail).
 *
 * Backend-side SSOT: `application/common/provider_ui_descriptor_schema.py::
 * PROVIDER_FEATURE_STATUS_*`. The cross-language mapping is sealed by
 * `tests/test_fe_phase2_followup.py::TestStatusMappingSsot`.
 */
export function featureStatusKind(status: string): StatusKind {
  const v = status.trim().toLowerCase();
  if (v === 'supported') return 'pass';
  if (v === 'restricted') return 'stale';
  if (v === 'read_only') return 'missing';
  // `planned` and any forward-compat status: treat as "future / in-flight".
  return 'running';
}

/** The canonical `ProviderFeature.status` tokens that have a localized label. */
const FEATURE_STATUS_TOKENS = ['supported', 'restricted', 'read_only', 'planned'] as const;

/** The i18n leaf token for an unknown / forward-compat feature status. */
const FEATURE_STATUS_UNKNOWN_TOKEN = 'unknown';

/**
 * Map a raw `ProviderFeature.status` to its i18n **leaf token** so the provider
 * "Test Types" screen renders a tester-readable label instead of the raw
 * backend token (R2). The route resolves it at render time:
 * ``t(`routes.providers.featureStatus.${featureStatusLabelToken(status)}`)``.
 * This is the label twin of {@link featureStatusKind} (which owns the badge
 * color). Trim + lowercase so casing / whitespace variants collapse; an unknown
 * status degrades to {@link FEATURE_STATUS_UNKNOWN_TOKEN}. Backend-side SSOT:
 * `application/common/provider_ui_descriptor_schema.py::PROVIDER_FEATURE_STATUS_*`.
 */
export function featureStatusLabelToken(status: string): string {
  const v = status.trim().toLowerCase();
  return (FEATURE_STATUS_TOKENS as readonly string[]).includes(v)
    ? v
    : FEATURE_STATUS_UNKNOWN_TOKEN;
}

/**
 * Measurement job status (`'queued'` / `'running'` / `'completed'` /
 * `'failed'` / `'cancelled'`) → status SSOT.
 *
 * - `completed` → pass (terminal success), `failed` → fail (terminal error,
 *   surfaced loud via the badge's `alert` role).
 * - `running` → running (in-flight).
 * - `queued` → stale (waiting, an informational `note` — not yet in-flight).
 * - `cancelled` → missing (operator-stopped / absent, like a removed artifact).
 * - any forward-compat status defaults to `stale`: an unknown *job* status is
 *   informational (unlike a live WS channel, it carries no liveness risk), so
 *   we surface it as a neutral note rather than a loud `fail`.
 *
 * Trim + lowercase so capitalisation / whitespace variants collapse to the
 * canonical token. Backend-side SSOT:
 * `src/domain/models/execution_job.py::MeasurementJobStatus`.
 */
export function jobStatusToStatusKind(status: string): StatusKind {
  const v = status.trim().toLowerCase();
  if (v === 'completed') return 'pass';
  if (v === 'failed') return 'fail';
  if (v === 'running') return 'running';
  if (v === 'cancelled') return 'missing';
  // `queued` and any forward-compat status: informational waiting state.
  return 'stale';
}

/**
 * The canonical measurement-queue status tokens that have a localized label.
 * The single SSOT for "which queue statuses are known" — both the
 * report-automation request status and the measurement job status share this
 * vocabulary (queued/running/completed/failed/cancelled).
 *
 * Exported (W3-6 M3, 2026-07-31) because the report-request *cancel* affordance
 * needs the set a status can be drawn from at all. `reports.tsx` already owns
 * the TERMINAL subset; deriving "cancellable = known − terminal" from this list
 * keeps the two sets provably complementary. Enumerating the cancellable
 * statuses by hand instead would be a THIRD copy of one vocabulary, and the day
 * the backend adds a queue state the three copies disagree — silently, because
 * each one alone still looks self-consistent.
 */
export const QUEUE_STATUS_TOKENS = [
  'queued',
  'running',
  'completed',
  'failed',
  'cancelled',
] as const;

/** The i18n leaf token for an unknown / forward-compat queue status. */
const QUEUE_STATUS_UNKNOWN_TOKEN = 'unknown';

/**
 * Map a raw measurement-queue status (`'queued'` / `'running'` / `'completed'`
 * / `'failed'` / `'cancelled'`) to its i18n **leaf token** so a route renders a
 * localized label instead of the raw backend token (R5). The route prefixes its
 * own namespace and resolves the live locale at render time, e.g.
 * ``t(`routes.reports.stats.${queueStatusLabelToken(status)}`)`` — so the
 * request-detail badge reuses the SAME stats keys the metric strip already uses
 * (no near-duplicate label set), and the measurement-jobs badge / filter reuse
 * ``routes.jobs.counts.*``.
 *
 * Trim + lowercase so casing / whitespace variants from the backend collapse to
 * the canonical token; an unknown / forward-compat status degrades to
 * {@link QUEUE_STATUS_UNKNOWN_TOKEN} (`'unknown'`) so the raw token never
 * surfaces. This is the label twin of {@link jobStatusToStatusKind} (which owns
 * the badge *color*). Backend-side SSOT:
 * `src/domain/models/execution_job.py::MeasurementJobStatus`.
 */
export function queueStatusLabelToken(status: string): string {
  const v = status.trim().toLowerCase();
  return (QUEUE_STATUS_TOKENS as readonly string[]).includes(v) ? v : QUEUE_STATUS_UNKNOWN_TOKEN;
}

/**
 * Chamber availability status → status SSOT (멀티챔버 P6 / P10). This single
 * object is the one place a canonical chamber status token is paired with its
 * badge kind; both {@link KNOWN_CHAMBER_STATUSES} ("which statuses have a
 * localized label") and {@link chamberStatusKind} ("which badge color") derive
 * from it, so the known-set and the color map can never drift apart.
 *
 * - `idle` → pass: the chamber is up and free to start a measurement.
 * - `in_use` → running: a measurement is in-flight on the node.
 * - `offline` → missing: no fresh heartbeat (last_heartbeat + TTL elapsed) — the
 *   node is unreachable/absent, surfaced like a removed artifact rather than a
 *   loud failure (an idle node simply went quiet; the operator picks another).
 *
 * Backend-side SSOT: `src/domain/models/chamber_node.py::ChamberNodeStatus`.
 */
const CHAMBER_STATUS_KINDS = {
  idle: 'pass',
  in_use: 'running',
  offline: 'missing',
} as const satisfies Record<string, StatusKind>;

/** Badge kind for any status the backend forward-compat may add later: an
 *  unknown chamber status is informational, not a loud failure. */
const CHAMBER_STATUS_FALLBACK_KIND: StatusKind = 'stale';

/**
 * Map a chamber availability status to its status SSOT badge kind. Trim +
 * lowercase so capitalisation / whitespace variants collapse to the canonical
 * token; an unknown status degrades to the neutral {@link
 * CHAMBER_STATUS_FALLBACK_KIND}. Derived from {@link CHAMBER_STATUS_KINDS}.
 */
export function chamberStatusKind(status: string): StatusKind {
  const v = status.trim().toLowerCase();
  return (CHAMBER_STATUS_KINDS as Record<string, StatusKind>)[v] ?? CHAMBER_STATUS_FALLBACK_KIND;
}

/**
 * The canonical chamber availability statuses that have a localized label
 * (`routes.chambers.status.{idle|in_use|offline}`). Derived from the
 * {@link CHAMBER_STATUS_KINDS} keys (not a separately-owned literal list) — the
 * single token set so the route does not scatter status literals while deciding
 * "known vs unknown" for the fallback label.
 */
export const KNOWN_CHAMBER_STATUSES = Object.keys(
  CHAMBER_STATUS_KINDS,
) as readonly (keyof typeof CHAMBER_STATUS_KINDS)[];

/**
 * True when `status` (trimmed + lowercased) is one of the canonical chamber
 * statuses with a localized label. A forward-compat status the backend may add
 * later returns `false`, so the route renders a generic "unknown status"
 * fallback (echoing the raw value) instead of a missing-key string — P10 status
 * fallback. The badge *color* still degrades gracefully via
 * {@link chamberStatusKind} (unknown → `stale`).
 */
export function isKnownChamberStatus(status: string): boolean {
  const v = status.trim().toLowerCase();
  return (KNOWN_CHAMBER_STATUSES as readonly string[]).includes(v);
}

/**
 * Any live-channel connection lifecycle the UI renders.
 *
 * The session WS (`/session/events`) and the chamber progress relay
 * (`/platform/chambers/events`) declare the identical four tokens in their own
 * modules — deliberately, because each is a mirror of its own backend contract
 * and neither should import the other's. This union is where the two meet for
 * *presentation* (fe-w2-b-execution-freshness M3, 2026-07-28), so one connection
 * state cannot acquire two names depending on which screen shows it.
 *
 * Widening the parameter — rather than adding a second `chamberStreamStatusKind`
 * — is the point: a second function is exactly how the two screens would drift.
 */
export type StreamStatus = SessionStreamStatus | ChamberStreamStatus;

/**
 * Stream status (`'open'` / `'connecting'` / `'reconnecting'` / `'closed'`) →
 * status SSOT.
 *
 * `open` is pass (live channel); `connecting` / `reconnecting` are
 * in-flight (running); `closed` is missing; any forward-compat value is
 * `fail` (we cannot vouch for an unknown state on a live channel — be loud).
 * Note the fallback differs from every *data* mapping above, which degrade to
 * the neutral `stale`: an unknown job status is informational, an unknown state
 * on a channel the operator is trusting for liveness is not.
 *
 * Backend-side SSOT: `application/session/event_stream.py::SESSION_STREAM_*`
 * and the chamber relay's `ChamberStreamStatus` mirror.
 */
export function streamStatusKind(status: StreamStatus): StatusKind {
  if (status === 'open') return 'pass';
  if (status === 'connecting' || status === 'reconnecting') return 'running';
  if (status === 'closed') return 'missing';
  return 'fail';
}

/** The canonical stream lifecycle tokens that have a localized label. */
const STREAM_STATUS_TOKENS = [
  'connecting',
  'open',
  'reconnecting',
  'closed',
] as const satisfies readonly StreamStatus[];

/** The i18n leaf token for an unknown / forward-compat stream status. */
const STREAM_STATUS_UNKNOWN_TOKEN = 'unknown';

/**
 * Map a stream status to its i18n **leaf token** so a route renders a localized
 * label instead of the raw backend token. The route resolves it at render time:
 * ``t(`ui.streamStatus.${streamStatusLabelToken(status)}`)``.
 *
 * This is the label twin of {@link streamStatusKind} (which owns the badge
 * colour) — the same pairing `featureStatus*` and `queueStatus*` already use.
 * It exists because `/control` labelled its badge with the RAW status, so a
 * Korean operator read "reconnecting" on an otherwise Korean screen; copying
 * that habit onto the chamber surface would have put two vocabularies on one
 * state. An unknown status degrades to {@link STREAM_STATUS_UNKNOWN_TOKEN} so
 * the raw token never reaches the screen.
 */
export function streamStatusLabelToken(status: StreamStatus): string {
  return (STREAM_STATUS_TOKENS as readonly string[]).includes(status)
    ? status
    : STREAM_STATUS_UNKNOWN_TOKEN;
}

/**
 * Project lifecycle status (`'active'` / `'completed'`) → status SSOT
 * (PM·RF Phase A, 2026-06-23). The central `projects.status` token domain is
 * `active | completed` (English tokens stored in the DB; the Korean
 * 진행중/완료 labels live only in i18n). This maps the token onto an EXISTING
 * `StatusKind` — no new status kind is added (StatusBadge / global.css status
 * SSOT stays frozen):
 *
 * - `active`    → running: the project is in-flight (a measurement campaign is
 *   ongoing), surfaced like any other live/in-progress state.
 * - `completed` → pass: the project's campaign is finished (terminal success).
 * - any forward-compat / unset status defaults to `stale`: an unknown lifecycle
 *   state is a neutral informational note (mirror of the draft/job/chamber
 *   fallback convention), NOT a loud failure.
 *
 * Trim + lowercase so capitalisation / whitespace variants collapse to the
 * canonical token. Backend-side SSOT: central `projects.status`
 * (docs/platform/central_db_schema.v1.json) — default `'active'` on create.
 */
export function projectStatusKind(status: string | null | undefined): StatusKind {
  const v = (status ?? '').trim().toLowerCase();
  if (v === 'active') return 'running';
  if (v === 'completed') return 'pass';
  return 'stale';
}

/**
 * Test-plan draft lifecycle status (`'draft'` / `'published'` / `'archived'`)
 * → status SSOT. c3-status-kind (2026-06-17).
 *
 * Before this bridge `test-plans.tsx` *borrowed* `stale`/`pass`:
 * `publishable ? 'stale' : 'pass'`. That conflated two unrelated axes — a
 * DRAFT is not "stale measurement data" and a PUBLISHED plan is not a
 * measurement "pass" (and ARCHIVED collapsed into "pass" too, mislabelling a
 * retired plan as a success). The lifecycle now has first-class kinds:
 *
 * - `draft`     → draft: editable, in authoring (its own neutral-cool token).
 * - `published` → published: released / locked terminal artifact.
 * - `archived`  → missing: retired / withdrawn, surfaced like a removed
 *   artifact (the same neutral grey `cancelled` jobs and `offline` chambers
 *   use) — distinct from a live published plan.
 * - any forward-compat status defaults to `stale`: an unknown lifecycle state
 *   is an informational note, mirroring the {@link jobStatusToStatusKind} /
 *   {@link chamberStatusKind} fallback convention.
 *
 * Trim + lowercase so capitalisation / whitespace variants from the backend
 * collapse to the canonical token. Backend-side SSOT:
 * `src/domain/models/test_plan_draft.py::DraftStatus`.
 */
export function draftStatusKind(status: string): StatusKind {
  const v = status.trim().toLowerCase();
  if (v === 'draft') return 'draft';
  if (v === 'published') return 'published';
  if (v === 'archived') return 'missing';
  // Forward-compat lifecycle state: neutral informational note.
  return 'stale';
}
