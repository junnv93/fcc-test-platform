/**
 * ProgressBar — token-driven WAI-ARIA progress indicator (A2 fe-progress-bar,
 * 2026-06-17).
 *
 * A pure presentational primitive: it renders a `role="progressbar"` track with
 * the full ARIA value contract and a token-driven fill. It owns NO data — the
 * caller (e.g. the chambers progress UI) feeds it the already-fetched snapshot
 * from the C1/C2 polling boundary, so this primitive never opens a fetch/query
 * path of its own.
 *
 * Two states (architecture plan §5.0-5 motion restraint):
 *   - **determinate** — `aria-valuenow` is set and the fill width tracks the
 *     value within [`valueMin`, `valueMax`]; assistive tech announces the
 *     percentage it derives from valuenow/min/max.
 *   - **indeterminate** — `aria-valuenow` is OMITTED (per the ARIA spec, an
 *     absent valuenow signals "in progress, amount unknown"); a `valueText`
 *     becomes `aria-valuetext` so AT still announces a meaningful state. The
 *     animated sweep consumes `--motion-emphasis`, so the
 *     `prefers-reduced-motion: reduce` cascade (which zeroes every motion token)
 *     collapses it to a static bar with no per-component guard.
 *
 * All colour/spacing/motion comes from `global.css` tokens (`--accent`,
 * `--status-*`, `--space-*`, `--motion-*`) — no inline hex (sealed by
 * `tests/test_fe_phase1_ui_foundation.py::TestPrimitivesNoInlineHexColors`).
 */

/** Colour tone — maps to the accent or a status-SSOT foreground token. */
export type ProgressTone = 'accent' | 'running' | 'pass' | 'stale';

export interface ProgressBarProps {
  /** Accessible name for the progressbar (required for a11y). */
  readonly label: string;
  /** Current value. Omit, pass `undefined`, or set `indeterminate` for the
   *  indeterminate state (the `| undefined` is intentional — an explicit
   *  `undefined` is the documented indeterminate signal under
   *  `exactOptionalPropertyTypes`). */
  readonly valueNow?: number | undefined;
  /** Lower bound of the value range (default 0). */
  readonly valueMin?: number;
  /** Upper bound of the value range (default 100). */
  readonly valueMax?: number;
  /** Human caption rendered beside the bar (aria-hidden) and used as
   *  `aria-valuetext` in the indeterminate state. */
  readonly valueText?: string;
  /** Colour tone (default `accent`). */
  readonly tone?: ProgressTone;
  /** Force the indeterminate state. Defaults to `valueNow === undefined`. */
  readonly indeterminate?: boolean;
  readonly testId?: string;
}

/** Sanitize a public bound into a finite number. A non-finite `valueMin`/
 *  `valueMax` (`NaN`/`±Infinity`) collapses to its default, so the declared
 *  range that feeds `aria-valuemin`/`aria-valuemax` and the clamp math can never
 *  be `NaN`/`Infinity`. This is the bound half of the sanitize SSOT. */
function finiteBound(value: number, fallback: number): number {
  return Number.isFinite(value) ? value : fallback;
}

/** Sanitize a raw value into the [min, max] range. A non-finite value
 *  (`NaN`/`±Infinity`) or a degenerate range collapses to `min`, so neither the
 *  ARIA announcement nor the fill width can ever be `NaN` or escape the declared
 *  bounds. This is the single sanitize/clamp SSOT — both `aria-valuenow` and the
 *  fill width derive from its result, so they can never disagree. */
function clampValue(value: number, min: number, max: number): number {
  if (!Number.isFinite(value) || max <= min) return min;
  return Math.max(min, Math.min(max, value));
}

/** Map an already-clamped value to a 0–100 fill percentage. */
function toPercent(clamped: number, min: number, max: number): number {
  if (max <= min) return 0;
  return ((clamped - min) / (max - min)) * 100;
}

export function ProgressBar({
  label,
  valueNow,
  valueMin = 0,
  valueMax = 100,
  valueText,
  tone = 'accent',
  indeterminate,
  testId,
}: ProgressBarProps): JSX.Element {
  const isIndeterminate = indeterminate ?? valueNow === undefined;
  // Sanitize the public bounds first — a non-finite valueMin/valueMax must not
  // leak into aria-valuemin/aria-valuemax nor poison the clamp math.
  const min = finiteBound(valueMin, 0);
  const max = finiteBound(valueMax, 100);
  // Single sanitized source — `aria-valuenow` and the fill width both derive
  // from `clampedNow`, so a `NaN`/`Infinity`/out-of-range input can never make
  // the announcement and the visual disagree (nor leave [min, max]).
  const clampedNow =
    isIndeterminate || valueNow === undefined ? undefined : clampValue(valueNow, min, max);
  const pct = clampedNow === undefined ? undefined : toPercent(clampedNow, min, max);

  const toneClass = tone === 'accent' ? '' : ` progress-bar--${tone}`;
  const stateClass = isIndeterminate ? ' progress-bar--indeterminate' : '';

  return (
    <div
      className={`progress-bar${toneClass}${stateClass}`}
      data-testid={testId ?? 'progress-bar'}
      data-state={isIndeterminate ? 'indeterminate' : 'determinate'}
    >
      <div
        className="progress-bar__track"
        role="progressbar"
        aria-label={label}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={clampedNow}
        aria-valuetext={isIndeterminate ? valueText : undefined}
      >
        <div
          className="progress-bar__fill"
          style={pct !== undefined ? { inlineSize: `${pct}%` } : undefined}
        />
      </div>
      {valueText !== undefined && (
        <span className="progress-bar__value" aria-hidden="true">
          {valueText}
        </span>
      )}
    </div>
  );
}

export default ProgressBar;
