# fcc-test-platform

> ## ⚠️ 이 레포는 **읽기 전용 납품물**입니다
>
> 여기 있는 파일은 이 레포에서 작성되지 않았습니다. 비공개 모노레포
> `junnv93/FCC_mobile_test_automation` 에서 추출 매니페스트를 따라 **생성**되어 배송됩니다.
>
> **여기서 고친 것은 다음 배송에서 조용히 덮어써집니다.** 배송은 이 트리를 병합하지 않고
> *다시 만들기* 때문입니다 — merge 가 아니라 replace 라서 충돌도, 경고도 나지 않습니다.
> 고칠 곳은 원본입니다 → [§변경은 어디로 보내나](#변경은-어디로-보내나)

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
# ⚠️ 두 레포를 형제로 나란히 두세요 — 이 상자는 혼자 돌지 않습니다.
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

### 4. 이 상자는 **혼자 돌지 않습니다** — 형제 레인이 필요합니다

`fcc-test-contracts` 가 `sys.path` 에 함께 있어야 import 가 해소됩니다. 아래 §테스트 참조.

---

## 어떻게 돌리나

### 설치

```bash
python3 -m pip install -e .            # 런타임
python3 -m pip install -e '.[test]'    # + pytest, openpyxl
```

Python **3.11 이상**이 필요합니다(중앙 API 컨테이너 베이스 이미지에서 파생된 하한).

⚠️ **`fcc-test-contracts` 를 담을 패키지 인덱스가 아직 없습니다.** `pyproject.toml` 이 그것을
의존성으로 선언하지만 해석기가 받아올 곳이 없으므로, 오늘은 형제 레포를 나란히 clone 해서
경로로 얹습니다(아래).

### 테스트

```bash
# 두 레포가 형제로 나란히 있다고 가정
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
| **설치 검증** | 휠이 빌드된다는 것과 설치된 배포물이 돈다는 것은 다른 명제입니다 |
| **형제 레인 해소** | 위 §설치 — 오늘은 경로로 얹습니다 |
| **태그된 릴리스** | `version` 은 형제 npm 배포물에서 파생된 값이고 대응 태그가 아직 없습니다 |

**그리고 알려진 경계 부채가 있습니다.** 이 상자에는 아직 import 경계 위반이 남아 있고
그 개수 또한 매니페스트에 등재돼 한 방향으로만 줄어듭니다
(`governance.staged_import_violation_baseline`). 개수는 `EXTRACTED_FROM.md` 에 있습니다.

---

## 변경은 어디로 보내나

읽기 전용 고지가 만드는 질문에 답이 없으면 그 고지는 막다른 길입니다. 답은 이렇습니다.

1. **모노레포 접근 권한이 있다면** — `junnv93/FCC_mobile_test_automation` 에서 고치세요.
   이 트리의 각 파일이 모노레포의 어느 경로에서 왔는지는 추출 매니페스트
   `docs/api/headless_contract_extraction_manifest.v1.json` 의 `entries` 가 갖고 있습니다
   (`current_path` → `future_path`).
2. **접근 권한이 없다면** — 이 레포에 **이슈**를 여세요. PR 은 병합될 수 없습니다.
   원본이 여기가 아니므로, 여기 머지된 커밋은 다음 배송에서 사라집니다.

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
