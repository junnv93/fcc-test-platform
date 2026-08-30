#!/usr/bin/env node
/**
 * Dev stack orchestrator (fe-dev-stack-launcher, 2026-06-14).
 *
 * One command to bring up the local full stack: the dev IdP (Keycloak) plus the
 * Vite dev gateway and the three backend ASGI apps (session / headless /
 * platform), each on the port the gateway proxy forwards to. The surface
 * topology (prefix / port / uvicorn factory / ws) is read from
 * `dev-stack.config.json` — the SAME SSOT `vite.config.ts` derives its proxy
 * from — so the launcher and the proxy can never drift on ports.
 *
 * Infra (Keycloak) is brought up first with docker-native readiness (up -d
 * --wait) and left DETACHED — dev:stack never tears it down (lifecycle
 * separation, mirroring scripts/preview-web.sh). Skip it with
 * FCC_DEV_STACK_SKIP_INFRA=1 when the IdP is managed separately.
 *
 * The backends run from the repo's existing Python venv (no Docker image — the
 * web runtime pulls the full desktop deps via requirements-web.txt, too heavy to
 * containerise cleanly). The PURE command builders below are unit-tested
 * (`dev-stack.test.mjs`); the actual process spawning in `main()` is a manual
 * smoke (run `npm run dev:stack`), mirroring the harness GUI-smoke convention.
 *
 *   npm run dev:stack
 *
 * Env passthrough: each backend still needs its own runtime env
 * (FCC_SESSION_EXCEL_PATH, FCC_HEADLESS_DB_PATH, FCC_CENTRAL_DB_URL, ...) — set
 * them before running. Override the venv python via FCC_PYTHON. Platform's
 * PostgreSQL is out of scope (start it separately; see the runbook).
 */
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { applyOidcDefaults } from './derive-oidc-env.mjs';

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const REPO_ROOT = resolve(APP_ROOT, '..', '..');
const SRC_DIR = resolve(REPO_ROOT, 'src');

/** Dev IdP (Keycloak) compose file — single SSOT path (mirrors preview-web.sh). */
export const INFRA_COMPOSE_FILE = 'infra/docker-compose.idp.yml';
/** compose healthcheck budget: start_period 30s + retries 12×interval 10s. */
export const INFRA_WAIT_TIMEOUT = 180;

/** Load the dev-topology SSOT (shared with vite.config.ts). */
export function loadDevStackConfig(appRoot = APP_ROOT) {
  return JSON.parse(readFileSync(resolve(appRoot, 'dev-stack.config.json'), 'utf8'));
}

/**
 * Load the dev OIDC SSOT — the SAME browser-facing `runtime-config.dev.json` the
 * SPA reads (docs/development/dev-preview.md, principle #1). Read once at startup
 * (never per-request); the backend OIDC env is derived from it (see
 * derive-oidc-env.mjs). Returns {} if absent so a no-IdP dev still boots.
 */
export function loadDevRuntimeConfig(appRoot = APP_ROOT) {
  const path = resolve(appRoot, 'public', 'runtime-config.dev.json');
  if (!existsSync(path)) return {};
  return JSON.parse(readFileSync(path, 'utf8'));
}

/**
 * Pure: map each backend surface to a uvicorn launch descriptor. No spawning —
 * the runner (and the unit test) consume these. Ports + factories come only from
 * the SSOT config (no literals here).
 */
export function buildBackendCommands(config, { pythonBin = 'python', host } = {}) {
  const bindHost = host ?? config.host;
  return config.surfaces.map((s) => ({
    key: s.key,
    pythonBin,
    args: [
      '-m',
      'uvicorn',
      '--factory',
      s.uvicornFactory,
      '--host',
      bindHost,
      '--port',
      String(s.port),
    ],
    cwd: SRC_DIR,
  }));
}

export function selectDevStackSurfaces(config, selection) {
  if (!selection?.trim()) return config.surfaces;
  const requested = new Set(
    selection
      .split(',')
      .map((key) => key.trim())
      .filter(Boolean),
  );
  const known = new Set(config.surfaces.map((surface) => surface.key));
  const unknown = [...requested].filter((key) => !known.has(key));
  if (unknown.length > 0) throw new Error(`Unknown FCC_DEV_STACK_SURFACES: ${unknown.join(', ')}`);
  return config.surfaces.filter((surface) => requested.has(surface.key));
}

export function buildGatewayCommand(env = process.env) {
  const explicitUrl = env.FCC_DEV_STACK_GATEWAY_URL?.trim();
  if (!explicitUrl) return ['run', 'dev'];
  const url = new URL(explicitUrl);
  if (url.protocol !== 'http:' || !url.port) {
    throw new Error('FCC_DEV_STACK_GATEWAY_URL must be an http URL with an explicit port');
  }
  return ['run', 'dev', '--', '--host', url.hostname, '--port', url.port, '--strictPort'];
}

/**
 * Pure: resolve the venv python interpreter for a platform. FCC_PYTHON overrides;
 * otherwise the repo's `fcc_test_env` (Windows `Scripts/`, POSIX `bin/`), falling
 * back to bare `python` on PATH.
 */
export function resolveVenvPython({
  platform = process.platform,
  env = process.env,
  repoRoot = REPO_ROOT,
  exists = existsSync,
} = {}) {
  if (env['FCC_PYTHON']) return env['FCC_PYTHON'];
  const candidate =
    platform === 'win32'
      ? resolve(repoRoot, 'fcc_test_env', 'Scripts', 'python.exe')
      : resolve(repoRoot, 'fcc_test_env', 'bin', 'python');
  return exists(candidate) ? candidate : 'python';
}

/**
 * Pure: convert a Windows repo path to its WSL `/mnt/<drive>/...` form so a
 * win32 host can run the dev IdP via `wsl.exe ... docker compose`. A non-Windows
 * path is returned unchanged.
 */
export function toWslPath(winPath) {
  return winPath
    .replace(/^([A-Za-z]):[\\/]/, (_, d) => `/mnt/${d.toLowerCase()}/`)
    .replace(/\\/g, '/');
}

/**
 * Pure: build the command that brings up the dev IdP (Keycloak) with
 * docker-native readiness (`up -d --wait` — container healthcheck, not host curl
 * polling). On win32 the host shells into WSL Ubuntu; on POSIX it calls docker
 * directly. Returns null when skipped. No port/realm literals — only the compose
 * file SSOT path. The container is detached (-d) so it OUTLIVES dev:stack (the
 * launcher never tears it down — preview-web.sh's no-self-destruct rule).
 */
export function buildInfraUpCommand({
  platform = process.platform,
  repoRoot = REPO_ROOT,
  composeFile = INFRA_COMPOSE_FILE,
  waitTimeout = INFRA_WAIT_TIMEOUT,
  skip = false,
} = {}) {
  if (skip) return null;
  if (platform === 'win32') {
    const wslRepo = toWslPath(repoRoot);
    return {
      command: 'wsl.exe',
      args: [
        '-d',
        'Ubuntu',
        'bash',
        '-lc',
        `export DOCKER_CONFIG=/tmp; cd ${wslRepo} && ` +
          `docker compose -f ${composeFile} up -d --wait --wait-timeout ${waitTimeout}`,
      ],
    };
  }
  return {
    command: 'docker',
    args: [
      'compose',
      '-f',
      composeFile,
      'up',
      '-d',
      '--wait',
      '--wait-timeout',
      String(waitTimeout),
    ],
    cwd: repoRoot,
  };
}

export function main() {
  const config = loadDevStackConfig();
  const pythonBin = resolveVenvPython();

  // Infra (Keycloak) first — docker-native readiness, detached so it survives
  // this process (lifecycle separation: dev:stack never tears the IdP down —
  // mirrors preview-web.sh). Skip when the IdP is managed separately.
  const infra = buildInfraUpCommand({
    skip: process.env['FCC_DEV_STACK_SKIP_INFRA'] === '1',
  });
  if (infra) {
    console.log(`[dev:stack] infra up: ${infra.command} ${infra.args.join(' ')}`);
    const r = spawnSync(infra.command, infra.args, {
      cwd: infra.cwd,
      stdio: 'inherit',
      shell: false,
    });
    if (r.status !== 0) {
      console.warn(
        '[dev:stack] dev IdP (Keycloak) up failed or timed out — backends will ' +
          'still start, but OIDC-gated requests may fail. Manage it separately ' +
          'or check `docker compose -f infra/docker-compose.idp.yml ps`.',
      );
    }
  } else {
    console.log('[dev:stack] infra skipped (FCC_DEV_STACK_SKIP_INFRA=1)');
  }

  const children = [];
  let shuttingDown = false;

  const start = (label, command, args, cwd, env = process.env) => {
    console.log(`[dev:stack] start ${label}: ${command} ${args.join(' ')}`);
    const child = spawn(command, args, { cwd, stdio: 'inherit', env, shell: false });
    child.on('exit', (code) => {
      if (!shuttingDown) {
        console.error(`[dev:stack] ${label} exited (${code}) — shutting down stack`);
        shutdown(1);
      }
    });
    children.push(child);
  };

  const shutdown = (code) => {
    if (shuttingDown) return;
    shuttingDown = true;
    for (const c of children) c.kill('SIGINT');
    process.exit(code);
  };

  process.on('SIGINT', () => shutdown(0));
  process.on('SIGTERM', () => shutdown(0));

  // Derive the backend OIDC env from the runtime-config SSOT once at startup so
  // the three surfaces validate the SAME issuer/audience the SPA logs in against
  // — no per-developer hardcoding, no drift. Explicit env values always win
  // (applyOidcDefaults only fills gaps + sets oidc_jwt where not opted out).
  const backendEnv = applyOidcDefaults(process.env, loadDevRuntimeConfig());
  const selectedConfig = {
    ...config,
    surfaces: selectDevStackSurfaces(config, process.env.FCC_DEV_STACK_SURFACES),
  };
  for (const cmd of buildBackendCommands(selectedConfig, { pythonBin })) {
    start(cmd.key, cmd.pythonBin, cmd.args, cmd.cwd, backendEnv);
  }
  // The Vite dev server is the gateway itself (no backend OIDC env needed).
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  start('gateway', npm, buildGatewayCommand(), APP_ROOT);
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  main();
}
