# 하루에 열한 곳이 거짓이 됐다 — 정정을 «파일»이 아니라 «사실»로 하는 법

> 2026-09-07 12:08 실측. 대상: 온보딩 문서 15개 + 교육자료 2개 + 두 레포의 `README`·`CODEOWNERS`·`CLAUDE.md`.

## Why — 무엇이 문제였나

앞 세션이 **「`fcc-test-contracts` 의 `main` 은 무방비다」**를 다섯 파일에 적었다.
그 문장은 쓰인 시점(2026-09-07 오전)에 참이었고, **같은 날 낮에 거짓이 됐다** —
그 사이 보호가 켜졌고(`lane-check` required) 초대 2건이 발송됐다.

이것은 이 레포가 이미 이름으로 기록한 함정이다(`90-known-traps.md` §0):
**시점을 담은 서술은 쓴 날에는 참이라 재측정할 이유가 생기지 않는다.**

## What — 실측이 인계문보다 넓었다

인계문은 고칠 곳 **다섯**을 지목했다. 전수 grep 결과는 **열하나**였다.

| # | 어디 | 인계문이 지목했나 |
|---|---|---|
| 1 | `05-github-access.md` §1 표 (초대 0 → 2) | ✅ |
| 2 | `05-github-access.md` §3 「무방비」 절 | ✅ |
| 3 | `06-workspace-and-claude-md.md` §4 표 | ✅ |
| 4 | `02-how-we-collaborate.md` §4 표 | ✅ |
| 5 | 두-트랙 교육자료 「첫날에 할 일」 1·3행 | ✅ |
| 6 | `20-platform-codev-kickoff.md` §강제 안 되는 것 | ❌ **새로 찾음** |
| 7 | `README.md` §훅 한계 (「그 날이 오면」) | ❌ **새로 찾음** |
| 8 | `CLAUDE.md` §거짓 서술 표 | ❌ **새로 찾음** |
| 9 | `CLAUDE.md` §참조 표 (규칙 «셋» → 넷, `intent-workflow.md` 누락) | ❌ **새로 찾음** |
| 10 | 「의도로 시작하는 개발」 교육자료 §5 타일 3개 | ❌ **새로 찾음** |
| 11 | `fcc-test-contracts` 의 `README.md` · `CODEOWNERS` | ❌ **새로 찾음 — 다른 레포** |

## How — 세 가지 갈라짐의 형태

### (a) 한 사실이 여러 «파일»에 흩어진다

보호가 켜진 사건은 **하나**인데, 그것을 거짓으로 만든 문장은 **다섯 파일**에 있었다.
파일 이름으로 물으면 인계문의 목록에서 멈춘다. **문장으로 물어야 한다.**

### (b) 한 사실이 여러 «레포»에 흩어진다

> *"진짜 강제는 러너가 돌아오고 branch protection 이 이 검사를 required 로 거는 날에 생깁니다."*

이 문단은 `fcc-test-platform/README.md` 와 `fcc-test-contracts/README.md` 에
**글자 그대로** 있었다. 두 레포는 독립이고 CI 도 따로 돌므로, **어느 게이트도 이
갈라짐을 볼 수 없다.**

### (c) 한 사실이 같은 «파일 안»에서 갈라진다

`platform/README.md` 는 §CI 에서 이미
「`main` 의 branch protection 이 그것을 required 로 요구합니다」로 **정정돼 있었다.**
그런데 200줄 아래 §훅 한계는 여전히 「그 날에 생깁니다」라고 **미래형**이었다.

**정정은 파일 단위가 아니라 문단 단위로 일어난다.** 한 자리를 고치는 것은 정정이
아니라 갈라짐을 만드는 일이 될 수 있다.

## Verification — 무엇을 어떻게 쟀나

```bash
gh api repos/junnv93/fcc-test-contracts/branches/main/protection
#   contexts=["lane-check"] strict=false enforce_admins=false
#   required_approving_review_count=0 require_code_owner_reviews=false
gh api repos/junnv93/fcc-test-platform/branches/main/protection
#   같은 값, 단 enforce_admins=true
gh api repos/junnv93/fcc-test-{platform,contracts}/invitations   --jq '.[].invitee.login'
#   JJDHJJ, kkulhong  (두 레포 각각)
gh api repos/junnv93/fcc-test-{platform,contracts}/collaborators --jq '.[].login'
#   junnv93            ← 초대는 «발송»이고 아직 «수락»이 아니다
git -C fcc-test-contracts ls-tree --name-only -r origin/main .claude/rules | wc -l   # 0
ls .claude/rules/*.md | wc -l                                                        # 4  ← CLAUDE.md 는 「셋」이라 적고 있었다
```

**레인 게이트 (전용 venv, 워크트리 editable 설치):**

```
lane-check: fcc-test-platform    선언 0 / 관측 0   ✅  (3,359 passed, 863 subtests)
lane-check: fcc-test-contracts   선언 29 / 관측 29 ✅
```

### ⚠️ 그 전에 같은 트리가 84건 red 를 냈다 — 설치본 축

공유 `.venv`(설치본 `0.1.8`, `.pth` 가 낡은 main 체크아웃 `b3c619e` 를 가리킴)로
돌리면 **84건 실패**, 워크트리를 editable 설치한 전용 venv 로 돌리면 **0건**.
**같은 커밋이다.** 코드 diff 가 0인 문서-only 변경이었으므로 「내가 깨뜨렸다」는
읽기가 틀렸다는 것을 diff 로 먼저 확인했다.

## 후속 — 남은 것과 그 이유

| 항목 | 상태 |
|---|---|
| `fcc-test-contracts` 의 `.claude/rules/` | **여전히 0개.** 그 레인에는 경로 조건부 규칙이 하나도 도달하지 않는다 |
| `enforce_admins` 비대칭 (contracts `false`) | **운영자 판정 대기.** 태그 레인의 비상구일 수 있으나 근거가 어디에도 없다 |
| `required_approving_review_count` 0 → 1 | **초대 수락 후에만 가능.** 순서를 뒤집으면 아무것도 머지할 수 없다 |

⚠️ 뒤의 둘은 **GitHub 설정 변경**이고 `CLAUDE.md` §P0-5 가 사람의 승인을 요구한다.
에이전트가 스스로 하지 않는다.
