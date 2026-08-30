/**
 * Run one live e2e lane.
 *
 * Lane-agnostic: the spec path, the auth profile and the data preconditions all
 * come from `live-lane-registry.mjs`. Adding a lane is a descriptor plus a spec,
 * never a copy of this file — the shape this wave exists to remove.
 *
 * The runner **verifies** the stack; it does not start one. Containers are left
 * detached by design and a harness background job dies with `exit 144`, so
 * spawning a long-lived stack from here produces failures that look like the
 * product. Instead it probes and, on a mismatch, prints the command to run.
 *
 * Everything checkable without a bearer token is checked here, before a browser
 * starts: manifest presence, shape version, selector satisfaction, gateway
 * reachability and auth posture. What remains — that the seeded rows are still
 * in the database the screen talks to — is asserted after login by the fixture,
 * because answering it here would mean this runner minting its own token, and a
 * second authentication path to keep in sync is the duplication being removed.
 */
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import process from 'node:process';

import {
  LIVE_STACK_ENV,
  SEED_MANIFEST_RELATIVE_PATH,
  laneIds,
  resolveLiveLaneDecision,
} from './live-lane-registry.mjs';
import { devGatewayOrigin, probeGatewayPosture } from './live-stack-readiness.mjs';
import {
  resolvePlaywrightRuntime,
  rewriteBaseUrlForDocker,
  startPlaywrightDockerServer,
} from './playwright-docker-runtime.mjs';
import {
  assertSupportedNodeRuntime,
  ensurePlaywrightBrowserRuntimeReady,
} from './playwright-runtime-preflight.mjs';

const APP_ROOT = new URL('..', import.meta.url);
const REPO_ROOT = new URL('../../../', import.meta.url);
const PLAYWRIGHT_CLI = new URL('../node_modules/playwright/cli.js', import.meta.url);

function parseArgs(argv) {
  let laneId;
  const passthrough = [];
  for (const arg of argv) {
    if (arg.startsWith('--lane=')) laneId = arg.slice('--lane='.length);
    else passthrough.push(arg);
  }
  return { laneId, passthrough };
}

export function readSeedManifest(repoRoot = REPO_ROOT) {
  const path = resolve(new URL(repoRoot).pathname, SEED_MANIFEST_RELATIVE_PATH);
  if (!existsSync(path)) return { manifest: null, path };
  try {
    return { manifest: JSON.parse(readFileSync(path, 'utf8')), path };
  } catch (error) {
    // A half-written or corrupt manifest is NOT "never seeded" — say which it
    // is, or the operator re-seeds when the real problem is a bad file.
    throw new Error(`seed manifest at ${path} is not valid JSON: ${error.message}`);
  }
}

function playwrightCommand(specPath, passthrough) {
  const args = ['test', specPath, '--project=chromium-desktop', ...passthrough];
  const localCli = PLAYWRIGHT_CLI.pathname;
  if (existsSync(localCli)) return { command: process.execPath, args: [localCli, ...args] };
  return {
    command: process.platform === 'win32' ? 'npx.cmd' : 'npx',
    args: ['playwright', ...args],
  };
}

async function main() {
  assertSupportedNodeRuntime();
  const { laneId, passthrough } = parseArgs(process.argv.slice(2));
  if (laneId === undefined) {
    console.error(`usage: run-live-e2e.mjs --lane=<${laneIds().join('|')}> [playwright args]`);
    return 2;
  }

  // The runner arms the lane. Running it and then reporting "the lane was
  // switched off" would be a skip dressed as an execution.
  const env = { ...process.env, [LIVE_STACK_ENV]: '1' };

  const gatewayOrigin = env.E2E_BASE_URL ?? devGatewayOrigin();
  const gatewayPosture = await probeGatewayPosture(gatewayOrigin);
  const { manifest, path } = readSeedManifest();

  const decision = resolveLiveLaneDecision({ laneId, env, manifest, gatewayPosture });
  if (decision.kind === 'skip') {
    // Unreachable via this runner (it sets the env above); kept because the
    // decision function is total and swallowing a branch here would hide a
    // future change to what "armed" means.
    console.error(`[live-e2e] ${laneId} skipped: ${decision.reason}`);
    return 1;
  }
  if (decision.kind === 'fail') {
    console.error(`[live-e2e] ${laneId} cannot run.\n${decision.reason}`);
    console.error(`[live-e2e] manifest path: ${path}`);
    return 1;
  }

  console.log(
    `[live-e2e] lane=${laneId} authProfile=${decision.lane.authProfile} ` +
      `gateway=${gatewayOrigin} posture=${gatewayPosture}`,
  );

  env.E2E_BASE_URL = gatewayOrigin;
  if (decision.lane.authProfile === 'oidc') env.E2E_OIDC = '1';

  // The remote-browser runtime is carried over from the lane-named runner this
  // module replaces. It is a property of the *host*, not of any lane, so it is
  // resolved the same way for all three rather than being offered to one.
  const runtime = resolvePlaywrightRuntime();
  let dockerServer;
  if (runtime === 'docker-server') {
    dockerServer = await startPlaywrightDockerServer({ env });
    env.PW_TEST_CONNECT_WS_ENDPOINT = dockerServer.endpoint;
    env.E2E_WEB_SERVER_URL = gatewayOrigin;
    env.E2E_WEB_SERVER_HOST = '0.0.0.0';
    env.E2E_BASE_URL = rewriteBaseUrlForDocker(gatewayOrigin, env);
  } else {
    await ensurePlaywrightBrowserRuntimeReady();
  }

  try {
    const cli = playwrightCommand(decision.lane.spec, passthrough);
    return await new Promise((resolvePromise, reject) => {
      const child = spawn(cli.command, cli.args, {
        cwd: new URL(APP_ROOT).pathname,
        stdio: 'inherit',
        env,
      });
      child.on('exit', (code, signal) => {
        if (signal) reject(new Error(`Playwright terminated by signal ${signal}`));
        else resolvePromise(code ?? 0);
      });
      child.on('error', reject);
    });
  } finally {
    await dockerServer?.stop?.();
  }
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error(`[live-e2e] ${error.message}`);
    process.exit(1);
  },
);
