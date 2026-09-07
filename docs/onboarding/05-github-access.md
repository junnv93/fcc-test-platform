# GitHub 접근 — 무엇을 초대하고, 초대 전에 무엇을 고쳐야 하나

> 대상: **운영자**(초대하는 사람)와 **신규 합류자**(초대받는 사람).
>
> 이 문서의 모든 값은 **2026-09-07 에 `gh api` 로 직접 조회**한 것입니다.
> 재조회 방법을 각 절에 함께 적었습니다 — 이 표가 낡았는지 당신이 직접 물을 수 있어야 합니다.

---

## 1. 실측 — 오늘의 상태

```bash
# 이 표를 다시 만드는 명령
for r in junnv93/fcc-test-platform junnv93/fcc-test-contracts junnv93/FCC_mobile_test_automation; do
  gh api repos/$r --jq '"\(.full_name) visibility=\(.visibility) owner_type=\(.owner.type)"'
done
gh api repos/junnv93/fcc-test-platform/collaborators --jq '.[] | "\(.login) \(.role_name)"'
```

| | `fcc-test-platform` | `fcc-test-contracts` | `FCC_mobile_test_automation` |
|---|---|---|---|
| 가시성 | **public** | **public** | **private** |
| 소유자 유형 | `User` | `User` | `User` |
| 협업자 | `junnv93` (admin) | `junnv93` (admin) | — |
| 대기 중 초대 | **2** — `JJDHJJ` · `kkulhong` (미수락) | **2** — `JJDHJJ` · `kkulhong` (미수락) | — |
| `main` 보호 | ✅ 있음 (§3) | ✅ **있음** (§3 — 2026-09-07 켜짐) | — |

<!-- 정정 이력 — 지우지 마세요. -->
> 🔴 **정정 (2026-09-07 12:08 실측).** 이 표의 옛 판은 **대기 중 초대 `0` / `0`**,
> `fcc-test-contracts` 의 `main` 보호 **「🔴 없음」** 이었습니다.
> **그 값들은 쓰인 날 아침에는 참이었고 같은 날 낮에 거짓이 됐습니다** —
> 01:32~01:58 에 초대 2건이 두 레포에 각각 발송됐고, 그 사이 contracts 에
> branch protection 이 켜졌습니다.
> ⚠️ **초대는 «발송»이지 «수락»이 아닙니다** — `collaborators` 는 아직 1명입니다.
> 그 구분이 §3 의 권고 순서 ③·④ 를 가릅니다.
> 재는 법:
> ```bash
> gh api repos/junnv93/fcc-test-platform/invitations   --jq '.[].invitee.login'
> gh api repos/junnv93/fcc-test-platform/collaborators --jq '.[].login'
> gh api repos/junnv93/fcc-test-contracts/branches/main/protection
> ```

---

## 2. 답 — 각각 초대해야 합니다

**예. 두 레포에 따로 초대해야 합니다.**

세 가지 사실이 그 답을 만듭니다:

1. **소유자가 개인 계정입니다** (`owner.type == "User"`). Organization 이 아니므로
   **Team 이 존재하지 않고**, 여러 레포에 한 번에 권한을 주는 경로가 없습니다.
2. **두 레포는 별개의 레포입니다.** GitHub 의 collaborator 권한은 레포 단위입니다.
3. 두 레인은 **정식으로 분리된 레포**이지 모노레포의 하위 폴더가 아닙니다.

### 그런데 «읽기» 는 초대가 필요 없습니다

두 레인 다 **public** 이므로 다음은 **초대 없이** 됩니다:

```bash
git clone https://github.com/junnv93/fcc-test-platform.git      # OK
git clone https://github.com/junnv93/fcc-test-contracts.git     # OK
pip install "fcc-test-platform @ git+https://github.com/junnv93/fcc-test-platform@v0.1.12"   # OK
```

⚠️ **이것이 트랙 A(provider 화)에 중요합니다.** provider 개발자는 자기 레포에서
두 패키지를 pip 로 설치하기만 하면 되고, 그 설치에는 **토큰도 초대도 필요 없습니다.**
계약 검사는 provider 자기 레포에서 돕니다(운영자 판정 2026-08-31).

### 초대가 필요한 것은 «쓰기» 입니다

| 하려는 것 | 초대 필요? |
|---|---|
| clone · 읽기 · `pip install git+https` | ❌ 불필요 (public) |
| `git push origin HEAD:refs/heads/<브랜치>` | ✅ **필요** (write) |
| PR 생성 · 리뷰 승인 · 머지 | ✅ **필요** |
| 모노레포(`FCC_mobile_test_automation`) 조회 | ✅ **필요** (private — 세 번째 초대) |

이 프로젝트의 작업 규율은 **fork 가 아니라 origin 브랜치**를 씁니다
(`CLAUDE.md` §2 — 공유 체크아웃에서 브랜치를 바꾸지 말고
`git push origin HEAD:refs/heads/<name>`). 그러므로 **개발에 참여하는 사람은
write 권한이 필요합니다.**

### 초대 명령

```bash
# 트랙 B(플랫폼 공동개발)에 참여하는 모든 사람
gh api -X PUT repos/junnv93/fcc-test-platform/collaborators/<핸들> -f permission=push

# 계약 커널을 만질 사람 (필드 추가는 2-레포 작업이고 커널 태그가 앞선다)
gh api -X PUT repos/junnv93/fcc-test-contracts/collaborators/<핸들> -f permission=push

# provider 화 참고용으로 모노레포를 열 경우에만
gh api -X PUT repos/junnv93/FCC_mobile_test_automation/collaborators/<핸들> -f permission=pull
```

⚠️ `permission=push` 는 write 입니다. `admin` 을 주지 마십시오 — admin 은
**branch protection 자체를 끌 수 있고**, 그것이 이 레포의 유일한 강제층입니다(§3).

---

## 3. ✅ `fcc-test-contracts` 의 `main` — 보호가 켜졌습니다 (2026-09-07)

```bash
$ gh api repos/junnv93/fcc-test-contracts/branches/main/protection
```

| 항목 | `fcc-test-contracts` | `fcc-test-platform` | 같나 |
|---|---|---|---|
| `required_status_checks.contexts` | `["lane-check"]` | `["lane-check"]` | ✅ |
| `required_status_checks.strict` | `false` | `false` | ✅ |
| `required_approving_review_count` | `0` | `0` | ✅ |
| `require_code_owner_reviews` | `false` | `false` | ✅ |
| `dismiss_stale_reviews` | `true` | `true` | ✅ |
| `required_linear_history` | `false` | `false` | ✅ |
| `allow_force_pushes` / `allow_deletions` | `false` / `false` | `false` / `false` | ✅ |
| **`enforce_admins`** | 🔴 **`false`** | ✅ **`true`** | ❌ **다릅니다** |

### 🔴 남은 비대칭 하나 — `enforce_admins`

**`fcc-test-contracts` 에서는 관리자가 보호를 우회해 `main` 에 직접 push 할 수 있습니다.**
platform 에서는 못 합니다.

✅ **이 비대칭은 «의도»이고 근거가 적혀 있습니다** — `fcc-test-contracts/CLAUDE.md`
§`enforce_admins` 절이 그것을 상세히 답합니다:

> *"이 레인은 **태그를 내는 레인**입니다. … 이 계정의 Actions 는 **할당량 때문에
> 러너를 못 받은 전례가 있고**(2026-08), 그 건에 대한 2026-09-07 판정은
> 「지금은 그대로 둔다」입니다 — 즉 **원인이 해소되지 않았습니다.**
> `enforce_admins: true` 였다면 그날 이 레인의 머지가 **전면 정지**하고, 그것은
> 지역적 불편이 아니라 **공급 사슬이 멈추는 것**입니다."*

그리고 같은 절이 **평시의 대가도 정확히 적습니다** — 이 레포의 협업자는 1명이고
그 1명이 `admin` 이므로, 「관리자는 우회할 수 있다」는 곧 **「사실상 아무나, 언제든
우회할 수 있다」**입니다. 즉 이 레포에서 보호가 실제로 하는 일은
**「PR 을 거치게 하는 것」 하나**이고, `lane-check` 은 그 PR 에 붙는 **조언**입니다.

⚠️ **「red 면 서버가 막는다」는 이 레인에서 거짓입니다.** platform 에서는 참입니다.

<!-- 정정 이력 — 지우지 마세요. -->
> 🔴 **정정 (2026-09-07, 같은 날).** 이 문단의 초판은 이렇게 적었습니다:
>
> > *"이 비대칭이 «의도»인지 «누락»인지 이 문서는 모릅니다. … **그 근거가 어디에도
> > 적혀 있지 않으므로**, 오늘은 「우회할 수 있다」만 참입니다."*
>
> **그 문장이 거짓이었습니다.** 근거는 `fcc-test-contracts/CLAUDE.md` 에 한 절로
> 적혀 있었고, 초판을 쓴 세션은 **그 파일을 §5 까지만 읽고** 나머지를 나중에
> 읽었습니다. 「없다」와 **「내가 못 봤다」의 출력이 같습니다.**
>
> ⚠️ **부재를 주장할 때는 「내 도구가 어디까지 봤나」를 함께 적으십시오** —
> 「`CLAUDE.md` §1~§5 와 `README` 에는 없다」였다면 그 문장은 참이었을 것이고,
> 다음 사람이 어디를 더 봐야 하는지도 알았을 것입니다.
>
> 재는 법: `git -C fcc-test-contracts show origin/main:CLAUDE.md | grep -n -A20 enforce_admins`

**남은 것은 「의도인가」가 아니라 「그 근거가 오늘도 유효한가」입니다** —
근거의 전제는 *「러너 할당량 문제의 원인이 해소되지 않았다」*이고,
그 전제가 바뀌는 날 이 값을 다시 판단해야 합니다.

<!-- 정정 이력 — 지우지 마세요. 이 절의 옛 판을 근거로 「초대 전에 보호부터」라고
     판단한 문서가 이 레포에 여럿 있었고, 그 판단은 이미 이행됐습니다. -->
> 🔴 **정정 (2026-09-07 12:08 실측).** 이 절의 옛 판은 이랬습니다:
>
> > *「## 3. 🔴 초대보다 먼저 — `fcc-test-contracts` 의 `main` 은 무방비입니다 …
> > `{"message":"Branch not protected", "status":"404"}` … 협업자를 초대하는 순간,
> > 그 사람은 `fcc-test-contracts` 의 `main` 에 직접 push 할 수 있습니다.
> > 리뷰도, 검사도, 아무것도 그 사이에 없습니다.」*
>
> **그 서술은 쓰인 날 아침에는 참이었고 오늘 낮에는 거짓입니다.** 보호가 켜졌고
> `lane-check` 가 required 입니다. 다만 **완전히 해소된 것은 아닙니다** — 위 표의
> `enforce_admins: false` 행이 남은 구멍이고, 그 구멍은 **관리자에게만** 열려 있습니다.
> 즉 옛 판의 「누구나 직접 push」는 이제 「**관리자만** 직접 push」입니다.

그리고 이 레인은 **계약 커널**입니다 — platform 이 그것을 소비하므로,
여기의 사고는 platform 전체로 번집니다. 그것이 이 레인의 보호를 먼저 물은 이유입니다.

### 참고 — platform 단독 표 (위 대비표와 같은 값입니다)

```bash
$ gh api repos/junnv93/fcc-test-platform/branches/main/protection
```

| 항목 | 값 | 뜻 |
|---|---|---|
| `required_status_checks.contexts` | `["lane-check"]` | 이 검사가 초록이어야 머지된다 |
| `required_status_checks.strict` | `false` | base 가 움직여도 재검사를 요구하지 않는다 |
| `enforce_admins` | **`true`** | **관리자도 우회 못 한다** ← 이것이 강제의 근거 |
| `required_approving_review_count` | **`0`** | **승인 없이 머지된다 — 의도된 값** |
| `require_code_owner_reviews` | `false` | `CODEOWNERS` 는 리뷰어를 요청만 하고 막지 않는다 |
| `required_linear_history` | `false` | merge 커밋 허용 (no-squash 규율과 정합) |

⚠️ **`required_approving_review_count: 0` 이 뜻하는 것.**
**이 레포에는 승인자가 없습니다**(2026-09-07 2차 개정). 개발 담당자가 `Status: accepted`
를 적고 직접 머지합니다. 즉 이 `0` 은 「규칙이 강제 안 되는 상태」가 아니라 **규칙 그
자체**입니다 — `intent/README.md` §승인 모델이 그렇게 선언합니다.

협업자가 둘 이상이 되면 이 값을 `1` 로 올릴지 **다시 판단**합니다. 그 판단 재료는
선언 파일 `.claude/contracts/branch-protection-declaration.json` 의 `why_each_value`
에 있습니다.

⚠️ **오늘 `1` 로 올리면 안 됩니다.** 협업자가 1명일 때 올리면 GitHub 이 자기 PR
자기 승인을 금지하므로 **아무것도 머지할 수 없게 됩니다.** 순서는 **초대 먼저,
승인수 인상은 그 다음**입니다.

### 권고 순서

```
① fcc-test-contracts 에 branch protection 을 켠다      ✅ 완료 (2026-09-07)
② 두(또는 세) 레포에 각각 초대한다                        ✅ 완료 — JJDHJJ · kkulhong, 두 레포 각각
③ 초대가 «수락된 것»을 확인한다                          ⏳ 대기 — collaborators 는 아직 junnv93 1명
④ 그 다음에 required_approving_review_count 를 1 로     ⏳ ③ 이후 — 순서를 뒤집으면 잠긴다
⑤ (미결) fcc-test-contracts 의 enforce_admins 를 어떻게 할지 판정  ← 운영자
```

**오늘 서 있는 자리는 ③ 입니다.** ②까지 끝났고 ③이 사람의 클릭을 기다립니다.

⚠️ **③ 을 «건너뛰고» ④ 를 하면 잠깁니다.** 초대가 발송됐다는 것과 수락됐다는 것은
다른 사실이고, `invitations` 와 `collaborators` 는 **서로 다른 엔드포인트**입니다.
`invitations` 에 이름이 있는 동안은 아직 협업자가 아닙니다.

⚠️ ①·④ 는 **GitHub 설정 변경**이고, `CLAUDE.md` §P0-5 가 **사람의 승인이 필요한 것**
으로 이름을 적어 둔 항목입니다. 에이전트가 스스로 하지 않습니다 — 운영자가 지시해야 합니다.

---

## 4. 합류자가 처음 할 일

```bash
gh auth login          # HTTPS 를 고르십시오
gh auth setup-git      # HTTPS 자격증명을 git 에 붙입니다

git clone https://github.com/junnv93/fcc-test-platform.git
cd fcc-test-platform
git config core.hooksPath githooks     # ⚠️ clone 마다 opt-in 입니다. 자동이 아닙니다
```

⚠️ **SSH 를 쓰지 마십시오.** 이 레포들에 SSH 키가 등록돼 있지 않은 기계에서
`Permission denied (publickey)` 가 실측된 적이 있습니다(2026-08-30, `README.md`).

⚠️ **`core.hooksPath` 를 설정하지 않으면 커밋 게이트 셋이 전부 꺼진 채로 일하게 됩니다.**
그 상태에서도 커밋은 되고, **CI 에서 처음 막힙니다.**

### 확인 — 훅이 실제로 걸렸는가

```bash
git config --get core.hooksPath           # githooks 가 나와야 한다
ls githooks/                              # commit-msg  pre-commit  pre-push
```

⚠️ **워크트리는 `.git/config` 를 공유합니다.** 즉 한 세션이 훅을 끄면 **모든 세션에서
꺼집니다.** 훅이 사라졌다면 당신이 아니라 옆 세션일 수 있습니다.

---

## 5. 이 문서가 낡았는지 확인하는 법

이 문서의 모든 표는 위에 적은 `gh api` 명령의 출력입니다. **의심되면 다시 재십시오.**
값이 다르면 이 문서를 고치는 것이 그 세션의 일입니다 — 다르다는 것을 알고도
넘어가면, 다음 사람이 틀린 표를 믿습니다.
