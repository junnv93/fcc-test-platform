import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';

// Import the browser runtime directly. Importing @playwright/test from a
// globalSetup module makes Playwright recursively load its own test runner on
// Node 24, which can deadlock before the first worker starts.
import { chromium } from 'playwright';

import {
  buildHarnessBrowserEnv,
  resolveHarnessBrowserChannel,
  resolveHarnessBrowserExecutable,
  summarizeHarnessBrowserRuntime,
} from './harness-browser-runtime.mjs';

const PLAYWRIGHT_CLI = new URL('../node_modules/playwright/cli.js', import.meta.url);

export function parsePlaywrightInstallDepsDryRunOutput(output) {
  const lines = output
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  const aptLine = lines.find((line) => line.includes('apt-get install')) ?? '';
  const packageTokens = aptLine
    .replace(/^.*apt-get install -y --no-install-recommends\s+/, '')
    .replace(/["']\s*$/, '')
    .split(/\s+/)
    .filter(Boolean)
    .filter((token) => !token.startsWith('apt-get') && token !== 'update&&');
  return {
    rawOutput: output,
    command: aptLine,
    packages: packageTokens,
    unsupportedOsFallback: lines.some((line) => line.includes('not officially supported')),
  };
}

export function runPlaywrightInstallDepsDryRun(env = process.env) {
  const localCli = PLAYWRIGHT_CLI.pathname;
  const command = existsSync(localCli)
    ? process.execPath
    : process.platform === 'win32'
      ? 'npx.cmd'
      : 'npx';
  const args = existsSync(localCli)
    ? [localCli, 'install-deps', 'chromium', '--dry-run']
    : ['playwright', 'install-deps', 'chromium', '--dry-run'];
  const result = spawnSync(command, args, {
    cwd: new URL('..', import.meta.url),
    encoding: 'utf8',
    env,
  });
  const output = `${result.stdout ?? ''}${result.stderr ?? ''}`.trim();
  return {
    ...parsePlaywrightInstallDepsDryRunOutput(output),
    exitCode: result.status ?? 0,
  };
}

function formatAptCommand(packages) {
  return packages.length === 0 ? '' : `sudo apt-get install ${packages.join(' ')}`;
}

export async function ensurePlaywrightBrowserRuntimeReady() {
  if (process.env.PW_TEST_CONNECT_WS_ENDPOINT) {
    return {
      ok: true,
      skipped: true,
      reason: 'remote Playwright server endpoint is configured; local browser preflight skipped',
      endpoint: process.env.PW_TEST_CONNECT_WS_ENDPOINT,
    };
  }

  if (process.platform !== 'linux') {
    return {
      ok: true,
      skipped: true,
      reason: `browser runtime preflight is Linux-only (current=${process.platform})`,
    };
  }

  const channel = resolveHarnessBrowserChannel(process.env);
  if (channel) {
    const browser = await chromium.launch({
      channel,
      headless: true,
      env: buildHarnessBrowserEnv(),
    });
    await browser.close();
    return {
      ok: true,
      channel,
      browserExecutableEnvKeys: ['FCC_PLAYWRIGHT_BROWSER_CHANNEL', 'FCC_CAPTURE_BROWSER_CHANNEL'],
      reason: 'Playwright branded browser channel launch succeeded.',
    };
  }

  const executablePath = resolveHarnessBrowserExecutable(chromium.executablePath());
  const summary = summarizeHarnessBrowserRuntime(executablePath, buildHarnessBrowserEnv());
  if (summary.ok) return summary;

  const officialDryRun = runPlaywrightInstallDepsDryRun(process.env);
  const officialInstallCommand = officialDryRun.command || '';
  const fallbackInstallCommand = formatAptCommand(summary.suggestedAptPackages);
  const message = [
    'Playwright browser runtime preflight failed.',
    `Executable: ${summary.executablePath}`,
    summary.executableExists ? '' : 'Executable path does not exist on this host.',
    summary.lddExitCode === 0 ? '' : `ldd exited with code ${summary.lddExitCode}.`,
    `Missing shared libraries: ${summary.missingLibraries.join(', ') || 'unknown'}`,
    officialInstallCommand
      ? `Official Playwright dry-run install command: ${officialInstallCommand}`
      : '',
    !officialInstallCommand && fallbackInstallCommand
      ? `Fallback install command: ${fallbackInstallCommand}`
      : '',
    officialDryRun.unsupportedOsFallback
      ? 'Playwright reports this host OS as unsupported and falls back to ubuntu20.04-x64 dependency guidance.'
      : '',
    `Diagnostics: ${JSON.stringify({ ...summary, officialDryRun })}`,
  ]
    .filter(Boolean)
    .join('\n');
  const error = new Error(message);
  error.cause = { ...summary, officialDryRun };
  throw error;
}

async function main() {
  try {
    const summary = await ensurePlaywrightBrowserRuntimeReady();
    console.log(JSON.stringify(summary, null, 2));
  } catch (error) {
    const diagnostics = error?.cause ?? { message: String(error?.message ?? error) };
    console.error(JSON.stringify(diagnostics, null, 2));
    process.exitCode = 1;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}

/**
 * Refuse a Node version outside `package.json`'s `engines.node`.
 *
 * Lives here rather than in a lane-named module (its previous home) because it
 * is a property of the runtime, not of any one live lane — the module it came
 * from was deleted precisely because everything in it had been named after the
 * first lane that needed it.
 */
export function assertSupportedNodeRuntime(version = process.versions.node, range) {
  const effectiveRange =
    range ??
    JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).engines?.node;
  const match = /^>=(\d+)\.(\d+)\s+<(\d+)$/.exec(effectiveRange ?? '');
  if (!match) throw new Error(`Unsupported package.json engines.node contract: ${effectiveRange}`);
  const [, minMajor, minMinor, maxMajor] = match.map(Number);
  const [major, minor] = version.split('.').map(Number);
  if (major < minMajor || (major === minMajor && minor < minMinor) || major >= maxMajor) {
    throw new Error(`Node ${version} does not satisfy apps/web engines.node ${effectiveRange}`);
  }
}
