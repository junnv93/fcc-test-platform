import { chromium } from '@playwright/test';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';

import {
  DEMO_PROJECT_ID,
  TRAINING_SCREENS,
  installProjectWorkspaceCaptureMocks,
} from './capture-fixtures/project-workspace-demo.mjs';
import { describeHarnessBrowserRuntime, launchHarnessBrowser } from './harness-browser-runtime.mjs';
import {
  resolvePlaywrightRuntime,
  rewriteBaseUrlForDocker,
  startPlaywrightDockerServer,
} from './playwright-docker-runtime.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = resolve(HERE, '..');
const REPO_ROOT = resolve(APP_ROOT, '..', '..');
const OUT = resolve(REPO_ROOT, 'docs', 'education', 'images');
const DOCUMENTATION_ARCHIVE_ROOT = resolve(REPO_ROOT, 'docs', 'education', 'archive');
const DEFAULT_BASE = process.env.FCC_WEB_BASE ?? 'http://127.0.0.1:5173';
const STORAGE_KEY_TOKENS = 'fcc-oidc:tokens';
const VITE_CLI = resolve(APP_ROOT, 'node_modules', 'vite', 'bin', 'vite.js');
const LOCAL_HOSTNAMES = new Set(['127.0.0.1', 'localhost', 'hostmachine']);
const API_PREFIXES = ['/headless/', '/platform/', '/report-automation/', '/session/'];
const READY_TIMEOUT_MS = 45_000;
const LOADING_SURFACE_SELECTOR = '[aria-busy="true"], .block-skeleton, .data-table-skeleton';
const ERROR_SURFACE_SELECTOR =
  '.error-fallback, .error-state, [data-testid="route-error-fallback"], [data-testid="shell-error-fallback"]';

function isInside(root, candidate) {
  const relativeCandidate = relative(root, candidate);
  return (
    relativeCandidate === '' ||
    (!relativeCandidate.startsWith('..') && !isAbsolute(relativeCandidate))
  );
}

function assertOutsideDocumentationArchive(candidate, label) {
  if (isInside(DOCUMENTATION_ARCHIVE_ROOT, candidate)) {
    throw new Error(
      `${label} must be outside ${DOCUMENTATION_ARCHIVE_ROOT}; documentation archive artifacts are never diagnostic output.`,
    );
  }
}

function resolveRuntimePath(diagnosticsDir) {
  const candidate = process.env.FCC_CAPTURE_RUNTIME_PATH
    ? resolve(process.env.FCC_CAPTURE_RUNTIME_PATH)
    : join(diagnosticsDir, 'project-workspace-training.runtime.json');
  if (isInside(DOCUMENTATION_ARCHIVE_ROOT, candidate)) {
    throw new Error(
      `FCC_CAPTURE_RUNTIME_PATH must be outside ${DOCUMENTATION_ARCHIVE_ROOT}; the protected runtime record is never writable by this capture entry point.`,
    );
  }
  return candidate;
}

function base64url(value) {
  return Buffer.from(value, 'utf8').toString('base64url');
}

function buildTokenSet(nowMs) {
  const payload = {
    sub: 'training-operator',
    name: 'Training Operator',
    email: 'training-operator@fcc.test',
    permissions: [
      'session:read',
      'session:control',
      'session:events',
      'headless:read',
      'headless:control',
      'report_automation:read',
      'report_automation:control',
      'platform:read',
      'platform:claim',
      'platform:admin',
    ],
    scope: 'openid profile',
    roles: ['operator'],
  };
  const accessToken = [
    base64url(JSON.stringify({ alg: 'RS256', typ: 'JWT' })),
    base64url(JSON.stringify(payload)),
    'training-signature',
  ].join('.');
  return {
    accessToken,
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 3600,
    scope: 'openid profile',
    issuedAt: nowMs,
  };
}

async function waitForBase(url, timeoutMs = 45_000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(url, { redirect: 'manual' });
      if (response.ok || response.status === 304) return;
    } catch {
      // The server is expected to refuse connections until it is ready.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 500));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

export function buildViteDevServerSpec(baseUrl, runtime) {
  const url = new URL(baseUrl);
  if (url.protocol !== 'http:') {
    throw new Error(`Capture Vite server requires an http URL, received ${baseUrl}`);
  }
  const port = url.port || '80';
  const host = runtime === 'docker-server' ? '0.0.0.0' : url.hostname;
  return {
    readinessUrl: new URL('/', url).toString(),
    args: [VITE_CLI, '--host', host, '--port', port, '--strictPort'],
  };
}

async function startViteDevServer(baseUrl, runtime) {
  const spec = buildViteDevServerSpec(baseUrl, runtime);
  const child = spawn(process.execPath, spec.args, {
    cwd: APP_ROOT,
    stdio: 'pipe',
    env: process.env,
  });

  const logs = [];
  const remember = (chunk) => {
    if (logs.length < 40) logs.push(chunk.toString());
  };
  child.stdout.on('data', remember);
  child.stderr.on('data', remember);

  try {
    await waitForBase(spec.readinessUrl);
  } catch (error) {
    child.kill('SIGINT');
    throw new Error(`${error.message}\n${logs.join('')}`);
  }
  return child;
}

async function assertDocumentationReady(page, label, ready, state) {
  await page.locator(ready).waitFor({ state: 'visible', timeout: READY_TIMEOUT_MS });
  await page.evaluate(
    (timeoutMs) =>
      Promise.race([
        globalThis.document.fonts.ready,
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error('document.fonts.ready timed out')), timeoutMs),
        ),
      ]),
    READY_TIMEOUT_MS,
  );
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
  if (state.unexpectedRequests.length > 0 || state.unexpectedApiRequests.length > 0) {
    throw new Error(
      `${label} violated the request policy: ${JSON.stringify({
        external: state.unexpectedRequests,
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

async function captureScreens() {
  await mkdir(OUT, { recursive: true });
  const configuredDiagnosticsDir = process.env.FCC_CAPTURE_DIAGNOSTICS_DIR;
  const diagnosticsDir = configuredDiagnosticsDir
    ? resolve(configuredDiagnosticsDir)
    : await mkdtemp(join(tmpdir(), 'fcc-web-ui-training-'));
  assertOutsideDocumentationArchive(diagnosticsDir, 'FCC_CAPTURE_DIAGNOSTICS_DIR');
  const cleanupDiagnostics = configuredDiagnosticsDir === undefined;
  let browser;
  let dockerServer;
  let devServer;
  try {
    const runtimePath = resolveRuntimePath(diagnosticsDir);
    await mkdir(dirname(runtimePath), { recursive: true });
    const runtime = resolvePlaywrightRuntime(process.argv.slice(2), process.env);
    const baseUrl =
      runtime === 'docker-server'
        ? rewriteBaseUrlForDocker(DEFAULT_BASE, process.env)
        : DEFAULT_BASE;
    devServer = await startViteDevServer(DEFAULT_BASE, runtime);
    if (runtime === 'docker-server') {
      dockerServer = await startPlaywrightDockerServer({ env: process.env });
      browser = await chromium.connect(dockerServer.endpoint);
      await writeFile(
        runtimePath,
        JSON.stringify(
          {
            ok: true,
            runtime,
            endpoint: dockerServer.endpoint,
            image: dockerServer.image,
            alias: dockerServer.alias,
            effectiveBaseUrl: baseUrl,
            timestamp: new Date().toISOString(),
          },
          null,
          2,
        ),
        'utf8',
      );
    } else {
      try {
        browser = await launchHarnessBrowser(chromium);
      } catch (error) {
        await writeFile(
          runtimePath,
          JSON.stringify(describeHarnessBrowserRuntime(chromium.executablePath()), null, 2),
          'utf8',
        );
        throw error;
      }
      await writeFile(
        runtimePath,
        JSON.stringify(
          {
            runtime,
            effectiveBaseUrl: baseUrl,
            ...describeHarnessBrowserRuntime(chromium.executablePath()),
          },
          null,
          2,
        ),
        'utf8',
      );
    }
    const context = await browser.newContext({
      viewport: { width: 1440, height: 960 },
      deviceScaleFactor: 2,
    });
    const allowedHostnames = new Set([new URL(baseUrl).hostname, ...LOCAL_HOSTNAMES]);
    const unexpectedRequests = [];
    const unexpectedApiRequests = [];
    await context.route('**/*', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (!allowedHostnames.has(url.hostname)) {
        unexpectedRequests.push(`${request.method()} ${request.url()}`);
        await route.abort();
        return;
      }
      if (API_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) {
        unexpectedApiRequests.push(`${request.method()} ${request.url()}`);
        await route.abort();
        return;
      }
      await route.continue();
    });
    await installProjectWorkspaceCaptureMocks(context);
    await context.addInitScript(
      ({ key, value, locale }) => {
        globalThis.window.sessionStorage.setItem(key, value);
        globalThis.window.localStorage.setItem('fcc-locale', locale);
      },
      {
        key: STORAGE_KEY_TOKENS,
        value: JSON.stringify(buildTokenSet(Date.now())),
        locale: 'ko',
      },
    );

    const page = await context.newPage();
    const pageErrors = [];
    const consoleErrors = [];
    page.on('pageerror', (error) => pageErrors.push(error.stack ?? error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    for (const screen of TRAINING_SCREENS) {
      pageErrors.length = 0;
      consoleErrors.length = 0;
      unexpectedRequests.length = 0;
      unexpectedApiRequests.length = 0;
      try {
        await page.goto(new URL(screen.route, baseUrl).toString(), {
          waitUntil: 'domcontentloaded',
          timeout: READY_TIMEOUT_MS,
        });
        await page.waitForSelector(screen.waitFor, { state: 'visible', timeout: READY_TIMEOUT_MS });
        await assertDocumentationReady(page, screen.file, screen.waitFor, {
          pageErrors,
          consoleErrors,
          unexpectedRequests,
          unexpectedApiRequests,
        });
      } catch (error) {
        const diagnostics = {
          route: screen.route,
          waitFor: screen.waitFor,
          coverageError: await page.locator('[data-testid="coverage-error"]').count(),
          coverageEmpty: await page.locator('[data-testid="coverage-empty"]').count(),
          projectEmpty: await page.locator('[data-testid="projects-project-empty"]').count(),
          nextState: await page
            .locator('[data-testid="projects-next-state"]')
            .textContent()
            .catch(() => null),
          pageErrors,
          consoleErrors,
          unexpectedRequests,
          unexpectedApiRequests,
        };
        await mkdir(diagnosticsDir, { recursive: true });
        const htmlPath = join(diagnosticsDir, `${screen.file}.debug.html`);
        const diagnosticsPath = join(diagnosticsDir, `${screen.file}.diagnostics.json`);
        await writeFile(htmlPath, await page.content(), 'utf8');
        await writeFile(diagnosticsPath, JSON.stringify(diagnostics, null, 2), 'utf8');
        console.error('capture diagnostics:', JSON.stringify(diagnostics, null, 2));
        console.error(`debug html written to ${htmlPath}`);
        throw error;
      }
      if (screen.route.startsWith('/projects')) {
        await page.getByTestId('projects-next-reports').scrollIntoViewIfNeeded();
      }
      if (screen.route.startsWith('/reports')) {
        await page.getByTestId('reports-submit').scrollIntoViewIfNeeded();
      }
      await page.screenshot({
        path: join(OUT, `${screen.file}.png`),
        fullPage: false,
      });
      console.log(`captured ${screen.file}.png (${screen.route})`);
    }
    console.log(`training project: ${DEMO_PROJECT_ID}`);
  } finally {
    await browser?.close();
    await dockerServer?.stop();
    devServer?.kill('SIGINT');
    if (cleanupDiagnostics) {
      await rm(diagnosticsDir, { recursive: true, force: true });
      console.log(`training capture diagnostics cleaned: ${diagnosticsDir}`);
    } else {
      console.log(`training capture diagnostics retained: ${diagnosticsDir}`);
    }
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  captureScreens().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
