/**
 * MeasurementValueCell — value + unit split renderer (FD-C).
 *
 * Phase 1 §5.1 primitive. The today `sessions.tsx:252` renders
 * `result1 ?? ''` as one cell — when the value is `"22.0 dBm"` the trailing
 * unit prevents tabular-nums + right-align from producing an Excel-like
 * digit column. This primitive separates the numeric prefix from the unit
 * suffix so digits live on a tabular grid and the unit floats to the right
 * with a softer color.
 *
 * Strategy:
 * - If both `value` (numeric / numeric-string) and an explicit `unit` are
 *   passed, render them in two spans.
 * - If only one combined string is passed (no `unit`), best-effort split a
 *   leading numeric prefix from a trailing unit. On failure (no numeric
 *   prefix or empty value), the whole string is rendered as the numeric
 *   span so display never breaks.
 */
export interface MeasurementValueCellProps {
  /** Raw measurement value — `number`, numeric string ("22.0"), or a
   *  combined "22.0 dBm" string. `null`/`undefined`/empty render as `—`. */
  readonly value: number | string | null | undefined;
  /** Explicit unit ("dBm", "MHz", ...). When supplied, no fallback parse
   *  runs. */
  readonly unit?: string;
}

const NUMERIC_PREFIX_RE = /^\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(.*)$/u;
const EMPTY_DASH = '—';

/** Split a combined string into [numeric prefix, unit suffix]. Returns
 *  `null` when no numeric prefix is found. Exported for unit testing. */
export function splitValueUnit(input: string): { number: string; unit: string } | null {
  const match = NUMERIC_PREFIX_RE.exec(input);
  if (match === null) return null;
  const numeric = match[1] ?? '';
  const remainder = (match[2] ?? '').trim();
  if (numeric === '') return null;
  return { number: numeric, unit: remainder };
}

export function MeasurementValueCell({ value, unit }: MeasurementValueCellProps): JSX.Element {
  if (value === null || value === undefined) {
    return (
      <span className="measurement-value" data-testid="measurement-value" data-empty="true">
        <span className="measurement-value__number">{EMPTY_DASH}</span>
      </span>
    );
  }

  if (typeof value === 'number') {
    return (
      <span className="measurement-value" data-testid="measurement-value">
        <span className="measurement-value__number">{String(value)}</span>
        {unit !== undefined && unit !== '' && (
          <span className="measurement-value__unit" data-testid="measurement-value-unit">
            {unit}
          </span>
        )}
      </span>
    );
  }

  if (value === '') {
    return (
      <span className="measurement-value" data-testid="measurement-value" data-empty="true">
        <span className="measurement-value__number">{EMPTY_DASH}</span>
      </span>
    );
  }

  if (unit !== undefined) {
    return (
      <span className="measurement-value" data-testid="measurement-value">
        <span className="measurement-value__number">{value}</span>
        {unit !== '' && (
          <span className="measurement-value__unit" data-testid="measurement-value-unit">
            {unit}
          </span>
        )}
      </span>
    );
  }

  const parsed = splitValueUnit(value);
  if (parsed === null) {
    return (
      <span className="measurement-value" data-testid="measurement-value">
        <span className="measurement-value__number">{value}</span>
      </span>
    );
  }

  return (
    <span className="measurement-value" data-testid="measurement-value">
      <span className="measurement-value__number">{parsed.number}</span>
      {parsed.unit !== '' && (
        <span className="measurement-value__unit" data-testid="measurement-value-unit">
          {parsed.unit}
        </span>
      )}
    </span>
  );
}

export default MeasurementValueCell;
