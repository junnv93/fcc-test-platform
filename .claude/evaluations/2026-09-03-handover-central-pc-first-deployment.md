# 인계 — 중앙 PC 최초 구축 완료, 챔버 연결 대기 (2026-09-03)

## 지금 어디까지 왔나

```
중앙 PC (10.206.34.233, WSL)
  postgres          Up (healthy)   마이그레이션 30건, ok:true, exit 0
  keycloak          Up (healthy)   :8081  realm fcc-dev
  central-migrate   Exited (0)
  platform-api      Up (healthy)   :8002  /platform/health → {"status":"ok"}
  web · headless-api  ⛔ 안 떠 있다 — headless 이미지가 없다

  fcc-test-platform  0.1.8 · fcc-test-kernel 0.3.0 · fcc-test-contracts 0.1.12
```

⚠️ **최초 구축이었다.** 운영자가 *「중앙 PC 는 한 번도 제대로 동작한 적 없고 DB 도
쓸모없다」* 고 확인해서 갱신이 아니라 새로 세웠다. `central.env` 새 생성,
`pgdata`·`headless-data` 삭제 후 재생성, `central-kcdata` 는 **애초에 없어서 처음 생김**.

## 다음 단계 — 순서가 강제된다

```
1. 챔버 PC   .\fcc-session-node.exe --check-config   traceback 전문
2.           그 모듈을 Nuitka --include-package 에 넣고 재빌드
3.           fcc-unlicensed-headless-api:latest 빌드
4.           docker save → 중앙 PC → docker load
5. 중앙 PC   up -d headless-api web            ← 여기서 8080 이 열린다
6. 챔버 PC   노드 기동 → 첫 heartbeat
```

⚠️ **5 가 6 의 선행 조건이다.** `web`(nginx, :8080)이 유일한 입구이고
`/platform/` 을 `platform-api:8002` 로 프록시한다. 챔버 프로비저너가 8080 이
아닌 값을 거절하므로 우회도 안 된다. (이 세션이 *「web 은 나중에」* 라고 적었다가
형제 세션이 정정했다 — `depends_on` 을 보고 「무엇이 기동하나」를 확인한 뒤
「무엇이 도달 가능한가」를 답했다.)

## ⛔ 절대 하지 말 것

```
provision 재실행 (chamber-01)   secret 이 회전돼 운영자가 이미 넣은 값이 죽는다
down -v                          운영 DB·Keycloak 볼륨을 지운다
docker system prune -a           같은 Docker 에 EMS 가 돈다 (compose-* 컨테이너)
--build 생략                     같은 :latest 태그의 캐시가 조용히 재사용된다
```

## 실측으로 확정된 값 — 추측 금지

```
FCC_CENTRAL_BASE_URL        http://10.206.34.233:8080
FCC_CENTRAL_OIDC_TOKEN_URL  http://10.206.34.233:8081/realms/fcc-dev/protocol/openid-connect/token
FCC_SESSION_OIDC_ISSUER     http://10.206.34.233:8081/realms/fcc-dev      ← well-known 으로 실측
FCC_SESSION_OIDC_AUDIENCE   fcc-chamber-chamber-01
FCC_CENTRAL_CLIENT_ID       fcc-chamber-chamber-01
FCC_CENTRAL_CHAMBER_ID      chamber-01
FCC_CENTRAL_PROVIDER_ID     fcc-unlicensed-conducted
secret 경로 (챔버)          C:\LabAutomation\node-config\session-node.secrets.env
```

⚠️ `JWKS_URI` 만 well-known 필드를 직접 안 읽고 관례로 적었다 — **아직 대조 안 됐다.**

## 세션 분담

```
중앙 PC            이 세션
챔버 설정·배포      fcc-mobile-test-automation-f9  (= -ed)
Nuitka exe 빌드     fcc-mobile-test-automation-ab  (= -3b)
```

## 이 세션이 병합한 것 — 15건

```
platform #37–51    계약 #17 #18 #19
태그  kernel-v0.2.0 · kernel-v0.3.0 · v0.1.12
```

핵심 둘:
- **커널 이관 완료** — 공유 폐포 24 → **0**, 이름 소유권 위반 **0**,
  platform 이 주장하는 최상위 이름이 `fcc_test_platform` **하나**
- **선언된 부채 17 → 0** — 추출 이래 처음 완전 green (2,915 passed)

## 이 라운드가 반복해서 만난 형태

> **어느 기계인지는 이름이 아니라 도달성으로 판정한다.**

세션 셋이 오늘 **네 번** 걸렸다 — 개발 PC 와 중앙 PC 가 같은 이름의 컨테이너를
돌려 `docker ps`·`docker inspect` 출력이 완전히 같은 모양이다. 매번 사람이
*「여기는 개발 PC야」* 라고 알려줘서 정정됐다. 그래서 드리프트 게이트가 이제
보고 맨 위에 **측정 기계**를 적는다(PR #51).

> **비-공허성 팔을 쓸 때 물어라: 이 검사가 성공하면 이 팔이 red 가 되는가?**

「0」이 두 가지를 뜻한 자리가 하루에 넷이었고 넷 다 비-공허성 팔이 만들었다
(`.claude/rules/check-axis-blindness.md` §비-공허성 팔이 성공을 금지하는 경우).

## 마지막으로 — 이 세션의 진단 하나가 형제 세션에게 정정됐다

`-ValidateOnly` traceback 의 원인을 셋으로 **나열**했는데, 형제가 그것을
**가르는 축**을 댔다: `session_node_entry.py::main()` 이 설정·OS·값 오류를 잡아
exit 2 + 한 줄로 내고 `ImportError` 는 catch 0건이다. 즉 **traceback 이 보였다는
사실 자체가 「설정 문제 아님」을 증명**한다 — frozen exe 모듈 누락이 유력하다.

내 목록은 *「무엇일 수 있나」* 였고 그쪽은 *「관측이 무엇을 배제하나」* 였다.
후자가 판정이다.

---

# ⚠️ 정정 — 2026-09-04 (`fcc-delivery-final-6c`, 개발 PC)

이 인계문을 이어받은 세션이 **위 본문의 두 곳을 정정**한다. 원문을 지우지 않고 아래에
둔다 — 무엇이 왜 틀렸는지가 다음 사람에게 본문만큼 중요하기 때문이다.

## 1. `JWKS_URI` — 「미대조」가 아니라 **잴 값이 아니었다**

본문 마지막 줄이 *「`JWKS_URI` 만 well-known 필드를 직접 안 읽고 관례로 적었다 —
아직 대조 안 됐다」* 고 적었고, 그 처방으로 다음 한 줄이 유통됐다:

```bash
# ⛔ 이 한 줄을 쓰지 마라
curl -s http://localhost:8081/realms/fcc-dev/.well-known/openid-configuration \
  | python3 -c "...['jwks_uri']"
```

⚠️ **이 명령은 중앙 PC 에서 돌려도 챔버에 넣으면 안 되는 값을 낸다.**

`infra/docker-compose.central.yml`(**fcc-test-platform 레포**) 이 SSOT 이고, 구조는
의도된 split-horizon 이다:

```
:115      KC_HOSTNAME                       http://${PUBLIC_HOST}:${KEYCLOAK_PORT}   ← 고정
:116      KC_HOSTNAME_BACKCHANNEL_DYNAMIC   true                                    ← 동적
:189,:393 FCC_*_OIDC_ISSUER    http://${PUBLIC_HOST}:${KEYCLOAK_PORT}/realms/fcc-dev
:190,:394 FCC_*_OIDC_JWKS_URI  http://keycloak:8080/realms/fcc-dev/.../certs        ← 내부 이름
```

`:108-114` 주석이 2026-06-20 에 이유를 적어 뒀다 — 토큰 `iss` 는 단일값이어야 하므로
issuer 를 `PUBLIC_HOST` 로 고정하고, backchannel 만 dynamic 으로 열어 내부
(`keycloak:8080`)·127 접근을 호환한다.

**그래서 well-known 의 `jwks_uri` 는 「측정되는 값」이 아니라 「물어본 주소의 메아리」다.**
개발 PC 실측(2026-09-04): 같은 문서에서 `issuer` 는 고정값을, `jwks_uri` 는 질의 주소를
답한다 — `localhost` 로 물으면 `localhost` 가, LAN IP 로 물으면 LAN IP 가 나온다.

⚠️ **챔버 노드는 이 키를 필수로 요구한다.** `-3b` 실측:
`src/session_node_entry.py:103` `_OPERATIONAL_REQUIRED_KEYS` 에
`FCC_SESSION_OIDC_JWKS_URI` 가 있고 `--check-config` 가 부재를 거부한다. 즉 이 값은
실제로 챔버 설정 파일에 들어간다.

```
챔버 노드용  http://10.206.34.233:8081/realms/fcc-dev/protocol/openid-connect/certs
⛔ 금지      http://keycloak:8080/...    compose 내부 이름 — 챔버에서 해소 불가
⛔ 금지      http://localhost:8081/...   노드가 자기 자신에게 JWKS 를 요청한다
```

⚠️ 참고: `docs/platform/identity_policy.v1.json:9` 이 *「jwks_uri 는 HTTPS 여야 하고
localhost 를 쓰면 안 된다」* 를 명문화한다 — 위 한 줄이 만들어내는 바로 그 값이다.

**본문의 `FCC_SESSION_OIDC_ISSUER` 는 그대로 맞다.** `KC_HOSTNAME` 이 `PUBLIC_HOST`
파생이므로 중앙에서 `PUBLIC_HOST=10.206.34.233` 이면 본문 값이 유도된다. 확인할 것이
있다면 well-known 이 아니라 **`grep -E '^PUBLIC_HOST=' central.env` 한 줄**이다.

## 2. 이미지 신선도 — 4단계(`up -d headless-api web`) 전에 물어야 한다

본문 머리말이 중앙 상태를 *`fcc-test-platform 0.1.8 · fcc-test-kernel 0.3.0 ·
fcc-test-contracts 0.1.12`* 로 적었다. **개발 PC 의 같은 이름 컨테이너를 재 보니 다르다**
(2026-09-04, `fcc-central-platform-api:latest`, created `2026-09-03T02:02:24Z`):

```
컨테이너 안:  fcc-test-kernel 0.1.0 · fcc-test-contracts 0.1.11 · fcc-test-platform 0.1.8
docker exec … python -c "import fcc_test_kernel.domain.models.enums"
  → ModuleNotFoundError
설치된 fcc_test_platform 중 fcc_test_kernel.domain 을 부르는 파일:  0건
소스 트리의 fcc_test_platform 중 fcc_test_kernel 을 부르는 곳:   137건
```

즉 그 이미지는 **커널 이관 전 코드**다(02:02 는 2·3단계 착지 전). 낡은 코드와 낡은
커널이 서로 정합해서 컨테이너는 **healthy** 다 — 아무것도 안 걸린다.

⚠️ **`fcc-test-platform 0.1.8` 은 이관 전/후를 구별하지 못한다.** 낡은 이미지도 0.1.8,
지금 트리도 0.1.8 이다. 버전이 안 올랐으므로 *「0.1.8 이 설치됐다」* 는 커널 이관 코드가
들어왔는지에 대해 **아무 말도 하지 않는다.**

**중앙 PC 에서 확인할 세 줄** (이 세션도 `-3b` 도 `10.206.34.233` 에 안 닿는다 —
운영자 경유여야 한다):

```bash
docker exec fcc-central-platform-api pip list | grep -i fcc-test
docker exec fcc-central-platform-api python -c "import fcc_test_kernel.domain.models.enums as m; print(m.__file__)"
docker inspect fcc-central-platform-api --format '{{.Created}}'
```

중앙 이미지도 02:02 산이면 중앙 역시 이관 전 코드다. 재빌드 자체는 안전하다 —
`requirements-central.txt:90`(**fcc-test-platform 레포**)이 커널을 `kernel-v0.3.0` 으로
핀하고 `infra/central/Dockerfile.api:74-79`(**같은 레포**)의 git+ 분리가 그것을 레인 쪽으로
떨어뜨린다. 커널 0.3.0 이 선언하는 의존성은 `fcc-test-contracts` 하나뿐(서드파티 0건)이라
`--no-deps` 도 아무것도 안 빠뜨린다.

⚠️ **다만 `--build` 를 빼면** 본문 「절대 하지 말 것」의 경고대로 `:latest` 캐시가 조용히
재사용돼 낡은 쌍이 그대로 남는다.

⚠️ **인용에는 레포를 붙였다** — `requirements-central.txt` 와 `infra/central/` 은 FCC
모노레포에도 **같은 이름으로** 있고 두 벌이 갈렸다(`-3b` 실측: FCC 사본에는 커널 줄이
없고 Dockerfile 도 필터 없는 2-way split 이다). 중앙을 지배하는 것은 platform 레포
사본이다. 오늘 라운드가 반복한 *「이름이 아니라 도달성으로 판정한다」* 의 파일 판이다.

## 3. 새로 세운 red 하나 — 레지스트리 아티팩트 교차 레인 검사

`tests/test_provider_registry_artifacts_resolve_cross_lane.py` (신규).

platform 작업트리의 `config/headless_provider_registry.json` 에 `kc-unlicensed-headless`
가 **미커밋으로** 추가돼 있는데, 가리키는 아티팩트가 어느 트리에도 없다. 계약 레인 체커를
손으로 물려야만 보였다(exit 2 · `providers: []` — 새 항목 하나가 아니라 **레지스트리
전체가 로드 실패**). 두 레인의 pytest 는 전부 초록이었다. 그 침묵을 red 로 만들었다.

⚠️ **미커밋 변경 자체는 손대지 않았다** — KC 레인 세션(현재 offline)의 것으로 보인다.
그리고 **눈에 보이는 수리가 함정이다**: 아티팩트를 계약 트리나 레지스트리 옆에 만들어
넣으면 `_resolve_artifact_path` 폴백으로 코드 변경 0에 초록이 되는데, 그것이 운영자가
2026-08-31 에 기각한 안 「다」다(`fcc-test-contracts/docs/OPEN-QUESTIONS.md` §1,
판정은 안 「나」 — 발행처가 검사하고 중앙은 결과만 수신).

⚠️ **이 배송 트리에는 pytest 도 `fcc_test_contracts` 도 없다.** 검증은 스크래치패드에
세운 venv 셋으로 했고(트리/휠/부재 × 깨짐/깨끗 = 5조합 전부 의도대로), **전체 스위트는
돌리지 못했다** — 기준선을 건드리지 않았는지는 확인되지 않았다.

---

## ✅ 정정 §2 의 미확인이 닫혔다 (2026-09-04, 운영자 실측 · `fcc-delivery-final-29` 경유)

정정 §2 가 *「중앙 이미지도 02:02 산이면 중앙 역시 이관 전 코드다」* 를 미확인으로 남겼다.
**답: 중앙은 이관 후 코드다.** 개발 PC 와 다르다.

```
                    개발 PC                     중앙 PC (10.206.34.233)
Created             2026-09-03T02:02:24Z        2026-09-03T07:17:52Z
fcc-test-kernel     0.1.0                       0.3.0
import fcc_test_kernel.domain.models.enums
                    ModuleNotFoundError         POST-MIGRATION OK
fcc-test-platform   0.1.8                       0.1.8      ← 같다
```

⚠️ **`pip list` 만 봤으면 오판했다.** 두 기계 모두 `fcc-test-platform 0.1.8` 이다 —
정정 §2 가 *「0.1.8 은 이관 전/후를 구별하지 못한다」* 고 적은 그대로이고, 갈라 준 것은
`import fcc_test_kernel.domain.models.enums` 한 줄이다. **같은 이름, 다른 개체**였다.

### 그리고 `:8080` 이 열렸다

```
fcc-central-headless-api  Up (healthy)   ← 이관한 이미지 (revision 9f85c7a5…, 라벨 대조 통과)
fcc-central-web           Up
curl -i :8080/health → 200, Server: nginx/1.27.5
platform-api 는 건드리지 않았다 (uptime 유지)
```

### ⚠️ 남은 차단 요인은 인계문에 없던 것이다 — D 는 「행 하나 INSERT」가 아니다

`POST /platform/chambers` 는 `platform:admin` 을 요구하는데 그것은 realm 의 `admins`
그룹(사용자 `admin`)만 갖는다. 서비스 계정 client 중 보유자가 없고
`fcc-platform-frontend` 는 `directAccessGrantsEnabled=false` 라 비밀번호 그랜트도 막힌다.
그래서 브라우저 로그인이 필요한데 **평문 HTTP 에서는 원리적으로 불가능하다** — PKCE 의
`crypto.subtle` 이 보안 컨텍스트에서만 제공되고 SPA 가 그 조합을 부팅에서 거부한다.

정공은 `FCC_PLATFORM_AUTH_MODE=local_jwt` + `WEB_AUTH_MODE=local` 이고, ⚠️ **두 값이
아니라 12개**다 — `FCC_HEADLESS_LOCAL_JWT_*` 를 platform 과 동일 값으로 맞추지 않으면
로그인은 되는데 headless 가 전부 401 이다. `scripts/check_auth_mode_pairing.py` 가
재기동 전에 판정한다.

---

# ✅ 완료 — 두 축이 동시에 초록 (2026-09-04 06:15 UTC, 운영자 실측)

인계문의 완료 조건이 충족됐다. **오늘 처음으로 브라우저 화면과 노드 heartbeat 가
동시에 성립한다.**

```
노드 축     fcc-central-platform-api-node   POST /platform/chambers/heartbeat → 200 OK ×4
            fcc-central-platform-api        heartbeat 0건        ⭐ 경로 분기 성공
            chamber_heartbeat_events        30초 주기, 재기동 후 새 행
브라우저 축  http://10.206.34.233:8080       로그인 성공 · 챔버 목록에 chamber-01 표시
컨테이너     web · platform-api · platform-api-node · headless-api   전부 (healthy)
```

⚠️ **두 로그의 대비가 증명이다.** heartbeat 가 노드 인스턴스에만 찍히고 브라우저
인스턴스에는 **한 줄도 없다** — `location = /platform/chambers/heartbeat` 가 접두사
`/platform/` 보다 먼저 매치됐다는 **관측**이지 추론이 아니다. 그리고 브라우저에서 챔버
목록이 보이는 것은 두 인스턴스가 **한 DB 상태를 공유**한다는 확인이기도 하다.

## ⚠️ 이것이 「정상」이다 — `oidc_jwt` 로 「고치려」 들지 마라

```
central.env   FCC_PLATFORM_AUTH_MODE=local_jwt · FCC_HEADLESS_AUTH_MODE=local_jwt
              WEB_AUTH_MODE=local · ALLOW_INSECURE_TRANSPORT=true
              + LOCAL_JWT SECRET/ISSUER/AUDIENCE/TTL   (platform·headless 동일값)
              + BOOTSTRAP_ADMIN  — platform-api 에만. 노드 인스턴스는 빈 값이어야 한다
```

⚠️ **노드 인스턴스에 `FCC_PLATFORM_BOOTSTRAP_ADMIN_*` 를 주지 마라.** 그 부트스트랩은
합성에서 **무조건** 호출되고 인증 모드에 걸리지 않는다(`api_composition.py:798`). 「로컬
사용자가 없을 때만」이라 멱등이지만, 빈 users 테이블에서 **두 인스턴스가 동시에 뜨면 둘 다
「없다」를 보고 둘 다 넣는다.** 환경변수를 안 주면 즉시 반환한다(`:934`) — 경합이
**구조적으로** 사라진다.

## ⚠️ 평문 HTTP 에서 두 축은 원래 **동시에 성립하지 않는다**

```
oidc_jwt    노드 heartbeat 200 ✅   SPA 부팅 거부 ⛔
local_jwt   SPA 로그인 동작 ✅      노드 heartbeat 401 ⛔
```

PKCE 의 `crypto.subtle` 이 보안 컨텍스트 전용이라 평문 HTTP + OIDC 로그인은 원리적으로
불가능하고, `local_jwt` 로 우회하면 platform-api 가 자기 HS 키로만 검증해 노드의 Keycloak
RS256 토큰이 401 이다.

⚠️ **브라우저 정책 우회(`OverrideSecurityRestrictionsOnInsecureOrigin`)로는 못 푼다** —
`crypto.subtle` 은 살아나지만 `runtime.ts` 의 D-6 규칙이 `oidc + insecureTransportAllowed`
조합 자체를 **부팅에서 거부**한다. 다음 사람이 이것을 먼저 시도하고 `crypto.subtle` 이
사는 것을 보고 「됐다」로 읽은 뒤 SPA 부팅 거부에서 막힌다.

그래서 배포 계층에서 끝냈다 — `platform-api` 를 인증 모드별로 **둘로 나누고** nginx 가
노드 경로를 선점한다(PR #57). **앱 코드 변경 0.**

## 🔴 되돌리는 것이 정상 형상이다 — 이 이중 인스턴스는 **임시**다

운영자 판단: *「당분간 HTTP 로 운영해야 한다. 실제로 운영하고 검증받고 인정을 받아야
사내에서 인증서 발급이 가능하다.」* 즉 **HTTPS 는 결과이지 전제가 아니다.**

⚠️ 그러므로 인증서가 발급되면 **되돌린다**:

```
지운다   platform-api-node 서비스 · nginx 의 노드 경로 블록 ·
         EXPECTED_SERVICES/census 등재
남긴다   단일 platform-api (oidc_jwt)
```

**이 문장이 없으면 다음 사람이 이중 인스턴스를 영구 구조로 읽고 늘린다.** 그것은 인증
모드가 둘인 배포를 영구화하는 것이고, 두 인스턴스가 갈라지는 날 그것을 말해 주는 축이 없다.

## 관측 불가로 남은 것 — 어느 쪽 증거로도 쓰지 않는다

**챔버 등록 폼(빈 상태 화면)은 지금 검증할 수 없다.** web 을 재빌드했고 브라우저도
정상이지만, 챔버 목록이 더 이상 비어 있지 않다 — `ChamberAdminPanel` 의 부트스트랩 폼은
**목록이 비었을 때만** 뜨는 갈래다. PR #56 이 고친 빈 상태 문구는 지금 **도달할 수 없는
화면**이다.

⚠️ 그러므로 답은 「보인다」도 「안 보인다」도 아니라 **「확인 못 했다」**다. 검증하려면 빈
DB 가 필요하고, 중앙에서 행을 지워 재현하지 **않는다** — 그 행은 지금
`chamber_heartbeat_events` 의 FK 대상이다.
