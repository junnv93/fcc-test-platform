# Plan: 의도 기반 개발 흐름 도입

Intent: ./intent.md
Spec: ./spec.md
Engineer: 세션 3d (리드 위임)
Date: 2026-09-07
Status: accepted
Slug: intent-driven-workflow
Branch: feature/intent-driven-workflow

> ⚠️ `plan.md` 는 PR 로 따로 올리지 않습니다. feature 브랜치에 코드와 함께
> 올라가고, **§1 의 표가 diff 와 대조됩니다** — 표에 없는 파일이 diff 에 있으면
> 방아쇠 T4 가 발화합니다(`spec.md` §3 D2/D3).
>
> 🔄 **2026-09-07 개정.** 승인 모델이 「리드 상시 승인」에서 **예외 승인**으로
> 바뀌면서 파일 목록과 순서가 달라졌습니다. `CODEOWNERS` 가 뒤쪽 부수 작업에서
> **T1 을 실현하는 핵심 부재**로 올라왔습니다.

## 1. 바뀌는 파일 — 순서대로

| # | 파일 | 무엇을 | 왜 이 순서 | 요구사항 |
|---|---|---|---|---|
| 1 | `intent/README.md` | 운영 매뉴얼 (예외 승인 모델) | 나머지 전부가 이것을 인용한다 | R2 R3 R9 |
| 2 | `intent/_templates/intent.md` | 서식 | | R2 R6 |
| 3 | `intent/_templates/spec.md` | 서식 — **§4 `Debt-Accepted-By:`** · **§7 `→ 답(이름):`** 필드 추가 | 검사(#8)가 이 필드를 판정 대상으로 삼는다 | R6 R10 |
| 4 | `intent/_templates/plan.md` | 서식 — §1 표가 T4 의 판정 입력임을 명시 | 같음 | R6 R10 |
| 5 | `intent/intent-driven-workflow/{intent,spec,plan}.md` | 첫 의도 = 이 작업 자체 | 검사가 통과할 **실물 예시**가 하나는 있어야 한다 | R6 |
| 6 | `CLAUDE.md` (레인 루트) | 200줄 이하. 모든 세션에 참인 것만 | 규칙이 도달해야 나머지가 의미를 갖는다 | R1 |
| 7 | `/home/kmjkds/fcc-delivery-final/CLAUDE.md` | 컨테이너 루트 | 세션이 여기서 시작한다 | R1 |
| 8 | `.claude/rules/intent-workflow.md` | **`paths:` 없음** — 무조건 로드 | 이 레인 최초의 무조건 규칙 | R1 R3 |
| **9** | **`CODEOWNERS`** | **T1 = 경로 축 방아쇠의 SSOT.** 리드 소유 경로 5종 | **#10 이 이 파일을 «파싱»한다 — 먼저 있어야 한다** | **R7 R9** |
| **10** | **`scripts/human_judgment_triggers.py`** | **T2~T5 판정 SSOT.** T1 경로는 `CODEOWNERS` 에서 **파생**한다 | 검사(#11)가 이것을 import 한다 | **R9 R10** |
| **11** | **`tests/test_human_judgment_is_recorded.py`** | 위 판정기의 봉인 + **주입 케이스 8종** | 판정기가 있어야 대상을 가진다 | **R9 R10** |
| 12 | `tests/test_intent_triplet_wellformed.py` | 세 파일의 형식·짝·상태 + 주입 케이스 | | R6 |
| 13 | `.claude/settings.json` + `.claude/hooks/guard_contributor_role.sh` | L1 `PreToolUse` 훅 | 검사가 초록이 된 뒤에 붙인다 | R4 |
| 14 | `githooks/pre-commit` (수정) | intent/code 혼합 커밋 거부 축 **추가** | 기존 두 축 **뒤에** 붙인다 — 덮어쓰지 않는다 | R5 R8 |
| 15 | `docs/education/2026-09-07-의도로-시작하는-개발-협업자-교육자료.html` | 동료 교육 문서 | 흐름이 확정된 뒤 | R2 |
| 16 | `fcc-test-contracts/CLAUDE.md` | 커널 레인용 (별도 PR) | | R1 |
| — | **branch protection 켜기** | GitHub 설정 | **§4 A1** — 코드 변경 아님 | R7 |

> ⚠️ **#9 → #10 의 순서가 D4 를 실현합니다.** 판정기는 리드 소유 경로를
> 하드코딩하지 않고 `CODEOWNERS` 를 파싱합니다. 같은 집합이 두 곳에 있으면
> 갈라지고, 갈라지면 **서버가 막는 것과 검사가 말하는 것이 달라집니다.**

## 2. 무엇으로 검증하나

| 요구사항 | 검사 | 지금 |
|---|---|---|
| R1 | 새 세션 `/context` 의 Memory files (**수동**) | 자동화 불가 |
| R2 | 동료 1인의 첫 PR (**수동**) | 사람 관측 |
| R3 | `test_intent_triplet_wellformed.py::test_spec_requires_accepted_intent` | 새로 만든다 |
| R4 | `test_intent_triplet_wellformed.py::test_contributor_role_hook_denies_code_edit` | 새로 만든다 |
| R5 | `tests/test_intent_commit_separation.py` | 새로 만든다 |
| R6 | `test_intent_triplet_wellformed.py` 전체 | 새로 만든다 |
| R7 | `gh api .../branches/main/protection` (**수동 1회**) | 사람 관측 |
| R8 | `scripts/lane_check.py --root .` 전후 이름집합 | 이미 있다 |
| **R9** | `test_human_judgment_is_recorded.py` — 방아쇠 발화/미발화 양방향 | 새로 만든다 |
| **R10** | 같은 파일 — `Debt-Accepted-By` · `→ 답(이름):` 파싱 | 새로 만든다 |

> ⚠️ **R1·R2·R7 은 자동 검사가 없습니다.** 표가 다 채워져 있으면 다음 사람이
> 「전부 자동으로 지켜진다」고 믿습니다. 셋은 사람이 한 번씩 눈으로 확인하고
> 결과를 `.claude/evaluations/` 에 남깁니다.

### 주입 케이스 — 여덟 개, 양방향

`test_human_judgment_is_recorded.py` 는 방아쇠마다 **깨진 것과 정상인 것**을 한 쌍씩 넣습니다.

| # | 주입 | 기대 |
|---|---|---|
| 1 | 기준선에 이름 하나 추가, `Debt-Accepted-By` 없음 | **거부** (T2) |
| 2 | 같은 추가 + `Debt-Accepted-By: 홍길동 — 상류 수정 대기` | 통과 |
| 3 | `spec.md` §7 에 `→ 답( ):` 빈 항목 | **거부** (T3) |
| 4 | 같은 항목에 `→ 답(홍길동): 그대로 간다` | 통과 |
| 5 | diff 에 `plan.md` §1 표에 없는 파일 | **거부** (T4) |
| 6 | 그 파일을 표에 추가 | 통과 |
| 7 | 선언 6개 대비 변경 13개 | **거부** (T5) |
| 8 | 선언 6개 대비 변경 11개 | 통과 |

> **왜 양방향인가.** 발화만 단언하면 「항상 거부하는 판정기」가 통과합니다.
> 미발화만 단언하면 「항상 통과하는 판정기」가 통과합니다. **둘 다 없으면
> 아무것도 안 하는 판정기와 구별되지 않습니다.**

## 3. 게이트 통과 계획

* 착지 후 `scripts/lane_check.py --root .` 의 실패 **이름집합**이 착지 전과 같아야 합니다.
* `delivered_test_run_baseline.json` 에 **새 이름을 추가하지 않습니다.**
  추가가 필요해지면 그것이 곧 T2 이고, 이 계획의 결함 신호입니다.
* 커밋 메시지 17항목 자가점검이 붙습니다.
* pytest 는 `QT_QPA_PLATFORM=offscreen` 이 필요합니다.
* `githooks/pre-commit` 수정(#14)은 **기존 두 축 뒤에** 덧붙입니다.

## 4. 승인 지점 (사람이 눌러야 하는 것)

| # | 무엇 | 왜 승인이 필요한가 |
|---|---|---|
| **A1** | ✅ **완료 (2026-09-07, 운영자 승인).** `main` branch protection 켬 | 아래 §4-A1 참조 |
| **A2** | **`CODEOWNERS` 신설** | 🔴 **이것은 게이트가 «아닙니다»** — `require_code_owner_reviews: false` 이고, 협업자 1명이라 켜면 그 경로를 아무도 못 고칩니다. 소유의 **기록**이고 리뷰어 자동 요청만 합니다. 그래도 승인 지점인 이유: 리드에게 리뷰 요청이 자동으로 가기 시작하고, 이 파일이 **T1 경로 목록의 SSOT** 가 되기 때문입니다. |
| **A3** | **`.claude/settings.json` 훅 추가** | 이 레포를 여는 **모든** Claude Code 세션의 도구 호출에 개입합니다. 형제 세션 포함. |
| A4 | 이 세 문서의 머지 | 흐름 자체의 확정 |

A2~A3 은 **각각 따로** 묻습니다. 하나의 「진행해라」로 묶지 않습니다.

### A1 — 적용된 값과 그 근거 (2026-09-07 완료)

> 🔑 **값의 SSOT 는 산문이 아니다.**
> `.claude/contracts/branch-protection-declaration.json` 이 값을 들고,
> `tests/test_documented_gate_values_match_the_live_config.py` 가 그것을
> **GitHub 실제 설정과 대조**한다.
>
> ⚠️ 값을 여기 «다시» 적지 마라. 이 세션은 하루에 네 번 틀렸고 네 번 다
> 「이 게이트가 무엇을 강제하는가」에 대한 산문이었다. 사본을 만들면 갈라지고,
> 갈라진 쪽을 읽은 사람이 틀린 것을 믿는다.
>
> 각 값의 **근거**는 그 JSON 의 `why_each_value` 에 있다 — 값 옆에.

> ⚠️ **「필수 승인 수 0」은 임시값입니다.** 뒤집는 조건이 명확합니다 —
> **두 번째 협업자가 추가되는 날 1 로 올립니다.** 그때까지는 「PR 은 필수인데
> 리뷰는 아무도 안 하는」 상태이고, 그 사실을 여기 적어 두지 않으면
> **동료가 합류한 뒤에도 자기 PR 을 자기가 머지할 수 있게 됩니다.**
>
### §신선도 — 남는 구멍 하나, 명시해 둡니다

`lane-check` 이 합친 트리를 보므로 `strict=true` 의 이득 **대부분**은 이미 있습니다.
그러나 하나가 남습니다:

> **GitHub 은 base 가 움직여도 검사를 다시 돌리지 않습니다.**
> 기록된 SUCCESS 는 **«그때의» base 에 합친 결과**입니다.
> `strict=true` 가 강제하는 것이 정확히 그 재실행이고, 그것을 껐습니다.

보상 통제로 `scripts/merge_readiness_guard.py` 가 있지만 **그것은 관례이지 게이트가
아닙니다** — `githooks/pre-push:33` 이 `advise || true` 로 부르고 그 함수는 경고 후
`return 0` 합니다(파일 390행). `merge <N>` 하위명령은 `.github/workflows/` 에서
**참조 0건**입니다. 즉 사람이 고르면 도는 것입니다.

⚠️ **처음 인용했던 사례 둘(PR #137·#138)은 «반례가 아니었습니다».** CI 로그를
직접 열어 보니 둘 다 **그때의 최신 main 과 합쳐** 검사했습니다:

```
#138 · HEAD is now at 37d2339 Merge b0a8d84… into dc9fdf94…
#137 · HEAD is now at 0b9d502 Merge bc978178… into ae7d7d40…
```

보고한 세션이 스스로 정정했고 저도 확인했습니다. **그래서 사례를 인용하지
않습니다** — 이 구멍은 «기제»로만 참입니다. 잰 것과 안 잰 것을 섞으면 다음 사람이
안 잰 것을 근거로 씁니다.

**이것을 감수합니다** — 다만 감수한다고 적어 둡니다. 다음 사람이 `CLEAN` 을
「지금 합쳐도 초록」으로 읽지 않도록.

> 🔴 **필수 검사가 러너를 못 받으면 머지가 «전면» 정지합니다.** `enforce_admins: true`
> 이므로 관리자도 우회할 수 없고, 협업자가 1명이라 사람으로 우회할 경로도 없습니다.
> **이 계정에는 전례가 있습니다** — 모노레포는 같은 결제/한도 축 위에서 러너를 받지
> 못했고 2026-09-07 운영자 판정이 「그대로 둔다」입니다. 이 레인의 `lane-check` 도
> GitHub 호스티드 러너에서 돕니다. 그날의 탈출구는 **protection 을 손으로 고치는 것뿐**입니다.
>
> 적어 두는 이유: 그날 당황한 사람이 원인을 **`lane-check` 의 red 로 오진**합니다.
> 「도구가 없다」와 「위반이 있다」가 또 같은 빨강이 됩니다.

> ⚠️ **탈출구가 하나 남습니다 — 그리고 그것이 의도입니다.** 관리자는 protection
> 자체를 끌 수 있습니다. 그러나 그것은 **per-머지 무음 우회가 아니라 의도적이고
> 기록되는 행위**입니다. 이 레포의 훅들이 우회 환경변수를 일부러 시끄럽게 만든 것과
> 같은 형태입니다. `lane-check` 최근 10회 중 9 success(1 running)라 플레이크로
> 전면 차단될 위험은 낮습니다.

> ⚠️ **차단은 «주입 검증되지 않았습니다».** 되읽기로 선언은 확인했고
> (`protected: true`, contexts `["lane-check"]`), 실제 직접 push 를 시도해 보지는
> 않았습니다 — 만약 통과해 버리면 force push 가 방금 금지돼 **치울 수 없기**
> 때문입니다. 첫 실증은 다음 세션의 push 에서 나옵니다.

> ⚠️ **순서가 뒤집히면 아무것도 강제되지 않습니다.** `CODEOWNERS` 는
> branch protection 의 *"Require review from Code Owners"* 가 켜져 있을 때만
> 발화합니다. 파일만 두고 protection 을 안 켜면 **파일은 있는데 아무것도 막지
> 않는 상태**가 되고, 그것은 이 레포가 반복해 기록한 실패 모양입니다.
> 그래서 **A1 → A2 → A1 의 옵션 체크 → 동료 초대** 순서입니다.

## 5. 되돌리는 법

| # | 되돌리기 |
|---|---|
| 1~12, 15~16 | 파일 삭제 또는 revert. 런타임 영향 0. |
| 9 (`CODEOWNERS`) | 삭제 시 T1 이 **조용히 사라집니다** — protection 은 켜진 채 owner 만 없어져 아무나 승인할 수 있게 됩니다. 되돌릴 때 A1 도 함께 재검토해야 합니다. |
| 13 (훅) | `.claude/settings.json` 삭제 → 다음 세션부터 원복. **진행 중 세션에는 즉시 반영되지 않습니다.** |
| 14 (pre-commit) | 추가한 절만 제거. 기존 두 축은 무손실. |
| A1 (protection) | 적용 전 값은 **없었습니다**(`404`). 현재 값 백업: `.git/branch-protection-backup-20260907.json`. 해제: `gh api -X DELETE repos/junnv93/fcc-test-platform/branches/main/protection` — **되돌리기 전에 백업을 읽어 무엇이 사라지는지 보세요.** 그 사이 머지된 것은 그대로 남습니다. |

## 6. 범위 밖으로 새는 것을 막는 선언

작업 중 눈에 띄어도 **이 계획에서는 하지 않습니다.** 발견하면 새 intent 로 올립니다.

* `.claude/rules/` 기존 3개의 `paths:` 재검토.
* `README.md` 의 「CI 휴면」·「private」 서술 정정 — **오늘 거짓임이 실측됐지만**
  이 계획의 대상이 아닙니다. 별도 의도로 올립니다.
* 모노레포 `CLAUDE.md` 637줄 분해.
* 슬랙 연동 · spec 자동생성 CI.
* **T5 의 「2배」 임계 재조정** — 표본이 없습니다. 첫 5개 의도 뒤에 정합니다(`spec.md` §7-5).
* 🆕 **`githooks/pre-push` 가 «삭제 push» 를 구분하지 못하는 것.** 실측(2026-09-07,
  형제 세션 보고 + 자체 확인): 그 훅은 **stdin 을 아예 읽지 않아** ref 삭제(local sha
  all-zero)를 알아볼 수 없고, **검사할 트리가 없는 삭제에도 전량 시험이 돕니다.**
  게다가 실패 모양이 「pytest 가 없다」인데 메시지는 「네가 방금 깨뜨렸다」로 읽혀
  **부재와 회귀가 같은 빨강**입니다. 탈출구는 `FCC_SKIP_LANE_CHECK=1` 뿐입니다.
  → **별도 intent 로 올립니다.** 이 계획의 범위가 아닙니다.
* 🆕 **base 신선도의 서버 축 신설** — 위 A1 이 감수하기로 한 위험입니다.
* 🔴 **T1 을 «실제로» 강제하는 레인 시험.** CODEOWNERS 는 이 규모에서 게이트가 될 수
  없습니다. `lane-check` 은 required 이고 관리자도 우회 못 하므로, 「게이트 파일이 이
  PR 에서 바뀌었는가」를 묻는 **테스트 파일**이 여기서는 이빨이 있습니다.
  이 계획의 범위가 아니고 `spec.md` §7-5 가 그 열린 질문입니다.

**Scope-Extended-By**: (없음)
