# CLAUDE.md — fcc-test-platform

<!--
  이 파일에는 **모든 세션에 참인 것만** 남긴다.
  한 서브시스템에서만 참인 것은 `.claude/rules/` 의 `paths:` 규칙이 소유한다 → §참조.

  근거(code.claude.com/docs/en/memory):
    "target under 200 lines per CLAUDE.md file. Longer files consume more
     context and reduce adherence."
  ⚠️ 이 파일이 200줄을 넘기 시작하면 그것은 내용을 rules 로 내릴 신호다.
     모노레포(FCC_mobile_test_automation)의 CLAUDE.md 는 637줄이고 그것은 반면교사다.

  ⚠️ 이 파일이 생기기 전(2026-09-07 이전) 이 레인에는 **매 세션 로드되는 규칙이
     하나도 없었다.** `.claude/rules/*.md` 셋은 전부 `paths:` 를 가져 조건부였고,
     그 자리를 사용자 auto memory 가 메우고 있었다. auto memory 는 기계-로컬이라
     팀과 공유되지 않는다 — 그것이 이 파일이 필요한 이유다.
-->

## 이 레포는 무엇인가

여러 시험 분야(provider)가 공유하는 **웹 플랫폼**. 중앙 DB · 백엔드 API · 인증/RBAC ·
프론트엔드 셸 · provider 레지스트리 · 성적서/아티팩트 열람.

**측정 코드는 여기 없다.** 스펙트럼 분석기 제어 · EUT 제어 · Excel 시험계획 · GUI 는
provider 비공개 레포에 남는다. 이 레포가 아는 것은 *분야와 무관하게 참인 것*뿐이다.

의존 방향은 **단방향**: 이 레인 → `fcc-test-contracts`. 역은 없다.

---

## P0 — 모든 세션이 지켜야 하는 것

### 1. 기능 작업은 `intent/` 에서 시작한다

새 기능 · 동작 변경 · 외부에 보이는 변화는 **코드보다 먼저**
`intent/<slug>/intent.md` 를 만들고 **사람의 승인(PR 머지)** 을 받는다.

* 세 파일: `intent.md` → `spec.md` → `plan.md`
* **승인은 «예외 승인»이다.** 기본은 동료 1인(작성자 제외)이고, **리드는 상시
  승인자가 아니라 예외 판단자**다. 기계가 판정하는 방아쇠 다섯이 걸릴 때만 개입한다:
  되돌릴 수 없는 경로(`CODEOWNERS`) · 기준선에 이름 추가 · 답 없는 열린 질문 ·
  `plan.md` 표에 없는 파일 · 선언 대비 과대 변경.
  ⚠️ 방아쇠는 **알림이 아니라 red** 다 — 아티팩트에 이름 붙은 결정이 적히기 전까지
  검사가 초록이 되지 않는다.
* **초안은 전부 에이전트가 쓰고, 사람은 승인만 한다.**
* 규칙 본문 SSOT → **`intent/README.md`**. 서식 → `intent/_templates/`.
* ⚠️ **적용 안 함**: 오탈자 · 명백한 버그 한 줄 · 이미 승인된 spec 안의 후속 수정.
  전부에 요구하면 형식이 되고, 형식이 되면 아무도 안 읽는다.

### 2. 커밋 위생 — 셋 다 차단형이고 서로 독립이다

* **Explicit-files commit**: 항상 `git add <파일...>` 로 명시 지정. `-A` / `.` 금지.
  여러 세션이 한 저장소를 만지므로 bulk staging 은 **다른 세션 파일을 실어 나른다.**
* **No-squash merge**: PR 은 `gh pr merge --merge`. `--squash` 는 커밋 본문을 PR 설명으로
  **대체**해 각 커밋에 실린 17항목 자가점검이 착지 커밋에서 사라진다. 푸시 뒤에는 고칠 수 없다.
* **Merge base 신선도**: 머지 직전 `python3 scripts/merge_readiness_guard.py merge <N> --update`.
  판정식은 `git log <base> --not <head> --no-merges` 가 **0줄**이고 `--is-ancestor` 가 **아니다.**
* **17항목 자가점검**: 커밋 메시지에 `^[A-D]-[1-5]:\s+(PASS|SKIP|N/A)` 물리 17줄 + 끝에
  `Self-Audit: 17-items` 트레일러. 상태 뒤에 **사유**가 없으면 거부된다.
  판정 SSOT는 `scripts/self_audit_message.py` 하나. 정의 → `.claude/contracts/sprint-self-audit-checklist.md`.

훅 설치는 clone 마다 opt-in: `git config core.hooksPath githooks`.

### 3. 검사 게이트는 «이름 집합» 으로 판정한다

`scripts/lane_check.py --root .` 는 「전부 통과」가 아니라
**관측된 실패 이름 집합 == `delivered_test_run_baseline.json` 의 선언**을 본다.

* 개수가 아니라 **이름**이다 — 하나 고치고 하나 깨뜨리면 개수는 같다.
* **새 실패를 기준선에 추가하지 마라.** 추가는 부채 등재이고 별도 승인 사항이다.
* 이 레인의 선언된 실패는 **0** 이다. 실패를 보면 그것은 네 환경 문제이거나 새로 깨진 것이다.

### 4. 새 검사는 «테스트 파일»로 붙인다

이 레인의 실질 게이트는 `githooks/pre-push` → `lane_check.py` → pytest 다.
워크플로 스텝에만 넣으면 **로컬 push 를 막지 못한다.**

⚠️ 그리고 검사를 만든 것과 그 검사가 **이빨을 가진 것**은 다른 명제다.
새 검사에는 **일부러 깨진 입력을 주입해 빨개지는지** 확인하는 케이스를 함께 넣어라.
공허한 검사는 초록과 구별되지 않는다.

### 5. 사람의 승인이 필요한 것

다음은 「진행해라」 하나로 묶어 처리하지 않는다. **각각 따로 묻는다.**

* 태그 push · 컨테이너 재기동 · DB 마이그레이션 적용 · 배포
* GitHub 설정 변경 (branch protection · CODEOWNERS)
* `.claude/settings.json` 훅 추가 — 이 레포를 여는 **모든** 세션에 개입한다

---

## 명령

```bash
# 테스트 — 디스플레이가 없으면 core dump 가 난다
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/ -q

# 레인 게이트 (pre-push 와 CI 가 부르는 것과 같은 진입점)
python3 scripts/lane_check.py --root .

# 머지 전 신선도
python3 scripts/merge_readiness_guard.py merge <PR번호> --update
```

`FCC_LANE_CHECK_PYTHON` 이 없으면 pre-push 가 막힐 수 있다 — venv 인터프리터를 가리켜라.

---

## 이 레포 문서에서 «오늘 거짓인» 서술

<!-- 낡은 문장을 믿고 잘못된 판정을 내린 사례가 이 레포에 반복해서 있었다. -->

| 어디 | 뭐라고 적혀 있나 | 실측 (2026-09-07 12:08) |
|---|---|---|
| `README.md`, `.github/workflows/checks.yml` | GitHub Actions 가 러너를 못 받아 **휴면** | **거짓.** 러너 정상(`runner_name` 채워짐, 최근 런 `success`) |
| `README.md` | 이 레포는 **private** 이라 Actions 가 분당 과금 | **거짓.** 레포는 **PUBLIC** — 표준 러너 무과금 |
| `fcc-test-contracts/README.md` · `CODEOWNERS` | 이 레인은 **읽기 전용 납품물**, PR 병합 불가 | **거짓.** 배송 기계는 2026-08-31 퇴역, 거기서 고치고 PR 로 머지한다 |
| 온보딩 문서 다수 | `fcc-test-contracts` 의 `main` 은 **무방비** | **거짓.** `lane-check` **required**. 단 `enforce_admins: false` — **관리자는 우회 가능**(platform 은 `true`) |

⚠️ **「없다」를 적은 문장이 가장 빨리 낡는다.** 없던 것이 생기는 데는 커밋 하나면
충분하고, 그 커밋은 그 문장에 알림을 보내지 않는다. 고칠 때는 **파일이 아니라 그
«사실»을** `git grep` 하라 — 같은 사실을 말하는 자리가 다른 파일, 심지어 **다른
레포**에 있다(2026-09-07 실측: 같은 문단이 두 레인의 `README.md` 에 있었다).

구조·품질 판정은 **이 레포에 직접 도구를 돌려서** 하라. 원본 수치를 인용하지 마라.

---

## 참조 — 경로를 만질 때만 로드되는 규칙

| 파일 | 내용 | 로드 조건 |
|---|---|---|
| `.claude/rules/intent-workflow.md` | 의도 흐름 — **무조건 로드** (`paths:` 없음) | **항상** |
| `.claude/rules/supervisor-workflow.md` | 워크트리·claim 위생 · 머지 신선도 · 병렬 레인 락 | `.claude/**` · `scripts/supervisor_*` 등 |
| `.claude/rules/check-axis-blindness.md` | 「이 검사가 재는 축에 같은 값을 갖는 다른 상태가 있는가」 판별 | `tests/**` · 게이트 스크립트 |
| `.claude/rules/incomplete-landing-lifecycle.md` | reviewer FAIL 과 release readiness 분리 | `.claude/**` |
| `intent/README.md` | 의도 기반 흐름의 규칙 본문 | (규칙 파일이 아님 — §P0-1 이 가리킨다) |
| `.claude/README.md` | 이 디렉터리가 물려받은 것과 물려받지 않은 것 | — |

⚠️ **규칙은 넷이고, 그중 셋이 `paths:` 조건부다.** 그 경로를 만지지 않는 세션에는
로드되지 않는다. 모든 세션에 참인 규칙을 그쪽에 쓰면 필요한 순간에 도달하지 않는다 —
이 파일이나 `intent-workflow.md` 처럼 `paths:` 없는 규칙에 써라.

<!-- 정정 이력 — 지우지 마세요. -->
> 🔴 **정정 (2026-09-07 12:08).** 이 표의 옛 판은 규칙을 **셋만** 적었고
> `intent-workflow.md` 가 **빠져 있었습니다.** 그리고 표 아래 문장은
> *「위 셋은 전부 `paths:` 조건부다」* 였습니다 — 그 문장은 이 표에 대해서는 참이지만
> **레포에 대해서는 거짓**이었습니다(무조건 규칙이 하나 있었으므로).
>
> ⚠️ **지도에서 빠진 규칙은 「있는데 아무도 모르는」 상태가 됩니다.** 규칙을 더할 때는
> 파일을 만드는 것으로 끝내지 말고 **이 표에 등재**하십시오.
> 재는 법: `ls .claude/rules/*.md | wc -l` 을 이 표의 행 수와 대조.

⚠️ **`.claude/` 는 모노레포와 동기화하지 않는다.** 여기 문서가 모노레포의 사고를
인용하는 것은 정상이지만(그것이 규칙의 근거다), 앞으로의 정정은 **여기서** 한다.
