/**
 * Gateway reachability + auth posture, classified on status **and** content type.
 *
 * The helper this replaces probed a few paths and demanded 2xx. That is unsound
 * in one direction and blind in another:
 *
 *  - `vite.config.ts` registers the API proxy under `server:` only, so a
 *    `vite preview` server answers `/platform/...` with the SPA's `index.html`
 *    and HTTP **200**. A 2xx-only probe reads "there is no backend" as "ready".
 *  - With auth enforced, a token-less read is **403** — correct behaviour, but a
 *    2xx-only probe calls it broken.
 *
 * So the question is not "did it return 2xx" but "what answered". JSON means a
 * backend; `application/problem+json` with 401/403 means a backend that is
 * enforcing auth (the repo's RFC 9457 error contract makes that discriminator
 * reliable rather than invented); HTML means no proxy at all.
 */
import { readFileSync } from 'node:fs';

import { GATEWAY_POSTURE } from './live-lane-registry.mjs';

const RUNTIME_CONFIG_PATH = new URL('../public/runtime-config.dev.json', import.meta.url);

/** A read that requires a permission, so an enforcing stack must refuse it. */
export const POSTURE_PROBE_PATH = '/platform/chambers';

/** The dev gateway origin, from the same SSOT the stack launcher reads. */
export function devGatewayOrigin() {
  const runtimeConfig = JSON.parse(readFileSync(RUNTIME_CONFIG_PATH, 'utf8'));
  const configured = runtimeConfig.apiBaseUrl;
  if (typeof configured !== 'string' || configured.trim() === '') {
    throw new Error(
      `runtime-config.dev.json must define a non-empty apiBaseUrl: ${RUNTIME_CONFIG_PATH.pathname}`,
    );
  }
  return new URL(configured).origin;
}

/**
 * Classify one probe response. Exported separately from the fetch so the
 * decision is testable without a socket — the classification is the part that
 * was wrong before, not the transport.
 */
export function classifyPostureResponse({ status, contentType }) {
  const type = String(contentType ?? '').toLowerCase();
  if (type.includes('text/html')) return GATEWAY_POSTURE.NOT_A_GATEWAY;
  if ((status === 401 || status === 403) && type.includes('json')) {
    return GATEWAY_POSTURE.ENFORCED;
  }
  if (status >= 200 && status < 300 && type.includes('json')) {
    return GATEWAY_POSTURE.OPEN;
  }
  // Anything else — a 502 from a dead backend, an empty body, a plain-text
  // error page — is not a gateway we can reason about. Say so rather than
  // guessing a posture and failing later for an unrelated-looking reason.
  return GATEWAY_POSTURE.NOT_A_GATEWAY;
}

export async function probeGatewayPosture(baseUrl, options = {}) {
  const fetchImpl = options.fetchImpl ?? fetch;
  const url = new URL(POSTURE_PROBE_PATH, baseUrl).toString();
  let response;
  try {
    response = await fetchImpl(url, { redirect: 'manual' });
  } catch (error) {
    throw new Error(
      `the live stack is unreachable at ${url}: ${error.message}\n` +
        'Start it first:\n  apps/web/scripts/dev-stack-local.sh --fresh',
    );
  }
  const contentType =
    typeof response.headers?.get === 'function' ? response.headers.get('content-type') : undefined;
  return classifyPostureResponse({ status: response.status, contentType });
}
