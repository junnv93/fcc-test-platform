# 도메인 타입 게이트와 경계 계약을 켠다 — S1·S2 (2026-09-05)

설계서 `docs/architecture/2026-09-04-플랫폼-리팩토링-설계서.md` §6.1~6.2 의 착지다.
**모든 수치는 이 두 레포에 도구를 직접 돌려 얻었다** — 원본 모노레포 인용 0건.
격리 venv(`mypy 2.3.1` · `import-linter 2.15`), 캐시·프로브 설정은 저장소 밖.

## Why

도메인은 이미 순수하다(서드파티 의존 0). 그런데 그것을 **지키는 기계가 없었다** —
`tests/test_architecture_conformance.py` 의 계층·순수성 클래스들은 이 레포에
넘어오지 않았고(파일 헤더가 "나머지 형제 검사는 저쪽에 남았다"고 적는다), 남은
순수성 가드는 **모듈을 하나씩 손으로 고른 AST 검사**다. 그 방식은 원리적으로
전이(간접) 경로를 못 본다 — 실제로 이번에 찾은 위반 ②가 정확히 그것이다.

## What

| 산출물 | 성격 |
|---|---|
| `mypy.ini` | 신규. `domain/*` 만 `disallow_untyped_defs` |
| `.importlinter` | 신규. 3계약 + `app-no-db` baseline 2건 |
| `tests/test_architecture_gate_conformance.py` | 신규. 게이트를 pytest 에 붙이고 baseline 을 봉인 |
| `domain/services/progress_{bucket,pricing,expectation}.py` | 매개변수 주석 4곳 |

## How — 판정 셋

### ① 배송 경계 — 「예약」과 「배송」은 다르다

⚠️ **README §배너와 설계서 §B.1 의 판정 레시피가 틀렸다.**

```bash
python -c "... '<경로>' in json.load(open('.extraction-layout.json'))['paths'] ..."
```

`paths` 는 **원본 경로 → 이 레포 경로** 매핑이고, `in` 은 **키(원본 경로)**를 본다.
921 중 671 은 키와 값이 같아 우연히 맞지만, **250 은 다르다.** 그래서 이 레시피는
루트 `pyproject.toml` 을 「자유」라고 답한다 — 값 쪽에는
`packaging/fcc-test-platform/pyproject.toml -> pyproject.toml` 로 실재하는데도.
패키지 모듈은 값에서 `fcc_test_platform/` 접두사가 빠져 있어(`domain/services/x.py`)
양쪽 어디에도 안 걸린다.

**그리고 값에 있다고 배송된 것도 아니다.** 매니페스트는 *예약 선언*이라 디스크에
없는 경로 55개까지 담는다. 그래서 판정은 두 축이다:

> **예약됨(매니페스트 값) × 실림(배송 커밋에 blob 존재)**

| 파일 | 예약 | 최신 배송(`1c15065`)에 실림 | 판정 |
|---|:--:|:--:|---|
| `pyproject.toml` | ✅ | ✅ | 🔒 배송 관리 |
| `.github/workflows/checks.yml` | ✅ | ✅ | 🔒 배송 관리 |
| `.gitignore` · `scripts/lane_check.py` | ✅ | ✅ | 🔒 배송 관리 |
| `domain/services/progress_*.py` | ✅ | ❌ | ✅ **이 레포가 저자** |

세 도메인 파일은 배송이 아니라 커밋 `61ae9bf`(150 중 **117번째**, 2026-09-03)가
270줄로 **신규 생성**했다. 배송 커밋은 여섯 개뿐이고 전부 #1~#6 이며, 어떤 배송도
`domain/services/` 를 실은 적이 없다. 그래서 고쳐도 배송이 멈추지 않는다.

⚠️ 판정은 **작업트리가 아니라 HEAD 블롭**에 대고 했다 — `.extraction-layout.json`
자체가 다른 세션의 미커밋 수정 상태였다(마이그레이션 3건 추가).

**따라서 설정을 `pyproject.toml` 에 두지 않았다.** 설계서 §6.1 의 `[tool.mypy]`
예시를 그대로 따르면 배송이 그 파일을 이름으로 대며 거부한다. `mypy.ini` 는
설정 우선순위상 `pyproject.toml` 보다 앞서므로 배송본이 갱신돼도 게이트가 안 흔들린다.

### ② 게이트의 «실제» 위치 — 워크플로가 아니다

`checks.yml` 은 **휴면**이다: 이 계정은 Actions 러너를 배정받지 못한다(그 파일이
스스로 적는다). 오늘 막는 것은 `githooks/pre-push` → `scripts/lane_check.py` → pytest 다.
그리고 `checks.yml` 자신이 이렇게 못박는다 — *"검사 정의는 이 파일에 없다.
여기에 명령을 인라인하면 그 순간 두 게이트가 갈라진다."*

그래서 새 워크플로 YAML 을 만들지 않았다. **테스트 한 파일**로 붙였다. 그러면
pre-push 와 (러너가 돌아온 날의) CI 가 자동으로 같은 것을 본다. 배송 파일도 0건 건드린다.
파일명은 `*_conformance` — `tests/conftest.py` 의 `_INVARIANT_FILENAME_TOKENS` 가
`invariant` marker 를 자동 부착하는 명명 규약이다.

### ③ baseline 이 한 방향으로만 줄게 하는 축 둘

- **그래프 축** — `unmatched_ignore_imports_alerting = error`(기본값). 위반이
  해소되면 등재가 그래프에서 안 맞아 게이트가 깨진다 → 고친 사람이 등재를 지운다.
- **정책 축** — `TestTheDbBaselineOnlyShrinks` 가 등재 집합을 **이름으로** 못박는다
  → 새 위반을 조용히 등재해 초록을 만드는 길이 막힌다.

①만으로는 「늘려서 초록 만들기」를, ②만으로는 「해소됐는데 남아 있기」를 못 막는다.
개수가 아니라 이름 집합인 이유는 `delivered_test_run_baseline.json` 과 같다.

## Verification

| 검사 | 결과 |
|---|---|
| mypy `domain/*` strict — 착수 전 | **4건** / 54파일, 3파일. 전부 `no-untyped-def` |
| mypy — 수정 후 (격리) | `Success: no issues found in 54 source files` |
| mypy — 수정 후 (**커널 노출**) | 동일. 주석이 실 시그니처에 대해서도 성립 |
| import-linter | **211 파일 / 919 의존** · 레이어 KEPT · 순수성 KEPT · `app-no-db` BROKEN **2건** |
| import-linter — baseline 등재 후 | `3 kept, 0 broken (2 ignored imports)` |
| 반증 ⓐ 낡은 등재가 남으면 | `exit=1` (`No matches for ignored import …`) |
| 반증 ⓑ 미등재 위반이 있으면 | `exit=1` (`broken_contract_guidance` 출력됨) |
| 진행률 도메인 테스트 | 8 passed |
| 전체 스위트 — **실패 이름 집합** | 변경 전 31 = 변경 후 31, **양방향 차집합 공집합** |

마지막 줄이 이 커밋의 안전 근거다. 사본(`cp -a`)에 내 3파일만 HEAD 블롭으로
되돌려 같은 명령을 돌렸다 — 다른 세션의 미커밋 작업은 양쪽에 그대로 두었으므로
차이는 **내 변경만** 격리한다. 개수는 32↔31 로 흔들렸고 이름 집합은 동일했다.
그 31건은 다른 세션 소관(`sample_inventory` · OpenAPI 아티팩트 · docker compose)이다.

### 타입을 «고른» 근거

추측이 아니라 코드가 정한다.

- `band: Optional[str]` — 커널의 `band_type_for_workbook_band(band_token: Optional[str])`
  에 그대로 넘어간다. **시그니처가 강제한다.**
- `technology: Optional[str]` — 본문에 `if technology is not None` 방어 분기가
  실재한다. `str` 로 좁히면 그 분기가 죽은 코드가 되면서 거짓말이 된다.
- `raw_test_type: str` — 반대로 None 가드가 **없다**. `Optional[str]` 을 붙이면
  `str(None) == 'None'` 이라는 버그를 타입으로 승인하는 셈이다.

## ⚠️ 이번에 실제로 겪은 오진 — 조용한 초록

게이트 첫 판은 `python -m importlinter.cli` 로 계약을 돌렸다. 테스트는 **초록**이었다.
그런데 그 패키지에는 `__main__.py` 가 없어서 모듈을 import 만 하고 **아무 출력 없이
exit=0** 으로 끝난다 — 즉 **계약을 하나도 검사하지 않았다.**

종료코드만 보면 「도구가 안 돌았다」와 「위반이 없다」가 같은 값이다. 이 레포가 같은
계열의 값을 이미 두 번 치렀다(`lane_check` 의 수집 0개, `checks.yml` 의 러너 미배정).
그래서 두 팔 모두 **일했다는 증거**를 함께 본다 — 검사한 파일 수, 분석한 의존 수,
그리고 `KEPT` 가 셋인지.

## 후속

- **S3(DB 어댑터 이전)** 이 baseline 2건을 갚는다. 완료 판정을 이제 **기계가** 한다 —
  옮기면 등재가 안 맞아 게이트가 깨지고, 그때 등재를 지우는 것이 완료 신호다.
- **README §배너 · 설계서 §B.1 의 판정 레시피 정정.** 문서 정합 세션 소관이라
  여기서 고치지 않았다. 근거는 위 §How ①.
- **저장소 전체 strict 확대**는 합의된 범위가 아니다 — `mypy.ini` 의 전역
  `disallow_untyped_defs = False` 를 게이트가 봉인해 둔다.
- `import-linter` 는 `contracts` 레인에 넣지 않는다(설계서 §7 · §2.2 기각).
