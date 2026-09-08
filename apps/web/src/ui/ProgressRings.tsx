/**
 * One ring, filled by band (flowdeck-console-ui, programme pass).
 *
 * The circle is the WHOLE plan. Each band contributes an arc sized by the cases
 * it has finished, laid end to end; what is left of the circle is what is left
 * of the work. So the ring answers two questions with one shape — how far along
 * the programme is, and which band that progress came from.
 *
 * ⚠️ This replaced three concentric rings. Concentric rings compare each band
 * against ITSELF (each is its own 0–100%), which is the same thing the three
 * cards beside them already say, and it left the reader with four percentages
 * that do not add up to anything. A single ring makes the parts sum to the
 * whole, which is the one relationship the cards cannot show.
 *
 * ⚠️ Segments are ordered as the caller passes them, and that order must match
 * the cards beside the dial.
 */

export interface RingSegment {
  readonly id: string;
  readonly label: string;
  /** This band's share OF THE WHOLE PLAN, 0…1 — not its own completion.
   *  The caller owns that division because only it knows the denominator. */
  readonly share: number;
  /** CSS custom-property slot (`unlicensed` | `licensed` | `mmwave` | …) —
   *  resolved to a token in `global.css`, never a colour literal here. */
  readonly band: string;
}

export interface ProgressRingsProps {
  readonly segments: readonly RingSegment[];
  /** Already-localised centre figure, e.g. "48%". */
  readonly centreLabel: string;
  readonly centreCaption: string;
  readonly testId?: string;
}

/* viewBox 좌표계. 화면 크기는 CSS(--ring-size)가 정하고 SVG 가 따라 늘어난다 —
   여기 숫자는 «비율»만 정한다. */
const SIZE = 160;
const STROKE = 18;
const R = (SIZE - STROKE) / 2;
const C = 2 * Math.PI * R;

/** 세그먼트 사이 틈(원주 단위). 붙여 놓으면 세 밴드가 한 덩어리로 읽히고,
 *  띄우면 «세 조각»으로 읽힌다 — 이 링의 요점이 그 구분이다. */
const GAP = 5;

export function ProgressRings({
  segments,
  centreLabel,
  centreCaption,
  testId,
}: ProgressRingsProps): JSX.Element {
  // Running offset so each arc starts where the previous one ended.
  let consumed = 0;

  return (
    <div className="rings" data-testid={testId ?? 'progress-rings'}>
      {/* Decoration over a figure that is announced in the middle; the arcs
          carry nothing a screen reader can use, and every value they hint at is
          stated in words by the cards in the same panel. */}
      <svg className="rings__svg" viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden="true">
        <g transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}>
          <circle
            className="rings__track"
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            strokeWidth={STROKE}
            fill="none"
          />
          {segments.map((segment) => {
            const share = segment.share < 0 ? 0 : segment.share > 1 ? 1 : segment.share;
            const offset = consumed;
            consumed += share;
            // 조각이 틈보다 작으면 틈을 빼지 않는다 — 음수 길이가 되면 사라진다.
            const raw = C * share;
            const drawn = raw > GAP * 1.5 ? raw - GAP : raw;
            if (drawn <= 0) return null;
            return (
              <circle
                className="rings__seg"
                data-band={segment.band}
                key={segment.id}
                cx={SIZE / 2}
                cy={SIZE / 2}
                r={R}
                strokeWidth={STROKE}
                fill="none"
                /* 둥근 끝 + 조각 사이 틈. 틈이 있으므로 둥근 끝이 이웃을 덮지
                   않고, 덕분에 「덩어리」가 아니라 「조립된 것」으로 보인다. */
                strokeLinecap="round"
                strokeDasharray={`${drawn} ${C}`}
                strokeDashoffset={-C * offset}
              />
            );
          })}
        </g>
      </svg>

      <div className="rings__centre">
        <strong className="rings__figure">{centreLabel}</strong>
        <span className="rings__caption">{centreCaption}</span>
      </div>
    </div>
  );
}
