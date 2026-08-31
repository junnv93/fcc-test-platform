# Cross-tech token lifetime + refresh policy

**Status**: Accepted
**Date**: 2026-05-23 (Sprint S2-α)
**Scope**: Aligns frontend `MIN_REFRESH_MARGIN_SECONDS` (`apps/web/src/auth/session.ts`)
with backend OIDC JWT validation (`src/application/headless/oidc_principal_resolver.py::BearerTokenPrincipalResolver`)
and the dev IdP token lifetime policy (`infra/idp-policy.json`).

---

## Industry-standard reference values

| Value | Source | Standard reference |
|-------|--------|-------------------|
| `MIN_REFRESH_MARGIN_SECONDS = 30` | `apps/web/src/auth/session.ts` | RFC 6749 § 5.1 — "the authorization server SHOULD set [expires_in] conservatively …". Auth0 / Microsoft Identity / Keycloak all recommend 15–60 s pre-expiry refresh for SPA public clients; 30 s is the median. Gives a client one round-trip budget. |
| `accessTokenLifespanSeconds = 600` (dev) | `infra/idp-policy.json` | Short access tokens are an OAuth 2.0 best practice — limits damage when a token is leaked. 10 min dev value exercises silent refresh frequently. Production realms may use 1–4 h depending on threat model. |
| `ssoSessionIdleSeconds = 1800` (dev) | `infra/idp-policy.json` | Operator switches tabs mid-measurement without re-auth. 30 min is the typical "active session" threshold. |
| `ssoSessionMaxSeconds = 28800` (dev) | `infra/idp-policy.json` | 8 h matches a long lab session ceiling. Forces re-authentication at shift boundary. |

---

## Interaction matrix — what fires when

```
T=0          T=570         T=600                   T=1800
 │            │             │                       │
 │ login      │ silent      │ access_token          │ SSO idle
 │ complete   │ refresh     │ would expire          │ timeout
 │            │ fires       │ (refresh             │ (operator
 │            │ (margin=30) │  already grabbed     │  must re-auth
 │            │             │  new one)             │  if reopens tab)
 │            │             │                       │
─┴────────────┴─────────────┴───────────────────────┴──→ time (s)
```

- The 30 s margin (`MIN_REFRESH_MARGIN_SECONDS`) guarantees the silent
  refresh completes ~30 s before the access_token would expire on the
  IdP — even with one full round-trip stall, the client never sends an
  expired bearer to the backend.
- `accessTokenLifespanSeconds = 600` means silent refresh fires every
  `(600 − 30) = 570 s` ≈ 9.5 min in dev.

---

## Backend interaction (`PyJWT.decode(leeway=...)`)

`src/application/headless/oidc_principal_resolver.py::BearerTokenPrincipalResolver._decode`
calls `jwt.decode(token, ..., audience=..., issuer=...)`. Today this
uses PyJWT's default `leeway=0` — the token is considered expired the
instant `exp` passes.

The frontend's 30 s margin means a fresh access_token reaches the
backend at most ~9.5 min after `iat`, with `~30 s` of headroom before
expiry. Backend leeway of 0 is therefore safe: the silent-refresh
guard fires before backend expiry kicks in.

If a production deployment introduces clock skew (federated IdP vs
backend host), set backend `leeway=30` (matching the frontend margin)
to absorb up to 30 s of skew without triggering false `401`s. A larger
leeway would erode the security benefit of short access tokens.

---

## Runbook — tuning the tokens (S2-γ β-P1-6 + S2-δ γ-P1-1 + γ-P2-3)

Two distinct concepts share the 30-second value but **must be tuned
independently** — pre-S2-δ they were conflated, and the S2-γ audit
caught the bug.

### Concept 1 — clock-skew tolerance (`OIDC_CLOCK_TOLERANCE_SECONDS = 60`)

The number of seconds of clock drift between the IdP and the RP
(backend + frontend) that JWT validation will accept on `exp` / `iat`
/ `nbf`. Same value on both sides:

Sprint S2-ε δ-P1-1 — bumped from 30 → **60 (Auth0 default)** so this
value is genuinely independent from `OIDC_REFRESH_MARGIN_SECONDS = 30`.
Distinct values force the tuner to consider the two concerns
separately; identical values made the cross-check trivially pass.

- backend: `src/application/headless/oidc_principal_resolver.py::OIDC_CLOCK_TOLERANCE_SECONDS`
  consumed by `jwt.decode(..., leeway=…)`.
- frontend: `apps/web/src/auth/session.ts::OIDC_CLOCK_TOLERANCE_SECONDS`
  consumed by `jose.jwtVerify(..., { clockTolerance: … })`.

### Concept 2 — silent-refresh schedule margin (`OIDC_REFRESH_MARGIN_SECONDS`)

The number of seconds before the access token's `exp` that the SPA's
silent-refresh path fires. Client-side scheduling only — backend
keeps the constant so the cross-check invariant can verify both sides
agree on the operational value.

- backend: `src/application/headless/oidc_principal_resolver.py::OIDC_REFRESH_MARGIN_SECONDS`
- frontend: `apps/web/src/auth/session.ts::MIN_REFRESH_MARGIN_SECONDS`

### Tuning procedure (one concept at a time)

1. Decide which concept needs tuning (almost never both at once — they
   solve different problems).
2. Edit the backend constant (source of truth).
3. Edit the frontend mirror (same integer).
4. Run:
   ```
   python -m pytest tests/test_apps_web_auth_scaffold.py -q -k "RefreshMargin or ClockTolerance"
   ```
   Must pass — the strong cross-check imports the actual backend value
   and grep-verifies the frontend literal.
5. If the new value crosses the warning band noted in "When to revisit
   this policy" below, also update the rationale comment in the
   backend constant's docstring AND in this document.
6. Commit both files together. The backend cross-check invariant
   blocks PRs where only one side has changed.

Reverting follows the same order in reverse.

## When to revisit this policy

1. `accessTokenLifespanSeconds` drops below `4 × MIN_REFRESH_MARGIN_SECONDS = 120` — silent
   refresh fires more than once per access_token lifetime; either raise
   the lifetime or lower the margin (no smaller than 15 s per industry
   guidance).
2. Production IdP imposes hard short token lifetimes (e.g. Auth0
   default 24 h) — adjust `idp-policy.production.json` and verify the
   bench `silent_refresh_overhead < 1 % access_token_lifetime` invariant
   still holds.
3. Federated IdP with measured clock skew > 5 s — set backend
   `PyJWT.decode(leeway=...)` to absorb skew explicitly.
4. WebSocket connection times out before silent refresh fires — switch
   to per-message bearer reattachment instead of per-connection.

---

## Cross-tech SSOT touchpoints (do not drift)

| File | Owns |
|------|------|
| `apps/web/src/auth/session.ts::MIN_REFRESH_MARGIN_SECONDS` | client-side refresh margin (this document) |
| `apps/web/src/auth/session.ts::CLAIM_PERMISSIONS` / `_SCOPE` / `_ROLES` | claim-name SSOT — mirrors backend `HttpAuthConfig` defaults |
| `src/application/common/auth_config.py::HttpAuthConfig` | backend OIDC config dataclass (F-2-D4) |
| `infra/idp-policy.json` | IdP token lifetime SSOT (this document) |
| `infra/keycloak/fcc-dev-realm.json` | dev realm import — `accessTokenLifespan` / `ssoSessionIdleTimeout` / `ssoSessionMaxLifespan` byte-identity-match the policy JSON |
| `tests/test_apps_web_auth_scaffold.py::TestIdpPolicySsotCrossCheck` | enforces the byte-identity invariant |
| `tests/test_apps_web_auth_scaffold.py::TestCrossTechTokenPolicyDocs` | enforces that this document exists + cites the RFC + describes the interaction matrix |
