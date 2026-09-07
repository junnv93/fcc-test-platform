# 작업 폴더 배치와 `CLAUDE.md` 를 어디에 두는가

> 신규 합류자가 **처음 30분 안에 반드시** 부딪히는 질문 셋에 답합니다.
> 근거는 **Claude Code 공식 문서**(`code.claude.com/docs/en/memory`)와 **2026-09-07 실측**입니다.

---

## 1. 결론부터

| 질문 | 답 |
|---|---|
| 두 레포를 한 폴더에 나란히 둬야 하나? | **조건부.** 커널을 만질 사람만 필수 (§2) |
| 그 폴더 이름은? | 내용을 말하는 이름. **`fcc-delivery-final` 은 오해를 부릅니다** (§3) |
| `CLAUDE.md` 는 어디에? | **각 레포에 반드시 따로.** 컨테이너 폴더의 것은 «보조»이고 팀과 공유되지 않습니다 (§4) |

---

## 2. 나란히 둘 필요가 있는가

두 레포는 **git 상 아무 관계가 없습니다.** 실측:

```bash
$ cd ~/fcc-delivery-final && git rev-parse --git-dir
fatal: not a git repository            # 컨테이너는 저장소가 아니다
$ ls ~/fcc-delivery-final/.gitmodules
No such file or directory              # submodule 도 아니다
```

각 하위 디렉터리는 **자기 `.git` 디렉터리를 가진 독립 clone** 이고, 서로를 잇는 것은
**pip 의존 하나**뿐입니다(`platform → contracts`, 태그 핀).

| 당신이 하는 일 | 나란히 둘 필요 | 이유 |
|---|---|---|
| 트랙 A만 — 자기 프로그램 provider 화 | ❌ **불필요** | 두 레인이 **public** 이라 `pip install git+https://…` 로 끝. clone 도 초대도 필요 없다 |
| 트랙 B — platform 공동개발 | △ 있으면 편함 | 계약 정의를 자주 열어 보게 된다 |
| 계약 커널에 필드 추가·수정 | ✅ **필요** | **2-레포 작업이고 순서가 있다** — 커널 태그가 먼저 나가야 platform 이 그것을 본다 |

⚠️ **트랙 A 만 하는 팀원에게 「폴더를 이렇게 만드세요」를 요구하지 마십시오.**
필요 없는 절차는 지켜지지 않고, 지켜지지 않는 절차는 **다른 절차의 신뢰도까지 깎습니다.**

### 권장 배치 (커널을 만지는 사람)

```
~/fcc-lanes/                       ← 이름은 §3
   ├── CLAUDE.md                      «이 PC 의 폴더 배치» 만. 규칙을 쓰지 마라 (§4)
   ├── fcc-test-contracts/            독립 clone
   └── fcc-test-platform/             독립 clone
```

---

## 3. 폴더 이름 — `fcc-delivery-final` 은 바꾸는 편이 낫습니다

### 왜

* **「delivery(배송)」은 2026-08-31 에 퇴역한 기계의 이름입니다.** 그 기계가 만든 유물
  (`EXTRACTED_FROM.md` · `.extraction-layout.json`)이 레인 안에 남아 있어서, 신규
  합류자는 이 폴더를 **「배송 산출물 보관소」**로 읽습니다. 실제로는 **오늘 개발이
  일어나는 작업 폴더**입니다. 정반대입니다.
* **「final」은 아무 정보도 주지 않습니다.** 그리고 「final」이 붙은 이름은
  **뒤에 `-final2` 가 붙는 날**이 옵니다.

권장: **`fcc-lanes/`** — 「레인 둘을 담는다」는 사실 그대로. 또는 `fcc-workspace/`.

### 바꿔도 안전한가 — 실측했습니다

```bash
$ grep -rl 'fcc-delivery-final' fcc-test-platform --include='*.py' --include='*.json' \
    --include='*.md' --include='*.yml' | grep -v '.git/\|node_modules\|__pycache__\|.venv'
# → 12 파일. 전부 «주석과 사후 평가 기록». 실행되는 로직은 0건.
$ grep -rl 'fcc-delivery-final' fcc-test-contracts …
# → 0 파일
```

**레인 코드는 깨지지 않습니다.**

### ⚠️ 딱 하나가 깨집니다 — auto memory

```
~/.claude/projects/-home-kmjkds-fcc-delivery-final/
                   └─────────────┬──────────────┘
                    폴더의 «절대 경로» 에서 파생된 이름
```

공식 문서: *"Each project gets its own memory directory at
`~/.claude/projects/<project>/memory/`. The `<project>` path is **derived from the git
repository**, so all worktrees and subdirectories within the same repo share one auto
memory directory. **Outside a git repo, the project root is used instead.**"*

컨테이너 폴더는 git 저장소가 **아니므로** 「project root = 그 폴더의 경로」가 이름이 됩니다.
**폴더를 rename 하면 그 디렉터리를 못 찾고, 저장된 메모리가 전부 끊깁니다.**

대처 둘 중 하나:

```bash
# (가) 메모리 디렉터리도 같이 rename
mv ~/fcc-delivery-final ~/fcc-lanes
mv ~/.claude/projects/-home-kmjkds-fcc-delivery-final \
   ~/.claude/projects/-home-kmjkds-fcc-lanes

# (나) 이름을 «고정» 해서 앞으로 경로에 안 묶이게 한다
export CLAUDE_CODE_PROJECT_DIR_NAME=fcc-lanes    # v2.1.234+
```

⚠️ **어차피 auto memory 는 기계-로컬입니다.** 팀원 PC 로 따라가지 않습니다.
그러므로 **팀이 알아야 하는 것을 auto memory 에 두면 안 됩니다** — 그것이 §4 의 이유입니다.

---

## 4. `CLAUDE.md` 를 어디에 두는가

### 공식 규칙 (원문)

> *"Claude Code loads `CLAUDE.md` and `CLAUDE.local.md` from your current working
> directory and **every directory above it**. … All discovered files are **concatenated**
> into context rather than overriding each other."*
>
> *"Claude also discovers `CLAUDE.md` … in **subdirectories** under your current working
> directory. Instead of loading them at launch, they are included **when Claude reads
> files in those subdirectories**."*

즉 **위로는 launch 시점에 전부, 아래로는 그 파일을 읽을 때** 로드됩니다.

### 그래서 이렇게 됩니다

| 위치 | 언제 로드 | **팀과 공유?** |
|---|---|---|
| `~/fcc-lanes/CLAUDE.md` (컨테이너) | 레인 안에서 세션을 열면 **상위 탐색으로 로드됨** | 🔴 **안 됨** — git 저장소가 아니다 |
| `fcc-test-platform/CLAUDE.md` | 그 레포에서 세션을 열면 로드 | ✅ **source control** |
| `fcc-test-contracts/CLAUDE.md` | 〃 | ✅ |
| `~/.claude/CLAUDE.md` (개인) | 모든 프로젝트 | 🔴 나만 |
| auto memory | 모든 세션 (첫 200줄) | 🔴 **기계-로컬** |

공식 문서의 표가 그 축을 이름으로 적습니다 — *Project instructions … **Shared with:
Team members via source control***.

### 🔴 규칙을 컨테이너 `CLAUDE.md` 에 쓰지 마십시오

그 파일은 **당신 PC 에만 존재합니다.** 팀원이 `fcc-test-platform` 을 단독 clone 하면
그 파일은 **없습니다.** 규칙이 있는 것과 규칙이 **도달하는** 것은 다른 명제이고,
이 프로젝트는 이미 그 사고를 한 번 겪었습니다:

> `fcc-test-platform` 은 2026-09-07 이전까지 **매 세션 로드되는 규칙이 0개**였습니다.
> `.claude/rules/*.md` 셋이 전부 `paths:` 조건부여서 **그 경로를 만지지 않는 세션에는
> 로드되지 않았고**, 그 자리를 사용자 auto memory 98개가 메우고 있었습니다.
> **auto memory 는 기계-로컬이라 동료 PC 에 하나도 가지 않습니다.**

컨테이너 `CLAUDE.md` 에 두어도 되는 것은 **이 PC 의 폴더 배치에 대한 사실**뿐입니다 —
「여기서 `git` 을 치면 `not a git repository` 가 난다. 정상이다」 같은 것.

### 오늘의 상태 — `fcc-test-contracts` 는 «절반» 메워졌습니다

실측 (2026-09-07 **09:58**, `origin/main` 기준):

| 레인 | `CLAUDE.md` | `.claude/rules/` | `main` 보호 |
|---|---|---|---|
| `fcc-test-platform` | ✅ 있음 (PR #143) | ✅ 3개 | ✅ `lane-check` required |
| `fcc-test-contracts` | ✅ 138줄 (`f8992ee`, **09:43:43**) | 🔴 **0개** | 🔴 **없음** |

```bash
git -C fcc-test-contracts fetch origin
git -C fcc-test-contracts ls-tree --name-only origin/main CLAUDE.md
gh api repos/junnv93/fcc-test-contracts/branches/main/protection
```

⚠️ **커널 레인의 `main` 은 여전히 무방비입니다** — `{"message":"Branch not protected"}`.
협업자를 초대하는 순간, 그 사람은 계약 커널의 `main` 에 **직접 push 할 수 있습니다.**
platform 이 그것을 소비하므로 여기의 사고는 platform 전체로 번집니다.
**초대보다 이것이 먼저입니다.**

---

## 4-a. ⚠️ 이 문서가 «쓰이는 동안» 낡은 사례

<!-- 사고 기록이다. 지우지 마라 — 지우면 다음 사람이 같은 확신을 갖는다. -->

이 절의 초판은 이렇게 적혀 있었습니다:

> 🔴 **`fcc-test-contracts` 에는 `CLAUDE.md` 도 `.claude/rules/` 도 없습니다.**
> 계약 커널 레인을 여는 세션에는 오늘 아무 규칙도 도달하지 않습니다.

**그 문장은 쓰인 시점에 참이었고, 15분 뒤에 거짓이 됐습니다.**

```
09:33 경   실측했다 — CLAUDE.md 없음
09:43:43   형제 세션이 f8992ee 로 그것을 만들었다
09:43 경   그 실측을 문장으로 옮겨 적었다        ← 적는 순간 이미 거짓이었다
09:58      착지 직전 재확인하다 발견했다
```

⚠️ **`90-known-traps.md` §0 이 말하는 바로 그 유형입니다.** 시점을 담은 서술은
**쓴 날에 참이라 재측정할 이유가 생기지 않습니다.** 이 사례에서 그 「날」은
**15분**이었습니다.

**교훈 둘:**

1. **실측과 «그것을 문장으로 적는 일» 사이에도 시간이 흐릅니다.**
   착지 직전에 다시 재십시오 — 특히 「없다」는 주장은.
2. **「없다」는 「있다」보다 빨리 낡습니다.** 없는 것은 누군가 만들면 되지만,
   있는 것이 사라지려면 누군가 지워야 합니다.
   **부재 주장의 유통기한이 더 짧습니다.**

---

## 5. 그래서 «무엇을 어디에» 쓰나 — 판단 규칙

| 이 사실은… | 어디에 |
|---|---|
| 모든 세션에 참이고 **팀이 알아야 함** | 그 레포의 **`CLAUDE.md`** (200줄 목표) |
| 특정 경로를 만질 때만 참 | 그 레포의 **`.claude/rules/*.md`** + `paths:` |
| 여러 단계의 절차 | **skill** (`.claude/skills/`) — 필요할 때만 로드 |
| **막아야** 하는 것 (지침이 아니라 강제) | **hook** — `CLAUDE.md` 는 강제 계층이 아니다 |
| 내 PC 의 폴더 배치 | 컨테이너 `CLAUDE.md` 또는 `CLAUDE.local.md` |
| 내 개인 취향 | `~/.claude/CLAUDE.md` |

공식 문서가 그 경계를 명시합니다 — *"Claude treats them as context, **not enforced
configuration**. To block an action regardless of what Claude decides, use a
**PreToolUse hook** instead."*

⚠️ **이 구분이 이 프로젝트에서 특히 중요합니다.** 여기서 실제로 막는 것은
`githooks/` 셋과 서버의 `lane-check` 이고, `CLAUDE.md` 는 **막지 않습니다.**
「`CLAUDE.md` 에 썼으니 지켜질 것이다」는 이 레포가 이름 붙여 기록한 실패 유형
(**「거부될 수 없는 승인은 게이트가 아니다」**)의 한 사례입니다.

---

## 6. 확인 — 내 세션에 무엇이 실제로 로드됐나

```
/context      ← Memory files 목록에 무엇이 있는지 «본다»
/memory       ← 파일을 열어 고친다
```

⚠️ **「파일이 있다」로 만족하지 마십시오.** `/context` 로 **실제 로드를 확인**하십시오.
이 프로젝트가 겪은 사고가 정확히 그 형태입니다 — 파일 셋이 있었는데 전부 조건부라
**아무 세션에도 도달하지 않았습니다.**
