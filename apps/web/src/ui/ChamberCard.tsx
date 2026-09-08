import { StatusBadge } from './StatusBadge';

import type { StatusKind } from './StatusBadge';
import type { ReactNode } from 'react';

/**
 * One chamber as a block (flowdeck-console-ui, monitoring pass).
 *
 * ⚠️ A chamber is a ROOM, not a row. The table this replaces made five physical
 * bays read as five records: to answer "which bay can I walk to right now" the
 * operator had to scan a status column and map it back to a place. A card holds
 * one chamber's whole state in one glance-sized block, and a grid of them keeps
 * the fleet's shape — which is what the operator actually carries in their head.
 *
 * ⚠️ Deliberately actionless. The home screen watches; it does not start, stop
 * or edit anything. Buttons here would put irreversible operations one stray
 * click from a screen people leave open on a wall display, and they would make
 * the card's meaning depend on the viewer's permissions. Navigation to the
 * chamber's own surface belongs to the nav, not to the monitor.
 *
 * The accent rail across the top is toned by status rather than by a per-chamber
 * colour: this fleet's cards must be comparable at a glance, and a palette that
 * says "this is chamber B" competes with the palette that says "this one is
 * offline".
 */

export interface ChamberCardProps {
  readonly name: string;
  readonly chamberId: string;
  readonly status: StatusKind;
  /** True while a measurement is in flight — drives the LED and the rail sweep. */
  readonly running: boolean;
  /** 0…1, or null when the chamber reports no progress (idle nodes do not). */
  readonly ratio: number | null;
  /** Already-localised, e.g. "7 / 24 조건 완료". */
  readonly progressLabel?: string;
  /** Already-localised relative age, e.g. "12초 전". */
  readonly heartbeatLabel: string;
  readonly address: string;
  /** Localised label for the disabled/unavailable note, when there is one. */
  readonly note?: ReactNode;
  readonly testId?: string;
}

export function ChamberCard({
  name,
  chamberId,
  status,
  running,
  ratio,
  progressLabel,
  heartbeatLabel,
  address,
  note,
  testId,
}: ChamberCardProps): JSX.Element {
  return (
    <article
      className="chamber-card"
      data-status={status}
      data-running={running}
      data-testid={testId ?? `chamber-card-${chamberId}`}
    >
      {/* The rail is decoration that repeats the badge below it; hiding it from
          assistive tech keeps the status from being announced twice. */}
      <div className="chamber-card__rail" aria-hidden="true">
        {running && <span className="chamber-card__sweep" />}
      </div>

      <div className="chamber-card__body">
        <div className="chamber-card__head">
          <span className="chamber-card__led" aria-hidden="true" />
          <span className="chamber-card__name">{name}</span>
          <StatusBadge status={status} />
        </div>

        <span className="chamber-card__id mono">{chamberId}</span>

        {ratio !== null ? (
          <div className="chamber-card__progress">
            <div
              className="chamber-card__track"
              role="meter"
              aria-valuemin={0}
              aria-valuemax={1}
              aria-valuenow={ratio}
              aria-label={name}
            >
              {/* Width is data-driven geometry — the only property a percentage
                  can express in CSS. Colour stays with the tone class. */}
              <span className="chamber-card__fill" style={{ width: `${ratio * 100}%` }} />
            </div>
            <span className="chamber-card__pct mono">{`${Math.round(ratio * 100)}%`}</span>
          </div>
        ) : (
          <div className="chamber-card__progress chamber-card__progress--none" aria-hidden="true">
            <div className="chamber-card__track" />
          </div>
        )}

        {progressLabel !== undefined && (
          <span className="chamber-card__detail">{progressLabel}</span>
        )}
        {note !== undefined && <span className="chamber-card__note">{note}</span>}

        <dl className="chamber-card__foot">
          <div>
            <dt className="sr-only">heartbeat</dt>
            <dd className="mono">{heartbeatLabel}</dd>
          </div>
          <div>
            <dt className="sr-only">address</dt>
            <dd className="mono chamber-card__addr">{address}</dd>
          </div>
        </dl>
      </div>
    </article>
  );
}
