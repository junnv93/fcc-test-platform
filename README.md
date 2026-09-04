# fcc-test-platform

> ## 이 레포는 **정식 분리된 레포**입니다 (2026-09-05 갱신)
>
> 이 레인은 자기 일을 수행합니다 — 여기서 설계하고, 개발하고, 커밋합니다.
> 형제 레인 `fcc-test-contracts` 도 자기 OpenAPI 를 스스로 발행합니다(`d83ebee`).
>
> ⚠️ **다만 배송 체계는 아직 살아 있고, 파일마다 다릅니다.**
> `.extraction-layout.json` 에 등재된 **921개 경로**는 여전히 모노레포
> `junnv93/FCC_mobile_test_automation` 에서 생성되어 배송되며, **그 파일을 고치면
> 배송이 그 파일을 이름으로 대며 거부**합니다. 등재되지 않은 것(이 README ·
> `docs/architecture/**` · 새로 더하는 파일)은 **여기서 자유롭게 작성·수정**합니다.
>
> 무엇이 어느 쪽인지 판정하려면:
> ```bash
> python -c "import json;print('납품' if '<경로>' in json.load(open('.extraction-layout.json'))['paths'] else '자유')"
> ```
>
> 🔴 **이 배너의 옛 판(「읽기 전용 납품물」)이 설계 오류를 낳은 적이 있습니다.**
> 그 문장을 근거로 리팩토링 대상을 모노레포로 잘못 잡은 설계서 판이 폐기됐습니다.
> 구조·품질을 판정할 때는 **이 레포에 직접 도구를 돌리십시오** —
> 현황은 [`docs/architecture/2026-09-04-플랫폼-리팩토링-설계서.md`](docs/architecture/2026-09-04-플랫폼-리팩토링-설계서.md).

---

## 이 레포는 무엇인가

여러 시험 분야(provider)가 공유하는 **웹 플랫폼**입니다. 중앙 DB, 백엔드 API, 인증/RBAC,
프론트엔드 셸, provider 레지스트리, 배포 조합, 그리고 성적서/아티팩트 열람 워크플로를 담습니다.

**여기에 측정 코드는 없습니다.** 스펙트럼 분석기 제어, EUT(피시험 단말) 제어, Excel 시험계획,
GUI 는 전부 provider 비공개 레포에 남습니다. 이 레포가 아는 것은 *어느 분야인지 무관하게 참인 것*
— 프로젝트·챔버·세션·결과·성적서 — 뿐입니다.

의존 방향은 **단방향**입니다. 이 레인은 `fcc-test-contracts` 에 의존하고, 그 역은 없습니다.

---

## 받아가기

```bash
# 형제 clone 은 더 이상 필요 없습니다 — pip 이 형제를 태그로 끌어옵니다(2026-08-31).
# pip install "fcc-test-platform @ git+https://github.com/junnv93/fcc-test-platform@v0.1.2"
# 아래 clone 은 *개발*할 때만 필요합니다.
git clone https://github.com/junnv93/fcc-test-platform.git
git clone https://github.com/junnv93/fcc-test-contracts.git
cd fcc-test-platform
```

⚠️ **HTTPS 를 쓰세요.** 이 레포들은 private 이고, 배송 머신에서는 SSH 키가 등록돼 있지
않습니다(실측 2026-08-30: `Permission denied (publickey)`). `gh auth login` 후
`gh auth setup-git` 을 한 번 돌리면 HTTPS 자격증명이 붙습니다.

---

## 지금 이 사본이 정확히 무엇인가 → `EXTRACTED_FROM.md`

원본 SHA · 추출 시각 · 매니페스트 버전 · 파일 수 · 알려진 실패 수는 **`EXTRACTED_FROM.md`** 에
있고 배송할 때마다 새로 생성됩니다.

⚠️ **이 README 에는 그 숫자를 적지 않습니다.** 배송마다 바뀌는 값을 두 곳에 적으면
반드시 어긋나고, 어긋난 쪽을 읽은 사람은 틀린 것을 믿게 됩니다.

---

## ⚠️ 처음 clone 한 사람이 먼저 알아야 할 것 넷

### 1. 여기서 고치지 마세요 (위 §읽기 전용)

### 2. git history 가 없습니다 — 그리고 그건 결함이 아닙니다

운영자 판정으로 **history 를 이전하지 않습니다.** 첫 커밋이 이 레포의 전부입니다.
"누가 왜 이 줄을 썼나"가 필요하면 **모노레포를 직접 조회**하세요.

### 3. 테스트가 전부 통과하지 않습니다 — 그리고 그것도 알려진 상태입니다

이 상자는 자기 테스트 suite 를 싣고 있고, 그중 **일부는 실패한 채로 배송됩니다.**
그 실패는 **node id 집합으로 모노레포 매니페스트에 등재돼** 있고
(`governance.delivered_test_run_baseline`), 한 방향으로만 줄어듭니다.

> **당신이 무언가를 깨뜨린 것이 아닙니다.** 정확한 개수는 `EXTRACTED_FROM.md` 에 있습니다.

⚠️ 개수가 아니라 **이름 집합**으로 판정하는 이유: 개수는 *고쳐진 실패*와 *새로 깨진 실패*를
맞바꾼 것을 구분하지 못합니다.

### 4. **설치는 혼자 되고, 테스트는 형제 트리가 필요합니다** — 둘은 다릅니다

이 둘을 하나로 묶어 적으면 한쪽이 반드시 틀립니다. 2026-08-31 실측:

| 하려는 일 | 형제 clone 필요? | 실측 |
|---|---|---|
| **설치·실행** (`pip install`) | **불필요** | 형제를 태그로 자동 해소 — `fcc-test-contracts 0.1.2` 가 함께 설치됩니다 |
| **납품 테스트 실행** | **필요 (트리로)** | 없으면 **1,492 수집 + 오류 6**, 형제 트리를 얹으면 **1,820 수집 + 오류 0** |

⚠️ **형제를 «설치»하는 것으로는 테스트가 낫지 않습니다.** 휠은 import 가능한 패키지만 싣고,
상자 **루트**에 놓이는 아티팩트(`artifacts/`·레이아웃 기록)는 싣지 않습니다. 그래서 해소기가
정직하게 거부합니다 — `DependencyTreeUnavailable: … A lane installed as a wheel carries its
importable packages, not the artifacts it ships at its box root`. 필요한 것은 **트리**입니다.

---

## 어떻게 돌리나

### 설치

```bash
python3 -m pip install -e .            # 런타임
python3 -m pip install -e '.[test]'    # + pytest, openpyxl
```

Python **3.11 이상**이 필요합니다(중앙 API 컨테이너 베이스 이미지에서 파생된 하한).

✅ **형제는 자동으로 해소됩니다 (2026-08-31).** `pyproject.toml` 이 `fcc-test-contracts` 를
**직접 참조**(태그 고정 git URL)로 선언하므로 인덱스가 없어도 pip 이 받아옵니다. 실측: 갓
clone 한 트리에서 `pip install -e .` 만으로 `fcc-test-contracts 0.1.2`·`bcrypt`·`fastapi` 가
함께 설치됩니다. **테스트를 돌릴 때만** 형제 트리가 추가로 필요합니다(위 §4).

### 테스트

```bash
# ⚠️ 여기서는 형제 «트리»가 필요합니다 — 설치와 달리(§4).
#    상자 루트의 아티팩트는 휠에 실리지 않기 때문입니다.
#   <parent>/fcc-test-platform     ← 여기
#   <parent>/fcc-test-contracts
SIB="$(cd .. && pwd)/fcc-test-contracts"
PYTHONPATH="$PWD:$PWD/scripts:$SIB:$SIB/scripts" \
python3 -m pytest -q -p no:randomly -p no:cacheprovider \
        --tb=no -ra --continue-on-collection-errors
```

⚠️ **`--continue-on-collection-errors` 는 장식이 아닙니다.** pytest 는 기본적으로 모듈 하나가
import 실패하면 수집 전체를 중단합니다. 그러면 상자가 **0개를 수집하고**, 0개는 "새 실패 없음"을
완벽하게 만족합니다 — 실패가 성공과 같은 모양이 됩니다. 이 플래그가 그 모듈을 하나의 `ERROR`
노드로 남겨서 다른 실패와 똑같이 판정되게 합니다.

⚠️ **`scripts/` 는 이 상자에서 의도적으로 패키지가 아닙니다**(`__init__.py` 없음).
자기 것과 **형제 레인의 것 둘 다** `PYTHONPATH` 에 들어가야 합니다 — 패키지 관리자가 있었다면
그것이 얹었을 자리입니다.

### 프론트엔드

`apps/web/` 에 프론트엔드 셸이 함께 배송됩니다. ⚠️ **`node_modules/` 와 빌드 산출물은
배송되지 않습니다**(추출 exclusions) — `npm install` 로 직접 설치해야 합니다.

---

## ⚠️ 챔버 노드 런타임은 이 상자에 없습니다

이 상자에는 챔버의 **중앙 쪽**이 전부 들어 있습니다 — 챔버 등록부 스키마, 읽기/쓰기 어댑터,
토큰 발급, 계측 스테이징, 관련 마이그레이션과 API 표면. 그래서 **챔버라는 말이 코드 전반에
나옵니다.**

그런데 **챔버 PC 에서 도는 노드 런타임은 여기 없습니다.** 그것은 매니페스트에 실재하는
별도 레인 `fcc-chamber-node` 의 소유이고, 그 레인은 오늘 **어느 상자에도 배송되지 않습니다**
(`extraction_target: false`). 구체적으로 `infra/chamber/` 와
`packages/session-node-artifacts/` 가 이 상자에 **없습니다** — `infra/` 는 있는데
`infra/chamber/` 만 없으므로, 찾다 보면 빠뜨린 것처럼 보입니다. 빠뜨린 것이 아닙니다.

> **이 상자로 챔버 노드를 세울 수 없습니다.** 중앙을 세울 수는 있습니다.

⚠️ 없는 것을 있는 것처럼 읽히게 두면 다음 사람이 없는 메뉴를 찾다가 포기합니다 — 이 문단은
그것을 막으려고 있고, `fcc-chamber-node` 가 배송되기 시작하면 이 문단을 요구하는 봉인이
스스로 꺼집니다.

---

## 무엇이 아직 없나 — 이 배송의 완료명은 「첫 배송 + 리허설」입니다

「레포 분리 완료」가 **아닙니다.** 다음이 아직 없습니다:

| 없는 것 | 뜻 |
|---|---|
| **CI** | 이 레포에서 게이트가 돌지 않습니다. 판정은 아직 모노레포에서만 납니다 |
| **lockfile** | 두 배포물을 담을 인덱스가 아직 없어 해석기가 비교할 대상이 없습니다 |
| ~~**설치 검증**~~ | ✅ **했습니다 (2026-08-31)** — 갓 clone 한 트리에서 `pip install "fcc-test-platform @ git+…@v0.1.2"` 가 성공하고, platform 은 `BcryptPasswordHasher` 왕복까지 확인했습니다. ⚠️ *휠이 빌드된다*와 *설치된 배포물이 돈다*가 다른 명제라는 이 행의 말은 옳았고, 그래서 재 봤습니다 |
| **형제 레인 해소** | 위 §설치 — 오늘은 경로로 얹습니다 |
| **태그된 릴리스** | `version` 은 형제 npm 배포물에서 파생된 값이고 대응 태그가 아직 없습니다 |

**그리고 알려진 경계 부채가 있습니다.** 이 상자에는 아직 import 경계 위반이 남아 있고
그 개수 또한 매니페스트에 등재돼 한 방향으로만 줄어듭니다
(`governance.staged_import_violation_baseline`). 개수는 `EXTRACTED_FROM.md` 에 있습니다.

---

## 검사를 켜 두기 — `pre-push` 훅 (clone 마다 한 번)

이 상자에는 검사가 하나 있고, **당신이 켜야 켜집니다.**

```sh
git config core.hooksPath githooks
```

⚠️ **왜 GitHub 이 대신 해 주지 않나.** 2026-08-30 실측: 이 계정의 GitHub Actions 는
잡을 **러너에 배정하지 못합니다**. 본문이 `echo` 한 줄뿐인 워크플로조차 2초 만에
`steps: []` · `runner_name: ""` 로 실패합니다 — 즉 *검사가 실패한 것*이 아니라
*시작조차 못 한 것*이고, 두 상태는 화면에서 똑같은 빨간 X 로 보입니다.
`.github/workflows/checks.yml` 은 그래서 오늘 **휴면**입니다. 지우지 마세요 —
결제/가시성이 풀리는 날 같은 검사를 그대로 이어받습니다.

### 이 검사가 판정하는 것

**전부 통과인가**가 아닙니다. 이 상자는 오늘 전부 통과하지 못합니다(모노레포에만
있는 경로를 단언하는 테스트가 남아 있고, 분리가 끝나야 사라집니다). 대신
**관측된 실패의 이름 집합이 `delivered_test_run_baseline.json` 의 선언과 같은가**를
봅니다. 그래서 셋이 동시에 성립합니다:

* 알려진 실패는 **통과**로 읽힙니다 — 당신을 헛되이 막지 않습니다.
* 새로 깨진 것은 **즉시 red** 이고 **이름으로** 말합니다.
* 고쳐진 것도 red 입니다 — 선언이 낡았다는 뜻이고, 그것도 소식입니다.

⚠️ **개수가 아니라 이름 집합입니다.** 하나 고치고 하나 깨뜨리면 개수는 같습니다.

직접 돌리려면:

```sh
python3 scripts/lane_check.py
```

### ⚠️ 판정이 설치 방식에 따라 달라집니다

`pip install .`(non-editable)은 상자 안에 `build/lib/` 를 만들고, 트리를 스캔하는
테스트 일부가 그 사본을 원본과 **함께 셉니다** — 선언에 없는 실패가 생깁니다.
그리고 `.gitignore` 가 `build/` 를 덮으므로 **`git status` 는 깨끗하다고 답합니다.**
게이트는 그런 트리에서 판정을 **거부**하고 무엇을 지우라고 말합니다.

판정에 쓰는 설치는 이것입니다:

```sh
pip install -e '.[test,oidc]'
```

### 한계 — 이것은 실수 방지층이지 방어층이 아닙니다

설치가 clone 마다 opt-in 이고, `--no-verify` 한 번이면 사라집니다. 진짜 강제는
러너가 돌아오고 branch protection 이 이 검사를 required 로 거는 날에 생깁니다.
그날까지 이것이 **가장 값싼 근사**입니다.

우회해야 하면 `FCC_SKIP_LANE_CHECK=1 git push`, 그리고 **왜 우회했는지 적으세요.**

## 변경은 어디로 보내나

읽기 전용 고지가 만드는 질문에 답이 없으면 그 고지는 막다른 길입니다. 답은 이렇습니다.

1. **모노레포 접근 권한이 있다면** — `junnv93/FCC_mobile_test_automation` 에서 고치세요.
   이 트리의 각 파일이 모노레포의 어느 경로에서 왔는지는 추출 매니페스트
   `docs/api/headless_contract_extraction_manifest.v1.json` 의 `entries` 가 갖고 있습니다
   (`current_path` → `future_path`).
2. **접근 권한이 없다면** — 무엇을 하려는지에 따라 답이 다릅니다(2026-08-31 이후).

   | 하려는 일 | 여기서 되는가 |
   |---|---|
   | **새 파일·새 모듈**을 더한다 | **된다.** 배송이 지우지 않습니다 |
   | 커밋을 푸시한다 | **된다.** 배송의 부모로 남습니다 |
   | **납품 파일**(아래 표의 트리)을 고친다 | **여기서는 안 됩니다** — 아래 참조 |

   ⚠️ **납품 파일을 고치면 배송이 멈춥니다.** 사라지지는 않습니다 — 배송이 그 파일을
   **이름으로 대며 거부**하고, 누군가 그 편집을 모노레포로 옮길 때까지 이 레포는 새 배송을
   받지 못합니다. 즉 잃는 것은 당신의 작업이 아니라 **모두의 배송**입니다. 그러니 납품 파일
   수정은 이슈로 올려 주세요.

⚠️ **이것은 불편이 아니라 설계입니다.** 이 분리의 목적은 아키텍처가 아니라 **공개 범위**입니다 —
측정 코드·GUI·EUT 제어·Excel 은 비공개로 남고, 그 나머지가 여기서 팀과 함께 개발됩니다.

---

## 레이아웃

| 경로 | 내용 |
|---|---|
| `fcc_test_platform/` | 레인의 런타임 코드. 최상위 import 패키지 |
| `domain/` · `application/central_contract/` | shared-kernel 소비 폐포. 소유는 shared-kernel, 납품은 이 상자 |
| `apps/web/` | 프론트엔드 셸 (Vite + React + TS) |
| `tests/` | 이 레인의 테스트 suite (귀속 ∪ import 폐포 ∪ `conftest.py`) |
| `scripts/` | 운영자 CLI. **패키지가 아닙니다** — 최상위 이름으로 도달 |
| `migrations/` · `docs/platform/` | 중앙 DB 마이그레이션과 그 스키마/증거 문서 |
| `infra/` · `config/` | 배포 조합(compose 등)과 런타임 설정 |
| `pyproject.toml` | 설치 선언. 모노레포 `packaging/fcc-test-platform/` 에서 리뷰되고 여기 루트로 배송됩니다 |
| `.extraction-layout.json` | ⚠️ **지우지 마세요.** 패키저가 *무엇을 어디로 옮겼는지* 남긴 기록이고, 런타임 아티팩트 해소기가 이것을 읽습니다. 빌드 잔여물이 아닙니다 |
