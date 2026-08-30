# 중앙 허브 상시 운영 배포 — 중앙 PC (WSL)

이 문서는 FCC 중앙 허브(`infra/docker-compose.central.yml`)를 **중앙 PC의 WSL
Ubuntu**에서 **상시 가동**으로 올리는 절차입니다.

> 회사가 승인한 온프레미스 Linux 서버/VM이 가능해지면 그쪽이 권장 경로입니다.
> 중앙 허브 이미지는 win32-free임이 봉인돼 있어(`tests/test_central_docker_compose.py`)
> Linux 서버로 그대로 이전 가능합니다 — 장비/`.exe`에 묶인 것은 챔버 노드뿐입니다.
> 현재는 그 서버가 불가하여 중앙 PC(WSL)를 운영 위치로 사용합니다.
>
> **개발(코드 작업)은 이 문서가 아니라 `infra/central/LOCAL_DEVELOPMENT.md`** —
> 개발 PC localhost(`PUBLIC_HOST=localhost`)에서 같은 스택을 띄웁니다. 운영은 이 문서.

## 배포 모델

```
       사내 LAN (중앙 PC 고정 IP = <CENTRAL_IP>)
                       │
  ┌────────────────────┴─────────────────────────┐
  │  중앙 PC  (WSL Ubuntu + Docker)                 │
  │  docker-compose.central.yml — restart: 상시     │
  │                                                 │
  │  app-network ─ web(:8080 게이트웨이)             │
  │              ├ keycloak(:8081)                  │
  │              ├ headless-api(:8001)              │
  │              └ platform-api ─┐                  │
  │  data-network(internal) ─ postgres ◀┘           │
  └────────────────────┬─────────────────────────┘
                       │  LAN
        ┌──────────────┼───────────────┐
    챔버 노드 PC      챔버 노드 PC      시험원 브라우저
    (네이티브 .exe)                    http://<CENTRAL_IP>:8080
```

- **진입점**: 브라우저는 `http://<CENTRAL_IP>:8080` 한 origin만 사용(nginx가
  `/headless`·`/platform`을 API 컨테이너로 reverse-proxy → CORS 불필요).
- **DB 격리**: postgres는 `data-network`(`internal: true`)에 있어 컨테이너 간에는
  platform-api만 다리 역할을 한다. 현재 운영 compose는 중앙 PC의 시드/백업과 기존
  결과 동기화 호환을 위해 `POSTGRES_PORT`를 호스트에 publish하므로, 보안 승인 범위를
  `8080/8081`로 제한하는 최종 전환에서는 이 host binding을 별도로 잠가야 한다.

## 1. 사전 준비

```bash
wsl.exe -d Ubuntu bash -lc 'docker compose version && docker version'
```

repo가 Windows `C:\FCC_mobile_test_automation`에 있으면 WSL에서는 `/mnt/c/...`.
손상된 `~/.docker/config.json`(BOM) 우회를 위해 `DOCKER_CONFIG=/tmp/...`를 선두에
지정합니다(중앙 PC도 처음엔 같은 함정을 겪을 수 있음).

## 2. env 작성 — 운영값

```bash
cd /mnt/c/FCC_mobile_test_automation
cp infra/central/central.env.example infra/central/central.env
```

`infra/central/central.env`에서 **반드시** 바꿀 값:

| 키 | 운영값 |
|----|--------|
| `PUBLIC_HOST` | 중앙 PC의 **고정 LAN IP** (예: `172.30.1.10`) |
| `POSTGRES_PASSWORD` | 실제 시크릿 (데모 `fcc-dev-password` 폐기) |
| `KEYCLOAK_ADMIN_PASSWORD` | 실제 시크릿 (데모 `admin` 폐기) |
| `FCC_CHAMBER_CLIENT_SECRET` | 실제 시크릿 (챔버 노드 머신 토큰) |
| `FCC_STAGING_CLI_SECRET` | 실제 시크릿 (사람 직접-grant 클라이언트) |
| `FCC_CENTRAL_CLIENT_RANGES` | 시험원·챔버 PC 가 사는 **사내망 대역** (예: `10.206.0.0/16`) — 아래 §2-bis |

### 2-bis. peer 축 신뢰 hop — 이 값이 없으면 사용량 한도가 "전원 합쳐서" 다

서버가 **누가 요청했는지**를 알려면, nginx 뒤의 uvicorn 이 실 클라이언트 주소를 복원해야
한다. 그 복원은 `FORWARDED_ALLOW_IPS` 에 적힌 **한 주소**에서 온 요청에 대해서만 일어나고,
compose 가 그 값을 `web`(nginx) 컨테이너의 정적 주소에서 파생해 두 API 에 넘긴다.

**보통은 아무것도 설정할 필요가 없다.** 기본값이 이미 그 배선이다. 손대야 하는 경우는 둘:

| 상황 | 조치 |
|---|---|
| `docker compose up` 이 `Pool overlaps with other one on this address space` 로 실패 | 이 호스트의 주소 풀이 `172.31.240.0/24` 를 이미 쓴다. `CENTRAL_APP_SUBNET` 과 `CENTRAL_PROXY_IP` 를 **함께** 다른 대역으로 옮긴다(주소는 대역 안, `.1`(게이트웨이)·네트워크·브로드캐스트 주소가 아니어야 한다) |
| 사내망 대역을 알고 있다 | `FCC_CENTRAL_CLIENT_RANGES` 에 적는다. 그러면 신뢰 대상이 실 클라이언트를 삼키는 오설정이 **부팅에서 거부**된다 — 그 오설정의 증상은 오류가 아니라 *"공격자가 헤더에 적은 값이 그대로 신원이 되는 것"* 이라 조용하다 |

겹치는 대역을 찾는 명령(어느 네트워크가 범인인지까지 알려준다):

```bash
docker network ls -q | xargs -r docker network inspect \
  -f '{{.Name}} {{json .IPAM.Config}}'
```

**부팅 후 반드시 확인한다** — 한 줄이 어느 상태인지 말한다:

```bash
docker logs fcc-central-platform-api 2>&1 | grep -i 'proxy trust\|FORWARDED_ALLOW_IPS'
```

* `proxy trust configured: … (peer-axis mode: per-source)` → 서버가 실 출처를 본다.
* ⚠️ `proxy trust configured: … (peer-axis mode: deployment-wide)` → **값은 있는데 배선은
  안 됐다.** 루프백만 적힌 경우(`FORWARDED_ALLOW_IPS=127.0.0.1`)가 여기다 — 그것은 uvicorn 의
  기본값이라 «설정했다» 가 아니다. **앞부분(`proxy trust configured`)만 보고 판단하지 마라.
  괄호 안의 mode 가 판정이다.**
* `FORWARDED_ALLOW_IPS is unset … per DEPLOYMENT, not per caller` → 배선이 빠졌다.
* 컨테이너가 아예 안 뜨고 `refusing to start: unsafe proxy trust configuration` →
  **고장이 아니라 설계다.** 거부 사유가 어느 값이 왜 위험한지 이름으로 적혀 있다.

⚠️ **`per-source` 는 "서버가 복원할 준비가 됐다" 이지 "시험원마다 버킷이 다르다" 가 아니다.**
중앙 PC 가 WSL 이고 LAN 클라이언트가 Windows 쪽 릴레이(portproxy 등)를 거쳐 들어오면, nginx 가
보는 주소는 **그 릴레이 하나**가 되어 전원이 다시 한 버킷을 공유한다. 실제로 그런지는 관측으로
확인한다 — 서로 다른 두 시험원 PC 에서 접속한 뒤:

```bash
docker logs --tail 200 fcc-central-web 2>&1 \
  | grep -oE '^([0-9]{1,3}\.){3}[0-9]{1,3}' | sort -u
```

⚠️ **`docker exec … tail /var/log/nginx/access.log` 를 쓰지 마라 — 그 명령은 끝나지
않는다.** 이 이미지(`nginx:alpine`)에서 그 경로는 `/dev/stdout` 으로의 **심볼릭 링크**라
`tail` 이 영원히 대기한다(적대 평가 실측 2026-08-23: 120초 후 강제 종료). nginx 의
접근 로그는 컨테이너 stdout 으로 나가므로 읽는 곳은 `docker logs` 다.

⚠️ **`awk '{print $1}'` 를 쓰지 마라 — 주소가 아닌 것을 주소로 센다**(적대 평가 3R
실측). 이 컨테이너의 stdout 에는 접근 로그만 나오는 것이 아니라 nginx **에러 로그**
(첫 필드가 `2026/08/22` 같은 날짜)와 엔트리포인트 줄(`/docker-entrypoint.sh:`)도 섞인다.
실측: 시험원이 **한 명뿐인**(=이 확인이 검출하려는 붕괴 상태) 배포에서 그 명령은
`172.31.240.150` 과 `2026/08/22` **두 줄**을 뱉었고, 운영자는 규칙대로 «둘 이상이니
보존된다» 고 읽는다 — 같은 순간 아래 게이지는 `observed-single` 이라고 답했다.
한 런북 안의 두 진단이 같은 배포 상태에 **반대 결론**을 주면 안 된다. 위 `grep -oE`
는 첫 필드가 실제로 IPv4 주소인 줄만 센다.

주소가 **둘 이상** 나오면 경로가 출처를 보존하는 것이고, **하나뿐**이면 보존하지 않는 것이다
(흥미로운 답은 «둘 이상» 이 아니라 **«둘 미만»** 이다). 후자라면 이 축은 아직 배포에서
효력이 없다(장부 등재 항목).

#### 2-bis-1. 로그를 뒤지지 않고 묻는 법 — 배포가 스스로 답한다 (2026-08-23)

위 절차는 **한 번 해 보고 잊는** 확인이다. 그 사이 토폴로지가 바뀌면(상위 프록시 신설,
릴레이 추가) 아무도 다시 보지 않는다. 그래서 각 API 프로세스가 **자기가 실제로 본 출처의
가짓수**를 게이지로 답한다 — 배선(`FORWARDED_ALLOW_IPS`)이 아니라 **관측**이다.

```bash
curl -s http://<CENTRAL_IP>:8080/platform/metrics | grep peer_axis
curl -s http://<CENTRAL_IP>:8080/headless/metrics | grep peer_axis
```

⚠️ **두 표면을 모두 읽고, 약한 쪽을 배포의 답으로 삼아라**(적대 평가 3R 실측).
판정은 **프로세스 로컬**이라 두 API 가 같은 배포에서 다른 답을 낸다 — 실측: 같은 순간
platform 은 `observed-distinct`, headless 는 `unobserved` 였다. 한쪽만 읽고 «배포가
출처를 보존한다» 고 결론 내면, 그것은 그 프로세스에 대한 사실이지 배포에 대한 사실이
아니다. 한 API 만 롤링 재기동해도 둘은 갈라진다.

| 게이지 | 뜻 |
|---|---|
| `fcc_platform_peer_axis_sources_observed` | 이 프로세스가 본 서로 다른 출처 — `0` / `1` / `2`(= **둘 이상**) |
| `fcc_platform_peer_axis_verdict{verdict="…"}` | 지금 성립하는 판정에 `1`, 나머지에 `0` |
| `fcc_platform_peer_axis_samples_total` | 그 판정이 **몇 요청**에서 나왔나 (3 에서 나온 판정과 30,000 에서 나온 판정은 다르다) |
| `fcc_platform_peer_axis_observation_age_seconds` | 그 판정이 **얼마나 오래됐나**. 창이 없으므로 이 값이 신선도의 전부다. **스크레이프 시점에 계산**되므로 배포가 놀고 있어도 계속 증가한다 |
| `fcc_platform_peer_axis_unattributable_samples_total` | 선언된 대역 **밖**에서 온 caller 요청 수. `sources_observed` 가 `0` 인데 이 값이 크면 «아직 아무것도 안 왔다» 가 아니라 **«오는데 귀속이 안 된다»** 다 |

판정 여섯과 **할 일**:

| 판정 | 뜻 | 조치 |
|---|---|---|
| `deployment-wide` | 루프백 밖의 신뢰 hop 이 없다 | **배선을 먼저 고친다**(§2-bis 위쪽). 관측은 그 전까지 도움이 안 된다 |
| `undeclared-clients` | 신뢰 hop 은 있는데 **시험원이 어디 사는지 아무도 선언하지 않았다** | `FCC_CENTRAL_CLIENT_RANGES` 에 사내망 대역을 적는다. 그 전까지 이 축은 아무것도 «증명» 이라 부르지 않는다 |
| `unobserved` | 요청 자체가 아직 없다 | 재기동 직후의 **정상 상태**. 시험원이 일하기 시작한 뒤 다시 본다 |
| `sources-unattributable` | 요청은 **오고 있는데** 프록시 경로의 시험원으로 귀속되는 것이 하나도 없다 | ⚠️ **«정상» 으로 읽지 마라.** 다섯 중 하나다: (1) 선언한 대역이 틀렸거나 빠졌다, (2) **신뢰 hop 앞에 프록시가 하나 더 있어** 전원이 그 주소로 접힌다(= 이 축이 검출하려는 그 붕괴), (3) 트래픽이 프록시가 아니라 **게시 포트로 직접** 온다, (4) `FORWARDED_ALLOW_IPS` 가 **실제 프록시 주소와 어긋나** 복원이 일어나지 않는다, (5) 프록시가 `host:port` 형태로 전달해 이 빌드가 복원을 인식하지 못한다. 게이트웨이 접근 로그의 주소와 선언 대역을 대조해 가른다. ⚠️ 재기동 **직후** 시험원이 아직 아무도 접속하지 않았을 때 인프라 probe 하나로도 이 값이 뜰 수 있다 — 그때는 일단 시험원이 일하기 시작한 뒤 다시 봐라 |
| `observed-single` | 출처를 **하나만** 봤다 | ⚠️ **붕괴로 읽지 마라.** 이것은 *혼자 일하는 시험원 한 명*과 **아직** 구분되지 않는다. **두 번째 시험원 PC 로 접속한 뒤 다시 읽어라** |
| `observed-distinct` | 서로 다른 출처를 **둘 이상** 봤다 | 없음 — 경로가 출처를 보존한다는 **증명**이다 |

⚠️ **세는 대상은 «신뢰 hop 을 지나 주소가 복원된» 요청뿐이다**(적대 평가 3R 실측).
게시된 API 포트(`8001`/`8002`)는 LAN 에 열려 있고 **직접 닿는 호스트들이 사는 곳이
바로 선언된 대역**이다 — 포트를 잘못 친 시험원 브라우저, 챔버 노드 PC, LAN 스캐너,
모니터링 에이전트. 실측: 리버스 프록시가 **한 번도 뜬 적 없는** 스택에서 선언 대역의
두 호스트가 보낸 **인증도 안 된 403 두 개**로 판정이 `observed-distinct` 가 됐다.
그래서 관측은 서버가 실제로 주소를 복원한 요청만 센다. **부작용**: 프록시를 우회하는
트래픽만 있는 배포는 `observed-distinct` 가 아니라 `sources-unattributable` 로 답한다 —
그것이 정확히 맞는 답이다.

⚠️ **그리고 «세지 않는다» 가 «기록하지 않는다» 가 되면 안 된다.** 첫 판이 그 구분을
놓쳐, 복원되지 않은 요청을 관측기에 **넘기지도 않았다** — 그러면 판정은 다시
`unobserved` 가 되고 이 표는 «재기동 직후의 정상» 이라고 말한다. 실측된 재현:
`FORWARDED_ALLOW_IPS` 가 실제 프록시와 어긋난 배포에서 시험원 A 의 요청 50건이
시험원 B 를 **B 의 첫 요청에서 429** 시켰고(= 두 사람이 한 버킷을 공유한다는 증명이
그 자리에 있었는데) 게이지는 `unobserved` 였다. 지금은 그런 요청이
`unattributable_samples_total` 로 세어지고 판정은 `sources-unattributable` 이다.

⚠️ **`observed-single` 을 «릴레이가 접었다» 로 읽으면 안 되는 이유**: 그 둘을 가르는
임계값(*"N 요청 이상인데 출처가 하나면 붕괴"*)은 **존재하지 않는다** — 한 사람이 하루
종일 쓰면 어떤 N 도 넘는다. 그래서 이 판정의 이름이 *붕괴*가 아니라 **아직 모른다** 이고,
답을 얻는 방법은 관측을 더 오래 하는 것이 아니라 **두 번째 PC 를 붙이는 것**이다.

⚠️ **판정은 프로세스 재기동 때 초기화된다.** 창이 없으므로 *"둘 이상 봤다"* 는 그
프로세스가 사는 동안 유지된다 — 부팅 이후 토폴로지가 바뀌어도 다시 묻지 않는다.
그래서 **재기동**이 재관측의 계기이고, 그 사이 판정이 얼마나 오래됐는지는 위
`observation_age_seconds` 가 답한다. 배포를 바꿨다면 스택을 다시 올린 뒤 읽어라.

⚠️ **다중 워커에서는 워커마다 자기가 본 것만 답한다.** 시험원 트래픽은 워커들로
나뉘므로 각자가 보는 다양성은 *줄어든다*. 오늘 배포는 컨테이너당 워커 1이다.

⚠️ **이 게이지는 «선언된 시험원 대역에서 온 요청» 만 센다.** 두 API 포트는 LAN 에 게시돼
있으므로 nginx 를 **거치지 않고** 직접 닿는 요청이 존재한다 — 이웃 컨테이너, 브리지
게이트웨이, LAN 스캐너, 포트를 잘못 친 운영자. 그런 요청도 «서로 다른 출처» 이므로,
전원이 릴레이로 접힌 배포에서 **요청 하나**(404 여도 무관 — 관측은 인가 앞이다)가 판정을
`observed-distinct` 로 올렸다(적대 평가 실측 2026-08-23). 코드가 그것을 가릴 방법은
하나뿐이다 — **운영자가 시험원이 사는 대역을 선언하는 것**. 그래서 선언이 없으면 판정은
`undeclared-clients` 이고, 그 상태에서는 어떤 관측도 «증명» 이 되지 않는다.

⚠️ **이 게이지는 배포 자신의 트래픽도 세지 않는다.** 컨테이너 healthcheck 는 nginx 를
지나지 않고 컨테이너 **안에서** 루프백으로 15초마다 들어오고, 운영자의 `/metrics` 스크레이프
(= 바로 위 명령)도 요청이다. 그 둘을 세면 붕괴한 배포가 부팅 15초 뒤부터 영원히
`observed-distinct` 로 보인다 — 즉 **질문하는 행위가 답을 바꾼다**. 그래서 루프백 출처와
probe·metrics 경로는 관측에서 제외한다. 제외의 방향은 «다양성 주장을 약화시키는 쪽» 뿐이므로
잘못 제외해도 답은 *"아직 모른다"* 로 떨어질 뿐 «증명됐다» 로 올라가지 않는다.

⚠️ **그러므로 `observed-distinct` 가 나오려면 서로 다른 두 «실 클라이언트»가 실제로 업무
API 를 써야 한다.** 스크레이프만 반복해서는 절대 그 답이 나오지 않는다.

#### 2-bis-2. 현재 하네스 현장 증거 상태 (2026-08-24)

이번 identity-deployment-gates 실행의 raw field curl 종료 코드는 저장소 안에 보존한다:

```text
.claude/evidence/identity-deployment-gates-20260824/platform.exit  = 7
.claude/evidence/identity-deployment-gates-20260824/headless.exit  = 7
```

두 endpoint 연결이 성립하지 않아 `platform.metrics.txt`, `headless.metrics.txt`,
`field-verdict.md`는 생성하지 않았다. 따라서 이 실행은 어느 API도
`observed-distinct`라고 판정하지 않으며, `source_rotation_budget` 호출자·production
source cap을 추가하지 않는다. 다음 측정에서도 두 표면을 모두 읽고 두 exit가 0이며
둘 다 `observed-distinct`일 때만 Phase 4를 연다. 그 전까지는 이 문서의 여섯 판정별
조치와 blocker를 그대로 적용한다.

> `central.env`는 시크릿을 담으므로 커밋하지 않습니다(`infra/central/central.env`는
> gitignore 대상). 시크릿 암호화(sops 등)는 향후 하드닝 후보입니다.

## 3. Keycloak realm에 LAN origin 추가 ⚠

`PUBLIC_HOST`를 LAN IP로 바꾸면, realm import는 **정적**이라 envsubst가 적용되지
않습니다. `infra/keycloak/fcc-dev-realm.json`의 `fcc-platform-frontend` 클라이언트에
중앙 PC origin을 **추가**해야 브라우저 OIDC 로그인이 됩니다(기존 `localhost:8080`,
`127.0.0.1:8080`, dev `:5173`은 그대로 둠 — additive):

- `redirectUris`: `http://<CENTRAL_IP>:8080/auth/callback` 추가
- `webOrigins`: `http://<CENTRAL_IP>:8080` 추가
- `attributes."post.logout.redirect.uris"`: `http://<CENTRAL_IP>:8080/*` 추가(`##` 구분)

추가 후 keycloak 컨테이너를 recreate하면 realm이 다시 import됩니다.

## 4. 부팅 (상시 가동)

```bash
wsl.exe -d Ubuntu bash -lc 'cd /mnt/c/FCC_mobile_test_automation && \
  DOCKER_CONFIG=/tmp/fcc-docker-config \
  docker compose -f infra/docker-compose.central.yml \
    --env-file infra/central/central.env up -d --build'
```

모든 서비스가 `restart: unless-stopped`라 컨테이너 크래시·docker 데몬 재시작 시
자동 복구됩니다. 상태 확인:

```bash
docker compose -f infra/docker-compose.central.yml ps
```

> 이 WSL 셋업에서는 `docker inspect -f '{{.State.Health.Status}}'`가 빈 값을
> 반환할 수 있으니 `ps`의 STATUS 열을 신뢰하세요.

### 중앙 PC 재부팅 시 자동 기동

`restart: unless-stopped`는 docker 데몬이 떠 있을 때만 동작합니다. 중앙 PC 재부팅
후에도 스택이 뜨려면 **docker 데몬이 부팅 시 시작**되어야 합니다:

- Docker Desktop 사용 시: Settings → "Start Docker Desktop when you log in".
- WSL 네이티브 docker 사용 시: WSL 배포가 부팅 시 기동되고(`wsl --set-default`,
  작업 스케줄러 또는 `.wslconfig`), 그 안에서 dockerd가 자동 시작되도록 설정.

부팅 후 `docker compose ... ps`로 5개 서비스가 떠 있는지 확인합니다.

## 5. 운영 시드 (진행률 catalog)

진행률(progress) catalog는 자동 적용되지 않습니다. 중앙 DSN + provider-id로 1회:

```bash
python3 scripts/seed_standard_time_catalog.py \
  --workbook <워크북 경로> \
  --provider-id "$FCC_CENTRAL_PROVIDER_ID" \
  --db-url "postgresql://<user>:<pw>@<CENTRAL_IP>:5432/<db>" \
  --apply
```

⚠️ **`--workbook` 은 선택이 아니라 필수다** — 옛 문언은 그것을 적지 않아 그대로
따라 치면 `error: the following arguments are required: --workbook` 이다(적대 평가
3R 실측). 그리고 **`python` 이 아니라 `python3`** 이다: 이 배포가 도는 Ubuntu 계열은
`python` 이름을 제공하지 않는다(`bash: python: command not found`).

## 6. 백업 (pg_dump)

중앙 DB는 named volume(`central-pgdata`)에 영속됩니다(컨테이너 재생성에도 보존).
호스트에서 백업은 `pg_dump`로:

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > backup_$(date +%Y%m%d).sql
```

⚠️ **따옴표의 위치가 이 명령의 전부다.** `$POSTGRES_USER` 와 `$POSTGRES_DB` 는
`infra/central/central.env` 에 있고 **운영자 셸에는 없다** — `--env-file` 은 compose
파일 보간에 쓰이지 컨테이너나 셸의 환경이 아니다(같은 함정을 compose 파일이
`ALLOW_INSECURE_TRANSPORT` 주석에서 이미 이름으로 적는다). 옛 문언처럼 바깥 셸에서
전개하면 빈 값이 되어 libpq 가 OS 사용자 `root` 로 접속을 시도하고
`FATAL: role "root" does not exist` 로 실패한다. **그런데 리다이렉션은 이미
파일을 만든 뒤라, 결과는 «성공한 백업» 처럼 보이는 0바이트 `backup_<오늘>.sql` 이다**
(적대 평가 3R 실측). 위처럼 `sh -c` 로 감싸면 변수는 컨테이너 안에서 전개된다 —
postgres 이미지가 실제로 그 값을 갖고 있는 유일한 곳이다.

⚠️ 백업 직후 **크기를 확인**하라. 0바이트는 백업이 아니다.

```bash
ls -l backup_$(date +%Y%m%d).sql
```

복구는 부팅된 빈 DB에 해당 SQL을 적용합니다. (EMS는 호스트 bind-mount로 백업
디렉토리를 노출하지만, WSL 경로 복잡성을 피하기 위해 우리는 named volume +
`pg_dump` 절차로 같은 의도를 달성합니다.)

## 7. 챔버 노드 연결

측정 PC(챔버 노드)는 네이티브로 남아 LAN으로 중앙에 접속합니다. 노드의
`FCC_CENTRAL_*` 환경을 중앙 PC를 가리키게 설정:

- 운영 Gateway: `http://<CENTRAL_IP>:8080`
- 노드 Session API 광고 주소: `http://<NODE_IP>:9000`
- 머신 토큰: `FCC_CHAMBER_CLIENT_SECRET`(realm `fcc-chamber-node`)

## 검증

```bash
# 정적: compose YAML 유효성 + 봉인 테스트
wsl.exe -d Ubuntu bash -lc 'cd /mnt/c/FCC_mobile_test_automation && \
  DOCKER_CONFIG=/tmp/fcc-docker-config docker compose \
  -f infra/docker-compose.central.yml --env-file infra/central/central.env config >/dev/null && echo CONFIG-OK'
python -m pytest tests/test_central_docker_compose.py -q
```

## 추가 하드닝 후보 (tech-debt — 런타임 부팅 검증 후 적용)

현재 적용된 하드닝: `restart: unless-stopped`, `no-new-privileges`(전 서비스),
postgres `cap_drop ALL` + 최소 cap, 2계층 네트워크(DB internal). 다음은 실제
부팅 검증이 필요해 보류된 항목입니다:

- `cap_drop: ALL`을 keycloak/api/web에도 적용(web의 내부 80 특권 포트 바인드 +
  자바/nginx 런타임 검증 필요).
- `read_only: true` + tmpfs 전 서비스 적용.
- 시크릿 암호화(sops 등)로 `central.env` 평문 제거.
- realm LAN-origin 추가를 자동화하는 헬퍼 스크립트.
- 회사 SSO(Azure AD/Entra) 위임으로 자체 Keycloak 대체.
