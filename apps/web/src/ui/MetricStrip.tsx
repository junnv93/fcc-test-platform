import type { ReactNode } from 'react';

/**
 * MetricStrip — compact metric row (queue stats, coverage summary, ...).
 *
 * Phase 1 §5.1 primitive. Replaces the ad-hoc `<dl>` row that today renders
 * report-queue stats (see `reports.tsx` `<dl className="reports-stats">`).
 * Each item shows an uppercased xs label and a tabular-nums lg value, so a
 * dashboard of 4-6 stats reads as a single horizontal scan band.
 */
export interface MetricStripItem {
  readonly key: string;
  readonly label: string;
  readonly value: ReactNode;
  /** Optional override `data-testid` for the value element so a per-stat
   *  test like `getByTestId('stat-queued')` keeps working when migrated. */
  readonly valueTestId?: string;
}

export interface MetricStripProps {
  readonly items: readonly MetricStripItem[];
  /** Region label — exposed via `aria-label` on the wrapping `<section>`. */
  readonly ariaLabel: string;
}

export function MetricStrip({ items, ariaLabel }: MetricStripProps): JSX.Element {
  return (
    <section className="metric-strip" aria-label={ariaLabel} data-testid="metric-strip">
      {items.map((item) => (
        <div key={item.key} className="metric-strip__item" data-testid="metric-strip-item">
          <span className="metric-strip__label">{item.label}</span>
          <span
            className="metric-strip__value"
            data-testid={item.valueTestId ?? 'metric-strip-value'}
          >
            {item.value}
          </span>
        </div>
      ))}
    </section>
  );
}

export default MetricStrip;
