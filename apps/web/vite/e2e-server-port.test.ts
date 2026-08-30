import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { deriveE2eServerPort } from './e2e-server-port';

/**
 * M10 (w4-entry-route-e2e-oidc-ci, 2026-08-01) — webServer port SSOT.
 *
 * Lives under vite/ (not tests/) mirroring vite/dev-csp.test.ts: registered
 * into vitest `include` via `vite/**\/*.test.ts`, kept out of the strict
 * browser tsconfig.json program.
 */

describe('deriveE2eServerPort', () => {
  it('extracts the port from an arbitrary URL', () => {
    expect(deriveE2eServerPort('http://localhost:6006')).toBe('6006');
    expect(deriveE2eServerPort('http://127.0.0.1:9999')).toBe('9999');
  });

  it('falls back to the scheme default when the URL omits a port', () => {
    expect(deriveE2eServerPort('http://example.com')).toBe('80');
    expect(deriveE2eServerPort('https://example.com')).toBe('443');
  });

  it('matches the playwright.config.ts E2E_BASE_URL default byte-identically', () => {
    // The config's fallback must keep resolving to port 5173 when
    // E2E_BASE_URL is unset (CI never sets it) — this pins that behavior
    // independent of the config file itself.
    expect(deriveE2eServerPort('http://localhost:5173')).toBe('5173');
  });
});

describe('playwright.config.ts port literal SSOT', () => {
  const configPath = resolve(__dirname, '../playwright.config.ts');
  const configSource = readFileSync(configPath, 'utf-8');

  it('actually read the config file (lower bound — not a vacuous pass)', () => {
    expect(configSource.length).toBeGreaterThan(0);
  });

  it('contains the port literal 5173 exactly once (the E2E_BASE_URL default)', () => {
    const occurrences = configSource.split('5173').length - 1;
    expect(occurrences).toBe(1);
  });

  it('derives webServer.command and webServer.url from the host-resolvable server URL', () => {
    expect(configSource).toMatch(
      /command:\s*`npm run preview -- --port \$\{E2E_SERVER_PORT}\$\{E2E_WEB_SERVER_HOST_ARG}`/,
    );
    expect(configSource).not.toMatch(/command:\s*['"]npm run preview -- --port \d+['"]/);
    expect(configSource).not.toMatch(/url:\s*['"]http:\/\/localhost:\d+['"]/);
    expect(configSource).toMatch(/url:\s*E2E_WEB_SERVER_URL/);
    expect(configSource).toMatch(/webServer:\s*LIVE_STACK_E2E\s*\?\s*undefined\s*:/);
  });
});
