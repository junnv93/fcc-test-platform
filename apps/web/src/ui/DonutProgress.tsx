/**
 * A single ratio as a ring (flowdeck-console-ui, programme pass).
 *
 * ⚠️ Used for ONE number only. A donut split into slices makes the reader
 * compare arc lengths, which people do badly — that is what the bars beside it
 * are for. Here the ring carries a single "how far along", and the figure in
 * the middle carries the same value in text so nothing depends on reading an
 * angle.
 *
 * Inline SVG rather than a chart library, for the same reason as `TrendChart`:
 * a dependency would bring a second theming system beside `global.css`, and
 * colour here must come from the same tokens the rest of the console uses.
 */

export interface DonutProgressProps {
  /** 0…1. Clamped rather than rejected — a ratio of 1.0000001 from float
   *  division should not blank the widget. */
  readonly ratio: number;
  /** Already-localised centre figure, e.g. "62%". */
  readonly label: string;
  /** Already-localised caption under the figure. */
  readonly caption?: string;
  readonly testId?: string;
}

const SIZE = 132;
const STROKE = 14;
const R = (SIZE - STROKE) / 2;
const C = 2 * Math.PI * R;

export function DonutProgress({ ratio, label, caption, testId }: DonutProgressProps): JSX.Element {
  const value = ratio < 0 ? 0 : ratio > 1 ? 1 : ratio;

  return (
    <div className="donut" data-testid={testId ?? 'donut'}>
      <svg
        className="donut__svg"
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        role="meter"
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={value}
        aria-valuetext={label}
      >
        {/* Rotated so the arc starts at 12 o'clock. Without this it starts at
            3 o'clock, which reads as "already a quarter done". */}
        <g transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}>
          <circle
            className="donut__track"
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            strokeWidth={STROKE}
            fill="none"
          />
          <circle
            className="donut__value"
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            strokeWidth={STROKE}
            fill="none"
            strokeLinecap="round"
            strokeDasharray={`${C * value} ${C}`}
          />
        </g>
      </svg>
      {/* aria-hidden: the ring above already announces the value through
          `role="meter"`; repeating it here would read the number twice. */}
      <div className="donut__figure" aria-hidden="true">
        <strong className="donut__value-text mono">{label}</strong>
        {caption !== undefined && <span className="donut__caption">{caption}</span>}
      </div>
    </div>
  );
}
