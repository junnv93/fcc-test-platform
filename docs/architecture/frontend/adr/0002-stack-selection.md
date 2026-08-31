# ADR-0002: Frontend stack selection

**Status**: Proposed
**Date**: 2026-05-23
**Deciders**: shared web/platform maintainers + user
**Depends on**: ADR-0001 (frontend repo location)

## Context

`apps/web/` (ADR-0001 결정) 에 도입할 frontend stack 의 framework / build / routing / state / form / test 선택을 결정한다.

### Constraints

- backend `docs/api/session-api.openapi.json` (OpenAPI 3.1) + `session-api.asyncapi.json` (AsyncAPI 3.0) 가 SSOT — TS type 자동 생성 호환 stack 필요
- multi-PC measurement test management UX 가 핵심 — table virtualization / WebSocket realtime / form validation 필수
- production target: desktop 운영자 위주 + mobile fallback (responsive)
- 팀원 onboarding cost 고려 — 산업 표준 채택 + 명확한 documentation
- 8 production checklist 충족 (Core Web Vitals / bundle budget / code splitting / accessibility / i18n / security headers / observability / e2e)

## Decision

| 영역 | 선택 | 1줄 근거 |
|------|-----|---------|
| **Framework** | React 18 | 산업 표준, ecosystem maturity, 팀 채용 풀 |
| **Build tool** | Vite 5 | esbuild + Rollup, dev HMR < 100ms, OpenTelemetry plugin 지원 |
| **Language** | TypeScript 5 (strict) | OpenAPI codegen → typed client, shift-left defect detection |
| **Routing** | React Router v6 (data router) | loader/action 패턴, 표준 — TanStack Router 와 양립 |
| **Data fetching** | TanStack Query v5 | cache + retry + invalidation + mutation queue + WS subscription |
| **State** | TanStack Query + URL query + React Context (small) | server-canonical state — Redux / Zustand 별도 store 도입 금지 |
| **Form 검증** | Zod + react-hook-form | OpenAPI → Zod 변환 가능 (`openapi-zod-client`), uncontrolled perf |
| **Unit test** | Vitest | Vite 일관, ESM-native, Jest 호환 API |
| **E2E** | Playwright | multi-browser + multi-viewport (WEB-FE-6 evidence) + screenshot |
| **CSS** | CSS Modules + design token | Tailwind 미채택 — token 기반 design system + RTL/접근성 자유 |
| **Theme** | CSS variables + light/dark | backend `ThemePalette` (Report 페이지 WCAG AA) 패턴 차용 |
| **i18n** | react-i18next | 산업 표준, namespace + lazy load |
| **Observability** | `@opentelemetry/sdk-trace-web` + `web-vitals` (ADR-0004) | distributed tracing + Core Web Vitals |
| **Lint** | ESLint + `@typescript-eslint` + `eslint-plugin-react` + `eslint-plugin-jsx-a11y` | strict + a11y |
| **Format** | Prettier | trailing comma + semicolon + 2-space indent |

## Consequences

### Positive
- TypeScript strict + OpenAPI codegen → backend DTO 변경 시 frontend compile-time error (SSOT chain)
- Vite HMR < 100ms → developer feedback loop tight
- TanStack Query 가 cache/retry/optimistic 추상화 → boilerplate 감소
- vitest + Playwright 가 evidence 측정 가능 (Sprint S10 evidence schema derive)
- Tailwind 미채택 → design token 으로 backend `ThemePalette` 와 일관 SSOT

### Negative
- React 18 concurrent mode 학습 곡선 (Suspense / startTransition)
- TanStack Query v5 가 React Query v3/v4 와 API 차이 — migration guide 필수
- Vitest 가 Jest 대비 ecosystem 약함 (특정 lib mock 시 추가 작업)
- CSS Modules 가 Tailwind 대비 design system 도입 작업 많음 — 단기 비용

## Alternatives Considered

### Framework
- **Next.js**: SSR / file-based routing — overkill. FCC platform 은 admin app (internal traffic) — SSR 불필요. Static SPA + Vite 가 적합.
- **Vue 3 / SvelteKit / SolidJS**: ecosystem maturity / 팀 채용 풀 부족 — rejected.

### Build tool
- **Webpack 5**: dev HMR 1초+ 느림. rejected.
- **Parcel**: zero-config 매력적이나 plugin ecosystem 약함. rejected.

### Routing
- **TanStack Router**: type-safe 매력적이나 v1 ecosystem 신생. v2~v3 안정 후 revisit.

### Data fetching
- **SWR**: 가벼우나 mutation queue / optimistic update 약함 (backend control plane 시나리오 부적합). rejected.
- **Apollo Client / urql**: GraphQL 전제 — FCC backend 가 REST + WS — overkill.

### Form
- **Formik**: React Hook Form 대비 perf 낮음 + ecosystem 정체. rejected.

### Test
- **Jest**: Vite 환경에서 transform overhead — vitest 우선.
- **Cypress**: Playwright 대비 multi-browser 약함 + worker model 무거움. rejected.

### CSS
- **Tailwind CSS**: 산업 표준이나 design token SSOT 와 충돌 — token 기반 design system 직접 구축이 backend `ThemePalette` 와 일관.
- **styled-components / Emotion**: runtime CSS-in-JS — Core Web Vitals (FCP/LCP) 페널티. rejected.

## Revisit Conditions

1. **React 19 안정 release** → concurrent mode / use() hook / Actions API 활용 redesign
2. **TanStack Router v2+ 안정** → React Router 에서 마이그레이션 검토
3. **Tailwind v4 production-grade** → design token 호환 mode 도입 시 재검토
4. **bundle size > 500KB gzipped** → framework downsize (Preact / SolidJS) 검토
5. **운영자 mobile-first 비율 50% 초과** → React Native / Capacitor hybrid 검토

## References

- [React 18 docs](https://react.dev/)
- [Vite docs](https://vitejs.dev/)
- [TanStack Query](https://tanstack.com/query/latest)
- [Zod + react-hook-form](https://react-hook-form.com/get-started#SchemaValidation)
- backend `application/session/api_contracts.py` — F-2-D3 OpenAPI 3.1 SSOT
- `ui/theme_palette.py::ThemePalette` — backend WCAG AA SSOT (GUI-PG-2, 2026-05-23)
