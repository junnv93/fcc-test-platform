---
name: verify-apps-web-scaffold
description: Sprint S1 (2026-05-23) `apps/web/` React+Vite+TS frontend scaffold invariant — ADR-0001~0006 정합 + TS strict full + 보안 헤더 meta tags + Zod runtime config + observability bootstrap + OpenAPI codegen SSOT chain + 하드코딩 budget 영구 금지. Phase 0 (2026-05-29) dev/prod CSP source separation 추가 — prod meta CSP 음성 단언(localhost/127.0.0.1/단독 http: 부재) + dev CSP helper(`vite/dev-csp.ts`)가 runtime-config origin 에서 connect-src 파생(host/port 하드코딩 0) + served stub ↔ `runtime-config.dev.json` drift guard + Vite header/meta-strip 배선. repository-local Python gate가 Node 22/npm engine 검증 후 실제 production build도 실행.
version: 2026-05-23
source: FCC pytest invariants wrapper (AD-1)
disable-model-invocation: false
mapped_invariants:
  - tests/test_apps_web_scaffold.py
  - tests/test_frontend_build_gate.py
trigger_patterns:
  - apps/web/vite/*
  - apps/web/vite/**/*
  - apps/web/vite.config.ts
  - apps/web/tsconfig*.json
  - apps/web/eslint.config.js
  - apps/web/package.json
  - apps/web/index.html
  - apps/web/public/runtime-config.dev.json
  - apps/web/scripts/write-runtime-config-stub.mjs
  - apps/web/scripts/write-dev-runtime-config.mjs
  - scripts/frontend_build_gate.py
  - tests/test_frontend_build_gate.py
---
# verify-apps-web-scaffold

## 대상 invariant

`apps/web/` 디렉토리의 React + TypeScript + Vite scaffold 가 ADR-0001~0006
decision 과 정합을 유지함을 backend Python pytest 단독으로 봉인한다 — frontend
toolchain (`npm run typecheck` / `lint` / `test`) 이 도는 환경 외에서도
scaffold drift 가 검출되도록.

| pytest test ID | 검증 내용 |
|----------------|----------|
| `tests/test_apps_web_scaffold.py::TestAppsWebDirectoryShape` | ADR-0001 monorepo layout — `apps/web/` 디렉토리 + 29 필수 파일 존재 |
| `tests/test_apps_web_scaffold.py::TestPackageJsonInvariants` | private + engines.node ≥ 20 + scripts (dev/build/preview/codegen/codegen:check/typecheck/lint/format/test/test:e2e) + ADR-0002 stack deps (react / react-dom / react-router-dom / @tanstack/react-query / zod / openapi-fetch) + ADR-0004 (@opentelemetry/sdk-trace-web / exporter-otlp-http / instrumentation-fetch) + ADR-0006 (@sentry/browser / web-vitals) + dev-deps (openapi-typescript / typescript / vite / vitest / @playwright/test / eslint / prettier) |
| `tests/test_apps_web_scaffold.py::TestTypescriptStrictInvariants` | tsconfig.compilerOptions strict full 10 flag (strict / noImplicitAny / noUnusedLocals / noUnusedParameters / noFallthroughCasesInSwitch / noUncheckedIndexedAccess / exactOptionalPropertyTypes / noImplicitOverride / noImplicitReturns / forceConsistentCasingInFileNames) |
| `tests/test_apps_web_scaffold.py::TestAdrSeriesPresent` | ADR-0001~0006 6 ADR 파일 + README 인덱스 + 각 ADR 본문 reference |
| `tests/test_apps_web_scaffold.py::TestRuntimeConfigSsotInvariants` | runtime.ts Zod schema export + RuntimeConfigError + getRuntimeConfig + `oidcClientSecret` property declaration/access 0 (public-client) |
| `tests/test_apps_web_scaffold.py::TestCodegenScriptSsot` | scripts/codegen.mjs 가 backend `docs/api/session-api.openapi.json` (F-2-D3 SSOT) 참조 + `--check` 모드 (CI gate) |
| `tests/test_apps_web_scaffold.py::TestObservabilityModulesPresent` | ADR-0004 W3CTraceContextPropagator + ParentBasedSampler + ADR-0006 @sentry/browser maskAllText default + web-vitals 5 metric (onCLS/onINP/onLCP/onTTFB/onFCP) |
| `tests/test_apps_web_scaffold.py::TestIndexHtmlSecurityHeaders` | index.html 보안 헤더 meta — CSP (frame-ancestors none / object-src none) + Referrer-Policy + X-Content-Type-Options + runtime-config.js LOAD ORDER (BEFORE main.tsx) |
| `tests/test_apps_web_scaffold.py::TestRootGitignoreCoversAppsWeb` | root .gitignore — apps/**/node_modules + dist + coverage + playwright-report + generated types 가드 |
| `tests/test_apps_web_scaffold.py::TestAdrCommitPolicyNoHardcodedThreshold` | 하드코딩 budget magic number 영구 금지 — `MAX_INITIAL_RENDER_MS_P95` / `MIN_LARGE_TABLE_ROWS` / `MAX_DOWNLOAD_GRANT_TTL_SECONDS` AST 0 (Sprint S6/S8 measurement-driven baseline 영역으로 분리) |
| `tests/test_apps_web_scaffold.py::TestFeP5RemoteControl` | FE-P5 (2026-05-26) — 원격 제어 라우트 seal: `control.tsx`/`session-events.ts` 존재 + app.tsx `/control` 라우트 wiring + _layout nav + WS `wsBaseUrl` SSOT(하드코딩 URL 0) + RBAC permission 문자열(`session:control`/`session:events`)이 백엔드 `SESSION_PERMISSION_*` SSOT 와 cross-language 일치 |
| `tests/test_apps_web_scaffold.py::TestFeP6ReportsView` | FE-P6 (2026-05-26) — 리포트/아티팩트 뷰 seal: `reports.tsx` 존재 + app.tsx `/reports` 라우트 wiring + RBAC permission 문자열(`report_automation:read`/`headless:read`)이 백엔드 `HEADLESS_API_PERMISSIONS` SSOT 와 cross-language 일치 |
| `tests/test_apps_web_scaffold.py::TestFeP7ProductionGate` | FE-P7 (2026-05-26) — production 게이트 seal: CI/CD 워크플로우 4종(`frontend-{build,e2e,deploy,qa-evidence}.yml`) 존재 + **측정-파생 bundle budget**(`maxGzipBytes == ceil(measuredGzipBytes × headroomFactor)` — magic number 금지) + `check-bundle-budget.mjs` derivation 재검증 + lighthouserc accessibility=error(WCAG AA 게이트, ≥0.9) + performance-budget.md 산출근거 |
| `tests/test_apps_web_scaffold.py::TestFeP4SessionBrowser` | FE-P4 frontend (2026-05-26) — 세션/결과 브라우저 seal: `sessions.tsx` 존재 + app.tsx `/sessions` 라우트 wiring + FE-P4 backend attempts API(`/headless/sessions/{id}/attempts`) 소비 + RBAC `headless:read`(`list_session_attempts`) cross-language SSOT |
| `tests/test_apps_web_scaffold.py::TestFeP2CoverageDashboard` | FE-P2 (2026-05-27) ★1순위 — 프로젝트 커버리지 대시보드 seal: `projects.tsx` 존재 + app.tsx `/projects` 라우트 wiring + FE-P0d central platform read API(`/platform/projects/{id}/coverage`) 소비(로컬 per-target SQLite stopgap 금지 — headless/session-client import 0) + RBAC `platform:read`(`get_project_coverage`) cross-language SSOT |
| `tests/test_apps_web_scaffold.py::TestDevCspSeparation` | Phase 0 (2026-05-29) — dev/prod CSP 소스 분리 seal: prod meta `connect-src` 음성 단언(localhost/127.0.0.1/단독 http:/wildcard 부재 + `'self' https: wss:` 불변) + dev CSP 산출물 존재(`vite/dev-csp.ts`/`vite/runtime-config-source.mjs`/`public/runtime-config.dev.json`/`vite/dev-csp.test.ts`) + dev helper host/port 하드코딩 0(connect-src 는 runtime-config origin 파생) + origin source 공유(stub writer `loadRuntimeConfig` ↔ dev CSP `deriveConnectSrcOrigins`) + served stub ↔ `runtime-config.dev.json` drift guard + `vite.config.ts` header/meta-strip 배선 |
| `tests/test_frontend_build_gate.py` | repository-local production gate — deterministic `apps/web` root, package engine Node 22/npm checks, explicit missing-tool diagnostics, shell-free actual `npm run build`, and build-failure propagation |

## 실행 명령

```bash
python3 scripts/frontend_build_gate.py
python -m pytest tests/test_apps_web_scaffold.py -q
python -m pytest tests/test_frontend_build_gate.py -q
```

## 트리거

- `apps/web/**/*.{ts,tsx,js,mjs,json,html,css}` 변경
- `docs/architecture/frontend/adr/*.md` 변경
- `tests/test_apps_web_scaffold.py` 변경
- `scripts/frontend_build_gate.py` 변경
- `tests/test_frontend_build_gate.py` 변경
- root `.gitignore` 의 `apps/**/...` 패턴 변경
- `CONTRIBUTING.md` (frontend 워크플로우 섹션) 변경

## 비-목표

- 실제 React 동작 검증 — frontend toolchain (`npm run dev` / `npm test` / `npm run test:e2e`) 책임; production build 자체는 위 repository-local gate가 책임
- OpenAPI codegen 산출물 검증 — `npm run codegen:check` CI gate 책임
- Lighthouse CI / Core Web Vitals budget — Sprint S8 신설 예정
- OIDC PKCE 실제 동작 — Sprint S2 신설 예정 (`verify-frontend-auth` redesign)

## 향후 Sprint 별 확장

| Sprint | 추가 invariant |
|--------|---------------|
| S2 | OIDC PKCE TS module (`src/auth/`) + IdP mock compose 정합 |
| S3 | central DB migration SQL 산출물 (ADR-0005) |
| S5~S7 | Job / Session / Report route TS 파일 존재 + TanStack Query hook 패턴 |
| S8 | Lighthouse CI config + Core Web Vitals budget measurement-driven (실측 baseline) |
| S9 | `.github/workflows/frontend-*.yml` 4 workflow 정합 |
| S10 | evidence schema *derived* from scaffold + provider-private leakage scan |
