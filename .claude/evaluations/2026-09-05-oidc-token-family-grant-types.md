# `family: 'token'` 은 엔드포인트이지 grant 가 아니다 (2026-09-05)

## Why

`oidc-conformance` 잡이 `auth-flow.spec.ts` 에서 빨갛다. `unexpectedRequests` 에
이것이 들어온다:

```
POST …/realms/fcc-dev/protocol/openid-connect/token missing authorization-code PKCE body
```

진단명이 **PKCE 결함**을 가리킨다. 그런데 아니었다.

## 판정 — 전수 증명

판정기(`real-auth-fixture.ts`)는 `family: 'token'` 의 **모든** POST 에 대해
`grant_type === 'authorization_code' && has('code_verifier')` 를 요구했다.
그 family 의 정의는 「그 경로로 가는 POST」다 — 즉 **엔드포인트**다.

그런데 SPA 가 그 한 엔드포인트로 보내는 grant 는 **둘**이다(`oidc-pkce.ts`):

| 함수 | grant_type | `code_verifier` |
|---|---|---|
| `exchangeCode()` | `authorization_code` | **있다** (분기 없이 항상) |
| `refreshTokens()` | `refresh_token` | **없다 — RFC 6749 § 6 그대로** |

전수 확인: `postToken` 의 호출자는 위 둘뿐이고, 앱 안에 토큰 엔드포인트로 가는 다른
`fetch` 는 없다(`grep -rn "openid-connect/token" src/` → 0건).

> ∴ **그 메시지를 낼 수 있는 요청은 refresh 교환뿐이다.** PKCE 구현은 정상이고,
> 틀린 것은 **계수**다. 그리고 진단명이 읽는 사람을 인증 구현 쪽으로 잘못 보냈다 —
> 잘못된 이름표는 잘못된 빨강보다 비싸다.

## What — 약해지지 않고 강해진다

`assertTokenGrant()` 가 grant 별로 자기 규격을 본다:

* `authorization_code` — `code_verifier` **필수**. 없으면 그것이 진짜 PKCE 결함이다.
* `refresh_token` — `refresh_token` 필수, 그리고 `code_verifier` 는 **있으면 안 된다**
  (갱신 요청에 검증기가 실려 오면 유출이고, RFC 는 요구하지도 않는다).
* 그 외 — 이 SPA 가 보내지 않는 grant. 이름을 대고 거부한다.

옛 판정은 refresh 에 대해 **아무 규격도 보지 않았다**(그냥 결함으로 셌다). 새 판정은
두 개의 진짜 반례를 새로 잡는다.

`refreshTokenGrantRequests` 를 원장에 더해 두 grant 를 **따로** 센다 — 한 숫자에
섞으면 「PKCE 교환이 일어났다」와 「토큰 요청이 있었다」가 구별되지 않는다.

## 봉인 — 목록을 손으로 유지하지 않는다

`TestTokenGrantClassifierCoversEverySentGrant` — `oidc-pkce.ts` 의 `grant_type`
리터럴을 **파생**해 판정기의 `case` 와 대조한다. 다음에 grant 가 하나 더 늘면
판정기가 조용히 그것을 결함으로 세는 대신 이 검사가 멈춘다.

「일했다는 증거」를 함께 본다(`assertGreaterEqual(len(sent), 2)`) — 정규식이 아무것도
못 잡고 「차집합 없음」으로 초록이 되는 자리를 막는다.

**반증 실측:** 판정기에서 `case 'refresh_token':` 을 지우면 빨갛고 이름을 정확히
지목한다(`['refresh_token'] != []`). 되넣으면 초록.

## Verification

* `tests/test_apps_web_auth_scaffold.py` **134 passed / 1 skipped**
* `lane_check` **EXIT=0**
* `tsc --noEmit` · `eslint` · `prettier --check` 초록
* ⚠️ **실제 Keycloak 실증은 CI 가 한다.** 이 개발 PC 에서는 `docker-compose.idp.yml`
  이 쓰는 **8081** 을 이미 `fcc-central-keycloak` 이 점유하고 있어, 띄우면 바인딩이
  실패하거나 공유 개발 스택을 내려야 한다. `oidc-conformance` 잡은 깨끗한 러너에서
  자기 IdP 를 띄우므로 그쪽이 더 나은 실증이고 위험이 0이다.

## 인증 모드 맥락 (이 결함과 **무관**하지만 기록해 둔다)

중앙 운영 형상은 브라우저 축이 **EMS 컨셉**이다 — 런북이 `FCC_PLATFORM_AUTH_MODE=
local_jwt` + `WEB_AUTH_MODE=local` 을 못박는다. 평문 HTTP 에서 PKCE 가 쓰는
`crypto.subtle` 이 원리적으로 없기 때문이다. 그래서 시험원은 Keycloak 으로 로그인하지
않는다.

그렇다고 Keycloak 이 폐기된 것은 아니다: **챔버 노드**가 머신 토큰(RS256)을 보내므로
`platform-api-node` 두 번째 인스턴스가 `oidc_jwt` 로 그것을 받고, compose 주석이
이 이중 형상을 **HTTPS 이전의 잠정**이라 적으며 *「되돌리는 것이 정상 형상이다」* 라고
선언한다. 즉 이 레인은 죽은 코드가 아니라 **되돌아갈 형상**을 지킨다.

⚠️ 그리고 이 결함 자체는 인증 모드와 무관하다 — refresh 교환을 PKCE 결함으로 세는
것은 어느 모드에서도 틀린 계수다.
