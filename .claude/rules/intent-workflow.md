<!--
  ⚠️ 이 파일에는 `paths:` frontmatter 가 **없다**. 그것이 요점이다.

  공식 문서(code.claude.com/docs/en/memory):
    "Rules without a `paths` field are loaded unconditionally and apply to all files."

  2026-09-07 실측: 이 레인의 `.claude/rules/*.md` 셋은 **전부** `paths:` 를 가져
  조건부였다. `supervisor-workflow.md`(81KB, 머지 위생)는 `.claude/**` 나
  `scripts/supervisor_*.py` 를 만질 때만 로드된다 — 즉 **평범한 기능 작업 세션은
  그것을 한 글자도 보지 못했다.** 이 파일이 이 레인 최초의 «무조건» 규칙이다.

  ⚠️ 여기에 `paths:` 를 붙이지 마라. 붙이는 순간 이 규칙은 「의도 흐름을 이미
     아는 세션」에게만 도달하고, 그것은 아무에게도 도달하지 않는 것과 같다.

  ⚠️ 짧게 유지하라. 긴 파일은 adherence 를 낮춘다(공식 문서). 상세는
     `intent/README.md` 가 SSOT 이고 이 파일은 «거기로 보내는 것»이 일이다.
-->

# 의도 기반 개발 흐름 — 모든 세션에 참인 것

## 언제 이 규칙이 걸리나

**새 기능 · 동작 변경 · 외부에 보이는 변화**를 만들려 할 때.
오탈자·명백한 버그 한 줄·이미 승인된 스펙 안의 후속 수정에는 걸리지 않는다.
전부에 요구하면 형식이 되고, 형식이 되면 아무도 안 읽는다.

## 무엇을 해야 하나

코드보다 먼저 `intent/<slug>/intent.md` 를 만들고 **사람의 승인(PR 머지)** 을 받는다.
그 다음 `spec.md`(승인), 그 다음 `plan.md`(개발 담당자 확정), 그 다음 코드.

**초안은 전부 에이전트가 쓰고 사람은 승인만 한다.** 사람이 초안을 쓰면 속도의
이득이 사라지고, 사람이 승인을 안 하면 책임이 사라진다.

## 승인은 «예외 승인» 이다

기본은 **동료 1인(작성자 제외)** 이다. 리드는 상시 승인자가 아니라 **예외 판단자**다.
방아쇠 다섯이 걸릴 때만 사람의 «기록된» 결정이 요구된다:

| # | 언제 | 초록이 되는 조건 |
|---|---|---|
| T1 | 되돌릴 수 없는 경로 + 게이트 자신 | `CODEOWNERS` — 🔴 **오늘 게이트가 아니다**(아래) |
| T2 | 기준선에 실패 이름 추가 | `Debt-Accepted-By: <이름> — <사유>` |
| T3 | `spec.md` 에 답 없는 열린 질문 | `→ 답(<이름>): <내용>` |
| T4 | `plan.md` §1 표에 없는 파일이 diff 에 | 표에 추가 또는 `Scope-Extended-By:` |
| T5 | 변경 파일 수가 선언의 2배 초과 | T4 와 같음 |

판정 SSOT → `scripts/human_judgment_triggers.py`.
**방아쇠는 알림이 아니라 red 다** — 아티팩트에 이름 붙은 결정이 적히기 전까지
검사가 초록이 되지 않는다.

## 🔴 무엇이 «실제로» 강제되는가 — 이것을 틀리지 마라

| 층 | 강제되나 |
|---|---|
| L0 `CLAUDE.md` · 이 파일 | 막지 않는다 (규칙을 도달시킨다) |
| L1 Claude Code `PreToolUse` 훅 | 설정 삭제로 우회 가능 |
| L2 `githooks/` | `--no-verify` 로 우회 가능 |
| L3 pytest → `lane_check` (pre-push) | `FCC_SKIP_LANE_CHECK=1` 로 로컬만 우회 가능 |
| **L4 branch protection** | **불가능 — 서버에서 돈다** |
| — `CODEOWNERS` | 🔴 **오늘 아무것도 막지 않는다** |

`CODEOWNERS` 가 게이트가 «될 수 없는» 이유: `require_code_owner_reviews: false`
이고, 켜면 협업자가 1명이라 그 경로를 **아무도 고칠 수 없게** 된다.
이 규모에서는 원리적으로 게이트가 될 수 없다(2026-09-07 실측).

⚠️ 이 표를 산문으로 바꾸지 마라. 「이 게이트가 무엇을 강제하는가」는 이 저장소가
하루에 **네 번** 틀린 진술 종류다. 값의 SSOT 는
`.claude/contracts/branch-protection-declaration.json` 이고
`tests/test_documented_gate_values_match_the_live_config.py` 가 대조한다.

## 규칙 본문

**`intent/README.md`** — 폴더 모양 · 서식 · 상태 어휘 · 동료용 3단계 · 방아쇠 상세.
어긋나면 그쪽이 맞다.
