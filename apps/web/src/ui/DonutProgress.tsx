import { useId } from 'react';

/**
 * A single ratio as a ring (flowdeck-console-ui, programme pass).
 *
 * ⚠️ ONE number only. A donut split into slices makes the reader compare arc
 * lengths, which people do badly — that is what the bars and the trend beside
 * it are for. Here the ring carries a single "how far along", and the figure in
 * the middle carries the same value in text so nothing depends on reading an
 * angle.
 *
 * The ring is the *state* instrument and the trend beside it is the *pace*
 * instrument. Keeping both is deliberate: "48% today" and "48% and climbing
 * three points a week" are different sentences, and a PM reports the second one
 * but is asked the first.
 *
 * ⚠️ The sweep is a gradient and the leading end carries a knob (2026-09-09,
 * from the reference the owner supplied). Both are decoration with a job: the
 * gradient makes a long arc legible as one continuous quantity rather than a
 * flat band, and the knob answers "where does it END" on a ring whose start and
 * end are otherwise the same stroke. Gradient stops take their colour from
 * `global.css`, not from literals here — inline hex in TSX is a sealed rule.
 *
 * Inline SVG rather than a chart library, for the same reason as the trend: a
 * dependency would bring a second theming system beside `global.css`, and
 * colour here must come from the same tokens the rest of the console uses.
 */

export interface DonutLegendItem {
  readonly id: string;
  /** Already-localised. */
  readonly label: string;
  /** `done` takes the sweep colour, `rest` the track colour. */
  readonly kind: 'done' | 'rest';
  /** The row's PRIMARY figure — on this console that is time, because progress
   *  is measured in time (a plan prices every condition in minutes). */
  readonly primary?: string;
  /** The SECONDARY figure on the same row, e.g. a case count. Sits beside the
   *  primary, not under it: a row that wraps stops reading as one fact.
   *
   *  ⚠️ Order is the argument. 100 of 100 cases done says "finished" while the
   *  remaining minutes say otherwise — the long radiated items are the ones
   *  left. Whichever number comes first is the one people quote, so time comes
   *  first and the count stands beside it as corroboration. */
  readonly secondary?: string;
}

export interface DonutProgressProps {
  /** 0…1. Clamped rather than rejected — a ratio of 1.0000001 from float
   *  division should not blank the widget. */
  readonly ratio: number;
  /** Already-localised centre figure, e.g. "62%". */
  readonly label: string;
  /** Already-localised caption under the figure. */
  readonly caption?: string;
  /** Already-localised heading above the ring. */
  readonly title?: string;
  readonly legend?: readonly DonutLegendItem[];
  readonly testId?: string;
}

const SIZE = 168;
const STROKE = 23;
const R = (SIZE - STROKE) / 2;
const C = 2 * Math.PI * R;

export function DonutProgress({
  ratio,
  label,
  caption,
  title,
  legend,
  testId,
}: DonutProgressProps): JSX.Element {
  const gid = useId().replace(/:/g, '');
  const value = ratio < 0 ? 0 : ratio > 1 ? 1 : ratio;

  /* The knob sits at the END of the sweep. Angles start at 12 o'clock, which is
     where the arc starts — at 3 o'clock a fresh ring would read as "already a
     quarter done". */
  const theta = (value * 360 - 90) * (Math.PI / 180);
  const knobX = SIZE / 2 + R * Math.cos(theta);
  const knobY = SIZE / 2 + R * Math.sin(theta);

  return (
    <div className="donut" data-testid={testId ?? 'donut'}>
      {title !== undefined && <h3 className="donut__title">{title}</h3>}
      <div className="donut__ring">
        <svg
          className="donut__svg"
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          role="meter"
          aria-valuemin={0}
          aria-valuemax={1}
          aria-valuenow={value}
          aria-valuetext={label}
        >
          <defs>
            {/* Diagonal so the two ends of the sweep differ most where the eye
                lands first. Stop colours are set in `global.css`. */}
            <linearGradient id={`${gid}-sweep`} x1="0" y1="1" x2="1" y2="0">
              <stop className="donut__stop donut__stop--from" offset="0%" />
              <stop className="donut__stop donut__stop--to" offset="100%" />
            </linearGradient>
          </defs>

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
              stroke={`url(#${gid}-sweep)`}
              strokeDasharray={`${C * value} ${C}`}
            />
          </g>

          {/* The knob is outside the rotated group because its position is
              already computed in screen angles. */}
          <circle
            className="donut__knob"
            cx={knobX}
            cy={knobY}
            r={STROKE / 2}
            strokeWidth={3.5}
          />
        </svg>

        {/* aria-hidden: the ring above already announces the value through
            `role="meter"`; repeating it here would read the number twice. */}
        <div className="donut__figure" aria-hidden="true">
          <strong className="donut__value-text">{label}</strong>
          {caption !== undefined && <span className="donut__caption">{caption}</span>}
        </div>
      </div>

      {legend !== undefined && legend.length > 0 && (
        <ul className="donut__legend">
          {legend.map((item) => (
            <li className="donut__legend-item" data-kind={item.kind} key={item.id}>
              <span className="donut__legend-dot" aria-hidden="true" />
              {item.label}
              {item.primary !== undefined && (
                <span className="donut__legend-value">{item.primary}</span>
              )}
              {item.secondary !== undefined && (
                <span className="donut__legend-time">{item.secondary}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
