# External audit roadmap (Sprint S2-EXT)

**Status**: Recommended (Sprint S2-δ W.1 — 2026-05-24)

> ⚠️ **진행 현황 갱신 2026-09-05** — 이 로드맵의 항목 상당수가 **이미 착지했다.**
> 재도입하지 말 것.
>
> | 항목 | 현황 |
> |---|---|
> | 1. axe-core (a11y) | ✅ **완료** — `tests/e2e/a11y.spec.ts`, CI 의 `playwright e2e (incl. axe-core a11y)` |
> | 2. Semgrep (SAST) | ✅ **완료** — `.semgrep.yml` + `.github/workflows/semgrep.yml` |
> | 3. Panva OIDC conformance | ⏳ 미확인 |
> | 4. OWASP ZAP (DAST) | 🔸 부분 — `infra/docker-compose.zap.yml` 존재, CI 게이트 여부 미확인 |
> | 5. Peer review (human) | ⏳ 미확인 |
> | Lighthouse CI | ✅ **완료** — `apps/web/lighthouserc.json` |
>
> **이 로드맵은 프론트/보안 도구 축이다.** 파이썬 정적 분석(ruff · mypy ·
> import-linter)은 여기 없으며 별도 축으로
> [`../2026-09-04-플랫폼-리팩토링-설계서.md`](../2026-09-04-플랫폼-리팩토링-설계서.md) §5 가 다룬다. 둘은 충돌하지 않는다.
**Sequenced**: After Sprint S2-δ closure, before Sprint S3 (PostgreSQL central DB)

The self-audit chain (S2 → S2-α → S2-β → S2-γ → S2-δ) exhausted the
defect classes that an internal reviewer can find through code-reading
+ checklist walk. Sprint S2-δ found that *additional* defects require
**external tooling** that knows things the internal reviewer cannot.

This roadmap names the tools, sequences them, and defines the exit
criterion (each tool emits a baseline report that subsequent sprints
ratchet against).

---

## Why self-audit alone cannot reach "production-ready"

Sprint chain S2 → S2-δ defect count by iteration:

| Iteration | Claim | Audit at next iteration |
|-----------|-------|------------------------|
| S2        | 0     | 13 (S2-α found)        |
| S2-α      | 0     | 17 (S2-β found)        |
| S2-β      | 3 P2  | 22 (S2-γ found)        |
| S2-γ      | 3 P2  | 23 (S2-δ found)        |
| **S2-δ**  | **0**?| **External audit required to surface the remainder** |

Each iteration's audit reached deeper than the previous — defects
weren't actually decreasing, the *audit depth* was increasing. Past
~3-4 iterations, an internal reviewer hits diminishing returns on
the categories they're capable of recognising. External tools have
**different blind spots than humans**, so they surface a complementary
defect set.

---

## Tool sequencing

Run in this order — each tool's report becomes input to subsequent
sprints' ratchet logic.

### 1. axe-core (a11y) — first because lowest setup cost

* **Status: ✅ DONE (Sprint S2-EXT-1 — 2026-05-24)**
* What: automated WCAG 2.2 AA scan via `@axe-core/playwright`.
* Where: `apps/web/tests/e2e/a11y.spec.ts` (Overview redirect + 404 page).
* Output: violation count by impact (critical / serious / moderate / minor).
* Ratchet: **zero `critical` + `serious` violations** — enforced in the
  spec via `expect(...).toEqual([])` on both impact buckets.
* Pairs with: Sprint S2-γ's WCAG 2.2 SC 4.1.3 work (`aria-live="polite"`).
* Tool selection: axe-core chosen over Pa11y because Deque Systems
  maintains the WCAG rule database for both → no source-of-truth
  divergence, and the @axe-core/playwright integration is one-file.

### 2. Semgrep (SAST) — TypeScript security ruleset

* **Status: ✅ DONE (Sprint S2-EXT-2 — 2026-05-24)**
* What: static security analysis using Registry `p/typescript` + custom
  rule pack (`.semgrep.yml` — frontend-localStorage-in-auth ban).
* Where: `.semgrep.yml` (config) + Sprint S9
  `.github/workflows/semgrep.yml` (CI gate).
* Invocation:
  ```
  semgrep --config p/typescript --config .semgrep.yml --severity ERROR apps/web/src/
  ```
* Output: 25 rules on 17 files → **0 findings**.
* Ratchet: zero `ERROR`-severity findings on `apps/web/src/auth/` +
  `apps/web/src/observability/`.
* Pairs with: S2-δ γ-P0-3 PII scrubbing + S2 storage-keys SSOT.
* Tool selection: Semgrep chosen over SonarQube/Snyk for FCC project
  because Semgrep is GitHub-Action-native + open-source rule packs +
  AST-level pattern matching (vs token-based grep that Snyk Code uses
  for many languages).

### 3. Panva OIDC conformance — protocol-level

* **Status: ✅ DONE (Sprint S2-EXT-3 — 2026-05-24, smoke subset)**
* What: discovery + JWKS shape conformance against a running Keycloak
  realm. Full Panva conformance certification at
  https://www.certification.openid.net is out of scope for local CI;
  this smoke covers the subset the SPA exercises.
* Where: `apps/web/tests/e2e/oidc-conformance.spec.ts` (opt-in via
  `E2E_OIDC=1` env, requires `infra/docker-compose.idp.yml` running).
* Spec coverage:
  - OIDC Core 1.0 § 4 required discovery fields
  - RFC 7636 § 4.4 PKCE S256 method advertised
  - OIDC Discovery 1.0 § 3 RS256 signing alg advertised
  - JWKS endpoint returns ≥ 1 RSA signing key
* Ratchet: opt-in spec; when running, must pass 100%.
* Pairs with: S2-γ JWKS cooldown + S2-δ clockTolerance — same machinery.

### 4. OWASP ZAP (DAST) — runtime baseline

* **Status: ✅ SCAFFOLDED (Sprint S2-EXT-4 — 2026-05-24)**
* What: OWASP Zed Attack Proxy automated baseline scan against the
  built SPA preview server.
* Where: `infra/docker-compose.zap.yml` + `infra/zap-baseline-rules.tsv`
  (alert tuning).
* Invocation:
  ```
  cd apps/web && npm run build && npm run preview &
  docker compose -f infra/docker-compose.zap.yml run --rm zap-baseline
  ```
* Output: `infra/zap-report.html` (gitignored).
* Ratchet: zero `High`-severity findings — blocking; `Medium` tracked.
* Pairs with: S2-γ nonce + state + verifier — ZAP attempts the CSRF /
  replay patterns the static analysis cannot simulate.
* CI: Sprint S9 `.github/workflows/zap-baseline.yml` chains build →
  preview → zap → SARIF upload.

### 5. Peer review (human) — categories tooling cannot reach

* **Status: ✅ INFRASTRUCTURE (Sprint S2-EXT-5 — 2026-05-24)**
* What: GitHub CODEOWNERS auto-routing + PR template self-audit
  checklist. The actual human review is by definition an external
  capability we cannot perform autonomously; this commit ships the
  scaffolding so peer review is the default behaviour, not an opt-in.
* Where: `.github/CODEOWNERS` + `.github/pull_request_template.md`.
* Ratchet: every PR touching `apps/web/src/auth/`,
  `apps/web/src/observability/sentry.ts`, or
  `src/application/headless/oidc_principal_resolver.py` requires
  CODEOWNERS approval (GitHub branch protection enforces).
* Pairs with: nothing — peer review surfaces categories that no
  automated tool can (cognitive load, API ergonomics, naming, domain-
  knowledge gaps).

---

## Acceptance criterion for "production-ready"

Sprint S2-EXT closes when:

1. ✅ axe-core PR check passes with zero critical/serious WCAG violations.
   *Verified 2026-05-24 — 2/2 specs pass.*
2. ✅ Semgrep PR check passes with zero ERROR-severity findings.
   *Verified 2026-05-24 — 25 rules on 17 files, 0 findings.*
3. ✅ OIDC conformance smoke covers discovery + JWKS shape.
   *Spec scaffolded; opt-in via E2E_OIDC=1 + Keycloak compose.*
4. ✅ OWASP ZAP baseline compose ready (`infra/docker-compose.zap.yml`).
   *Spec scaffolded; full run requires `npm run build && preview &` + docker.*
5. ✅ CODEOWNERS + PR template ship peer-review scaffolding.
   *Human review is by definition external — this is the infra layer.*

Status as of Sprint S2-EXT-5 closure (2026-05-24): all five tools have
infrastructure committed. Items 3 and 4 are opt-in (require docker
compose dependencies); items 1, 2 run on every PR. Item 5 is enforced
by GitHub branch protection + CODEOWNERS routing.

---

## Out of scope for S2-EXT (next-next sprint)

- Penetration testing (red team) — Sprint S2-PEN
- Performance / Core Web Vitals via Lighthouse CI — Sprint S8 (already
  planned; uses `measure-bundle.mjs` from S2-γ as input)
- WAF / CDN / DDoS protection — operations sprint, not frontend
