# provider 화 — 모노레포가 이미 겪은 것

> **출처는 «1차 사료»입니다.** 이 문서의 모든 항목은
> `FCC_mobile_test_automation`(첫 번째 provider)의 **평가 기록 759건 · 커밋 5,209개 ·
> 인계 문서 · 마이그레이션 스킬**에서 회수했습니다. 요약이 아니라 **인용**입니다.
>
> ⚠️ **회수한 이유**: 그 저장소는 **private** 입니다. 두 번째 provider 팀은 그것을 볼 수
> 없고, 그래서 **같은 실수를 처음부터 다시 하게 됩니다.** 이 문서가 그 통로입니다.
>
> ⚠️ **그리고 회수하면서 «오늘의 실측»으로 정정했습니다** — 원문 그대로 옮기면
> 낡은 경로와 낡은 라우트를 그대로 물려주게 됩니다(§2-a).

---

## 0. 무엇을 회수했나

| 원본 (모노레포, private) | 무엇 | 이 문서에서 |
|---|---|---|
| `docs/handovers/mmwave-provider-onboarding.md` (232줄) | **두 번째 provider 를 붙이려던 실제 인계문** | §1 · §3 · §5 |
| `.claude/skills/provider-platform-migration/SKILL.md` | 운영 규칙 · **소유 경계** · 구현 순서 | §1 |
| `.../references/mmwave-handoff-prompts.md` (5,263 bytes) | **복붙 프롬프트 8개** | §2 |
| `.claude/evaluations/` 중 provider 계열 **75건** | 실수의 계급 | §4 |

---

## 1. 소유 경계 — 원문 그대로

이것이 **모든 판단의 출발점**입니다. 스킬 원문:

```
Shared platform owns:            Providers own:
  중앙 DB 공통 스키마               측정 알고리즘
  provider 레지스트리                계측기·keystring·장치 제어
  인증/RBAC                         로컬 복구 · 오프라인 큐
  프로젝트·모델·시료·세션·작업 공통 모델   provider 고유 성적서 처리
  결과 열람 셸                       provider 고유 «결과 → 봉투» 매핑
  아티팩트 열람 셸
  성적서 요청/상태/다운로드 워크플로
```

### 🔴 금지 — 원문 네 줄

```
- platform 이 provider 구현 모듈을 import 하는 것
- fcc-test-contracts 가 «있는데도» provider 레포가 계약 DTO 를 «복사»하는 것
- Unlicensed/mmWave/licensed 측정 알고리즘을 공유하는 것
- 애플리케이션 로직에 아티팩트 루트를 «하드코딩»하는 것
```

### 운영 규칙 — 계약이 «먼저»입니다

```
contract  →  provider adapter  →  platform integration  →  end-to-end verification
```

> *"프런트엔드 페이지나 provider 내부를 «바꾸는 것으로 시작하지 마라».
> 계약과 provider 경계를 먼저 안정시켜라."*

⚠️ **핵심 문장 하나** (인계문 §Core Message):
> *"당신은 웹 시스템 전체를 만들라는 요청을 받은 것이 아닙니다.
> **공유 웹 플랫폼이 공통 계약을 통해 제어할 수 있는 provider 를** 만드는 것입니다."*

---

## 2. 복붙 프롬프트 8개 — 회수본 (오늘의 실측으로 정정)

원본은 mmWave 를 대상으로 쓰였습니다. **당신 분야 이름으로 바꿔 쓰십시오.**

### ⚠️ 2-a. 원본에서 «고친» 것 — 그대로 쓰면 틀립니다

실측으로 대조한 결과 **셋이 낡았습니다**:

| 원본 | 오늘 | 근거 |
|---|---|---|
| `POST /headless/jobs/{job_id}/stop` | **`{job_uuid}`** | 계약 v0.1.22 — *"측정 job 을 «불투명 핸들»로"* |
| `GET /headless/jobs/{job_id}` | **`{job_uuid}`** | 〃 |
| 「`docs/api/provider_contract_v1.md` 를 읽어라」 | **`fcc_test_contracts/artifacts/provider_contract_v1.md`** (설치된 패키지 안) | 2026-08-31 레인 분리 |
| 「`repository_split_adr.md` 를 읽어라」 | 🔴 **모노레포에만 있음(private)** — 못 읽습니다. 대신 `04-why-two-repos.md` | — |
| `python scripts/check_headless_provider_registry.py` | 🔴 **체커는 contracts 소유, 명부는 platform 소유** | 2026-08-31 판정 |

```bash
# 오늘 유효한 라우트 목록을 «직접» 확인하는 법
python3 -c "import json,urllib.request as u; \
  print(*sorted(json.load(u.urlopen('https://raw.githubusercontent.com/junnv93/fcc-test-platform/main/docs/api/headless-api.openapi.json'))['paths']),sep='\n')"
```

---

### 📋 프롬프트 1 — 경계를 «본다» (코드는 안 고친다)

```
이 저장소를 FCC 중앙 플랫폼의 provider 로 만들 준비를 한다.
지금은 «아무것도 고치지 말고» 경계만 파악해줘.

읽을 것:
  - docs/PROVIDER-KICKOFF.md (내려받아 둔 것)
  - 설치된 fcc_test_contracts/artifacts/provider_contract_v1.md
  - 설치된 fcc_test_contracts/artifacts/provider_onboarding.md

제약:
  - 아직 코드를 고치지 마라.
  - 측정 알고리즘을 플랫폼 쪽으로 옮기자는 제안을 하지 마라.
  - 이미 있는 job / session / result / artifact / report 개념을 찾아라.

내놓을 것:
  1. «비공개로 남아야 하는» 모듈 — 측정·계측기 제어·분야 성적서 로직
  2. «어댑터/API 경계가 될» 모듈
  3. 구현 전에 내가 답해야 하는 «빠진 정보»
  4. 좁은 첫 PR 계획 하나

⚠️ 이미 어댑터 성격의 층이 있으면 그것을 «먼저» 늘려라.
   측정 내부를 건드리는 것은 마지막 수단이다.
```

### 📋 프롬프트 2 — provider 메타데이터 + `/headless/api-contract`

```
provider 메타데이터와 GET /headless/api-contract 를 붙여줘.

  provider_id     = <운영자가 배정한 자연키>     ⚠️ UUID 아님
  product_line    = <제품군>
  contract_family = fcc-conducted-headless

제약:
  - 측정 실행 로직을 바꾸지 마라.
  - ⚠️ 공유 계약 패키지가 있으면 계약 DTO 를 «복사하지» 마라 — import 해라.
    (스킬 원문이 금지 목록에 이름으로 적은 항목이다)
  - 계약 코드는 의존을 가볍게 유지해라.

⚠️ provider_id 를 문자열 리터럴로 박지 마라. 계약 SSOT 에서 «읽어서» 쓰고,
   값이 «있으면» 통과시키지 말고 SSOT 와 «대조» 해라.
   계약 패키지를 못 읽으면 「통과」가 아니라 「검증하지 못했다」로 말해라.

검증: 계약 JSON 을 export 하고 적합성 검사기를 돌려라.
```

### 📋 프롬프트 3 — `/headless/capabilities`

```
GET /headless/capabilities 를 구현해줘.

실제로 지원하는 기술·시험군·아티팩트 종류·성적서 종류와,
«지원하지 않는 것»을 돌려줘라.

🔴 provider 가 실행할 수 없는 기능을 광고하지 마라.
   그리고 아직 배선되지 않은 라우트를 «live» 로 광고하지 마라 —
   점진 온보딩 중에는 /headless/api-contract 가 «실제로 배선된 부분집합»만
   발행해도 된다.

⚠️ 「가짜 성공을 발명하지 마라」(원문: Do not invent fake success).
   못 하는 것은 명시적 unsupported 로 답해라.
```

### 📋 프롬프트 4 — 작업(job) 어댑터

```
provider 쪽 job 생성/조회/중지를 붙여줘.

  POST /headless/jobs
  GET  /headless/jobs
  GET  /headless/jobs/{job_uuid}          ⚠️ job_id 가 아니라 job_uuid 다
  POST /headless/jobs/{job_uuid}/stop

제약:
  - 실행 로직은 기존 서비스 «안»에 둬라. 어댑터는 번역만 한다.
  - 기존 시스템이 지원하면 idempotency key 를 써라.

⚠️ 공통 job 필드의 «의미»를 지켜라:
   - assigned_worker_id 는 «작업자 귀속»이다. 종료 스냅샷에서도 보존해라
     (명시적으로 미할당 상태로 재큐잉되는 경우만 예외).
   - lease_expires_at 은 «벽시계 ISO 타임스탬프»다.
     🔴 monotonic 마감을 그 필드로 «위장하지» 마라 — 벽시계 만료가 없으면
        provider 고유 추가 속성으로 따로 내보내라.

검증: 어댑터가 UI 전용 코드를 import 하지 않음을 증명하는 경계 시험을 넣어라.
```

### 📋 프롬프트 5 — 결과 봉투(envelope) 어댑터

```
우리 결과 레코드를 공통 봉투로 매핑해줘.

  GET /headless/sessions/{session_id}/results

  {
    "provider_id": "...", "session_id": "...", "result_id": "...",
    "test_name": "...", "technology": "...",
    "condition": { ... },     ← provider 소유 JSON
    "result":    { ... },     ← provider 소유 JSON
    "verdict": "Pass", "measured_at": "2026-05-14T00:00:00Z"
  }

⚠️ condition 과 result 는 «우리 것»이다. 플랫폼은 필터·표시에 쓸
   공통 필드만 안정적이면 된다.

🔴 다른 provider 의 컬럼 이름을 우리 내부에 «억지로 밀어 넣지» 마라
   (원문: Do not force Unlicensed column names into mmWave internals).

검증: 빈 세션과 «없는» 세션의 동작도 시험해라 — 둘은 다른 답이어야 한다.
```

### 📋 프롬프트 6 — 아티팩트 메타데이터 어댑터

```
플롯·스크린샷·트레이스 메타데이터를 공통 계약으로 내보내줘.

  GET /headless/sessions/{session_id}/artifacts

규칙:
  - 바이너리는 DB «밖»에 둔다.
  - relative_path · artifact_type · original_filename · sha256 · byte_size ·
    storage_backend · session_id · (가능하면) result_id 를 돌려준다.

🔴 회사 파일서버 루트를 «코드에» 하드코딩하지 마라 — 설정으로 받아라.
   내부에서 파일을 찾을 때 절대 경로를 «쓰는» 것은 되지만,
   플랫폼에 «보내는» 메타데이터에는 절대 경로가 나타나면 안 된다.
```

### 📋 프롬프트 7 — 성적서 어댑터

```
성적서 생성을 공통 요청 계약으로 노출해줘.

  POST /headless/sessions/{session_id}/reports
  GET  /headless/reports/{request_id}

제약:
  - 성적서 «처리»는 우리 것으로 남긴다. 어댑터는 요청을 받아 기존 처리기를 부른다.
  - 아직 준비 안 됐으면 «명시적으로» unsupported 를 돌려줘라.
```

### 📋 프롬프트 8 — 적합성·경계 최종 점검

```
최종 검증을 돌려줘.

  - 계약 적합성 검사기
  - provider 레지스트리 검사기 (contracts 레인의 것을 쓴다 — §2-a)
  - provider API 어댑터 단위 시험
  - 플랫폼이 우리 내부를 import 하지 않는지 import 검사

내놓을 것: 통과/실패 요약 · 남은 unsupported 목록 · 남은 것이 있으면 다음 PR 하나
```

---

## 3. PR 체크리스트 — 인계문 원문

```
[ ] provider 메타데이터가 맞다
[ ] API contract 엔드포인트가 동작한다
[ ] capabilities 가 «실제» 지원을 서술한다
[ ] job 시작/상태/중지가 구현됐거나 «명시적으로» unsupported 다
[ ] 결과 봉투가 «실제» 출력을 매핑한다
[ ] 아티팩트 메타데이터가 상대 경로와 해시를 쓴다
[ ] 성적서 요청 경로가 구현됐거나 «명시적으로» unsupported 다
[ ] 적합성 검사기가 통과한다
[ ] 다른 provider 의 import 가 «없다»
[ ] 플랫폼이 우리 내부를 «import 하지 않는다»
```

---

## 4. ⚠️ 실수의 «계급» — 평가 기록에서 회수한 것

각 항목은 실제 평가서 인용입니다. **문장이 아니라 «형태»를 기억하십시오.**

### 4-1. 경계 «오귀속» — 분야 중립인 것을 provider 로 분류했다

> `shared-kernel → provider @ src` 크로싱 **18건은 하나의 축이 아니었다.**
> 15건은 진짜 분야 지식이고, **3건은 «오귀속»**이었다 — 로거 이름 조립(1건, 분야 중립)과
> Port 의 역의존(2건). … 오귀속 3건을 정정하고 … 크로싱을 **18→13** 으로 줄인다.

**「provider 폴더에 있으니 분야 지식이다」는 틀립니다.** 이름 조립·로깅·페이지네이션 같은
것은 그 안에 있어도 **분야 중립**입니다.

> ### 🧭 자문
> **「이것이 다른 분야에서도 «똑같이» 참인가?」** 참이면 커널로 가야 합니다.

### 4-2. 계약을 실제보다 «좁게» 선언했다 — 그리고 그 검사가 공허했다

`contract-nullability-underdeclaration` 평가서:

> **판정 넷 중 둘이 «조용히 삭제 가능»했다.** … 판정 4에 **자기 offender 가 없었다** —
> no-op 으로 만들어도 `24 passed`. 그런데 판정 4는 **producer 타입 드리프트의 «유일한»
> 킬러**다. *아무도 볼 수 없는 유일한 팔이 아무것도 대신할 수 없는 팔이었다.*

⚠️ 그리고 **비-공허성 검사 자체가 못 본 이유가 구조적**이었습니다 —
그것은 헬퍼를 **손으로 만든 집합**에 먹였고, **호출자가 진실을 건네는지는 별개**입니다.

> ### 🧭 자문
> **「내 계약 검사를 no-op 으로 만들어도 초록인가?」** 초록이면 그 검사는 없는 것입니다.
> 그리고 **「헬퍼가 거짓말을 거부하는 것」과 「호출자가 진실을 건네는 것」은 다른 명제**입니다.

### 4-3. 경계 기본값이 «정직하지» 않았다 — 400 이어야 할 것이 500

`headless-boundary-default-honesty` 평가서:

> **요청 거부가 400 이 아니라 500 으로 나간다.** … 옛 404 보다 **더 나쁜 방향의 거짓말**
> (시험원 잘못을 «서버 고장»이라고 말한다)이고, 그것이 이 웨이브가 없애겠다고 선언한
> 바로 그 형태다.

⚠️ 같은 평가서: **정공은 착지했으나 봉인되지 않았다** — 변이 13종 중 **8종이 살아남았고**,
커밋의 자가점검은 `PASS` 였지만 **신규 라우트 구동 테스트가 0건**이었습니다.

> ### 🧭 자문
> **「이 오류 코드는 «누구 잘못»이라고 말하는가?」** 그리고 —
> **「내 변경을 되돌리는 변이를 만들면 검사가 빨개지는가?」**

### 4-4. 「멈추지 않은 stop」을 stop 이라 부르지 마십시오

`boundary-plumbing-and-node-liveness` 평가서 D3:

> **`stop` 타임아웃 기각** — 타임아웃으로 거절하는 stop 은 **«멈추지 않은 stop»**이고,
> 그것이 없애려던 결함이다.

**그리고 같은 문서 D7**: *"transport 이동이 아니라 «인자 제거» — **정보 손실과 전송 은닉은
다른 성질**이고 섞으면 봉인이 무엇을 증명했는지 흐려진다."*

### 4-5. 「의존을 늘려서」 숫자를 0으로 만들지 마십시오

`contracts-lane-dependency-free` 평가서:

> `depends_on += ['shared-kernel']` **로도 3 키가 0 이 된다. 하지 않았다**:
> import 하나도 옮기지 않고 같은 숫자를 **«공허하게» 0 으로 만들며** … 새 provider 팀에
> **측정 도메인 380 모듈을 통째로 넘긴다.**

⚠️ **`fcc-test-contracts` 의 의존성이 오늘도 `[]` 인 것은 그 결정의 결과**이고,
`test_contracts_lane_still_declares_no_dependencies` 가 그것을 봉인합니다.

> ### 🧭 자문
> **「이 숫자가 좋아진 것은 «내가 고쳐서»인가, «집합을 넓혀서»인가?」**

### 4-6. 절차 실수도 기록됩니다 — 그리고 그것을 «부하»로 오진했습니다

> ⚠️ **첫 회귀에서 7 failed 가 났고, 그 중 셋은 «제 절차 실수»였다.**
> 전량 회귀를 백그라운드로 띄운 **바로 그 시점에** 독립 평가를 띄우고
> *"머신에 부하를 걸어라"* 고 지시했다.
>
> ### ⚠️ 3-패스가 봉인이 못 잡은 결함을 잡았다 — **그리고 제가 그것을 부하로 오진했다**

**red 를 「부하 탓」으로 돌리기 전에, 그 red 가 «진짜 결함»일 가능성을 먼저 배제하십시오.**

### 4-7. 「지시받은 파일이 존재하지 않는다」를 말하십시오

> ⚠️ 지시받은 3번째 명령의 `tests/test_dev_seed.py` 는 **저장소에 존재하지 않는다**
> (`ERROR: file or directory not found`). 실재하는 형제 4종으로 **대체 실행** — 184 passed.
>
> ⚠️ routine 레인 전체는 **재실행하지 못했다**. 따라서
> **"routine green" 은 이 평가가 «주장하지 않는다»**.

**「재지 못한 것」을 「초록」으로 적지 마십시오.** 무엇을 재지 «않았는지»를 함께 적으십시오.

---

## 5. 런타임 경로는 «설정»이지 코드 상수가 아닙니다

인계문 원문이 이름으로 적은 둘:

```
FCC_HEADLESS_ARTIFACT_ROOTS       세미콜론으로 구분된 파일서버·오브젝트스토리지 루트
FCC_HEADLESS_REPORT_OUTPUT_DIR    성적서 요청이 output_dir 를 생략했을 때의 기본 위치
```

⚠️ 이것이 §1 의 금지 네 줄 중 마지막(**「애플리케이션 로직에 아티팩트 루트를
하드코딩하지 마라」**)의 실현입니다.

---

## 6. 이 문서의 한계 — 명시합니다

* 회수 대상은 **provider 계열 평가서 75건 중 실수 키워드 밀도 상위 6건**입니다.
  나머지 69건은 읽지 않았습니다 — **더 있습니다.**
* 커밋 5,209개 중 **전수 조사는 하지 않았습니다.** 접두사·키워드 스캔입니다.
* 원본 저장소는 **private** 이므로 여기 인용된 것 외에는 확인할 수 없습니다.
  더 필요하면 **모노레포 접근 권한을 요청**하십시오(`05-github-access.md` §2).

⚠️ **그러므로 이 문서의 부재는 「없다」가 아니라 「내가 거기까지 봤다」입니다** —
`90-known-traps.md` §9 가 말하는 그 구분입니다.
