# 중앙 PC 실운영 전환 검증 런북 (S0 → S6)

> 🔴 **필독 — 중앙 PC 최초 구축·전환 전용.**
> **챔버 PC 절차는 여기 없습니다** → [`chamber-pc-operational-verification-runbook.md`](chamber-pc-operational-verification-runbook.md).
> ⚠️ 최종 갱신 2026-08-05 이후 착지한 챔버 승인 축(`PATCH /platform/chambers/{chamber_id}/web-session-approval`)과 워크북 업로드 경로가 이 문서에 **없습니다**.

이 문서는 **중앙 PC를 데모/개발 상태에서 실운영 상태로 전환하고, 측정 1건이 중앙
화면에 반영되는 것까지 확인**하는 1회성 절차다. 매일 아침 점검은 이 문서가 아니라
[`central-pc-fcc-platform-verification-guide.md`](./central-pc-fcc-platform-verification-guide.md),
설치·구성 SSOT 는 [`ONPREM_DEPLOYMENT.md`](../../infra/central/ONPREM_DEPLOYMENT.md) 소관이다.

> 이 아래 모든 명령에서 `<CENTRAL_IP>` = 중앙 PC 사내망 고정 IP,
> `<NODE_IP>` = 측정 PC(챔버 노드) IP 로 바꿔 읽는다.

**전제**: 중앙 PC 에 WSL Ubuntu + Docker 가 설치돼 있고 repo 가 배치돼 있다.

---

## 사전 단계 — 중앙 PC · 측정 PC **양쪽** 코드 최신화 (0단계보다 먼저)

이 절차는 2026-07-30 에 머지된 수정(main `45d7e1f9`)을 전제한다. **그 수정이 두 PC 에
나뉘어 들어가므로 한쪽만 갱신하면 측정 결과가 중앙에 유입되지 않는다.**

| 무엇이 | 어느 PC 에 | 없으면 |
|---|---|---|
| 마이그레이션 `011` (ingestion id/timestamp DB 소유권) + `012` (`report_runs.created_at` DB 소유권) | **중앙 PC** | 세션 또는 report parent INSERT 가 NOT NULL 위반 |
| 멤버십 issuer 해소 (platform-api) | **중앙 PC** | 권한 부여가 404 |
| `test_sessions` 부모행 upsert (`test_runner_init` 합성) | **측정 PC** | 첫 sync 가 `session_id` FK 위반 |

## ⚠️ 저장소 배치 (2026-09-03 변경)

**중앙 PC 에는 `fcc-test-platform` 하나만 둔다. `FCC_mobile_test_automation` 을 두지
않는다.**

| PC | 두는 저장소 | 빌드하는 이미지 |
|---|---|---|
| **중앙 PC** | `fcc-test-platform` 하나 + Docker | `fcc-central-platform-api` · `fcc-central-web` |
| **챔버 PC** | `FCC_mobile_test_automation` 하나 | `fcc-unlicensed-headless-api` |

무엇이 바뀌었나: 이전에는 이미지 하나(`fcc-central-api:latest`)가 세 서비스를 겸했고
빌드 컨텍스트가 FCC 저장소 루트였다 — **그것 하나 때문에** 중앙 PC 가 FCC 저장소를
요구했다. 설계가 아니라 편의였다. 지금은 `headless-api` 만 provider 저장소가 빌드해
태그하고, 중앙 compose 는 `build:` 없이 `image:` 로 소비한다(`web` 이 2026-08-31 에 간
길의 거울상이다).

근거(실측 2026-09-03): FCC 트리 없이 빌드한 `fcc-central-platform-api` 이미지가
`create_app()` 에 성공하고 **OpenAPI 64 경로가 기존 서비스와 완전히 동일**하다.
이미지 안에 `/app/src` 가 없다.

⚠️ **provider UI descriptor 는 이 저장소가 담지 않는다.** provider 배포가
`config/provider-ui/*.json` 에 놓고 platform 이 기동 시 읽는다(몇 개를 어디서 읽었는지
매 기동 로그로 말한다). 담으면 provider 소유 내용의 두 번째 사본이 되고 사본은
갈라진다 — 2026-09-01 에 실측된 그대로다.

### 중앙 PC

```bash
cd /path/to/fcc-test-platform             # ⚠️ FCC 저장소가 아니다 (위 배치표)
git status --short                        # 로컬 수정이 있으면 먼저 정리/보존
git pull --ff-only origin main
git log --oneline -1
ls migrations/011_ingestion_owned_defaults.sql    # 존재해야 한다
ls migrations/012_report_run_ingestion_parent.sql # report parent default
```

⚠️ **경로가 `migrations/` 이지 `docs/platform/migrations/` 가 아니다.** 이 레인은 그
트리를 상자 루트로 배달한다. 컨테이너 **이미지 안**에서는 `/app/docs/platform/
migrations` 이고, compose 가 그 경로를 `--migrations-dir` 로 명시한다 — 이미지에는
상자 표식이 없어 자동 해소가 성립하지 않기 때문이다.

`git pull` 이 거부되면 로컬에 커밋되지 않은 수정이 있다는 뜻이다. 그 내용을 확인해
보존할지 버릴지 정한 뒤 진행한다(임의로 `checkout --` 하지 않는다).

`central.env` 는 gitignore 대상이라 pull 로 덮이지 않는다.

### 측정 PC (챔버 노드)

세션 부모행을 만드는 코드는 **측정 PC 쪽 러너**에 있다. 중앙만 갱신하면 이 단계가 빠진다.

```bat
cd C:\FCC_mobile_test_automation
git pull --ff-only origin main
python build_nuitka.py                    REM .exe 로 배포해 쓰는 경우에만
REM headless-api 이미지도 여기서 빌드해 태그한다 (2026-09-03 —
REM 중앙 PC 는 이 저장소를 두지 않으므로 중앙에서 빌드할 수 없다).
```

판정: 측정 PC 에서 아래가 비어 있지 않아야 한다(부모행 upsert 배선 존재 확인).

```bat
findstr /C:"provider_session_id" src\application\headless\central_backend_sync_adapter.py
```

---

## 0단계 — 방화벽 포트 (가장 먼저, 여기서 막히면 뒤가 전부 헛수고)

보안 승인 기준의 FCC 운영 경로는 **중앙 `8080/8081` + 챔버 `9000`**이다.
결과 동기화는 인증된 `POST /platform/chambers/{chamber_id}/result-ingestions`
경계를 지나므로 챔버가 중앙 PostgreSQL `5432`에 직접 붙지 않는다 — `5432`는
중앙 내부 전용이다. EMS의 중앙 `8090`은 기존 서비스가 사용하므로 FCC가 건드리지 않는다.

| 방향 | 포트 | 운영 상태 | 쓰는 주체 | 없으면 생기는 증상 |
|---|---:|---|---|---|
| → 중앙 `8080` | web Gateway | 승인 대상 | 시험원 브라우저, 챔버 heartbeat·등록 | 웹 접속/챔버 heartbeat 실패 |
| → 중앙 `8081` | Keycloak | 승인 대상 | 브라우저 로그인, 챔버 토큰 발급 | 로그인·토큰 발급 실패 |
| **→ 노드 `9000`** | 챔버 Session API | 승인 대상 | 중앙이 측정 시작·진행률 요청 | 원격 측정 시작/진행 조회 실패 |
| 중앙 내부 `5432` | PostgreSQL | **중앙 내부 전용 — 챔버가 접속하지 않는다** | 중앙 platform-api, 시드/백업 | (챔버에서 열 필요 없음) |
| 중앙 내부 `8001/8002` | headless/platform API | LAN 직접 접속 금지 대상 | Docker Gateway 내부 | Gateway 뒤 API가 동작하지 않음 |

챔버 PC에서 중앙을 향해 확인한다:

```powershell
# Windows PowerShell 기준
Test-NetConnection 10.206.34.233 -Port 8080
Test-NetConnection 10.206.34.233 -Port 8081
```

중앙 PC에서 챔버를 향해 확인한다:

```powershell
Test-NetConnection <NODE_IP> -Port 9000
```

세 결과 모두 `TcpTestSucceeded : True`여야 한다. 스택이나 Session API가 아직 떠 있지
않으면 False가 정상이므로, 각 기동 단계 이후 다시 확인한다.

### ⚠️ 노드 `9000` 은 승인 요청 **전에** 그 기계에서 비었는지 확인한다

`9000` 은 **잘 겹치는 포트**다. 실측 2026-09-01: 개발 PC 에서 이미 다른 제품(EMS 오브젝트
스토리지 `rustfs`)이 `9000` 을 점유하고 있었고 `9001` 도 사용 중이었다. **NI 계측
소프트웨어가 깔린 챔버 PC** 라면 특히 확인이 필요하다.

⚠️ **승인받은 포트가 이미 남의 것이면 그 승인은 헛수고다.** 요청서를 내기 전에 그 기계에서:

```powershell
# Windows PowerShell — 비어 있으면 아무것도 출력되지 않는다
Get-NetTCPConnection -LocalPort 9000 -ErrorAction SilentlyContinue
```

**운영자 판정 (2026-09-01)** — 중앙(운영) 챔버 PC 는 **`9000` 을 그대로 쓴다**. 개발 PC 처럼
그 포트가 이미 점유된 기계에서만 우회한다. 코드는 포트를 바꿀 수 있으므로 이 판정에 코드
변경이 필요 없다:

```bash
FCC_SESSION_NODE_PORT=9010    # 기본값은 9000 — 점유된 기계에서만 바꾼다
```

> ⚠️ 바꿨다면 **세 곳이 같이 움직인다** — 노드 기동 환경변수, 중앙 챔버 등록의
> `base_url`(`http://<NODE_IP>:<PORT>`), 그리고 방화벽 승인 포트. 하나만 바꾸면 중앙이
> 측정을 시작시키지 못하고 그 증상은 **연결 실패가 아니라 「시작이 안 걸린다」**로 나타난다.

> **`5432`는 승인 범위에 포함될 필요가 없다.** 측정 결과 동기화는 인증된
> `POST /platform/chambers/{chamber_id}/result-ingestions`(중앙 `8080` Gateway 뒤)를
> 지나고, 중앙이 그 뒤에서 DB에 적재한다. 챔버 런타임은 `FCC_CENTRAL_DB_URL`도 psycopg도
> 쓰지 않는다. 따라서 `8080/8081/9000`만 허용한 환경에서 S6 결과 반영이 정상 동작해야 하며,
> 챔버 PC에서 `Get-NetTCPConnection -RemotePort 5432`가 **0건**인 것으로 그것을 확인한다.

### WSL 네트워킹

중앙 PC WSL 이 `networkingMode=mirrored` 면 컨테이너 published 포트가 호스트 LAN IP 로
그대로 노출된다. **NAT 모드면 `netsh interface portproxy` 가 추가로 필요**하다.

```bash
grep -i networkingMode /mnt/c/Users/*/.wslconfig
```

---

## S0 — 운영 env 작성

```bash
cd /path/to/fcc-test-platform    # ⚠️ 2026-09-03: 중앙 PC 는 FCC 저장소를 두지 않는다
cp infra/central/central.env.example infra/central/central.env
```

`infra/central/central.env` 에서 **반드시** 바꿀 값:

| 키 | 운영값 | 데모 기본값(폐기 대상) |
|---|---|---|
| `PUBLIC_HOST` | `<CENTRAL_IP>` | `localhost` |
| `POSTGRES_PASSWORD` | 실제 시크릿 | `fcc-dev-password` |
| `KEYCLOAK_ADMIN_PASSWORD` | 실제 시크릿 | `admin` |
| `FCC_CHAMBER_CLIENT_SECRET` | 실제 시크릿 (챔버 노드 머신 토큰) | `fcc-chamber-dev-secret` |
| `FCC_STAGING_CLI_SECRET` | 실제 시크릿 | `fcc-staging-cli-dev-secret` |

판정:

```bash
grep -E '^PUBLIC_HOST=|^POSTGRES_PASSWORD=|^KEYCLOAK_ADMIN_PASSWORD=' infra/central/central.env
hostname -I    # PUBLIC_HOST 와 같은 IP 가 목록에 있어야 한다
```

`PUBLIC_HOST` 가 브라우저 접속 주소와 다르면 **로그인 직후 튕기거나 403** 이 난다.
`central.env` 는 gitignore 대상이므로 커밋되지 않는다.

---

## S0-L — 로컬 로그인(`local_jwt`) 선택 ⚠ 평문 HTTP 로 노출한다면 **이쪽이다**

> **먼저 읽을 것.** 중앙 PC 를 `https://` 없이 `http://<CENTRAL_IP>:8080` 으로 노출하면
> **Keycloak 로그인은 원리적으로 불가능하다.** 브라우저는 PKCE 가 쓰는 `crypto.subtle` 을
> **보안 컨텍스트(https 또는 localhost)에서만** 제공하고, 그 API 는 설정으로 되살릴 수
> 없다. 증상은 브라우저 콘솔의
> `TypeError: Cannot read properties of undefined (reading 'digest')` 하나뿐이다.
>
> ⚠️ `ALLOW_INSECURE_TRANSPORT=true` 는 **이것을 고치지 않는다.** 그 스위치는 프론트의
> 전송 검증만 통과시킬 뿐이다. 2026-08-21 이후 그 조합(`authMode=oidc` +
> 평문 허용)은 **부팅에서 거부**되므로 조용히 잘못 뜨지는 않는다.

**어느 쪽을 고를지는 딱 하나로 갈린다.**

| 브라우저 접속 주소 | 고를 것 | 이 문서에서 볼 절 |
|---|---|---|
| `https://…` (인증서 있음) | Keycloak (`oidc_jwt`) | S1 · S3 (기존 절차 그대로) |
| `http://<IP>:8080` (평문) | **로컬 로그인 (`local_jwt`)** | **이 절 → S3-L**. S1 은 건너뛴다 |
| `http://localhost:8080` (그 PC 에서만) | 둘 다 가능 | 기존 절차 |

로컬 로그인을 고른다면 `infra/central/central.env` 에 다음을 더한다:

| 키 | 값 | 비고 |
|---|---|---|
| `FCC_PLATFORM_AUTH_MODE` | `local_jwt` | 다섯 번째 모드 |
| `FCC_PLATFORM_LOCAL_JWT_SECRET` | **32바이트 이상** 랜덤 문자열 | 짧으면 **부팅 거부**(RFC 7518 §3.2) |
| `FCC_PLATFORM_LOCAL_JWT_ISSUER` | 예: `https://fcc-platform.internal/auth` | 토큰 `iss` — 접속 주소와 무관해도 된다 |
| `FCC_PLATFORM_LOCAL_JWT_AUDIENCE` | 예: `fcc-platform-web` | 토큰 `aud` |
| `FCC_PLATFORM_BOOTSTRAP_ADMIN_EMAIL` | 최초 관리자 사내 이메일 | 없으면 **아무도 로그인할 수 없다** |
| `FCC_PLATFORM_BOOTSTRAP_ADMIN_PASSWORD` | 초기 비밀번호 (8자 이상) | 첫 로그인에서 **변경 강제** |
| `WEB_AUTH_MODE` | `local` | SPA 로그인 전략 |
| `FCC_HEADLESS_AUTH_MODE` | `local_jwt` | ⚠️ **빠뜨리기 쉽다** — SPA 는 `/headless/*` 에도 **같은 토큰**을 보낸다 |
| `FCC_HEADLESS_LOCAL_JWT_SECRET` / `_ISSUER` / `_AUDIENCE` | platform 과 **같은 값** | 같은 토큰을 검증해야 하므로 같아야 한다 |
| `ALLOW_INSECURE_TRANSPORT` | `true` | ⚠️ **평문 배포에 필수** — 없으면 SPA 가 부팅을 거부한다 |

비밀키 생성:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

⚠️ **이 표는 2026-08-22 에 넷이 늘었고, 그 넷이 빠지면 각각 이렇게 실패한다** — 어느
증상도 *"설정이 틀렸습니다"* 라고 말해 주지 않는다:

| 빠뜨린 것 | 증상 |
|---|---|
| `WEB_AUTH_MODE` | 화면이 여전히 IdP 로 튕긴다 |
| `FCC_HEADLESS_AUTH_MODE` (+ 그 `LOCAL_JWT_*`) | 로그인은 되는데 **headless 화면이 전부 401** |
| `ALLOW_INSECURE_TRANSPORT` | **화면이 아예 안 뜬다** (SPA 런타임 스키마가 평문 엔드포인트를 거부) |
| `FCC_PLATFORM_LOCAL_JWT_*` | platform-api 가 **부팅 거부** → 게이트웨이 뒤 전부 죽는다 |

⚠️ **마지막 줄은 2026-08-22 까지 운영자가 고칠 수 없는 것이었다.** compose 가 그 값들을
컨테이너로 넘기지 않아, `central.env` 에 적어도 **아무 효과가 없었다**(같은 파일이
`ALLOW_INSECURE_TRANSPORT` 에 대해 이미 겪고 적어 둔 형태다). 그 배선은 이제 있다 —
**중앙 PC 의 repo 를 이 커밋 이후로 갱신했는지 확인할 것.**

**그래서 이제 확인할 수 있다** (2026-08-22) — 값을 바꾼 뒤 반드시 돌린다:

```bash
python3 scripts/check_auth_mode_pairing.py --env-file infra/central/central.env
```

`0` = 정합 · `1` = 어긋남(고칠 값을 알려준다) · `2` = 판정할 값이 없음.
⚠️ **`2` 는 통과가 아니다** — 묻지 못한 축은 통과가 아니라 미확인이다.

⚠️ **`$` 를 비밀번호나 시크릿에 쓰지 말 것 — compose 가 조용히 먹는다.** 실측:
`FCC_PLATFORM_BOOTSTRAP_ADMIN_PASSWORD=Pa$$w0rd$USER!` 는 컨테이너에 `Pa$w0rddevuser!` 로
도착한다(`$$`→`$`, `$USER`→호스트 변수). 증상은 *"env 는 맞는데 로그인이 안 된다"* 이고
원인이 화면에 뜨지 않는다. 위 `token_urlsafe` 는 `$` 를 만들지 않아 안전하고, 관리자
비밀번호는 영숫자로 정하거나 `$` 를 `$$` 로 이스케이프한다.

> **마이그레이션 026 은 따로 돌릴 필요가 없다.** 실측(2026-08-22): `central-migrate` 는
> `condition: service_completed_successfully` 원샷이고 **매 `docker compose up -d` 마다
> 다시 실행**되며 `migrate` 가 미적용분 전체를 체크섬으로 적용한다. S2 의 스택 기동이 곧
> 026 적용이다. ⚠️ 이 문단의 이전 판은 *"이미 떠 있는 DB 에는 자동 적용되지 않는다"* 고
> 적었고 그것은 **거짓이었다** — 적대 평가가 실 컨테이너로 반증했다.

스택을 올린 뒤에는 **실제로 서빙되는 값**까지 확인한다. `runtime-config.js` 는 web 컨테이너
기동 시 env 에서 생성되므로, `central.env` 만 고치고 재기동하지 않으면 화면은 옛 전략으로
남는다:

```bash
python3 scripts/check_auth_mode_pairing.py --env-file infra/central/central.env \
  --runtime-config-url http://<CENTRAL_IP>:8080/runtime-config.js
```

⚠️ **초기 비밀번호는 여기(env)에 적히고 배포 로그에도 남는다.** 그래서 첫 로그인이
비밀번호 변경 화면으로 **강제 이동**하고, 바꾸기 전에는 다른 어떤 화면도 열리지 않는다
(서버가 `AUTH_PASSWORD_CHANGE_REQUIRED` 로 거부한다). 바꾼 뒤 env 의 그 값은 더 이상
유효하지 않다.

⚠️ **부트스트랩은 로컬 사용자가 하나도 없을 때만 동작한다.** 이미 관리자가 있으면
env 를 바꾸고 재시작해도 **아무 일도 일어나지 않는다** — 그렇지 않으면 그것은
부트스트랩이 아니라 비밀번호 재설정 백도어다.

⚠️ **그리고 평문 HTTP 가 안전해지는 것은 아니다.** 브라우저가 막지 않을 뿐이고 비밀번호와
토큰은 LAN 을 평문으로 흐른다. **TLS 가 유일한 근본 해결이다**(장부 등재).

---

## S1 — Keycloak realm 에 LAN origin 추가 ⚠

`PUBLIC_HOST` 는 envsubst 로 주입되지만 realm import 는 **정적 파일**이라 LAN IP 가
자동으로 안 들어간다. 이걸 빠뜨리면 S3 로그인이 100% 실패한다.

```bash
# 등재 여부만 확인 (누락이면 exit 1)
python scripts/central_realm_add_origin.py --host <CENTRAL_IP> --check

# 실제 추가 (멱등 — 이미 있으면 no-op)
python scripts/central_realm_add_origin.py --host <CENTRAL_IP>
```

`redirectUris` / `webOrigins` / `post.logout.redirect.uris` 3곳에 additive 로 들어가고
기존 `localhost` · `127.0.0.1` · `:5173` 항목은 보존된다. 변경 후 diff 가 3곳이 아니면
멈추고 확인한다.

realm 파일 변경은 **keycloak 컨테이너 recreate 시점에 반영**되므로 S2 부팅으로 이어진다.

---

## S2 — DB 백업 → 초기화 → 스택 부팅

### (a) 기존 DB 백업

개발 중 쌓인 시드 데이터가 있으면 운영 시작 전에 남겨 둔다.

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  pg_dump -U fcc fcc_central > ~/fcc_central_backup_$(date +%Y%m%d_%H%M).sql
ls -lh ~/fcc_central_backup_*.sql    # 파일 크기가 0 이 아니어야 한다
```

### (b) 볼륨 삭제 (dev 시드 폐기)

```bash
docker compose -f infra/docker-compose.central.yml down
docker volume rm fcc-central_central-pgdata
```

> 볼륨명이 다르면 `docker volume ls | grep pgdata` 로 확인한다. 이 명령은 **되돌릴 수
> 없다** — (a) 의 백업 파일이 실제로 생성됐는지 먼저 확인한다.

### (c) 부팅

```bash
DOCKER_CONFIG=/tmp/fcc-docker-config \
docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env up -d --build
```

### (d) 판정

```bash
docker compose -f infra/docker-compose.central.yml ps
```

기대값 — 5개 `Up` + migrate 는 `Exited (0)`:

```text
fcc-central-postgres       Up (healthy)
fcc-central-keycloak       Up (healthy)
fcc-central-headless-api   Up (healthy)
fcc-central-platform-api   Up (healthy)
fcc-central-web            Up
fcc-central-migrate        Exited (0)
```

**마이그레이션이 끝까지 적용됐는지 반드시 확인**한다 (부분 적용은 나중에 조용히 터진다):

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -tAc "select version from schema_migrations order by version;"
```

`docs/platform/migrations/` 의 파일 목록과 **개수·이름이 일치**해야 한다 (현재 012 까지).
**011 이 빠지면 측정 동기화가 세션 타임스탬프 NOT NULL 로 실패**하고, **012 가 빠지면
report parent 의 `created_at` DB default 가 없어져 첫 report ingestion 이 실패**한다 (S6 참고).
불일치면 migrate 로그를 본다:

```bash
docker compose -f infra/docker-compose.central.yml logs central-migrate
```

> **S2(b) 로 볼륨을 지우지 않고 기존 DB 를 그대로 쓰는 경우** — `001` 은 스키마 SSOT 가
> 바뀔 때마다 exporter 가 **재생성**하므로 파일 checksum 이 최초 적용 시점의 기록과
> 달라진다. 러너는 이를 drift 로 보고 멈추는데, bootstrap(`001`)의 drift 는 양성이므로
> 전용 서브커맨드로 원장을 정정한 뒤 증분을 적용한다:
>
> ```bash
> python scripts/platform_db_migrate.py reconcile   # 001 원장 checksum 정정
> python scripts/platform_db_migrate.py migrate     # 011/012 등 미적용분 적용
> ```
>
> `reconcile` 은 **bootstrap 행만** 손대고, 증분 마이그레이션이 변조된 경우에는 거부한다
> (append-only 위반). 볼륨을 새로 만든 경우엔 해당 없음.

이 시점에 **0단계의 포트 확인을 다시** 수행한다 (이제 실제로 열려 있어야 한다).

---

## S3 — 접속 · 로그인 · 화면 워크스루

1. **중앙 PC 브라우저**에서 `http://<CENTRAL_IP>:8080` → Keycloak 로그인 화면.
2. **다른 PC 브라우저**에서 같은 주소 → 같은 화면. (여기서 실패하면 포트/방화벽 문제이지
   애플리케이션 문제가 아니다.)
3. 로그인 후 왼쪽 메뉴 10개를 눌러 오류 없이 렌더되는지 확인 —
   [검증 가이드 §5](./central-pc-fcc-platform-verification-guide.md) 표 그대로.

`localhost` 로는 되는데 `<CENTRAL_IP>` 로만 로그인이 실패하면 **S1 미반영**이다.
realm 은 컨테이너 recreate 때만 import 되므로:

```bash
docker compose -f infra/docker-compose.central.yml up -d --force-recreate keycloak
```

---

## S3-L — 로컬 로그인 워크스루 (S0-L 을 골랐을 때만)

S1 · S3 대신 이 절을 수행한다.

1. **중앙 PC 브라우저**에서 `http://<CENTRAL_IP>:8080` → **자체 로그인 화면**이 뜬다
   (Keycloak 으로 튕기지 않는다).
2. `FCC_PLATFORM_BOOTSTRAP_ADMIN_EMAIL` / `_PASSWORD` 로 로그인.
3. **비밀번호 변경 화면으로 자동 이동한다.** 여기서 바꾸지 않으면 다른 화면은 열리지
   않는다 — 정상 동작이다.
4. 변경 후 홈으로 이동하고, 왼쪽 메뉴가 오류 없이 렌더되는지 확인한다.

판정 — 화면 없이 확인하려면:

```bash
# 로그인 (토큰 쌍이 돌아와야 한다)
curl -s -X POST http://<CENTRAL_IP>:8080/platform/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"<ADMIN_EMAIL>","password":"<INITIAL_PW>"}' | head -c 200

# 틀린 비밀번호 — 401 이어야 하고, 없는 이메일과 응답이 같아야 한다
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://<CENTRAL_IP>:8080/platform/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"<ADMIN_EMAIL>","password":"wrong"}'
```

⚠️ **없는 이메일과 틀린 비밀번호의 응답은 의도적으로 구분 불가**하다(상태코드·본문·
소요 시간 전부). 잠긴 계정도 같은 답을 준다. 그것이 사내 이메일 명부 열거를 막는 유일한
방법이므로, *"없는 계정이라고 알려주지 않는다"* 는 결함이 아니라 설계다.

⚠️ **5회 연속 실패하면 15분 잠긴다.** 잠긴 동안에는 올바른 비밀번호도 거부되고, 별도
해제 조치 없이 시간이 지나면 풀린다.

### 로그인이 **429** 로 거부될 때 (2026-08-22, 계정축 스로틀)

잠금(401)과 스로틀(429)은 **다른 것**이고 응답으로 구분된다.

| 응답 | 무엇 | 얼마나 |
|---|---|---|
| `401` | 자격증명 불일치 **또는** 계정 잠김 | 잠겼다면 15분 |
| `429` + `RateLimit-Limit: 6` | **계정축** — 이 이메일로 한 창에 **실패**를 6회 썼다 | 60초 |
| `429` + `RateLimit-Limit: 1200` | **peer 축**(이 웨이브가 건드리지 않은 기존 예산) | 60초 |

`Retry-After` 헤더가 남은 초를 그대로 알려준다. 웹 화면도 그 초를 표시한다.

⚠️ **실재하는 계정이 429 를 보면 이미 15분 잠긴 뒤다.** 잠금은 실패 **5회**에 걸리고
스로틀은 **7회째**에 걸리므로, 두 줄은 선택지가 아니라 순서다 — 60초를 기다려도 401 이
계속 나오면 그것은 스로틀이 아니라 잠금이고 15분이 답이다. 429 만 나고 잠기지 않는 것은
**실재하지 않는 이메일**로 시도했을 때뿐이다(그것이 계정 열거를 막는 설계다).

⚠️ **성공한 로그인은 예산을 쓰지 않는다.** 세는 것은 실패뿐이라, 오타 두 번 뒤 성공한
시험원은 다음 방문에서 예산을 온전히 받는다.

⚠️ **예산 6은 계정 잠금 5회보다 하나 크게 파생된 값이다.** 좁히면 잠금에 도달하는 시도가
먼저 429 로 잘려 **계정 잠금 기능이 죽는다**(코드가 부팅에서 거부한다). ⚠️ 넓히는 방향과
`window_seconds` 는 그 가드가 보지 않으므로, 값을 바꾸려면 개발자에게 알릴 것.

### 로그에 **"token revocation list"** 경고가 뜰 때 (2026-08-22)

이 경고는 **두 종류**이고, 문장이 다르며, 운영자가 할 일도 다르다.

| 로그에서 찾을 문구 | 무슨 일 | 할 일 |
|---|---|---|
| `No custodian within its share lost an entry` | 자기 몫보다 많이 든 신원이 **자기 항목**을 버렸다. 몫 이내인 사람은 아무도 잃지 않았다 | ⚠️ **이것만으로는 공격이 아니다.** 같은 줄이 정상 포화에서도 뜬다 — 그 줄이 *"N개의 연속 활성 세션이면 이 상한에 스스로 닿는다"* 를 함께 알려 준다. 공격과 포화를 가르는 것은 문구가 아니라 **속도**다: 실린 두 수(`since the last report` / `since start`)가 몇 분 사이에 수천으로 뛰면 캡처된 리프레시 토큰 재생을 의심하고, 며칠에 걸쳐 수십이면 용량이다 |
| `This is a MISCONFIGURATION` | 추적 중인 **신원 수가 상한보다 많다.** 모두가 최소 몫 1 을 들고 있어 공정한 선택 자체가 없다 | **부하가 아니라 구성 오류다.** 그 줄이 실은 신원 수와 상한을 개발자에게 그대로 전달할 것. ⚠️ 프로덕션 상한 20,000 에서는 서로 다른 인증 신원 20,000개를 요구하므로 사실상 도달하지 않는다 |

⚠️ **위 두 문구는 코드 상수에 결박돼 있다** (`CUSTODY_EVICTION_PHRASE` ·
`DEGENERATE_EVICTION_PHRASE`) — 봉인이 이 문서와 코드를 같은 값에 묶는다. 그 결박이
없던 판에서 이 절은 **코드에 존재하지 않는 두 문자열을 grep 하라고 적고 있었고, 의미도
반대였다**(적대 평가 2라운드에서 평가자 둘이 각각 발견). 그것을 따른 운영자는 정상
포화에 무고한 시험원의 비밀번호를 바꾸게 한다.

⚠️ **경고는 창당 한 줄로 묶인다**(사건당 한 줄이면 초당 수백 줄이 된다). 실린 첫 수는
**마지막 보고 이후** 누적이지 *"지금 이 순간"* 이 아니고, 창이 닫힌 뒤 축출이 멈추면
그 꼬리는 **다음 축출이 올 때까지 보고되지 않는다** — 그래서 두 번째 수(프로세스
시작 이후 누계)를 함께 싣는다. 두 수가 크게 벌어져 있으면 조용한 구간이 있었다는 뜻이다.

⚠️ **축출된 항목은 만료 전까지 다시 유효해진다.** 그것이 이 경고가 존재하는 이유다.

### 여러 명이 동시에 429 를 받는다면 (계정축이 아니다)

`RateLimit-Limit: 1200` 이면 peer 축이다.

⚠️ **peer 축이 "시험원별"인지 "배포 전체"인지는 배포 설정이 정한다 (2026-08-22 이후).**
그 버킷은 `/platform/**` 의 **모든 기본 예산 요청**이 공유하므로, 분당 1200 은 "로그인
1200회"가 아니라 "화면 조작을 포함한 요청 1200건"이다. 문제는 *누구의* 1200건이냐다.

**먼저 이것부터 확인한다** — API 컨테이너 기동 로그 한 줄이 답한다:

```bash
docker logs fcc-central-platform-api 2>&1 | grep -i 'proxy trust\|FORWARDED_ALLOW_IPS'
```

| 로그 | 뜻 | 그러면 |
|---|---|---|
| `proxy trust configured: FORWARDED_ALLOW_IPS=… (peer-axis mode: per-source)` | 시험원 **한 명당** 1200건 | 여러 명이 동시에 429 면 그것은 peer 축이 아니다. 게이트웨이 `limit_req` 나 다른 원인을 보라 |
| `FORWARDED_ALLOW_IPS is unset … peer rate-limit tier is charged per DEPLOYMENT, not per caller` | **전원 합쳐서** 1200건 | 시험원 수십 명이 대시보드를 함께 쓰는 것만으로 닿는다. **스크립트나 재시도 루프부터 찾지 말 것** |

두 번째 줄이 보이면 배포가 미완이다 — `central.env` 에 `CENTRAL_PROXY_IP` 를 두고
compose 가 그것을 `web` 의 정적 주소이자 두 API 의 `FORWARDED_ALLOW_IPS` 로 넘긴다
(기본값은 그렇게 되어 있으므로, 보통은 **누군가 그 값을 비웠다**는 뜻이다).

⚠️ **컨테이너가 아예 뜨지 않고 `refusing to start: unsafe proxy trust configuration` 이
찍힌다면 그것은 고장이 아니라 설계다.** 거부 사유가 어느 값 때문인지 이름으로 적혀 있다.
`*` 나 `0.0.0.0/0` 으로 "일단 넓게" 여는 것이 정확히 그 거부의 대상이다 — 그렇게 하면
공격자가 `X-Forwarded-For` 에 적은 값이 **그대로 신원이 된다**(오류 없이). 사내망 대역을
넣는 것도 같은 결과다. 신뢰해야 하는 것은 **리버스 프록시 한 주소**뿐이다.

확인 (⚠️ 아래 커맨드는 **일부러 존재하지 않는 이메일**을 쓴다 — 실제 관리자 이메일로
5회 돌리면 그 계정이 15분 잠긴다):

```bash
curl -s -o /dev/null -D - -X POST http://<CENTRAL_IP>:8080/platform/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"no-such-user@invalid.example","password":"x"}' \
  | grep -Ei 'HTTP/|RateLimit|Retry-After'
```

⚠️ **개발자에게 알릴 것**: 위 표가 `per-source` 인데도 여러 명이 동시에 429 라면 그것은
새 사실이다 — 값을 조정하기 전에 보고할 것. 반대로 `deployment-wide` 라면 **예산을 조이는
방향은 근무 시작 시각에 전원을 막을 수 있다**(실제로 한 번 그렇게 됐고 착지 전에 철회했다 —
ADR-0021 `D-11`). 예산 자체를 시험원 수에 맞추는 작업은 아직 안 됐다(장부 항목).


---

## S4 — 운영 시드 + 시험원 계정 발급

### (a) provider 행 등록 ⚠ (필수 — 없으면 측정 동기화가 전부 실패)

**마이그레이션 001~012 은 `providers` 테이블에 행을 넣지 않는다** (RBAC
permissions/roles 만 시드한다). 지금까지 있던 provider 행은 개발용 증거 스크립트가
만든 것이므로, **볼륨을 삭제하면 사라진다.**

`measurement_results.provider_id` · `measurement_attempts.provider_id` ·
`standard_time_catalog.provider_id` 는 모두 **`providers(id)` 를 참조하는 uuid FK** 이고,
동기화 어댑터는 `FCC_CENTRAL_PROVIDER_ID` 값을 **그대로 레코드에 넣는다**. 따라서
provider 행이 없거나 env 에 UUID 가 아닌 문자열을 넣으면 첫 동기화가
`invalid input syntax for type uuid` 또는 FK 위반으로 실패한다.

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -tAc "select provider_id from providers;"
```

비어 있으면 등록한다 (`provider_id` 는 provider registry 의 자연키):

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -c \
  "INSERT INTO providers(id, provider_id, product_line, contract_family, contract_version,
     base_url, capabilities_json, enabled, created_at, updated_at)
   VALUES (gen_random_uuid(), 'fcc-unlicensed-conducted', 'unlicensed-conducted',
     'fcc-conducted-headless', 'v1', 'http://<CENTRAL_IP>:8001', '{}', true, now(), now())
   ON CONFLICT (provider_id) DO NOTHING;"
```

등록 후 **UUID 를 확보해 둔다** — S4(b) 시드와 S5 측정 PC env 양쪽에 같은 값을 쓴다:

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -tAc \
  "select id from providers where provider_id='fcc-unlicensed-conducted';"
```

> ⚠ `central.env.example` 의 `FCC_CENTRAL_PROVIDER_ID=unlicensed` 는 **자연키처럼 보이는
> 예시값**이다. 측정 PC 에는 반드시 위에서 조회한 **UUID** 를 넣는다.

### (b) 진행률 catalog 시드

진행률(%)의 **분모**가 되는 표준 시험시간 catalog 는 자동 적용되지 않는다. 이 시드는
운영자가 손으로 관리하는 **진행률 워크북(.xlsm)** 에서 시험 유형별 계획 분(minute)을
읽어 넣는다 — 워크북 경로가 필요하다.

```bash
# provider UUID 확보 (--provider-id 는 providers.id 의 uuid 다, provider_id 텍스트가 아니다)
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -tAc \
  "select id from providers where provider_id='fcc-unlicensed-conducted';"

# 먼저 dry-run 으로 무엇이 들어갈지 확인 (DB 불필요, 기본 동작)
python scripts/seed_standard_time_catalog.py --workbook "<진행률 워크북.xlsm>"

# 확인 후 실제 적용
python scripts/seed_standard_time_catalog.py \
  --workbook "<진행률 워크북.xlsm>" \
  --provider-id <PROVIDER_UUID> \
  --db-url "postgresql://fcc:<POSTGRES_PASSWORD>@localhost:5432/fcc_central" \
  --apply
```

판정:

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -tAc "select count(*) from standard_time_catalog;"
```

0 이 아니어야 한다. 시드해도 **플랜 발행 전에는 `published_plan_expectation` 이 0** 이고,
이때 진행률 화면은 `0%` 가 아니라 "계획 시간 미설정" 문구를 띄운다 — 가짜 0% 를 만들지
않기 위한 의도된 동작이다.

### (c) 시험원 Keycloak 계정 생성

데모 계정(`admin` / `operator` / `viewer`)은 운영에 쓰지 않는다. 실제 인원 계정을 만든다.

```bash
KC() { docker compose -f infra/docker-compose.central.yml exec -T keycloak \
  /opt/keycloak/bin/kcadm.sh "$@"; }

KC config credentials --server http://localhost:8080 --realm master \
  --user admin --password '<KEYCLOAK_ADMIN_PASSWORD>'

# 사용자 생성 (이메일은 중앙 users 표시명/식별에 쓰인다)
KC create users -r fcc-dev \
  -s username=hong.gildong -s email=hong.gildong@company.com \
  -s firstName=길동 -s lastName=홍 -s enabled=true

# 최초 비밀번호 (첫 로그인 시 변경 강제)
KC set-password -r fcc-dev --username hong.gildong --new-password '<임시비밀번호>' --temporary
```

판정:

```bash
KC get users -r fcc-dev --fields id,username,email,enabled
```

### (d) 프로젝트 생성 → 생성자 자동 권한 (JIT)

**중앙 DB 에 사용자를 미리 넣을 필요는 없다.** 로그인한 사용자가 프로젝트를 생성하면
JIT(just-in-time) 로 중앙 `users` 에 등재되고 그 프로젝트의 `project_admin` 이 부여된다.

- 웹에서 프로젝트를 하나 생성한다 (관리번호 · 모델명 입력).
- 확인 (`project_membership` 은 `user_id` uuid 로 `users.id` 를 참조한다):

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -c \
  "select u.email, u.subject, m.role_key, m.team, p.project_code
     from project_membership m
     join users u on u.id = m.user_id
     join projects p on p.id = m.project_id;"
```

생성자가 `project_admin` 으로 1행 나오면 JIT 온보딩이 정상 동작한 것이다.

### (e) 나머지 인원에게 역할 · 팀 부여

역할은 **프로젝트 단위**로 준다. `role_key` 는 3종:

| `role_key` | 권한 | 대상 |
|---|---|---|
| `project_pm` | read + 시료 PM 칸 쓰기 | PM (시료 물류/자산) |
| `project_engineer` | read + claim + 시료 입고 칸 쓰기 | 시험원 |
| `project_admin` | 위 전부 + 멤버십 관리 | 프로젝트 관리자 |

`team` 은 권한과 **직교**한 분류축이며 `RF` / `SAR` 두 값만 유효하다 (게이트가 아니라
귀속·기본 필터용).

부여 대상은 **먼저 최소 1회 로그인**해야 한다 — 로그인 시 JIT 로 중앙 `users` 에 등재되기
때문이다. `user_subject` 는 Keycloak 사용자 ID(OIDC `sub`)다:

```bash
KC get users -r fcc-dev -q username=hong.gildong --fields id,username
```

화면 경로: 웹의 **멤버십** 화면(`/membership?project=<PROJECT_ID>`)에서 assign/revoke 가
가능하다. **이것이 정상 경로다.**

> **참고 — issuer 불일치 404 는 해소됐다 (2026-07-29).** 중앙 정체성은
> `(issuer, subject)` 로 식별되는데, 화면은 subject 만 보내고 서버는 빈 issuer 를
> legacy 로 치환해 조회했다. 반면 JIT 로 만들어진 사용자 행은 **실제 OIDC issuer**
> 를 쓰므로 조회가 빗나가 `unknown user_subject` 404 가 났다. 이제 issuer 미지정 시
> **요청자(actor)의 검증된 issuer → legacy** 순으로 해소한다(구 legacy 행 호환 유지).
> 아래 curl 은 다른 IdP 사용자를 지정하는 등 issuer 를 **명시해야 할 때만** 쓴다.

```bash
curl -X POST "http://<CENTRAL_IP>:8080/platform/projects/<PROJECT_ID>/memberships" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"user_subject":"<KEYCLOAK_USER_ID>",
       "user_issuer":"http://<CENTRAL_IP>:8081/realms/fcc-dev",
       "role_key":"project_engineer","team":"RF"}'
```

판정: 같은 경로 `GET` 이 방금 부여한 멤버십을 반환하고, 해당 시험원이 로그인했을 때
그 프로젝트가 **내 프로젝트** 목록에 보인다. 아래로 같은 subject 가 issuer 두 개로
갈라졌는지 점검할 수 있다 (갈라졌다면 권한이 로그인 계정에 안 붙는다):

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -c \
  "select subject, count(*) , array_agg(issuer) from users group by subject having count(*) > 1;"
```

> `<ACCESS_TOKEN>` 은 브라우저 개발자도구 Network 탭의 `Authorization` 헤더에서
> 복사하는 것이 가장 빠르다.

---

## S5 — 챔버 노드(측정 PC) 연결

### (a) 중앙에 챔버를 먼저 등록한다 ⚠

노드는 부팅 시 `POST /platform/chambers` 로 **자기등록을 시도하지만**, 그 엔드포인트는
`platform:admin` 권한을 요구하는 반면 노드 머신 토큰(realm `fcc-chamber-node`)은
`platform:chamber`(heartbeat 전용)만 갖는다. 자기등록 client 는 **실패를 non-fatal WARNING
으로 흡수**하므로 노드는 조용히 뜨고, 챔버는 화면에 영영 안 보인다.

따라서 **사람이 admin 토큰으로 먼저 등록**한다:

```bash
curl -X POST "http://<CENTRAL_IP>:8080/platform/chambers" \
  -H "Authorization: Bearer <ADMIN_ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"chamber_id":"chamber-a","name":"1번 챔버",
       "base_url":"http://<NODE_IP>:9000","enabled":true}'
```

판정: `GET /platform/chambers` 또는 `chamber_nodes` 조회에 행이 생긴다. 등록 없이는
heartbeat 도 참조 대상이 없어 실패한다.

### (b) 측정 PC 환경변수

측정 PC 는 컨테이너가 아니라 네이티브 `.exe` 다. `.env` 자동 로딩이 없으므로 **프로세스
환경변수**로 준다 (Windows 시스템 환경변수 또는 실행 배치 파일).

```bat
REM run_fcc.bat — 측정 PC
set FCC_CENTRAL_BASE_URL=http://<CENTRAL_IP>:8080
set FCC_CENTRAL_CHAMBER_ID=chamber-a
set FCC_CENTRAL_NODE_NAME=1번 챔버
set FCC_CENTRAL_NODE_BASE_URL=http://<NODE_IP>:9000
set FCC_CENTRAL_OIDC_TOKEN_URL=http://<CENTRAL_IP>:8081/realms/fcc-chamber-node/protocol/openid-connect/token
set FCC_CENTRAL_CLIENT_ID=fcc-chamber-node
set FCC_CENTRAL_CLIENT_SECRET=<FCC_CHAMBER_CLIENT_SECRET 와 동일한 값>
set FCC_CENTRAL_HEARTBEAT_INTERVAL_SECONDS=30

REM 측정 결과 동기화 (중앙 DB 직결)
REM ⚠ PROVIDER_ID 는 providers.id 의 UUID — S4(a) 에서 조회한 값 (자연키 문자열 아님)
set FCC_CENTRAL_DB_URL=postgresql://fcc:<POSTGRES_PASSWORD>@<CENTRAL_IP>:5432/fcc_central
set FCC_CENTRAL_PROVIDER_ID=<PROVIDER_UUID>
set FCC_CENTRAL_SYNC_POLL_INTERVAL_SECONDS=300

main_entry.exe
```

주의:

- `FCC_CENTRAL_CHAMBER_ID` 는 노드마다 **유일**해야 한다.
- `FCC_CENTRAL_BASE_URL` 이 비어 있으면 heartbeat 는 **완전 no-op** 이다 (조용히 꺼진다).
- `FCC_CENTRAL_DB_URL` 이 비어 있으면 측정 결과 동기화도 **조용히 꺼진다**. 측정은
  정상 동작하므로 "중앙에 안 올라온다" 는 증상만 나타난다 — S6 에서 이 두 개를 각각
  판정한다.
- ⚠ **`FCC_CENTRAL_PROVIDER_ID` 는 `providers.id` UUID 이며, 한 번 정하면 절대 바꾸지
  않는다.** 이 값은 두 곳에 동시에 쓰인다 — (1) 레코드의 `provider_id` FK 값,
  (2) 중앙 세션 uuid 파생 `uuid5(namespace, "{provider_id}:{로컬 세션번호}")`. 바꾸면
  같은 측정이 **다른 세션으로 중복 유입**된다. 모든 측정 PC 가 같은 값을 쓴다.

판정: 웹의 **시험 챔버** 메뉴에 노드가 보이고 온라인 표시. DB 로도 확인 가능
(등록은 `chamber_nodes`, 살아있음은 `chamber_heartbeat_events` 로 각각 본다):

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -c \
  "select chamber_id, name, base_url, enabled, heartbeat_ttl_seconds from chamber_nodes;"

docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -c \
  "select chamber_id, reported_status, occurred_at, expires_at
     from chamber_heartbeat_events order by occurred_at desc limit 5;"
```

`chamber_nodes` 에는 있는데 `chamber_heartbeat_events` 의 `occurred_at` 이 갱신되지
않으면 **등록만 되고 노드가 신호를 못 보내는 상태**다 (토큰 또는 중앙 8080 차단).

---

## S6 — 측정 1건 E2E (운영 검증의 종점)

> **포트 승인과 결과 동기화의 차이:** 현재 측정 PC 결과 동기화는
> `FCC_CENTRAL_DB_URL`로 중앙 `5432`에 직접 연결한다. 따라서 이 단계는 Session API
> 측정 경로(`8080/8081/9000`)와 결과 반영 경로(`5432`)를 분리해서 판정해야 한다.
> 보안 승인이 `8080/8081/9000`으로 제한된 동안에는 결과 반영을 PASS로 표시하지 않는다.

> **참고 — 이 단계를 막던 블로커는 해소됐다 (2026-07-29).**
>
> `measurement_results.session_id` · `measurement_attempts.session_id` 는
> `test_sessions(id)` 를 참조하는 FK 인데, 동기화 파이프라인에 **부모행을 만드는
> 코드가 없었다**. 개발/증거 경로(`scripts/dev_seed/central.py`,
> `platform_central_db_live_proof.py`)가 세션을 미리 심어 두어 드러나지 않았을 뿐,
> 실운영 빈 DB 에서는 첫 동기화가 FK 위반으로 실패했다.
>
> 이제 ingestion 이 `test_sessions` 를 **같은 트랜잭션에서 가장 먼저 upsert** 한다
> (`INGESTION_TABLE_ORDER[0]`). 세션 timestamp 는 마이그레이션 **011** 이, report parent의
> `created_at` 은 **012** 가 DB default 로 소유하므로 caller 가 stamp 하지 않는다. 따라서
> **S2 에서 012 까지 적용됐는지 확인**하는 것으로 충분하고, 부모행을 손으로 만들 필요는 없다.
>
> `report_runs` 도 운영자가 미리 만드는 행이 아니다. provider reference 행은 S4(a)에서
> 운영자가 등록하지만, report 실행이 실제 terminal status 와 output metadata 를 만든 뒤
> ingestion 이 같은 transaction 에서 `test_sessions` → `report_runs` → `report_outputs`
> 순서로 정확히 한 parent 를 upsert 한다. 출력 metadata 가 비어 있으면 parent 와 child
> 를 모두 만들지 않는다. `report_runs.created_at` 은 caller 가 넣지 않고 012의 `now()`가
> DB 에서 소유한다.

순서대로 수행하고 각 단계에서 멈춰 확인한다.

| # | 행위 | 판정 |
|---|---|---|
| 1 | 웹에서 프로젝트 생성 (관리번호·모델명) | `projects` 에 1행, 생성자에게 `project_admin` |
| 2 | 웹 시료 CRUD → 상태/이력 확인 → PM/RF export | `samples` / `sample_intakes` / `sample_inventory_revisions` 에 현재·불변 기록; `sample_import_runs` 는 새로 쓰지 않음 |
| 3 | 테스트 플랜 작성 → **발행** | `published_plan_expectation` 이 0 → N 으로 증가 (진행률 분모 생성) |
| 4 | 측정 PC 에서 해당 플랜으로 측정 1건 실행 | 로컬 `.fcc.db` 에 결과 + `result_outbox` 에 pending |
| 5 | 동기화 대기 (기본 300초) 또는 세션 종료 | `result_outbox` 의 pending 이 0 으로 감소 |
| 6 | 중앙 **측정 현황** 화면 | 방금 측정이 보인다 |
| 7 | 중앙 **진행률** 화면 | `%` 가 숫자로 표시된다 (3 을 안 했으면 "미설정" 문구) |

중앙 DB 직접 확인:

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -c \
  "select count(*) from measurement_attempts;
   select count(*) from measurement_results;
   select count(*) from published_plan_expectation;"
```

**세션 부모행 자동 생성 확인** — 사전 단계에서 측정 PC 코드를 갱신했는지 여기서 드러난다.
행이 없는데 측정 결과도 0 이면 측정 PC 가 옛 코드다:

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -c \
  "select provider_session_id, status, project_id is not null as has_project, created_at
     from test_sessions order by created_at desc limit 5;"
```

**Report parent/output 확인** — report output 이 실제로 생성된 경우에만
`report_runs` 가 ingestion transaction 에서 먼저 만들어져야 한다. provider 행은 S4(a)의
운영 reference data 이며, 이 parent 를 확인하기 위해 별도 SQL seed 를 실행하지 않는다.

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -c \
  "select rr.id, rr.provider_id, rr.session_id, rr.status, rr.created_at,
          count(ro.id) as output_count
     from report_runs rr
     left join report_outputs ro on ro.report_run_id = rr.id
    group by rr.id, rr.provider_id, rr.session_id, rr.status, rr.created_at
    order by rr.created_at desc limit 5;"
```

판정: 각 non-empty report batch 에 대해 parent 1행, expected output 수, 그리고 DB가
채운 `created_at` 이 보여야 한다. 같은 batch 를 재전송해도 parent/output 수가 늘지
않아야 하며, 출력이 없었던 실행은 두 테이블에 행을 만들지 않는다.

**프로젝트 귀속 확인** — 결과가 올라와도 프로젝트에 붙지 않으면 화면에서 안 보인다:

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central -c \
  "select count(*) filter (where project_id is null) as 미귀속,
          count(*) filter (where project_id is not null) as 귀속
     from measurement_results;"
```

귀속은 **로컬 측정 세션의 `project_code` → 중앙 `projects.project_code` 조회**로 이뤄진다.
로컬 세션에 project_code 가 없으면 `project_id` 가 NULL 로 들어오고, 값이 있는데 중앙에
없으면 동기화가 **loud error** 로 실패한다(조용한 NULL FK 를 만들지 않는 설계). 즉
**측정 전에 중앙에서 프로젝트를 먼저 만들고, 그 프로젝트의 발행 플랜으로 측정**해야 한다.

동기화가 안 올라올 때 확인 순서:

1. 측정 PC 에서 중앙 5432 도달 가능한가 (현재 임시 동기화 경로; 보안 승인 여부 확인).
2. `FCC_CENTRAL_DB_URL` 이 실제로 프로세스에 들어갔는가 (오타·따옴표).
3. 로컬 outbox 에 dead-letter 가 쌓였는가 — 재시도 상한 초과분은 `dead_letter` 로
   격리되고 스케줄러가 WARNING 을 남긴다.
4. 중앙 `platform-api` 로그의 ingestion 오류.

---

## 부록 A — 아직 화면이 없는 기능 (백엔드만 존재)

S3 워크스루에서 "왜 안 되지?" 로 시간을 쓰지 않도록 미리 적는다. 아래는 **API 는 있고
프론트 배선이 0건**인 항목이다 (2026-07-30 실측):

| 기능 | API | 현재 대안 |
|---|---|---|
| 프로젝트 메타 편집 (FCC ID·성적서 번호) | `PATCH /platform/projects/{id}` | curl 직접 호출 |
| 시료 CRUD·revision history | `GET/PATCH /platform/projects/{id}/samples/{sample_id}` + `/history` | 웹 inventory 화면에서 직접 수정·이력 확인 |
| 프로젝트 서버측 검색 | `GET /platform/projects?q=` | 화면 내 클라이언트 필터 |

**이미 화면이 있는 것** (없다고 오해하기 쉬움):

- **성적서**(`/test-reports`) — 2026-07-30 배선 완료. 목록/생성 + 자동 인용까지 화면에서 된다.
- **멤버십**(`/membership`) — 화면 존재. issuer 결함도 해소됨(S4(e)).

즉 **S6 의 측정 데이터 흐름과 성적서 단계 모두 화면으로 완주 가능**하고, 남은 공백은
프로젝트 메타 편집 1건이다. 시료 CRUD·history·필터·export와 프로젝트 서버측 검색은 웹 화면에서 제공한다.

## 부록 B — 증상별 1차 조치

| 증상 | 조치 |
|---|---|
| 다른 PC 에서 접속 자체가 안 됨 | 0단계 포트 + WSL networkingMode |
| 로그인 화면은 뜨는데 로그인 후 튕김/403 | S1 realm origin + `PUBLIC_HOST` 일치 |
| `502 Bad Gateway` | `ps` 로 API 컨테이너 상태 → 해당 API 로그 |
| `column ... does not exist` | 마이그레이션 부분 적용 — S2 (d) |
| 챔버가 목록에 없음 | S5(a) 등록 누락(자기등록 403 은 조용히 흡수됨) · `FCC_CENTRAL_BASE_URL` 미설정(no-op) · 중앙 8080 차단 |
| 동기화가 `invalid input syntax for type uuid` | `FCC_CENTRAL_PROVIDER_ID` 에 UUID 아닌 값 — S4 (a) |
| 동기화가 `test_sessions` FK 위반 | S6 블로커 — 부모행 미생성. S6 (0) 우회 또는 코드 정공 수정 |
| 멤버십 부여가 `unknown user_subject` 404 | issuer 불일치 — S4 (e) 의 `user_issuer` 명시 |
| 측정은 되는데 중앙에 없음 | `FCC_CENTRAL_DB_URL` 미설정(no-op) 또는 5432 차단 |
| 결과는 올라오는데 화면에 안 보임 | `measurement_results.project_id` NULL — 로컬 세션의 project_code 부재 (S6) |
| 동기화가 `session_id` FK 위반 | **측정 PC 가 옛 코드** — 사전 단계에서 측정 PC `git pull`(+ 필요 시 재빌드) |
| 같은 측정이 세션 2개로 중복 | `FCC_CENTRAL_PROVIDER_ID` 를 도중에 바꿈 (S5 ⚠) — 값을 되돌리고 중복 세션 정리 |
| provider 목록이 빈 화면 | `providers` 행 없음 — S4 (a) |
| 진행률이 `0%` 가 아니라 비어 있음 | 정상 — 플랜 미발행/시간 미설정 |
| 같은 사람이 `users` 에 2행(issuer 다름) | 2026-07-29 이전에 만들어진 중복. 로그인 계정이 쓰는 행(실제 OIDC issuer)에 권한을 부여하고, 남은 legacy 행은 그대로 두어도 무해 |

## 부록 C — 롤백

S2 (a) 의 백업으로 되돌린다:

```bash
docker compose -f infra/docker-compose.central.yml down
docker volume rm fcc-central_central-pgdata
docker compose -f infra/docker-compose.central.yml --env-file infra/central/central.env up -d postgres
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  psql -U fcc -d fcc_central < ~/fcc_central_backup_<타임스탬프>.sql
```

---

## 관련 문서

- 일상 점검: [`central-pc-fcc-platform-verification-guide.md`](./central-pc-fcc-platform-verification-guide.md)
- 설치·구성 SSOT: [`ONPREM_DEPLOYMENT.md`](../../infra/central/ONPREM_DEPLOYMENT.md)
- 챔버 토큰·권한: [`chamber-token-rbac-runbook.md`](./chamber-token-rbac-runbook.md)
- 챔버 실측 스테이징: [`chamber-real-measurement-staging-runbook.md`](./chamber-real-measurement-staging-runbook.md)
