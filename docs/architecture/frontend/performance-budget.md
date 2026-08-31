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
- **Baseline (2026-05-26)**: `measuredGzipBytes = 236284` (≈ 231 KB gzip).
  Dominant chunks: `observability` (OpenTelemetry + Sentry) ≈ 100 KB, `react`
  vendor ≈ 65 KB, app `index` ≈ 37 KB. The FE-P5 `control` + FE-P6 `reports`
  routes are lazy code-split (≈ 2.3 KB / 2.2 KB gzip each), so feature growth
  does not bloat the initial chunk.
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
| `frontend-build.yml` | codegen drift + typecheck + lint + unit tests + build + **bundle budget** |
| `frontend-e2e.yml` | Playwright e2e (incl. axe-core a11y) + Lighthouse CI (a11y/best-practices error, perf warn) |
| `frontend-deploy.yml` | manual dispatch — re-gates then publishes `frontend-dist` artifact behind the protected `frontend-production` environment (deploy target is env-configured, not fabricated) |
| `frontend-qa-evidence.yml` | validates committed browser-QA / deployment evidence manifests against their schemas + emits canonical templates |

## Re-measure procedure

```bash
cd apps/web
node scripts/measure-bundle.mjs --build      # writes dist/bundle-size.json
# update bundle-budget.json: measuredGzipBytes = new totalGzipBytes,
# maxGzipBytes = ceil(measuredGzipBytes * headroomFactor), bump measuredAt.
node scripts/check-bundle-budget.mjs          # must pass (derivation + budget)
```
