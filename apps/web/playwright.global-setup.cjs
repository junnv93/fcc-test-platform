const { spawnSync } = require('node:child_process');
const path = require('node:path');

const PREFLIGHT_TIMEOUT_MS = Number(process.env['FCC_PLAYWRIGHT_PREFLIGHT_TIMEOUT_MS'] ?? 10_000);

module.exports = async function globalSetup() {
  const script = path.join(__dirname, 'scripts', 'playwright-runtime-preflight.mjs');
  const result = spawnSync(process.execPath, [script], {
    cwd: __dirname,
    env: process.env,
    encoding: 'utf8',
    timeout: PREFLIGHT_TIMEOUT_MS,
  });
  if (result.error || result.status !== 0) {
    const output = `${result.stdout || ''}${result.stderr || ''}`.trim();
    throw new Error(
      `Playwright browser runtime preflight failed (exit=${result.status ?? 'unknown'}): ${
        output || result.error?.message || 'no diagnostics'
      }`,
    );
  }
};
