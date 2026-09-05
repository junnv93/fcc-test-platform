/**
 * UI 프리미티브에 `data-testid` 를 직접 넘기면 **조용히 사라진다** (2026-09-05).
 *
 * `Card` 를 비롯한 프리미티브들은 `testId` prop 을 받아 이렇게 렌더한다:
 *
 *     createElement(as, { ...props, 'data-testid': testId ?? 'card' }, …)
 *
 * 스프레드가 **먼저**이므로, 호출부가 `data-testid="x"` 로 넘긴 값은 뒤따르는
 * 명시 키에 덮여 DOM 에는 `data-testid="card"` 가 남는다. 저자가 쓴 이름이 사라지는데
 * 아무도 말해 주지 않는다.
 *
 * ⚠️ 이 부류는 **브라우저에서만** 드러났다. 실측 2026-09-05: `my-projects.tsx` 의 생성
 * 패널이 그렇게 이름을 잃었고, vitest 1495건은 전부 초록이었으며(그 testid 를 묻는
 * 단위 시험이 없었다) `tsc` 도 잡지 못했다 — React 의 `HTMLAttributes` 는 `data-*` 를
 * 타입으로 갖지 않아 JSX 가 임의의 `data-*` 를 통과시킨다. e2e 가 처음 말했다.
 *
 * ## 왜 타입이 아니라 이 게이트인가
 *
 * 프리미티브마다 `readonly 'data-testid'?: never` 를 손으로 붙이면 컴파일 시점에
 * 잡히지만, **그 목록은 손 목록이다** — 새 프리미티브를 만든 사람이 그 한 줄을 잊으면
 * 보호가 조용히 빠진다. 여기서는 덮어쓰는 프리미티브 집합 자체를 **소스에서 파생**하므로,
 * 내일 누가 같은 패턴의 프리미티브를 새로 만들어도 그 순간부터 함께 지켜진다.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

// 다른 시험과 같은 관용구 — `import.meta.url` 을 파일 경로로 풀어 apps/web 루트를 잡는다.
const APPS_WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(APPS_WEB_ROOT, 'src');

function tsxFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return tsxFiles(full);
    return full.endsWith('.tsx') ? [full] : [];
  });
}

/** `testId ?? '…'` 로 `data-testid` 를 **덮어쓰는** 프리미티브 이름 — 소스에서 파생한다. */
function overridingPrimitives(files: string[]): Set<string> {
  const names = new Set<string>();
  for (const file of files) {
    const source = readFileSync(file, 'utf8');
    if (!/'data-testid':\s*testId\s*\?\?|data-testid=\{testId\s*\?\?/.test(source)) continue;
    for (const match of source.matchAll(/export function (\w+)\(/g)) {
      const name = match[1];
      if (name !== undefined) names.add(name);
    }
  }
  return names;
}

/**
 * `<Prim` 의 **자기 여는 태그** 텍스트만 돌려준다.
 *
 * 중첩된 `<` 를 만나면 거기서 멈추는 것이 이 함수의 전부다 — 멈추지 않으면
 * `action={<Link data-testid="…">}` 같은 **자식의** 속성이 부모의 것으로 읽혀
 * 게이트가 거짓 경보를 낸다(초판이 실제로 그렇게 5건을 잘못 잡았다).
 */
export function ownOpeningTag(source: string, start: number): string {
  let depth = 0;
  for (let i = start; i < source.length; i += 1) {
    const char = source[i];
    if (char === '{') depth += 1;
    else if (char === '}') depth -= 1;
    else if (char === '<' && i > start) return source.slice(start, i);
    else if (char === '>' && depth === 0) return source.slice(start, i + 1);
  }
  return source.slice(start);
}

describe('UI 프리미티브의 testid 소유권', () => {
  const files = tsxFiles(SRC);
  const primitives = overridingPrimitives(files);

  it('덮어쓰는 프리미티브를 실제로 찾아낸다 (파생이 공허하지 않다)', () => {
    // 하나도 못 찾으면 아래 검사가 통과로 읽힌다 — 게이트가 스스로 꺼지는 것을 막는다.
    expect(primitives.size).toBeGreaterThan(3);
    expect(primitives.has('Card')).toBe(true);
  });

  it('자기 태그와 자식 태그를 구별한다 (초판이 틀렸던 자리)', () => {
    const nested = '<EmptyState title={x} action={<Link data-testid="pick">go</Link>} />';
    expect(ownOpeningTag(nested, 0)).not.toContain('data-testid');

    const own = '<EmptyState data-testid="mine" />';
    expect(ownOpeningTag(own, 0)).toContain('data-testid');
  });

  it('어느 호출부도 프리미티브에 data-testid 를 직접 넘기지 않는다', () => {
    const offenders: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, 'utf8');
      for (const primitive of primitives) {
        for (const match of source.matchAll(new RegExp(`<${primitive}\\b`, 'g'))) {
          const index = match.index ?? 0;
          if (!ownOpeningTag(source, index).includes('data-testid=')) continue;
          const line = source.slice(0, index).split('\n').length;
          offenders.push(
            `${file.replace(APPS_WEB_ROOT + '/', '')}:${line}  <${primitive} … data-testid=…>`,
          );
        }
      }
    }
    expect(
      offenders,
      `프리미티브는 \`testId\` prop 을 받는다. \`data-testid\` 로 넘긴 값은 스프레드 뒤의 ` +
        `명시 키에 덮여 DOM 에 남지 않는다 — \`testId=\` 로 바꿔라:\n` +
        offenders.map((line) => `  · ${line}`).join('\n'),
    ).toEqual([]);
  });
});
