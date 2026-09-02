---
paths:
  - ".claude/**/*"
  - "scripts/supervisor_*.py"
  - "scripts/work_claim_*.py"
  - "scripts/merge_readiness_guard.py"
  - "scripts/self_audit_message.py"
  - "scripts/stranded_branches.py"
  - "scripts/exec_plan_buckets.py"
  - "scripts/dev_review_supervisor.py"
  - "scripts/githooks/*"
  - "tests/test_supervisor_*.py"
  - "tests/test_work_claim_*.py"
  - "tests/test_merge_readiness_guard.py"
  - "tests/test_self_audit_commit_msg_gate.py"
  - "tests/test_meta_self_audit_invariant.py"
  - "tests/test_stranded_branch_report.py"
  - "tests/test_exec_plan_buckets.py"
  - "tests/test_hook_bypass_guard.py"
---

<!--
이 파일은 2026-08-16 까지 `paths:` 가 없어 매 세션 로드됐다(≈10k 토큰).
내용은 supervisor 루프·워크트리·머지 위생이라 위 경로를 만질 때만 필요하다.
⚠️ 커밋 시점에 반드시 지켜져야 하는 셋(explicit-files / no-squash / merge-base
신선도)은 `CLAUDE.md` §Session Hygiene Rules 에 그대로 있고 게이트가 봉인한다 —
즉 이 스코핑은 강제력을 하나도 옮기지 않는다. 강제는 훅이 하고 여기는 사유를 적는다.
-->

# Supervisor Workflow Hygiene

## FAIL/BLOCKED checkpoint integration lifecycle (SSOT, 2026-08-25)

`PASS` is the completion/release oracle, not the only delivery state. A fresh
Evaluator `FAIL` or `BLOCKED` must not strand committed work or force later
sessions to reconstruct it from a dirty worktree. When the exact failed
criteria, evaluator artifact, active plan, claim status, and evaluated SHA are
recorded, the branch may be pushed and merged as a **checkpoint integration**.

Checkpoint integration is explicitly not completion: it does not authorize
release, production cutover, archive, claim closure, or any PASS wording. The
plan remains in `active/`; the claim remains `active`/`blocked`; the next
session creates a new frozen SHA and resumes unresolved gates. It still
requires explicit-file commits, branch/claim guards, clean custody of every
integrated path, merge-readiness evidence, and no unsequenced overlap with an
active claim. It never permits fail-open preflight, `fetch --prune`, reset,
rebase, snapshot-only defect hiding, or edits in another session's scope.

Every new contract and harness workflow inherits this two-axis lifecycle:
`delivery = checkpoint-integrated` may be true while `completion = blocked`.
Only a fresh requirement-to-evidence PASS may transition completion to ready
and allow archive/claim closure.

## 손으로 고른 `--max-iterations` 는 그만 — 래퍼가 파생한다 (2026-08-18)

**`python3 .claude/scripts/supervisor_run.py` 로 웨이브를 돌려라.**

```bash
python3 .claude/scripts/supervisor_run.py \
  --verify-cmd 'QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_x_invariants.py -q' \
  --loop-dir .claude/agent-loop-<slug> \
  -- --task .claude/exec-plans/active/<plan>.md --live --auto-commit
```

세 가지를 사람 손에서 뺀다:

1. **예산** — Planner 가 계획을 **자기가** 쪼개므로 `--max-iterations` 를 미리 알 수 없다.
   래퍼가 루프의 `plan.md` 에서 milestone 수를 세어 `× 3` 으로 파생한다. 세는 대상은
   `plan.md` 이지 exec-plan 이 아니다. **passthrough 로 `--max-iterations` 를 주면 거부**한다
   — 그것이 없애려는 자리다.
2. **detach** — 항상 `setsid`. 하네스 배경 작업으로 띄우면 수명 제한으로 죽는다
   (실측 **exit 144** 2회, 재기동 ~15분).

   ⚠️ **이 문단이 적는 그 명령이 2026-08-19 까지 fork bomb 이었다.** 재실행 조립이
   `--foreground` 를 argv **끝**에 붙였는데, 위 예시를 포함한 모든 호출이
   `-- --task <plan> --live` 를 갖는다 → 토큰이 구분자 뒤로 가고 자식 파서가 보지 못해
   **다시 detach** 했다. 실측 **재기동 1,897회 / 2분**, `state.json`·`plan.md` 없음,
   실제로 돈 라운드 **0**. 즉 *느린 웨이브*와 *한 번도 시작하지 않은 웨이브*가
   관측자에게 같아 보였다. 지금은 두 축으로 막힌다 — argv 를 래퍼 자신의 구간에 넣고,
   `FCC_SUPERVISOR_RUN_DETACHED` 표식이 두 번째 detach 를 **구조적으로** 막는다.
   **로그에 시작 배너가 두 번을 넘게 찍히면 그것이 재발 신호다.**
3. **완료 판정** — **`--verify-cmd` exit 0** 이고 리뷰 문자열이 아니다. 리뷰어는
   **milestone** 을 승인하고 루프는 그것을 웨이브 승인으로 읽는다. `--verify-cmd` 가
   없으면 **시작을 거부**한다(완료 오라클 없이 도는 것은 캡까지 도는 것과 같다).

**진전 없음에서 멈춘다.** `dev_review_supervisor` 의 캡은 **누적**이라
(`range(len(state['iterations']) + 1, max_iterations + 1)`) 이미 돈 횟수 이하의 캡은
루프 본체를 **한 번도 돌리지 않고** 즉시 `max_iterations_reached` 를 낸다. 그 가드가
없으면 래퍼가 무한히 돈다 — 느린 웨이브와 구분되지 않는 모습으로.

⚠️ **이것은 opt-in 래퍼이고 강제가 아니다** (`supervisor_run.WRAPPER_LIMITATION`,
*"opt-in wrapper, not an enforcement boundary"*). `commit-msg`/`pre-commit` 훅과 달리
맨 `dev_review_supervisor.py` 호출에 끼어들지 **않는다** — 그쪽은 여전히 손으로 고른
캡을 받는다. `scripts/hook_bypass_guard.py::GUARDRAIL_LIMITATION` 과 같은 형상으로
모듈 상수 + 매 실행 stderr 고지 + 이 문단, 세 곳에 적는다. 이 문장이 사라지면 다음
세션이 방어층으로 믿고 그 위에 다른 것을 얹는다.

**래퍼는 `dev_review_supervisor.py` 를 고치지 않는다** — 리뷰 문자열 판정은 그
스크립트의 계약이고 다른 레포도 쓴다. 검증 실행은 그 스크립트의 `_run_verify_gate`
를 **재사용**하되 매 라운드 **다시 실행**한다(기록된 결과 재사용은 *"그 사이 트리가
안 바뀌었다"* 는 미증명 가정을 새로 들이는 것이고, `_finish` 가 같은 사유로 이미
의도적 재실행을 택했다).

보호: `tests/test_supervisor_run_wrapper_invariants.py`.

## Token-budgeted supervisor loop

Routine supervisor runs use `--max-iterations 2` and
`--codex-reasoning-effort medium`. High-risk schema, security, release, or
cross-repository work may explicitly use both `--max-iterations 5` and
`--codex-reasoning-effort max`. Planner/Implementer/Reviewer routing,
`--max-replans 1`, the read-only Reviewer sandbox, the 7200-second hard
timeout, and idle timeout 0 remain unchanged. The first Reviewer gets full
context. Every Reviewer prompt keeps the same byte-identical inline stable
prefix — Reviewer contract preamble, harness skill context, repo/scope context,
plan, and original task — for prompt-cache continuity. Later Reviewers get
current status/diff plus bounded report/finding/verify context and file-backed
references for those variable audit artifacts. Missing, tampered, legacy, or
unsafe resume references require full fallback or terminal failure. `--verify-cmd`
stays scoped to deterministic changed-surface checks; bench/full-suite runs are
not defaults.

Supervisor work uses multiple worktrees and agents. Treat branch/worktree state as
shared infrastructure, not private scratch space.

## Required Flow

1. Run preflight before starting or resuming a supervisor task:

   ```powershell
   python scripts\supervisor_preflight.py --task <slug> --scope <path> --shared-output <path>
   ```

   Closure evidence uses `--no-prune`: the preflight refreshes with Git's explicit
   non-pruning mode and a refresh failure is an exit-2 blocker. `--warn-only` is
   reserved for legacy report-only callers and is not valid for closure evidence.
   An overlap is cleared only by the target claim's machine-readable
   `authorized_transfers` record, which names the source claim/worktree, destination,
   exact paths, user authority, and source retention of all unlisted scope. The source
   claim remains active and is reported as an authorized transfer; deleting or silently
   ignoring it is not a clearance path.

   Preflight also names the **opposite** shape — a claim whose work already landed but
   whose `status` is still non-terminal (`scripts/landed_claims.py`). That one is more
   expensive than a stranded branch: an open claim **owns its scope**, and that ownership
   is a hard **Blocker** here, so one unclosed claim locks those paths indefinitely and
   the session that could unlock it has already left. 실측 2026-08-19: a claim sat at
   `review` for **16 days** with both its commits in `main` and its branch, worktree and
   PR all gone, while the debt ledger held an item whose stated handover condition was
   that very claim merging. Two axes, and their answerability differs — *landed* needs
   `gh` (an open PR makes `review` legitimate), *branch-gone* does not (no remote copy
   means no open PR can exist). **경고 전용이고 아무것도 자동으로 닫지 않는다** — closing
   is a judgement ("this wave is done"), and "the branch is fully in the base right now"
   is only an observation. 봉인: `tests/test_landed_claims_report.py`.

   Preflight also **names stranded branches** — pushed, no open PR, not in the base.
   Merging is the one final step nothing here enforces, so a finished wave can sit
   unopened until someone stumbles on it (실측 2026-08-12: 12 커밋). Two report points
   because they answer for different sessions: the session-**end** hook
   (`.claude/hooks/stop-hook.sh`) warns the session that made the branch, and this
   session-**start** report reaches the session that will actually pick it up. Both
   are **warning-only** and both delegate to the same judgement,
   `scripts/stranded_branches.py` — a second copy would drift, and the drifting one
   would be the start report. Silence when `gh` cannot answer: without PR state,
   "in review" and "abandoned" are indistinguishable, and a check that cannot tell
   them apart is a false-positive generator.

2. Register non-trivial active work in `.claude/work-claims/<slug>.json`, then
   **bind your worktree to it** so a commit cannot land on someone else's branch:

   ```bash
   git worktree add <path> -b <your-branch> origin/main   # never share a checkout
   cd <path> && python scripts/work_claim_branch_guard.py bind <slug>
   ```

   One-time per clone: `python scripts/work_claim_branch_guard.py install`
   (points `core.hooksPath` at `scripts/githooks`, which applies to every worktree).
3. Keep feature PRs scoped to source-of-truth files. Shared generated outputs
   should have a single writer, preferably the final integration PR.
4. After any PR merges, update remaining supervisor branches from `origin/main`
   before continuing them.
5. After a PR merges, run cleanup:

   ```powershell
   python scripts\supervisor_cleanup.py --merged-pr <number> --delete-merged-local
   ```

## Reviewer FAIL과 작업 landing은 분리한다

`FAIL = merge 금지`로 해석하지 않는다. 그 해석은 실패 작업을 dirty worktree에
쌓아 다음 세션의 병목으로 바꾼다. 상세 lifecycle은
`.claude/rules/incomplete-landing-lifecycle.md`가 SSOT다.

- reviewer FAIL 또는 upgrade-only BLOCKED 작업은 `LANDED_INCOMPLETE`로 explicit
  SHA·evaluation·실패 MUST·재현 명령과 함께 three-way `--merge`할 수 있다.
- compile/import, security, data-loss, destructive migration, false PASS,
  protected-domain 침범은 수리 전 merge하지 않는다.
- stale base·squash/rebase·explicit-file·work-claim guard는 그대로 적용한다.
- landing 뒤에도 plan은 active, claim은 merged-landed, `production_cutover`는
  `NOT_READY`다. PASS 전 plan archive/completion, claim close, release READY를
  선언하지 않는다.

## 동결 검토 사본은 프로비저닝해서 준다 — 「브라우저 레인은 못 돈다」는 사실이 아니다 (2026-08-27)

`git worktree add --detach` 로 만든 동결 사본에서 프론트엔드 레인이 안 도는 것은
**구조적 한계가 아니라 준비 부족**이다. 그 워크트리에는 gitignore 된 세 가지가 없고,
셋 다 20여 초면 재생된다:

| 없는 것 | 없을 때 증상 | 복구 |
|---|---|---|
| `apps/web/node_modules` | 모든 레인이 모듈 해소 실패 → *"레인을 돌 수 없다"* 로 읽힌다 | `cp -al`(하드링크, 디스크 0) |
| `apps/web/src/api/generated/*.ts` | 무관해 보이는 TypeScript 에러 수십 건 | `npm run codegen` |
| `apps/web/dist` | Playwright `webServer` 60초 타임아웃 | `npm run build` |

⚠️ **이것을 규칙으로 남기는 이유는 다섯 번 반복됐기 때문이다.** 한 웨이브의 독립 검토자
**다섯 명이 연속으로** *"동결 사본에 `node_modules` 가 없어 브라우저/Vitest/lint/build 를
확인하지 못했다"* 를 적었고, 매번 계약의 프론트엔드 절반이 **실행이 아니라 영수증으로**
판정됐다. 여섯 번째는 프로비저닝된 사본을 받아 **NOT-VERIFIED 를 0건** 으로 냈다.

```bash
bash scripts/provision_review_worktree.sh <목적지> [<sha>]   # 실측 25.7초
# ⚠️ 2026-08-31 — 이 스크립트는 `fcc-test-platform` 레포로 이사했다. 재는 대상
#    (`apps/web`)이 거기 있고, 여기서는 엔진 범위의 유일한 소유자
#    (`apps/web/package.json`)가 없어 **어느 모드로도 동작하지 않는다.**
#    프론트 리뷰 사본이 필요하면 그 레포에서 돌린다:
#      fcc-test-platform:scripts/provision_review_worktree.sh
```

- **고정된 Node 를 강제한다 — 그리고 그것을 「fnm 이 있는가」로 판정하지 않는다**
  (정정 2026-08-30). 저장소는 `apps/web/package.json` 의 `engines.node` 로 major 를
  고정하는데 머신 기본값은 흔히 다른 major 이고, **틀린 major 로 돌린 실패는 코드
  결함처럼 보인다**.

  ⚠️ **옛 문언은 처방까지 적었고(`fnm exec --using=22`) 그 처방이 이 머신에서 틀렸다.**
  스크립트가 묻던 것은 *「fnm 이 설치돼 있는가」* 였는데 그것은 **대리 지표**이고,
  fnm 이 없고 nvm 만 있는 머신에서 두 답이 갈린다 — 그때 스크립트는 stderr 로 경고
  한 줄을 흘리고 **모든 레인을 기본 node(24)로 돌렸다.** 운영자는 고정이 지켜졌다고
  믿는다. 즉 *「고정을 선언했다」* 와 *「고정이 실제로 적용됐다」* 가 그 출력에서
  같은 값이었다(→ `.claude/rules/check-axis-blindness.md`).

  지금은 **관측으로 판정한다** — `scripts/frontend_build_gate.py::find_satisfying_node_bin`
  이 fnm·nvm·PATH 후보를 **각각 실행해** `engines` 를 만족하는지 재고, 스크립트는 그
  답을 **소비만** 한다(셸에 두 번째 탐색 정책을 두지 않는다). 만족하는 후보가 **하나도
  없으면 경고가 아니라 거부**다(`exit 1`) — 경고는 스크롤을 타고 지나가고 레인은 그대로
  돈다. 검토자에게 출력되는 레인 접두사도 **선택된 러너에서 파생**되므로, 이 머신에서
  실제로 통하는 명령이 나온다. 버전 리터럴은 `apps/web/package.json` 에만 있다.
  봉인: `tests/test_frontend_build_gate.py` §runtime-selection axis.
- 검토 지시문에 *"브라우저 레인은 못 돈다"* 라고 미리 적지 말 것. 그 문장이 있으면
  검토자는 시도하지 않는다 — 다섯 번 그렇게 됐다.
- 사본은 **읽기 전용**이고, 검토 산출물은 트리 **밖**에 쓴다(레인이 도는 중
  `.claude/` 에 append 하면 그 레인이 없는 회귀를 보고한다).
- 스냅샷 재기준선(`test:e2e:visual:update`)은 검토 사본에서 **금지**다.

## Operating Rules

- Do not start two active branches with overlapping `scope` or `shared_outputs`
  unless their claim files explicitly document sequencing.
- Do not delete dirty worktrees during cleanup. Resolve, commit, or abandon them
  explicitly first.
- Prefer one canonical branch per milestone. Avoid parallel `audit`, `scoped`,
  and `final` branches unless each has a distinct claim.
- Treat `.claude/scripts/impact-tests-generated.sh`, generated OpenAPI files,
  and generated evaluation supplements as shared outputs.

## 공유 체크아웃에 커밋하지 마라 — 이제 훅이 막는다 (2026-08-06)

**git 의 "현재 브랜치"는 프로세스가 아니라 작업 디렉터리에 속한다.** 두 세션이 한 체크아웃을
공유하면 한쪽의 `git switch` 가 다른 쪽 HEAD 를 말없이 옮기고, 다음 `git commit` 은 그때
HEAD 가 가리키는 곳에 착지한다. git 에는 세션 개념도 잠금도 없다.

**실제 사고 (2026-08-06 `extraction-manifest-ownership-partition`)** — reflog 그대로:

```
@{6}  세션 A 가 자기 브랜치 생성
@{5}  세션 B: 그 브랜치에서 딴 데로 switch      ← A 의 발밑이 바뀜
@{3}  세션 B: ...-scope-v2 로 switch
@{2}  세션 A 의 커밋이 v2 에 착지
@{1}  세션 B 의 커밋
@{0}  세션 A 의 두 번째 커밋이 v2 에 착지
```

B 의 커밋이 A 의 두 커밋 **사이**에 끼어, A 의 것만 빼내려면 B 의 published history 를
재작성해야 했다. 위 "Required Flow" 1~2 는 사고 **이전에도 있었고 막지 못했다** —
**아무것도 강제하지 않는 규칙은 건너뛸 수 있는 규칙이다.**

- **게이트**: `scripts/githooks/pre-commit` → `scripts/work_claim_branch_guard.py check`.
  worktree 가 claim 에 bind 돼 있고 HEAD 가 그 claim 의 `branch` 와 다르면 **커밋 차단**.
- **bind 는 per-worktree**(`git rev-parse --git-path fcc-work-claim`) — linked worktree
  마다 독립이라 한 세션의 claim 이 다른 세션에 강요되지 않는다.
- **fail-open 이 의도다**: detached HEAD(= rebase/cherry-pick), unbound worktree,
  사라진 claim, `branch` 없는 claim 은 전부 **통과 + stderr 경고**. 위생 게이트가
  오탐을 내면 사람들이 꺼버리고, 꺼진 게이트는 아무것도 지키지 않는다. 차단하는 경우는
  확신할 수 있는 하나뿐이다 — bind 존재 + claim 이 브랜치를 명시 + HEAD 가 다름.
- **비상 우회**: `FCC_ALLOW_BRANCH_MISMATCH=1 git commit ...` (일부러 시끄럽게 만들었다).
- 보호: `tests/test_work_claim_branch_guard.py` — 사고 형상 차단 + 오탐 6종 + **실제 임시
  git 레포에서 훅 차단/허용/우회 end-to-end** + bind per-worktree 독립성.

## Integration checkpoint와 closure를 분리한다 (2026-08-25)

Repository integration and work completion are separate states. A session may land an
explicit-path checkpoint commit, push it, and merge it while focused or full checks are
still red, provided the red checks are recorded as FAIL/BLOCKED and no evidence is deleted
or relabeled. This prevents useful work from accumulating in a shared worktree.

Checkpoint landing requires:

- claim remains `review` and its plan remains under `active/`; `merged` means lifecycle
  completion and is reserved for the post-review closure step;
- commit SHA, cutoff HEAD/status, explicit changed paths, focused results, failed gates,
  and next owner/action are recorded;
- explicit-path staging, `git diff --check`, applicable focused checks, and the
  merge-readiness guard are run; use a merge commit and never force-push, squash, or rewrite
  history;
- `production_cutover=NOT_READY` is preserved and release/operations docs are not finalized.

Only a fresh independent reviewer PASS authorizes final documentation, claim `merged`, plan
archive, production readiness, or cutover. A checkpoint merge is integrated work, not a
PASS declaration.

## 옛 base 에서 머지하지 마라 — 이제 게이트가 있다 (2026-08-08)

**브랜치가 green 인 것은 그 브랜치가 갈라진 base 위에서의 사실이다.** 착지하는 곳은
그 base 가 아니라 **지금의 main** 이다. 그 둘이 벌어진 채로 머지하면, 무엇이 사라졌는지
아무도 보지 못한 채 사라진다.

**실제 사고 (2026-08-07 PR #126)** — PR #126 이 PR #127 착지 **이전**의 base 에서
갈라진 브랜치를 **스쿼시**로 머지했다. 스쿼시는 브랜치의 *자기 base 대비 전체 diff* 를
재생하므로, 그것을 오늘의 main 에 재생하자 **#127 이 통째로 되돌아갔다(13파일)**.
아무것도 실패하지 않았다. 유일한 증상은 착지 커밋의 **감사 블록 누락**이었고(스쿼시가
본문을 PR 설명으로 대체했으므로), 코드 손실 자체는 아무도 보지 못했다. 복구는 별도
PR #133 이 필요했고 오염 커밋 `0cf7acee` 는 되돌릴 수 없는 역사다.

사고는 **두 조건의 곱**이었다:

1. 머지 방식이 3-way 머지가 아니라 diff 재생이었고,
2. 브랜치의 base 가 낡았다.

No-squash 규칙은 조건 1을 — **문장으로** — 다뤘다. 사고 **이전에도 있었고 막지 못했다.**
`work_claim_branch_guard.py` 를 쓰게 만든 것과 같은 이유다: **아무것도 강제하지 않는
규칙은 건너뛸 수 있는 규칙이다.**

조건 2 는 진짜 머지 커밋에서도 남는다. 3-way 머지가 #127 을 되돌릴 수는 없지만,
**텍스트 병합 가능성은 의미 합치가 아니다.**

- **게이트**: `scripts/merge_readiness_guard.py`. 머지 직전에 돌린다.

  ```bash
  python3 scripts/merge_readiness_guard.py check --pr <N>      # 판정만
  python3 scripts/merge_readiness_guard.py merge <N> --update  # 판정 + 갱신 + 머지
  ```

- **판정식은 하나뿐이고 `--is-ancestor` 가 아니다** — `git log <base> --not <head>
  --no-merges` 가 **0줄**. base 에만 있고 브랜치가 못 본 *실제 작업 커밋*의 부재다.
  `--is-ancestor` 는 브랜치가 base 를 머지해 들여온 형상(= 신선해지는 통상적 방법)에서
  다른 답을 준다. `--no-merges` 는 통합 머지 커밋이 "놓친 작업"으로 읽히는 것을 막는다
  — 그것이 오탐으로 게이트를 삭제당하게 만드는 모드다.
- **두 축은 독립이고 우회도 독립이다** — `FCC_ALLOW_STALE_MERGE_BASE=1` /
  `FCC_ALLOW_SQUASH_MERGE=1`. 하나를 면제해도 나머지는 계속 막는다. 한 우회로 둘 다
  풀리면 사고 형상이 우회 하나로 통과한다.
- **fail-open 이 의도다** — base ref 미해소, 원격 불통, detached HEAD, 비교 실패는
  전부 통과 + stderr 경고. 차단하는 경우는 확신할 수 있는 둘뿐이다.
- **`pre-push` 훅은 경고 전용, 절대 차단하지 않는다** — WIP 브랜치 push 는 정당하다.
  다만 push 는 서버 머지 직전의 마지막 로컬 순간이라 경고를 놓을 유일한 자리다.
  `core.hooksPath` 는 `work_claim_branch_guard.py install` 이 이미 설정한다.
  훅의 원격 왕복은 `FETCH_TIMEOUT_SECONDS` 로 제한된다 — 느린 원격이 push 를 잡아두면
  그 게이트는 삭제당한다.
- **⚠️ 자동으로 도는 것은 아무것도 차단하지 않는다** — 정직하게 적는다. 자동 발화하는 것은
  경고 전용 `pre-push` 뿐이고, `check`/`merge` 는 **호출해야** 차단한다. 손으로 친
  `gh pr merge --squash` 를 가로채는 것은 없다. 즉 이 규칙의 자기 논거("아무것도 강제하지
  않는 규칙은 건너뛸 수 있는 규칙이다")가 이 게이트에도 **부분적으로** 적용되며, 진짜
  건너뛸 수 없는 것은 아래 서버측 branch protection 하나뿐이다. 그때까지는 **절차가 게이트다.**
- **서버측 종착지**: GitHub branch protection 의 *"Require branches to be up to date
  before merging"*(strict required status checks). 그것이 업계표준 답이고 건너뛸 수
  없다. **지금 켜지 않는 이유**는 그것이 *"Require status checks to pass before
  merging"* 의 하위 옵션인데 이 저장소의 Actions 가 결제 계층에서 막혀 체크가 하나도
  보고되지 않기 때문이다 — 켜면 **모든 머지가 영구 차단**된다. 결제 복구에 게이트한다.
- **⚠️ 무인 통합기는 이 규칙의 예외가 아니라 가장 위험한 적용 지점이었다 (2026-08-12)** —
  `.claude/scripts/supervisor_pr_integrator.py` 는 `--merge-method` 기본값이 **`squash`**
  였고 `gh pr merge` 를 **직접** 호출해 위 게이트를 통째로 우회했다. 즉 사고를 만든 두
  조건(diff 재생 + 옛 base)을 **아무도 보지 않는 자리에서** 기본값으로 갖고 있었다.
  위 "자동으로 도는 것은 아무것도 차단하지 않는다" 문단이 *"손으로 친 `gh pr merge
  --squash` 를 가로채는 것은 없다"* 고 적은 것은 맞았지만, **손으로 치지 않는 경로가
  하나 있었다는 사실**을 빠뜨렸다. 정정: 기본값을 `merge` 로 바꾸고 `_merge_pr` 이
  `scripts/merge_readiness_guard.py merge <pr> --update` 를 **경유**한다(`gh` 직접 호출
  0건, AST 음성 단언). 게이트 스크립트 부재는 여기서만 **fail-closed** 다 — 이 저장소의
  위생 훅이 fail-open 인 이유(오탐이 결함보다 비싸다)가 성립하지 않는다. 되돌릴 수 없는
  바깥 행위이고, 이 스크립트는 이미 형제 GC 스크립트를 같은 방식으로 요구한다.
- **supervisor 자동 커밋은 자가점검을 위조하지 않는다 (2026-08-12)** — `_auto_commit`/
  `_checkpoint_commit` 이 조립하는 본문에는 17항목이 없고, 2026-08-10 차단형
  `commit-msg` 게이트 이후 **두 경로 모두 이 저장소에서 커밋 불가**였다(실측 `exit=1`,
  17/17 missing). 정확히는 *게이트가 면제하는 경우를 제외하고* 불가다 — 게이트 스크립트가
  없는 트리, 진행 중인 merge/cherry-pick/rebase, 재생된 `fixup!` 제목, 우회 변수. 그
  면제 목록을 preflight 가 **다시 적지 않는** 것이 이 수정의 요지다(아래).
  수리는 17줄을 **자동 생성하지 않는 쪽**이다 — supervisor 는 A-1~C-5 를
  판정할 근거가 없고(일한 것은 Implementer 다), D-1~D-3 은 Implementer 가 만들지 않은
  커밋에 대한 **보관 사실**이라 그쪽 자기진술을 가져오는 것은 대리 완료다. 대신 `git add`
  **이전에** 게이트 SSOT 로 preflight 해 이름을 대고 거부하며 변경은 워킹 트리에 남긴다.
  ⚠️ **preflight 는 `check_message_file` 을 부른다 — 훅이 exec 하는 바로 그 함수다.**
  술어 조각으로 판정을 재조립한 첫 구현은 **한 번에 네 곳에서 갈라졌다**(진행 중 작업
  면제 · `fixup!` 면제 · 빈 메시지 · `=0` 은 우회가 아니라는 의미). *게이트보다 엄격한
  preflight 는 게이트가 받아줄 커밋을 막고, 그런 검사는 삭제된다.* 게이트를 못 읽거나
  진입점이 없으면 **fail-open + loud**(훅이 권위다 — import 가 됐다는 것만으로 쓸 수 있는
  guard 가 아니라서 진입점 존재를 확인한다). 성공 경로의 훅 stderr 도 보존한다 —
  work-claim fail-open·인터프리터 부재·우회 변수 사용은 전부 *설계된 degradation 고지*이고,
  성공했다고 버리면 고지가 침묵이 된다.
  ⚠️ **남은 false-green**: 커밋이 거부돼도 CLI 는 `approved`/exit 0 이고
  `supervisor_watch.py` 가 그것을 성공으로 읽는다. 장부 등재(`2026-08-12 supervisor`).
  → **상환됨**: `_finish` 의 `unfulfilled` 집계가 status 를 `SIDE_EFFECT_INCOMPLETE_STATUS`
  로 바꾸고 watcher 가 같은 상수를 미러한다.

- **그래서 자동 커밋은 이렇게 다시 동작한다 — 두 당사자, 두 블록 (2026-08-12)** —
  17줄을 **누가 답하는가**가 계약으로 정해졌다. `A-1~C-5` 는 **Implementer 자기진술**로,
  보고 끝의 델리미터 블록(`<!-- SELF-AUDIT-BEGIN/END -->`, 형식은 `.claude/contracts/
  dev-review-loop.md` 멘트 A §6)에서 **verbatim 이식**한다. `D-1~D-3` 은 **supervisor 의
  보관 기록**으로, 자기가 만드는 커밋에서 파생한다. **합치지 않는다** — 합치면 supervisor 가
  Implementer 의 목소리로 자기 보관 사실을 주장한다.

  세 가지를 특히 기억할 것:

  1. **Implementer 블록은 미검증이다.** 커밋 경로는 `PASS` 의 근거를 확인하지 않고 게이트도
     형식만 본다. 의심하는 채널은 Reviewer 이고 그 승인이 커밋의 전제다. **항목 하나만
     교차검증하지 말 것** — 나머지 열셋도 검증된 것처럼 읽힌다.
  2. **`D-3` 은 `SKIP` 이고, 그 대신 실행된다.** 커밋 객체 대조는 메시지 작성 시점에 없는
     사실이라 `PASS` 는 forward-reference 위조다. supervisor 가 **정확한 SHA** 에 결박해
     (`HEAD` 가 아니다 — 공유 체크아웃에서 남의 커밋이 그 사이에 들어온다) `git show
     --name-only -z` 로 대조하고, 술어는 **부분집합**이다(pathspec 에 있으나 순변경 0인
     경로는 정당하게 없다). 미확인이면 **push 하지 않는다**.
  3. **없는 블록을 지어내지 않는다.** 없거나 형식이 깨지면 거부하고 변경을 워킹 트리에
     남긴다 — *가드를 아예 못 읽는* 경우만 fail-open 이다(그 저장소는 이 체크리스트를
     채택하지 않았을 수 있다). *"물을 수 없다"* 와 *"물었고 답이 아니오"* 는 다르다.
- 보호: `tests/test_merge_readiness_guard.py`(순수 판정 + 실 임시 레포 end-to-end
  사고 재현/복구 + 훅 exit 0) + `tests/test_meta_self_audit_invariant.py::
  TestMergeBaseFreshnessRuleIsRecorded`(3 사이트 drift 게이트).

## 자가점검 없는 커밋은 이제 훅이 막는다 (2026-08-10, 운영자 판정)

**같은 형태가 하루에 두 번 일어났다.** `e3a5e2ca`(타 세션)와 `b925d2ef`(같은 세션)가 17줄
자가점검 없이 머지됐고 **둘 다 복구 불가**였다 — 다른 세션이 이미 그 위에 쌓아, history
rewrite 는 선택지가 아니다. `test_meta_self_audit_invariant.py` 의 forward-exception 목록이
그 흉터다. 사후 게이트는 *알려주지만* 막지 않고, 알려주는 시점에는 대개 이미 push 된 뒤다.

이 저장소는 같은 결론을 두 번 냈다(work-claim 브랜치 가드, 머지 신선도 가드):
***아무것도 강제하지 않는 규칙은 건너뛸 수 있는 규칙이다.***

- **게이트**: `scripts/githooks/commit-msg` → `scripts/self_audit_message.py`.
  `pre-commit` 이 아니라 **`commit-msg`** 다 — pre-commit 은 메시지가 존재하기 전에 돌므로
  검증 대상 자체를 볼 수 없다.
- **판정은 사후 게이트와 같은 정의를 쓴다.** `self_audit_message.py` 가 유일 정의이고
  `tests/test_meta_self_audit_invariant.py` 가 그것을 **import** 한다(사본 0, 봉인됨).
  훅이 자기 사본을 들고 있으면 사후 게이트가 통과시키는 메시지를 훅이 거절하고, 그러면
  사람들이 훅을 끄고, 꺼진 훅이 트리에 남아 보호받고 있다는 착시만 남는다.
- **`core.hooksPath` 는 저장소 전역**이라 이 훅은 살아 있는 워크트리 **전부**에 즉시 적용된다.
  그래서 운영자 판정 사항이었고, 2026-08-10 에 승인됐다.
- **fail-open 이 의도다**: 인터프리터 부재, 메시지 파일 판독 실패, **머지/체리픽/리버트/
  rebase 진행 중**(`MERGE_HEAD` 등 마커 존재), `fixup!`/`squash!`/`amend!`/`Revert "` 로
  시작하는 **재생된** 메시지는 전부 통과 + stderr 경고. 사후 게이트가 `--no-merges` 로
  머지를 면제하므로 훅도 면제해야 한다 — **훅이 게이트보다 엄격하면 그 훅은 꺼진다.**
- **비상 우회**: `FCC_ALLOW_MISSING_SELF_AUDIT=1 git commit ...` (일부러 시끄럽게 만들었다).
- **거절은 무엇이 빠졌는지 이름을 댄다** — 빠진 항목 번호, 중복된 항목 번호, 트리거 값.
  `b925d2ef` 는 17줄을 **다 갖고** 트레일러 값만 틀렸고, 저자는 문서를 따랐을 뿐이었다.
- 보호: `tests/test_self_audit_commit_msg_gate.py` — 실제 임시 git 저장소에서 차단·허용·
  우회·머지/fixup/체리픽 면제·주석 라인 무효·인터프리터 부재 fail-open end-to-end.

## `PASS` 는 `SKIP` 보다 싸면 안 된다 — 값 축 (2026-09-02, `self-audit-value-axis`)

위 게이트는 17줄의 **형식**만 본다. 값의 진위를 보는 것은 없었고, 그래서 `PASS` 를
적는 것이 `SKIP` 을 적는 것보다 **쌌다**. 두 세션이 연달아 같은 위조를 했고
(2026-08-31 · 2026-09-01), 두 번째는 *"자가점검에 미래를 적지 마라"* 라고 경고하는
인계문을 **읽은 뒤에** 넷을 위조했다. 부주의가 아니라 경제다.

**실측이 처방을 세 번 뒤집었다.** 장부는 *"`B-3`(장부)·`B-4`(평가서)는 커밋에서
기계로 확인 가능"* 이라고 적었지만, 감사 커밋 **556개** 전수에 물으면:

| 판정 범위 | `B-3` | `B-4` | `A-3` |
|---|---:|---:|---:|
| 커밋 단위 "PASS ⟹ 이 커밋이 산출물을 담는다" | 61.7% | 56.4% | 96.2% |
| 브랜치(PR) 단위 | 29.4% | 36.7% | 85.5% |

**56% 는 56% 위조가 아니다** — 웨이브는 7커밋인데 평가서를 담는 커밋은 1개다. 즉
관행이 웨이브 단위 진술이고, 그것을 커밋 단위로 차단하면 오탐 56% 이며 그런 게이트는
**삭제된다**(이 문서가 세 번 낸 결론).

그래서 **게이트를 넓히지 않고 축을 나눴다.** 셋이고 서로 다른 질문에 답한다.

- **형식 축** — 무변경. `has_physical_full_audit` 는 여전히 옛 질문만 답하므로
  **사후 역사 게이트가 붉어지지 않는다**.
- **사유 축**(`reasonless_rows`) — 상태 뒤에 아무것도 없는 행은 *어테스테이션이 아니라
  단어 PASS 다*. **새 규칙이 아니다**: 같은 모듈의 `extract_implementer_audit` 가 이미
  그 형태와 자리표시자 사유를 거부하고 있었다 — 한 모듈이 한 문법을 두 잣대로
  판정했고, 커밋 메시지는 싼 쪽을 지났다. 실측 최근 300 감사 커밋 중 **99개(33%)가
  17줄 전부를 맨몸**으로 적는다.
- **값 축**(`unsupported_claims`) — 선언된 항목의 `PASS` 는 관측으로 뒷받침돼야 한다.

### 값 축이 검사하는 것은 둘뿐이고, 그 사실을 매번 말한다

| 항목 | 산출물 |
|---|---|
| `B-3` | `.claude/exec-plans/tech-debt-tracker.md` |
| `B-4` | `.claude/evaluations/*.md` |

선정 기준은 판단이 아니라 **항목 본문의 동사**다 — 등재/추가/작성은 *생산* 동사라
참된 `PASS` 가 "내가 만들었다" 말고 다른 뜻일 수 없다. 기각한 후보와 실측 사유:

- `A-3` — 항목 본문이 `PASS`="검토 완료, 해당 항목 없음"을 **명시 허가**한다(85.5%). 영구 불가.
- `B-1`·`B-2` — 기존 glob 이 이미 라우팅하는 새 테스트 파일은 편집이 필요 없어
  `PASS`="이미 등재됨"이 정당하다(45.5% / 48.9%). 진짜 오탐 채널.
- `A-2` — 20.0% 이지만 항목이 등재 **검토**라고 적어 같은 읽기를 허가한다.

⚠️ **부분 강제는 나머지를 검증된 것처럼 읽히게 한다.** 그래서
`self_audit_message.VALUE_AXIS_LIMITATION` 이 **값 축이 돌 때마다** stderr 로
검사한 둘과 검사하지 **않은 열다섯을 이름으로** 고지한다. 미검사 목록은
`SELF_AUDIT_ITEMS − 선언 집합` **파생**이라 체크리스트에 항목이 늘면 자동으로
미검사로 들어간다(손 열거는 다음 항목이 추가된 날 조용히 커버리지를 잃는다).
`hook_bypass_guard.GUARDRAIL_LIMITATION` · `supervisor_run.WRAPPER_LIMITATION` 과
같은 형상이다 — 모듈 상수 + 매 실행 고지 + 이 문단, 세 곳.

### 참조는 **이 웨이브 안**에서만 통한다 — 그리고 그 창의 한계를 적어 둔다

⚠️ **적대적 평가가 이 자리에서 치명 구멍을 찾았다.** 처음 판은 *도달 가능한 아무
조상*을 받았는데, 이 저장소 조상 **677개 중 470개**가 tracker 나 evaluations 를
만진다 — 즉 값 축이 *"470개 중 아무 hex 하나 적어라"* 였고 `PASS` 는 다시 공짜였다.
자기 봉인은 가짜 해소기를 주입해 판정의 분기만 시험했기에 이것을 못 봤다.

창은 **웨이브**다 — HEAD 도달 가능 ∧ 공유 역사 **비포함**(`merge-base` with
`@{upstream}`/`origin/main`/`origin/HEAD`). 의미상으로도 그것이 맞다: 이전 PR 의
산출물은 `SKIP` 이 정답이다.

⚠️ **`WAVE_WINDOW_LIMITATION` — 그 창의 바닥은 저자가 움직일 수 있는 로컬 ref 에서
파생된다.** `git update-ref refs/remotes/origin/main <root>` 한 번이면, 또는 그냥
fetch 를 안 하면, 창이 다시 역사 전체로 넓어진다 — **환경변수 없이, 조용히**.
원격에 물어 검증하려면 커밋 훅 안에 네트워크 왕복을 넣어야 하고 그것이 훅이
삭제되는 방식이다. 그러므로 이것은 **습관적 위조를 막는 guardrail 이지 작정한
저자를 막는 방어층이 아니다** — `hook_bypass_guard.GUARDRAIL_LIMITATION` 이 이미
갖는 지위다. 이 문장이 사라지면 다음 세션이 방어층으로 믿고 그 위에 다른 것을 얹는다.

### 후방 참조는 싸고 전방 참조는 불가능하다

산출물이 이 커밋에 없으면 **그것을 담은 커밋을 지목**할 수 있다
(`B-4: PASS — 평가서는 3f2a1b9`). 해소기는 그 객체가 **HEAD 에서 도달 가능**하고
**실제로 그 산출물을 담는지** 확인한다. 그러므로:

> **아직 없는 커밋은 이름이 없다.** 전방 참조는 거짓말이 아니라 **적을 수 없는 문장**이 된다.

메모리 `self-audit-must-not-forward-reference` 가 산문으로 적던 규칙이 산술이 됐다.
정직한 대안은 늘 둘이다 — 산출물을 이 커밋에 넣거나, `SKIP — 왜 이 커밋의 범위가
아닌지`. **`SKIP` 은 심문하지 않는다**(심문하면 두 답이 다시 같은 값이 되고, 그것이
이 웨이브가 고치는 경제다).

### 관측은 인자다

판정 함수는 파일도 git 도 열지 않는다 — 변경 경로와 참조 해소기를 **인자로 받는다**
(`artifact_custody_policy` · `workbook_upload_gc_policy` 가 쓰는 그 분리). 관측이
없으면 축을 **건너뛴다**(fail-open) — 게이트가 볼 수 없어서 거부하는 것이 훅을
삭제당하게 만드는 실패 모드다.

⚠️ **관측은 인덱스가 아니라 커밋이다.** 이 저장소의 관행인
`git commit -F <msg> -- <files>` 에서 git 은 임시 인덱스를 만들어 `GIT_INDEX_FILE` 로
내보내므로 `git diff --cached` 가 **pathspec 제한 목록**을 답한다(실측). 즉 산출물이
**스테이징돼 있어도** 그 커밋에 정당하게 없을 수 있고, 인덱스를 읽는 게이트는 이
축이 거부하려는 바로 그 주장을 통과시킨다. 봉인이 실제 저장소에서 그 형상을 만든다.

⚠️ **`--amend` 는 탐지할 수 없어서 관측으로 푼다.** amend 의 스테이징 diff 는 **HEAD**
— 곧 대체될 그 커밋 — 대비라, 산출물을 **정말로 담고 있는** 커밋이 빈 diff 를 보이고
거부된다(실측: 고치기 전에 실제로 거부됐다). 그리고 git 은 `commit-msg` 훅에 amend
신호를 **주지 않는다** — 환경변수 동일, `.git` 마커 동일, `prepare-commit-msg` 도
`-m`/`-F` 와 함께면 source `message` + sha 없음(실측). 그래서 **HEAD 의 경로를 관측에
포함**한다: amend 에는 **정확**하고, 보통 커밋에는 **부모 1커밋 관용**이다(산출물을
착지시킨 바로 다음 커밋은 지목 없이 `PASS` 가능). ⚠️ **그 대가를 숨기지 않는다** —
봉인이 *조부모는 안 된다*를 함께 단언하므로 관용은 한 칸에서 멈추고, 그 한 칸은
어차피 저자가 명시적으로 적을 수 있었던 참조다. git 이 언젠가 amend 를 구분해 주면
`test_git_gives_the_hook_no_amend_signal` 이 red 로 알려주고, 그때 관용은 사유를 잃는다.

- **사유는 *보이는* 문자를 요구한다** — `PASS \xa0`(NBSP)는 화면에서 맨몸과 구분되지
  않으므로 사유가 아니다. 판정은 `isprintable() and not isspace()` 이고 형제
  `extract_implementer_audit` 이 같은 함수를 쓴다(문법 하나).
- **집행점 둘은 같은 문법을 쓴다** — 관측은 `_paths_that_gained_content`,
  참조는 `resolve_reference(token, floor=…, tip=…)`. ⚠️ 한때 갈라져 있었고
  (사후 게이트가 `--name-only` + 창 없음) 그 결과 **훅이 닿지 않는 모집단**
  (`--no-verify` 한 번이면 누구나 그 모집단이다)에 치명 구멍이 그대로 열려 있었다.
  **anchor 만 다르다** — 훅은 `tip=HEAD`·`floor=웨이브 베이스`, 사후 게이트는
  `tip=그 커밋`·`floor=축 baseline`. 후자가 더 성긴 이유는 **머지된 브랜치의 분기점은
  사후에 복원 불가**이기 때문이고, 그 차이는 사고가 아니라 **이름 붙인 비대칭**이다.
- **비상 우회**: 형식 축과 **같은** `FCC_ALLOW_MISSING_SELF_AUDIT=1`(축마다 우회를
  따로 두면 우회 표면만 늘고, 이 셋은 같은 한 사건 — *"이 메시지를 믿을 수 있는가"* — 을 답한다).
- **사후 게이트는 forward-only**(`VALUE_AXIS_BASELINE_SHA` 이후). 역사는 이 규칙을
  제안받은 적이 없다.
- ⚠️ **남는 위조 비용은 정직하게 적는다** — 값 축이 요구하는 최소치는 *기존 장부에
  한 줄, 아무 평가서에 한 줄*이다. 그것을 좁히려면 "이 웨이브의 평가서"를 기계가
  알아야 하는데 slug 는 커밋이 말해주지 않는다. 축이 없애는 것은 **아무것도 안 하고
  `PASS` 적기**이지 *의도적 위조*가 아니다.
- 보호: `tests/test_self_audit_commit_msg_gate.py::TestTheReasonAxis` ·
  `::TestTheValueAxis` · `::TestTheValueAxisEndToEnd` ·
  `::TestThePartialGateAnnouncesItsOwnEdges`.

## Work-claim 상태 어휘 (2026-08-13, CLAUDE.md 에서 이관 2026-08-29)

> 이 절은 `CLAUDE.md` §Session Hygiene Rules 에 있었다. 주제가 `.claude/work-claims/*.json`
> 100% 라 이 파일의 `paths: .claude/**` 트리거와 정확히 일치하고, 무조건 로드 예산
> (`tests/test_claude_md_context_budget.py`)에서 1,368 B 를 회수한다. CLAUDE.md 에는 포인터가 남는다.

`.claude/work-claims/*.json` 의 `status` 는 **정확히 다섯 토큰**이다 — `active` · `blocked` · `review`(= 범위 소유 중) / `merged` · `abandoned`(= 종결). 정의는 `scripts/work_claim_status.py::ClaimStatus` 하나이고 `.claude/work-claims/README.md` 와 집합 상등으로 봉인된다. **`complete`/`completed`/`closed` 로 닫지 말 것** — 그것이 이 게이트 이전의 실제 어휘였고(150 파일에 14종), `active_claims()` 술어가 3종만 알아 *"아직 소유 중인데 preflight 에 안 보이는"* claim 을 만들었다(= 두 세션이 같은 파일을 만지는 그 실패). 웨이브를 닫을 때는 `merged`(= 이 저장소의 완료 판정은 "머지됨"), 줍지 않기로 했으면 `abandoned`. 미지 토큰은 **런타임에서 소유 중으로 degrade + loud 경고**(안전한 방향)이고 **게이트에서는 red** 다 — 게이트 메시지가 파일명·받은 토큰·정식 집합을 이름으로 댄다. 그리고 **계획서의 위치는 이 상태에서 파생된다**(`scripts/exec_plan_buckets.py`): 소유 중 → `active/`, `merged` → `completed/`, `abandoned`·claim 없음 → `archive/`. claim 이 **없는** `active/` 계획서는 차단이 아니라 preflight 세션 시작 **경고**다. 보호: `/verify-supervisor-workflow-hygiene`.

## Scratch-Directory Hygiene (`.claude/agent-loop-*`)

Each supervisor run writes a scratch directory `.claude/agent-loop-<slug>/`
(state.json + iteration-NN/ + tee'd `.log` files). These are gitignored
(`.gitignore` `.claude/agent-loop*/`) and exist only to resume/audit a single
loop. A loop is **finished** when its `state.json` carries a TERMINAL status
(`approved` / `max_iterations_reached` / `stopped_*` / `dry_run_prepared` /
`completed` / `timeout` / `idle_timeout`) — it will never resume, so the directory
is disposable.

- **Auto-prune**: `dev_review_supervisor.py main()` calls
  `prune_terminated_agent_loops()` at startup, deleting finished loops older than
  `--prune-keep-days` (default 7) while always excluding the current loop and any
  loop with a missing/in-flight status. Opt out with `--no-prune`. This keeps
  `.claude/` from growing unbounded with no manual step.
- **Manual sweep**:

  ```powershell
  python .claude\scripts\clean_agent_loops.py --keep-days 0 --dry-run   # preview
  python .claude\scripts\clean_agent_loops.py --keep-days 0             # delete all finished
  ```

  `--grace-minutes` (default 60) never deletes a loop touched within the window.
- Deletion policy is a single source of truth in
  `dev_review_supervisor.prune_terminated_agent_loops`; the auto-prune and the CLI
  both delegate to it. Regression-sealed by
  `tests/test_dev_review_supervisor_observability.py` (`*prune*`).

## 선재 실패 차분 판정 (Pre-existing Failure Attribution)

baseline 레인이 `exit=0` 이 아닌 동안, 웨이브의 완료 여부는 다음으로**만** 판정한다. 이 규칙은
`platform-backend-debt-closure`(2026-07-30)와 `openapi-nullable-ref-oneof`(2026-07-30)가 **각자 재발명한 것**을
전역화한 것이다 — 웨이브 로컬 문서에만 두면 다음 웨이브가 물려받지 못한다.

1. **이름 단위 집합 포함** — `실패집합(브랜치) ⊆ 실패집합(pristine base)`.
   **개수 비교 금지**: 신규 회귀가 선재 실패와 1:1 교체돼도 개수는 같으므로, 차분 기준이 **회귀 세탁 채널**이 된다.
2. **pristine base 재현 실측** — 작업 트리와 격리된 트리에서 같은 실패를 재현하고 그 사실을 자평에 적는다.
   **"관련 파일이 byte-identical 이라 무관하다"는 필요조건일 뿐 충분조건이 아니다** — 간접 import 로 결과가 달라질 수 있다.

   ⚠️ **재정정 2026-09-02 — `scripts/provision_review_worktree.sh` 는 이 저장소에 없다.**
   레포 분리(`5b63e4dd`)로 `fcc-test-platform` 으로 이사했고, **이 파일의 §동결 검토 사본 절은
   그 사실을 이미 적고 있다.** 즉 정정이 사본 전부에 닿지 않았고, 사본이 **같은 파일 안**에
   있어 더 나쁘다 — 이 항목에서 멈춘 독자는 그 절을 영영 보지 않는다.
   **여기서는 `git worktree add --detach <base>` 로 만든다.** 아래 2026-08-29 경고가 걸었던
   축(gitignore 된 `apps/web` 빌드 산출물)은 그 트리가 저장소를 떠나면서 **이 저장소에서
   성립하지 않는다**. ⚠️ 다만 **원칙은 살아 있다** — pristine 과 작업 트리가 *커밋 내용*
   말고 다른 축에서 다르면 그 차이가 「내 회귀」로 읽힌다. 프론트 사본이 필요하면 그
   레포에서 `fcc-test-platform:scripts/provision_review_worktree.sh` 를 쓴다.

   <details><summary>2026-08-29 원 경고 (그 산출물이 이 트리에 있던 동안의 사유)</summary>
   차분이 재는 축은 *커밋 내용*인데 두 트리는 그것 말고도 다르다 — 메인 체크아웃에는
   `apps/web/dist/` · `apps/web/src/api/generated/*.ts` 같은 **gitignore 빌드 산출물**이 쌓여 있고
   `--detach` pristine 에는 없다. 그리고 **그 산출물을 스캔하는 테스트가 있다**(생성 TS 의
   `Record<string, never>` · 번들 안의 퇴역 토큰). `git status` 는 양쪽 다 깨끗하다고 답하므로 그 축을
   볼 수 없고, **「내 회귀」와 「트리 상태 차이」가 같은 출력**이 된다
   (→ `.claude/rules/check-axis-blindness.md`).

   실측 2026-08-29: 「신규 실패 2건」이 나왔고 둘 다 이것이었다 —
   `test_frontend_architecture_conformance::…test_generated_component_schemas_do_not_collapse_to_never`
   와 `test_sample_inventory_import_retirement::test_active_tree_has_no_retired_operation_vocabulary`.
   `git diff --name-only origin/main | grep apps/web` 은 **0건**이었다. pristine 에 같은 산출물을
   복제하고 그 둘만 재실행하자 **동일하게 실패** → 이름 집합 동일, 차분 PASS.
   증명도 검사를 더하지 말고 **두 트리를 구분되지 않게 만들어서** 하라.

   </details>

   ⚠️ 그리고 pristine 워크트리 이름에는 **base SHA 를 담는다** — `fcc-wt-pristine-<sha>`.
   역할 이름(`-a`/`-b`)은 *어느 base 인가* 축을 갖지 않아 옛 것을 재사용해도 `git status` 는 깨끗하고
   **결과만 조용히 틀린다**(상대 웨이브의 변경이 내 차분에 신규 실패로 섞인다).
   같은 base 에서 갈라진 여러 세션은 pristine 을 **하나만** 만들어 읽기 전용으로 공유한다.
3. **간헐 실패는 연속 3회** — 1회 green 은 근거가 부족하다. **회차별 종료 코드와 소요 시간**을 기록한다.

   ⚠️ **그래도 뒤집히는 것이 남는다 — 「판정 불가」라는 세 번째 상태다** (2026-08-29 실측).
   `-m bench` **단독** 실행에서도 실행마다 답이 바뀌는 항목이 있다. 같은 커밋·같은 트리인데
   조용한 단독 실행에서만 실패하는 것도 있었다 — 부하 하나로 설명되지 않는다. **예산이 이 머신의
   잡음 폭보다 좁은 것**이고, 그 축에서 「코드가 느려졌다」와 「이 머신의 잡음」이 같은 값이다.

   > 반복 실행에서 **안정적으로** 실패하는 것만 판정한다 — 전부 실패하면 **선재**, 전부 통과하면
   > **무해**, 뒤집히면 **판정 불가**로 장부에 별도 축을 만든다.

   ⚠️ **판정 불가를 「통과」로도 「실패」로도 접지 말 것.** 접는 순간 그 항목은 다음 세션에
   *확정된 사실*로 읽히고, 두 방향 모두 틀릴 수 있다.

   ⚠️ **그리고 판정 불가를 「그 항목 하나의 문제」로 보지 마라 — 옆 게이트의 신호까지 먹는다.**
   실측 2026-08-29: 같은 레인에 **안정적으로 실패하는 실재 회귀**(export 경로 2.5× 지연)가 있었는데,
   그 빨강이 옆에서 **뒤집히던 항목들과 구분되지 않아** 레인 **전체가 「원래 그런 것」으로** 읽혔다.
   두 달 넘게 아무도 그것을 회귀로 읽지 않았다.

   > **흔들리는 게이트는 실재 회귀의 위장막이 된다.** 게이트를 신뢰할 수 없게 두면 잃는 것은
   > 그 게이트 하나가 아니라 **그 옆에 선 모든 게이트의 신호**다.

   그러므로 판정 불가는 *「지금은 답할 수 없으니 둔다」* 가 아니라 **레인 신뢰도의 부채**로 등재한다.

   ⚠️ **그리고 「판정 불가」는 종착지가 아니라 상환 대상이다** (2026-08-29 실측 정정). 그 축에
   앉은 항목을 **붙박이 예시로 적지 마라** — 상환되면 그 문장이 거짓이 된다. 실측이 답한 형태는
   이것이다:

   > **예산의 여유가 그 예산을 판정하는 추정량 자신의 산포보다 좁으면 뒤집힌다.
   > 그때 손봐야 하는 것은 예산이 아니라 추정량이다.**

   ⚠️ **그 산포는 「그 게이트가 실제로 도는 조건」에서 재야 한다 — 이 한정이 빠지면 측정이
   조용히 틀린다.** 실측 2026-08-29: 뒤집히는 항목들을 **완전 정적 호스트의 고립 실행**으로
   15회 재자 **위반 0** 이었고, 그래서 *「부하 없이도 뒤집힌다」* 는 결론이 나왔다가 **철회**됐다.
   실제 조건은 고립 실행이 아니라 **한 프로세스에서 117개를 연달아 도는 `-m bench` 레인**이고,
   그 조건에서는 pristine 4회 중 **3회**가 뒤집힌다. 결함은 **「부하 민감」도 「부하 무관」도
   아니다** — *예산의 여유가 **운영 조건**에서의 산포보다 좁다* 는 것이다.

   ⚠️ 이것 역시 이 저장소가 **이미 적어 둔 것**이다(`_measure` docstring, 2026-08-01:
   *「고립 실행에서는 세 시나리오 모두 통과하지만 bench 레인 전체와 섞이면 단발 p95 가 예산을
   넘겨 red 가 됐다」*). 그 문장을 **읽고 인용까지 하고서** 측정을 고립 실행으로 설계했다.
   → 재현 방지 물음: **「내 측정 조건이 그 게이트의 운영 조건과 같은가?」**
   (→ `.claude/rules/check-axis-blindness.md` — *조건* 축에서 「깨끗한 측정」과 「틀린 조건의
   측정」이 같은 모양이다.)

   예산을 올려 초록으로 만드는 것은 **탐지력을 파는 것**이고, 근거 없이 하면 다음 세션이 그
   예산을 사실로 읽는다. 올려야 한다면 잡음 폭을 **실측해** 그 위로 올리고 **얼마나 팔았는지
   적어라**. 추정량 쪽 손잡이가 먼저다 — `benchmark_harness` 의 min-of-trials 시행 횟수처럼
   **예산을 한 글자도 안 건드리고** 산포를 줄이는 축이 있는지 먼저 확인한다.
4. **선재 실패는 등재 대상이지 면제 대상이 아니다** — 발견 즉시 `tech-debt-tracker.md` 에 근본 원인과
   상환 방법을 한 줄 단위로 적고 소유 웨이브를 지정한다. **baseline 집합을 넓히는 것이 아니라 없애는 것이 목표다.**
   집합을 넓히는 조치는 한 번은 정당해도 반복되면 세탁이다.
5. **라벨 규율** — `"green"` 이라는 단어는 해당 게이트가 **실제로 `exit=0`** 일 때만 쓴다.
   선재 실패가 남아 있으면 라벨은 `차분 PASS (선재 N건)` 이다.
   `-m invariant` 레인은 **baseline 이 `0 failed` 인 동안** literal `0 failed` 를 요구한다 —
   그것이 정상 상태이고, 이 레인은 웨이브가 직접 소유하는 표면이기 때문이다.
   ⚠️ **baseline 자신이 red 인 동안에는 그 요구가 어떤 브랜치로도 만족될 수 없다**(운영자 판정
   2026-08-26). 옛 문언은 *"예외 없이 literal `0 failed`"* 였고, 그것은 웨이브가 **자기 힘으로 도달할 수
   없는 완료 조건**이라 M-2·M-25 와 같은 자기모순 형태였다. 실측이 그것을 드러냈다 — pristine
   `origin/main@7bd58971` 의 invariant 레인이 **10 failed / 6 이름**이고 그중 6건은 다른 세션의 이미
   푸시된 커밋에 대한 자가점검 누락이라 **재작성이 불가능**하다. 그 상태에서 이 절은 완화가 아니라
   **모순 제거**로 바뀐다:

   > baseline 이 red 인 동안 `-m invariant` 는 (a) 1번의 이름 단위 집합 포함을 만족하고,
   > (b) baseline 실패 **전건**이 `tech-debt-tracker.md` 에 소유자와 함께 등재돼 있으며,
   > (c) 브랜치 실패 중 **이 claim 이 선언한 파일에 있는 것이 0건**일 때 `차분 PASS` 다.

   (c) 가 옛 문언의 의도 — *웨이브가 직접 소유하는 표면은 깨끗해야 한다* — 를 그대로 옮긴 것이고,
   (b) 가 4번의 "등재 대상이지 면제 대상이 아니다" 를 이 레인에도 강제한다. **셋 다 만족해도 라벨은
   여전히 `green` 이 아니다.**

   ⚠️ 이 절의 기계화된 형태(`lane_baseline_diff` 계열)를 다른 웨이브가 짓고 있다. 착지하면 이 문단은
   그 도구에 위임하고 손 판정을 지운다 — 같은 판정이 두 곳에 살면 그중 하나가 먼저 낡는다.

**적용 전제**: ⚠️ **2026-08-26 현재 이 규칙은 발동 중이다.** 옛 문언은 *"2026-07-31
`test-gate-credibility-restoration` 이 routine 레인을 `exit=0` 으로 복구했으므로 현재는 이 규칙이 발동할
상태가 아니다"* 였고, 그 사실은 그 사이에 뒤집혔다. 실측 — pristine `origin/main@7bd58971` 에서 routine
**13 failed**, invariant **10 failed**(각각 13·6 이름). 이 문장이 실측과 어긋나 있으면 다음 웨이브가
*"규칙이 발동하지 않는다"* 를 읽고 자기 차분 기준을 재발명한다. 그것이 실제로 반복해서 일어났고, 그 반복
자체가 이 규칙을 전역화한 이유다. **그리고 이 상태를 정상으로 굳히지 말 것** — 4번대로 선재 실패는
등재 대상이고, 목표는 baseline 집합을 넓히는 것이 아니라 **없애는 것**이다.

## 부하 민감 게이트는 supervisor 루프 안에서 검증하지 않는다

**`--verify-cmd` 에 `-m bench`(또는 벽시계 지연 SLA 를 단언하는 어떤 게이트도) 를 넣지 마라.**

**Why**: supervisor 는 자기 자신이 부하원이다. `claude -p` 자식이 수백 MB 를 쓰며 도는 머신에서
p95/p99 지연 예산을 측정하면 초과한다. **측정자가 부하원이면 지연 측정은 성립하지 않는다.**
루프 안에서는 구조적으로 통과할 수 없으므로, 그 게이트를 넣는 순간 웨이브는 **무한 반려**에 빠진다.

**실측 (2026-08-01 `gate-and-deploy-path-parity`)** — 같은 코드, 같은 명령:

| 실행 환경 | `-m bench` |
|---|---|
| supervisor 루프 안 (iteration 1) | `2 failed` (`TestWalCheckpointDurabilityLatencyBudget`) |
| supervisor 루프 안 (iteration 2) | `4 failed` (**다른 테스트** — `TestBtSchemaDrivenDecimal~` 외) |
| 감독 세션, 유휴 상태 | **`102 passed, 0 failed`** |

두 iteration 이 **서로 다른 테스트**에서 실패한 것이 진단의 핵심이었다 —
**코드 결함이면 같은 것이 반복 실패한다.** 실패 대상이 이동하면 부하를 의심하라.

**How to apply**:
1. supervisor `--verify-cmd` 에는 **결정적 게이트만** 넣는다(`-m invariant`, routine, `npm` 계열).
2. 부하 민감 게이트는 **감독 세션이 머지 직전 유휴 상태에서 1회** 검증하고, 그 결과를 자평 게이트 표에 적는다.
3. 자평에는 **양쪽 사실을 모두** 적어라 — "유휴에서 green" 만 적으면, 다음 세션이 그 브랜치를 보고
   "왜 두 번 반려됐는데 머지됐나"를 이해할 수 없다.
4. 웨이브가 테스트를 bench 레인으로 **옮겼다면** 옮긴 뒤 실제로 실행되는지 반드시 확인하라
   (커버리지가 조용히 사라지는 것을 막는다). 다만 그 확인도 루프 밖에서 한다.

**같은 원리가 CI 에도 적용된다** — codex 교차검증(2026-08-01) 권고:
*"bench 를 PR required 로 두면 flaky 부채가 된다. 성능 회귀는 dedicated idle runner 또는
nightly baseline 비교가 낫다."* 상세는 `tech-debt-tracker.md` §`test-gate-throughput`.

## 병렬 세션 레인 락 — 부하원이 **바깥에도** 있을 때 (2026-08-29)

바로 위 절은 *supervisor 루프가 자기 부하원인 경우*를 다룬다. 세션 **둘 이상**이 같은 머신에서
동시에 도는 라운드에서는 부하원이 하나 더 있다 — **상대 세션**. 이 절이 그 일반화다.
(출처: 2026-08-29 2세션 병렬 라운드 실측. 원문은 그 인계문 §9 에서 지웠다 —
같은 판정이 두 곳에 살면 그중 하나가 먼저 낡는다.)

### 락의 단위는 웨이브가 아니라 **레인**이다 — 그리고 배제 대상은 **「무거운 레인」이 아니다**

부하에 민감한 것은 *웨이브*가 아니라 **`bench`** 다. 문언을 「3-패스를 잡은 쪽」으로 시작하면
충돌 구간이 생긴다 — 실측: 한 세션이 전량 3-패스를 「잡은」 상태에서 상대의 impact subset 이
**25분째** 돌고 있었다. 즉 «내가 3-패스를 잡았다»가 «상대의 반복 게이트가 멈춘다»를 뜻하지 않는다.

> ⚠️ **정정 2026-08-29 (실측 기각).** 이 절의 초판은 *「`routine`·`gui` 는 pass/fail 게이트라 부하에
> 둔감하므로 그 구간의 병렬은 유효하다」* 로 이어졌고, 같은 날 3세션 라운드가 그 문언을
> *「무거운 레인만 토큰이 필요하다」* 로 물려받았다. **둘 다 틀렸다.**
>
> 실측: 한 세션이 `-m bench` 3회 반복 중 **1회차 실패 이름 집합이 이동**했다 —
> `run0`(상대 레인 시작 전) `active_filter · full_plan · named_generation` →
> `run1`(상대 레인 도는 중) `outbound_header_with_parent · full_plan · named_generation`.
> 그때 상대가 돌린 것은 **단일 프로세스 · 워커 0 · `-n` 없음의 `-m invariant` 레인**이다.
> **부하의 크기는 판정 기준이 아니다.**
>
> 그리고 이 서명은 이 파일이 **2026-08-01 에 이미 적어 둔 것**이다(위 §부하 민감 게이트:
> *"두 iteration 이 서로 다른 테스트에서 실패한 것이 진단의 핵심이었다 — 코드 결함이면 같은
> 것이 반복 실패한다. 실패 대상이 이동하면 부하를 의심하라"*). **적혀 있었고, 그런데도 같은
> 분류가 다시 만들어졌다** — 그래서 결론이 아니라 판정 기준을 적는다.

> **락이 배제하는 것은 「무거운 레인」이 아니라 「bench 가 측정 중인 창」이다.
> 그 창 동안에는 어떤 pytest 레인도 돌지 않는다** — 워커 수도, 프로세스 수도,
> 레인 이름도 면제 사유가 아니다. 판정 기준은 하나다: **지금 bench 가 도는가.**

⚠️ **오분류가 자연스러운 이유 — 적어 두지 않으면 다음 세션이 또 만든다.**
「무거운 레인」은 *자원 소비량*으로 정의되는데, bench 판정을 깨뜨리는 것은 소비량이 아니라
**판정 창 안에 스케줄러 경쟁자가 존재하는가**다. `p95` 는 상위 5% 꼬리를 고르는 **순서통계량**이라
**선점 몇 번이면 충분**하고, 선점 횟수는 상대의 CPU 총량이 아니라 **동시에 깨어 있었는가**에
좌우된다. 그래서 *「가벼우니 괜찮다」* 는 직관이 **틀린 방향으로 안전해 보인다** —
이 절이 「크기」축을 버리고 「창」축으로 옮긴 이유가 그것이다.

### 토큰 보유는 「조용하다」가 아니다 — 측정 **직전에** 창을 확인한다

⚠️ 같은 날 실측된 **절차 결함**(측정한 쪽이 스스로 보고했다): 토큰을 쥔 세션이 3회 반복을
시작하면서 **상대 레인이 도는지 확인하지 않았다**. 토큰 보유를 조용함과 **동일시**한 것이다.

> **토큰 보유는 「내가 시작해도 된다」는 권리이지 「지금 조용하다」는 관측이 아니다.**
> 둘은 다른 축이고, 같다고 보면 그 측정은 **부하 미상**이 된다.

측정 직전에 창을 관측한다(같은 이유로 `ps | grep` 이 아니라 `cwd` 로 판정한다):

⚠️ **아래 술어는 두 축을 모두 갖는다 — 그리고 그것이 이 자리의 요점이다.** 이 문단은
19줄 뒤에서 *"술어를 넓히지 마라 … 잡아야 하는 것은 `pytest`/`run_test_lanes`/빌드처럼
실제로 스케줄러를 두고 경쟁하는 것"* 이라고 경고하는데, **2026-09-02 까지 여기 실린 예시는
그 필터를 갖고 있지 않았다.** 산문은 알고 코드는 몰랐고, 그 예시를 그대로 배포한 세션이
`ALIVE 20`(전부 유휴 VS Code·MCP 노드·셸)을 받았다 — 규칙이 예언한 바로 그
*"검사가 영원히 거부"* 하는 상태다. **이 문단이 이미 적고 있던 과거 발화 사례**(*"같은 날
다른 세션의 게이트도 자기 `bash -c` 두 개를 잡아 모든 측정을 거부했다"*)에도 불구하고
세 번째로 발화했다. 결론을 적는 것으로는 부족하고 **예시가 그 결론을 만족해야 한다.**

**그리고 술어는 인라인이 아니라 파일에 둔다** — heredoc 이나 `bash -c` 로 붙여 넣으면
`pytest` 같은 패턴 문자열이 **관측자 자신의 `cmdline` 에 실려** 자기포획이 `cmdline` 축에서
되살아난다(아래 §정지 확인의 (a)와 같은 기전). 자기 제외를 매 스크립트에서 다시 유도하면
반드시 하나는 빠진다 — 2026-09-02 에 한 세션이 조상 제외를 넣은 술어를 파일로 두고도, 그
뒤 급히 쓴 진단 스크립트 **둘에는 넣지 않아 둘 다 자기 heredoc 을 결과에 올렸다.**

```bash
# window.sh — 트리 밖에서 실행한다(스크립트가 스스로 cd 한다).
cd /tmp || exit 1
self=$$; anc=" $self "; p=$self
while :; do                                   # 조상 사슬 전체를 제외한다
  p=$(awk '{print $4}' /proc/$p/stat 2>/dev/null) || break
  { [ -z "$p" ] || [ "$p" = 0 ]; } && break
  anc="$anc$p "
done
for pid in $(ls /proc | grep -E '^[0-9]+$'); do
  case "$anc" in *" $pid "*) continue;; esac   # ① 자기포함 (cwd 축)
  c=$(readlink /proc/$pid/cwd 2>/dev/null) || continue
  case "$c" in <저장소 루트>*|<워크트리 접두>*) ;; *) continue;; esac
  cmd=$(tr '\0' ' ' </proc/$pid/cmdline 2>/dev/null)
  case "$cmd" in                               # ② 경쟁자만 — 유휴 에디터/MCP 는 제외
    *pytest*|*run_test_lanes*|*"npm run"*|*vite*|*tsc*|*"playwright test"*|*build_nuitka*)
      echo "BUSY $pid $c";;
  esac
done
```

실측 2026-09-02, 같은 순간 두 술어: 필터 없는 옛 예시 **ALIVE 20**, 위 술어 **BUSY 0**.
그 20개는 전부 세션 셋이 주 체크아웃에서 기동한 VS Code 서버·Playwright MCP 노드·유휴 셸이다.

⚠️ **`cd /tmp` 가 이 루프의 일부다 — 트리 안에서 돌리면 자기 자신을 센다.**
`pkill -f` 가 자기 **명령줄**을 잡는 것(아래 §정지 확인)과 **같은 자기 포함이 `cwd` 축에도**
있고, 이쪽은 **오차 방향이 나쁘다**:

| 어디서 실행 | 나오는 수 | 어떻게 읽히나 |
|---|---|---|
| 트리 **밖** | `0` | 진짜 0 — 안전 |
| 트리 **안** | `1` | *「나뿐이겠지」* 로 할인 → **남의 1개를 놓친다** |
| 트리 **안** | `2` | 「나 + 1개」인지 「2개」인지 **구분 불가** |

즉 틀리는 방향이 **「조용하다」쪽**이라, 이 검사가 막으려는 바로 그 실패로 이어진다.
실측 2026-08-29: 트리 안에서 세어 `ALIVE 1` 을 받았고 `/tmp` 에서 다시 세니 **0**, 그 PID 는
검사를 실행한 셸 자신이었다. 같은 날 다른 세션의 게이트도 자기 `bash -c` 두 개를 잡아
**모든 측정을 거부**했다. 처방은 둘 중 하나 — **트리 밖에서 실행**(싸다) 또는 `$$` 와 그
조상 제외. ⚠️ 그리고 **술어를 넓히지 마라**: 유휴 에디터·MCP 서버까지 잡으면 검사가 영원히
거부하고, **오탐을 내는 게이트는 삭제된다.** 잡아야 하는 것은 `pytest`/`run_test_lanes`/
빌드처럼 **실제로 스케줄러를 두고 경쟁하는 것**이다.

⚠️ 이것은 §편도 통지 절의 **나머지 절반**이다. 그 절은 *「상대가 멈췄다고 회신했는가」* 를
요구하고, 이 절은 *「그 회신 이후에도 실제로 조용한가」* 를 요구한다. 회신은 **상대의 진술**이고
`cwd` 전수는 **내 관측**이다 — 셋째 세션이나 잊힌 백그라운드 작업은 회신에 나타나지 않는다.

`routine`·`gui`·`-m invariant`·impact subset 의 **서로 간** 병렬은 여전히 유효하다 — 그것들은
pass/fail 게이트라 서로의 판정을 바꾸지 않는다. 무효한 것은 **그중 무엇이든 bench 와 겹치는 것**이다.

### 락의 획득 시점 — **멈출 수 있는 가장 작은 실행 단위**에서 잡는다

> **락은 보호 대상이 아니라, 그 대상을 포함하는 「멈출 수 있는 가장 작은 실행 단위」에서 잡는다.**

보호 대상은 `bench` 하나지만(= 락의 단위는 레인), `scripts/run_test_lanes.py` 는
routine → gui → bench 를 **한 프로세스**로 돌아 레인 경계에서 멈추지 못한다. 그러므로 실제
획득 시점은 **3-패스 시작**이다. *「bench 진입 전에 알린다」* 는 이행 불가능하다 — 「bench 진입」을
감지하는 시점엔 **이미 돌고 있다**.

⚠️ **결론만 적지 마라.** 러너를 레인별로 쪼갠 세션이 훗날 *「왜 여기서 잡지?」* 라고 물을 때
이유가 없으면 규칙이 미신이 된다. **러너가 레인별로 분리되면 그때 시점이 앞당겨진다.**

### 편도 통지는 락이 아니다 — **회신을 받은 뒤에** 시작한다

```
요청   → 현 보유자에게 「토큰 필요, 예상 소요 N분」
인계   → 보유자가 ① 자기 레인 0 확인 ② 「토큰 넘긴다」 통지 ③ 나머지에게 CC
수령   → 받는 쪽이 「토큰 받음, 지금 시작」 회신 후 시작
```

상대가 알림을 못 봤거나(도구 호출 사이) 죽이는 데 실패해도 진입한 쪽은 그것을 **구분할 수
없고**, 그러면 *부하 위에서 돈 bench* 와 *깨끗한 bench* 가 요약에서 **같은 모양**이다
(→ `.claude/rules/check-axis-blindness.md` 증거 2번). **회신이 없으면 그 bench 결과는
「부하 미상」이므로 판정에 쓰지 않는다.**

⚠️ **보유 중 상태를 갱신하라.** 보유자가 조용하면 대기자는 「일하는 중」과 「막힌 중」을 구분할
수 없다(실측: 레인이 끝난 뒤 차분 분석에 한 시간이 걸렸고 대기자에게 그 침묵을 읽을 축이 없었다).
**침묵은 상태가 아니다.**

⚠️ **게이트가 통과했다는 사실이 프로토콜이 작동했다는 증거는 아니다.** 실측 2026-08-29 의 bench 는
깨끗했으나(잔여 사망 `08:17:09` → bench 시작 `08:17:42`, 여유 33초) 그것은 확인이 옳아서가 아니라
**상대 routine 이 느려서**였다. 그 구분을 스스로 적지 않으면 「프로토콜이 작동했다」와
「이번엔 운이 좋았다」가 구분되지 않는다.

### 정지 확인은 `ps | grep` 이 아니다

자식 pytest 의 argv 에는 워크트리 경로도 로그 경로도 없다(`cd` 와 리다이렉트가 소비한다).

```bash
for p in $(ls /proc | grep -E '^[0-9]+$'); do
  c=$(readlink /proc/$p/cwd 2>/dev/null); case "$c" in <내워크트리>*) echo "ALIVE $p";; esac
done
```

⚠️ 같은 이유로 `pkill -f <패턴>` 은 **자기 명령줄도 후보**라 스스로를 죽인다(실측 exit 144).
**PID 로 죽여라.**

#### 정지 술어는 두 축에서 틀리고, 방향이 반대다 (2026-09-02)

| 술어 | 판정 기준 | 오차 |
|---|---|---|
| **어휘** — cmdline 에 `pytest` 등 | 무엇을 실행하는가 | **과포획** — 남의 레인·자기 셸을 잡는다 |
| **계보** — 루트 PID 의 자손 | 누구에게서 났는가 | **과소포획** — 부모가 먼저 죽으면 자식이 `init` 으로 재지정돼 빠져나간다 |

그러므로 **2단**으로 한다: ① 계보로 죽인다 → ② **cwd 축**으로 창이 실제로 비었는지 다시
묻는다(위 §측정 직전 창 관측의 술어). 고아가 된 자식은 부모를 잃어도 **cwd 는 그대로**라
②에 잡힌다. **「죽였다」와 「비었다」는 다른 질문이다** — 계보만 쓰면 잔존이 **조용하다**
(`STOP` 줄이 안 찍히는 것과 애초에 없었던 것이 출력에서 같다).

⚠️ 어휘 축의 실해는 **둘**이고 옛 문언은 하나만 알았다. (a) 패턴이 **관측자 자신의
cmdline** 에 들어가 자기를 죽인다(실측 exit 144 — 뒤 문장 미실행). (b) 패턴 매칭은
**경로를 보지 않으므로** 다른 워크트리에서 도는 남의 레인을 죽인다 — 2026-09-02 실측:
한 세션의 정리 스크립트가 `cmdline` 에 `-m pytest` 가 있는 모든 프로세스를 죽여, **다른
워크트리에서 돌던 레인의 routine 이 68초·14% 지점에서 SIGTERM 으로 끝났다.** 그 세션의
자기 진단이 정확하다 — *"「자기를 죽이지 않는 법」만 고쳤고 「남을 죽이지 않는 법」은
손대지 않았다."* 옛 「자기 포함」 처방은 (a) 만 답한다.

⚠️ **그리고 죽은 레인은 `failing=0` 을 낸다** — 완주해서 0 이 아니라 **14% 에서 죽어
나머지를 세지 않아서** 0 이다. 그 세션은 `exit=-15` 를 보았기 때문에 갈랐다.
**회차 종료는 실패 개수가 아니라 `exit` 코드로 판정한다.** 판별자도 함께 적는다 —
`-15`(SIGTERM)는 *누가 껐다*, `-9`(SIGKILL)는 대개 커널 OOM 이다(OOM 킬러는 `-15` 를
보내지 않는다). 중단된 회차는 **버린다**(N회 반복의 의미가 사라진다).

⚠️ **그리고 이 루프도 트리 안에서 돌리면 자기 자신을 센다** — 자기 포함이 `cmdline` 축과
`cwd` 축 **양쪽에** 있다. 위 §측정 직전 창 관측의 `cd /tmp` 와 그 표가 사유를 갖는다.
정지 확인에서는 오차가 *「아직 살아 있다」* 쪽이라 측정 창만큼 위험하지는 않지만,
**같은 명령을 두 자리에서 다르게 쓰면 그중 하나가 먼저 낡는다.**

### 락은 **세션 사이만** 막는다 — 3-패스 자기 안에서 bench 가 오염된다

`run_test_lanes.py` 는 레인 사이에 정착 시간이 없다. 즉 bench 는 **방금 8워커가 만든 압박 위에서**
시작한다. 실측 2026-08-29 (같은 트리·같은 커밋):

| 실행 | bench 실패 |
|---|---:|
| 3-패스 안의 bench (routine `-n 8` 직후, 같은 프로세스) | **5** |
| `-m bench` 단독 (조용한 상태, load 0.6) | **2** |

사라진 셋은 전부 latency budget 계열이었다.

> **bench 판정은 `-m bench` 단독 실행으로 한다.** 3-패스 안의 bench 는 *실행됐다*는 사실만 주고
> **판정에 쓰지 않는다**. 차분 대조도 양쪽 다 단독으로 잰다.

⚠️ **이것이 차분 판정을 무효화하지는 않는다 — 성격 규정을 바꾼다.** 양쪽이 같은 3-패스 조건이면
오염이 **같은 방향**이라 이름 집합 비교는 여전히 성립한다. 틀릴 수 있는 것은 그 실패를
*「선재 결함」* 이라고 부르는 것이다 — *「3-패스 조건에서 부하로 깨진다」* 일 수 있고, 둘은 장부에서
**다른 항목**이다(전자는 소유 웨이브가 필요한 부채, 후자는 측정 절차의 결함).
그래도 뒤집히는 것은 §선재 실패 차분 판정 3번의 **판정 불가**다.

⚠️ routine 은 `-n 8` 이다. 12코어에서 둘이 동시에 돌면 16 워커가 서로를 느리게 한다.
그러나 **그것은 속도 문제이고, bench 오염은 판정 문제다** — 둘을 같은 규칙으로 다루지 마라.
속도는 느려질 뿐 답이 바뀌지 않고, 판정은 **답이 바뀐다.**

### 소유권과 공유 생성물

병렬 라운드는 **파일 단위로 disjoint 한 소유**를 선언한다(claim 의 `scope`). 그리고 재생성 가능한
공유 산출물(`.claude/scripts/impact-tests-generated.sh` · `.claude/rules/skills-invariant-map.md` ·
OpenAPI 아티팩트)은 **손으로 병합하지 않는다** — 나중에 머지하는 쪽이 **render 로 재생성**한다.
집합 상등으로 판정하는 래칫(예: 납품 매니페스트)은 텍스트 자동 병합이 **오히려 위험**하다.

**머지 순서는 게이트 등급의 역순** — 가벼운 쪽부터 머지하면 무거운 쪽이 rebase 를 한 번만 한다.

### ⚠️ 이 프로토콜은 필요조건이고, 실제로 판정을 구한 것은 **자진 신고**였다

2026-08-29 3세션 라운드의 정직한 회고다. 락 대기는 전 라운드 **91분(33%)** 에서 **약 10분**으로
줄었지만, **그 이유는 이 절의 규약이 아니다** — 규약은 그때 *「무거운 레인만 배제」* 라고
**틀리게** 적고 있었고, 그것을 고친 것도 관측이 아니라 대화였다. 실제로 오염된 판정 둘을 구한 것은
**두 건의 자진 신고**였다:

1. 한 세션이 *「내가 지금 이 레인을 돌린다」* 를 **먼저 알렸다** → 상대가 실패 이름 집합의 이동을
   자기 레인과 연결지을 수 있었다.
2. 다른 세션이 *「내 프로세스가 그 창에 있었을 수 있다」* 를 **스스로 말했다** → 그 세션이
   **그럴듯한 수치를 버리고 다시 쟀다**. 아무도 말하지 않았으면 그 수치는 **영구히 사실**이 됐다.

> **오염된 창과 조용한 창은 출력에서 같은 모양이다. 그러므로 그 구분은 관측이 아니라 진술에서
> 온다** — 규약이 할 수 있는 최선은 *무엇을 돌리는지 말하는 것을 값싸고 당연하게* 만드는 것이다.

**실무 형태**: 레인을 시작할 때 **무엇을·어디서·얼마나** 를 한 줄로 알린다(허락을 구하는 것이
아니라 **기록을 남기는 것**이다). 그리고 나중에 *「그때 내가 뭘 돌렸을 수도 있다」* 를 떠올렸으면
**늦더라도 말한다** — 그 한 줄의 값은 상대가 **버려야 할 측정을 아는 것**이고, 침묵의 값은
**틀린 수치가 확정되는 것**이다. ⚠️ 자진 신고를 실책으로 다루면 다음부터 아무도 하지 않는다.

## Commands

- `python scripts\supervisor_status.py`
  summarizes worktrees, open PRs, and active claims.
- `python scripts\supervisor_preflight.py`
  blocks on dirty worktrees, overlapping active claims, and open PR path overlap.
- `python scripts\supervisor_cleanup.py`
  fast-forwards main, prunes remotes, deletes merged branches, and removes only
  clean merged worktrees.
