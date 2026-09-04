# Provider 통합 가이드 — headless provider 를 중앙 플랫폼에 붙이기

**대상 독자**: 자기 시험 분야(KC · mmWave · licensed …)의 headless 서비스를 만들어
이 중앙 플랫폼에 붙이려는 개발자.

**전제**: 당신은 **이 저장소(`fcc-test-platform`)만** 봅니다. 다른 provider 의
저장소는 비공개이고(ADR-0018 D-5) 참조 구현을 받을 수 없습니다. 이 문서는 그
전제에서 필요한 것을 전부 담는 것을 목표로 합니다.

> ⚠️ **이 문서가 존재하는 이유.** 2026-09-04 까지 provider 가 읽어야 할 절차는
> `fcc-test-contracts` 패키지 안의 `provider_onboarding.md` **하나**였고 그것은
> *계약 적합성*만 다뤘습니다. **배포 경로**(이미지를 어떻게 만들어 중앙에 넣는가,
> 챔버 PC 를 어떻게 붙이는가)는 어느 문서에도 없었고, 실제로 첫 실장비 배포에서
> 그 부재가 하루치 비용을 냈습니다. 이 문서는 그 절반을 채웁니다.

---

## 0. 이 문서가 답하는 것 / 답하지 않는 것

| | 어디에 있나 |
|---|---|
| 계약(API 표면)을 어떻게 맞추나 | `fcc-test-contracts` 패키지의 `provider_onboarding.md` |
| **이미지를 어떻게 만들어 중앙에 넣나** | **이 문서 §4** |
| **챔버 PC 를 어떻게 붙이나** | **이 문서 §5** |
| **중앙에 어떻게 등록하나** | **이 문서 §6** |
| 중앙 PC 자체를 어떻게 세우나 | `docs/operations/central-pc-operational-validation-runbook.md` |
| 포트가 왜 그 값인가 | `docs/operations/fcc-central-pc-port-topology.md` |

---

## 1. 지도 — 무엇이 어디서 도는가

```
                    중앙 PC (한 대)
   ┌───────────────────────────────────────────────┐
   │  web (nginx)  :8080   ← 브라우저·챔버의 유일한 입구 │
   │      │                                         │
   │      ├── platform-api    :8002   (플랫폼 소유)   │
   │      └── headless-api    :8001   ← ★ 당신 것    │
   │  postgres · keycloak :8081                      │
   └───────────────────────────────────────────────┘
                    ▲                    │
       heartbeat    │                    │  세션 지시
                    │                    ▼
   ┌───────────────────────────────────────────────┐
   │  챔버 PC (여러 대)                                │
   │    세션 노드  :9000   ← ★ 당신 것                 │
   │    계측기 · DUT                                  │
   └───────────────────────────────────────────────┘
```

**★ 표시 둘이 당신이 만드는 것**입니다. 나머지는 플랫폼이 소유합니다.

⚠️ **중앙 PC 에는 당신의 저장소를 두지 않습니다.** 중앙은 `fcc-test-platform`
하나 + Docker 만 둡니다. 당신의 코드는 **이미지 파일로만** 건너갑니다(§4).
provider 가 늘어도 중앙은 그대로 platform 하나이고, provider 마다 이미지가
하나씩 더 붙을 뿐입니다.

---

## 2. 당신이 만드는 것 셋

| # | 무엇 | 어디서 | 중앙이 보는 것 |
|---|---|---|---|
| ① | **계약 적합성** | 당신 저장소 | 등재된 계약 아티팩트 |
| ② | **headless 이미지** | 당신 저장소 → `docker save` | 이미지 태그 하나 |
| ③ | **챔버 노드** | 당신 저장소 → 챔버 PC | `chamber_nodes` 행 + heartbeat |

셋은 **독립적으로** 만들 수 있지만 **검증은 순서가 있습니다**(§7).

---

## 3. ① 계약 적합성

`fcc-test-contracts` 패키지를 받아 그 안의 `provider_onboarding.md` 절차를
따르십시오. 요지만 옮기면:

- 스키마가 아니라 **예시 계약에서 출발**합니다
  (`fcc_test_contracts/artifacts/*_headless_api_contract.example.json`).
- 검사기를 **당신 CI 에서** 돌립니다 — 중앙은 결과만 받습니다
  (운영자 판정 2026-08-31: provider 가 자기 레포에서 검사한다).

당신의 계약 아티팩트가 준비되면 이 저장소의
`config/headless_provider_registry.json` 에 등재합니다:

```json
{
  "provider_id": "<자연키>",
  "product_line": "<제품군>",
  "contract_family": "<계약 패밀리>",
  "contract_artifact": "fcc_test_contracts/artifacts/<당신 계약>.json"
}
```

⚠️ **`contract_artifact` 가 가리키는 파일이 실제로 존재해야 합니다.** 없으면
레지스트리 **전체**가 로드에 실패하고(`providers: []`) 증상은 *「내 provider 만
안 보인다」* 가 아니라 *「전부 안 보인다」* 입니다.

⚠️ **아티팩트 사본을 이 저장소에 두지 마십시오.** 레지스트리 옆에 두면 폴백
해소로 검사가 통과해 버리고, 그것은 운영자가 2026-08-31 에 기각한 형태입니다.

---

## 4. ② headless 이미지

### 4-0. headless API 가 하는 일 — 왜 **당신이** 만들어야 하나

먼저 이것부터. 「왜 이미지로 넘기나」의 답이 여기서 나옵니다.

**headless API 는 당신 분야의 시험 지식을 서빙하는 서비스입니다.** 중앙 5개 서비스 중
**유일하게 provider 소유**이고, 나머지 넷(nginx · platform-api · postgres · keycloak)은
분야를 모릅니다.

실측(Unlicensed, 계약 아티팩트 `docs/api/headless-api.openapi.json`) — **36 경로**:

| 무엇 | 개수 | 예 |
|---|---|---|
| **시험계획 저작** | 16 | 초안 생성·행 편집·검증·게시·미리보기·내보내기·생성 카탈로그 |
| **성적서** | 7 | 사전점검 · 산출물 목록 · 다운로드 · 자동화 요청 취소·통계 |
| **세션 결과 조회** | 5 | 결과 · 시도 · 아티팩트 · 내보내기 · 성적서 |
| **자기 선언** | 4 | `capabilities` · `ui-descriptor` · `api-contract` · `status` |
| **작업(job)** | 3 | 생성 · 조회 · 중지 |
| 헬스체크 | 1 | `/health` |

⚠️ **이 목록을 보십시오 — 전부 「이 분야에서 무엇이 유효한 시험인가」입니다.**

- *유효한 시험계획 행이란 무엇인가* — FCC 무선과 KC 는 **완전히 다릅니다**
- *이 계획을 게시해도 되는가* — 검증 규칙이 분야마다 다릅니다
- *성적서에 무엇이 들어가는가*
- *웹 화면이 이 분야에 대해 무엇을 보여줘야 하는가* (`ui-descriptor`)

**플랫폼은 이것을 알 수 없고, 알아서도 안 됩니다.** 알게 되면 provider 가 늘 때마다
플랫폼을 고쳐야 합니다.

#### 결정적 실측

중앙 compose 주석이 그 근거를 실측으로 적어 둡니다(2026-09-03, 도는 컨테이너 안에서):

```
sys.path 에서 /app/src 를 빼면
  platform-api   → 휠만으로 조립된다 (OpenAPI 64 경로 동일)
  headless-api   → ModuleNotFoundError
```

**즉 중앙 5개 서비스 중 provider 저장소를 실제로 필요로 하는 것은 이 하나뿐입니다.**
그 하나 때문에 예전에는 이미지 하나가 셋을 겸하며 빌드 컨텍스트를 provider 저장소
루트로 잡고 있었고, 그래서 **중앙 PC 에 provider 저장소가 있어야 했습니다.**

그 하나를 이미지로 떼어내자 중앙은 `fcc-test-platform` 하나만 두면 됩니다.

### 4-1. 그래서 이미지다

중앙 compose 는 당신의 서비스를 **태그로만** 소비합니다:

```yaml
headless-api:
  image: ${FCC_HEADLESS_IMAGE:-fcc-unlicensed-headless-api:latest}
```

`build:` 스탠자가 **없습니다.** 있으면 중앙 PC 에 당신의 소스 트리가 있어야 하고,
그러면 위 §4-0 의 분리가 무의미해집니다.

```
당신 저장소   소스 + 시험 지식  ──build──▶ 이미지 ──save/load──▶ 중앙
중앙 PC       fcc-test-platform + Docker            소스 0줄
```

⚠️ **provider 가 늘어도 중앙은 그대로 platform 하나입니다.** provider 마다 이미지가
하나씩 붙을 뿐이고, 그것이 이 설계가 지키려는 성질입니다.

⚠️ **반대 방향도 참입니다** — `web`(프런트엔드)은 2026-08-31 에 provider 저장소를
**떠나** 플랫폼으로 갔습니다. 화면은 분야 중립이기 때문입니다. headless-api 는 그
거울상이고, 가르는 기준은 하나입니다: **분야 지식이 들어 있는가.**

### 4-2. 당신 저장소에 필요한 것

전용 Dockerfile 하나입니다.

⚠️ **참고할 실물은 이 저장소에서 열 수 없습니다.** Unlicensed 의 것은
`infra/central/Dockerfile.headless` 인데 그 파일은 **provider 저장소**에 있고
그 저장소는 비공개입니다(ADR-0018 D-5). 그래서 베낄 수 있는 형태가 아니라
**만족해야 하는 성질**로 적습니다:

```dockerfile
FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src
# 1. 당신의 소스 + 그것이 실제로 import 하는 것만.
# 2. 진입점 하나 — uvicorn --factory <당신 모듈>:create_app
# 3. 헬스체크는 표준 라이브러리로 (slim 이미지에 curl 이 없다).
# 4. 리비전 라벨 (§4-3).
```

만족해야 하는 것:

| 성질 | 왜 |
|---|---|
| 진입점 **하나** | 플랫폼 진입점까지 담으면 경계가 이미지 안에서 무너진다 |
| 플랫폼 소유 자산 **미포함** | 마이그레이션 러너 등은 platform 이미지가 소유한다 |
| `org.opencontainers.image.revision` 라벨 | 폐쇄망에 레지스트리가 없다(§4-3) |
| compose 가 부르는 **포트·명령과 정합** | `uvicorn --factory <모듈>:create_app --host 0.0.0.0 --port 8001` |

⚠️ **「headless 전용」은 진입점을 뜻하지 의존 폐포를 뜻하지 않습니다.** Unlicensed
의 실물도 `fcc-test-platform` 휠을 **설치합니다** — headless 표면이 그 휠을
모듈 레벨로 import 하기 때문입니다. 의존을 줄이려다 이미지를 깨뜨리지 마십시오.

### 4-3. 빌드 — 커밋 번호를 반드시 새기십시오

```bash
docker build -f infra/central/Dockerfile.headless \
  --build-arg GIT_REVISION="$(git rev-parse HEAD)" \
  -t <당신-이미지>:latest .
```

⚠️ **`GIT_REVISION` 을 빼지 마십시오.** 폐쇄망에는 이미지 레지스트리가 없어
`docker save`/`load` 로 옮기는데, 그러면 **「중앙에 있는 것이 최신인가」** 를 물을
방법이 사라집니다. 라벨이 그 유일한 답입니다:

```bash
docker inspect <이미지> --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
```

⚠️ **`:latest` 만으로는 재빌드 여부를 가릴 수 없습니다.** 실측(2026-09-04):
개발 PC 의 `:latest` 가 하루 전 코드였고, 그 안의 의존성이 낡아 있었는데
**컨테이너는 healthy 였습니다** — 낡은 코드와 낡은 의존성이 서로 정합했기
때문입니다. 아무것도 실패하지 않습니다.

### 4-4. 이관

```bash
# 개발 PC
docker save <당신-이미지>:latest -o headless-api.tar

# [ 파일을 중앙 PC 로 옮김 — USB · 공유 폴더 · scp ]

# 중앙 PC
docker load -i headless-api.tar
docker images <당신-이미지>
```

⚠️ **`docker compose up -d` 만으로는 새 이미지가 안 붙을 수 있습니다.** 같은
태그면 캐시가 조용히 재사용됩니다. `docker load` 후 해당 컨테이너를 **재생성**
하고, §4-3 의 라벨로 실제 리비전을 확인하십시오.

### 4-5. 태그 이름

`config/headless_provider_registry.json` 의 `provider_id` 와 **혼동하지
마십시오** — 이미지 태그는 배포 아티팩트의 이름이고 `provider_id` 는 계약
정체성입니다. 둘이 비슷하게 생겨도 다른 축입니다.

중앙에서는 `.env` 의 `FCC_HEADLESS_IMAGE` 로 태그를 지정합니다.

---

## 5. ③ 챔버 노드

챔버 PC 는 시험을 실제로 수행하는 기계입니다. 중앙과의 관계는 둘뿐입니다:

```
챔버 → 중앙   heartbeat (내가 살아 있다 / 측정 중이다)
중앙 → 챔버   세션 지시 (이 계획으로 측정해라)
```

### 5-1. 챔버는 분야 중립입니다

`chamber_nodes` 표에 **시험 종류를 적는 칸이 없습니다**(실측 11 컬럼):

```
id · chamber_id · name · base_url · enabled · heartbeat_ttl_seconds
artifact_storage_root · equipment_config_json · accepts_web_sessions
created_at · updated_at
```

**한 챔버에서 KC 도 FCC 도 할 수 있고, 챔버 자격증명은 하나입니다.**
분야는 `FCC_CENTRAL_PROVIDER_ID` 라는 **다른 칸**에 있습니다.

⚠️ 챔버 client id 접두사 `fcc-chamber-` 의 `fcc` 는 **시스템 이름**이지 시험
종류가 아닙니다(realm `fcc-dev`, 웹 audience `fcc-platform-frontend` 도 같은
접두사). 이름이 그 사실과 반대로 생겼다는 것이 알려진 부채입니다.

### 5-2. 챔버 자격증명

챔버마다 Keycloak client 하나이고 id 는 **파생**됩니다:

```
chamber_client_id(chamber_id) = "fcc-chamber-" + chamber_id
```

⚠️ **`chamber_id` 를 client id 로 그대로 쓰지 마십시오.** 실장비에서 실제로
그렇게 됐고, 노드가 **존재하지 않는 이름으로** 토큰을 요청했습니다. 증상은
`invalid_client` 인데 그것은 「client 없음」과 「secret 회전」에 **같은 답**이라
사후에 원인을 가릴 수 없고, 화면에는 그저 **Offline** 으로 보입니다.

⚠️ **client 프로비저닝을 두 번 돌리지 마십시오** — secret 이 회전해 이미 배포된
값이 죽습니다. 이미 있으면 admin API 로 현재 값을 읽으십시오.

### 5-3. 등록

```sql
select chamber_id, name, base_url, enabled, accepts_web_sessions from chamber_nodes;
```

`base_url` 은 **중앙이 그 챔버를 부를 때 쓰는 주소**입니다 —
`http://<챔버 PC 의 LAN IP>:<노드 포트>`.

⚠️ **틀리면 진단이 어렵습니다.** heartbeat 는 챔버→중앙 방향이라 **성공**하고,
중앙→챔버 forward 만 막힙니다. 즉 *「노드는 살아 있는데 아무것도 안 온다」* 가
됩니다.

⚠️ `name` 은 사람이 읽는 자유 입력입니다(한글 가능). 다만 §8 의 인코딩 함정을
보십시오.

---

## 6. 중앙에 등록하기 — 값의 출처

| 키 | 값의 주인 | 주의 |
|---|---|---|
| `provider_id` | 계약 레지스트리 | **자연키**입니다. UUID 아님 |
| `FCC_HEADLESS_IMAGE` | 당신의 이미지 태그 | §4-5 |
| `chamber_id` | 운영자 | 접두사와 겹치는 이름은 피하십시오 |
| `base_url` | 챔버 PC 의 실측 주소 | §5-3 |
| OIDC issuer | 중앙 `PUBLIC_HOST` 파생 | §8 |

⚠️ **`provider_id` 는 자연키입니다.** UUID 컬럼에 넣는 어댑터가 있으면 깨집니다
(2026-08 에 실제로 그 회귀가 있었습니다). 중앙 env 짝맞춤은
`scripts/check_central_provider_id_pairing.py` 가 봅니다.

---

## 7. 검증 순서 — 되돌리기 어려운 것 앞에 공짜인 것을 두십시오

```
① 계약 검사       당신 CI          아무것도 안 건드림
② 이미지 빌드     당신 PC          로컬
③ 이미지 load     중앙             되돌릴 수 있음(태그 보존)
④ compose up      중앙             컨테이너만
⑤ 챔버 프로비저닝  챔버 PC          ⚠️ 파일·방화벽·ACL 변경
⑥ 노드 기동       챔버 PC
⑦ 연결 확인       양쪽
```

⚠️ **⑤ 는 반드시 `-ValidateOnly` 를 먼저 돌리십시오.** 실측(2026-09-04):
그 단계가 **여섯 개의 결함을 아무것도 안 건드린 상태에서** 하나씩 드러냈습니다.
그냥 설치했다면 파일이 깔리고 방화벽이 열린 뒤에 실패해, 반쯤 설치된 상태에서
원인을 찾아야 했을 것입니다.

각 단계의 판정:

```
③ docker images <태그>                          + 리비전 라벨 대조
④ docker ps                                     Up 인가
⑥ curl http://<챔버 IP>:<포트>/session/health    {"status":"ok",...}
⑦ chamber_nodes 행 + heartbeat 신선도
```

⚠️ **⑥ 은 반드시 네트워크 주소로 재십시오.** `127.0.0.1` 로만 재면 「떠 있다」와
「밖에서 쓸 수 있다」를 구분하지 못합니다.

---

## 8. 함정 — 실장비에서 실제로 나온 것들

첫 실장비 배포(2026-09-04)에서 나온 것들입니다. **전부 개발 PC 에서는 보이지
않았습니다.**

### 8-1. `:latest` 는 재빌드 여부를 답하지 않는다

낡은 이미지도 최신 이미지도 같은 태그입니다. 그리고 낡은 코드와 낡은 의존성이
서로 정합하면 **컨테이너는 healthy** 입니다. → §4-3 의 리비전 라벨.

### 8-2. 의존성 버전이 이관 전/후를 구별하지 못할 수 있다

`fcc-test-platform 0.1.8` 이 커널 이관 전/후 양쪽에 붙어 있었습니다. **버전이
안 올랐으므로 버전으로 판정할 수 없습니다.** 설치된 패키지의 **내용**을 보십시오.

### 8-3. 공유 레인은 서로를 핀하고 그 핀이 뒤처진다

```
fcc-test-platform 0.1.8  →  fcc-test-contracts 0.1.11 을 요구
requirements 는          →  fcc-test-contracts 0.1.12 를 선언
한 번에 resolve 하면     →  ResolutionImpossible
```

해법은 **분리 설치**입니다 — 서드파티는 정상 resolve, `git+` 레인만 `--no-deps`.
중앙 `Dockerfile.api` 가 그렇게 합니다. ⚠️ 그 규칙이 Dockerfile 안에만 있으면
**Dockerfile 이 없는 레인**(챔버 PC 등)은 그것을 받지 못합니다.

### 8-4. `.well-known` 의 `jwks_uri` 는 물어본 주소의 메아리다

`KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` 라서 **중앙에서 물으면 `localhost` 가
나옵니다.** 그 값을 챔버에 넣으면 노드가 자기 자신에게 JWKS 를 요청합니다.
설정은 형식상 완전하고 값도 그럴듯하며 **런타임에만 틀립니다.**

챔버가 쓸 값은 중앙의 **공개 주소**입니다:
`http://<중앙 IP>:<keycloak 포트>/realms/<realm>/protocol/openid-connect/certs`

### 8-5. 포트는 승인 집합 안에서 고르십시오

회사 방화벽 승인이 **PC 마다 따로** 납니다. 승인 밖 포트를 고르면 노드는 정상
기동하고 heartbeat 도 가는데 **중앙→노드 forward 만** 막힙니다.
→ `docs/operations/fcc-central-pc-port-topology.md`

### 8-6. Windows: BOM 없는 UTF-8 이 ANSI 로 읽힌다

PowerShell 5.1 의 `Get-Content` 는 BOM 없는 파일을 **ANSI 코드페이지**로
읽습니다. 한글 값이 깨지고 — ⚠️ 그것으로 끝이 아닙니다. CP949 의 lead 바이트가
뒤 바이트를 trail 로 삼키므로 **멀티바이트 길이가 홀수면 줄바꿈까지 삼켜**
다음 줄과 병합되고 **키 하나가 통째로 사라집니다.**

읽는 쪽에 `-Encoding UTF8` 을 명시하십시오.

### 8-7. Windows: 콘솔 클릭이 프로세스를 얼린다

포그라운드 콘솔로 서비스를 돌리면, **창을 클릭하는 것만으로** 프로세스가
얼어붙습니다(QuickEdit 기본값). 그러면:

```
TCP 연결   받는다 (OS 가 backlog 에 넣는다)
응답       없다
로그       한 줄도 안 늘어난다
```

**밖에서 보면 「죽지 않았는데 죽은 것」** 이고, 중앙은 살아 있는 챔버를
로테이션에서 뺍니다. 시작 전에 QuickEdit 를 끄거나, 서비스로 돌리십시오.

### 8-8. 관측 불가를 부재의 증거로 쓰지 마십시오

같은 이름의 컨테이너가 여러 기계에서 돌므로 `docker ps` 출력이 완전히 같은
모양입니다. **보고에 측정 기계 주소를 명시하십시오.** 2026-09-04 라운드에서
세 세션이 각자 다른 기계를 재고 같은 이름으로 보고했습니다.

---

## 9. 아직 정해지지 않은 것

이 문서를 읽는 당신이 부딪힐 수 있고, **답이 아직 없는** 것들입니다:

- **이미지 태그 규약** — 버전 축이 정해지지 않았습니다. Unlicensed 는 현재
  `:latest` + 리비전 라벨로 갑니다.
- **적합성 증거 채널** — provider 가 자기 CI 결과를 중앙에 어떻게 제출하는지가
  2026-09-04 에 착지했습니다. 최신 상태는 `config/provider_conformance_evidence/`
  를 보십시오.
- **챔버 노드 서비스화** — 지금은 포그라운드 콘솔입니다(§8-7). 서비스로 옮기면
  계정 교차 배타(계측기 락이 서비스 계정과 시험원 계정 사이에서 성립하는가)가
  선행 조건이 됩니다.
- **접두사 중립화** — §5-1 의 `fcc-chamber-` 문제. 바꾼다면 **다른 provider 가
  붙기 전이 유일하게 싼 시점**입니다.
