import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

import { chromium } from 'playwright';

const LHCI_CLI = resolve('node_modules/@lhci/cli/src/cli.js');
const configuredChromePath = process.env.CHROME_PATH;
const playwrightChromePath = chromium.executablePath();
const chromePath =
  configuredChromePath && existsSync(configuredChromePath)
    ? configuredChromePath
    : existsSync(playwrightChromePath)
      ? playwrightChromePath
      : configuredChromePath;
const env = chromePath === undefined ? process.env : { ...process.env, CHROME_PATH: chromePath };

// Lighthouse's Chrome launcher can inherit a host-specific profile path from
// the browser environment. Keep the profile outside the repository so a run
// cannot create platform-specific debug trees (for example a literal
// `C:\\Users\\...` directory on WSL) that Prettier or documentation capture
// tooling later mistakes for source. The profile is disposable evidence, not
// an input to the audit.
const chromeProfileDir = mkdtempSync(join(tmpdir(), 'fcc-lighthouse-chrome-'));
const lighthouseArgs = [
  'autorun',
  `--collect.settings.chromeFlags=--user-data-dir=${chromeProfileDir}`,
  ...process.argv.slice(2),
];

const child = spawn(process.execPath, [LHCI_CLI, ...lighthouseArgs], {
  cwd: process.cwd(),
  env,
  stdio: 'inherit',
});

child.on('error', (error) => {
  rmSync(chromeProfileDir, { recursive: true, force: true });
  console.error(`Unable to start Lighthouse CI: ${error.message}`);
  process.exitCode = 1;
});

child.on('exit', (code, signal) => {
  rmSync(chromeProfileDir, { recursive: true, force: true });
  process.exitCode = code ?? (signal === null ? 1 : 1);
});
