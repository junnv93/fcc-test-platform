# provider 화 실행 런북 — 처음부터 끝까지

> **이 문서는 «읽는» 것이 아니라 «따라 하는» 것입니다.**
>
> 다른 문서들은 「무엇을 왜」를 답합니다. 이 문서는 **「지금 무엇을 치고, 다 됐는지 어떻게
> 알고, 언제 다음으로 가는가」**만 답합니다.
>
> ⚠️ **당신 저장소에서 실행합니다.** `fcc-test-platform` 이 아닙니다.

---

## 0. 이 런북의 모양

provider 화는 한 번에 끝나지 않습니다. **의도 다섯 개로 쪼갭니다.**

```
 의도 ①  provider-lane-bootstrap      환경 + 경계 조사        코드 변경 «0»
 의도 ②  headless-contract-surface    계약 · capabilities      2 경로
 의도 ③  headless-job-and-results     작업 · 결과 · 아티팩트    7 경로
 의도 ④  headless-report-surface      성적서                   2 경로
 의도 ⑤  central-deployment           이미지 · 챔버 노드 · 등록  배포
```

⚠️ **왜 다섯으로 쪼개나.** 한 폴더에 `spec.md` 가 둘 필요해 보이면 **스펙을 쪼갤 신호가
아니라 의도를 쪼갤 신호**입니다. 그리고 각 의도는 **혼자서 승인받고 혼자서 착지**할 수
있어야 합니다 — 안 그러면 다섯 달치 작업이 하나의 리뷰로 몰립니다.

### 각 의도가 도는 방식

```
  intent.md  ──▶  동료 승인  ──▶  spec.md  ──▶  동료 승인  ──▶  plan.md  ──▶  코드  ──▶  PR
    «왜»                          «무엇을»                     «어떻게»
```

**초안은 전부 에이전트가 쓰고, 사람은 승인만 합니다.**
규칙 본문 SSOT → `fcc-test-platform:intent/README.md`.
⚠️ 승인 모델은 **당신 팀 인원에 맞게 다시 정하십시오** — 그것은 플랫폼의 결정이지
보편 규칙이 아닙니다.

### 표기

| | 뜻 |
|---|---|
| 📋 | **Claude Code 에 그대로 붙여넣는 멘트** |
| ✅ | **완료 판정** — 이 명령을 «실행해» 결과를 보고 판단합니다 |
| 🔴 | **여기서 멈추십시오** — 사람의 결정이 필요합니다 |

---

# 의도 ① — `provider-lane-bootstrap`

**목표**: 환경을 세우고, **코드를 한 줄도 안 고친 채** 경계를 파악한다.
**선행**: `07-first-day-scripts.md` §A-0 ~ A-3 (문서 내려받기 · 커널 설치 · Claude 실행)

## ①-1 의도를 쓴다

### 📋

```
이 저장소를 FCC 중앙 플랫폼의 provider 레인으로 만들려고 한다.
docs/PROVIDER-KICKOFF.md 와 docs/KNOWN-TRAPS.md 를 읽었다.

먼저 intent 를 써줘. 이 저장소에는 아직 intent/ 폴더가 없으니 함께 만든다.
서식은 fcc-test-platform 의 intent/_templates/intent.md 를 따른다
(raw.githubusercontent.com 에서 받아도 된다 — 그 레포는 public 이다).

슬러그: provider-lane-bootstrap

Problem 에 쓸 것:
  우리 측정 프로그램은 지금 사람이 GUI 로 돌린다. 중앙 웹 플랫폼이 이것을
  제어하려면 공통 계약을 만족하는 API 표면이 필요한데, 우리 코드에 그 경계가
  어디인지 아무도 «문서로» 답할 수 없다.

⚠️ Open questions 를 「없음」으로 비우지 마라 — 지금 단계에서는 모르는 것이 많은
   것이 «정상»이고, 그것을 적는 것이 이 의도의 산출물이다.

⚠️ Success criteria 를 「경계를 파악한다」로 쓰지 마라 — 그것은 못 잰다.
   「비공개로 남을 모듈 목록과 어댑터가 될 모듈 목록이 파일 이름으로 적혀 있다」
   처럼 «셀 수 있게» 써라.
```

### ✅ 완료 판정

```bash
ls intent/provider-lane-bootstrap/intent.md     # 있어야 한다
grep -c '^## ' intent/provider-lane-bootstrap/intent.md   # 6 (여섯 절)
grep -A3 'Open questions' intent/provider-lane-bootstrap/intent.md   # 비어 있으면 안 된다
```

🔴 **여기서 멈추고 동료 한 분에게 보여 주십시오.** 「이 문제가 진짜인가」는 그 일을
같이 하는 사람이 판단합니다. **자기 의도는 자기가 승인하지 않습니다.**

## ①-2 경계를 «본다» (승인된 뒤에)

### 📋

```
intent/provider-lane-bootstrap/intent.md 가 승인됐다.
이제 spec.md 를 쓰기 «전»에, 실제로 이 저장소의 경계를 조사해줘.

⚠️ 아직 코드를 고치지 마라. 조사만 한다.
⚠️ 측정 알고리즘을 플랫폼 쪽으로 옮기자는 제안을 하지 마라.

찾을 것:
  1. 이미 있는 job / session / result / artifact / report 개념
  2. «비공개로 남아야 하는» 모듈 — 측정·계측기 제어·분야 성적서 로직
  3. «어댑터/API 경계가 될» 모듈
  4. 이미 어댑터 성격의 층이 있나 — 있으면 그것을 «먼저» 늘린다

내놓을 것: 위 넷을 «파일 이름으로». 그리고 내가 답해야 하는 빠진 정보 목록.
```

### ✅ 완료 판정

에이전트가 내놓은 목록이 **파일 이름**을 갖고 있어야 합니다. 「measurements 쪽」이
아니라 `measurements/obw.py` 처럼.

## ①-3 spec → plan → 착지

### 📋

```
조사 결과로 spec.md 를 써줘. 서식은 intent/_templates/spec.md 를 따른다.

⚠️ §2 「범위 밖」에 «측정 알고리즘 변경»을 명시적으로 넣어라.
⚠️ §4 「영향받는 경계」에 어느 모듈이 공개 표면이 되고 어느 것이 비공개로 남는지
   파일 이름으로 적어라.

그 다음 plan.md 를 쓰고, 이 의도의 산출물을 만들어라:
  - CLAUDE.md (200줄 이하)
  - .claude/rules/ (paths: 조건부)
  - intent/ (서식 포함)
  - .claude/skills/ (계약 검사 · 이미지 빌드 절차)

⚠️ CLAUDE.md 에 반드시 넣을 것 넷:
   - 이 저장소는 provider 레인이고 «분야 지식의 주인»이다
   - 중앙에 우리 소스를 두지 않는다. 이미지로만 간다
   - 계약 아티팩트는 «여기서» 발행한다. 중앙 트리에 복사하지 않는다
   - provider_id 는 계약 SSOT 에서 «읽어서» 쓴다
```

### ✅ 완료 판정

```bash
wc -l CLAUDE.md                       # 200 이하
ls .claude/rules/ .claude/skills/
head -5 .claude/rules/*.md | grep -c 'paths:'    # 경로 규칙에는 paths: 가 있어야 한다
```

⚠️ **`/context` 로 `CLAUDE.md` 가 «실제로 로드됐는지» 확인하십시오.**
「파일이 있다」와 「로드됐다」는 다른 명제입니다.

---

# 의도 ② — `headless-contract-surface`

**목표**: `/headless/api-contract` 와 `/headless/capabilities` 를 세운다.
**왜 이것이 먼저인가**: 스킬 원문 — *"프런트엔드나 provider 내부를 바꾸는 것으로
시작하지 마라. **계약과 provider 경계를 먼저 안정시켜라.**"*

## ②-1 의도

### 📋

```
intent/_templates/intent.md 서식으로 새 intent 를 만들어줘.
슬러그: headless-contract-surface

Problem:
  중앙 플랫폼은 provider 가 «무엇을 할 수 있는지»를 물어볼 방법이 없다.
  오늘 우리 프로그램은 자기 능력을 기계가 읽을 수 있는 형태로 선언하지 않는다.

Success criteria 에 쓸 것:
  - GET /headless/api-contract 가 계약 검사기를 통과하는 문서를 돌려준다
  - GET /headless/capabilities 가 «실제» 지원 목록을 돌려주고,
    지원하지 않는 것을 명시적으로 표시한다
  - 두 라우트를 도는 검사가 CI 에 붙어 있다
```

## ②-2 구현

### 📋

```
provider 메타데이터와 두 라우트를 붙여줘.

  provider_id     = <운영자가 배정한 자연키>     ⚠️ UUID 아님
  product_line    = <제품군>
  contract_family = fcc-conducted-headless

⚠️ provider_id 를 문자열 리터럴로 «박지» 마라. 계약 SSOT 에서 읽어서 쓰고,
   값이 «있으면» 통과시키지 말고 SSOT 와 «대조» 해라.
   계약 패키지를 못 읽으면 「통과」가 아니라 「검증하지 못했다」로 말해라.

⚠️ 공유 계약 패키지가 있으면 계약 DTO 를 «복사하지» 마라 — import 해라.
   (스킬 원문이 금지 목록에 이름으로 적은 항목이다)

/headless/capabilities:
🔴 provider 가 실행할 수 없는 기능을 광고하지 마라.
🔴 아직 배선 안 된 라우트를 «live» 로 광고하지 마라 — 점진 온보딩 중에는
   api-contract 가 «실제로 배선된 부분집합»만 발행해도 된다.
⚠️ 「가짜 성공을 발명하지 마라」. 못 하는 것은 명시적 unsupported 로 답해라.
```

### ✅ 완료 판정 — **실행해서 봅니다**

```bash
# 서비스를 띄우고
curl -s http://127.0.0.1:8001/headless/api-contract | python3 -m json.tool | head -20
curl -s http://127.0.0.1:8001/headless/capabilities | python3 -m json.tool | head -20

# 계약 검사기 (contracts 패키지 안의 절차를 따른다)
python3 -c "import fcc_test_contracts,pathlib;print(pathlib.Path(fcc_test_contracts.__file__).parent/'artifacts'/'provider_onboarding.md')"
```

⚠️ **`curl` 을 `127.0.0.1` «만»으로 재지 마십시오** — 나중에 중앙이 부를 때는
네트워크 주소입니다. 둘은 다른 질문입니다.

## ②-3 ⚠️ 검사에 «이빨»이 있는지 확인

### 📋

```
방금 만든 계약 검사에 «일부러 깨진 입력»을 주입해서 빨개지는지 확인해줘.

⚠️ 그 주입이 «착지했는지» 부터 확인해라 — 공허한 주입은 성공한 주입과 같은 모양이다.
⚠️ 검사를 no-op 으로 만들어도 초록이면, 그 검사는 없는 것이다.

첫 provider 의 기록: 「판정 넷 중 «둘이 조용히 삭제 가능»했다. 유일한 킬러에
자기 offender 가 없었다」 — 같은 자리에 서지 마라.
```

---

# 의도 ③ — `headless-job-and-results`

**목표**: 작업 3 + 결과 5 + 아티팩트 = **7 경로**.

## ③-1 의도

### 📋

```
슬러그: headless-job-and-results

Problem:
  중앙이 우리 프로그램에 측정을 «시킬» 방법과, 그 결과를 «가져갈» 방법이 없다.
  오늘은 사람이 GUI 를 눌러 돌리고 결과 파일을 손으로 옮긴다.

⚠️ Constraints 에 넣을 것:
  - 실행 로직은 기존 서비스 «안»에 남는다. 어댑터는 번역만 한다
  - 바이너리(플롯·트레이스)는 DB 밖에 둔다
  - 회사 파일서버 루트를 «코드에» 하드코딩하지 않는다 (설정으로 받는다)
```

## ③-2 작업(job) 어댑터

### 📋

```
provider 쪽 job 생성/조회/중지를 붙여줘.

  POST /headless/jobs
  GET  /headless/jobs
  GET  /headless/jobs/{job_uuid}          ⚠️ job_id 가 아니라 job_uuid 다
  POST /headless/jobs/{job_uuid}/stop

⚠️ 공통 job 필드의 «의미»를 지켜라:
   - assigned_worker_id 는 «작업자 귀속»이다. 종료 스냅샷에서도 보존해라
   - lease_expires_at 은 «벽시계 ISO 타임스탬프»다
     🔴 monotonic 마감을 그 필드로 «위장하지» 마라 — 없으면 provider 고유
        추가 속성으로 따로 내보내라

🔴 stop 이 타임아웃으로 «거절»하게 만들지 마라.
   첫 provider 의 판정: 「타임아웃으로 거절하는 stop 은 «멈추지 않은 stop»이다」
```

### ✅ 완료 판정

```bash
# 어댑터가 UI 전용 코드를 import 하지 않는지 — «실행해서» 확인
python3 -c "
import ast,pathlib,sys
UI={'sidebar','ui','gui','tkinter','PySide6','PyQt5'}
bad=[]
for p in pathlib.Path('<어댑터 폴더>').rglob('*.py'):
    for n in ast.walk(ast.parse(p.read_text())):
        if isinstance(n,(ast.Import,ast.ImportFrom)):
            mods=[a.name for a in n.names] if isinstance(n,ast.Import) else [n.module or '']
            for m in mods:
                if m.split('.')[0] in UI: bad.append((str(p),m))
print('UI import',len(bad),bad[:5]); sys.exit(1 if bad else 0)"
```

⚠️ **grep 이 아니라 AST 로 물으십시오.** 줄 단위 스캔은 여러 줄로 쪼개진 import 를
못 보고, **「없다」와 「못 봤다」의 출력이 같습니다.**

## ③-3 결과 봉투 · 아티팩트

### 📋

```
결과와 아티팩트 라우트를 붙여줘.

  GET /headless/sessions/{session_id}/results
  GET /headless/sessions/{session_id}/artifacts

결과 봉투의 공통 필드는 안정적이어야 한다:
  provider_id · session_id · result_id · test_name · technology · verdict · measured_at
⚠️ condition 과 result 는 «우리 것»이다 — provider 소유 JSON.
🔴 다른 provider 의 컬럼 이름을 우리 내부에 «억지로 밀어 넣지» 마라.

아티팩트 메타데이터:
  relative_path · artifact_type · original_filename · sha256 · byte_size · storage_backend
🔴 절대 경로가 «플랫폼에 보내는» 메타데이터에 나타나면 안 된다.
   (내부에서 파일을 찾을 때 쓰는 것은 된다 — 두 축은 다르다)

검증에 넣을 것:
  - 빈 세션과 «없는» 세션의 동작 — 둘은 «다른» 답이어야 한다
  - 파일이 실재할 때의 sha256 / byte_size
```

---

# 의도 ④ — `headless-report-surface`

### 📋

```
슬러그: headless-report-surface

성적서 생성을 공통 요청 계약으로 노출해줘.
  POST /headless/sessions/{session_id}/reports
  GET  /headless/reports/{request_id}

⚠️ 성적서 «처리»는 우리 것으로 남긴다. 어댑터는 요청을 받아 기존 처리기를 부른다.
⚠️ 아직 준비 안 됐으면 «명시적으로» unsupported 를 돌려줘라 — 가짜 성공 금지.
```

⚠️ **이 의도는 «전부 unsupported» 로 끝나도 됩니다.** 그것이 정직한 상태이고,
`capabilities` 가 그것을 말하면 중앙은 그 기능을 안 부릅니다.

---

# 의도 ⑤ — `central-deployment`

**목표**: 이미지 → 중앙 · 챔버 노드 · 등록.
**전체 절차** → `15-image-handoff-walkthrough.md` (명령 단위 + 체크리스트)

## ⑤-0 ⚠️ 순서가 있습니다 — 되돌리기 비싼 것이 뒤입니다

```
① 계약 검사        당신 CI      아무것도 안 건드림          ← 공짜
② 이미지 빌드      당신 PC      로컬
③ 이미지 load      중앙        되돌릴 수 있음
④ compose up       중앙        컨테이너만
⑤ 챔버 프로비저닝   챔버 PC     ⚠️ 파일·방화벽·ACL 변경     ← 비싸다
⑥ 노드 기동        챔버 PC
⑦ 연결 확인        양쪽
```

🔴 **③~⑦ 은 「사람의 승인이 따로 필요한」 구간입니다.**

## ⑤-1 최종 점검 (프롬프트 8)

### 📋

```
최종 검증을 돌려줘.

  - 계약 적합성 검사기
  - provider 레지스트리 검사기 (⚠️ 체커는 contracts 레인의 것을 쓴다.
    명부는 platform 소유다 — 2026-08-31 판정)
  - provider API 어댑터 단위 시험
  - 플랫폼이 우리 내부를 import 하지 않는지 AST 검사

내놓을 것: 통과/실패 요약 · 남은 unsupported 목록 · 남은 것이 있으면 다음 PR 하나

⚠️ 「재지 못한 것」을 초록으로 적지 마라. 무엇을 재지 «않았는지»를 함께 적어라.
```

## ⑤-2 PR 체크리스트

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

# 막혔을 때 — 물어야 하는 순서

```
① 컨텍스트가 다른가?   훅이 넣은 env · 인터프리터 · stdin · PATH
② 설치본이 다른가?     .pth 가 어느 트리를 가리키나
③ 내가 깨뜨렸나?       대조군과 «이름 집합»을 비교했나
```

⚠️ **③ 을 먼저 물으면, 고칠 것이 없는데 무언가를 고치게 됩니다.**

### 📋 그대로 붙여넣는 진단 멘트

```
검사가 빨갛다. 아래 출력을 보고 판정해줘.

⚠️ 결론하기 전에 이 셋을 «갈라라»:
   1. 내가 방금 깨뜨렸나  → 대조군(내 변경 없는 상태)과 «이름 집합»을 비교해라
   2. 환경 문제인가       → 설치본이 어느 트리를 가리키는지 봐라
   3. 원래 간헐적인가     → 알려진 flaky 이름과 대조해라

<검사 출력을 여기 붙여넣으세요>
```

---

# 이 런북이 가리키는 문서

| 필요할 때 | 어디 |
|---|---|
| 개념 (CI · 이미지 · 빌드 · 배포) | `01-concepts-for-non-developers.md` |
| 왜 저장소가 나뉘었나 | `04-why-two-repos.md` |
| 첫날 환경 세우기 | `07-first-day-scripts.md` |
| 트랙 A 전체 지도 | `10-provider-lane-kickoff.md` |
| **이미지 → 중앙 이관 (명령 단위)** | `15-image-handoff-walkthrough.md` |
| **첫 provider 가 이미 겪은 것** | `91-provider-lessons-from-the-monorepo.md` |
| 밟은 지뢰 전체 | `90-known-traps.md` |
| intent 규칙 본문 (SSOT) | `fcc-test-platform:intent/README.md` |
