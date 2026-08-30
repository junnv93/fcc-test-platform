/**
 * OIDC sessionStorage key SSOT — Sprint S2-α.
 *
 * Sprint S2 originally scattered these constants across two modules
 * (`oidc-pkce.ts` defined STATE / VERIFIER / RETURN_TO; `session.ts`
 * defined TOKENS). The self-audit flagged this as an SSOT drift —
 * adding a new transaction key (e.g. NONCE for OIDC Core 1.0 § 3.1.2.1)
 * required edits in two files.
 *
 * Single source: this module owns every sessionStorage key the auth
 * subsystem touches. `tests/test_apps_web_auth_scaffold.py::TestStorageKeysSsot`
 * AST-checks that no other auth/*.ts file declares its own STORAGE_KEY_*
 * string or `OIDC_STORAGE_PREFIX`-prefixed literal.
 *
 * Naming convention: `${OIDC_STORAGE_PREFIX}:<purpose>` — colon separator
 * matches the legacy `web/platform-shell/auth.js` prefix convention so a
 * future migration can no-op when storage keys collide with a different
 * tab's state. Adding a new key here REQUIRES a paired contract amendment
 * (the audit guard counts the literal set).
 */

/** sessionStorage key prefix for OIDC transactions. */
export const OIDC_STORAGE_PREFIX = 'fcc-oidc';

/** Authorization-request CSRF state (RFC 6749 § 10.12). */
export const STORAGE_KEY_STATE = `${OIDC_STORAGE_PREFIX}:state`;

/** RFC 7636 PKCE code_verifier (held between `startLogin` and `completeLogin`). */
export const STORAGE_KEY_VERIFIER = `${OIDC_STORAGE_PREFIX}:verifier`;

/** OIDC Core 1.0 § 3.1.2.1 nonce — replay protection for the id_token. */
export const STORAGE_KEY_NONCE = `${OIDC_STORAGE_PREFIX}:nonce`;

/** App-relative URL captured at `startLogin` so the post-callback navigation
 *  restores the operator's originally requested deep-link. */
export const STORAGE_KEY_RETURN_TO = `${OIDC_STORAGE_PREFIX}:return-to`;

/** Persisted OidcTokenSet (access / refresh / id token + metadata). */
export const STORAGE_KEY_TOKENS = `${OIDC_STORAGE_PREFIX}:tokens`;

/** Flag set by `signOutAtIdp({ forceReauth: true })`. The next `startLogin`
 *  reads + clears this flag to add `prompt=login` to the auth request,
 *  bypassing any surviving SSO cookie on the IdP. */
export const STORAGE_KEY_FORCE_REAUTH = `${OIDC_STORAGE_PREFIX}:force-reauth`;

/** Sprint S2-γ β-P0-2 + S2-δ γ-P2-10 — OIDC Core 1.0 § 3.1.2.1 `max_age`
 *  echo. Persists the value the RP passed to `startLogin({ maxAge })`
 *  so `completeLogin` can forward it to `verifyIdToken` for the
 *  `auth_time` cross-check.
 *
 *  Key suffix `auth-max-age` (not just `max-age`) avoids semantic
 *  collision with HTTP `Cache-Control: max-age`. The `auth-` prefix
 *  makes the OIDC context unambiguous to anyone grepping sessionStorage. */
export const STORAGE_KEY_MAX_AGE = `${OIDC_STORAGE_PREFIX}:auth-max-age`;

/**
 * Which strategy minted the stored token set — `'oidc'` or `'local'`.
 *
 * ⚠️ It has to be PERSISTED, not merely in memory. After a page reload
 * `restoreSession()` rehydrates tokens from sessionStorage and immediately
 * schedules a silent refresh; without this marker that refresh would take the
 * OIDC path for a locally-issued token, hit the IdP with a refresh_token it
 * never issued, fail, and sign a working operator out. The failure would only
 * appear ~15 minutes after a reload, which is exactly the kind of delay that
 * makes a bug look like "the network".
 *
 * ⚠️ It is NOT a credential and NOT an authorization input — the server never
 * reads it. Tampering with it can only break the holder's own refresh.
 */
export const STORAGE_KEY_AUTH_STRATEGY = `${OIDC_STORAGE_PREFIX}:auth-strategy`;

/** Semantic value written to `STORAGE_KEY_FORCE_REAUTH` when the flag is
 *  set. Sprint S2-β α-12 — replaces the literal `'1'` magic value so the
 *  contract is explicit and tests assert the symbol, not the string.
 *
 *  Sprint S2-δ γ-P2-2 — typed as a TypeScript **literal type** (single
 *  value). The prior commit message and S2-β contract called this a
 *  "Literal Union" which was a misnomer — a Union requires ≥ 2 members.
 *  Adding a second flag value (e.g. `'consent'` for `prompt=consent`)
 *  would extend the type into a true union (`'pending' | 'consent'`)
 *  without revisiting every caller, but until then it is a literal type. */
export type OidcForceReauthFlag = 'pending';
export const OIDC_FORCE_REAUTH_FLAG_VALUE: OidcForceReauthFlag = 'pending';

/** Legacy sessionStorage prefix used by the pre-S2 prototype
 *  (`web/platform-shell/auth.js`). Sprint S2-β α-7 — `purgeLegacyStorage`
 *  scans + deletes every key starting with this prefix on app boot so an
 *  operator who used the prototype before visiting the new SPA cannot
 *  carry a stale state/verifier/token into the modern flow. */
export const LEGACY_OIDC_STORAGE_PREFIX = 'fcc-platform-shell-oidc';

/** All keys grouped for `tests/test_apps_web_auth_scaffold.py` byte-identity
 *  cross-check. Adding a key here MUST be paired with a contract amendment. */
export const ALL_STORAGE_KEYS = Object.freeze([
  STORAGE_KEY_STATE,
  STORAGE_KEY_VERIFIER,
  STORAGE_KEY_NONCE,
  STORAGE_KEY_RETURN_TO,
  STORAGE_KEY_TOKENS,
  STORAGE_KEY_FORCE_REAUTH,
  STORAGE_KEY_MAX_AGE,
  STORAGE_KEY_AUTH_STRATEGY,
]);

/** Sprint S2-ε δ-P0-1 — modern prefix keys that have been *renamed*
 *  between sprints. Operators with a stale sessionStorage from a prior
 *  app version would otherwise carry over a key the current code never
 *  reads — quiet but accumulating cruft. List grows append-only.
 *
 *  Format: `${OIDC_STORAGE_PREFIX}:<old-suffix>` (full key, NOT just suffix).
 *  Add NEW entries to the TOP of the array when renaming, with a
 *  comment naming the rename sprint + the new key. */
const MODERN_PREFIX_RETIRED_KEYS = Object.freeze([
  // S2-γ β-P0-2 used `max-age`; S2-γ β-P2-10 renamed to `auth-max-age`
  // to avoid the HTTP Cache-Control: max-age semantic collision.
  `${OIDC_STORAGE_PREFIX}:max-age`,
]);

/** Sprint S2-β α-7 + S2-γ β-P2-6 + S2-ε δ-P0-1 — purge sessionStorage
 *  keys left behind by:
 *    (a) the legacy `web/platform-shell/auth.js` prototype, AND
 *    (b) earlier versions of the modern auth subsystem that used keys
 *        the current code no longer reads (see MODERN_PREFIX_RETIRED_KEYS).
 *
 *  Called once at app boot (`main.tsx`) before `restoreSession`.
 *  Idempotent — safe to call multiple times. Production code outside
 *  the boot path must not call this (no defensive runtime hook).
 *
 *  Emits `console.info` when any keys are cleared so the migration is
 *  observable (Sentry breadcrumb when Sprint S4 lands). Silent on the
 *  common case (nothing to purge). */
export function purgeLegacyStorage(): number {
  const legacyPrefix = `${LEGACY_OIDC_STORAGE_PREFIX}:`;
  const removed: string[] = [];
  // Iterate descending so removal does not shift indices we have not seen.
  for (let i = globalThis.sessionStorage.length - 1; i >= 0; i -= 1) {
    const key = globalThis.sessionStorage.key(i);
    if (key === null) continue;
    if (key.startsWith(legacyPrefix) || MODERN_PREFIX_RETIRED_KEYS.includes(key)) {
      removed.push(key);
    }
  }
  for (const key of removed) {
    globalThis.sessionStorage.removeItem(key);
  }
  if (removed.length > 0) {
    // S2-ε δ-P0-1 — `console.info` not on eslint allow-list (only warn /
    // error). Use `console.warn` (allowed) — the operator should see
    // migrations even at warn-suppressed log levels.
    console.warn(
      `[auth] purged ${removed.length} retired sessionStorage key(s) ` +
        `(legacy prototype + renamed modern keys)`,
    );
  }
  return removed.length;
}
