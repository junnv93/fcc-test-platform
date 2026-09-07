# 첫날 대본 — 그대로 따라 치십시오

> **이 문서는 「이해」가 아니라 「실행」을 위한 것입니다.**
> 개념은 `01-concepts-for-non-developers.md` 가 답합니다. 여기서는 **명령과 멘트**만 줍니다.
>
> 두 트랙은 **다른 폴더에서 다른 명령을 칩니다.** 섞지 마십시오.
>
> ⚠️ 표기: `<...>` 는 당신이 바꿔 넣을 자리입니다. 꺾쇠까지 지우고 넣으십시오.

---

# 트랙 B — 플랫폼 공동개발

**여기부터가 「우리 프로젝트」입니다.** 대부분 이쪽을 먼저 세웁니다.

## B-0. 준비 — 설치돼 있어야 하는 것

```bash
git --version         # 아무 버전이나
python3 --version     # 3.11 이상 (실측 요구: requires-python >=3.11)
gh --version          # GitHub CLI. 없으면 https://cli.github.com
node --version        # v22.13 이상 v23 미만  ← 웹 화면을 만질 때만
```

⚠️ **파이썬이 3.11 미만이면 여기서 멈추고 먼저 올리십시오.** 뒤에서 나는 오류가
원인을 말해 주지 않습니다.

## B-1. GitHub — 초대를 «수락» 합니다

1. 운영자가 초대하면 **메일**이 옵니다. 또는 직접:
   `https://github.com/junnv93/fcc-test-platform/invitations`
2. **Accept** 를 누릅니다.
3. 커널도 만질 예정이면 `fcc-test-contracts` 도 같은 방식으로 수락합니다.

**확인** — 수락됐는지 스스로 물어보십시오:

```bash
gh api repos/junnv93/fcc-test-platform/collaborators --jq '.[].login'
# → 목록에 «당신 핸들» 이 있어야 한다
```

⚠️ **두 레포는 public 이라 «읽기»는 초대 없이도 됩니다.** 초대는 **push 권한**을 위한
것입니다. clone 이 된다고 초대가 수락된 것은 아닙니다.

## B-2. 로컬 인증 — HTTPS 로 붙입니다

```bash
gh auth login
#   ? What account do you want to log into?   GitHub.com
#   ? What is your preferred protocol?        HTTPS      ← ⚠️ SSH 아님
#   ? Authenticate Git with your credentials? Yes
#   ? How would you like to authenticate?     Login with a web browser

gh auth setup-git
```

⚠️ **SSH 를 고르지 마십시오.** SSH 키가 등록되지 않은 기계에서
`Permission denied (publickey)` 가 실측된 적이 있습니다.

## B-3. 폴더를 만들고 clone 합니다

```bash
mkdir -p ~/fcc-lanes
cd ~/fcc-lanes

git clone https://github.com/junnv93/fcc-test-platform.git
git clone https://github.com/junnv93/fcc-test-contracts.git    # 커널도 만질 때만
```

**결과:**

```
~/fcc-lanes/
   ├── fcc-test-platform/       ← 독립 저장소
   └── fcc-test-contracts/      ← 독립 저장소 (선택)
```

⚠️ **`~/fcc-lanes` 자체는 저장소가 «아닙니다».** 두 clone 을 나란히 두는 폴더일
뿐이고, 거기서 `git status` 를 치면 `not a git repository` 가 납니다 — **정상입니다.**
자세한 이유와 이름에 대해서는 `06-workspace-and-claude-md.md` §2·§3.

⚠️ **폴더 이름을 나중에 바꾸면 Claude Code 의 저장된 메모가 끊깁니다.**
바꿀 생각이면 **지금** 정하십시오 (`06-…` §3).

## B-4. 훅을 켭니다 — ⚠️ clone 마다 «수동»입니다

```bash
cd ~/fcc-lanes/fcc-test-platform
git config core.hooksPath githooks

# 확인
git config --get core.hooksPath        # → githooks
ls githooks/                           # → commit-msg  pre-commit  pre-push
```

⚠️ **이걸 안 하면 커밋 게이트 셋이 «전부 꺼진 채로» 일하게 됩니다.**
그 상태에서도 커밋은 되고, **CI 에서 처음 막힙니다.**

⚠️ **그리고 훅을 `git -c core.hooksPath=…` 로 «임시로» 켜지 마십시오.**
그 방식은 하위 프로세스에 `GIT_CONFIG_PARAMETERS` 를 넘기고, 그러면
**「훅이 걸렸나」를 묻는 검사가 «미설정 체크아웃도 통과»시킵니다**(실측 2026-09-07).
즉 게이트가 조용히 이빨을 잃습니다.

**설정으로 두고 그냥 `git push` 하십시오.**

⚠️ 그 상태에서 `test_an_unset_checkout_reads_as_empty` 가 빨개지면
**그것은 결함이 아니라 정확한 경보입니다** — 「이 환경에서는 본 검사가 미설정을
못 본다」는 뜻입니다. **테스트를 고치지 말고 훅 켜는 방식을 고치십시오.**

## B-5. 파이썬 환경 — CI 와 «같게» 맞춥니다

```bash
cd ~/fcc-lanes/fcc-test-platform
python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate

pip install -e '.[test]'            # ⚠️ -e 가 중요합니다
```

⚠️ **`-e` 를 빼지 마십시오.** CI(`checks.yml`)가 정확히 이 명령을 쓰고,
워크플로 주석이 이유를 적습니다 — *"비-editable 은 `build/` 를 남겨 실패 집합을
오염시킨다."* 그리고 **당신이 검사하는 폴더와 설치된 폴더가 같아야** 합니다
(`01-concepts-for-non-developers.md` §3).

**확인 — 여기서 초록이 나와야 다음으로 갑니다:**

```bash
QT_QPA_PLATFORM=offscreen python3 scripts/lane_check.py --root .
# → lane-check: fcc-test-platform
#      선언된 실패 0개 / 관측된 실패 0개
#      ✅ 일치 — 이 상자는 선언한 그대로다.
```

⚠️ **`QT_QPA_PLATFORM=offscreen` 을 빼면 화면이 없는 환경에서 core dump 가 납니다.**

⚠️ **빨간색이 나오면 「내가 깨뜨렸다」가 아닙니다** — 아직 아무것도 안 고쳤으니까요.
`01-concepts-for-non-developers.md` §3 을 읽고, 그래도 모르겠으면 **출력을 그대로**
전달하십시오.

## B-6. Claude Code 를 «그 폴더에서» 엽니다

```bash
cd ~/fcc-lanes/fcc-test-platform      # ⚠️ 반드시 이 폴더에서
claude
```

⚠️ **`~/fcc-lanes` 에서 열지 마십시오.** 규칙 파일(`CLAUDE.md`)은
**현재 폴더와 그 위 폴더들**에서 읽힙니다. 레인 폴더에서 열어야 그 레인의 규칙이
로드됩니다.

## B-7. 📋 첫 멘트 — 그대로 복사해 붙여넣으십시오

```
이 저장소에서 처음 작업을 시작한다. 먼저 «환경이 제대로 섰는지»부터
확인하고 결과를 보고해줘. 고치지는 말고 «상태만» 알려줘.

1. /context 를 확인해서 CLAUDE.md 가 실제로 «로드됐는지» 봐줘.
   「파일이 있다」가 아니라 「로드됐다」를 봐야 한다.

2. git config --get core.hooksPath 가 githooks 인지.

3. git fetch origin 후, 내 HEAD 가 origin/main 의 조상인지.
   ⚠️ 이 저장소는 여러 세션이 동시에 만진다. HEAD 가 낡으면
      「남이 이미 착지시킨 파일」이 내 미커밋 변경처럼 보인다.

4. git worktree list 로 지금 누가 무엇을 하고 있는지.

5. QT_QPA_PLATFORM=offscreen python3 scripts/lane_check.py --root .
   가 초록인지.
   ⚠️ 빨간색이면 「내가 깨뜨렸다」고 결론하지 마라. 나는 아직 아무것도
      안 고쳤다. 「트리 × 설치본」 축을 먼저 의심하고, .pth 가 어느 트리를
      가리키는지 확인해줘.

그리고 이 저장소를 처음 보는 나에게, docs/onboarding/README.md 를 읽고
「내가 이번 주에 무엇부터 하면 되는지」를 세 줄로 정리해줘.
```

## B-8. 📋 실제로 일을 시작할 때 — 기능 작업의 첫 멘트

```
intent/_templates/intent.md 서식으로 새 intent 를 만들어줘.

문제는:
(겪은 문제를 «그냥 말로» 쓰세요. 정식 문서로 쓰려고 애쓰지 마십시오 —
 그게 에이전트의 일입니다.)

⚠️ 해결책을 문제로 쓰지 마라. 「배지를 추가해야 한다」는 문제가 아니라
   이미 정해진 해결책이고, 그 문장으로는 다 만든 뒤에 됐는지 판정할 수 없다.
   「누가 · 무엇을 못 해서 · 어떤 비용이 나는지」를 물어봐 줘.

⚠️ Open questions 를 「없음」으로 비우지 마라.
   열린 질문이 하나도 없는 의도는 대개 «덜 생각한» 의도다.

⚠️ 이 저장소는 PUBLIC 이다. 의뢰자·고객을 실명이나 연락처가 아니라
   «역할» 로 써라 (「외부 의뢰자 한 곳」, 「시험원 A」 처럼).

⚠️ intent.md «하나만» 만들고 멈춰라. spec.md 와 plan.md 는 이것이
   승인(PR 머지)된 뒤다.
```

⚠️ **여기서 멈추십시오.** 동료 한 분이 PR 을 읽고 머지하면 승인된 것입니다.
**자기 PR 은 자기가 승인하지 않습니다.**

## B-9. 📋 코드를 만지기 시작할 때

```
intent/<슬러그>/spec.md 가 승인됐다. plan.md 를 만들고 구현해줘.

⚠️ 먼저 docs/onboarding/90-known-traps.md 를 읽어라. 이 저장소가 이미
   밟은 함정들이고, 여러 건은 「기록을 읽고도 같은 자리에 다시 선」 것이다.

⚠️ 커밋 규율:
   - git add <파일...> 로 명시 지정. -A 나 . 는 금지 (다른 세션 파일이 실린다)
   - 브랜치를 «바꾸지» 말고 push: git push origin HEAD:refs/heads/<이름>
   - 머지는 gh pr merge --merge. --squash 금지 (자가점검이 사라진다)
   - 커밋 메시지에 17항목 자가점검 필수. 상태 뒤에 «사유»가 없으면 거부된다

⚠️ 새 검사를 만들면, 일부러 깨진 입력을 «주입해서» 빨개지는지 확인해라.
   그 주입이 «착지했는지» 부터 확인해라 — 공허한 주입은 성공한 주입과
   같은 모양이다.

⚠️ 다 되면 push 하기 «전»에 lane_check 을 돌려서 초록인지 보여줘.
```

---

# 트랙 A — 내 프로그램을 provider 로

**여기는 «당신의» 저장소입니다.** 위에서 만든 폴더가 아닙니다.

## A-0. 준비 — 두 가지만 확인합니다

```bash
cd <당신의 프로젝트 폴더>

git status              # git 저장소인가? 아니면 git init 부터
docker --version        # 이미지를 만들려면 필요합니다
python3 --version       # 3.11 이상 권장 (플랫폼과 맞춥니다)
```

⚠️ **아직 git 저장소가 아니어도 됩니다.** 첫 멘트에서 에이전트가 같이 세웁니다.

⚠️ **당신 저장소를 public 으로 만들 필요는 «없습니다».** provider 코드는
비공개로 남는 것이 이 설계의 전제입니다(ADR-0018 D-5).

## A-1. 온보딩 문서를 «가져옵니다»

두 레인이 public 이므로 토큰 없이 받을 수 있습니다.

```bash
mkdir -p docs

curl -sL https://raw.githubusercontent.com/junnv93/fcc-test-platform/main/docs/onboarding/10-provider-lane-kickoff.md \
  -o docs/PROVIDER-KICKOFF.md

curl -sL https://raw.githubusercontent.com/junnv93/fcc-test-platform/main/docs/onboarding/15-image-handoff-walkthrough.md \
  -o docs/IMAGE-HANDOFF.md

curl -sL https://raw.githubusercontent.com/junnv93/fcc-test-platform/main/docs/onboarding/90-known-traps.md \
  -o docs/KNOWN-TRAPS.md
```

**확인:**

```bash
head -3 docs/PROVIDER-KICKOFF.md      # 제목이 보이면 성공
```

⚠️ **이 파일들은 «사본»입니다.** 원본이 고쳐져도 자동으로 따라오지 않습니다.
분기마다 다시 받거나, 원본을 읽으십시오.

## A-2. 계약 커널을 설치합니다

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install "fcc-test-contracts @ git+https://github.com/junnv93/fcc-test-contracts@v0.1.26"
```

⚠️ **`@v0.1.26` 은 «태그»입니다. 브랜치가 아닙니다.** 최신 태그는
`gh api repos/junnv93/fcc-test-contracts/tags --jq '.[0].name'` 로 확인하십시오.

**확인:**

```bash
python3 -c "import fcc_test_contracts, pathlib; print(pathlib.Path(fcc_test_contracts.__file__).parent / 'artifacts')"
# → 그 폴더에 provider_onboarding.md 와 *_headless_api_contract.example.json 이 있어야 한다
```

## A-3. Claude Code 를 «당신 폴더에서» 엽니다

```bash
cd <당신의 프로젝트 폴더>
claude
```

## A-4. 📋 첫 멘트 — 환경부터 세웁니다

```
이 저장소를 FCC 중앙 플랫폼의 provider 레인으로 만들려고 한다.
docs/PROVIDER-KICKOFF.md 를 읽어줘.

⚠️ 코드부터 만지지 마라. §1 의 「0단계」— «환경» 부터 세운다.

만들 것 넷:
  1. 루트에 CLAUDE.md   — 200줄 이하. 「매 세션 참인 것」만.
  2. .claude/rules/     — 경로별 규칙 (paths: frontmatter 필수)
  3. intent/            — 기능 개발의 앞문
  4. .claude/skills/    — 반복 절차 (계약 검사 · 이미지 빌드 등)

⚠️ CLAUDE.md 에 반드시 들어가야 하는 것 넷:
   - 이 저장소는 provider 레인이고, 분야 지식(측정·시험계약)의 «주인» 이다
   - 중앙 플랫폼에 우리 소스를 두지 않는다. 이미지로만 간다
   - 계약 아티팩트는 «여기서» 발행한다. 중앙 트리에 «복사하지» 않는다
   - provider_id 는 계약 SSOT 에서 «읽어서» 쓴다. 문자열로 박지 않는다

⚠️ 각 파일을 만들기 «전»에 「무엇을 왜 그 위치에 두는지」를 한 줄로 설명하고,
   내가 승인하면 써라. 한 번에 다 쓰지 마라.

⚠️ 그리고 docs/KNOWN-TRAPS.md 를 먼저 읽어라. 우리가 이미 밟은 것들이고,
   그중 여럿은 「기록을 읽고도 같은 자리에 다시 선」 것이다.
```

## A-5. 📋 계약 적합성 — 두 번째 멘트

```
계약 적합성을 세워줘.

1. 설치된 패키지 안의 fcc_test_contracts/artifacts/provider_onboarding.md 를
   «읽어서» 절차를 따라라. docs/PROVIDER-KICKOFF.md 의 요약을 믿지 말고
   원문을 읽어라.

2. ⚠️ 스키마가 아니라 «예시 계약» 에서 출발해라 —
   fcc_test_contracts/artifacts/*_headless_api_contract.example.json

3. 검사를 우리 CI 에 붙여라. 로컬에서만 도는 검사는 잊혀진다.

4. ⚠️ 검사를 만든 «뒤», 일부러 깨진 입력을 주입해서 빨개지는지 확인해라.
   빨개지지 않으면 그 검사는 없는 것과 같다.

5. 우리 계약 아티팩트를 이 저장소에서 export 하는 진입점을 만들어라.
   ⚠️ 그것을 중앙 저장소에 «복사하지» 마라 — 운영자가 2026-08-31 에
      명시적으로 기각한 형태다. 중앙 레지스트리는 «가리킬» 뿐이다.
```

## A-6. 📋 이미지 — 세 번째 멘트

```
docs/IMAGE-HANDOFF.md 를 읽고, 우리 headless API 이미지를 만들 준비를 해줘.

⚠️ §1 의 「빌드 전에 확인할 것 셋」을 실제로 확인하고 결과를 보고해라.
   git status 가 깨끗하지 않으면 «멈추고» 물어봐라 — 리비전 라벨이
   거짓말하게 된다.

⚠️ --build-arg GIT_REVISION 을 «반드시» 넣어라. 빼면 폐쇄망에서
   「중앙에 있는 것이 최신인가」를 물을 방법이 사라진다.

⚠️ 의존성 설치는 «분리» 해라 — 서드파티는 정상 resolve,
   git+ 레인만 --no-deps. 한 번에 하면 ResolutionImpossible 이 난다.
   그리고 핀 값을 Dockerfile 에 다시 적지 마라. 목록에서 «파생» 해라.

⚠️ 중앙 PC 에서 도는 명령은 «실행하지 말고» 내가 칠 명령으로 출력만 해라.
   그건 내 승인이 필요한 구간이다.
```

---

# 공통 — 첫 주에 자주 쓰는 멘트

## 📋 「지금 상태가 어떤지 모르겠다」

```
지금 이 저장소의 상태를 정리해서 알려줘.
- 내 HEAD 가 origin/main 보다 얼마나 뒤처졌나
- 미커밋 파일이 있나. 있으면 «내 것»인가 (origin/main 과 diff 해서 판정해줘)
- 지금 도는 워크트리가 있나
- lane_check 이 초록인가

⚠️ git status 만 보고 판정하지 마라. 그건 「어느 참조점에 대해」를
   말해 주지 않는다.
```

## 📋 「문서와 실제가 다르다」

```
docs/<파일> 의 <어느 문장> 이 지금도 맞는지 «실측으로» 확인해줘.
맞지 않으면 그 문장을 «지우지 말고» 정정으로 남기고, 재는 명령을 함께 적어줘.

⚠️ 다르다는 것을 알고도 넘어가면 다음 사람이 그 틀린 문서를 믿는다.
   이 프로젝트에는 그렇게 잘못된 판정을 내린 기록이 여러 건 있다.
```

## 📋 「검사가 빨간데 왜인지 모르겠다」

```
검사가 빨갛다. 아래 출력을 보고 판정해줘.

⚠️ 결론하기 전에 이 셋을 갈라라:
   1. 내가 방금 깨뜨린 것인가        → 대조군(내 변경 없는 상태)과 «이름 집합»을 비교해라
   2. 환경 문제인가                  → .pth 가 어느 트리를 가리키는지 봐라
   3. 원래 간헐적으로 빨간 것인가     → 알려진 flaky 이름과 대조해라

<검사 출력을 여기 붙여넣으세요>
```

## 📋 「모르는 말이 나왔다」

```
「<모르는 말>」 이 이 프로젝트에서 정확히 무슨 뜻인지 알려줘.
비유 말고, 이 저장소의 «실제 예시» 로 설명해줘. 어느 파일을 보면 되는지도.
```

⚠️ **그냥 물으십시오.** 이 프로젝트의 가장 비싼 사고들은 「모르는데 아는 척한 것」이
아니라 **「각자 다른 뜻으로 같은 말을 쓴 것」**에서 나왔습니다.

---

# ⚠️ 첫날에 «하지 말아야» 하는 것

| 하지 마십시오 | 왜 |
|---|---|
| `git add -A` · `git add .` | 다른 세션의 파일이 딸려 들어갑니다 |
| `git checkout -b` 로 브랜치 «바꾸기» | 옆 세션의 발밑이 바뀝니다. `push origin HEAD:refs/heads/<이름>` |
| `gh pr merge --squash` | 커밋 본문이 대체돼 자가점검이 **사라집니다.** 푸시 뒤엔 못 고칩니다 |
| `--no-verify` · `FCC_SKIP_LANE_CHECK=1` | 게이트를 끄는 것입니다. 막히면 **왜 막혔는지 물으십시오** |
| `git -c core.hooksPath=…` 로 훅 임시로 켜기 | 설정이 하위로 새어 **「훅이 걸렸나」 검사가 미설정 체크아웃도 통과**시킵니다. 게이트가 조용히 이빨을 잃습니다 |
| `main` 에 직접 push | platform 은 서버가 막습니다. **`fcc-test-contracts` 는 «안 막습니다»** |
| 태그 push · 컨테이너 재기동 · DB 마이그레이션 | **사람의 승인이 따로 필요한 항목**입니다 |
