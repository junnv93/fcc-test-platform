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
