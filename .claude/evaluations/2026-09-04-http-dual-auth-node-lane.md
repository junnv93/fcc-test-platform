# 평문 HTTP 에서 브라우저와 챔버 노드를 동시에 살린다 (2026-09-04)

## Why

운영자 요구가 바뀌었다 — **사내 검증·인정을 받아야 인증서가 발급된다.**
HTTPS 는 결과이지 전제가 아니므로 그때까지 평문 HTTP 로 운영해야 한다.

그런데 `FCC_PLATFORM_AUTH_MODE` 는 프로세스당 하나이고, 평문 HTTP 에서 브라우저와
챔버 노드가 요구하는 값이 서로 **반대**다 (중앙 PC 10.206.34.233 실측):

| 모드 | 브라우저 | 챔버 노드 |
|---|---|---|
| `local_jwt` | SPA 로그인 동작 ✅ | `POST /platform/chambers/heartbeat` 401 ⛔ |
| `oidc_jwt` | SPA 부팅 거부 ⛔ | heartbeat 200 ✅ |

PKCE 가 쓰는 `crypto.subtle` 은 보안 컨텍스트(https/localhost) 전용이라
`http://<IP>:8080` 에서 OIDC 로그인은 **원리적으로 불가능**하다. 한 프로세스로는
둘 중 하나가 반드시 401 이다.

⚠️ 이 축은 `check_auth_mode_pairing.py` 로 판정할 수 없다 — 그 도구는 SPA 정합만
보고, `oidc + 원격 host` 에서 `ALLOW_INSECURE_TRANSPORT` 가 true 든 false 든 FAIL 을
낸다. 「노드를 위해 SPA 를 포기」라는 선택지 자체가 그 도구의 어휘에 없다.

## What

인증 모드별로 `platform-api` 인스턴스를 둘로 나누고 nginx 가 경로로 가른다.
**앱 코드 변경 0, 챔버 PC 설정 변경 0**(노드는 계속 `:8080` 만 본다).

```
브라우저 ─► /platform/*                              ─► platform-api      (local_jwt)
챔버 노드 ─► /platform/chambers/heartbeat             ─► platform-api-node (oidc_jwt)
             /platform/chambers/{id}/reference-bundle
             /platform/chambers/{id}/result-ingestions
             /platform/chambers/{id}/artifact-custody-reports
             /platform/chambers/{id}/settings
```

## How

* `infra/docker-compose.central.yml` — `environment:`/`healthcheck:` 에 YAML 앵커를
  붙이고 `platform-api-node` 가 병합으로 상속한다. **값을 복사하지 않는다** — 두 벌이
  되면 갈라지고, 이 저장소는 web 의 compose 사본에서 이미 그 사고를 겪었다.
  28키 상속, 3키만 상이(`FCC_PLATFORM_AUTH_MODE`, `BOOTSTRAP_ADMIN_EMAIL/PASSWORD`).
* `infra/central/nginx.conf` — `location /platform/` **앞에** 정확일치 1 + 정규식
  4경로. nginx 우선순위상 `=` > `~` > 접두사이므로 나머지 `/platform/*` 는 그대로다.

**부트스트랩 관리자 경합 제거.** `api_composition.py:798` 의
`_bootstrap_local_admin_from_env` 는 합성에서 무조건 호출되고 인증 모드에 걸리지
않는다. 빈 `users` 표에서 두 인스턴스가 동시에 뜨면 둘 다 「없다」를 보고 둘 다
넣는다. 같은 파일 :934 가 `if not email or not password: return` 이므로, 노드
인스턴스에 그 두 값을 빈 문자열로 덮어 경합을 **구조적으로** 없앴다.

## Verification

```
docker compose config   7개 서비스 정상 해소 — 앵커 병합이 실제 Compose 에서 동작
nginx -t                syntax is ok / test is successful
location 우선순위        추가한 정규식이 파일의 유일한 regex — 순서 충돌 없음
                        = (225) > ~ (235) > 접두사 /platform/ (245)
```

노드 경로 판정 근거: heartbeat·reference-bundle 은 중앙 로그 실측, 나머지 셋은
OpenAPI 요약문의 **행위 주체**. ⚠️ `GET …/equipment-config` 는 **제외** — 챔버
레인이 두 독립 축으로 확정했다: ① 노드는 그것을 부르지 않고 `settings` 응답의
필드를 읽는다(중앙 왕복을 하나 더 만들지 않으려는 설계), ② 그 GET 은
`platform:read` 를 요구하는데 챔버 토큰은 `platform:chamber` 하나만 갖는다 → 403.

⚠️ **같은 판정이 다시 필요하면 요약문이 아니라 권한 테이블을 봐라.** 요약문은
「누가 읽는가」에 답하지 못한다.

### 계약 가드가 잡은 것 (lane_check, 2026-09-04)

첫 push 가 막혔다 — **선언에 없는 실패 6건**. `--write-baseline` 은 쓰지 않았다.

* `test_compose_defines_exactly_the_central_services` ·
  `test_the_service_census_is_derived_and_not_a_hand_list` — 서비스 census 가
  손목록/파생 둘 다로 봉인돼 있다. 신규 서비스는 **선언에 명시**해야 통과한다.
  의도적 추가이므로 두 곳에 등재했다(지울 조건도 주석에 적었다).
* `TestComposeHealthcheckWiring` 4건 — 이 가드는 compose **원문을 정규식으로**
  읽어 `healthcheck:\n … test:\n` 을 찾는다. `healthcheck: &anchor` 로 쓰면
  platform-api 의 healthcheck 를 **못 찾고 침묵한다.**
  ⚠️ **내 형식 때문에 가드를 느슨하게 만들지 않았다** — 앵커를 되돌리고 노드
  서비스에 healthcheck 를 명시 복제했으며, 왜 앵커가 아닌지와 「함께 고쳐라」를
  주석에 적었다. 환경값은 그대로 앵커 병합이다(그쪽엔 원문 가드가 없다).

재실행: `tests/test_central_docker_compose.py` + `tests/test_platform_ops_health_probe.py`
**125 passed**.

## 후속

1. ⚠️ **이것은 잠정 형상이다.** 인증서가 발급되면 `platform-api-node` 서비스와
   nginx 의 노드 경로 블록을 지우고 단일 `oidc_jwt` 로 되돌린다. **되돌리는 것이
   정상 형상**이므로 이 블록을 늘리지 마라.
2. nginx 의 경로 목록이 SSOT 다. 노드 API 가 늘었는데 추가하지 않으면 그 요청만
   브라우저 인스턴스로 가서 **401 로 조용히 실패**한다. 특히 `settings` 는 실패가
   무증상이다 — 노드는 정상 기동하고 heartbeat 도 200 인데 계측기 설정을 워크북
   값으로 붙는다(재배선 뒤 분석기가 옛 주소로 연결된다).
3. `check_auth_mode_pairing.py` 가 이 형상을 모른다. 노드 인스턴스의 존재를 축으로
   넣을지 검토 대상.
