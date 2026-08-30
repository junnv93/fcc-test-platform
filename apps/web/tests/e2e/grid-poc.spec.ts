import { AxeBuilder } from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

/**
 * Sprint 3b — grid-poc-evidence-hardening (2026-05-31).
 *
 * ADR-0008 PoC 의 evidence collection. 두 운영 모드를 명시적으로 분리한다.
 *
 *   - **measure** (default, `GRID_POC_MODE=measure` 또는 unset)
 *     모든 측정값을 console.log 로 emit + axe violation 의 소유권 (grid-poc
 *     자체 vs Phase 1 primitive) 자동 분류. expect() 하드 assertion 없음 →
 *     spec 전체가 PASS 로 끝남. dev mode 측정의 신호/잡음 분리 단계.
 *
 *   - **gate** (`GRID_POC_MODE=gate`)
 *     ADR-0008 target 을 hard threshold 로 강제. measurement 가 target 미달
 *     이면 spec FAIL → CI 차단.
 *
 * Sprint 3 의 혼란 (measure mode 결과를 gate mode 로 발화시켜 'fail' 이라고
 * 보고 + 운영자가 'gate PASS' 와 'evidence FAIL' 을 구분하지 못함) 의 구조적
 * 정공. 본 spec 의 default 운영은 measure — 운영자가 의도적으로 gate 켤 때만
 * 차단.
 *
 * 운영 명령 (PowerShell):
 *
 *   # (a) measure mode — evidence 수집, 항상 PASS, console.log 결과 분석용
 *   $env:GRID_POC_E2E = '1'
 *   $env:GRID_POC_MODE = 'measure'   # 또는 unset
 *   npm run test:e2e:grid-poc -- --reporter=list
 *
 *   # (b) gate mode — ADR-0008 hard threshold, target 미달 FAIL
 *   $env:GRID_POC_E2E = '1'
 *   $env:GRID_POC_MODE = 'gate'
 *   npm run test:e2e:grid-poc -- --reporter=list
 *
 * 본 spec 은 코드를 변경하지 않는다 — 측정만 한다.
 */

const SHOULD_RUN = process.env['GRID_POC_E2E'] === '1';
const GATE_MODE = process.env['GRID_POC_MODE'] === 'gate';
const PASTE_ITERATIONS = 10;
const REORDER_ITERATIONS = 10;
const RENDER_ITERATIONS = 5;

// ADR-0008 target thresholds (gate mode 의 hard limit).
const TARGET_PASTE_P95_MS = 200;
const TARGET_REORDER_P95_MS = 100;
const TARGET_INITIAL_RENDER_P95_MS = 1000;
const TARGET_SCROLL_BLOCKING_RATIO = 0.2;

// Default route alias is provided by playwright config (baseURL).
const GRID_POC_PATH = '/grid-poc';

// Sprint 3 prerequisite — RequireAuth 가 IdP redirect 하므로 e2e 가 fake JWT 로
// sessionStorage 를 미리 채워서 인증 통과시킨다. backend 는 본 e2e 에 의존하지
// 않고, RequireAuth 가 클라이언트 측 routing gate 라서 fake 면 충분.
function buildFakeJwt(claims: Record<string, unknown>): string {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url');
  const payload = Buffer.from(JSON.stringify(claims)).toString('base64url');
  return `${header}.${payload}.fake-signature`;
}

const FAKE_ACCESS_CLAIMS = {
  sub: 'e2e-operator',
  name: 'E2E Operator',
  email: 'e2e@local',
  permissions: ['session:control', 'session:events', 'platform:read'],
  scope: 'openid profile email',
  roles: ['operator'],
  iat: Math.floor(Date.now() / 1000),
  exp: Math.floor(Date.now() / 1000) + 3600,
};
const FAKE_TOKENS = {
  accessToken: buildFakeJwt(FAKE_ACCESS_CLAIMS),
  idToken: buildFakeJwt({ sub: 'e2e-operator', name: 'E2E Operator', email: 'e2e@local' }),
  refreshToken: null,
  tokenType: 'Bearer',
  scope: 'openid profile email',
  expiresIn: 3600,
  issuedAt: Date.now(),
};
const STORAGE_KEY_TOKENS = 'fcc-oidc:tokens';

/**
 * Gate-only hard assertion. measure mode 에서는 no-op + console.log.
 */
function recordOrGate<T>(
  actual: T,
  label: string,
  predicate: (value: T) => boolean,
  failMessage: string,
): void {
  const ok = predicate(actual);
  const status = ok ? 'PASS' : 'FAIL';
  console.log(`[evidence][${GATE_MODE ? 'gate' : 'measure'}] ${label} — ${status}`);
  if (GATE_MODE) {
    expect(ok, failMessage).toBe(true);
  }
}

function percentile(values: readonly number[], p: number): number {
  if (values.length === 0) return Number.NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = Math.ceil((p / 100) * sorted.length) - 1;
  return sorted[Math.max(0, Math.min(sorted.length - 1, rank))] ?? Number.NaN;
}

function median(values: readonly number[]): number {
  return percentile(values, 50);
}

function build500x10TsvPayload(): string {
  const rows: string[] = [];
  for (let r = 0; r < 500; r += 1) {
    const cells: string[] = [];
    for (let c = 0; c < 10; c += 1) {
      cells.push(`v${r}_${c}`);
    }
    rows.push(cells.join('\t'));
  }
  return rows.join('\n');
}

async function dispatchPasteAtActiveCell(page: Page, payload: string): Promise<number> {
  return page.evaluate((text) => {
    const grid = document.querySelector<HTMLElement>('[role="grid"]');
    if (!grid) throw new Error('grid element missing');
    const transfer = new DataTransfer();
    transfer.setData('text/plain', text);
    const start = performance.now();
    const event = new ClipboardEvent('paste', {
      clipboardData: transfer,
      bubbles: true,
      cancelable: true,
    });
    grid.dispatchEvent(event);
    return performance.now() - start;
  }, payload);
}

async function measureReorderOnce(page: Page): Promise<number> {
  const rows = page.locator('[role="row"][aria-rowindex]');
  const first = rows.first();
  const target = rows.nth(4);
  const firstBox = await first.boundingBox();
  const targetBox = await target.boundingBox();
  if (!firstBox || !targetBox) throw new Error('row bounding boxes missing');
  const startX = firstBox.x + firstBox.width / 2;
  const startY = firstBox.y + firstBox.height / 2;
  const endX = targetBox.x + targetBox.width / 2;
  const endY = targetBox.y + targetBox.height / 2;
  const t0 = await page.evaluate(() => performance.now());
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX, startY + 20, { steps: 4 });
  await page.mouse.move(endX, endY, { steps: 10 });
  await page.mouse.up();
  const t1 = await page.evaluate(() => performance.now());
  return t1 - t0;
}

// axe violation 의 소유권 분류 — grid-poc 자체 책임 vs Phase 1 primitive 책임.
// node target selector / parent class 를 보고 grid-poc__* / ui-* / 기타 분류.
// axe-core NodeResult.target 은 UnlabelledFrameSelector 라 nested selector
// 형태이지만 본 PoC 는 iframe 없으므로 string[] 으로 평탄화 가능.
function classifyAxeNodeOwnership(
  targetTokens: readonly string[],
): 'grid-poc' | 'primitive' | 'other' {
  const joined = targetTokens.join(' ');
  if (/\.grid-poc(__|\b)/.test(joined)) return 'grid-poc';
  if (
    /data-status=|StatusBadge|SectionBand|PageHeader|Toolbar|EmptyState|MetricStrip/i.test(joined)
  ) {
    return 'primitive';
  }
  return 'other';
}

function flattenAxeTarget(target: unknown): string[] {
  // NodeResult.target: UnlabelledFrameSelector = (string | string[])[]
  if (!Array.isArray(target)) return [];
  const out: string[] = [];
  for (const item of target) {
    if (typeof item === 'string') out.push(item);
    else if (Array.isArray(item)) {
      for (const inner of item) {
        if (typeof inner === 'string') out.push(inner);
      }
    }
  }
  return out;
}

interface AxeOwnershipSummary {
  readonly critical: number;
  readonly serious: number;
  readonly moderate: number;
  readonly minor: number;
  readonly ownership: {
    readonly gridPoc: number;
    readonly primitive: number;
    readonly other: number;
  };
}

async function scanAxeAndClassify(page: Page): Promise<AxeOwnershipSummary> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze();
  const counts = { critical: 0, serious: 0, moderate: 0, minor: 0 };
  const ownership = { gridPoc: 0, primitive: 0, other: 0 };
  for (const violation of results.violations) {
    const impact = violation.impact ?? 'minor';
    if (impact === 'critical') counts.critical += violation.nodes.length;
    else if (impact === 'serious') counts.serious += violation.nodes.length;
    else if (impact === 'moderate') counts.moderate += violation.nodes.length;
    else counts.minor += violation.nodes.length;
    if (impact === 'critical' || impact === 'serious') {
      for (const node of violation.nodes) {
        const owner = classifyAxeNodeOwnership(flattenAxeTarget(node.target));
        if (owner === 'grid-poc') ownership.gridPoc += 1;
        else if (owner === 'primitive') ownership.primitive += 1;
        else ownership.other += 1;
      }
    }
  }
  return { ...counts, ownership };
}

test.describe('Grid PoC evidence (Sprint 3b)', () => {
  test.skip(!SHOULD_RUN, 'GRID_POC_E2E=1 + VITE_GRID_POC=1 dev server 필요');

  test.beforeEach(async ({ context, page }) => {
    await context.addInitScript(
      ({ key, tokens }) => {
        window.sessionStorage.setItem(key, JSON.stringify(tokens));
      },
      { key: STORAGE_KEY_TOKENS, tokens: FAKE_TOKENS },
    );
    await page.goto(GRID_POC_PATH, { waitUntil: 'networkidle' });
    await expect(page.getByRole('heading', { name: 'Phase 3 Grid PoC' })).toBeVisible({
      timeout: 10_000,
    });
  });

  test('axe-core (light mode) — critical = 0, serious 소유권 분류', async ({ page }) => {
    // Chromium default = light mode. PoC 의 design primitive 가 dark-default 인지
    // light 도 지원하는지 본 측정으로 ownership 분리.
    const summary = await scanAxeAndClassify(page);
    console.log(
      `[evidence] axe light — critical=${summary.critical} serious=${summary.serious} ` +
        `moderate=${summary.moderate} minor=${summary.minor} | ` +
        `serious ownership: grid-poc=${summary.ownership.gridPoc} ` +
        `primitive=${summary.ownership.primitive} other=${summary.ownership.other}`,
    );
    recordOrGate(
      summary.critical,
      'axe light critical = 0',
      (n) => n === 0,
      `axe light critical violations: ${summary.critical}`,
    );
    recordOrGate(
      summary.ownership.gridPoc,
      'axe light serious owned by grid-poc = 0',
      (n) => n === 0,
      `grid-poc 자체 책임 serious violations: ${summary.ownership.gridPoc}`,
    );
  });

  test('axe-core (dark mode) — critical = 0, serious 소유권 분류', async ({ page, context }) => {
    // emulateMedia colorScheme dark — Phase 1 design primitive 가 dark-default
    // 인지 검증. light mode 884 serious 가 dark 에서 사라지면 → Phase 1
    // light-mode 미디자인 (PoC 책임 외) 확정.
    await context.addInitScript(() => {
      const noop = (): void => undefined;
      Object.defineProperty(window, 'matchMedia', {
        configurable: true,
        writable: true,
        value: (query: string) => ({
          matches: query === '(prefers-color-scheme: dark)',
          media: query,
          onchange: null,
          addListener: noop,
          removeListener: noop,
          addEventListener: noop,
          removeEventListener: noop,
          dispatchEvent: () => false,
        }),
      });
    });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.reload({ waitUntil: 'networkidle' });
    await expect(page.getByRole('heading', { name: 'Phase 3 Grid PoC' })).toBeVisible();
    const summary = await scanAxeAndClassify(page);
    console.log(
      `[evidence] axe dark — critical=${summary.critical} serious=${summary.serious} ` +
        `moderate=${summary.moderate} minor=${summary.minor} | ` +
        `serious ownership: grid-poc=${summary.ownership.gridPoc} ` +
        `primitive=${summary.ownership.primitive} other=${summary.ownership.other}`,
    );
    recordOrGate(
      summary.critical,
      'axe dark critical = 0',
      (n) => n === 0,
      `axe dark critical violations: ${summary.critical}`,
    );
    recordOrGate(
      summary.ownership.gridPoc,
      'axe dark serious owned by grid-poc = 0',
      (n) => n === 0,
      `grid-poc 자체 책임 serious violations (dark): ${summary.ownership.gridPoc}`,
    );
  });

  test('paste latency 500×10 TSV (p95)', async ({ page }) => {
    const payload = build500x10TsvPayload();
    const firstEditableCell = page.locator('[role="gridcell"][tabindex="0"]').first();
    await firstEditableCell.click();
    const samples: number[] = [];
    for (let i = 0; i < PASTE_ITERATIONS; i += 1) {
      samples.push(await dispatchPasteAtActiveCell(page, payload));
      await page.waitForTimeout(50);
    }
    const p95 = percentile(samples, 95);
    const p50 = median(samples);
    const minVal = Math.min(...samples);
    const maxVal = Math.max(...samples);
    console.log(
      `[evidence] paste latency (500×10 TSV) ms — p50=${p50.toFixed(2)} p95=${p95.toFixed(
        2,
      )} min=${minVal.toFixed(2)} max=${maxVal.toFixed(2)} samples=${samples.length}`,
    );
    recordOrGate(
      p95,
      `paste p95 ≤ ${TARGET_PASTE_P95_MS}ms`,
      (v) => v <= TARGET_PASTE_P95_MS,
      `paste latency p95 (${p95.toFixed(2)} ms) > target ${TARGET_PASTE_P95_MS}`,
    );
  });

  test('row reorder latency (p95)', async ({ page }) => {
    const samples: number[] = [];
    for (let i = 0; i < REORDER_ITERATIONS; i += 1) {
      samples.push(await measureReorderOnce(page));
      await page.waitForTimeout(80);
    }
    const p95 = percentile(samples, 95);
    const p50 = median(samples);
    const minVal = Math.min(...samples);
    const maxVal = Math.max(...samples);
    console.log(
      `[evidence] reorder latency ms — p50=${p50.toFixed(2)} p95=${p95.toFixed(
        2,
      )} min=${minVal.toFixed(2)} max=${maxVal.toFixed(2)} samples=${samples.length}`,
    );
    recordOrGate(
      p95,
      `reorder p95 ≤ ${TARGET_REORDER_P95_MS}ms`,
      (v) => v <= TARGET_REORDER_P95_MS,
      `reorder latency p95 (${p95.toFixed(2)} ms) > target ${TARGET_REORDER_P95_MS}`,
    );
  });

  test('initial render latency (p95)', async ({ page }) => {
    const samples: number[] = [];
    for (let i = 0; i < RENDER_ITERATIONS; i += 1) {
      await page.goto('/', { waitUntil: 'networkidle' });
      const t0 = Date.now();
      await page.goto(GRID_POC_PATH, { waitUntil: 'networkidle' });
      await page.locator('[role="grid"]').waitFor({ state: 'visible' });
      await page.locator('[role="gridcell"][tabindex="0"]').first().waitFor({ state: 'visible' });
      const t1 = Date.now();
      samples.push(t1 - t0);
    }
    const p95 = percentile(samples, 95);
    const p50 = median(samples);
    console.log(
      `[evidence] initial render ms — p50=${p50.toFixed(2)} p95=${p95.toFixed(2)} samples=${
        samples.length
      }`,
    );
    recordOrGate(
      p95,
      `initial render p95 ≤ ${TARGET_INITIAL_RENDER_P95_MS}ms`,
      (v) => v <= TARGET_INITIAL_RENDER_P95_MS,
      `initial render p95 (${p95.toFixed(2)} ms) > target ${TARGET_INITIAL_RENDER_P95_MS}`,
    );
  });

  test('scroll smoothness — long animation frames', async ({ page }) => {
    const metric = await page.evaluate(async () => {
      interface Sample {
        readonly type: string;
        readonly duration: number;
      }
      const samples: Sample[] = [];
      const observers: PerformanceObserver[] = [];
      const tryObserve = (type: string): void => {
        try {
          const observer = new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
              samples.push({ type, duration: entry.duration });
            }
          });
          observer.observe({ type, buffered: false });
          observers.push(observer);
        } catch {
          // ignore unsupported type
        }
      };
      tryObserve('long-animation-frame');
      tryObserve('longtask');

      const grid = document.querySelector<HTMLElement>('.grid-poc__viewport');
      if (!grid) throw new Error('grid viewport missing');
      const start = performance.now();
      for (let i = 0; i < 30; i += 1) {
        grid.scrollTop = i % 2 === 0 ? i * 200 : (30 - i) * 200;
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      }
      const elapsed = performance.now() - start;
      await new Promise<void>((resolve) => setTimeout(resolve, 100));
      for (const observer of observers) observer.disconnect();
      const longCount = samples.filter((s) => s.duration > 50).length;
      const totalDuration = samples.reduce((acc, s) => acc + s.duration, 0);
      return { elapsed, samples, longCount, totalDuration };
    });
    const blockingRatio = metric.elapsed > 0 ? metric.totalDuration / metric.elapsed : 0;
    console.log(
      `[evidence] scroll smoothness — elapsed=${metric.elapsed.toFixed(
        2,
      )}ms long_frames=${metric.longCount} total_blocking=${metric.totalDuration.toFixed(
        2,
      )}ms blocking_ratio=${(blockingRatio * 100).toFixed(1)}%`,
    );
    recordOrGate(
      blockingRatio,
      `scroll blocking_ratio < ${(TARGET_SCROLL_BLOCKING_RATIO * 100).toFixed(0)}%`,
      (v) => v < TARGET_SCROLL_BLOCKING_RATIO,
      `scroll blocking ratio (${(blockingRatio * 100).toFixed(1)}%) ≥ target`,
    );
  });
});
