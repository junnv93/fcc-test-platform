# ADR-0003: OpenAPI → TypeScript client generator

**Status**: Accepted — 구현됨

> ⚠️ **상태 갱신 2026-09-05** — 원문은 `Proposed` 였으나 **이 결정은 이미 구현돼 있다.**
> `openapi-typescript` + `openapi-fetch` 실재. CI 가 `codegen` → `codegen:check` 드리프트 게이트로 강제(`.github/workflows/frontend.yml`).
> 재구현하지 말 것. 현황은 `docs/architecture/2026-09-04-플랫폼-리팩토링-설계서.md` 참조.

**Date**: 2026-05-23
**Deciders**: shared web/platform maintainers + user
**Depends on**: ADR-0002 (stack selection — TanStack Query + Zod)

## Context

backend `docs/api/session-api.openapi.json` (OpenAPI 3.1, F-2-D3 SSOT, byte-identity invariant) 가 frontend TypeScript 측 type + API client 의 single source. 자동 codegen 선택을 결정한다.

이전 contract-only sprint 의 임시방편 결함:
- frontend evidence schema 가 `job_list_view.columns = ['job_id', 'provider_id', ...]` literal 로 column 명세 — backend DTO 와 silent drift 위험
- 본 ADR 이 OpenAPI artifact → TS type → frontend evidence schema → backend DTO chain 의 **single SSOT direction** 을 정한다

### Constraints

- OpenAPI 3.1 (F-2-D3) 채택 (3.0 미사용)
- AsyncAPI 3.0 (WebSocket event catalog) 도 별도 codegen 필요 (out of scope of this ADR — 별도 ADR 가능)
- TanStack Query 와 호환 (`queryKey` / `queryFn` 자동 생성 이상적)
- Zod schema 자동 생성 호환 (runtime validation 통합)
- monorepo Vite build 와 호환 (build script `npm run codegen`)
- frontend artifact 가 backend OpenAPI 변경 시 compile error 발생 → SSOT 강제

## Decision

**`openapi-typescript` (v7+) 채택** for TS type 생성 + **`openapi-fetch` (또는 manual TanStack Query 통합)** for runtime client + **`openapi-zod-client` 또는 직접 mapping** for Zod schema

### 구현 패턴

```bash
# package.json scripts
"codegen": "openapi-typescript ../../docs/api/session-api.openapi.json -o src/api/session-api.types.ts && openapi-typescript ../../docs/api/headless-api.openapi.json -o src/api/headless-api.types.ts"
```

```ts
// src/api/session-client.ts
import createClient from 'openapi-fetch';
import type { paths } from './session-api.types';

export const sessionClient = createClient<paths>({
  baseUrl: getRuntimeConfig().apiBaseUrl,
});

// TanStack Query 통합
export function useSessionInfo() {
  return useQuery({
    queryKey: ['session', 'info'],
    queryFn: async () => {
      const { data, error } = await sessionClient.GET('/session/info');
      if (error) throw error;
      return data;
    },
  });
}
```

### CI gate

- `npm run codegen` 후 git diff 가 있으면 CI fail (backend OpenAPI 변경 시 frontend codegen 자동 반영 강제)
- `tsc --noEmit` 으로 type compile (backend DTO 변경 시 frontend 측 compile error)

## Consequences

### Positive
- backend OpenAPI 변경 → frontend codegen → compile error → SSOT 강제 자동
- TanStack Query 와 type-safe 통합
- bundle size 작음 (openapi-fetch ~ 1KB gzipped, openapi-typescript dev-only)
- TypeScript 5 strict 호환

### Negative
- Zod schema 자동 생성은 별도 tool 필요 (`openapi-zod-client` v1 ecosystem 신생 — manual fallback 가능성)
- WebSocket event catalog (AsyncAPI) 는 별도 codegen — `asyncapi-codegen` 또는 manual
- AsyncAPI 3.0 codegen ecosystem 약함 — manual TS type 작성 가능성

## Alternatives Considered

### `openapi-generator-cli` (Java-based)
- **rejected because**: Java 의존성 + monorepo build 시 Docker 또는 JVM 설치 부담. dev experience 무거움. bundle 큰 boilerplate.

### `orval`
- **considered**: TanStack Query + Zod + MSW 통합 강력. v6+ feature 풍부.
- **deferred**: openapi-typescript 가 simpler + Vite 통합 깔끔. orval 은 SaaS app 에서 큰 codebase 시 재검토 — 단기 시작은 openapi-typescript.

### `swagger-codegen` / `nswag`
- **rejected because**: legacy tool, OpenAPI 3.1 지원 약함

### Manual hand-written TypeScript types
- **rejected because**: SSOT drift 의 직접적 원인 (이전 sprint 결함)

## Revisit Conditions

1. **AsyncAPI 3.0 codegen ecosystem 성숙** → WebSocket event 도 자동화
2. **orval v7+ stable + monorepo Vite 통합 검증** → switch 검토
3. **GraphQL 도입 결정** → Apollo Client + GraphQL Code Generator
4. **OpenAPI 4.0 release** → tool 호환성 재검토

## References

- [openapi-typescript](https://openapi-ts.dev/)
- [openapi-fetch](https://openapi-ts.dev/openapi-fetch/)
- [openapi-zod-client](https://github.com/astahmer/openapi-zod-client)
- backend `application/session/api_contracts.py` — F-2-D3 OpenAPI 3.1 SSOT
- `scripts/export_session_api_schemas.py` — `--verify` drift gate (artifact byte-identity invariant)
