/**
 * 비중을 «면적»으로 그리는 지도 (squarified treemap).
 *
 * ┌─ 왜 원형이 아닌가 ────────────────────────────────────────────────────┐
 * │ 파이/도넛은 조각이 넷을 넘으면 급격히 읽기 어려워진다. 사람이 각도를   │
 * │ 비교하는 정확도는 길이·면적보다 낮고, 15인이면 조각 15개에 이름표      │
 * │ 15개가 서로 겹친다 — 그때 도넛이 답하는 질문은 「누가 제일 많나」      │
 * │ 하나로 줄어들고, 나머지 14명은 색 띠가 된다.                          │
 * │                                                                      │
 * │ 100% 누적 막대 한 줄도 후보였다. 공간은 가장 적게 쓰지만 15칸이 되면   │
 * │ 각 칸이 실오라기라 이름이 하나도 안 들어간다.                         │
 * │                                                                      │
 * │ 트리맵은 «면적 = 비중»이라 요구와 1:1이고, 결정적으로 **큰 값일수록   │
 * │ 칸이 크다** — 즉 이름이 필요한 칸에 이름이 들어갈 자리가 생긴다.      │
 * │ 「누가 제일 고생하나」가 화면에서 가장 큰 사각형이다.                  │
 * └──────────────────────────────────────────────────────────────────────┘
 *
 * ⚠️ 「squarified」인 이유. 값을 순서대로 잘라 넣으면(slice-and-dice) 칸이
 * 길쭉한 띠가 되고, 그러면 면적이 같아도 두 칸을 비교할 수 없다 — 사람은 가늘고
 * 긴 것과 뭉툭한 것의 «면적»을 잘 못 견준다. 이 알고리즘은 각 칸의 가로세로비를
 * 1 에 가깝게 유지한다(Bruls et al., 2000).
 *
 * ⚠️ 의존을 들이지 않았다. d3-hierarchy 하나면 되지만 그 값은 이 60줄이고,
 * `bundle-budget.json` 을 다시 재야 한다.
 *
 * ⚠️ 색으로만 말하지 않는다. 이 저장소가 반복해 지킨 규칙이다 — 정체는 색과
 * «함께» 글자(칸 안의 표식)로도 나타나고, 칸이 작아 글자가 안 들어가면
 * `title` 이 그 말을 대신한다.
 */

/** 문자열 → 0…359 색조.
 *
 *  ⚠️ 「자동 생성 팔레트」의 알려진 대가를 안고 쓴다 — 두 사번이 이웃한 색조를
 *  받을 수 있고, 이 함수는 그것을 «막지 못한다». 그래도 여기서 괜찮은 이유는
 *  색이 «식별»이 아니라 «구분»의 축이기 때문이다: 칸에는 사번이 글자로 적혀
 *  있고(칸이 작으면 title 이), 색은 옆칸과 갈라 보이게 하는 일만 한다.
 *  ⚠️ 그래서 이 색을 다른 화면으로 «가져가면 안 된다». 화면을 넘나드는 순간
 *  색이 이름을 대신하기 시작하고, 그때는 대비를 보장하는 팔레트가 필요하다 —
 *  그 논의는 `intent/program-identity-colour/` 에 있다. */
export function hueOf(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i += 1) {
    h = (h * 31 + seed.charCodeAt(i)) % 3600;
  }
  // 황금각으로 흩어, 한 자리만 다른 이웃 사번이 인접 색조를 받지 않게 한다.
  return Math.round((h * 137.508) % 360);
}

export interface ShareCell {
  readonly id: string;
  readonly label: string;
  /** 면적을 정하는 값. 0 이하는 그리지 않는다. */
  readonly value: number;
  /** 칸 안에 작게 붙는 값 표기(이미 지역화된 문자열). */
  readonly detail?: string;
  /** 'stalled' 는 주의색, 'idle' 은 「아직 아무도 안 잡은 것」. */
  readonly tone?: 'normal' | 'stalled' | 'unassigned';
  /** 이 칸이 나(로그인한 사람)의 것인가. */
  readonly mine?: boolean;
  /** 0…359. 사람마다 다른 색조. 없으면 기본 accent 계열로 그린다. */
  readonly hue?: number;
  readonly title?: string;
}

interface Placed extends ShareCell {
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
}

function worstRatio(row: readonly ShareCell[], side: number, scale: number): number {
  const first = row[0];
  const last = row[row.length - 1];
  if (first === undefined || last === undefined || side <= 0) return Number.POSITIVE_INFINITY;
  const sum = row.reduce((acc, c) => acc + c.value, 0) * scale;
  if (sum <= 0) return Number.POSITIVE_INFINITY;
  const max = first.value * scale;
  const min = last.value * scale;
  if (min <= 0) return Number.POSITIVE_INFINITY;
  return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
}

/** 0…100 좌표계로 배치한다 — 화면 픽셀을 모르고도 그릴 수 있어야 부모의 폭이
 *  달라져도 이 함수를 다시 부를 필요가 없다(CSS 퍼센트가 나머지를 한다). */
export function squarify(cells: readonly ShareCell[]): Placed[] {
  const out: Placed[] = [];
  let rest = [...cells].filter((c) => c.value > 0).sort((a, b) => b.value - a.value);
  let remaining = rest.reduce((acc, c) => acc + c.value, 0);
  if (remaining <= 0) return out;

  let x = 0;
  let y = 0;
  let w = 100;
  let h = 100;

  while (rest.length > 0 && w > 0 && h > 0) {
    const scale = (w * h) / remaining;
    const side = Math.min(w, h);
    const head = rest[0];
    if (head === undefined) break;
    const row: ShareCell[] = [head];
    let i = 1;
    for (;;) {
      const next = rest[i];
      if (next === undefined) break;
      if (worstRatio([...row, next], side, scale) > worstRatio(row, side, scale)) break;
      row.push(next);
      i += 1;
    }
    const rowValue = row.reduce((acc, c) => acc + c.value, 0);
    const thickness = (rowValue * scale) / side;
    let offset = 0;
    for (const cell of row) {
      const length = (cell.value * scale) / thickness;
      out.push(
        w >= h
          ? { ...cell, x, y: y + offset, w: thickness, h: length }
          : { ...cell, x: x + offset, y, w: length, h: thickness },
      );
      offset += length;
    }
    if (w >= h) {
      x += thickness;
      w -= thickness;
    } else {
      y += thickness;
      h -= thickness;
    }
    remaining -= rowValue;
    rest = rest.slice(row.length);
  }
  return out;
}

export interface ShareTreemapProps {
  readonly cells: readonly ShareCell[];
  readonly testId?: string;
}

export function ShareTreemap({ cells, testId }: ShareTreemapProps): JSX.Element {
  const placed = squarify(cells);
  const total = cells.reduce((acc, c) => acc + (c.value > 0 ? c.value : 0), 0);

  return (
    <div className="share-map" data-testid={testId}>
      {placed.map((cell) => {
        const share = total > 0 ? cell.value / total : 0;
        return (
          <div
            className="share-map__cell"
            key={cell.id}
            data-tone={cell.tone ?? 'normal'}
            data-mine={cell.mine === true ? 'true' : undefined}
            style={{
              left: `${cell.x}%`,
              top: `${cell.y}%`,
              width: `${cell.w}%`,
              height: `${cell.h}%`,
              ...(cell.hue === undefined ? {} : { '--cell-hue': `${cell.hue}` }),
            }}
            title={cell.title ?? `${cell.label} · ${Math.round(share * 100)}%`}
          >
            {/* ⚠️ 글자는 «칸이 감당할 때만» 넣는다. 작은 칸에 억지로 넣으면
                줄임표만 남아 정보도 없이 잉크만 늘고, 그 잉크가 큰 칸의 이름을
                읽는 것을 방해한다. 작은 칸의 정체는 `title` 이 말한다. */}
            {cell.w > 16 && cell.h > 14 && (
              <span className="share-map__label">{cell.label}</span>
            )}
            {cell.w > 22 && cell.h > 26 && cell.detail !== undefined && (
              <span className="share-map__detail">{cell.detail}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default ShareTreemap;
