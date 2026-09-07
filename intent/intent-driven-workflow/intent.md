# Intent: 의도 기반 개발 흐름 도입

Author: 팀 리드 (kmjkds) / Claude 초안
Date: 2026-09-07
Status: accepted
Slug: intent-driven-workflow

## Problem

이 레포는 곧 **비개발자 동료들과 함께** 작업합니다. 동료들은 모든 작업을
Claude Code 를 통해 수행합니다. 그런데 오늘 이 레포는 그 상황을 감당할
준비가 되어 있지 않습니다. 실측한 것만 적습니다.

**1. 세션 시작 시 에이전트에게 도달하는 프로젝트 규칙이 0 입니다.**

* `CLAUDE.md` 가 어디에도 없습니다 — 레인 루트·컨테이너 루트·`~/.claude/` 전부.
* `.claude/rules/*.md` 3개는 있지만 **셋 다 `paths:` frontmatter** 를 갖습니다.
  공식 문서: *"Rules without a `paths` field are loaded unconditionally."*
  셋 다 `paths` 가 있으므로 **조건부**입니다. `supervisor-workflow.md`(81KB,
  머지 위생·워크트리 규율)는 `.claude/**` 나 `scripts/supervisor_*.py` 를 만질
  때만 로드됩니다 — **평범한 기능 작업 세션은 그것을 보지 못합니다.**
* 그동안 그 자리를 메운 것은 **auto memory**(토픽 파일 98개 + 97줄 인덱스)입니다.
  그런데 auto memory 는 **기계-로컬이고 팀과 공유되지 않습니다.** 동료가 자기
  PC 에서 Claude Code 를 켜면 그 98개는 **하나도 따라가지 않습니다.**

**2. `main` 브랜치가 보호되지 않습니다.**

`GET repos/junnv93/fcc-test-platform/branches/main/protection` → `404 Branch not
protected`(2026-09-07 실측). 즉 오늘 누구나 `main` 에 직접 push 할 수 있고,
서버측에서 아무것도 막지 않습니다. 지금은 세션들이 규율로 PR 을 쓰고 있을 뿐입니다.

**3. 「무엇을 왜 만드는가」가 남는 자리가 없습니다.**

이 레포는 「어떻게 검사하는가」에 대해서는 대단히 성숙합니다(차단형 훅 3층,
`lane_check` 이름집합 게이트, 사후 평가 30건+). 그런데 **그 앞단** — 무엇을 왜
만들기로 했고 누가 승인했는가 — 는 PR 설명과 커밋 메시지에 흩어져 있습니다.
비개발자 동료가 아이디어를 낼 통로도, 그것을 리드가 승인할 지점도 없습니다.

## Proposed outcome

* 모든 기능 작업이 **`intent/<slug>/intent.md`** 에서 시작하고, 그 파일이
  **사람의 승인(PR 머지)** 을 받아야 다음 단계로 갑니다.
* 그 승인의 기본 주체는 **동료 1인(작성자 제외)** 이고, **리드는 상시 승인자가
  아니라 예외 판단자**입니다 — 기계가 판정하는 방아쇠가 걸릴 때만 개입합니다.
  (2026-09-07 개정. 초판은 리드 상시 승인이었고 §Open questions 2 에서 폐기됐습니다.)
* 비개발자 동료가 코드베이스를 몰라도 Claude 와 대화만으로 의도를 제출할 수
  있고, **코드는 실수로도 건드리지 못합니다.**
* 규칙이 **매 세션 자동으로** 에이전트에게 도달합니다 — 동료의 PC 에서도.
* 승인 없는 `main` 착지가 **서버에서** 불가능해집니다.
* 「무엇을 왜 만들었나」가 코드 옆에 남아, 6개월 뒤 리뷰어가 의도를 추측하지
  않습니다.

## Affected users and systems

**사람**

| 역할 | 이 변경 후 하는 일 |
|---|---|
| 팀 리드 | **예외 판단자.** 되돌릴 수 없는 경로(T1)와 기록되지 않은 결정(T2~T5)에만 개입. |
| 비개발자 동료 | 의도 제출 · 스펙 초안 요청 · **서로의 intent·spec 승인.** 코드 편집 없음. |
| 개발 담당 세션 | `plan.md` 확정 · 구현 · feature PR |

**시스템**

* `fcc-test-platform` — `intent/` 폴더 신설, `CLAUDE.md` 신설, `.claude/settings.json` 신설, 검사 1건 추가.
* `fcc-test-contracts` — 같은 흐름을 적용하되 **의도는 platform 쪽에 기록**합니다.
  커널 변경은 대개 platform 의 필요에서 나오고, 두 곳에 의도를 나눠 적으면
  갈라집니다. contracts 에는 `CLAUDE.md` 만 별도로 둡니다.
* **`/home/kmjkds/fcc-delivery-final`(컨테이너 폴더)** — 세션이 여기서 시작되므로
  여기에도 `CLAUDE.md` 가 필요합니다. 이것은 git 레포가 아니어서 버전 관리되지
  않는다는 사실을 문서에 남겨야 합니다.
* **GitHub** — branch protection · CODEOWNERS.

## Constraints

* **동료는 비개발자입니다.** 지시가 세 줄을 넘으면 안 되고, 실패했을 때
  화면에 나오는 문장이 다음에 무엇을 해야 하는지 말해야 합니다.
* **기존 게이트를 대체하지 않습니다.** 자가점검 17항목 · work-claim 브랜치
  가드 · `lane_check` 는 그대로 둡니다. 축을 **덧붙입니다.**
  (근거: 이 레포는 이관하면서 덮어써 유일한 차단 게이트를 잃을 뻔한 적이 있습니다 —
  `githooks/pre-push` 머리말.)
* **새 검사는 테스트 파일로 붙입니다.** 이 레인의 실질 게이트는 `lane_check.py`
  → pytest 이름집합입니다. 워크플로에만 넣으면 로컬에서 아무것도 막지 않습니다.
* **`CLAUDE.md` 는 200줄을 넘기지 않습니다.** 공식 문서: *"target under 200 lines
  per CLAUDE.md file. Longer files consume more context and reduce adherence."*
  모노레포의 637줄은 반면교사입니다.
* **기존 부채를 소급 적용하지 않습니다.** 이미 진행 중인 작업에 intent 를
  요구하지 않습니다. 오늘 이후의 새 기능부터입니다.
* **branch protection 켜기는 되돌리기 어렵고 형제 세션 전부에 영향을 줍니다** —
  운영자 승인 지점입니다. 이 의도가 승인돼도 그것은 별도로 묻습니다.

## Success criteria

| # | 무엇을 | 어떻게 잰다 | 목표 |
|---|---|---|---|
| S1 | 규칙이 세션에 도달한다 | 새 세션에서 `/context` → **Memory files** 목록에 `CLAUDE.md` 가 보인다 | 보인다 |
| S2 | 비개발자가 의도를 낼 수 있다 | 동료 1인이 문서만 보고 intent PR 을 끝까지 올린다 | 도움 요청 0회 |
| S3 | 세 파일의 형식이 지켜진다 | `pytest tests/test_intent_triplet_wellformed.py` | 초록 |
| S4 | 검사가 공허하지 않다 | 일부러 깨진 intent 를 하나 넣어 위 검사가 **빨개지는지** 확인 | 빨개진다 |
| S5 | 승인 없는 main 착지가 막힌다 | `GET branches/main/protection` 이 200 을 반환 | 200 |
| S7 | 리드가 «필요할 때만» 불린다 | 첫 10개 PR 중 리드가 개입한 비율 | 30% 이하 |
| S6 | 기존 게이트가 그대로다 | `scripts/lane_check.py --root .` 실패 이름집합 변화 | 변화 없음 |

> S4 를 따로 세운 이유: 이 레포는 「검사는 있는데 아무것도 검사하지 않는」 상태를
> 여러 번 겪었습니다. 검사를 만든 것과 그 검사가 이빨을 가진 것은 다른 명제입니다.

## Open questions

1. **동료의 PC 에 `.claude/settings.json` 훅이 실제로 붙는가?** 프로젝트 설정은
   워크스페이스 신뢰(workspace trust) 승인을 거칩니다. 동료가 그것을 거절하면
   L1 훅이 없는 상태로 작업합니다. 그 경우를 감지할 방법이 필요합니다.
2. ~~**`spec.md` 승인자를 리드 1인으로 두는 것이 병목이 되는가?**~~
   → **답(리드, 2026-09-07): 병목이 맞다. 그리고 더 근본적으로, 리드가 그 판단을
   실제로 내리는 경우가 드물다. 항상 승인되는 승인은 승인이 아니다.**
   → 상시 승인을 폐기하고 **예외 승인**으로 간다(`spec.md` §3 D1). ✅ 해소
3. **contracts 레인의 의도를 platform 에 적는 결정이 옳은가?** 커널 태그가
   platform 보다 먼저 나가야 하는 구조라, 의도만 platform 에 있고 실행은
   contracts 에서 일어나는 비대칭이 생깁니다.
4. **컨테이너 폴더(`fcc-delivery-final`)의 `CLAUDE.md` 는 버전 관리되지 않습니다.**
   동료 PC 에서는 그 파일이 없을 것입니다. 각자 만들게 할지, 레인 쪽에서만
   해결할지 결정이 필요합니다.
