import { type Middleware } from 'openapi-fetch';

import { authorizationHeader } from '@/auth/route-guard';
import { lastRefreshWasThrottled, refreshAuthSession, signOut } from '@/auth/session';

/**
 * Shared auth middleware for every openapi-fetch typed client — Increment 6
 * (fe-multitab-auth-robustness).
 *
 * Two responsibilities, consolidated here so the three clients (session /
 * headless / platform) share ONE SSOT instead of each copy-pasting an inline
 * `authorizationMiddleware`:
 *
 *   1. onRequest — attach the current bearer token (omitted when
 *      unauthenticated; the backend returns 401/403 and view code handles it).
 *   2. onResponse — 401 retry-once: when a request comes back 401 (an
 *      access_token that expired mid-flight, e.g. the tab was backgrounded past
 *      the silent-refresh timer), attempt a single on-demand silent refresh and
 *      replay the original request with the fresh token. If the refresh fails,
 *      sign out with `token_expired`.
 *
 * Loop guard: the replay is issued via a direct `fetch` of a pristine clone,
 * which bypasses this middleware, so a second 401 cannot re-enter `onResponse`
 * — one retry maximum, structurally. The `RETRY_GUARD_HEADER` flag +
 * `retriedRequests` set are belt-and-suspenders: if a future refactor ever
 * routes the replay back through the client, they still cap it at one retry.
 */

/** Header marking a request that is itself a 401-driven replay. Its presence
 *  suppresses clone-stashing (onRequest) and any further retry (onResponse). */
const RETRY_GUARD_HEADER = 'X-Auth-Retry';

// A request body is a one-shot stream consumed by the fetch, so a 401 replay
// needs a clone captured BEFORE the fetch. Keyed by the exact Request object
// openapi-fetch threads from onRequest through fetch into onResponse.
const requestClones = new WeakMap<Request, Request>();
const retriedRequests = new WeakSet<Request>();

export const authRetryMiddleware: Middleware = {
  onRequest({ request }) {
    const header = authorizationHeader();
    if (header !== null) {
      request.headers.set('Authorization', header);
    }
    // Stash a pristine clone unless this request is already a replay (loop
    // guard) — the replay must never itself be retried.
    if (request.headers.get(RETRY_GUARD_HEADER) === null) {
      requestClones.set(request, request.clone());
    }
    return request;
  },
  async onResponse({ request, response }) {
    if (response.status !== 401) {
      requestClones.delete(request);
      return response;
    }
    const clone = requestClones.get(request);
    requestClones.delete(request);
    // Loop guard: no clone (this WAS a replay, or stashing was skipped) or the
    // request is already flagged retried → give up, surface the 401.
    if (clone === undefined || retriedRequests.has(request)) {
      return response;
    }
    const refreshed = await refreshAuthSession();
    if (!refreshed) {
      // ⚠️ **A THROTTLE IS NOT AN EXPIRED SESSION — and this door was missed.**
      //
      // The scheduled refresh path learned to reschedule on a 429 rather than
      // sign out; this path, which runs whenever an ordinary API call 401s,
      // still signed out. So the failure the server's rotation budget was
      // sized to prevent was still reachable — and reachable *on demand* by an
      // attacker holding a captured refresh token: burn the victim's rotation
      // budget, and their next API call ends their session. Adversarial review
      // measured it by driving this middleware.
      //
      // Surfacing the 401 keeps the session: the scheduled retry the session
      // module armed will refresh after the window the server named.
      if (lastRefreshWasThrottled()) {
        return response;
      }
      signOut('token_expired');
      return response;
    }
    const header = authorizationHeader();
    if (header !== null) {
      clone.headers.set('Authorization', header);
    }
    clone.headers.set(RETRY_GUARD_HEADER, '1');
    retriedRequests.add(clone);
    return fetch(clone);
  },
};
