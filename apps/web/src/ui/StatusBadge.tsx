/**
 * StatusBadge — status SSOT consumer (7 base + draft/published lifecycle kinds).
 *
 * Phase 1 §5.0-4 + §5.1 primitive. The status set is sealed: the TS literal
 * union, the icon glyph map, and the default Korean label set must agree
 * with the `--status-{name}-fg/bg` CSS tokens in `global.css` (sealed by
 * `tests/test_fe_phase1_ui_foundation.py`).
 *
 * c3-status-kind (2026-06-17): `draft` / `published` joined the SSOT as
 * first-class kinds so the test-plan lifecycle stops *borrowing* `stale`/`pass`
 * semantics (a draft is not "stale data", a published plan is not a measurement
 * "pass"). The draft→published lifecycle bridge lives in `draftStatusKind()`.
 *
 * §5.2 anti-pattern: color alone never conveys status — the badge always
 * renders an icon (decorative — `aria-hidden`) AND a visible label, so the
 * verdict survives without color and without a live region.
 */
import { useT } from '@/i18n';

import { liveRegionProps } from './live-region';

/** The status SSOT — must match `--status-{name}-*` tokens in global.css. */
export type StatusKind =
  | 'pass'
  | 'fail'
  | 'running'
  | 'stale'
  | 'missing'
  | 'claimed'
  | 'duplicate'
  | 'draft'
  | 'published';

export const STATUS_KINDS: readonly StatusKind[] = [
  'pass',
  'fail',
  'running',
  'stale',
  'missing',
  'claimed',
  'duplicate',
  'draft',
  'published',
] as const;

/** Decorative glyphs. Rendered with `aria-hidden="true"` because the label
 *  text is the accessible name (avoids screen-reader double-readout). */
const STATUS_ICON: Readonly<Record<StatusKind, string>> = {
  pass: '✓', // ✓
  fail: '✕', // ✕
  running: '◐', // ◐
  stale: 'ⓘ', // ⓘ
  missing: '⊘', // ⊘
  claimed: '🔒', // 🔒
  duplicate: '⚠', // ⚠
  draft: '◇', // ◇ — hollow diamond: not yet finalized
  published: '◆', // ◆ — filled diamond: released / locked
};

/** ARIA role — `staticLabel` for every kind (W4-A M4).
 *
 *  This used to be a per-status map where `fail` was `alert` and `running` was
 *  `status`. That reads sensibly for ONE badge and is a defect for the way
 *  badges are actually used: a measurement table renders one per row, so a
 *  page of failures created a page of ASSERTIVE live regions, each one able to
 *  interrupt the operator on any re-render. A live region has to be a single
 *  stable node whose CONTENT changes; a per-row label is not that. The verdict
 *  is still announced — it is visible text with an accessible name, and the
 *  screens that need to announce a change (`ErrorState`, `StatusMessage`,
 *  route notices) own live regions of their own. Ruling: `./live-region`. */
const BADGE_LIVE_REGION = liveRegionProps('staticLabel');

export interface StatusBadgeProps {
  readonly status: StatusKind;
  /** Override the default Korean label — useful when the surrounding copy
   *  already conveys the status verb. */
  readonly label?: string;
  /** Override `data-testid` so routes can wire a stable id when several
   *  badges coexist in one screen (e.g. `tech-cross-duplicate` next to
   *  `condition-claimed`). Defaults to `status-badge`. */
  readonly testId?: string;
  /** Override `title` attribute for hover tooltip context. */
  readonly title?: string;
  /** Size variant — `'lg'` renders a prominent badge for a headline verdict
   *  (e.g. a condition group's overall 합/부, §5⑤). Defaults to the compact
   *  inline size used inside tables. */
  readonly size?: 'md' | 'lg';
}

export function StatusBadge({
  status,
  label,
  testId,
  title,
  size = 'md',
}: StatusBadgeProps): JSX.Element {
  const { t } = useT();
  const text = label ?? t(`ui.statusBadge.${status}`);
  return (
    <span
      className={`status-badge status-badge--${status}${size === 'lg' ? ' status-badge--lg' : ''}`}
      {...BADGE_LIVE_REGION}
      data-status={status}
      data-testid={testId ?? 'status-badge'}
      title={title}
    >
      <span className="status-badge__icon" aria-hidden="true">
        {STATUS_ICON[status]}
      </span>
      <span className="status-badge__label">{text}</span>
    </span>
  );
}

export default StatusBadge;
