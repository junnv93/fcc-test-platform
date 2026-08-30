// e2e webServer port derivation — Node runtime only, NOT bundled into the
// browser app (lives in vite/, outside src/, mirrors vite/dev-csp.ts).
// M10 (w4-entry-route-e2e-oidc-ci, 2026-08-01).
//
// PROBLEM: playwright.config.ts declares `E2E_BASE_URL` as the single source
// for `use.baseURL`, but the `webServer` block (command's `--port` flag +
// `url`) used to hardcode port 5173 independently. Overriding `E2E_BASE_URL`
// alone left the spawned preview server listening on the wrong port while
// Playwright polled the right one — a silent split-brain, not a fast failure.
//
// SOLUTION: derive the webServer port from the same `E2E_BASE_URL` value so
// the port literal exists exactly once (the `E2E_BASE_URL` default in
// playwright.config.ts). `url` in the webServer block reuses `E2E_BASE_URL`
// verbatim — only the bare port (for the `--port` CLI flag) needs deriving.
export function deriveE2eServerPort(baseUrl: string): string {
  const { port, protocol } = new URL(baseUrl);
  if (port) {
    return port;
  }
  return protocol === 'https:' ? '443' : '80';
}
