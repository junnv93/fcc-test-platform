# 중앙 PC 소스 업데이트 후 재배포 런북

이 문서는 중앙 PC(LAN 서버)에서 **이미 운영 중인 FCC 중앙 스택을 최신 `main` 코드로
갱신**할 때 따르는 단일 실행 절차다. 예: 며칠 동안 개발 PC에서 수정·머지된 내용을 중앙 PC에
적용하고 스택을 다시 올리는 상황.

## 문서 경계

이 문서가 담당하는 범위:

- `git pull`로 최신 소스 반영
- `central.env`에 새로 필요해진 키가 있는지 대조
- 운영 DB 백업
- 이미지 재빌드와 스택 기동(= 마이그레이션 적용)
- 마이그레이션이 **실제로** 적용됐는지 확인
- 배포 후 스모크
- 배포 드리프트 게이트(도는 배포가 이 저장소와 같은가)
- 측정 PC(챔버)를 같은 리비전으로 함께 갱신
- 되돌리기

이 문서가 담당하지 **않는** 범위:

- 중앙 PC를 **처음 구축**하고 실운영으로 전환: [central-pc-operational-validation-runbook.md](./central-pc-operational-validation-runbook.md)
- 중앙 PC **재부팅 후 단순 기동**: [fcc-central-pc-reboot-ops-guide.md](./fcc-central-pc-reboot-ops-guide.md)
- 배포 모델·env 항목의 의미: [../../infra/central/ONPREM_DEPLOYMENT.md](../../infra/central/ONPREM_DEPLOYMENT.md)
- 챔버 PC에서 웹 경로가 도는지 검증: [chamber-pc-operational-verification-runbook.md](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/operations/chamber-pc-operational-verification-runbook.md)
- Session Node operator package 설치·기동: [chamber-session-node-operations.md](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/operations/chamber-session-node-operations.md)

---

## 0. 갱신 때 절대 하지 않는 것

> ⚠️ **`down -v` 를 쓰지 않는다.** 중앙 DB는 named volume `central-pgdata` 에 있고,
> `-v` 는 그 볼륨을 지운다. 컨테이너를 지우는 것은 안전하지만 볼륨을 지우면 운영 데이터가
> 사라진다.
>
> ⚠️ **최초 구축 런북의 S2(b) 를 따라오지 않는다.** 그 절은
> *"볼륨 삭제 (dev 시드 폐기)"* 를 지시한다 — **최초 구축 문맥에서만** 옳다. 갱신은
> 볼륨을 보존한다.
>
> ⚠️ **`docker system prune -a` / `docker image prune -a` 를 쓰지 않는다.**
> 이 중앙 PC 에서는 **EMS 가 같은 docker 로 함께 운영 중**이다
> (`10.206.34.233:8090` — [포트·토폴로지 기준](./fcc-central-pc-port-topology.md)).
> `-a` 는 "현재 컨테이너가 쓰지 않는" 이미지를 전부 지우므로 **EMS 이미지까지 지운다.**
> 이미지 정리가 필요하면 dangling 만: `docker image prune -f`.

---

## 1. 작업 위치와 코드 상태 확인

모든 명령은 **중앙 PC 의 WSL Ubuntu 터미널**에서 실행한다.

```bash
cd /path/to/fcc-test-platform    # ⚠️ FCC 저장소가 아니다 (2026-09-03 배치 변경)

git status --short
git branch --show-current
git log --oneline -5
```

기대:

- 브랜치가 `main`.
- `git status --short` 에 의도하지 않은 로컬 수정이 없다.

로컬 수정이 있으면 그 내용이 운영 반영 대상인지 먼저 확인한다. 모르면 **여기서 멈추고**
개발 담당자에게 확인한다 — 임의로 `git checkout --` 하지 않는다.

`infra/central/central.env` 는 gitignore 대상이라 `git status` 에
나타나지 않고 `git pull` 로 덮이지도 않는다.

---

## 2. 최신 소스 반영 — 의존성은 이미지가 가져간다

```bash
git pull --ff-only origin main
git log --oneline -1
```

`--ff-only` 가 거부하면 로컬에 머지되지 않은 커밋이 있다는 뜻이다. 그 내용을 확인하기 전에
강제로 넘기지 않는다.

> **호스트에서 의존성을 설치하는 단계는 없다.** Python 의존성은
> `requirements-central.txt` 가 `Dockerfile.api` 안에서, 프론트엔드 의존성은
> `apps/web/package-lock.json` 이 `Dockerfile.web` 의 `npm ci` 로 설치된다. 즉
> **의존성 반영 = 이미지 재빌드**(§5)이고, 별도 단계가 아니다.
>
> ⚠️ 그래서 §5 의 `--build` 를 빼면 lockfile 이 바뀐 배포에서 **옛 의존성이 그대로 돈다.**

---

## 3. env 축 — 새로 필요해진 키가 있는지 대조

`central.env` 는 운영자 소유 파일이라 `git pull` 이 갱신하지 않는다. 반면
`central.env.example` 은 코드와 함께 갱신되고, **compose 가 컨테이너로 넘기는 값은
compose 파일에 선언된 것뿐**이다. 새 릴리스가 새 키를 요구하는데 운영자 파일에 없으면
컨테이너가 보는 값은 빈 문자열이고, 모드에 따라 **부팅 거부**(예:
`ValueError: local_jwt auth requires local_jwt_issuer`) 또는 **조용한 기본값 동작**이 된다.

```bash
comm -23 \
  <(grep -oE '^[A-Z_]+=' infra/central/central.env.example | sort -u) \
  <(grep -oE '^[A-Z_]+=' infra/central/central.env         | sort -u)
```

출력이 비어 있으면 이번 갱신에 새 키가 없다. 키 이름이 나오면
`infra/central/central.env.example` 의 해당 주석을 읽고 운영값을 정해
`infra/central/central.env` 에 추가한다.

> ⚠️ **`$` 를 비밀번호에 쓰지 않는다.** compose 가 보간해 조용히 먹는다 — 실측:
> `Pa$$w0rd$USER!` 가 컨테이너에 `Pa$w0rddevuser!` 로 도착한다. 증상은
> *"env 는 맞는데 로그인이 안 된다"* 이고 원인이 화면에 뜨지 않는다.

로그인 전략을 바꾸는 갱신이면 **기동 전에** 짝을 확인한다. 백엔드만 바꾸면 화면이 여전히
IdP 로 튕기고, 프론트만 바꾸면 로그인 요청이 401 로 돌아온다.

```bash
python3 scripts/check_auth_mode_pairing.py --env-file infra/central/central.env
```

종료 코드 `0`=짝, `1`=어긋남, **`2`=판정할 값이 없음**. ⚠️ `2` 는 통과가 아니다.

### FCC 운영 인증 프로파일 — 현재 LAN 우선

HTTPS를 준비하기 전 중앙 PC는 다음 프로파일을 사용한다. 이 값들은 하나의 묶음이며
일부만 바꾸지 않는다.

```env
FCC_PLATFORM_AUTH_MODE=local_jwt
FCC_HEADLESS_AUTH_MODE=local_jwt
WEB_AUTH_MODE=local
ALLOW_INSECURE_TRANSPORT=true
```

운영자가 환경 파일을 직접 편집하지 않도록 아래 명령으로 필요한 키를 갱신할 수 있다.
명령은 기존 파일을 timestamp 백업하고, platform/headless secret이 이미 다르면
안전하게 중단한다. secret과 초기 비밀번호는 출력하지 않는다.

> **운영자 편집 금지:** 이 런북과 후속 인계에서는 `nano`, `vim`, 메모장 등으로
> `central.env`를 직접 열어 수정하라고 안내하지 않는다. 아래와 같은 자동화 명령만
> 사용한다. 이 규칙은 Claude와 Codex 모두에 적용된다.

```bash
python3 scripts/configure_central_lan_auth.py \
  --env-file infra/central/central.env

python3 scripts/check_auth_mode_pairing.py \
  --env-file infra/central/central.env
```

`configure_central_lan_auth.py`가 timestamp 백업, 중복 active key 거부, 동일 JWT
secret 보장, 관리자 입력, secret 비출력을 담당한다. 이 명령은 재실행 가능하며 긴
inline heredoc을 운영자 셸에 붙여 넣지 않는다.

정상 결과가 나온 뒤에만 `docker compose config`와 DB 백업, `up -d --build`를 수행한다.
HTTPS를 도입하면 위 네 값을 모두 `oidc_jwt / oidc_jwt / oidc / false`로 함께 전환한다.
EMS의 구형 Auth.js/HTTP 방식을 FCC에 복사하지 않는다.

---

## 4. 운영 DB 백업

마이그레이션은 §5 의 스택 기동에 **딸려서 자동으로 돈다.** 그러므로 백업은
**§5 보다 먼저** 받는다.

```bash
docker compose -f infra/docker-compose.central.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > backup_pre_deploy_$(date +%Y%m%d_%H%M).sql

ls -lh backup_pre_deploy_*.sql
```

> ⚠️ **따옴표의 위치가 이 명령의 전부다.** `$POSTGRES_USER` / `$POSTGRES_DB` 는
> `infra/central/central.env` 에 있고 **운영자 셸에는 없다** — `--env-file` 은 compose
> 파일 보간에 쓰이지 셸이나 컨테이너의 환경이 아니다. 바깥 셸에서 전개하면 빈 값이 되어
> libpq 가 OS 사용자로 접속을 시도하고 `FATAL: role "root" does not exist` 로 실패하는데,
> **리다이렉션은 이미 파일을 만든 뒤라 결과는 «성공한 백업» 처럼 보이는 0바이트 파일**이다.
> `sh -c` 로 감싸야 변수가 컨테이너 안에서 전개된다.

기대: 파일 크기가 **0바이트가 아니다.** 0바이트는 백업이 아니다.

---

## 5. 이미지 재빌드와 스택 기동

> 🔴 **`platform-api` 는 둘입니다 (2026-09-04 이후).** `platform-api-node` 는
> `build:` 가 없고 `platform-api` 가 만든 **같은 이미지**를 씁니다 — 누락이 아니라
> 의도입니다(두 번째 `build:` 를 두면 같은 컨텍스트를 두 번 빌드합니다).
> `up -d --build` 는 둘 다 재기동하므로 추가 명령은 필요 없지만, **완료 후 둘 다
> `healthy` 인지 확인**하십시오:
> ```bash
> docker compose -f infra/docker-compose.central.yml \
>   --env-file infra/central/central.env ps platform-api platform-api-node
> ```
> 인증 모드가 서로 반대(`local_jwt` / `oidc_jwt`)라 하나만 뜨면 브라우저나 챔버 노드
> 중 한쪽이 조용히 401 을 받습니다. 근거:
> [`.claude/evaluations/2026-09-04-http-dual-auth-node-lane.md`](../../.claude/evaluations/2026-09-04-http-dual-auth-node-lane.md)

```bash
cd /path/to/fcc-test-platform

GIT_REVISION="$(git rev-parse HEAD)" \
DOCKER_CONFIG=/tmp/fcc-docker-config \
docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env up -d --build
```

> **`GIT_REVISION` 은 이 배포가 어느 커밋인지를 이미지에 새긴다.** 태그가 `:latest` 로
> 고정이라 그 라벨이 없으면 *도는 코드가 현재 HEAD 인가* 를 **원리적으로 물어볼 수 없다**
> — §9 의 드리프트 게이트가 그 라벨을 읽는다. 빼먹어도 배포는 정상 동작하며, 게이트가
> 그 축을 `UNKNOWN` 으로 답한다(통과가 아니다).

> ⚠️ **`--build` 는 필수다.** 이 저장소가 빌드하는 태그
> (`fcc-central-platform-api:latest` — platform-api · **platform-api-node** · central-migrate 가 공유,
> 그리고 `fcc-central-web:latest`)가 **고정**이라, `--build` 없이 `up -d` 하면
> compose 는 같은 태그의 **로컬 캐시 이미지를 그대로 재사용**한다. `git pull` 로
> 소스가 바뀌어도 컨테이너 안의 코드는 옛 코드인 채로 스택이 "정상 기동" 한다 —
> 실패하지 않고 조용히 안 바뀐다. 마이그레이션도 마찬가지로 **옛 이미지에 들어 있던
> 마이그레이션 집합**만 적용된다.

> ⚠️⚠️ **`--build` 는 `headless-api` 를 만들지 않는다 (2026-09-03 변경).**
> 그 이미지의 소스는 provider 저장소(`FCC_mobile_test_automation`)에 있고, compose 의
> `headless-api` 서비스에는 `build:` 가 없다 —
> `image: ${FCC_HEADLESS_IMAGE:-fcc-unlicensed-headless-api:latest}` 를 **소비만** 한다.
> headless 표면이 바뀌었으면 **챔버 PC 쪽에서 그 이미지를 빌드해 같은 태그로 올린 뒤**
> 여기서 `up -d` 해야 한다.
>
> ⚠️ **이 문단의 이전 판은 `web` 에 대해 정반대를 적고 있었다.** 그 문언은 *FCC 저장소에
> 있던 사본*의 관점이라(*"이 저장소는 web 이미지를 빌드하지 않는다"*), 런북이 platform 으로
> 오면서 **거짓이 됐다** — 여기서는 `apps/web` 이 이 저장소에 있고 web 을 **빌드한다**.
> 문서를 옮길 때 *"이 저장소"* 가 가리키는 대상이 바뀐다는 것이 이 계급의 결함이고,
> 2026-09-03 이관에서 compose 주석에서도 같은 형태를 발견했다.
>
> ⚠️ 어느 쪽이든 이 결함은 **실패하지 않는다** — 스택은 정상 기동하고 그 서비스만
> 옛 것이 돈다. §9 의 드리프트 게이트가 `revision`/`image-id` 축으로 답한다.
> 단, `headless-api` 는 이 저장소가 빌드하지 않으므로 **그 게이트의 대상이 아니다**
> (게이트는 `build:` 를 가진 서비스만 본다). 그 이미지의 신선도를 보는 축은
> **provider 저장소 쪽에 있어야 한다.**

**마이그레이션은 이 명령이 적용한다.** `central-migrate` 는
`condition: service_completed_successfully` 원샷 job 이고 **매 `docker compose up -d`
마다 다시 실행**되며, `migrate` 가 미적용분 전체를 체크섬과 함께 순서대로 적용한다.
`platform-api` 는 그 job 이 **완료된 뒤에** serve 한다. 별도 마이그레이션 명령은 없다.
(실측 2026-08-22 — 이 사실의 이전 판 문언은 *"이미 떠 있는 DB 에는 자동 적용되지 않는다"*
였고 그것은 거짓이었다.)

빌드가 끝나면 dangling 이미지만 정리한다(§0 의 `-a` 금지 참조).

```bash
docker image prune -f
```

---

## 5-a. 챔버 전용 Keycloak client 가 살아남았는지 확인한다

> ⚠️ **`up -d` 는 Keycloak 도 다시 띄울 수 있고, 그러면 그 챔버 secret 으로 토큰이
> 나오지 않게 된다.** 그 client 는 realm 시드 JSON 에 들어 있지 않다 — 챔버는 배포마다
> 다르고 시크릿을 저장소에 넣을 수 없어서다(런북 S5(a2) 가 **런타임에** 만든다).
>
> ⚠️ **문장을 여기서 약하게 적는 것이 의도다.** 관측되는 것은 `invalid_client` 하나이고,
> 그것은 *client 가 지워졌다* 와 *secret 이 달라졌다* 에 **같은 답**을 준다. 두 가설은
> 사후에 가릴 수 없고(복구 명령이 client 를 만들거나 갱신해 버린다) **판정과 복구는
> 어느 쪽이든 같다.** 그러니 "사라진다"고 단정하지 말고 "그 secret 으로 토큰이 안 나온다"
> 로 읽으면 된다.
>
> ⚠️ **이 결함은 자격 문제처럼 보이지 않는다.** 노드의 heartbeat·참조 동기화·설정
> pull 이 전부 403 으로 떨어지는데, 화면에는 그냥 **「Offline · No signal」** 로 보인다.
> 실측 2026-09-02: 이미지 재빌드 직후 `fcc-chamber-chamber-devpc` 가 realm 에서
> 사라졌고 토큰 발급이 `invalid_client` 였다. 갱신 자체는 "성공"으로 끝난다.

**폭발 반경을 줄이는 법 — 갱신할 서비스를 이름으로 지정한다. 다만 이것은 보장이
아니다.** 같은 날 세 번의 실측:

| 실행 | 명령 | Keycloak |
|---|---|---|
| 1회차 | `up -d platform-api headless-api` | **재생성됐다** (`Container … Started`) |
| 2회차 | `up -d platform-api headless-api` | 그대로 (`Up About an hour`) |
| — | `docker inspect fcc-central-keycloak` | `StartedAt` 이 **1회차 시각과 일치**, `restartCount 0` |

> ⚠️ **이 문단은 2026-09-02 에 정정됐다.** 처음에는 *"서비스를 이름으로 지정하면
> Keycloak 이 재시작되지 않는다"* 를 대조표로 단언했는데, 그것은 **2회차 관측 하나에
> 기반한 과대 일반화**였다. 1회차는 **같은 명령 형태**로 Keycloak 을 재생성했다.
>
> 이유는 compose 의 동작이다 — **이름을 지정해도 `depends_on` 서비스는 평가되고,
> 그 서비스의 resolved config 가 도는 컨테이너와 다르면 재생성된다.** 그래서 이름
> 지정은 *대개* 안 건드리지만 *반드시* 안 건드리지는 않는다. 세션이 바뀌거나 env 가
> 달라진 뒤의 **첫 `up -d`** 가 특히 그렇다.
>
> **보장은 명령이 아니라 판정이다** — 아래 토큰 발급을 재빌드마다 한 번 돌린다.
> "이름을 지정했으니 괜찮다"는 추론이고, 그 추론이 이 문서에 한 번 적혔다.

파이썬 코드만 바뀌었다면 두 API 서비스만 이름으로 올리면 된다 — `central-migrate` 는
의존으로 함께 돌아 마이그레이션도 적용된다(실측: `Exited (0)`).

⚠️ **판정은 매번 한다.** 위 표의 어느 줄에 해당하는지는 실행 전에 알 수 없다.

**판정 1순위 — Keycloak 이벤트 로그를 본다. 응답 본문보다 이것이 먼저다.**

```bash
docker logs fcc-central-keycloak --since 30m 2>&1 | grep CLIENT_LOGIN_ERROR
```

| 로그의 `error=` | 실제 상태 | 옳은 복구 |
|---|---|---|
| `client_not_found` | **삭제** — realm 재import 가 지웠다 | **`provision` 을 돌린다.** 다른 방법이 없다 |
| `invalid_client_credentials` | **회전** — client 는 있고 secret 만 다르다 | ⚠️ **`provision` 을 돌리지 마라.** 또 회전시켜 *이미 새 secret 을 가진 노드*까지 끊는다. admin API 로 **현재 secret 을 읽어 배포**한다 |
| (아무 줄도 없음) | 그 자격으로 시도한 적이 없다 | 노드를 한 번 띄워 사건을 만든 뒤 다시 본다 |

> ⚠️ **HTTP 응답 본문으로는 이 둘을 구분할 수 없다.** 삭제도 회전도 똑같이
> `{"error":"invalid_client"}` 를 답한다 — OAuth 가 **client 열거를 막으려고** 일부러
> 그렇게 정한 것이라, 이것은 결함이 아니라 사양이다. 그래서 본문만 보면 *"구분 불가"*
> 라는 잘못된 결론에 이른다. **구분은 처음부터 가능했고, 표면이 달랐을 뿐이다.**
>
> ⚠️ **로그가 admin API 조회보다 나은 이유는 편의가 아니라 정확성이다.** admin 조회는
> *"지금 있는가"* 를 답하는데 그 답은 **언제 물었는가에 오염된다** — 복구를 돌린 뒤에
> 물으면 "있다"가 나오고, 그 "있다"는 *삭제된 적 없다* 와 *삭제됐다가 복구됐다* 를
> 구분하지 못한다. 기동 중에 물으면 realm import 전이라 "없다"가 나오고, 그 "없다"는
> *삭제됐다* 와 *아직 안 떴다* 를 구분하지 못한다. **로그는 사건 시각이 줄에 박혀 있어
> 두 오염 모두에 면역이다.**
>
> **실측 2026-09-02 — 같은 날에 두 사건이 다 났고, 셋 다 본문은 같았다:**
> ```
> 06:42:24  Import finished successfully
> 06:42:27  error="client_not_found"            ← 삭제. import 완료 3초 뒤라 기동 중이 아니다
> 06:42:33  error="client_not_found"
> 06:45:35  error="invalid_client_credentials"  ← 복구 뒤 옛 사본으로 친 반사실 = 회전 상태
> ```
> 마지막 줄이 **의도된 반사실**이라는 점이 중요하다 — 복구가 끝난 뒤 *일부러 옛 secret 으로*
> 쳐서 에러 토큰이 바뀌는 것을 확인했다. 그것이 이 판별자가 양방향으로 작동한다는 증거다.

**판정 2순위 — 토큰을 실제로 발급해 본다. 응답 코드가 아니라 **본문**을 본다.**

```bash
CHAMBER_ID=chamber-devpc      # 챔버마다 반복
curl -s -X POST "http://localhost:8081/realms/fcc-dev/protocol/openid-connect/token" \
  -d grant_type=client_credentials \
  -d "client_id=fcc-chamber-${CHAMBER_ID}" \
  -d "client_secret=${CHAMBER_CLIENT_SECRET}" | head -c 120; echo
```

- `{"access_token": …` → 살아 있다. 이 절은 끝이다.
- `{"error":"invalid_client"` → **사라졌다.** 아래로 간다.

**복구** — client 를 다시 만들고, 새 시크릿을 그 챔버 노드에 넣는다.

```bash
cd /path/to/fcc-test-platform
set -a; . <(grep -E '^(KEYCLOAK_ADMIN|KEYCLOAK_ADMIN_PASSWORD)=' infra/central/central.env); set +a
FCC_KEYCLOAK_BASE_URL=http://localhost:8081 \
python3 scripts/platform_chamber_token_evidence.py \
  --live --chamber-id "${CHAMBER_ID}" --action provision \
  --output /tmp/chamber-provision-${CHAMBER_ID}.json
```

시크릿은 **새로 발급된다**(옛 값은 더 이상 유효하지 않다). 읽어서 그 챔버 노드의
`FCC_CENTRAL_CLIENT_SECRET` 에 넣고 노드를 재기동한다.

```bash
CLIENT_UUID="$(docker exec fcc-central-keycloak /opt/keycloak/bin/kcadm.sh get clients \
  -r fcc-dev --fields clientId,id --server http://localhost:8080 \
  --realm master --user "${KEYCLOAK_ADMIN}" --password "${KEYCLOAK_ADMIN_PASSWORD}" \
  | python3 -c "import sys,json;print(next(c['id'] for c in json.load(sys.stdin) if c['clientId']=='fcc-chamber-'+'${CHAMBER_ID}'))")"
docker exec fcc-central-keycloak /opt/keycloak/bin/kcadm.sh get "clients/${CLIENT_UUID}/client-secret" \
  -r fcc-dev --server http://localhost:8080 \
  --realm master --user "${KEYCLOAK_ADMIN}" --password "${KEYCLOAK_ADMIN_PASSWORD}"
```

> ⚠️ **시크릿을 채팅·이슈·문서에 붙여 넣지 않는다.** 위 명령의 출력은 그 자리에서
> 챔버 PC 의 노드 설정으로만 옮긴다.

> ⚠️ **먼저 위 판별자를 보라 — `invalid_client_credentials`(회전) 이면 이 절을 돌리면
> 안 된다.** 아래는 `client_not_found`(삭제) 일 때의 절차다.
>
> ⚠️ **`--action provision` 은 secret 을 재발급한다 — 그 client 를 쓰는 다른 노드가
> 즉시 죽는다.** 여러 노드가 한 client 를 공유하는 형상(개발 PC 에서 흔하다)에서는
> 복구가 **다른 노드를 끊는 행위**이고, 그쪽 증상은 화면의 「Offline」과 로그의
> `Chamber 자가 등록 실패` 뿐이라 **원인으로 보이지 않는다.**
> → 복구 전에 **그 client 를 쓰는 모든 노드를 확인**하고, **한 번만** 돌리고, 새 secret 을
> **전부에** 넣는다. 실측 2026-09-02: 그 사실을 모른 채 낡은 사본의 secret 으로 401 을
> 본 세션이 *"몇 시간째 자격이 죽어 있었다"* 로 오진했다 — 죽어 있던 것은 **그 사본**이었다.

**확인** — 노드 재기동 뒤 그 챔버의 로그에 403 이 남는지 본다. 남는 것이 정상인 403 은
**자가 등록 하나뿐**이다(`register_chamber` 는 `platform:admin` 을 요구하는데 챔버
토큰은 `platform:chamber` 만 갖는다 — 별개 축이고 heartbeat 는 계속된다).

---

## 6. 마이그레이션이 실제로 적용됐는지 확인

`central-migrate` 가 `Exited (0)` 인 것만으로는 **이번 갱신의** 마이그레이션이 들어갔다는
증거가 못 된다(옛 이미지로 돌아도 0 으로 끝난다 — §5 의 `--build` 경고). 원장을 직접
읽는다.

```bash
docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env \
  exec platform-api python scripts/platform_db_migrate.py status
```

기대(JSON):

- `"pending": []` — 미적용분 0.
- `"drift": []` — 이미 적용된 마이그레이션 파일이 수정되지 않았다.

`pending` 이 비어 있지 않으면 §5 를 `--build` 와 함께 다시 실행한다.

`drift` 에 항목이 있으면 **재적용하지 않는다** — 이미 적용된 `.sql` 이 편집됐다는 뜻이고,
러너가 `MigrationDriftError` 로 거부하는 것이 설계다. 마이그레이션은 append-only 이므로
변경은 새 `NNN_*.sql` 로 와야 한다. 예외는 exporter 가 재렌더하는 bootstrap `001` 뿐이고
그것만 `reconcile` 서브커맨드로 해소한다 — 상세는
[../development/central-db-migrations.md](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/development/central-db-migrations.md).

---

## 7. 상태 확인

```bash
docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env ps
```

정상 기준:

```text
fcc-central-postgres       healthy
fcc-central-keycloak       healthy
fcc-central-headless-api   healthy
fcc-central-platform-api   healthy
fcc-central-web            running
fcc-central-migrate        Exited (0)
```

- **`fcc-central-migrate` 의 `Exited (0)` 이 정상이다.** 1회성 스키마 job 이 성공적으로
  끝난 상태다.
- `platform-api` 의 healthcheck 는 `/platform/ready` 다 — 중앙 DB 에 닿지 못하면
  `unhealthy` 가 된다(그것이 이 probe 를 쓰는 이유다).

> **nginx 를 따로 재생성하는 단계는 없다 — 넣지 말 것.** API 컨테이너가 재생성되어 IP 가
> 바뀌어도 `web` 은 옛 IP 를 들고 있지 않다: `infra/central/nginx.conf` 가
> `resolver 127.0.0.11 valid=30s` + 변수 `proxy_pass` 로 **요청 시점에** 서비스 이름을
> 해소한다. (EMS 런북에는 `deploy:nginx-apply` 단계가 있는데, 그것은 EMS 의 nginx 가
> 기동 시점에 upstream 을 고정하기 때문이다. 우리 형상에는 그 결함이 없다.)
>
> 다만 **`central.env` 만 고치고 재기동한 경우** SPA 가 받는 `/runtime-config.js` 는
> `web` 컨테이너 기동 시 env 에서 생성되므로, 값이 반영되지 않았으면
> `... up -d --force-recreate web` 로 그 컨테이너만 다시 만든다.

---

## 8. 스모크

```bash
CENTRAL_IP=<중앙 PC의 LAN IP>     # 예: 10.206.34.233

curl -fsS http://${CENTRAL_IP}:8080/health
curl -fsS http://${CENTRAL_IP}:8081/realms/fcc-dev/.well-known/openid-configuration >/dev/null

python3 scripts/check_auth_mode_pairing.py --env-file infra/central/central.env \
  --runtime-config-url http://${CENTRAL_IP}:8080/runtime-config.js
```

세 명령이 모두 성공하면 브라우저로 `http://${CENTRAL_IP}:8080` 에 접속해 확인한다.

- 로그인 화면이 뜨고, 로그인 후 홈 화면이 표시된다.
- `시험 챔버` 메뉴에 등록 챔버가 보이고, 노드가 떠 있으면 온라인/heartbeat 시각이 갱신된다.

> `check_auth_mode_pairing.py` 의 `--runtime-config-url` 축이 §3 과 다른 질문에 답한다:
> §3 은 *파일에 적힌 값*을, 여기는 **실제로 서빙되는 값**을 본다.

---

## 9. 배포 드리프트 게이트 — 도는 배포가 이 저장소와 같은가

앞 절들은 *스택이 응답하는가* 를 물었다. 이 절은 다른 질문에 답한다: **지금 도는 것이
방금 배포하려던 그것인가.** 갱신 배포의 최빈 결함(`--build` 누락)은 **실패하지 않으므로**
스모크로는 잡히지 않는다.

```bash
python3 scripts/check_deployment_drift.py

# 실제로 서빙되는 값까지 보려면
python3 scripts/check_deployment_drift.py \
  --runtime-config-url http://${CENTRAL_IP}:8080/runtime-config.js
```

여섯 축을 각각 `PASS` / `DRIFT` / `UNKNOWN` 으로 답한다.

| 축 | 무엇을 묻는가 | `DRIFT` 면 |
|---|---|---|
| `revision` | 도는 이미지가 현재 `git HEAD` 로 빌드됐는가 | §5 를 `--build` 와 함께 재실행 |
| `image-id` | 컨테이너가 그 태그의 **현재** 이미지를 쓰는가 | `... up -d` 로 컨테이너 재생성 |
| `migration` | 미적용/드리프트 마이그레이션이 있는가 | §6 |
| `env-keys` | `central.env.example` 이 요구하는 키가 다 있는가 | §3 |
| `auth-pair` | 백엔드 auth mode 와 SPA 로그인 전략이 짝인가 | §3 · §8 |
| `public-host` | `PUBLIC_HOST` 가 이 PC 가 실제로 가진 주소인가 | 재부팅 가이드 §2-1(WSL IP 변경) |

종료 코드: `0`=전 축 PASS, `1`=한 축이라도 DRIFT, `2`=DRIFT 는 없으나 판정하지 못한 축이 있음.

> ⚠️ **`2` 는 통과가 아니다.** 묻지 못한 축은 통과가 아니라 미확인이고, 둘을 같은 코드로
> 접으면 이 점검은 아무것도 하지 않으면서 초록으로 보인다. 가장 흔한 `UNKNOWN` 은
> *리비전 라벨이 비어 있다* — §5 의 배포 명령에 `GIT_REVISION` 을 붙이지 않은 것이다.
>
> ⚠️ **`DRIFT` 가 `UNKNOWN` 을 이긴다** — 아는 결함이 먼저 조치 대상이므로, `UNKNOWN`
> 하나가 `DRIFT` 를 가리지 않는다.

---

## 10. 측정 PC(챔버) 를 같은 리비전으로 갱신

> ⚠️ **중앙만 갱신하면 절반이다.** 세션 부모행을 만드는 코드, 결과 outbox, 노드 API 는
> **측정 PC 쪽**에 있다. 한쪽만 갱신하면 측정은 되는데 결과가 중앙에 유입되지 않는 형태로
> 깨진다(2026-07-30 에 실제로 그렇게 나뉘어 들어갔다).

챔버 PC 는 둘 중 하나이며 갱신 대상물이 다르다
([운영 문서 지도 §3](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/operations/README.md)):

| | 웹 PC (포트 승인됨) | 로컬 PC (미승인) |
|---|---|---|
| 설치물 | Session Node **만** | GUI **만** |
| 갱신 대상 | `fcc-session-node.exe` operator package | `main_entry.exe` |
| 빌드 | `python build_session_node_nuitka.py` | `python build_nuitka.py` |

```bat
REM 챔버 PC (Windows)
cd C:\FCC_mobile_test_automation
git pull --ff-only origin main
```

**웹 PC** — 새 operator package 를 배포하고 checksum 을 대조한 뒤에만 실행한다. package
root 는 `C:\FCC\SessionNode\package`, runtime state 는 형제 경로
`C:\FCC\SessionNode\runtime` 이다(승인된 SSOT: `infra/chamber/session-node-deployment-policy.json`).
package 는 통째로 교체해도 runtime state 가 사라지지 않는 배치라, **package 안에 runtime
폴더를 만들지 않는다.**

```powershell
Get-Content .\SHA256SUMS.txt
Get-FileHash .\fcc-session-node.exe -Algorithm SHA256
Get-Content .\package-manifest.json | ConvertFrom-Json
```

hash 가 맞지 않으면 **실행하지 말고** 배포 담당자에게 재배포를 요청한다. 설치·기동·방화벽
절차는 [chamber-session-node-operations.md](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/operations/chamber-session-node-operations.md) 소관이다.

> ⚠️ **두 프로그램을 같은 PC 에서 동시에 띄우지 않는다.** 계측기는 공유 자원이고 두
> 프로세스가 같은 분석기를 열면 SCPI 가 섞인다 — 실해는 측정 실패가 아니라 **조용히 틀린
> 값**이다. 배타는 ① 설치로 선언, ② 런타임 락(중복 기동 시 **exit 3**) 두 층이다.

---

## 11. 실패 시 1차 확인

```bash
docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env logs --tail=200 platform-api

docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env logs --tail=200 central-migrate

docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env logs --tail=200 web
```

| 증상 | 먼저 볼 것 |
|---|---|
| `platform-api` 가 안 뜬다 / `unhealthy` | `central-migrate` 로그 → 마이그레이션 실패면 §6. DB 도달 실패면 postgres 상태 |
| 부팅이 `ValueError: ... requires ...` 로 끝난다 | §3 — 새 env 키 누락. compose 가 넘기지 않는 값은 빈 문자열이다 |
| 화면은 뜨는데 코드가 안 바뀐 것 같다 | §5 의 `--build` 를 빠뜨렸다. 다시 실행 후 §6 로 확인 |
| 로그인 후 튕김 | `PUBLIC_HOST` 가 실제 접속 IP 인가, Keycloak realm 에 그 origin 이 등재됐는가 |
| 로그인 전략이 화면과 API 가 다르다 | §8 의 `--runtime-config-url` 점검. 두 모드는 함께 바뀐다 |
| 브라우저가 아예 안 열린다 | 재부팅 직후라면 **WSL IP 변경**과 **8080 선점**을 먼저 본다 → [재부팅 가이드 §2-1](./fcc-central-pc-reboot-ops-guide.md) |
| 챔버가 오프라인 | 챔버 PC 의 `FCC_CENTRAL_BASE_URL`, 중앙 `8080` 도달성, 토큰 |
| 측정은 됐는데 결과가 없다 | §10 — 측정 PC 를 갱신하지 않았을 수 있다. outbox 는 결과를 보존한 채 재시도한다 |

---

## 12. 되돌리기

**코드만 되돌린다**(스키마 변경이 없었을 때):

```bash
git log --oneline -10                 # 되돌릴 리비전 확인
git checkout <이전 리비전>
DOCKER_CONFIG=/tmp/fcc-docker-config \
docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env up -d --build
```

⚠️ **스키마가 앞서 나간 상태에서 코드만 되돌리면** 옛 코드가 새 스키마를 만난다. 그
조합이 안전하다는 보장은 없다. 마이그레이션이 포함된 배포를 되돌려야 하면 순서는
**앱 되돌리기 → 필요한 경우에만 스키마 되돌리기**이고, 스키마 되돌리기는 대상 하나씩
명시한다:

```bash
docker compose -f infra/docker-compose.central.yml \
  --env-file infra/central/central.env \
  exec platform-api python scripts/platform_db_migrate.py rollback --target <버전 stem>
```

> ⚠️ **이 명령은 세 조건을 모두 만족할 때만 돈다** — 그러지 않으면 loud 하게 거부한다.
> ① 대상이 **가장 최근에 적용된** 마이그레이션이어야 한다(임의 지점으로 건너뛸 수 없다.
> 여러 개를 되돌리려면 최신부터 한 번에 하나씩), ② 그 `.sql` 에 `--rollback` 주석이
> 있어야 한다(없으면 `migration has no rollback annotations`), ③ 파일 체크섬이 원장과
> 같아야 한다.

되돌릴 수 없는 마이그레이션이거나 데이터가 이미 변형됐으면 **§4 의 백업 복원**이 유일한
경로다. 그래서 §4 를 건너뛰지 않는다.

---

## 13. 판단표

| 상황 | 할 일 |
|---|---|
| 재부팅 후 스택만 켠다 | [fcc-central-pc-reboot-ops-guide.md](./fcc-central-pc-reboot-ops-guide.md) |
| 최신 `main` 을 적용한다 | 이 문서 전체 |
| 중앙 PC 를 **처음** 구축한다 | [central-pc-operational-validation-runbook.md](./central-pc-operational-validation-runbook.md) |
| 마이그레이션이 포함됐다 | §4 백업 먼저 → §5 기동(자동 적용) → **§6 으로 확인** |
| `pending` 이 비지 않는다 | §5 를 `--build` 와 함께 재실행 |
| `drift` 가 나온다 | 재적용 금지. [central-db-migrations.md](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/development/central-db-migrations.md) 의 `reconcile` |
| 새 env 키가 필요해졌다 | §3 |
| 소스를 갱신했는데 화면이 그대로다 | §5 `--build` 누락 또는 `web` 컨테이너 미재생성(§7) |
| 측정 결과가 중앙에 안 들어온다 | §10 — 측정 PC 갱신 |
| 되돌려야 한다 | §12 |

---

## 14. 이 런북의 한계

여기 적는 것은 *지금 없는 것*이다. 없는 것을 있는 것처럼 읽으면 그 자리에서 넘어진다.

| # | 한계 | 무슨 뜻인가 |
|---|---|---|
| 1 | **드리프트 게이트는 중앙 축만 본다** | §9 의 여섯 축은 전부 **중앙 PC** 를 묻는다. 측정 PC(§10)가 같은 리비전인지 대조하는 축은 없다 — 노드는 컨테이너가 아니라 Windows exe 라 라벨을 읽을 자리가 다르다 |
| 2 | **리비전 축은 배포 명령에 의존한다** | `GIT_REVISION` 을 붙이지 않은 빌드는 라벨이 비고, 게이트는 `UNKNOWN`(exit 2) 을 답한다. 조용히 통과하지는 않지만 **그 축을 강제하지도 못한다** — compose 는 빌드 인자를 필수로 만들 수 없다 |
| 3 | **`infra/central/ONPREM_DEPLOYMENT.md` §4 의 기동 명령에는 아직 `GIT_REVISION` 이 없다** | 그 문서를 따라 부팅하면 리비전 축이 `UNKNOWN` 이 된다. 그 파일은 다른 작업 축이 소유 중이라 이번에 손대지 않았다 |
| 4 | **배포 후 기능 스모크가 curl 3발이다** | §8 은 *스택이 응답하는가* 까지만 답한다. 측정 경로가 실제로 도는지는 [챔버 PC 검증 런북](https://github.com/junnv93/FCC_mobile_test_automation/blob/main/docs/operations/chamber-pc-operational-verification-runbook.md) 이 소관이고, 그것은 별도 실행이다 |
| 5 | **Windows 실환경 갱신 이력이 없다** | §10 의 챔버 갱신 절차는 실제 챔버 PC 에서 갱신 사이클로 검증된 적이 없다(CI 러너가 Linux 뿐) |
| 6 | **이 문서의 명령은 실 배포로 재검증되지 않았다** | 근거는 저장소의 compose/Dockerfile/스크립트와 기존 런북의 실측 기록이다. §9 의 게이트는 판정 로직이 봉인돼 있고 라벨 왕복도 실 docker 로 확인했으나, **여섯 축이 함께 도는 실행은 실 배포에서만 처음 일어난다.** 처음 이 절차를 도는 세션은 어긋난 지점을 이 문서에 되적어 주기 바란다 |

⚠️ **초판(2026-08-23)의 한계 1 «배포 드리프트 게이트가 없다» 는 §9 로 상환됐다.** 지운
것이 아니라 **무엇이 남았는지**로 대체했다 — 게이트가 생겼다고 모든 드리프트를 보는 것은
아니다.

한계 1~3 은 성격상 코드/절차 작업이므로 `.claude/exec-plans/tech-debt-tracker.md` 등재
대상이다.
