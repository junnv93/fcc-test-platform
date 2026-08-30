/**
 * Percent → display-string SSOT (fe-w2-a-result-report-honesty M1, 2026-07-28).
 *
 * Before this module every consumer formatted a progress percentage itself with
 * `Math.round(percent)` / `percent.toFixed(0)`. Arithmetically correct, and
 * *semantically dishonest at the two boundaries that carry meaning*:
 *
 *   - `99.6` → `"100%"` — a run with work left announces itself as finished.
 *   - `0.4`  → `"0%"`   — a run that has started announces itself as untouched
 *                          (while the very same row's bar tone says "running",
 *                          so the surface contradicts itself).
 *
 * In a regulatory test tool the worst failure mode is not a missing feature but
 * a screen that asserts something untrue, so the boundary is not a rounding
 * preference — it is a correctness rule:
 *
 *   P1  a "complete" rendering ⟺ `percent >= 100`
 *   P2  an "unstarted" rendering ⟺ `percent <= 0`
 *   P3  every `0 < v < 100` renders as *started and not finished*
 *   P4  monotone — `v1 <= v2` never renders as a lower progress
 *
 * Design — classify, then render:
 *
 *   `classifyPercent` is a pure, i18n-free decision (the honesty rule).
 *   `formatPercent` renders that decision through `t()` (the copy).
 *   `percentDisplayRank` projects a classification onto the real line so P4 is
 *   checkable as an ordering property rather than by string comparison.
 *
 * The rounding that remains lives *only* inside the interior band (1..99), so
 * it can never manufacture a boundary claim. `NaN` is neither complete nor
 * unstarted, so it gets its own arm ("unknown") rather than collapsing into one
 * of the two meaningful ends; `±Infinity` compares correctly against the
 * thresholds and needs no special case.
 *
 * Consumers MUST delegate — `tests/test_frontend_architecture_conformance.py::
 * TestPercentFormattingDelegatesToSsot` seals that no owned surface re-derives
 * a percentage string locally (P5).
 *
 * NOTE: this module governs the **displayed string** only. `ProgressBar`'s
 * `valueNow` (the aria `aria-valuenow`) stays the raw number — the accessibility
 * value axis keeps its numeric meaning.
 */
import { t } from '@/i18n';

/** At or above this, and only then, progress may be rendered as complete. */
export const PERCENT_COMPLETE_THRESHOLD = 100;
/** At or below this, and only then, progress may be rendered as unstarted. */
export const PERCENT_UNSTARTED_THRESHOLD = 0;
/**
 * The symbolic bands are *exactly* the values whose rounding would cross a
 * boundary — no wider, so nothing legible is thrown away. Below `0.5` rounds to
 * `0`; at or above `99.5` rounds to `100` (`Math.round` is half-up, so `99.5`
 * itself must be inside the band — using `> 99.5` would leak a literal `"100%"`).
 * Everything between rounds to `1..99` and renders as the plain number it is.
 */
export const PERCENT_LOW_BAND_BOUND = 0.5;
export const PERCENT_HIGH_BAND_BOUND = 99.5;

/**
 * The six honest renderings of a percentage.
 *
 * `belowLowBand` / `aboveHighBand` exist precisely because rounding into `0%` /
 * `100%` would cross a semantic boundary; they are the only members that are
 * NOT a plain number.
 */
export type PercentDisplayKind =
  | 'unknown'
  | 'unstarted'
  | 'belowLowBand'
  | 'exact'
  | 'aboveHighBand'
  | 'complete';

export interface PercentDisplay {
  readonly kind: PercentDisplayKind;
  /** The integer shown for the `exact` arm; `null` for every other arm (the
   *  boundary arms deliberately have no roundable number). */
  readonly roundedPercent: number | null;
}

/**
 * Classify a percentage into an honest display arm. Pure — no i18n, no DOM.
 *
 * Order of the guards is the specification: the two boundary equivalences (P1,
 * P2) are decided *before* any rounding can run, which is what makes them
 * equivalences rather than approximations.
 */
export function classifyPercent(percent: number): PercentDisplay {
  if (Number.isNaN(percent)) return { kind: 'unknown', roundedPercent: null };
  if (percent >= PERCENT_COMPLETE_THRESHOLD) {
    return { kind: 'complete', roundedPercent: null };
  }
  if (percent <= PERCENT_UNSTARTED_THRESHOLD) {
    return { kind: 'unstarted', roundedPercent: null };
  }
  if (percent < PERCENT_LOW_BAND_BOUND) {
    return { kind: 'belowLowBand', roundedPercent: null };
  }
  if (percent >= PERCENT_HIGH_BAND_BOUND) {
    return { kind: 'aboveHighBand', roundedPercent: null };
  }
  return { kind: 'exact', roundedPercent: Math.round(percent) };
}

/**
 * Project a classification onto the real line so monotonicity (P4) is a
 * checkable ordering rather than a string comparison. `unknown` has no place in
 * the order and yields `NaN` (excluded from the property).
 */
export function percentDisplayRank(display: PercentDisplay): number {
  switch (display.kind) {
    case 'unstarted':
      return PERCENT_UNSTARTED_THRESHOLD;
    case 'belowLowBand':
      // Strictly between "unstarted" (0) and the smallest `exact` rank (1).
      return PERCENT_LOW_BAND_BOUND;
    case 'exact':
      return display.roundedPercent ?? Number.NaN;
    case 'aboveHighBand':
      // Strictly between the largest `exact` rank (99) and "complete" (100).
      return PERCENT_HIGH_BAND_BOUND;
    case 'complete':
      return PERCENT_COMPLETE_THRESHOLD;
    default:
      return Number.NaN;
  }
}

/** i18n key per display arm. The copy (`<1%`, `>99%`, …) is never inlined —
 *  swapping the boundary wording is a locale-bundle edit, not a code edit. */
const MESSAGE_KEY_BY_KIND: Readonly<Record<PercentDisplayKind, string>> = {
  unknown: 'ui.percent.unknown',
  unstarted: 'ui.percent.unstarted',
  belowLowBand: 'ui.percent.belowLowBand',
  exact: 'ui.percent.exact',
  aboveHighBand: 'ui.percent.aboveHighBand',
  complete: 'ui.percent.complete',
};

/**
 * Render a percentage as the operator-facing display string.
 *
 * This is the single function every progress surface calls. It reads the live
 * locale at call time (same contract as `describeApiError`), so a locale switch
 * re-renders correctly without the caller threading `t` through.
 */
export function formatPercent(percent: number): string {
  const display = classifyPercent(percent);
  const key = MESSAGE_KEY_BY_KIND[display.kind];
  return display.kind === 'exact' ? t(key, { percent: display.roundedPercent ?? 0 }) : t(key);
}
