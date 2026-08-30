import { readFileSync } from 'node:fs';
import net from 'node:net';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { buildViteDevServerSpec } from '../scripts/capture-project-workspace-training.mjs';
import {
  buildHarnessBrowserEnv,
  parseMissingLibraries,
  resolveHarnessBrowserChannel,
  resolveHarnessBrowserExecutable,
  resolveHarnessBrowserLaunchOptions,
  summarizeHarnessBrowserRuntime,
  suggestedAptPackagesForLibraries,
} from '../scripts/harness-browser-runtime.mjs';
import { laneIds, liveLaneRunnerScript } from '../scripts/live-lane-registry.mjs';
import { classifyPostureResponse } from '../scripts/live-stack-readiness.mjs';
import {
  buildPlaywrightDockerServerSpec,
  resolvePlaywrightRuntime,
  readPlaywrightPackageVersion,
  resolveAvailablePlaywrightDockerPort,
  rewriteBaseUrlForDocker,
} from '../scripts/playwright-docker-runtime.mjs';
import {
  assertSupportedNodeRuntime,
  ensurePlaywrightBrowserRuntimeReady,
  parsePlaywrightInstallDepsDryRunOutput,
} from '../scripts/playwright-runtime-preflight.mjs';

const configPath = resolve(__dirname, '../playwright.config.ts');
const configSource = readFileSync(configPath, 'utf-8');
const globalSetupPath = resolve(__dirname, '../playwright.global-setup.cjs');
const globalSetupSource = readFileSync(globalSetupPath, 'utf-8');
const preflightPath = resolve(__dirname, '../scripts/playwright-runtime-preflight.mjs');
const preflightSource = readFileSync(preflightPath, 'utf-8');
const packageJsonPath = resolve(__dirname, '../package.json');
const packageJson = JSON.parse(readFileSync(packageJsonPath, 'utf-8'));

describe('harness browser runtime helpers', () => {
  it('enforces the package Node engine contract before launching live e2e', () => {
    expect(() => assertSupportedNodeRuntime('22.13.0', '>=22.13 <23')).not.toThrow();
    expect(() => assertSupportedNodeRuntime('22.12.0', '>=22.13 <23')).toThrow(/does not satisfy/);
    expect(() => assertSupportedNodeRuntime('24.0.0', '>=22.13 <23')).toThrow(/does not satisfy/);
  });

  it('does not read a proxy-less preview server as a ready stack', () => {
    // Replaces a test that asserted the previous helper's "any 2xx is ready"
    // rule. That rule is unsound here: `vite.config.ts` registers the API proxy
    // under `server:` only, so a preview server answers /platform with the
    // SPA's index.html and HTTP 200 — "there is no backend" read as "ready".
    // The behavioural coverage now lives in scripts/live-lane-registry.test.mjs;
    // this keeps one assertion at the boundary the old test guarded.
    expect(classifyPostureResponse({ status: 200, contentType: 'text/html' })).toBe(
      'not-a-gateway',
    );
    expect(classifyPostureResponse({ status: 403, contentType: 'application/problem+json' })).toBe(
      'gateway-enforced',
    );
  });

  it('scrubs snap library wiring but preserves standard process env', () => {
    const env = buildHarnessBrowserEnv({
      HOME: '/tmp/home',
      PATH: '/usr/bin',
      LD_LIBRARY_PATH: '/snap/lib',
      SNAP: '/snap/codex/current',
      SNAP_LIBRARY_PATH: '/var/lib/snapd/lib/gl',
    });

    expect(env.HOME).toBe('/tmp/home');
    expect(env.PATH).toBe('/usr/bin');
    expect(env.LD_LIBRARY_PATH).toBeUndefined();
    expect(env.SNAP).toBeUndefined();
    expect(env.SNAP_LIBRARY_PATH).toBeUndefined();
  });

  it('extracts missing shared libraries from ldd output', () => {
    const output = [
      'libnspr4.so => not found',
      'libnss3.so => not found',
      'libglib-2.0.so.0 => /lib/x86_64-linux-gnu/libglib-2.0.so.0',
    ].join('\n');

    expect(parseMissingLibraries(output)).toEqual(['libnspr4.so', 'libnss3.so']);
  });

  it('maps missing libraries to apt packages without duplicates', () => {
    expect(
      suggestedAptPackagesForLibraries([
        'libnss3.so',
        'libnssutil3.so',
        'libnspr4.so',
        'libatk-bridge-2.0.so.0',
      ]),
    ).toEqual(['libnss3', 'libnspr4', 'libatk-bridge2.0-0']);
  });

  it('resolves browser executable from the shared env precedence', () => {
    expect(
      resolveHarnessBrowserExecutable('/default/chrome', {
        FCC_CAPTURE_BROWSER_EXECUTABLE: '/legacy/chrome',
      }),
    ).toBe('/legacy/chrome');
    expect(
      resolveHarnessBrowserExecutable('/default/chrome', {
        FCC_PLAYWRIGHT_BROWSER_EXECUTABLE: '/preferred/chrome',
        FCC_CAPTURE_BROWSER_EXECUTABLE: '/legacy/chrome',
      }),
    ).toBe('/preferred/chrome');
    expect(resolveHarnessBrowserExecutable('/default/chrome', {})).toBe('/default/chrome');
  });

  it('resolves browser channel from the shared env precedence', () => {
    expect(
      resolveHarnessBrowserChannel({
        FCC_CAPTURE_BROWSER_CHANNEL: 'chrome',
      }),
    ).toBe('chrome');
    expect(
      resolveHarnessBrowserChannel({
        FCC_PLAYWRIGHT_BROWSER_CHANNEL: 'msedge',
        FCC_CAPTURE_BROWSER_CHANNEL: 'chrome',
      }),
    ).toBe('msedge');
  });

  it('prefers the official Playwright browser channel over a custom executable path', () => {
    expect(
      resolveHarnessBrowserLaunchOptions('/default/chrome', {
        FCC_PLAYWRIGHT_BROWSER_CHANNEL: 'chrome',
        FCC_PLAYWRIGHT_BROWSER_EXECUTABLE: '/custom/chrome',
      }),
    ).toEqual({ channel: 'chrome' });
    expect(resolveHarnessBrowserLaunchOptions('/default/chrome', {})).toEqual({
      executablePath: '/default/chrome',
    });
  });

  it('marks a missing executable path as not ready even when no "not found" libs are parsed', () => {
    const summary = summarizeHarnessBrowserRuntime('/definitely/missing/chrome');
    expect(summary.executableExists).toBe(false);
    expect(summary.ok).toBe(false);
    expect(summary.lddExitCode).not.toBe(0);
    expect(summary.lddOutput).toContain('No such file or directory');
  });

  it('derives a pinned Playwright Docker image from package.json SSOT', () => {
    const version = readPlaywrightPackageVersion(packageJson);
    expect(version).toBe('1.48.2');
    expect(buildPlaywrightDockerServerSpec({ packageJsonSource: packageJson }).image).toBe(
      'mcr.microsoft.com/playwright:v1.48.2-noble',
    );
  });

  it('selects an available ephemeral Docker server port when the preferred port is occupied', async () => {
    const server = net.createServer();
    await new Promise<void>((resolveListen) => server.listen(0, '127.0.0.1', resolveListen));
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Expected a TCP server address');
    try {
      const selected = await resolveAvailablePlaywrightDockerPort(address.port);
      expect(selected).not.toBe(address.port);
      expect(selected).toBeGreaterThan(0);
    } finally {
      await new Promise<void>((resolveClose, rejectClose) => {
        server.close((error) => (error ? rejectClose(error) : resolveClose()));
      });
    }
  });

  it('rewrites localhost base URLs for the Docker browser host alias only when needed', () => {
    expect(rewriteBaseUrlForDocker('http://127.0.0.1:4173')).toBe('http://hostmachine:4173/');
    expect(rewriteBaseUrlForDocker('http://localhost:4173/path?q=1')).toBe(
      'http://hostmachine:4173/path?q=1',
    );
    expect(rewriteBaseUrlForDocker('https://fcc.example.test/app')).toBe(
      'https://fcc.example.test/app',
    );
  });

  it('derives runtime from CLI arg, capture env, then shared env', () => {
    expect(resolvePlaywrightRuntime(['--runtime=docker-server'], {})).toBe('docker-server');
    expect(resolvePlaywrightRuntime([], { FCC_CAPTURE_RUNTIME: 'docker-server' })).toBe(
      'docker-server',
    );
    expect(resolvePlaywrightRuntime([], { FCC_PLAYWRIGHT_RUNTIME: 'docker-server' })).toBe(
      'docker-server',
    );
    expect(resolvePlaywrightRuntime([], {})).toBe('host');
  });

  it('derives the capture server port from its base URL and exposes Docker captures', () => {
    expect(buildViteDevServerSpec('http://127.0.0.1:4173', 'host')).toMatchObject({
      readinessUrl: 'http://127.0.0.1:4173/',
      args: expect.arrayContaining(['--host', '127.0.0.1', '--port', '4173']),
    });
    expect(buildViteDevServerSpec('http://127.0.0.1:5173', 'docker-server').args).toEqual(
      expect.arrayContaining(['--host', '0.0.0.0', '--port', '5173']),
    );
  });

  it('keeps capture routes root-relative after Docker URL rewriting', () => {
    const dockerBase = rewriteBaseUrlForDocker('http://127.0.0.1:5173');
    expect(new URL('/my-projects', dockerBase).toString()).toBe(
      'http://hostmachine:5173/my-projects',
    );
  });
});

describe('playwright runtime preflight wiring', () => {
  it('registers the global preflight in playwright.config.ts', () => {
    expect(configSource).toMatch(/globalSetup:\s*['"]\.\/playwright\.global-setup\.cjs['"]/);
  });

  it('keeps the Node 24-safe preflight boundary on the shared browser runtime helper', () => {
    expect(globalSetupSource).toMatch(/playwright-runtime-preflight\.mjs/);
    expect(globalSetupSource).toMatch(/spawnSync\(process\.execPath/);
    expect(globalSetupSource).toMatch(/timeout:\s*PREFLIGHT_TIMEOUT_MS/);
    expect(preflightSource).toMatch(
      /resolveHarnessBrowserExecutable\(chromium\.executablePath\(\)\)/,
    );
    expect(preflightSource).toMatch(/buildHarnessBrowserEnv\(\)/);
  });

  it('exposes a preflight script and one live script per registered lane', () => {
    expect(packageJson.scripts['test:e2e:preflight']).toBe(
      'node scripts/playwright-runtime-preflight.mjs',
    );
    // DERIVED from the lane registry rather than listing the lanes here: the
    // previous version named one lane and its runner, so adding a lane left the
    // script set unasserted — the drift this wave removes.
    const lanes = laneIds();
    expect(lanes.length).toBeGreaterThan(1);
    for (const laneId of lanes) {
      expect(packageJson.scripts[`test:e2e:live:${laneId}`]).toBe(
        // The registry is a JavaScript SSOT module consumed across Node/TS
        // boundaries; the runtime test validates its resolved script value.
        // eslint-disable-next-line @typescript-eslint/no-unsafe-call
        liveLaneRunnerScript(laneId),
      );
    }
  });

  it('parses the official playwright install-deps dry-run output', () => {
    const parsed = parsePlaywrightInstallDepsDryRunOutput(
      [
        'BEWARE: your OS is not officially supported by Playwright; installing dependencies for ubuntu20.04-x64 as a fallback.',
        'sudo -- sh -c "apt-get update&& apt-get install -y --no-install-recommends libnss3 libnspr4 libx11-6"',
      ].join('\n'),
    );

    expect(parsed.unsupportedOsFallback).toBe(true);
    expect(parsed.command).toContain('apt-get install -y --no-install-recommends');
    expect(parsed.packages).toEqual(['libnss3', 'libnspr4', 'libx11-6']);
  });

  it('skips local preflight when a remote Playwright server endpoint is configured', async () => {
    const original = process.env.PW_TEST_CONNECT_WS_ENDPOINT;
    process.env.PW_TEST_CONNECT_WS_ENDPOINT = 'ws://127.0.0.1:3000/';
    try {
      await expect(ensurePlaywrightBrowserRuntimeReady()).resolves.toMatchObject({
        ok: true,
        skipped: true,
        endpoint: 'ws://127.0.0.1:3000/',
      });
    } finally {
      if (original === undefined) delete process.env.PW_TEST_CONNECT_WS_ENDPOINT;
      else process.env.PW_TEST_CONNECT_WS_ENDPOINT = original;
    }
  });
});
