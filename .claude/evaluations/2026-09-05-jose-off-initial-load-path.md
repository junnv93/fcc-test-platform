# `jose` 를 초기 로드 경로에서 걷는다 — 그리고 「뺐다」와 「싸게 뺐다」는 다른 축이다 (2026-09-05)

## Why

초기 로드 경로 예산의 여유가 **17,797 B** 뿐이었다. 그 값은 일부러 tracing
청크(36,415 B)보다 작게 잡힌 것이라(`headroomRationale`), 웨이브 하나면 다시
넘는다.

엔트리 그래프 8청크 중 `index` 가 83,298 B 로 가장 크다. 소스맵으로 열어 보면
그 안에 **`jose` 가 통째로** 들어 있다 — 원격 JWKS 조회(`jwks/remote`)와 JWS
서명 검증 그래프까지.

### 왜 부팅 경로에 있었나

`session.ts::restoreSession` 이 `verifyIdToken` 을 정적 import 하고, 그것이
`main.tsx` 부팅에서 실행된다. `verifyIdToken` 은 `oidc-pkce.ts` 에 있고 거기서
`jose` 를 **값으로** 정적 import 했다. 그래서 라이브러리 전체가 엔트리 그래프에
정적으로 도달 가능했다.

그런데 그 코드가 **실제로 실행되는 시점은 부팅이 아니다.** id_token 검증은
(1) 로그인 콜백(`completeLogin`), (2) 침묵 갱신(`session.ts` refresh 경로)
두 곳뿐이고 **둘 다 이미 async** 다.

> 도달 가능성만 정적이고 실행은 지연돼 있었다 — 초기 경로 예산이 재는 것이
> 정확히 그 차이다: *"totalGzipBytes is blind to **when** a chunk is fetched."*

## ⚠️ 첫 처방이 상태를 악화시켰다 — 트리셰이킹 함정

`oidc-pkce.ts` 에서 `import('jose')` 를 직접 부르는 것이 자연스러운 처방이고,
초기 경로에서는 **실제로 빠졌다**(엔트리 내 jose 소스 0). 그런데:

| | 정적 import | `import('jose')` 직접 |
|---|---|---|
| 비동기 청크의 jose 모듈 수 | (엔트리에 23.19 kB raw 로 셰이킹됨) | **78개** |
| 그 청크 gzip | — | **16,960 B** |
| 상자 총계 | 402,509 B | **412,569 B (+10,060)** |

동적 import 는 **네임스페이스 전체**를 요구하므로 Rollup 이 무엇을 버려도 되는지
알 수 없다. 초기 경로 지표만 봤다면 이 처방은 «성공»으로 채점됐을 것이다.

### 정공 — 동적 경계를 자기 모듈에 둔다

`src/auth/jose-verify.ts` 가 쓰는 둘만 정적으로 재수출하고, `oidc-pkce.ts` 는
**그 모듈을** 동적으로 부른다. 경계 안쪽은 평범한 정적 그래프라 트리셰이킹이
그대로 돈다.

| | 결과 |
|---|---|
| 비동기 청크 | `jose-verify-*.js` — 모듈 **29개**, **7,356 B** gzip |
| 상자 총계 | 402,882 B (**+373**) |
| 엔트리 내 jose 모듈 | **0** |

> **「초기 경로에서 뺐다」와 「싸게 뺐다」는 다른 축이고, 총계 예산은 후자만 본다.**
> 두 예산이 함께 있어야 이 처방의 두 판본을 구별할 수 있다.

## ⚠️ 두 번째 오답 — 봉인을 느슨하게 할 뻔했다

`test_apps_web_auth_scaffold.py::test_id_token_claims_surface_in_complete_login_result`
가 빨개졌다. 원인은 명제가 아니라 **철자**였다: 내가 타입을 `Jose.JWTPayload` 로
네임스페이스 한정하자, `idTokenClaims: JWTPayload | null` **문자열**을 찾는 정규식이
못 찾은 것이다. 표면은 그대로였다.

정규식을 넓히는 것이 손쉬운 답이지만 그것은 봉인을 약화시킨다. 실제 원인은
**내가 필요 없는 네임스페이스 import 를 들여온 것**이었다 — resolver 타입은 이미
있는 `JoseVerify` 네임스페이스에서 뽑으면 되고, 그러면 `JWTPayload` 는 원래 철자
그대로 남는다. 봉인 무변경으로 초록.

## Verification

| | 전 | 후 |
|---|---|---|
| 총계 gzip | 402,509 | **402,882** (상한 483,011) |
| **초기 경로 gzip** | 177,967 | **171,007** (−6,960) |
| 초기 경로 여유 | 17,797 | **17,101** (기준선을 함께 내렸으므로) |
| `index` 청크 | 83,298 | **76,338** |
| 엔트리 청크 수 | 8 | 8 (구성 동일) |

* `lane_check` **EXIT=0 · 3054 passed**
* `vitest run` **128 files / 1498 passed**, `tests/auth` 137 passed
* `check-bundle-budget.mjs` 두 예산 모두 OK
* `tsc --noEmit` · `eslint` · `prettier --check .` 초록
* e2e `route-resilience.spec.ts` 통과(동적 청크 실패 경로를 보는 스펙)

### 기준선은 **내렸다** — 그리고 총계는 건드리지 않았다

`initialLoadPathJs.measuredGzipBytes` 177,967 → **171,007**,
`maxGzipBytes` = `ceil(171007 × 1.1)` = **188,108**. 문서가 적은 래칫 방향
그대로다: *"lower it when a wave measurably shrinks the initial path."*

⚠️ 총계 기준선(402,509)은 **의도적으로 그대로 둔다.** 관측값 402,882 는 상한
483,011 아래이고, 기준선을 올리면 상한이 함께 올라간다 — 래칫의 반대 방향이다.

## 후속

* 남은 초기 경로 후보는 `validation`(zod) **13,774 B** 이고, 이것을 끌어오는
  모듈은 `src/config/runtime.ts` **하나뿐**이다. 다만 그것은 부팅 시 런타임 설정
  검증이라 **진짜로 부팅 필수**다 — 코드 분할이 아니라 설계 변경(검증기 교체)이
  되므로 여기서 하지 않는다.
* `@opentelemetry` 는 초기 경로에 있지만 **의도된 것**이다(`vite.config.ts` 가
  `@opentelemetry/api` 를 "always-reachable" 로 명시). 예산 문서가 말하는 두
  SDK(tracing·sentry-runtime)와 **다른 패키지**이므로 오진하지 말 것.
