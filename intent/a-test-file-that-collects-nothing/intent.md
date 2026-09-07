# Intent: 시험을 0건 수집하는 시험 파일 하나 — 그리고 그것이 검사한다던 기제

Author: Claude (초안) / 세션 fcc-delivery-final-c1
Date: 2026-09-07
Status: draft
Slug: a-test-file-that-collects-nothing

## Problem

**`tests/test_impact_tests_invariant_coverage.py` 는 363줄인데 pytest 가 거기서
시험을 0건 수집한다. 그리고 그것이 검사한다던 기제는 «선언만 남고 판정기가 없다».**

측정 조건: `origin/main@6dd9b81`, 새 venv(`pip install -e '.[test]'`,
contracts 0.1.26 · kernel 0.5.4 · mypy 2.3.1). 도구 범위는 각 표에 적었다.

### ① 파일 자신 (AST 전수, `tests/*.py`)

| | |
|---|---|
| 줄 수 | 363 |
| `ClassDef` | **0** |
| `test*` 함수 | **0** |
| 헬퍼 | 4 — 둘은 이 파일 안에서만 호출, 둘(`_route_pattern_through_impact_tests` · `_detect_shadowed_specific_patterns`)은 **호출부 0건** |
| `tests/*.py` 중 `test*` 0개인 파일 | **이것 하나뿐** |

⚠️ 이름이 `test_*.py` 이고 정교한 docstring 을 가졌다. **봉인을 «세는» 도구도 사람도
이것을 「있다」로 센다.** pytest 만 0건을 수집하고, 0건은 초록과 같은 모양이다.

### ② 이 파일이 검사한다던 대상들 — 있는 것과 없는 것

| 이름 | 이 레포에 |
|---|---|
| `.claude/scripts/impact-tests.sh` (docstring 이 「the actual PR-gate routing lives in」이라 부름) | **없음** |
| `tests/test_invariant_skill_mapping_drift.py` (docstring 이 형제 게이트로 부름) | **없음** |
| `.claude/skills/verify-apps-web-scaffold/SKILL.md` 의 `mapped_invariants` + `trigger_patterns` | **있음 — 실제로 선언한다** |

즉 **계약의 «주체»는 여기 있고 «판정기»만 없다.** 「대상이 떠난 게이트인가」에 대한
답이 양쪽으로 갈리는 자리다.

### ③ 그 선언을 «읽는» 코드가 없다

도구 범위: `grep -rn --exclude-dir=.git --exclude-dir=node_modules`, **모든 파일형**,
레포 전량. 그리고 각 출현이 코드인지 산문인지는 **AST 로** 갈랐다.

    mapped_invariants   5곳   전부 산문
        .claude/skills/verify-apps-web-scaffold/SKILL.md:7      ← 선언 자신
        .claude/contracts/sprint-self-audit-checklist.md:32     ← 자가감사 B-1 항목
        docs/education/…scripts-알맹이-패키지-이관….html:981     ← 교육자료
        tests/test_impact_tests_invariant_coverage.py:16,22     ← **모듈 docstring**

    trigger_patterns    4곳   같은 분포

**코드에서 읽는 곳 0건.** 그리고 `/verify-implementation` 이나
`verify-apps-web-scaffold` 를 부르는 워크플로·훅·스크립트도 **0건**이다
(`.github/` · `githooks/` · `scripts/` · `.claude/settings.json` 검색).

### ④ 이 파일 안에서 두 주석이 반대말을 하고 있었다

    1~3행    「남은 것은 … TestFrontendConformanceSyntheticRouting 뿐이고」
    358행    「TestFrontendConformanceSyntheticRouting 는 이 레포로 오지 «못했다»」

실측이 꼬리 편이었다. 그리고 그 클래스는 **이관된 것도 아니다** — 양 레포 AST 전수에서
`class TestFrontendConformanceSyntheticRouting` 정의가 **0건**, 언급은 **7곳**
(모노레포 5 + 이 파일 2). **이사가 아니라 소멸이었다.**

⚠️ 그 7곳 중 모노레포 `.claude/skills/verify-frontend-conformance/SKILL.md:67` 은
*"Routing is sealed by `…::TestFrontendConformanceSyntheticRouting`"* 라고 **보장**한다.
없는 봉인을 이름까지 대며. 그리고 그 보장이 `mapped_invariants` 가 아니라 **산문에**
있어서, 그것을 잡으라고 만든 orphan-invariant 게이트가 **구조적으로 못 본다.**

> 머리 산문(위 1~3행)은 **별도 커밋으로 이미 정정했다.** 이 intent 는 그것이 아니라
> **「그래서 이 파일을 어떻게 할 것인가」**를 묻는다.

## Proposed outcome

**「이 레인에 라우팅 봉인이 있다」가 참이거나, 없다는 것이 이름으로 드러나거나 —
둘 중 하나가 된다.** 오늘은 셋째 상태다: 없는데 있어 보인다.

해결되면:

1. `tests/*.py` 중 `test*` 0개인 파일이 **0개**가 되거나, 그런 파일이 왜 남는지가
   **기계가 읽는 형태**로 적힌다.
2. `verify-apps-web-scaffold` 의 `mapped_invariants` 선언이 **무언가에 읽히거나**,
   읽히지 않는다는 것이 그 파일에 적힌다.
3. 「봉인이 있나」를 파일 존재로 답하는 사람이 틀리지 않게 된다.

## Affected users and systems

* **사람 — 개발자.** 이 레인에서 「이 축에 봉인이 있나」를 묻는 사람. 오늘은 파일
  이름과 docstring 이 「있다」고 답하고, 그 답이 틀렸다.
* **사람 — 자가감사를 쓰는 세션(나 자신 포함).** 나는 B-1 을 「이 레인의 기제가
  아니다」로 SKIP 했고 **틀렸다.** 기제는 있고 판정기가 없다. 그 오답이 이미 머지된
  커밋 메시지 다섯에 들어 있다.
* **레인** — `fcc-test-platform` **만**. 다만 이 결함의 **자매 5곳이 모노레포에** 있고
  형제 세션 `fcc-delivery-final-8c` 의 장부에 소유 미정으로 등재돼 있다.
  `fcc-test-contracts` 는 **닿지 않는다** — 커널 태그 발행 불필요.
* **DB · OpenAPI · 프론트엔드 · provider 레포** — 닿지 않는다.

## Constraints

* **이 파일이 «어떤» 시험도 안 담고 있으므로 지워도 red 가 안 난다.** 그것이 편한
  점이자 위험한 점이다 — 「지워도 초록」은 「지워도 된다」를 뜻하지 않는다.
  `verify-apps-web-scaffold` 의 선언이 이 레포에 살아 있다는 사실이 그 반대 근거다.
* **모노레포를 이 판에서 고치지 않는다.** 지금 그쪽 트리에서 차분 측정이 돌고 있고,
  회차 사이에 트리를 바꾸면 그 측정이 못 쓰게 된다.
* **`impact-tests.sh` 를 이 레포로 가져오는 것은 «새 게이트를 세우는» 일**이지 정리가
  아니다. 그 경로를 고르면 그것 자체가 별도 규모다.
* **기준선에 실패 이름을 추가하지 않는다.**
* **승인 지점 없음** — 태그 push · 컨테이너 재기동 없음.

## Success criteria

| # | 지금 | 이 판 뒤 |
|---|---|---|
| S1 | `tests/*.py` 중 `test*` 0개인 파일 **1개** | **0개**, 또는 그 사실을 단언하는 검사가 생김 |
| S2 | `mapped_invariants` 를 읽는 코드 **0건** | **≥1**, 또는 「아무것도 안 읽는다」가 SKILL.md 에 적힘 |
| S3 | 죽은 이름 `TestFrontendConformanceSyntheticRouting` 의 platform 쪽 언급 **1곳**(꼬리 358행) | **0곳**, 또는 「이 이름은 어디에도 없다」로 다시 쓰임 |
| S4 | `mypy` 0건 / 215 files · 게이트 18+9 · lane_check 0/0 | **안 움직임** |

## Open questions

1. **이 파일을 지울 것인가, 판정기를 세울 것인가, 아니면 「시험이 아님」을 이름으로
   드러낼 것인가?** 넷째도 있다 — 헬퍼 넷 중 둘은 호출부가 0건이라 그것부터 정리하면
   남는 것이 더 작아진다.
   초안 의견 없음. **이것이 이 intent 가 묻는 바로 그 질문이다.**
   → 답( ):

2. **`verify-apps-web-scaffold` 의 선언이 오늘 아무것에도 안 읽히는데, 그 선언을
   유지할 것인가?** 유지하면 「선언은 있는데 판정기가 없다」가 계속이고, 지우면 이
   레인이 그 기제를 «포기»한다는 결정이다. 후자는 이 파일 하나보다 큰 결정이다.
   → 답( ):

3. **`tests/*.py` 에 `test*` 가 0개인 파일을 금지하는 검사를 세울 것인가?** 세우면
   이 부류가 다시 생기는 것을 구성상 막는다. 다만 「의도적으로 헬퍼만 담은 파일」을
   `tests/` 에 두는 관례가 이 레포에 있는지 먼저 재야 한다(실측: 오늘 그런 파일은
   이것 하나뿐이고, `_` 로 시작하는 헬퍼 모듈과 `conftest.py` 는 그 셈에서 뺐다).
   → 답( ):

4. **모노레포 5곳을 이 intent 가 함께 가져갈 것인가?** 형제 세션이 「소유 미정으로
   두고, 원하면 넘긴다」고 했다. 두 레포에 걸치면 일정이 갈라지고, 안 가져가면 같은
   죽은 이름이 저쪽에 남는다 — 그중 하나는 없는 봉인을 «보장»하는 문서다.
   → 답( ):
