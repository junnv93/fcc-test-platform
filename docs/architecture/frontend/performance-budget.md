# Frontend Performance Budget & Production Gate (FE-P7, 2026-05-26)

This document is the **rationale SSOT** for the `apps/web` production gates so
no threshold is a fabricated "magic number" — every numeric gate is either a
measured baseline + documented headroom, or an industry-standard normalized
score with a stated basis.

## Bundle size budget (measured + derived)

- **SSOT**: `apps/web/bundle-budget.json`.
- **Measurement tool**: `node apps/web/scripts/measure-bundle.mjs --build` — runs
  `vite build`, gzips each `dist/assets/*.js` chunk, writes
  `dist/bundle-size.json` (`totalGzipBytes`).
- **Baseline**: ⚠️ the numbers live in `bundle-budget.json`, not here. This
  document went stale once already (it kept quoting the 2026-05-26 figure
  `236284` long after the JSON had moved on), so it now names the *shape* and
  points at the SSOT for the values.
  **Current (2026-09-05, main `275069b`)**: `measuredGzipBytes = 402509`
  → ceiling `483011`. Dominant chunks: `index` 83,298 · `sentry-runtime` 77,502
  · `react` 64,913 · `tracing` 36,415 — those four are 65% of the total, and the
  two SDKs (113,917 B, 28%) are **lazy**, which is exactly why a total-only
  budget is not enough (see the second metric below). Route chunks are lazy
  code-split (`my-projects` 4,347 · `test-reports` 4,236 · `projects` 8,082),
  so feature growth does not bloat the entry graph.
- **Derivation**: `maxGzipBytes = ceil(measuredGzipBytes * headroomFactor)` with
  `headroomFactor = 1.2` (20% headroom for normal dependency/feature drift)
  → `283541` bytes. The gate (`scripts/check-bundle-budget.mjs`) **re-derives**
  this on every run, so a hand-inflated ceiling decoupled from the measured
  baseline fails — to legitimately raise the budget you must re-measure and bump
  `measuredGzipBytes`.
- **Enforcement**: `frontend-build.yml` → `measure-bundle.mjs` →
  `check-bundle-budget.mjs` (fails the build over budget).
- **Why 20%**: a single gate threshold; large enough to absorb routine minor
  bumps (a transitive dep patch, a small route) without churn, small enough to
  surface a genuine regression (e.g. an accidental non-lazy heavy import).
  Re-measure when a deliberate large change lands (e.g. a new vendor lib).

## Initial-load-path budget — the metric users feel

⚠️ **This gate enforces TWO budgets and this document used to describe only one.**
`check-bundle-budget.mjs` fails on either.

- **SSOT**: `bundle-budget.json` → `initialLoadPathJs`.
- **Metric**: the entry `<script type="module">` in `dist/index.html` plus every
  `<link rel="modulepreload">` it declares — exactly the JavaScript a user must
  download before the app can run. Lazily `import()`ed route and observability
  chunks are excluded by construction (Vite only preloads the entry's static
  import graph).
- **Why a second metric**: `totalGzipBytes` is blind to *when* a chunk is
  fetched. The 2026-07-31 wave moved 114,561 B gzip off the initial path and the
  total barely moved — a total-only budget scores that win and its exact
  regression as "no change", which is how the defect it was meant to catch
  survived two months.
- **Current (2026-09-05)**: `measuredGzipBytes = 177967` → ceiling `195764`
  (headroom 1.1). Entry graph is 8 chunks; **neither SDK is among them.**
- **Why 10% and not 20%**: the headroom (17,797 B) is deliberately smaller than
  the smallest chunk the 2026-07-31 wave removed (`tracing`, 36,415 B), so
  re-gluing either SDK into the entry graph trips the gate immediately instead
  of being absorbed. Ratchet: lower it when a wave measurably shrinks the
  initial path; raising it needs a recorded justification.

## Lighthouse CI gate (normalized category scores)

- **SSOT**: `apps/web/lighthouserc.json` (run by `npm run lighthouse` = `lhci
  autorun`, and by `frontend-e2e.yml`).
- **Thresholds** are Lighthouse's own **0–1 normalized category scores**, not
  per-app millisecond budgets:
  - `categories:accessibility ≥ 0.9` — **error** (blocking). This is the
    automated **WCAG AA gate**, paired with the `@axe-core/playwright` scan
    wired in Sprint S2-EXT-1 (axe checks colour-contrast + ARIA that Lighthouse
    samples). Together they enforce WCAG AA without a hand-audited per-token
    contrast table.
  - `categories:best-practices ≥ 0.9` — **error** (blocking).
  - `categories:performance ≥ 0.8` — **warn** (non-blocking): runtime
    performance varies with the CI runner; the blocking performance gate is the
    deterministic bundle-size budget above, not a flaky wall-clock score.
  - `categories:seo` — off (internal operator tool, not a public SEO surface).

## Security headers

- **CSP / Referrer-Policy / X-Content-Type-Options**: `apps/web/index.html`
  meta tags (sealed by `TestIndexHtmlSecurityHeaders`).
- **HSTS**: a *server/edge* response header — configured at the deploy host
  (`frontend-deploy.yml` `frontend-production` environment), not in the static
  bundle. Documented here so it is not lost; the static app cannot emit it.
- **SRI**: the only dynamically-injected script is `runtime-config.js` (per-env,
  no stable hash to pin); bundled assets are same-origin Vite output. SRI on
  third-party CDNs applies only if/when one is introduced.

## CI/CD workflows

| Workflow | Gate |
|----------|------|
| `frontend.yml` → `build` | format check + **api-artifacts mirror drift** + codegen + codegen drift + lint + unit tests + production build (typechecks) + measure + **both bundle budgets** |
| `frontend.yml` → `e2e` / `lighthouse` / `oidc-conformance` | Playwright e2e (incl. axe-core a11y) · Lighthouse CI (a11y/best-practices error, perf warn) · real-OIDC conformance. All three consume the `frontend-dist` artifact `build` uploads, so **they do not run when `build` fails** |
| `frontend-deploy.yml` | manual dispatch — re-gates then publishes `frontend-dist` artifact behind the protected `frontend-production` environment (deploy target is env-configured, not fabricated) |
| `frontend-qa-evidence.yml` | validates committed browser-QA / deployment evidence manifests against their schemas + emits canonical templates |

## Re-measure procedure

```bash
cd apps/web
node scripts/measure-bundle.mjs --build      # writes dist/bundle-size.json
# Update BOTH budgets in bundle-budget.json — the gate checks both:
#   total   : measuredGzipBytes = totalGzipBytes
#             maxGzipBytes      = ceil(measuredGzipBytes * headroomFactor)
#   initial : initialLoadPathJs.measuredGzipBytes = initialLoadPathJs.gzipBytes
#             initialLoadPathJs.maxGzipBytes      = ceil(measured * headroomFactor)
# Bump measuredAt on both, and record WHY in measuredCommitNote.
node scripts/check-bundle-budget.mjs          # must pass (derivation + both budgets)
```

⚠️ **Re-deriving is not the default answer to a red gate.** Before bumping a
baseline, establish that the growth is not a regression — the cheapest checks
are (a) is either SDK back in `initialLoadPathJs.chunks`, and (b) does a build
of the previous main show the same number. Record what you found. A baseline
overwritten with the observed value is a gate turned off, which is what the
`_doc` field in the JSON is there to prevent.

⚠️ **A gate that cannot run is worse than a gate that fails.** Measured
2026-09-05: this budget had not executed in CI since 2026-08-31 because an
earlier step in the same job died on a path bug, so five days of drift
accumulated with nobody able to see it. When you fix a red step, check what was
*behind* it.
