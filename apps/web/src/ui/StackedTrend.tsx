import { useEffect, useId, useRef, useState } from 'react';

import type { CSSProperties } from 'react';

/**
 * Cumulative progress over time, bands stacked to the overall (flowdeck-console-ui).
 *
 * Sits beside the ring, not instead of it. The ring says where the programme
 * stands right now; this says how it got there, which band carried it, how fast
 * (the slope), and how far is left — the gap up to the 100% rule.
 *
 * ⚠️ The bands STACK. Each line is a running total: the first is the lowest
 * band alone, the second is that band plus the next, and the topmost is
 * everything — so the top line IS the programme and its destination is the
 * ceiling drawn across the top of the plot. A band's own contribution is the
 * distance between two neighbouring lines, which is why the faint ribbon
 * between them is drawn: it is what makes "these add up" visible.
 *
 * ⚠️ Lines and dots, not solid areas. The ribbon is deliberately faint. A full
 * stack reads as volume and invites comparing thickness, which the eye does
 * badly, while lines keep the slope — the thing a PM reports — legible. A dot
 * marks a day that carries a measurement, so a segment between two dots is
 * interpolation; saying so keeps a straight week from looking like steady work
 * when it was one batch on the last day.
 *
 * ⚠️ NO legend. It went through three forms — values in `pt`, names only,
 * floated inside the plot — before the right answer turned out to be none. The
 * band colours are consistent across the whole page (cards, columns, gauges,
 * issue rows), so by the time a reader reaches this chart the colour→band
 * mapping is already learned; a legend would re-teach what the page taught and
 * spend a row doing it. The end-of-line labels still name each line's value.
 *
 * ⚠️ No schedule line. Drawing "where we should be" needs a deadline, and no
 * field in the plan carries one, so the chart simply has no such axis.
 *
 * ⚠️ Inline SVG, no chart library — same reason as the rest of this console:
 * a library brings a second theming system beside `global.css`, and colour here
 * has to come from the same band tokens the cards and columns use.
 */

export interface TrendPoint {
  /** `YYYY-MM-DD`. */
  readonly day: string;
  /** Each band's cumulative share OF THE WHOLE PLAN, in the order of `bands`.
   *  These are contributions, not completion ratios: they sum to that day's
   *  overall progress, and that sum is what the top line draws. */
  readonly values: readonly number[];
}

export interface StackedTrendProps {
  readonly bands: readonly string[];
  readonly points: readonly TrendPoint[];
  /** Maps a band label to its CSS slot (`unlicensed` | `licensed` | `mmwave`). */
  readonly bandSlot: (band: string) => string;
  /** Already-localised label for the 100% rule, e.g. "plan complete". */
  readonly ceilingLabel: string;
  readonly formatRatio: (ratio: number) => string;
  /** True while the figures belong to a PREVIOUS selection and the current one
   *  is still in flight. The chart stays on screen but reads as "not the
   *  answer yet" — an empty plot would say the programme has no history. */
  readonly stale?: boolean;
  readonly testId?: string;
}

/* ⚠️ 사용자 단위 = CSS 픽셀. viewBox 의 폭을 «실제로 측정한 폭»으로 잡기
   때문에 스케일이 1 로 고정되고, 따라서 창을 줄여도 글자·선 두께·점 크기가
   그대로다. 줄어드는 것은 x 간격뿐 — 좌우로 «압축»된다.

   이렇게 하지 않으면(고정 viewBox + 비율 스케일) 창을 줄일 때 날짜와 축 라벨이
   같이 작아진다. 그래프가 작아질수록 읽기 어려워지는데, 작아진 화면이야말로
   라벨이 가장 필요한 곳이다. 높이는 고정한다 — 세로는 0~100% 라는 «의미가 있는»
   축이라 눌리면 기울기가 거짓말이 된다. */
const H = 268;
const MIN_W = 320;
const DEFAULT_W = 700;
const PAD_L = 48; // y-axis labels — '100 %' needs the extra column
const PAD_R = 48; // end-of-line labels sit outside the plot
const PAD_T = 22; // headroom above the 100% rule (and its label)
const PAD_B = 56; // rotated date labels
const PLOT_H = H - PAD_T - PAD_B;
const GRID = [0, 0.25, 0.5, 0.75, 1];

const clamp = (n: number): number => (n < 0 ? 0 : n > 1 ? 1 : n);

export function StackedTrend({
  bands,
  points,
  bandSlot,
  ceilingLabel,
  formatRatio,
  stale,
  testId,
}: StackedTrendProps): JSX.Element {
  const gid = useId();

  /** 그린 폭. ResizeObserver 를 쓰는 이유는 창 크기만이 아니라 «옆 칸»이
   *  바뀔 때도(링 카드의 폭, 사이드바 접힘) 이 그래프의 폭이 바뀌기 때문이다 —
   *  window resize 만 듣는 구현은 그때 갱신되지 않는다. */
  const hostRef = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState(DEFAULT_W);

  useEffect(() => {
    const host = hostRef.current;
    if (host === null) return undefined;
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width ?? 0;
      // 0 은 «아직 레이아웃 전»이지 폭이 0인 것이 아니다. 그걸 반영하면 축이
      // 한 프레임 무너졌다가 돌아온다.
      if (next > 0) setMeasured(Math.round(next));
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  const W = Math.max(MIN_W, measured);
  const PLOT_W = W - PAD_L - PAD_R;
  const step = points.length > 1 ? PLOT_W / (points.length - 1) : PLOT_W;

  const y = (ratio: number): number => PAD_T + PLOT_H * (1 - clamp(ratio));
  const x = (i: number): number => PAD_L + i * step;

  /** Running sums per day: `levels[i][k]` is bands 0…k added together, so the
   *  last entry of every row is that day's overall progress. */
  const levels = points.map((point) => {
    const out: number[] = [];
    let run = 0;
    for (const value of point.values) {
      run += value;
      out.push(run);
    }
    return out;
  });

  const levelAt = (i: number, k: number): number => levels[i]?.[k] ?? 0;
  const baseAt = (i: number, k: number): number => (k === 0 ? 0 : levelAt(i, k - 1));

  const lineFor = (k: number): string =>
    `M ${points.map((_, i) => `${x(i).toFixed(1)},${y(levelAt(i, k)).toFixed(1)}`).join(' L ')}`;

  /** The ribbon between band `k` and the line below it — faint on purpose. It
   *  says "these add up" without asking the eye to measure its thickness. */
  const ribbonFor = (k: number): string => {
    const top = points.map((_, i) => `${x(i).toFixed(1)},${y(levelAt(i, k)).toFixed(1)}`);
    const bottom = points
      .map((_, i) => `${x(i).toFixed(1)},${y(baseAt(i, k)).toFixed(1)}`)
      .reverse();
    return `M ${top.join(' L ')} L ${bottom.join(' L ')} Z`;
  };

  const lastIndex = points.length - 1;

  /** 끝 라벨의 y. 값을 그대로 쓰면 기여가 작은 계열에서 두 라벨이 «겹쳐서»
   *  둘 다 못 읽게 된다(실측: mmWave 가 4pt 라 44% 와 48% 가 4px 차이였다).
   *  위에서부터 최소 간격을 밀어 확보한다 — 선은 제자리에 두고 «글자만» 비킨다. */
  const LABEL_GAP = 12;
  const endLabelY = ((): readonly number[] => {
    if (lastIndex < 0) return [];
    const wanted = bands.map((_, k) => y(levelAt(lastIndex, k)));
    // 위(작은 y)에서 아래로 훑으며 아래쪽 라벨을 필요한 만큼 내린다.
    const order = wanted.map((v, k) => ({ v, k })).sort((a, b) => a.v - b.v);
    const out = [...wanted];
    let floor = -Infinity;
    for (const { k } of order) {
      const next = Math.max(out[k] ?? 0, floor + LABEL_GAP);
      out[k] = next;
      floor = next;
    }
    return out;
  })();

  /* 날짜를 몇 개 건너뛸지는 «개수»가 아니라 «남은 픽셀»이 정한다. 개수로 정하면
     같은 14일이 넓은 화면에서도 좁은 화면에서도 같은 밀도로 그려져, 좁아지면
     라벨끼리 겹친다. 64° 로 누운 11px 글자는 대략 12px 폭을 먹는다. */
  const stride = Math.max(1, Math.ceil(12 / Math.max(step, 1)));
  const showsDay = (i: number): boolean => i === lastIndex || i % stride === 0;

  return (
    <figure
      className="trend"
      data-stale={stale === true ? 'true' : undefined}
      aria-busy={stale === true ? true : undefined}
      data-testid={testId ?? 'stacked-trend'}
    >
      <div className="trend__plot" ref={hostRef}>
      <svg
        className="trend__svg"
        viewBox={`0 0 ${W} ${H}`}
        width={W}
        height={H}
        aria-hidden="true"
        focusable="false"
      >
        {/* ── y axis. Without labelled heights the reader cannot tell 48% from
               "somewhere in the middle", and the point of the ceiling is that
               the REMAINING distance is visible. ─────────────────────────── */}
        {GRID.map((g) => (
          <g key={`${gid}-g-${g}`}>
            <line
              className={g === 1 ? 'trend__ceiling' : 'trend__grid'}
              x1={PAD_L}
              x2={PAD_L + PLOT_W}
              y1={y(g)}
              y2={y(g)}
            />
            <text className="trend__ylabel" x={PAD_L - 8} y={y(g) + 3.5} textAnchor="end">
              {formatRatio(g)}
            </text>
          </g>
        ))}
        <text className="trend__ceiling-label" x={PAD_L + PLOT_W} y={y(1) - 7} textAnchor="end">
          {ceilingLabel}
        </text>
        <line className="trend__axis-line" x1={PAD_L} x2={PAD_L} y1={y(1)} y2={y(0)} />
        <line
          className="trend__axis-line"
          x1={PAD_L}
          x2={PAD_L + PLOT_W}
          y1={y(0)}
          y2={y(0)}
        />

        {/* ── x axis. A tick under every dot and the date turned upright, so a
               dot can be traced to a day without counting across. ────────── */}
        {points.map((point, i) => (
          <g key={`${gid}-x-${point.day}`}>
            <line
              className="trend__tick"
              x1={x(i)}
              x2={x(i)}
              y1={y(0)}
              y2={y(0) + (showsDay(i) ? 5 : 3)}
            />
            {showsDay(i) && (
              <text
                className="trend__xlabel"
                x={x(i)}
                y={y(0) + 10}
                textAnchor="end"
                transform={`rotate(-64 ${x(i)} ${y(0) + 10})`}
              >
                {point.day.slice(5)}
              </text>
            )}
          </g>
        ))}

        {/* ── the stack. Ribbons first so every line and dot sits on top of
               them: the ribbon is context, the line is the reading. ─────── */}
        {bands.map((band, k) => (
          <path
            className="trend__ribbon"
            data-band={bandSlot(band)}
            d={ribbonFor(k)}
            key={`${gid}-r-${band}`}
          />
        ))}

        {bands.map((band, k) => (
          <g
            className="trend__series"
            data-band={bandSlot(band)}
            data-top={k === bands.length - 1 ? 'true' : undefined}
            key={`${gid}-${band}`}
          >
            <path className="trend__line" d={lineFor(k)} />
            {points.map((point, i) => (
              <circle
                className="trend__dot"
                key={`${gid}-${band}-${point.day}`}
                cx={x(i)}
                cy={y(levelAt(i, k))}
                r={2.6}
              />
            ))}
          </g>
        ))}

        {/* Where the stack stands today, written at the end of each line so the
            lines name themselves without a trip to the legend. */}
        {lastIndex >= 0 &&
          bands.map((band, k) => (
            <text
              className="trend__end"
              data-band={bandSlot(band)}
              data-top={k === bands.length - 1 ? 'true' : undefined}
              key={`${gid}-e-${band}`}
              x={PAD_L + PLOT_W + 7}
              y={(endLabelY[k] ?? y(levelAt(lastIndex, k))) + 3.5}
            >
              {formatRatio(levelAt(lastIndex, k))}
            </text>
          ))}
      </svg>
      </div>
    </figure>
  );
}

/** Escape hatch for callers that need the band slot as a style hook. */
export type StackedTrendStyle = CSSProperties;
