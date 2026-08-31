# `infra/` — Local-only platform dependencies

본 디렉토리는 **개발 환경 전용** docker-compose / 설정 import 파일을 모은다.
운영 환경 IdP / DB / 모니터링은 별도 배포 아티팩트로 관리되며 본 디렉토리에 들어가지 않는다.

## OIDC IdP — Keycloak mock (Sprint S2)

### 부팅

```bash
# (repo root 에서)
docker compose -f infra/docker-compose.idp.yml up -d
```

첫 부팅 시 약 30~60초 소요. 헬스체크 통과 후 콘솔 진입:

- Admin: <http://localhost:8081/> (id: `admin`, pw: `admin`)
- Realm: `fcc-dev` (자동 import)
- Client: `fcc-platform-frontend` (public PKCE — secret 부재)
- Discovery: <http://localhost:8081/realms/fcc-dev/.well-known/openid-configuration>

### 테스트 계정

| user | pw | roles | permissions |
|------|----|-------|-------------|
| viewer | viewer | viewer | `session:read`, `session:events` |
| operator | operator | viewer, operator | `+ session:control` |
| admin | admin | viewer, operator, admin | `+ platform:admin` |

### Frontend dev 설정

`apps/web/public/runtime-config.dev.json` 이 dev origin SSOT다. 기본값은
Vite dev gateway와 Keycloak을 모두 localhost로 둔다.

```text
apiBaseUrl: http://localhost:5173
wsBaseUrl: ws://localhost:5173
oidcIssuer: http://localhost:8081/realms/fcc-dev
oidcRedirectUri: http://localhost:5173/auth/callback
```

`apps/web/public/runtime-config.js` 는 생성물이다. 직접 편집하지 말고
`cd apps/web && npm run dev` 또는 `node scripts/write-dev-runtime-config.mjs`로
재생성한다. 비인증 상태에서 보호된 라우트 진입 시 Keycloak login 페이지로 redirect된다.

### 종료 / 정리

```bash
docker compose -f infra/docker-compose.idp.yml down            # 컨테이너만 종료 (DB volume 유지)
docker compose -f infra/docker-compose.idp.yml down -v        # volume 까지 삭제 (realm import 다시 실행됨)
```

### CI (Sprint S9)

Sprint S9 가 `.github/workflows/frontend-e2e.yml` 에 본 compose 를 `services:` 블록으로 등록한다. 그 전까지는 dev 머신에서 수동 부팅.

## 중앙 허브 단일 스택 — `docker-compose.central.yml` (B1/P13)

중앙 허브 **전체**(Platform API :8002 + Headless API :8001 + 웹 nginx 정적
:8080 + PostgreSQL :5432 + Keycloak :8081)를 단일 compose 로 묶는다.
`docker-compose.idp.yml` 의 Keycloak 패턴을 확장한 형태.

> **챔버 노드는 컨테이너화 제외** — GPIB/USB 장비 접근 + Nuitka Windows `.exe`
> 의존 때문에 네이티브로 남아 이 중앙 허브에 네트워크로 접속한다. 중앙
> 컨테이너 경로에 win32 의존(winsound / thread_sampler `GetThreadTimes` /
> appium subprocess)이 없음은 `tests/test_central_docker_compose.py` 가 봉인.

### 부팅

> **개발/운영 분리** — 개발(코드 작업, localhost)은
> [`central/LOCAL_DEVELOPMENT.md`](central/LOCAL_DEVELOPMENT.md)(Docker Desktop vs
> WSL2 네이티브 선택 + 라이선스/충돌 이력 포함), 운영 상시 가동(중앙 PC, LAN IP)은
> [`central/ONPREM_DEPLOYMENT.md`](central/ONPREM_DEPLOYMENT.md). 아래는 빠른 부팅.

```bash
# (repo root 에서)
cp infra/central/central.env.example infra/central/central.env   # 포트/크리덴셜 SSOT
docker compose -f infra/docker-compose.central.yml \
    --env-file infra/central/central.env up -d --build
```

- Web SPA + API gateway: <http://localhost:8080/> — 브라우저는 **이 한 origin** 만
  사용한다. nginx 가 `/headless/*` → headless-api, `/platform/*` → platform-api 로
  reverse-proxy (same-origin → API 에 CORS 불필요).
- Headless API: <http://127.0.0.1:8001/> (개발·진단용 직접 포트. 운영 브라우저와
  챔버는 `:8080` Gateway 경유. auth-exempt liveness: `/headless/metrics`)
- Platform API: <http://127.0.0.1:8002/> (개발·진단용 직접 포트. 운영 챔버 heartbeat와
  등록은 `http://<CENTRAL_IP>:8080/platform/...` 경유. auth-exempt liveness:
  `/platform/metrics`)
- Keycloak: <http://localhost:8081/> · PostgreSQL: `127.0.0.1:5432`

### 구성 SSOT

- **포트/크리덴셜/엔드포인트**: `infra/central/central.env.example` 단일 SSOT.
  compose YAML 은 `${VAR:-default}` 만 참조 — 하드코딩 0. `FCC_*` 환경변수명은
  Python runtime-config SSOT(`application/{headless,platform}/runtime_config.py`,
  `application/headless/central_db_config.py`)와 일치하며 drift 는 테스트가 봉인.
- **DB DSN 단일소스(크리덴셜 rotation-safe)**: DB 크리덴셜은 `POSTGRES_USER`/
  `POSTGRES_PASSWORD`/`POSTGRES_DB` **한 곳**에만 산다. platform-api 의
  `FCC_CENTRAL_DB_URL` 은 compose 가 그 값들에서 **조립**(flat interpolation
  `postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}`)
  하므로 env 파일에 literal DSN 을 두지 않는다 — 크리덴셜을 교체해도 DSN 이
  stale 될 수 없다. 외부 관리형 Postgres 는 platform-api environment 에
  `FCC_CENTRAL_DB_URL` 을 직접 지정해 override.
- **중앙 DB 스키마**: `docs/platform/migrations/001_initial_central_db.sql` 을
  postgres `docker-entrypoint-initdb.d` 로 첫 부팅 시 적용
  (`docs/platform/central_db_schema.v1.json` 에서 생성된 산출물 — 직접 편집 금지).
- **브라우저 통합 — same-origin 게이트웨이(CORS 불필요)**: 웹 컨테이너 nginx 는
  정적 서버이자 브라우저의 **same-origin API 게이트웨이**다. 브라우저는 오직
  `PUBLIC_HOST:WEB_PORT`(:8080) 한 origin 만 호출하고, nginx 가 `/headless/*` →
  `headless-api:8001`, `/platform/*` → `platform-api:8002` 로 reverse-proxy 한다.
  모든 API/WS 호출이 same-origin 이므로 FastAPI surface 에 **CORS middleware 가
  필요 없다**(현재 없음). dev 토폴로지(`apps/web` vite proxy +
  `dev-stack.config.json`)와 동일한 path-routed 구조. `Authorization: Bearer`
  헤더는 그대로 전달돼 백엔드 OIDC resolver 가 검증.
- **웹 런타임 설정(포트 SSOT 에서 파생)**: 웹 SPA 엔드포인트는
  `runtime-config.central.js.template` 에서 컨테이너 시작 시 `envsubst` 로
  **생성**된다(`docker-entrypoint.d/30-runtime-config.sh`). `apiBaseUrl`/`wsBaseUrl`
  은 게이트웨이 origin `${PUBLIC_HOST}:${WEB_PORT}`(API 포트가 아님),
  `platformApiBaseUrl` 은 `null`(= apiBaseUrl 재사용). 템플릿엔 host:port literal
  이 없고 `${...}` placeholder 만 있어 env 포트를 바꾸면 SPA 엔드포인트도 함께
  바뀐다 — 편집할 별도 JS 가 없어 drift 원천 차단(옛 하드코딩
  `runtime-config.central.js` 제거).
- **OIDC issuer split-horizon**: 토큰의 `iss` 는 **브라우저가 토큰을 받은**
  issuer(`${PUBLIC_HOST}:${KEYCLOAK_PORT}`)다. 따라서 API 의 issuer 검증값
  (`FCC_{HEADLESS,PLATFORM}_OIDC_ISSUER`)도 그 브라우저-대면 issuer 여야 하고
  (내부 `keycloak:8080` 이름이 아님), 서명키는 컨테이너 네트워크 내부
  JWKS URL(`FCC_{HEADLESS,PLATFORM}_OIDC_JWKS_URI=http://keycloak:8080/.../certs`)
  에서 받는다. 둘 다 compose 가 포트 SSOT 에서 조립 → SPA `oidcIssuer` 와 단일
  소스. Keycloak realm(`infra/keycloak/fcc-dev-realm.json`)은 중앙 web origin
  (`localhost:8080`)을 redirectUri/webOrigins 에 정적 허용한다(dev `:5173` 과
  공존). PUBLIC_HOST/WEB_PORT 를 바꾸면 realm 에도 해당 origin 추가 필요
  (realm import 는 정적 — envsubst 불가).
  - **realm import JSON 에 주석(`_comment*`) 금지** — Keycloak 25 의
    `--import-realm` 은 `RealmRepresentation` 에 없는 알 수 없는 필드를 거부해
    컨테이너가 exit(1) 한다(`_comment_lifespans`/`_comment_origins`/protocol
    mapper `_comment` 가 그 원인이었다). 문서는 본 README / 테스트 docstring 에
    두고 import payload 에는 넣지 않는다. 봉인:
    `tests/test_central_docker_compose.py::TestCentralKeycloakRealmImportable`.
    - `accessTokenLifespan`(10분)은 dev 에서 frontend silent-refresh 경로가
      자주 돌도록 의도적으로 짧다(`apps/web/src/auth/session.ts` 의
      `MIN_REFRESH_MARGIN_SECONDS` 참조). prod realm 은 상향.
    - `fcc-permissions-mapper` 는 user/group 속성에서 `permissions` 클레임을
      방출한다(백엔드 `HttpAuthConfig.oidc_permissions_claim` 기본값 미러).
- **OIDC audience 는 client id SSOT 에서 파생**: `oidc_jwt` 검증은
  issuer + JWKS 외에 audience(access token `aud`)도 요구한다. compose 는 두 API
  의 `FCC_{HEADLESS,PLATFORM}_OIDC_AUDIENCE` 를 브라우저 런타임과 동일한
  `OIDC_CLIENT_ID` SSOT(`${OIDC_CLIENT_ID:-fcc-platform-frontend}`)에서 파생해
  audience 값이 중복·drift 되지 않는다(미설정 시 create_app 이 "oidc_jwt auth
  requires issuer, audience, and jwks_uri" 로 fail-fast).
- **headless 데이터 볼륨 쓰기 권한**: headless API 는 `/data/headless` 아래
  SQLite DB 를 쓴다(`central-headless-data` named volume). 컨테이너는 비-root
  `appuser`(uid 10001)로 돈다. `Dockerfile.api` 가 볼륨 마운트 전에
  `/data/headless` 를 appuser 소유로 미리 생성 → Docker 가 빈 named volume 을
  이미지 디렉터리(내용+소유권)에서 초기화하므로 root 로 돌리지 않고도 쓰기
  가능(미적용 시 `sqlite3.OperationalError: unable to open database file`).
- **이미지**: 중앙 API 는 단일 이미지(`infra/central/Dockerfile.api`,
  `requirements-central.txt` lean SSOT = `requirements-web.txt` 에서 데스크톱/
  장비 패키지 제외)로 두 ASGI factory 를 command 로 분기. 웹은 멀티스테이지
  (`Dockerfile.web` — Vite 빌드 → nginx 정적 + 게이트웨이).

### 종료 / 정리

```bash
docker compose -f infra/docker-compose.central.yml down       # 컨테이너만
docker compose -f infra/docker-compose.central.yml down -v    # volume 까지(DB 초기화)
```

## 향후 추가될 자산

- Sprint S7: signed-url Storage mock (`docker-compose.storage.yml` — MinIO)
- Sprint S8: OTel collector backend (`docker-compose.otel.yml`)

각 compose 파일은 본 README 에 부팅/진입/정리 명령을 추가한다.
