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
  /** 🔴 이미 «지역화된» 가용성 라벨(사용 중 / 사용 가능 / 오프라인).
   *
   *  필수 prop 이다. 없으면 `StatusBadge` 가 `StatusKind` 의 기본 라벨을 쓰는데,
   *  그 어휘는 «판정»의 것이라 유휴 챔버가 「합격」이라고 적힌다 — 실제로 그랬다.
   *  방은 합격하지 않는다. 합/부는 측정의 성질이고 챔버는 장소다.
   *
   *  ⚠️ 기본값을 주지 않은 것이 요점이다. 기본값이 있으면 새 호출자가 이것을
   *  «잊는 것»이 가능해지고, 잊었을 때 화면은 조용히 틀린 말을 한다. 필수로
   *  두면 tsc 가 그 자리를 잡는다. */
  readonly statusLabel: string;
  /** True while a measurement is in flight — drives the LED and the rail sweep. */
  readonly running: boolean;
  /** 0…1, or null when the chamber reports no progress (idle nodes do not). */
  readonly ratio: number | null;
  /** Already-localised, e.g. "7 / 24 조건 완료". */
  readonly progressLabel?: string;
  /** 「누가 쓰고 있나」 — 이미 지역화된 한 줄. 측정 중이 아니면 `undefined`.
   *
   *  ⚠️ 「모른다」와 「아무도 없다」를 구별해서 넘겨라. 방이 돌고 있는데 주인을
   *  모르는 것은 «정보의 부재»이고, 그것을 빈칸으로 두면 「아무도 안 쓴다」로
   *  읽힌다 — 관리자가 그 방을 비어 있다고 보고 다른 사람을 보낼 수 있다. */
  readonly operatorLabel?: string;
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
  statusLabel,
  running,
  ratio,
  operatorLabel,
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
          <StatusBadge status={status} label={statusLabel} />
        </div>

        {/* ⚠️ `mono` 를 걷었다. 이 카드에는 모노 자리가 셋 있었는데
            (식별자 · 하트비트 · 주소) 셋 다 «작고 조용한» 자리라, 결과적으로
            카드의 부속 정보만 서체가 달라 그 셋이 본문에서 떨어져 나와 보였다.
            모노의 값은 「자릿수가 세로로 맞는 것」인데 그 일은 `tabular-nums`
            가 이미 한다 — 서체를 바꾸지 않고. */}
        <span className="chamber-card__id">{chamberId}</span>

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

        {/* 「누가」가 「얼마나」보다 «위»다. 관리자가 이 카드에서 하는 유일한
            행동은 사람을 옮기는 것이라, 방의 주인이 진행률보다 먼저 읽혀야 한다. */}
        {operatorLabel !== undefined && (
          <span className="chamber-card__user">{operatorLabel}</span>
        )}
        {progressLabel !== undefined && (
          <span className="chamber-card__detail">{progressLabel}</span>
        )}
        {note !== undefined && <span className="chamber-card__note">{note}</span>}

        {/* ⚠️ 카드 «맨 아래»에 붙는다(`margin-top: auto`). 이 둘은 카드의
            내용이 아니라 «각주»다 — 방 이름과 진행률은 카드마다 줄 수가 다른데,
            각주가 그 흐름을 따라 떠다니면 카드 다섯 장에서 「몇 분 전」이 다섯
            개의 다른 높이에 놓인다. 격자에서 비교되는 값은 같은 높이에 있어야
            한다. */}
        <dl className="chamber-card__foot">
          <div>
            <dt className="sr-only">heartbeat</dt>
            <dd>{heartbeatLabel}</dd>
          </div>
          <div>
            <dt className="sr-only">address</dt>
            <dd className="chamber-card__addr">{address}</dd>
          </div>
        </dl>
      </div>
    </article>
  );
}
