# 중앙 PC FCC 플랫폼 검증 가이드 — 시험원용

> 🟡 **참조 문서 — 시험원 화면 워크스루.**
> 챔버 PC 의 기술 검증은 [`chamber-pc-operational-verification-runbook.md`](chamber-pc-operational-verification-runbook.md) 입니다.
> ⚠️ 최종 갱신 2026-08-05. 워크북 업로드·챔버 승인 축은 이 문서에 없습니다.

이 문서는 **중앙 PC**에서 **FCC 플랫폼(웹/API/DB/로그인)이 정상 동작하는지 확인**하는
방법을 시험원(비개발자 운영자) 기준으로 정리한 것이다. 명령은 그대로 복사해서
**WSL Ubuntu 터미널**에 붙여넣으면 된다.

> **이 문서는 "배포"가 아니라 "검증"용이다.** 스택을 처음 설치·구성하거나 realm/시크릿을
> 바꾸는 절차는 [`infra/central/ONPREM_DEPLOYMENT.md`](../../infra/central/ONPREM_DEPLOYMENT.md)
> 소관이다. 여기서는 **이미 배포된 플랫폼이 지금 살아있는지**만 확인한다. 새로 설치하거나
> 값을 바꿔야 하면 배포 문서로 간다.

---

## 1. 이 문서의 목적과 대상

- **무엇을 확인하나**: 중앙 PC의 FCC 플랫폼 컨테이너 5개가 떠 있고, 브라우저에서
  로그인되고, 시험원이 쓰는 화면(프로젝트/시료/진행률/성적서 등)이 살아있고, 챔버 노드가
  중앙에 붙는지.
- **대상**: 시험원(비개발자). Docker/Linux를 깊이 몰라도 따라 할 수 있게 썼다.
- **언제 보나**: 매일 아침 점검, 중앙 PC 재부팅 후, "웹이 안 뜬다/로그인이 안 된다"는
  문의가 왔을 때.

---

## 2. 먼저 알아둘 것

**FCC 플랫폼**은 여러 측정 PC(챔버 노드)에서 나온 시험 정보를 한곳에 모아 브라우저로 보여주는
중앙 시스템이다. **중앙 PC**는 그 중앙 시스템(웹·API·DB·로그인)을 Docker 컨테이너로 상시
가동하는 서버 역할을 한다.

### 구성도 (시험원 눈높이)

```text
   [시험원 브라우저]              [챔버 노드 PC들 = 측정 PC, 네이티브 .exe]
          │  http://<CENTRAL_IP>:8080          │  LAN
          └──────────────┬─────────────────────┘
                         ▼
        ┌──────────────────────────────────────────┐
        │  중앙 PC (WSL + Docker) — 컨테이너 5개      │
        │                                            │
        │   web (:8080)  ← 브라우저·챔버가 보는 외부 문 │
        │     ├─ /headless → headless-api (:8001)    │
        │     └─ /platform → platform-api (:8002)    │
        │   keycloak (:8081)  ← 로그인 담당           │
        │   postgres  ← 중앙 DB (내부 전용)           │
        └──────────────────────────────────────────┘
```

핵심 개념 3가지:

- **단일 진입점 `:8080`** — 시험원 브라우저는 오직 `http://<CENTRAL_IP>:8080` 한 주소만 쓴다.
  그 안의 `web` 컨테이너(nginx)가 `/headless`·`/platform` 요청을 뒤쪽 API로 대신 넘겨준다
  (reverse-proxy). 그래서 시험원은 API 포트(:8001/:8002)를 직접 칠 일이 없다.
- **로그인은 keycloak(:8081)이 담당** — 처음 접속하면 keycloak 로그인 화면이 뜬다.
- **챔버 노드는 컨테이너가 아니다** — 측정 PC는 장비(GPIB/USB)와 Windows `.exe`가 필요해
  네이티브로 남는다. 운영 heartbeat·자가 등록은 중앙 Gateway `:8080`의
  `/platform/...` 경로를 사용하고, 원격 측정 명령은 중앙이 챔버 `:9000`으로 전달한다.

> 이 아래 모든 명령에서 `<CENTRAL_IP>`는 **중앙 PC의 사내망 고정 IP**로 바꿔서 읽는다
> (확인 방법은 4단계 참고).

---

## 3. 1단계 — 스택이 떠 있는지 확인

작업 폴더로 이동한다. (중앙 PC에서 repo가 있는 실제 경로로 바꾼다 — WSL이면 보통
`/mnt/c/fcc-test-platform`. ⚠️ 2026-09-03 부터 중앙 PC 는 `FCC_mobile_test_automation`
을 두지 않는다 — 배포 런북 §문서 경계 참조.)

```bash
cd /path/to/fcc-test-platform    # ⚠️ FCC 저장소가 아니다 (2026-09-03 배치 변경)
```

컨테이너 상태를 본다.

```bash
docker compose -f infra/docker-compose.central.yml ps
```

**명령 의미**

| 명령 조각 | 의미 |
| --- | --- |
| `docker compose -f infra/docker-compose.central.yml` | FCC 중앙 스택 정의 파일을 지정한다. |
| `ps` | 그 스택의 컨테이너 목록과 상태(STATUS)를 보여준다. |

**정상 기대값** — 아래 5개 서비스가 `Up`(또는 `healthy`) 상태여야 한다.

```text
NAME                          STATUS
fcc-central-postgres          Up (healthy)
fcc-central-keycloak          Up (healthy)
fcc-central-headless-api      Up (healthy)
fcc-central-platform-api      Up (healthy)
fcc-central-web               Up
fcc-central-migrate           Exited (0)
```

판단 기준:

- 위 **5개(postgres/keycloak/headless-api/platform-api/web)가 `Up`이면 정상**이다.
- **`fcc-central-migrate`는 `Exited (0)`이 정상**이다. 이 컨테이너는 DB 스키마를 한 번
  적용하고 스스로 끝나는 **1회성 작업**(`restart: "no"`)이라, 계속 떠 있지 않고 종료된 게
  맞다. `Exited (0)`의 `0`은 "오류 없이 끝남"을 뜻한다.
- WSL 환경에서는 `docker inspect`의 health 값이 빈 값으로 나올 수 있으니 **`ps`의 STATUS
  열을 신뢰**한다.

목록이 아예 비어 있거나 서비스가 꺼져 있으면 스택이 안 떠 있는 것이다 — 부팅 절차는
[`ONPREM_DEPLOYMENT.md`](../../infra/central/ONPREM_DEPLOYMENT.md)의 "부팅" 섹션을 따른다
(참고로 부팅 명령은 아래이며, 상세·주의사항은 배포 문서에 있다):

```bash
# 스택을 상시 가동으로 올린다 (배포 문서 소관 — 여기선 검증 후 필요 시 참고용)
docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env up -d --build
```

---

## 4. 2단계 — 브라우저 접속 + 로그인 확인

### 중앙 PC의 LAN IP 확인

브라우저 주소와 플랫폼 설정(`PUBLIC_HOST`)이 같아야 로그인이 안정적으로 된다. 먼저 IP를
확인한다.

```bash
hostname -I
```

여기서 나온 중앙 PC의 사내망 IP가 `<CENTRAL_IP>`다. 플랫폼에 설정된 값과 같은지 대조한다.

```bash
# 플랫폼이 브라우저 주소로 기대하는 값(PUBLIC_HOST) 확인
grep -E '^PUBLIC_HOST=|^WEB_PORT=' infra/central/central.env
```

판단 기준:

- `hostname -I`의 IP와 `PUBLIC_HOST`가 **같아야 한다.**
- 예: `PUBLIC_HOST=172.30.1.10`, `WEB_PORT=8080`이면 접속 주소는
  `http://172.30.1.10:8080`이다.

> **왜 이게 중요한가** — 로그인 토큰의 발급자(issuer)는 `PUBLIC_HOST` 기준으로 고정돼 있다.
> 브라우저가 접속한 주소와 `PUBLIC_HOST`가 다르면, API가 토큰을 거부해서 **로그인 직후
> 튕기거나 403 오류**가 난다. "로그인이 안 된다"는 문의의 가장 흔한 원인이다.

### 브라우저 접속

브라우저에서 아래 주소로 들어간다.

```text
http://<CENTRAL_IP>:8080
```

**정상 기대값**:

- keycloak **로그인 화면**이 뜬다(아이디/비밀번호 입력창).
- 로그인에 성공하면 FCC 플랫폼의 홈(대시보드) 화면으로 넘어가고, 왼쪽에 **홈 / 시험하기 /
  결과 / 설정** 메뉴 그룹이 보인다.

로그인 화면이 안 뜨거나 로그인 후 다시 로그인 화면으로 튕기면 → 9단계 문제 해결표를 본다.

---

## 5. 3단계 — 기능별 확인 (시험원 워크스루)

로그인 후 왼쪽 메뉴를 눌러 각 기능이 살아있는지 확인한다. "무엇을 클릭 → 무엇이 보이면 정상"
기준이다. (메뉴명은 실제 화면 그대로다.)

| 메뉴 (그룹) | 클릭 후 | 정상이면 |
| --- | --- | --- |
| **홈** (홈) | 첫 화면 | 대시보드/요약 카드가 오류 없이 렌더된다. |
| **내 프로젝트** (시험하기) | 내게 배정된 프로젝트 목록 | 관리번호·모델명이 있는 프로젝트 카드가 보인다. 비어 있으면 배정이 없는 것(오류 아님). |
| **시료 목록** (시험하기) | 시료 인벤토리 | 시료번호·상태 목록과 직접 편집기, revision history, PM/RF export가 열린다. |
| **테스트 플랜** (시험하기) | 테스트 플랜 워크벤치 | 초안/발행된 플랜 목록이 보인다. |
| **시험 챔버** (시험하기) | 챔버 현황 | 등록된 챔버 노드와 진행 상태가 보인다(6단계 참고). |
| **진행률** (결과) | 프로젝트 진행률 | 진행률(%)이 보인다. `%`가 비어 있으면 계획 시간 미설정 표시가 뜬다(가짜 0%가 아님 — 정상). |
| **측정 현황** (결과) | 프로젝트별 측정 현황 | 관리번호/모델 기준으로 측정 결과가 보인다. |
| **측정 작업** (결과) | 측정 작업 목록 | 진행/완료된 측정 작업이 보인다. |
| **측정 이력** (결과) | 측정 세션 이력 | 지난 측정 세션 기록이 보인다. |
| **성적서** (결과) | 성적서 목록 | 프로젝트별 성적서(성적서 번호)가 보인다. |

각 화면이 **오류 페이지 없이 목록/카드가 렌더되면 정상**이다. 특정 화면만 하얗게 뜨거나
오류가 나면 해당 API 로그를 본다(7단계).

> 측정 결과가 중앙에 올라오는 흐름(측정 PC → 중앙 DB 동기화)은 백그라운드로 동작한다.
> **측정 현황/측정 이력**에 최근 측정이 반영돼 보이면 동기화가 살아있는 것이다.

---

## 6. 4단계 — 챔버 노드 연결 확인

챔버 노드(측정 PC)는 컨테이너가 아니라 네이티브로 돌며, LAN으로 중앙 Gateway
(`http://<CENTRAL_IP>:8080/platform/...`)에 붙는다. 중앙은 등록된 챔버 Session API
(`http://<NODE_IP>:9000`)으로 측정 명령을 전달한다. 노드가 중앙에 연결됐는지는
브라우저에서 본다.

- 브라우저에서 **시험 챔버** 메뉴를 연다.
- **정상이면**: 등록된 챔버 노드가 목록에 보이고, 최근에 신호를 보낸(heartbeat) 노드는
  "온라인/활성"으로 표시된다.

노드가 안 보이거나 오프라인이면 확인할 것:

- 노드 PC의 `FCC_CENTRAL_BASE_URL`이 `http://<CENTRAL_IP>:8080`인지.
- 중앙 DB의 챔버 `base_url`과 노드의 `FCC_CENTRAL_NODE_BASE_URL`이
  `http://<NODE_IP>:9000`인지.
- 노드의 머신 토큰(`FCC_CHAMBER_CLIENT_SECRET`, realm `fcc-chamber-node`)이 중앙과
  일치하는지.

> 챔버 토큰/권한(`platform:chamber` — heartbeat 전용, 데이터 읽기 불가)의 상세 운영은
> [`chamber-token-rbac-runbook.md`](./chamber-token-rbac-runbook.md)를 본다. 시험원은 보통
> "노드가 보이는지"만 확인하면 된다.

---

## 7. 로그/진단 보는 법

문제가 있을 때 서비스별 로그를 본다. `<서비스>`는 `postgres` / `keycloak` /
`headless-api` / `platform-api` / `web` 중 하나로 바꾼다.

특정 서비스 로그를 실시간으로 계속 본다(멈추려면 `Ctrl+C`):

```bash
docker compose -f infra/docker-compose.central.yml logs -f platform-api
```

최근 200줄만 본다:

```bash
docker compose -f infra/docker-compose.central.yml logs --tail=200 platform-api
```

에러만 대략 골라 본다:

```bash
docker compose -f infra/docker-compose.central.yml logs platform-api | grep -iE "error|exception|traceback"
```

**명령 의미**

| 옵션 | 의미 |
| --- | --- |
| `logs -f <서비스>` | 해당 서비스 로그를 실시간으로 따라간다. |
| `--tail=200` | 최근 200줄만 본다(전체 스크롤 방지). |
| `grep -iE "..."` | 대소문자 무시하고 오류 관련 줄만 추린다. |

어느 서비스 로그를 볼지 감이 안 오면:

- **로그인/접속 문제** → `keycloak`, `web`
- **화면은 뜨는데 특정 기능 오류** → `platform-api`(프로젝트/시료/진행률/성적서/챔버) 또는
  `headless-api`(측정 작업/세션)
- **DB 관련 오류 메시지** → `postgres`

---

## 8. 스모크/봉인 검증 (선택, 조금 더 아는 사람용)

컨테이너 상태만이 아니라 스택 정의 자체가 온전한지 확인하고 싶을 때.

스택 정의(compose)가 문법·값 오류 없이 유효한지 정적 검증:

```bash
docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env config
```

- **정상이면**: 최종 조립된 설정이 출력되고 오류 메시지가 없다. (출력이 길면 끝에
  `>/dev/null && echo CONFIG-OK`를 붙여 `CONFIG-OK`만 확인해도 된다.)

중앙 스택 구성 규칙(포트/서비스/win32-free 등)이 깨지지 않았는지 봉인 테스트:

```bash
python -m pytest tests/test_central_docker_compose.py -q
```

- **정상이면**: 모든 테스트가 통과(`passed`)한다.

---

## 9. 상황별 문제 해결 결정표

| 증상 | 할 일 |
| --- | --- |
| 로그인 직후 튕긴다 / 로그인 후 403 | 접속한 브라우저 IP와 `PUBLIC_HOST`가 같은지 확인(4단계) → 다르면 배포 문서로 realm/`PUBLIC_HOST` 정정 |
| 브라우저 접속 자체가 안 된다 | `ps`로 `fcc-central-web`이 `Up`인지 확인(1단계) → `logs web` 확인 |
| `502 Bad Gateway`가 보인다 | 뒤쪽 API 컨테이너가 죽었거나 재생성됨 → `ps`로 `headless-api`/`platform-api` 확인 → `logs <해당 API>` |
| 특정 화면만 하얗게/오류 | 그 기능 담당 API 로그 확인(7단계 매핑) |
| 진행률이 `0%`가 아니라 비어 있다 | 정상. 계획 시험 시간이 미설정된 항목은 `%` 대신 안내 문구가 뜬다(가짜 0% 방지) |
| `fcc-central-migrate`가 `Exited (0)` | 정상. 1회성 스키마 작업이 성공적으로 끝난 상태다 |
| 챔버 노드가 목록에 안 보인다 | 노드가 `http://<CENTRAL_IP>:8080`을 바라보는지, 챔버 `:9000`이 LISTENING인지, 머신 토큰이 일치하는지 확인(6단계) |
| 특정 서비스가 `Exited`/`Restarting` (migrate 제외) | `logs <서비스>`로 원인 확인 → 배포 문서 절차로 재기동 |
| 중앙 PC를 재부팅했다 | `ps`로 5개 서비스 자동 기동 확인(1단계). 안 떴으면 배포 문서 부팅 절차 |

---

## 10. 매일 아침 최소 점검 명령

복사해서 그대로 쓴다.

```bash
# 1) 작업 폴더 이동 (중앙 PC repo = fcc-test-platform)
cd /path/to/fcc-test-platform

# 2) 컨테이너 5개 + migrate(Exited 0) 상태 확인
docker compose -f infra/docker-compose.central.yml ps

# 3) 중앙 PC IP와 PUBLIC_HOST 대조
hostname -I
grep -E '^PUBLIC_HOST=|^WEB_PORT=' infra/central/central.env
```

그 다음 브라우저에서 접속해 로그인까지 되는지 확인한다.

```text
http://<CENTRAL_IP>:8080
```

이상이 있으면 9단계 결정표를 본다.

---

## 11. 관련 문서

- **배포(설치·구성·시크릿·realm)**: [`infra/central/ONPREM_DEPLOYMENT.md`](../../infra/central/ONPREM_DEPLOYMENT.md)
- **개발 PC에서 localhost로 띄우기**: [`infra/central/LOCAL_DEVELOPMENT.md`](../../infra/central/LOCAL_DEVELOPMENT.md)
- **중앙 스택 구성 SSOT(포트/서비스/게이트웨이)**: [`infra/README.md`](../../infra/README.md)
- **챔버 머신 토큰·권한 운영**: [`chamber-token-rbac-runbook.md`](./chamber-token-rbac-runbook.md)
- **챔버 실측 스테이징 런북**: [`chamber-real-measurement-staging-runbook.md`](./chamber-real-measurement-staging-runbook.md)
- **챔버 관측 대시보드**: [`chamber-observability-dashboard.md`](./chamber-observability-dashboard.md)
- **API 관측 런북 / 경보 규칙 / SLO**: [`runbook-api-observability.md`](./runbook-api-observability.md),
  [`prometheus-alert-rules.md`](./prometheus-alert-rules.md), [`slo.md`](./slo.md)
