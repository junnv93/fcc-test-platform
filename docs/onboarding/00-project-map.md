# 프로젝트 지도 — 무엇이 어디서 도는가

> 두 트랙 공통. **이 문서를 읽지 않고 어느 쪽도 시작하지 마십시오.**
>
> 모든 수치는 **2026-09-07, `fcc-test-platform @ 40ace1a` 에서 직접 실행**해 얻었습니다.
> 재는 방법을 §7 에 적었습니다.

---

## 1. 한 문장

**여러 시험 분야가 하나의 웹 플랫폼을 공유하고, 각 분야의 «시험 지식»은 그 분야
소유의 서비스 안에만 있습니다.**

이 한 문장이 이 프로젝트의 거의 모든 설계 결정을 만듭니다. 무엇을 어디에 둘지
헷갈릴 때 물어야 하는 질문은 언제나 하나입니다 —

> **이것은 분야 지식인가, 아니면 분야와 무관하게 참인 것인가?**

---

## 2. 세 개의 저장소

```
  ┌──────────────────────────┐        ┌──────────────────────────┐
  │  fcc-test-contracts      │◀───────│  fcc-test-platform       │
  │  계약 커널 (public)        │  의존   │  웹 플랫폼 (public)        │
  │  의존 «없음»               │        │  오늘 개발이 일어나는 곳     │
  └──────────────────────────┘        └──────────────────────────┘
              ▲                                     ▲
              │  pip install (git+https, 태그 핀)      │
              └───────────────┬─────────────────────┘
                              │
                  ┌───────────┴────────────┐        ┌────────────────────────┐
                  │ FCC_mobile_test_       │        │ 당신의 레포             │
                  │ automation (private)   │        │ (비공개)                │
                  │ = 첫 번째 provider      │        │ = 두 번째~ provider     │
                  └────────────────────────┘        └────────────────────────┘
```

| 레포 | 무엇 | 당신과의 관계 |
|---|---|---|
| `fcc-test-contracts` | 의존 없는 **계약 커널**. 스키마·계약 아티팩트·검사기 | 트랙 A: pip 로 소비 · 트랙 B: 커널 수정 시 **먼저** 태그 |
| `fcc-test-platform` | 중앙 DB · API · 인증/RBAC · 프런트엔드 · provider 레지스트리 | 트랙 B의 작업장 |
| `FCC_mobile_test_automation` | 원래의 모노레포. **오늘은 첫 번째 provider**(Unlicensed) | 참고용. 여기서 개발하지 않습니다 |

### ⚠️ 방향이 한 번 «역전»됐습니다

모노레포는 원래 두 레인의 **상위**였고, 배송 기계가 거기서 두 패키지를 만들어
내보냈습니다. **2026-08-31 에 그 기계가 퇴역**했고, 지금은 **모노레포가 두 패키지를
pip 로 소비**합니다. 실측:

```bash
$ ls FCC_mobile_test_automation/packaging/                     # No such file or directory
$ ls FCC_mobile_test_automation/scripts/stage_extraction_package.py   # No such file or directory
```

**재배송은 물리적으로 불가능합니다.** 그러므로 두 레인의 수정을 모노레포에
되반영할 필요가 없고, 모노레포에서 두 레인 파일을 고치는 것은 **의미가 없습니다.**

### 의존 방향은 단방향입니다

`platform → contracts`. 역은 없습니다. 그래서 **계약 커널에 필드 하나를 추가하는 것은
2-레포 작업이고 순서가 있습니다** — 커널 태그가 먼저 나가야 platform 이 그것을
소비합니다. 실측 (platform `pyproject.toml`):

```
fcc-test-contracts @ git+https://github.com/junnv93/fcc-test-contracts@v0.1.26
fcc-test-kernel    @ git+…/fcc-test-contracts@kernel-v0.5.4#subdirectory=packages/fcc-test-kernel
```

핀이 **태그**입니다. 브랜치가 아닙니다. 커널을 고치고 태그를 안 내면 platform 은
그 변경을 **보지 못합니다.**

---

## 2-a. ⚠️ 「내 PC 에 보이는 폴더」와 「저장소」는 다릅니다

합류자가 처음 반드시 헷갈리는 지점입니다. 개발 PC 에는 이런 폴더가 있을 수 있습니다:

```
~/fcc-delivery-final/          ← ⚠️ 이것은 «저장소가 아닙니다»
   ├── CLAUDE.md                  이 폴더를 여는 세션에게 주는 안내문
   ├── fcc-test-contracts/        ← 독립 clone (.git 디렉터리 보유)
   └── fcc-test-platform/         ← 독립 clone (.git 디렉터리 보유)
```

실측:

```bash
$ cd ~/fcc-delivery-final && git rev-parse --git-dir
fatal: not a git repository (or any of the parent directories): .git

$ ls ~/fcc-delivery-final/.gitmodules
No such file or directory                      # submodule 도 아니다
```

**`fcc-delivery-final` 은 평범한 디렉터리입니다.** 두 clone 을 나란히 두려고 만든
컨테이너일 뿐이고, git 상으로 두 레포는 **아무 관계가 없습니다.**
관계는 오직 **pip 의존**(§2)뿐입니다.

⚠️ **그러므로 그 폴더에서 `git` 명령을 치면 `not a git repository` 가 나고, 그것이
정상입니다.** 항상 레인 디렉터리로 들어가서 치십시오.

### ⚠️ 그리고 그 clone 은 «지금 이 순간에도» 낡아집니다

이 체크아웃은 여러 세션이 동시에 씁니다. 실측(2026-09-07):

```
로컬 fcc-test-platform HEAD  = 12e33fb
origin/main                  = 40ace1a      ← PR #143 이 그 사이에 머지됐다
```

**그 결과가 위험합니다** — `git status` 는 **어느 참조점에 대해** 묻는지 말해 주지
않으므로, 낡은 `HEAD` 때문에 **이미 착지한 남의 파일이 「내 미커밋 변경」처럼 보입니다.**

```bash
git fetch origin                       # 먼저
git diff origin/main --stat            # «origin/main 과» 비교하라. git status 만 보지 마라
git worktree list                      # 지금 누가 무엇을 하고 있는가
```

⚠️ **그리고 브랜치를 바꾸지 마십시오.** 옆 세션이 같은 트리에서 일하고 있습니다.
`git push origin HEAD:refs/heads/<이름>` 으로 push 하십시오.

---

## 3. 중앙 PC — 실제로 도는 것

```
                          중앙 PC (한 대)
   ┌────────────────────────────────────────────────────────────┐
   │                                                            │
   │   web (nginx)          :8080   ← 브라우저·챔버의 유일한 입구   │
   │     ├─▶ platform-api   :8002   플랫폼 소유                   │
   │     ├─▶ platform-api-node      플랫폼 소유 (노드 전용 인스턴스) │
   │     └─▶ headless-api   :8001   ★ provider 소유              │
   │                                                            │
   │   postgres             :5432   중앙 DB                      │
   │   keycloak             :8081   인증(OIDC)                    │
   │   central-migrate              마이그레이션 러너 (일회성)       │
   └────────────────────────────────────────────────────────────┘
              ▲                                    │
   heartbeat  │  (챔버 → 중앙)          세션 지시    │  (중앙 → 챔버)
              │                                    ▼
   ┌────────────────────────────────────────────────────────────┐
   │  챔버 PC (여러 대)                                            │
   │     세션 노드   :9000   ★ provider 소유                       │
   │     계측기 · DUT(피시험 단말)                                  │
   └────────────────────────────────────────────────────────────┘
```

**★ 표시 둘이 provider 가 만드는 것**입니다. 나머지는 플랫폼이 소유합니다.

### 실측 — 서비스는 **7개**입니다

```bash
$ python3 -c "import yaml;print(list(yaml.safe_load(open('infra/docker-compose.central.yml'))['services']))"
['postgres','keycloak','headless-api','central-migrate','platform-api','platform-api-node','web']
```

**7개 전부 `profiles` 가 없습니다** — 즉 조건부가 아니라 항상 기동합니다.

> 🔴 **오늘 거짓인 서술.** `docs/operations/provider-integration-guide.md`,
> `docs/operations/headless-migration-guide.md`,
> `docs/operations/central-pc-fcc-platform-verification-guide.md` 세 곳이
> **「중앙 5개 서비스」**라고 적습니다. 실측은 **7개**입니다.
> `central-migrate`(일회성 러너)와 `platform-api-node`(평문 HTTP 중앙에서
> 브라우저와 노드의 인증 모드가 반대라 갈라놓은 두 번째 인스턴스)가 나중에
> 붙었고 문장이 따라오지 않았습니다.
>
> ⚠️ **다만 그 문서들의 «결론»은 여전히 참입니다** — provider 저장소를 실제로
> 필요로 하는 서비스는 `headless-api` **하나**뿐입니다. 틀린 것은 분모입니다.
> 결론이 맞다고 분모를 그냥 두면, 다음 사람이 `docker compose ps` 에서
> 7줄을 보고 **「뭔가 잘못됐다」고 판단합니다.**

### 어느 서비스가 이미지를 «빌드» 하나

| 서비스 | `build:` 스탠자 | 이미지 |
|---|---|---|
| `platform-api` · `central-migrate` · `web` | **있음** | 중앙에서 빌드된다 |
| `headless-api` | **없음** | `${FCC_HEADLESS_IMAGE:-fcc-unlicensed-headless-api:latest}` |
| `platform-api-node` | 없음 | `platform-api` 와 **같은 이미지** 재사용 |
| `postgres` · `keycloak` | 없음 | 공식 이미지 |

⚠️ **`headless-api` 에 `build:` 가 없는 것이 이 설계의 핵심입니다.** 있으면 중앙 PC 에
provider 의 소스 트리가 있어야 하고, 그러면 「분야 지식은 provider 안에만」이
무너집니다. **provider 의 코드는 이미지 파일로만 중앙에 건너갑니다.**

---

## 4. 계측 프로그램은 중앙과 어떻게 말하는가

여기가 트랙 A 의 심장입니다. **연결은 두 방향이고, 계약이 각각 다릅니다.**

```
 ┌─ 방향 ①  챔버 → 중앙 ─────────────────────────────────────────┐
 │   POST /platform/chambers/heartbeat                          │
 │   "나 살아 있다 / 지금 측정 중이다 / 진행률은 이만큼이다"           │
 │   → 중앙은 이것이 끊기면 그 챔버를 OFFLINE 으로 «파생» 한다        │
 └──────────────────────────────────────────────────────────────┘

 ┌─ 방향 ②  중앙 → 챔버 ─────────────────────────────────────────┐
 │   POST http://<챔버 LAN IP>:<노드 포트>/session/start            │
 │   "이 계획으로 측정해라"                                        │
 │   → 챔버 노드가 계측기를 잡고 실제 시험을 수행한다                  │
 └──────────────────────────────────────────────────────────────┘
```

### 챔버 노드가 구현해야 하는 계약 — **6 경로**

`docs/api/session-api.openapi.json` 실측:

| 메서드 | 경로 | 하는 일 |
|---|---|---|
| `GET` | `/session/info` | API 버전 + 지원 오퍼레이션 카탈로그 |
| `GET` | `/session/is-running` | 지금 측정 중인가 |
| `GET` | `/session/progress` | 진행률 (완료·전체·비율) |
| `POST` | `/session/start` | 측정 시작 |
| `POST` | `/session/stop` | 측정 중지 |
| `POST` | `/session/workbook` | `.xlsx` 시험계획 업로드 → **불투명 핸들** 반환 |

⚠️ **`/session/workbook` 의 설계를 그대로 따르십시오.** 계약 문서가 명시합니다 —
*"노드가 바이트를 어디에 둘지 정한다. 핸들이 `start_session` 이 받는 유일한 것이고,
따라서 클라이언트가 파일시스템 경로를 이름으로 부르는 일이 없다."*
경로를 주고받게 만들면 중앙이 챔버의 디스크 구조를 알게 되고, 그 순간
분야 중립성이 깨집니다.

⚠️ **경로를 하드코딩하지 마십시오.** 중앙 쪽 어댑터
(`fcc_test_platform/infrastructure/adapters/driven/chamber_proxy_adapter.py`)는
그 경로들을 **계약에서 파생**하고 하드코딩을 금지한다고 주석에 적습니다.
당신의 노드도 같은 계약을 SSOT 로 삼아야 두 쪽이 함께 움직입니다.

### `base_url` 이 틀렸을 때의 증상을 기억하십시오

`chamber_nodes.base_url` 은 **중앙이 그 챔버를 부를 때 쓰는 주소**입니다.
틀리면 — heartbeat 는 **챔버→중앙** 방향이라 **성공**하고, 중앙→챔버 forward 만
막힙니다. 즉 화면에는 **「노드는 살아 있는데 아무것도 안 온다」**로 보입니다.
**「연결 안 됨」이 아니라 「연결됐는데 조용함」이 이 결함의 얼굴입니다.**

### 챔버는 «분야 중립» 입니다

`chamber_nodes` 표의 최종 컬럼 **11개** (실측: `migrations/001` 의 CREATE +
모든 `ALTER … ADD COLUMN` 을 합산):

```
id · chamber_id · name · base_url · enabled · heartbeat_ttl_seconds
artifact_storage_root · equipment_config_json · accepts_web_sessions
created_at · updated_at
```

**시험 종류를 적는 칸이 없습니다.** 한 챔버에서 여러 분야의 시험을 할 수 있고,
챔버 자격증명은 하나입니다. 분야는 `FCC_CENTRAL_PROVIDER_ID` 라는 **다른 칸**에
있습니다 — 이 둘을 섞는 것이 실장비에서 실제로 일어난 사고입니다
(`90-known-traps.md` §2).

---

## 5. headless API — provider 가 만드는 «시험 지식» 서비스

`docs/api/headless-api.openapi.json` 실측 **36 경로**. 그 목록을 보면 왜 이것이
플랫폼이 아니라 provider 소유인지가 드러납니다:

| 무엇 | 개수 | 왜 분야 지식인가 |
|---|---|---|
| 시험계획 저작 | 16 | *유효한 시험계획 행이란 무엇인가* — FCC 무선과 KC 는 완전히 다르다 |
| 성적서 | 7 | *성적서에 무엇이 들어가는가* |
| 세션 결과 조회 | 5 | 결과·시도·아티팩트·내보내기 |
| 자기 선언 | 4 | `capabilities` · `ui-descriptor` · `api-contract` · `status` |
| 작업(job) | 3 | 생성·조회·중지 |
| 헬스체크 | 1 | `/health` |

⚠️ **`ui-descriptor` 를 주목하십시오.** 웹 화면이 *이 분야에 대해* 무엇을 보여줘야
하는지를 **provider 가 선언**합니다. 그래서 프런트엔드는 플랫폼에 있으면서도
분야 중립일 수 있습니다.

**플랫폼은 이것을 알 수 없고, 알아서도 안 됩니다.** 알게 되면 provider 가 늘 때마다
플랫폼을 고쳐야 합니다.

---

## 6. 어디까지 완료됐나 — 2026-09-07 실측

| 축 | 값 | 뜻 |
|---|---|---|
| 중앙 API 표면 | `platform-api` **65** 경로 | 플랫폼 쪽은 두텁게 서 있다 |
| 챔버 노드 계약 | `session-api` **6** 경로 | 얇고 안정적 |
| DB 마이그레이션 | **35** 개 | 스키마가 여러 차례 자란 상태 |
| 웹 라우트 | **51** 개 | 화면은 대부분 존재한다 |
| 테스트 파일 | **178** 개 | |
| 레인 게이트 선언 실패 | **0** | `delivered_test_run_baseline.json` = `{"lane":…, "baseline":[]}` |
| 사후 평가 기록 | **49** 건 | 실패에서 배운 것이 문서로 남아 있다 |
| **등재된 provider** | **3** — 실물 **1**, 예시 **2** | ← **여기가 오늘의 최전선** |

### 그래서 «우리가 해야 하는 일» 은 무엇인가

```
  오늘:   provider 1개(Unlicensed)만 «실물» 이고, 나머지 둘은 예시 파일이다.
  목표:   두 번째 실물 provider 를 붙인다.
  ⚠️      그 순간 오늘 초록인 검사 하나가 «구조적으로 통과할 수밖에 없어서»
          초록이었다는 사실이 드러난다.
```

실측 — 레지스트리의 아티팩트 셋 전부가 **contracts 레인 안에** 있습니다:

```
fcc-unlicensed-conducted → fcc_test_contracts/artifacts/headless_api_contract.v1.json     (우리 SSOT 자신)
fcc-mmwave-headless      → …/mmwave_headless_api_contract.example.json                   (예시)
fcc-licensed-headless    → …/licensed_headless_api_contract.example.json                 (예시)
```

셋 다 **우리가 발행한 것**입니다. 그래서 배치 검사가 통과합니다.
진짜 provider 는 **자기 저장소에서 자기 구현으로부터** 아티팩트를 export 하고,
그러면 레지스트리는 **이 트리에 없는 파일**을 가리켜야 합니다.

운영자 판정 (2026-08-31): **아티팩트는 발행처에 머문다. provider 가 자기 레포에서
자기 계약을 검사하고, 중앙은 그 결과만 받는다.**

⚠️ 이 판정의 반대편이 **가장 쉽고 가장 나쁜 길**이며, 그 길이 코드에 이미
열려 있습니다 — `90-known-traps.md` §1 을 반드시 읽으십시오.

---

## 7. 이 문서의 수치를 다시 재는 법

```bash
cd fcc-test-platform

# API 경로 수
python3 -c "import json;[print(n,len(json.load(open(f'docs/api/{n}.openapi.json'))['paths'])) for n in ['platform-api','headless-api','session-api']]"

# 중앙 서비스
python3 -c "import yaml;d=yaml.safe_load(open('infra/docker-compose.central.yml'));[print(k, 'build' in v) for k,v in d['services'].items()]"

# 규모
ls migrations/*.sql | wc -l ; ls tests/*.py | wc -l ; ls .claude/evaluations/*.md | wc -l

# provider 등재
python3 -c "import json;d=json.load(open('config/headless_provider_registry.json'));[print(p['provider_id'], p['contract_artifact']) for p in d['providers']]"

# 레인 게이트 (pre-push·CI 가 부르는 것과 «같은» 진입점)
python3 scripts/lane_check.py --root .
```

⚠️ **테스트를 직접 돌릴 때는 디스플레이가 필요합니다** — 없으면 core dump 가 납니다:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/ -q
```
