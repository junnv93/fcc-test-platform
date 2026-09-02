# `.claude/` — 이 저장소가 물려받은 것과, 물려받지 않은 것

이 디렉터리는 2026-09-03 에 `FCC_mobile_test_automation`(`origin/main` @ `9fcf6191`)
에서 **분야 중립인 것만 골라** 들여온 것이다. 레인 분리로 이 저장소가 갈라져 나올 때
`.claude/` 는 governed root 가 아니어서 매니페스트에 이름으로 적힌 것만 왔고, 그 결과
스킬 1개(`verify-apps-web-scaffold`)만 있고 규칙·훅·계약은 **0개**였다.

## 왜 골라서 들여왔나

FCC 저장소의 `verify-*` 스킬 152 개는 **대부분 그 분야의 측정 도메인**을 검사한다
(`verify-bt-hopping-sleep` · `verify-dccf-share-policy` · `verify-gain-band-policy` …).
여기 올 이유가 없고, 오면 소음이 된다. **그것들은 일부러 두고 왔다.**

들여온 것은 **분야와 무관하게 참인 것** 둘뿐이다:

1. **여러 세션이 한 저장소를 동시에 만질 때의 규율** — 워크트리/claim 위생, 머지
   신선도, 병렬 레인 락, 검사 축 맹점.
2. **커밋 게이트 3층** — 자가점검 형식·사유·값 축 / 브랜치·claim 바인딩 / 스쿼시·옛
   base 차단.

셋 다 **실제 사고 뒤에** 만들어졌다: 커밋 두 개가 자가점검 없이 복구 불가로 머지됨 ·
한 세션의 커밋이 다른 세션 브랜치에 착지 · PR 하나가 다른 PR 을 통째로 되돌림(13 파일).
그 사고들은 **코드가 만든 것이 아니라 여러 세션이 한 저장소를 만지는 상황**이 만들었고,
이 저장소도 곧 그 상황이 된다.

## 들어온 것

| 경로 | 무엇 |
|---|---|
| `rules/supervisor-workflow.md` | 워크트리·claim 위생 · 머지 신선도 · 병렬 레인 락 · 선재 실패 차분 판정 · 자가점검 값 축 |
| `rules/check-axis-blindness.md` | 「이 검사가 재는 축에 같은 값을 갖는 서로 다른 상태가 있는가」 판별 절차 |
| `rules/incomplete-landing-lifecycle.md` | reviewer FAIL 과 release readiness 를 분리하는 상태기계 |
| `contracts/sprint-self-audit-checklist.md` | 커밋 메시지 17 항목 자가점검의 정의 |
| `../githooks/commit-msg` | 자가점검 형식·사유·값 축 게이트 (차단형) |
| `../githooks/pre-commit` | 브랜치·claim 바인딩 가드 (차단형) |
| `../scripts/self_audit_message.py` | commit-msg 게이트의 **판정 SSOT** |
| `../scripts/work_claim_branch_guard.py` | pre-commit 게이트의 판정 SSOT |
| `../scripts/merge_readiness_guard.py` | 스쿼시·옛 base 차단 (호출형) |
| `../scripts/hook_bypass_guard.py` | `--no-verify` 류 우회 탐지 |

네 파이썬 파일은 **표준 라이브러리만** 쓴다(실측). 훅은 `git rev-parse --show-toplevel`
로 루트를 구하므로 이 저장소 레이아웃에서 그대로 돈다.

## 설치 — opt-in 이다

```sh
git config core.hooksPath githooks
```

⚠️ **이것은 실수 방지층(guardrail)이지 방어층이 아니다.** clone 마다 opt-in 이고
`--no-verify` 한 번이면 사라진다. 이 저장소의 `githooks/pre-push` 가 이미 같은 문장을
자기 머리말에 적고 있다 — 진짜 강제는 러너가 돌아오고 branch protection 이 이 검사를
required 로 거는 날에 생긴다.

**비상 우회**: `FCC_ALLOW_MISSING_SELF_AUDIT=1` · `FCC_ALLOW_BRANCH_MISMATCH=1` ·
`FCC_ALLOW_SQUASH_MERGE=1` · `FCC_ALLOW_STALE_MERGE_BASE=1` (일부러 시끄럽게 만들었다).

**동작 확인**(빈 임시 저장소에서 실측 2026-09-03): 자가점검 없는 커밋 **거절** ·
17 줄 + `Self-Audit: 17-items` 트레일러가 있으면 **통과**. work-claim 미바인딩은
**fail-open + stderr 경고**다(위생 게이트가 오탐을 내면 사람들이 꺼버린다).

## ⚠️ 이것은 사본이 아니라 **이 저장소의 것**이다

가장 중요한 문단이다. **FCC 저장소와 동기화하지 마라.**

이 저장소는 이미 사본이 갈라지는 것을 겪었다 — `nginx.conf` 와 게이트웨이 설정이 두
벌로 남아 있다가 한쪽만 고쳐져 갈라졌고(실측 2026-09-01, 업로드 천장), 그래서 FCC 쪽
사본을 지웠다. 같은 일이 규칙 문서에서도 일어난다.

그러므로:

* 여기 문서가 **FCC 에서 일어난 사고**를 인용하는 것은 정상이다 — 그것이 규칙의 근거다.
  근거를 지우면 다음 사람이 규칙을 미신으로 읽고 지운다.
* 그러나 **앞으로의 정정은 여기서 한다.** 이 저장소가 자기 사고를 겪으면 그것을 여기
  적고, FCC 쪽 문서를 참조하러 가지 않는다.
* FCC 쪽이 같은 문서를 고쳐도 **자동으로 따라오지 않는다.** 그것이 의도다.

## 들어오지 않은 것 (알고 두고 온 것)

`skills/` 의 verify-* 152 개(FCC 측정 도메인) · `hooks/`(세션 시작·종료 훅, FCC 백로그
파일 경로에 결합) · `work-claims/` 374 개(그 저장소의 세션 기록) · `exec-plans/`
(그 저장소의 웨이브 이력) · `scripts/` 의 supervisor 루프 도구들.

⚠️ `pre-commit` 은 `scripts/supervisor_preflight.py` 도 부르는데 **그 파일은 오지
않았다.** 훅이 `[ -f "$preflight" ]` 로 감싸므로 부재는 조용히 건너뛴다(fail-open) —
결함이 아니라 설계다. 그 도구가 필요해지면 그때 가져오거나 여기서 새로 만든다.

출처: `FCC_mobile_test_automation` @ `9fcf6191` (2026-09-03)
