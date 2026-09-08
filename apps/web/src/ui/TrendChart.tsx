import { useId } from 'react';

/**
 * A two-series area trend, drawn as inline SVG (flowdeck-console-ui, R9/D6).
 *
 * ⚠️ No charting library, deliberately. Recharts/Chart.js would each bring a
 * second theming system beside `global.css`, and this widget needs exactly one
 * shape: two stacked-behind areas over a shared x axis. The cost of the library
 * is not the bundle alone — it is that colour would then have two sources of
 * truth, and `tests/design-token-conformance.test.ts` can only see one of them.
 *
 * Colour comes from CSS classes (`currentColor` on the path), never from a
 * prop: a caller passing `#4fd1a5` would be exactly the inline hex the gate
 * forbids, moved one level up where the gate cannot read it.
 *
 * ⚠️ An SVG chart is decoration to assistive tech no matter how it is marked
 * up — a path has no readable trend. So the series totals are rendered as a
 * visually-hidden summary and the graphic itself is `aria-hidden`. The chart
 * shows the SHAPE to people who can see it; the text carries the FACT to
 * everyone.
 */

export interface TrendSeries {
  readonly id: string;
  readonly label: string;
  readonly tone: 'accent' | 'bad';
  readonly points: readonly number[];
}

export interface TrendChartProps {
  readonly series: readonly TrendSeries[];
  /** x-axis tick labels, evenly spaced. Length need not match `points`. */
  readonly ticks: readonly string[];
  /** Localised summary sentence for the visually-hidden description. */
  readonly summary: string;
  readonly testId?: string;
}

const VIEW_W = 720;
const VIEW_H = 200;
const PAD_TOP = 12;
const PAD_BOTTOM = 24;

/** Smooth-ish polyline: a Catmull-Rom-to-Bezier would be nicer, but a plain
 *  polyline is honest about the sampling and never overshoots below zero —
 *  an area that dips under the baseline reads as a negative count. */
function pathFor(points: readonly number[], max: number): { line: string; area: string } {
  if (points.length === 0) return { line: '', area: '' };
  const plotH = VIEW_H - PAD_TOP - PAD_BOTTOM;
  const step = points.length > 1 ? VIEW_W / (points.length - 1) : VIEW_W;
  const xy = points.map((p, i) => {
    const x = i * step;
    const y = PAD_TOP + plotH * (1 - (max === 0 ? 0 : p / max));
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const line = `M ${xy.join(' L ')}`;
  const baseline = VIEW_H - PAD_BOTTOM;
  const first = xy[0] ?? '0,0';
  const lastX = ((points.length - 1) * step).toFixed(1);
  return {
    line,
    area: `${line} L ${lastX},${baseline} L ${first.split(',')[0] ?? '0'},${baseline} Z`,
  };
}

export function TrendChart({ series, ticks, summary, testId }: TrendChartProps): JSX.Element {
  const gradientBase = useId();
  // One shared maximum so the two series are comparable by eye. Per-series
  // scaling would make a failure count of 3 look as tall as a completion count
  // of 300 — the most common way a chart lies.
  const max = series.reduce((m, s) => s.points.reduce((n, p) => (p > n ? p : n), m), 0);

  return (
    <figure className="trend-chart" data-testid={testId ?? 'trend-chart'}>
      <figcaption className="sr-only">{summary}</figcaption>
      <svg
        className="trend-chart__svg"
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="none"
        aria-hidden="true"
        focusable="false"
      >
        <defs>
          {series.map((s) => (
            <linearGradient
              id={`${gradientBase}-${s.id}`}
              key={s.id}
              x1="0"
              y1="0"
              x2="0"
              y2="1"
            >
              <stop offset="0%" stopColor="currentColor" stopOpacity="0.28" />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </linearGradient>
          ))}
        </defs>
        {series.map((s) => {
          const { line, area } = pathFor(s.points, max);
          return (
            <g className="trend-chart__series" data-tone={s.tone} key={s.id}>
              <path className="trend-chart__area" d={area} fill={`url(#${gradientBase}-${s.id})`} />
              <path className="trend-chart__line" d={line} />
            </g>
          );
        })}
      </svg>
      <div className="trend-chart__ticks mono" aria-hidden="true">
        {ticks.map((tick) => (
          <span key={tick}>{tick}</span>
        ))}
      </div>
      <ul className="trend-chart__legend">
        {series.map((s) => (
          <li className="trend-chart__legend-item" data-tone={s.tone} key={s.id}>
            <span className="trend-chart__swatch" aria-hidden="true" />
            {s.label}
          </li>
        ))}
      </ul>
    </figure>
  );
}
