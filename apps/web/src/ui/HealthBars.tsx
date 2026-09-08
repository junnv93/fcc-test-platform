/**
 * A ranked list of ratios, worst first (flowdeck-console-ui, R10/D5).
 *
 * ⚠️ The sort is not a preference — it is the component's job. This widget
 * answers "what is going wrong", and a list that makes the operator scroll to
 * find the problem has not answered it. Callers pass rows in any order; the
 * component ranks them. Passing pre-sorted rows and expecting them to stay put
 * is the one thing this will not do.
 *
 * The bar is `<div>` geometry rather than `<progress>`: `<progress>` carries an
 * implicit "task in flight" meaning and its bar colour is not reliably
 * themeable across engines. Semantics are supplied explicitly with role
 * `meter`, and the numeric value is ALSO printed as text — a bar length is not
 * readable to a screen reader, and reading it off pixels is not readable to
 * anyone.
 */

export type HealthTone = 'ok' | 'warn' | 'bad' | 'progress';

export interface HealthRow {
  readonly id: string;
  readonly label: string;
  /** 0…1. Values outside are clamped rather than rejected: a ratio arriving as
   *  1.0000001 from float division should not blank the widget. */
  readonly ratio: number;
  /** Secondary line — e.g. "126 runs". Optional; absent renders nothing rather
   *  than an empty row that shifts the rhythm. */
  readonly detail?: string;
}

export interface HealthBarsProps {
  readonly rows: readonly HealthRow[];
  /** Below `badBelow` a row reads as bad, below `warnBelow` as warning.
   *  Defaults are deliberately NOT magic numbers buried in the render: they are
   *  named parameters so a caller with a different standard can say so. */
  readonly warnBelow?: number;
  readonly badBelow?: number;
  readonly formatRatio: (ratio: number) => string;
  /** Paint every row with one tone instead of ranking it against thresholds.
   *
   *  ⚠️ This exists because a RATIO IS NOT ALWAYS A HEALTH SCORE. A measurement
   *  that is 29% done is early, not failing — the first draft of this screen
   *  painted it red and told the operator a lie. Progress and health share the
   *  geometry and share nothing else, so the caller declares which one it has.
   */
  readonly forceTone?: HealthTone;
  readonly testId?: string;
}

const clamp = (n: number): number => (n < 0 ? 0 : n > 1 ? 1 : n);

function toneFor(ratio: number, warnBelow: number, badBelow: number): HealthTone {
  if (ratio < badBelow) return 'bad';
  if (ratio < warnBelow) return 'warn';
  return 'ok';
}

export function HealthBars({
  rows,
  warnBelow = 0.95,
  badBelow = 0.85,
  formatRatio,
  forceTone,
  testId,
}: HealthBarsProps): JSX.Element {
  // Copy before sorting: `rows` is the caller's array and mutating a prop is a
  // bug that only shows up when the caller renders the same array elsewhere.
  const ranked = [...rows].sort((a, b) => a.ratio - b.ratio);

  return (
    <ul className="health-bars" data-testid={testId ?? 'health-bars'}>
      {ranked.map((row) => {
        const ratio = clamp(row.ratio);
        const tone = forceTone ?? toneFor(ratio, warnBelow, badBelow);
        return (
          <li className="health-bars__row" key={row.id} data-tone={tone}>
            <div className="health-bars__head">
              <span className="health-bars__label">{row.label}</span>
              <span className="health-bars__value mono">{formatRatio(ratio)}</span>
            </div>
            {row.detail !== undefined ? (
              <span className="health-bars__detail">{row.detail}</span>
            ) : null}
            <div
              className="health-bars__track"
              role="meter"
              aria-valuemin={0}
              aria-valuemax={1}
              aria-valuenow={ratio}
              aria-valuetext={formatRatio(ratio)}
              aria-label={row.label}
            >
              {/* Width is geometry, not colour/spacing styling — the tone class
                  owns every token-driven property. A percentage here is the
                  only way to express a data-driven length in CSS. */}
              <span className="health-bars__fill" style={{ width: `${ratio * 100}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
