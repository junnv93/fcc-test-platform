/**
 * 핸드북용 화면 캡처 — docs/education/fcc-operator-handbook.html 에 삽입할 스크린샷 생성.
 *
 * ── 실행 (cwd = apps/web)
 *      node scripts/capture-handbook-screens.mjs
 *
 * ── 전제 (하나라도 없으면 빈 화면·404 가 찍힌다)
 *   1. dev 스택 가동: vite :5173 + session :8000 + headless :8001 + platform :8002 + keycloak :8081
 *   2. 중앙 DB 시드 + **로그인 계정에 프로젝트 배정**
 *        - 배정이 없으면 "내 프로젝트"가 비어 화면 대부분이 빈 상태로 찍힌다.
 *        - 배정 예: project_membership 에 (해당 project, 로그인 사용자, 'project_admin') 삽입.
 *          사용자는 **실제 OIDC issuer** 행이어야 한다(legacy issuer 행에 넣으면 화면에 안 뜸).
 *   3. headless SQLite 시드 — 테스트 플랜·측정 이력 데이터:
 *        python - <<'EOF'
 *        import sys; sys.path[:0] = ['src', 'scripts']
 *        from dev_seed import headless
 *        print(headless.seed_headless('.dev/headless.db', fresh=False))
 *        EOF
 *
 * ── 환경변수
 *   FCC_WEB_BASE       기본 http://localhost:5173
 *   FCC_KC_BASE        기본 http://localhost:8081
 *   FCC_USER/FCC_PASS  기본 admin/admin (dev realm 계정)
 *   FCC_DEMO_PROJECT   프로젝트 컨텍스트 UUID — **환경마다 다르므로 반드시 확인**.
 *                      기본값은 이 저장소 dev 시드의 DEMO-PROJ-01 이다.
 *   FCC_DEMO_SESSION   측정 이력에 쓸 세션 ID (기본 1)
 *
 * ── 알려진 함정
 *   · /sessions 는 vite dev 프록시가 /session* 을 백엔드로 라우팅하므로 직접 goto 불가 →
 *     SPA 네비게이션 후 history.pushState 로 세션 파라미터를 붙인다.
 *   · 진행률(/progress)은 published_plan_expectation 이 있어야 % 가 나온다. 없으면 빈 상태가 정상.
 *
 * 출력: docs/education/images/*.png
 */
import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, '../../../docs/education/images');
const BASE = process.env.FCC_WEB_BASE ?? 'http://localhost:5173';
const KC = process.env.FCC_KC_BASE ?? 'http://localhost:8081';
const USER = process.env.FCC_USER ?? 'admin';
const PASS = process.env.FCC_PASS ?? 'admin';
const READY_TIMEOUT_MS = 30_000;
const API_PREFIXES = ['/session/', '/headless/', '/platform/', '/report-automation/'];
const API_PATH_ALLOWLIST = [
  /^\/session\/(?:info|progress)$/u,
  /^\/headless\/(?:status|jobs|projects\/[^/]+\/.*|sessions\/[^/]+\/.*|reports\/.*)$/u,
  /^\/platform\/(?:projects(?:\/[^/]+(?:\/.*)?)?|providers(?:\/.*)?|chambers(?:\/.*)?)$/u,
  /^\/report-automation\/stats$/u,
];
const LOADING_SURFACE_SELECTOR = '[aria-busy="true"], .block-skeleton, .data-table-skeleton';
const ERROR_SURFACE_SELECTOR =
  '.error-fallback, .error-state, [data-testid="route-error-fallback"], [data-testid="shell-error-fallback"]';
const LOCAL_HOSTNAMES = new Set(['127.0.0.1', 'localhost', 'hostmachine']);

/** 캡처 대상: [파일명, 경로, 사람이 읽는 이름] */
const PROJECT = process.env.FCC_DEMO_PROJECT ?? '4f4e6500-8a6c-4bc9-b7fe-bfb6915ab8fb';
const PQ = `?project=${PROJECT}`;
const SESSION = process.env.FCC_DEMO_SESSION ?? '1';

/** 캡처 대상: [파일명, 경로, 사람이 읽는 이름]
 *  프로젝트 컨텍스트가 필요한 화면은 ?project= 를 붙인다 (없으면 "먼저 프로젝트를 고르세요").
 *  `/projects` 는 이제 단순 커버리지 표가 아니라 "프로젝트 작업 허브" 캡처로도 쓴다. */
const SCREENS = [
  ['01-overview', '/', '홈(개요)', '[data-testid="home-workbench"]'],
  ['02-my-projects', '/my-projects', '내 프로젝트', '[data-testid="my-projects-workbench"]'],
  ['03-fields', `/fields${PQ}`, '시험 분야 선택', '[data-testid="fields-workbench"]'],
  ['04-inventory', `/inventory${PQ}`, '시료 목록', '[data-testid="inventory-workbench"]'],
  [
    '05-test-plans',
    `/test-plans${PQ}`,
    '테스트 플랜',
    '[data-testid="test-plans-workbench-overview"]',
  ],
  ['06-chambers', '/chambers', '시험 챔버', '[data-testid="chambers-workbench-overview"]'],
  ['07-control', '/control', '원격 측정', '[data-testid="control-workbench"]'],
  ['08-progress', `/progress${PQ}`, '진행률', '[data-testid="progress-workbench"]'],
  [
    '09-projects',
    `/projects${PQ}`,
    '프로젝트 작업 허브 / 측정 현황',
    '[data-testid="coverage-matrix"]',
  ],
  ['10-jobs', '/jobs', '측정 작업', '[data-testid="jobs-workbench"]'],
  [
    '11-sessions',
    `/sessions?session=${SESSION}`,
    '측정 이력',
    '[data-testid="sessions-workbench"]',
  ],
  [
    '12-reports',
    `/reports${PQ}`,
    '성적서 생성(프로젝트 컨텍스트)',
    '[data-testid="reports-workbench"]',
  ],
  [
    '13-test-reports',
    `/test-reports${PQ}`,
    '성적서 대장',
    '[data-testid="test-reports-workbench"]',
  ],
  ['14-membership', `/membership${PQ}`, '프로젝트 권한', '[data-testid="membership-workbench"]'],
  ['15-providers', '/providers', '시험 종류', '[data-testid="providers-workbench"]'],
  ['16-diagnostics', '/diagnostics', '진단', '[data-testid="diagnostics-workbench"]'],
];

function isApiPath(pathname) {
  return API_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function isApprovedApiRequest(request) {
  return (
    request.method() === 'GET' &&
    API_PATH_ALLOWLIST.some((pattern) => pattern.test(new URL(request.url()).pathname))
  );
}

async function assertDocumentationReady(page, label, ready, state) {
  await page.locator(ready).waitFor({ state: 'visible', timeout: READY_TIMEOUT_MS });
  await page.evaluate(() => globalThis.document.fonts.ready);
  await page
    .locator(LOADING_SURFACE_SELECTOR)
    .waitFor({ state: 'detached', timeout: READY_TIMEOUT_MS });

  const errorSurfaceCount = await page.locator(ERROR_SURFACE_SELECTOR).count();
  const loadingSurfaceCount = await page.locator(LOADING_SURFACE_SELECTOR).count();
  const headingCount = await page.getByRole('heading', { level: 1 }).count();
  if (headingCount !== 1) {
    throw new Error(`${label} did not render exactly one route heading (count=${headingCount})`);
  }
  if (state.pageErrors.length > 0 || state.consoleErrors.length > 0) {
    throw new Error(
      `${label} emitted browser errors: ${JSON.stringify({
        pageErrors: state.pageErrors,
        consoleErrors: state.consoleErrors,
      })}`,
    );
  }
  if (state.unexpectedExternalRequests.length > 0 || state.unexpectedApiRequests.length > 0) {
    throw new Error(
      `${label} violated the request policy: ${JSON.stringify({
        external: state.unexpectedExternalRequests,
        api: state.unexpectedApiRequests,
      })}`,
    );
  }
  if (errorSurfaceCount > 0 || loadingSurfaceCount > 0) {
    throw new Error(
      `${label} is not a ready documentation state: ${JSON.stringify({
        errorSurfaceCount,
        loadingSurfaceCount,
      })}`,
    );
  }
}

async function main() {
  await mkdir(OUT, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    // 한국어 UI 로 캡처 (locale 토글은 localStorage 기반)
    storageState: {
      cookies: [],
      origins: [{ origin: BASE, localStorage: [{ name: 'fcc-locale', value: 'ko' }] }],
    },
  });
  const page = await context.newPage();
  const allowedOrigins = new Set([new URL(BASE).origin, new URL(KC).origin]);
  const state = {
    pageErrors: [],
    consoleErrors: [],
    unexpectedExternalRequests: [],
    unexpectedApiRequests: [],
  };
  page.on('pageerror', (error) => state.pageErrors.push(error.stack ?? error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') state.consoleErrors.push(message.text());
  });
  page.on('request', (request) => {
    const url = new URL(request.url());
    const isAllowedHost = LOCAL_HOSTNAMES.has(url.hostname) || allowedOrigins.has(url.origin);
    if (!isAllowedHost) {
      state.unexpectedExternalRequests.push(`${request.method()} ${request.url()}`);
    } else if (isApiPath(url.pathname) && !isApprovedApiRequest(request)) {
      state.unexpectedApiRequests.push(`${request.method()} ${url.pathname}`);
    }
  });

  // ── 로그인 (OIDC PKCE → Keycloak 폼)
  await page.goto(BASE + '/');
  try {
    await page.waitForURL((u) => u.toString().startsWith(KC), { timeout: 15_000 });
    await page.fill('input[name="username"]', USER);
    await page.fill('input[name="password"]', PASS);
    await page.click('input[type="submit"], button[type="submit"]');
    await page.waitForURL(
      (u) => u.toString().startsWith(BASE) && !u.toString().includes('/auth/callback'),
      { timeout: 20_000 },
    );
  } catch (err) {
    console.error('로그인 실패:', err.message);
    console.error('   dev 스택(vite/keycloak)이 떠 있는지 확인하십시오.');
    await browser.close();
    process.exit(1);
  }
  console.log(`로그인 성공 (${USER})`);

  // 전제 검사 — 배정이 없으면 대부분의 화면이 빈 상태로 찍히므로 즉시 실패한다.
  state.pageErrors.length = 0;
  state.consoleErrors.length = 0;
  state.unexpectedExternalRequests.length = 0;
  state.unexpectedApiRequests.length = 0;
  await page.goto(BASE + '/my-projects', { waitUntil: 'domcontentloaded', timeout: 20_000 });
  await page.locator('[data-testid="my-projects-workbench"]').waitFor({
    state: 'visible',
    timeout: READY_TIMEOUT_MS,
  });
  await assertDocumentationReady(
    page,
    'handbook precondition /my-projects',
    '[data-testid="my-projects-workbench"]',
    state,
  );
  // 카드 목록은 data-testid="project-card-list" 안의 li — 화면 문구에 의존하지 않는 선택자
  const projectCards = page.locator('[data-testid="project-card-list"] li');
  if ((await projectCards.count()) === 0) {
    console.error('\n[전제 미충족] "내 프로젝트"가 비어 있습니다.');
    console.error('  로그인 계정에 프로젝트가 배정되지 않았습니다 — 이 상태로 캡처하면');
    console.error('  대부분의 화면이 빈 화면으로 찍힙니다. 파일 상단 "전제" 2번을 참고하십시오.');
    await browser.close();
    process.exit(2);
  }

  for (const [file, route, label, ready] of SCREENS) {
    state.pageErrors.length = 0;
    state.consoleErrors.length = 0;
    state.unexpectedExternalRequests.length = 0;
    state.unexpectedApiRequests.length = 0;
    if (route.startsWith('/sessions')) {
      // dev 프록시가 /session* 을 백엔드로 라우팅하므로 직접 goto 불가 → SPA 네비게이션
      await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 20_000 });
      await page.getByRole('link', { name: '측정 이력' }).first().click();
      await page.getByLabel('측정 ID').waitFor({ state: 'visible', timeout: READY_TIMEOUT_MS });
      // 실제 시험원 동작 그대로 — 측정 ID 를 입력하고 조회한다.
      await page.getByLabel('측정 ID').fill(SESSION);
      await page.getByRole('button', { name: '조회' }).click();
    } else {
      await page.goto(BASE + route, { waitUntil: 'domcontentloaded', timeout: 20_000 });
    }
    await assertDocumentationReady(page, file, ready, state);
    const dest = path.join(OUT, `${file}.png`);
    await page.screenshot({ path: dest, fullPage: false });
    console.log(`  캡처 ${file}.png  ← ${label} (${route})`);
  }

  await browser.close();
  console.log(`\n완료: ${SCREENS.length}/${SCREENS.length} 캡처`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
