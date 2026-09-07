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
| 대기 중 초대 | 0 | 0 | — |
| `main` 보호 | 있음 (§3) | 🔴 **없음** | — |

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

## 3. 🔴 초대보다 먼저 — `fcc-test-contracts` 의 `main` 은 무방비입니다

```bash
$ gh api repos/junnv93/fcc-test-contracts/branches/main/protection
{"message":"Branch not protected", "status":"404"}
```

**협업자를 초대하는 순간, 그 사람은 `fcc-test-contracts` 의 `main` 에 직접 push 할 수
있습니다.** 리뷰도, 검사도, 아무것도 그 사이에 없습니다.

그리고 이 레인은 **계약 커널**입니다 — platform 이 그것을 소비하므로,
여기의 사고는 platform 전체로 번집니다.

### 대비 — platform 은 오늘 이렇습니다

```bash
$ gh api repos/junnv93/fcc-test-platform/branches/main/protection
```

| 항목 | 값 | 뜻 |
|---|---|---|
| `required_status_checks.contexts` | `["lane-check"]` | 이 검사가 초록이어야 머지된다 |
| `required_status_checks.strict` | `false` | base 가 움직여도 재검사를 요구하지 않는다 |
| `enforce_admins` | **`true`** | **관리자도 우회 못 한다** ← 이것이 강제의 근거 |
| `required_approving_review_count` | **`0`** | ⚠️ **승인 없이 머지된다** |
| `require_code_owner_reviews` | `false` | `CODEOWNERS` 는 리뷰어를 요청만 하고 막지 않는다 |
| `required_linear_history` | `false` | merge 커밋 허용 (no-squash 규율과 정합) |

⚠️ **`required_approving_review_count: 0` 이 뜻하는 것.**
`intent/README.md` 는 「동료 1인 승인」을 규칙으로 둡니다. 그런데 **오늘 그것을
강제하는 것은 아무것도 없습니다** — 사람이 지키는 규율이지 기계가 막는 게이트가
아닙니다. 협업자가 둘 이상이 되면 이 값을 `1` 로 올릴 수 있고, 그때 비로소
그 규칙에 이빨이 생깁니다.

⚠️ **오늘 `1` 로 올리면 안 됩니다.** 협업자가 1명일 때 올리면 GitHub 이 자기 PR
자기 승인을 금지하므로 **아무것도 머지할 수 없게 됩니다.** 순서는 **초대 먼저,
승인수 인상은 그 다음**입니다.

### 권고 순서

```
① fcc-test-contracts 에 branch protection 을 켠다      ← 오늘 비어 있다
② 두(또는 세) 레포에 각각 초대한다                        permission=push
③ 초대가 «수락된 것»을 확인한다                          collaborators 에 나타나야 한다
④ 그 다음에 required_approving_review_count 를 1 로     ← 순서를 뒤집으면 잠긴다
```

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
