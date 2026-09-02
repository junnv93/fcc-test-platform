# Sprint Self-Audit Checklist SSOT (2026-05-29)

> 본 checklist 는 sprint commit 직후 + sprint 종료 시 **반드시** 자가 점검하는
> 항목 SSOT. 사용자 추궁이 없어도 모든 sprint 가 본 checklist 를 따른다.

## 배경 (architectural fragility 메타 결함)

본 checklist 는 자기 audit cascade 결함 #14 정공으로 신설. cascade 흐름:
- turn 1: 단편 정공 (ready plan deferral)
- turn 2: 사용자 추궁 → 직접 정공
- turn 3: 사용자 추궁 → 6 결함 self-audit → 5 정공
- turn 4: 사용자 추궁 → 8 결함 self-audit → 4 정공

**메타 결함**: 매 정공의 trigger 가 사용자 추궁. 사용자 추궁 없으면 같은
결함 silent 누적. self-enforcing checklist 가 정공.

## Checklist (모든 sprint commit 후 적용)

### A. 봉인 후속 (4 항목)

- [ ] **A-1. MEMORY.md topic 신규 + index 갱신** (memory 외부 file +
      ~/.claude/projects/.../MEMORY.md index)
- [ ] **A-2. CLAUDE.md Critical Invariant 등재 검토** (신규 SSOT/패턴이면 등재
      필수, 단순 fix 면 생략 가능. **단락 비대화 시 .claude/contracts/ 분리**)
- [ ] **A-3. example-prompts.md strikethrough 검토** (해당 항목 있는지 확인.
      없으면 "검토 완료, 해당 항목 없음" 명시)
- [ ] **A-4. `/verify-implementation` 통합 검증 또는 영향 영역 pytest 실행
      결과 명시** (단순 영향 영역 pytest 도 허용 — 명시 필수)

### B. SSOT 봉인 (5 항목)

- [ ] **B-1. 신규 invariant 가 verify-* skill `mapped_invariants` 등재**
      (drift gate 자동 차단 — `tests/test_invariant_skill_mapping_drift.py`)
- [ ] **B-2. 신규 trigger 가 `.claude/scripts/impact-tests.sh` 등재**
      (`/verify-implementation` 영향 영역 자동 선택 메커니즘 활성)
- [ ] **B-3. tech-debt-tracker.md 완료 marker 등재 또는 신규 항목 추가**
- [ ] **B-4. evaluation 문서 작성 (`.claude/evaluations/<slug>.md`)** —
      Why/What/How/Verification/후속 5 섹션
- [ ] **B-5. 도메인 ↔ 인프라 경계 검토** — 신규 enum/상수/모델이 정합 위치 인지
      (도메인 모델 vs 인프라 concept). Anti-Corruption Layer 패턴 적용 필요 시
      cross-file SSOT invariant drift 가드 신설.

### C. 정공 검증 (5 항목)

- [ ] **C-1. byte-identical 주장 시 invariant 봉인** — 코드 주석만으로는 silent
      regression. boundary/oracle data 검증 invariant 추가.
- [ ] **C-2. AST 가드 추가 시 helper SSOT (`tests/_ast_string_finder.py`)
      위임** — 단순 `ast.Constant` 검사 금지 (5 우회 가능: dict/list/tuple value
      / JoinedStr / BinOp Add / case variant).
- [ ] **C-3. hot path 변경 시 bench budget invariant 추가** — `measure_latency_
      us_robust` 위임 + `LatencyBudget` invariant. "영향 0" 주장은 정성 — 정량
      봉인 필수.
- [ ] **C-4. 테스트 fragility 검토** — MagicMock 의존 테스트는 dataclass 시그
      니처 변경 silent regression. real dataclass + factory helper 사용.
- [ ] **C-5. workflow/seed/measurement 진입점 변경 시 cross-SoT validation
      hook 검토** — production 측정 cycle 이 DataFrame 직접 소비 path 면
      `_audit_*_workflow_path` hook 추가 (γ-#1 / 자기 audit #5 패턴).

### D. 세션 안전 (3 항목)

- [ ] **D-1. explicit-files commit** — `git add <명시파일>` + `git commit -F
      <msg-file> -- <files>`. `-A`/`.` 영구 금지.
- [ ] **D-2. 다른 세션 staged 분리** — `git status --short` + `git worktree
      list` quiescent window 확인. 충돌 영역 이연.
- [ ] **D-3. commit 직후 `git show --name-only HEAD` 의도 파일 확인**

## 무엇이 검사되고 무엇이 검사되지 않는가 (2026-09-02, `self-audit-value-axis`)

게이트는 오랫동안 **형식**만 봤다 — 17줄이 있는가. 그래서 `PASS` 가 `SKIP` 보다 싸고,
두 세션이 연달아 위조했다. 이제 축이 셋이다.

| 축 | 무엇을 묻는가 | 대상 |
|---|---|---|
| 형식 | 17줄이 각각 한 번씩 있고 트레일러가 있는가 | 17 항목 전부 |
| **사유** | 상태 뒤에 근거가 적혔는가 | 17 항목 전부 |
| **값** | `PASS` 가 말한 산출물이 이 커밋에 있는가 | **`B-3`·`B-4` 둘뿐** |

⚠️ **나머지 15 항목(`A-1`~`A-4`·`B-1`·`B-2`·`B-5`·`C-1`~`C-5`·`D-1`~`D-3`)은
아무도 검사하지 않는 자기진술이다.** 값 축은 돌 때마다 그 열다섯을 이름으로 고지한다
(`self_audit_message.VALUE_AXIS_LIMITATION`) — 부분 강제가 전량 검증처럼 읽히지 않게.

* **사유 축** — `B-4: PASS` 처럼 상태만 적은 행은 거부된다. 이것은 새 규칙이 아니라
  같은 모듈의 `extract_implementer_audit` 가 이미 강제하던 것이다(위 §commit message
  양식 예시가 모든 행에 사유를 다는 이유). `<근거>`/`<사유>` 자리표시자도 거부된다.
* **값 축** — `B-3: PASS` 는 이 커밋이 `tech-debt-tracker.md` 를 만졌을 때,
  `B-4: PASS` 는 `.claude/evaluations/*.md` 를 만졌을 때 통과한다.
  산출물이 **앞선 커밋**에 있으면 그 커밋을 지목하면 된다 — `B-4: PASS — 3f2a1b9`.
  아직 없는 커밋은 이름이 없으므로 **전방 참조는 적을 수 없다**. 그것이 요지다.
* **정직한 기본값은 `SKIP`** 이고 `SKIP` 은 심문받지 않는다. 사유는
  *"후속 커밋에서"* 가 아니라 **왜 이 커밋의 범위가 아닌지**를 적는다.
* **`--amend` 는 통과한다** — 산출물을 담은 커밋을 고쳐 쓰는 것은 정당하다. 그 대가로
  **바로 앞 커밋**이 담은 산출물도 인정된다(조부모는 안 된다). 사유는
  `.claude/rules/supervisor-workflow.md` — git 이 훅에 amend 를 알려주지 않는다.

기각한 후보(실측 브랜치 단위 오탐률): `A-3` 85.5% — 항목 본문이 `PASS`="검토 완료,
해당 항목 없음"을 허가한다 / `B-1` 45.5% · `B-2` 48.9% — 기존 glob 이 이미 라우팅하면
편집이 필요 없어 `PASS`="이미 등재됨"이 정당하다 / `A-2` 20.0% — 등재 **검토**라는
어휘가 같은 읽기를 허가한다. 상세 사유는 `.claude/rules/supervisor-workflow.md`.

## 메타 봉인

본 checklist 의 mechanism invariant: `tests/test_meta_self_audit_invariant.py`
가 본 markdown 문서 자체의 구조 + 13 checklist 항목 (`- [ ]` 카운트) +
verify-* skill 등재 확인 — 본 checklist 가 stale 되거나 손실되는 것 차단.

## 적용 정공

- **commit-time application**: 매 sprint commit 메시지 또는 evaluation 문서에
  본 checklist 의 적용 결과 명시 (Pass/Skip + 사유). 사용자 추궁 없이도
  본 checklist 가 self-enforcing.
- **opt-in trailer (Conventional Commits 1.0.0 §footer + RFC 822 token)** —
  자기 audit meta4 P0 정공 (2026-05-29, cascade-meta4-footer-trailer-and-bench-
  snapshot-gate): 옛 substring 매칭 (`'self-audit'` / `'17 항목'` 본문) 의
  false-positive (docstring/주석 우연 등장) 차단. 매 sprint commit body 끝
  footer block 에 `Self-Audit: <value>` trailer 명시:
    - `Self-Audit: 17-items` — 17 항목 자평 적용 의도 (분모/분자 포함).
    - `Self-Audit: skipped` — 의도적 자평 미적용 명시 (분모 제외).
    - `Self-Audit: n/a` — 적용 비대상 명시 (분모 제외).
    - trailer 부재 = 옛 sprint 또는 의도 미명시 → 분모 제외 (silent dilution
      차단).
  표준 reference:
    - https://www.conventionalcommits.org/en/v1.0.0/#specification (§footer)
    - https://git-scm.com/docs/git-interpret-trailers
    - Linux kernel patch convention (`Signed-off-by:`, `Reviewed-by:` 정합)
  parser SSOT: `tests/test_meta_self_audit_invariant.py::_parse_trailers` —
  body 끝 *footer block* 만 추출 (body 본문/docstring 우연 매칭 차단).
- **drift detection**: invariant 가 본 markdown 의 17 항목 (`- [ ] **<ID>.`)
  AST 카운트 검증 → 항목 추가/삭제 시 즉시 surface.
- **mechanism failure surfacing**: 본 checklist 가 stale 되면 `tests/
  test_meta_self_audit_invariant.py` FAIL → 다음 sprint 가 명시 갱신 강제.

### commit message 양식 예시

```text
feat(meta): cascade-meta4 footer trailer + bench snapshot gate 활성

본 sprint 가 자평 cascade 의 P0 정공 2건을 architectural primary 격상.

### A. 봉인 후속
A-1: PASS — MEMORY.md topic + index 갱신
A-2: PASS — CLAUDE.md Session Hygiene Rules trailer 등재 (SHOULD)
A-3: PASS — example-prompts.md 해당 항목 없음 (검토 완료)
A-4: PASS — impact-tests subset + verify-implementation PASS

### B. SSOT 봉인
B-1: PASS — TestSelfAuditTrailerStandardCompliance 신규 등재
B-2: SKIP — 신규 trigger 패턴 부재
B-3: PASS — tech-debt-tracker.md 완료 marker
B-4: PASS — .claude/evaluations/{slug}.md 작성
B-5: PASS — RFC 822 token-syntax 표준 정합 (도메인 외부)

### C. 정공 검증
C-1: PASS — _parse_trailers footer block 추출 byte-test invariant
C-2: SKIP — AST 가드 미신설
C-3: SKIP — hot path 미변경
C-4: SKIP — MagicMock 미사용
C-5: SKIP — workflow/seed/measurement 미변경

### D. 세션 안전
D-1: PASS — git add <명시파일> + git commit -F msg-file -- <files>
D-2: PASS — 다른 세션 9 worktree 침범 0건
D-3: PASS — git show --name-only HEAD 의도 파일 일치

Self-Audit: 17-items
```

⚠️ **이 예시는 형식의 SSOT 이지 값의 SSOT 가 아니다.** 위 §*무엇이 검사되고 무엇이
검사되지 않는가* 이후로 `B-3`/`B-4` 의 `PASS` 는 **그 커밋이 실제로 그 산출물에 내용을
더했을 때만** 통과한다. 즉 이 예시를 그대로 복사한 커밋은 장부·평가서를 함께 담지
않으면 거부된다 — 예시를 따랐다는 이유로 거부당하지 않도록, 담지 않는 커밋에서는
`SKIP — 왜 이 커밋의 범위가 아닌지`로 바꿔 적는다. (문서가 게이트와 다르면 그 문서를
믿은 세션이 정확히 그 자리에서 넘어진다 — `b925d2ef` 가 그렇게 red 로 머지됐다.)

## Related

- 자기 audit cascade memory: `~/.claude/projects/.../stop-hook-not-satisfied-
  then-self-audit-cascade.md`
- 본 sprint evaluation: `.claude/evaluations/meta-self-audit-cascade-resolution.md`
