import type { ReactNode } from 'react';

/**
 * A headline number with an honest provenance (flowdeck-console-ui, R8/D9).
 *
 * ⚠️ The `unavailable` variant is the POINT of this component, not a fallback.
 * `overview.tsx` records a standing judgement — *"Deliberately NOT shown (G3 —
 * require backend read contracts) … never faked from partial aggregates."* A
 * console layout invites a row of three tiles; the temptation is to fill the
 * third from whatever aggregate is at hand. This type makes that impossible to
 * do by accident: a tile either carries a `value`, or it carries a `reason`
 * saying which read contract is missing. There is no third shape.
 *
 * Delta is expressed THREE ways — arrow glyph, token colour, and the
 * `changeLabel` text — because colour alone fails WCAG 1.4.1 and this repo
 * gates accessibility as an error, not a warning.
 */

export type StatDirection = 'up' | 'down' | 'flat';

/** How a delta should READ, which is not how it points. A rising failure rate
 *  points up and is bad; a rising completion count points up and is good. The
 *  caller owns that judgement because only the caller knows the metric. */
export type StatSentiment = 'good' | 'bad' | 'neutral';

interface StatTileCommon {
  readonly label: string;
  readonly testId?: string;
}

interface StatTileValue extends StatTileCommon {
  readonly value: string;
  /** Omit entirely when there is nothing to compare against — an absent
   *  comparison is not a flat one, and drawing "0%" would claim a measurement
   *  that was never made. */
  readonly change?: {
    readonly direction: StatDirection;
    readonly sentiment: StatSentiment;
    /** Already-localised, e.g. "전일 대비 +9.4%". Carries the meaning for
     *  anyone who cannot use the colour or the glyph. */
    readonly label: string;
  };
  readonly unavailable?: never;
}

interface StatTileUnavailable extends StatTileCommon {
  readonly value?: never;
  readonly change?: never;
  /** Says WHICH contract is missing, not merely that data is absent. "아직
   *  데이터 없음" would be read as "nothing has happened yet"; the truth is
   *  that nothing can be read yet. Those call for different actions. */
  readonly unavailable: ReactNode;
}

export type StatTileProps = StatTileValue | StatTileUnavailable;

const ARROW: Record<StatDirection, string> = { up: '▲', down: '▼', flat: '■' };

export function StatTile(props: StatTileProps): JSX.Element {
  const { label, testId } = props;

  if (props.unavailable !== undefined) {
    return (
      <div className="stat-tile stat-tile--unavailable" data-testid={testId ?? 'stat-tile'}>
        <span className="stat-tile__label">{label}</span>
        <p className="stat-tile__unavailable">{props.unavailable}</p>
      </div>
    );
  }

  const { value, change } = props;
  return (
    <div className="stat-tile" data-testid={testId ?? 'stat-tile'}>
      <span className="stat-tile__label">{label}</span>
      <strong className="stat-tile__value mono">{value}</strong>
      {change !== undefined ? (
        <span
          className="stat-tile__change"
          data-sentiment={change.sentiment}
          data-direction={change.direction}
          data-testid={`${testId ?? 'stat-tile'}-change`}
        >
          {/* aria-hidden: the arrow is decoration layered on the label, which
              already says the direction in words. A screen reader announcing
              "▲" adds noise, not information. */}
          <span aria-hidden="true">{ARROW[change.direction]}</span> {change.label}
        </span>
      ) : null}
    </div>
  );
}
