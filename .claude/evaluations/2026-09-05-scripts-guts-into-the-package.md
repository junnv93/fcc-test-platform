# scripts/ 알맹이 이관 웨이브 — 실측 (2026-09-05)

## 왜 (Why)

`scripts/` 는 파이썬 패키지가 아니고 **휠이 나르지 못한다.** 실제로 지어 확인했다:

```
fcc_test_platform-0.1.8-py3-none-any.whl
  fcc_test_platform/*_cli.py   실림
  scripts/*                     0개
```

그래서 이 레인을 핀으로 받는 소비자(모노레포)에게 `scripts/` 의 로직은 «오지
않는다». 모노레포는 자기 사본을 갖고 있고, 배송 기계가 2026-08-31 에 퇴역한 뒤로
그 사본들은 아무것도 동기화하지 않은 채 드리프트 중이다.

즉 **같은 로직의 사본이 둘인데 어느 쪽이 원본인지 기계가 판정하지 못하는** 상태다.
알맹이가 패키지로 가면 휠이 나르고, 양쪽에 남는 껍데기는 22줄이라 갈라질 것이 없다.

## 무엇을 (What)

`.extraction-layout.json` 이 예약한 `scripts/*.py` 38건 중 선례 2건을 뺀 **36건**
(지시서는 34건이라 적었으나 실측은 36건 — 그 2건 차를 보고했다). 알맹이를
`fcc_test_platform/<name>_cli.py` 로, `scripts/` 에는 22줄 진입점만.

`.extraction-layout.json` 은 **건드리지 않았다** — 껍데기가 같은 경로에 남고
패키지 파일은 애초에 그 장부에 없다(선례 `86381a1` 도 안 건드렸다).

## 어떻게 (How)

한 번에 하나씩. 일괄 26건은 앞선 시도에서 47건 red 를 냈다. 매 건마다 **일곱 축**:

| 축 | 무엇을 묻는가 | 무엇을 못 보나 |
|---|---|---|
| ① 수집 에러 | import 가 해소되나 | 문자열 참조 |
| ② 심볼 해소·패키지 규율 | 이름이 실제로 있나 | 대상의 실재 |
| ③ 문자열 참조 | 옛 이름을 문자열로 든 곳 | 경로가 «식»으로 쪼개진 것 |
| ③b **공허화** | 옛 파일 «내용»을 읽던 시험 | — |
| ⑤ **미해소 내부 import** | 상자 안을 가리키는데 없는 대상 | — |
| ④ lane_check | 전체 시험 | 위 넷이 전부 초록으로 통과 |

③b 와 ⑤ 는 이 웨이브에서 **새로 세운 축**이다. 사유는 아래.

## 검증 (Verification)

- 기준선(origin/main d977275, 전용 워크트리 + 전용 venv): 3,051 통과 / 29 skip /
  783 subtest / 실패 1. 그 1건(`test_token_revocation_custody::…rotation_flood…`)은
  단독 재실행 73 passed → **부하 의존 flake**. 이관 시작 후로는 3,052 통과.
- `testpaths = ["tests"]` 라 `tests/integration/` 16건 포함(형제 세션이 자기 실행기의
  `ls tests/test_*.py` 로 그것을 빠뜨렸다고 알려 와 `--collect-only | grep -c` 로 확인).
- 매 건 `lane_check --root .` 「선언된 실패 0 / 관측된 실패 0 ✅ 일치」.

## 새로 세운 봉인 둘 — 그리고 왜 기존 봉인이 못 보나

### `tests/test_stale_internal_import_axis.py`

함수 «안»의 `from fcc_test_platform.<없는모듈> import X` 를 셋 다 통과시킨다:

| 봉인 | 왜 못 잡나 |
|---|---|
| `test_platform_api_name_resolution` | 「이름이 묶이는가」를 본다. `from X.Y import Z` 는 `X.Y` 가 없어도 `Z` 를 묶는다 |
| `test_supply_closure_axis` | 서드파티·타 레인을 잰다. 이 상자 «내부» 경로는 대상이 아니다 |
| `import-linter` | 없는 모듈은 간선을 안 만든다 — 신호가 0 |

셋 다 결함이 아니라 **축이 다르다.** 이 검사는 `find_spec` 으로 **해소해 본다.**
반증 양방향 확인: 탐침 주입 시 파일:줄+모듈명으로 1건 지목(exit 1), 제거 시
`fcc_test_platform` 177파일/305건 0건 · `scripts` 55파일/84건 0건.

⚠️ 검사한 **파일 수·import 수를 함께 돌려준다** — `0건` 이 「깨끗하다」인지
「안 돌았다」인지 갈리게. 비-공허성으로 그 둘을 단언한다.

### 공허해진 경계 단언 10곳의 상환

껍데기가 같은 경로에 남으므로 `read_text()` 는 계속 성공한다. 읽히는 것이 22줄
껍데기라 `assertNotIn('FastAPI', text)` 류가 **참이지만 아무것도 재지 않는 참**이
된다. **이관 전에도 후에도 초록이라 어떤 게이트도 못 잡는다** — 정적 감사로만 보인다.

⚠️ **그중 하나는 선례 커밋 `86381a1` 이 2026-08-31 에 남긴 것**이고 5일째 그
상태였다. 이 웨이브가 그것을 드러냈으므로 함께 상환한다.

처방은 저장소가 이미 갖고 있다 — `tests/_moved_module_source.py` 가 경로가 아니라
**모듈**에게 어디 사는지 묻는다. 거기에 **안티-공허 팔**을 함께 단다:

반증 3방향(실측):
- 현 상태 → `1 passed`
- 알맹이에 `import subprocess` 주입 → `AssertionError: ['subprocess'] is not false`
- 알맹이를 통째로 비움 → `AssertionError: [] is not true : … 알맹이가 또 옮겨갔다면
  이 검사도 «새 자리»를 가리키게 고쳐라`

세 번째 팔이 없으면 알맹이가 사라져도 「금지 집합과의 교집합이 공집합」이라 초록이다.
실패 메시지가 **「검사를 지워라」가 아니라 「검사도 따라 옮겨라」**를 가리켜야 한다.

⚠️ `test_platform_cutover_readiness_cli.py::test_the_cli_delegates_to_the_packaged_module`
은 **껍데기를 가리키는 것이 옳다** — 「껍데기가 배포판 모듈에서 가져오는가」를
묻는다. 옮기면 그 축이 사라진다. 옮기지 않았다.

## 소비 형태는 여섯이 아니라 여덟이었다

지시서의 여섯에 더해 실측으로 둘. ⑦ 은 위(공허화). ⑧ 은:

```python
# scripts/check_central_provider_id_pairing.py:57
_SIBLING = _REPO_ROOT / 'scripts' / 'check_auth_mode_pairing.py'   # 리터럴이 쪼개져 있다
spec = importlib.util.spec_from_file_location('check_auth_mode_pairing', _SIBLING)
```

`scripts/<name>.py` 를 **한 문자열**로 찾는 탐지기는 이것을 못 본다. 이관 직후
시험 3개가 red 가 되어서야 드러났다 —
`AttributeError: module 'check_auth_mode_pairing' has no attribute 'read_env_text'`.

⚠️ **예측한 형태 목록이 아니라 «드라이버가 실제로 멈춘 자리»가 목록을 늘렸다.**
그래서 한 건씩 돌리는 규율이 값을 한다 — 일괄이면 47건이 한꺼번에 빨개지고
어느 형태가 원인인지 안 갈린다.

수리는 경로 대신 모듈:
`_SIBLING_MODULE = 'fcc_test_platform.check_auth_mode_pairing_cli'` + `import_module`.
로드 실패를 **판정 불가(2)**로 내리는 기존 계약은 보존했다 — 그 계약 자체가
2026-09-03 에 「검사가 죽었다」와 「값이 틀렸다」가 같은 exit 1 로 보이던 결함의
수리다. 부수 효과로 **휠이 그 파서를 나르게** 됐다.

## 남은 판단거리 — 보고하고 바꾸지 않은 것

`PROJECT_ROOT = Path(__file__).resolve().parents[1]` 을 **실제 파일 접근에 쓰는** 3건
(`platform_extraction_runner` 7회 · `platform_central_db_live_proof` 7회 ·
`platform_cutover_live_workflow` 4회). 체크아웃에서는 `scripts/` 와
`fcc_test_platform/` 이 같은 깊이라 값이 **동일**하다. 휠 설치에서는 site-packages 를
가리킨다. **기계적 이관이므로 식을 그대로 뒀다** — 루트 해소 방식을 바꾸는 것은
별도 결정이고, 이 웨이브에 얹으면 「무엇이 이관이고 무엇이 설계 변경인가」가 섞인다.

## 후속

- **모노레포 껍데기는 릴리스 후에만.** 모노레포는 `@v0.1.8` 을 핀으로 받으므로
  새 `_cli` 모듈은 그때까지 설치본에 없다. 순서: 레인 커밋 → 태그 → 핀 갱신 → 모노레포.
- 왈러스 오탐(`_comprehension_walrus_targets`)이 `origin/main` 에 들어간 뒤 남은 3건.


## 이관이 «없던 검사를 새로 붙였다»

`platform_keyset_cursor_live_proof` 를 옮기자 `test_provider_id_uuid_slot_seal` 이
즉시 red 를 냈다 — 「uuid provider_id 슬롯에 INSERT 하는데 봉인 프로브가 없다」.
그 봉인은 `fcc_test_platform/` 만 훑으므로, 라이브 PostgreSQL 에 5개 표로 INSERT 하는
이 코드가 `scripts/` 에 있던 동안 **시야 밖**이었다.

프로브를 붙였고 공허하지 않음을 따로 쟀다 — 문장 20개 기록, uuid 슬롯 **8건** 관측,
전부 UUID 바인딩. (봉인의 `observed > 0` 은 프로브 «전체»를 합산하므로 내 것만
0건이어도 통과했을 자리다.)

**휠이 나른다는 것 말고도, 패키지의 더 엄격한 게이트가 그 코드를 처음으로 훑기
시작한다.** 그것이 이 웨이브의 두 번째 이득이다.

## 축 일곱과 lane_check 를 «전부 통과한» 결함 둘

껍데기를 전부 `--help` 로 불러 보니 시작 시 17개 중 **2개가 죽어 있었다.** 어떤
시험도 껍데기를 부르지 않아 모든 축이 초록이었다.

| 죽어 있던 것 | 왜 | 처방 |
|---|---|---|
| `_keycloak_chamber_admin` | CLI 가 아니라 **라이브러리**다 — `main()` 이 없는데 껍데기 템플릿이 `from … import main` 을 했다 | 이름에서 `_cli` 를 떼고 `scripts/` 쪽은 **재수출 shim** 으로 |
| `platform_provider_identity_live_proof` | 알맹이가 «모듈 최상위»에서 실행된다 — `DSN = sys.argv[1]` 이 import 시점에 돈다 | 본문을 `main()` 안으로. **휠이 나르는 모듈이 import 만으로 라이브 DB 에 붙으면 안 된다** |

「알맹이에 `main()` 이 있고 import 는 부수효과가 없다」는 껍데기 템플릿의 **가정**이고
아무도 검사하지 않았다. `tests/test_cli_shells_are_runnable.py` 로 그 가정을 검사로
바꿨다 — 팔이 둘이고 반증을 양쪽 다 확인했다:

```
팔① 알맹이의 main 제거     → rc=1 ImportError: cannot import name 'main' …
팔② 최상위 실행 주입       → 「이 자리들은 «import 하는 것만으로» 돈다 … main() 안으로 넣어라」
복원                        → 2 passed, 30 subtests
```

## 충실성 — 로직이 바뀐 것은 «한 줄»

최상위 함수·클래스를 이름으로 짝지어 `ast.dump` 로 대조했다(줄 번호·주석·docstring
위치는 무시된다). 28건 중 어긋난 것 둘, 둘 다 의도된 것이다:

```
chamber_token_evidence_cli._run_live   ← 형제 import 재지정 «한 줄»
- from scripts._keycloak_chamber_admin import run_lifecycle_live
+ from fcc_test_platform.keycloak_chamber_admin import run_lifecycle_live

provider_identity_live_proof_cli       ← 최상위 정의·상수가 main() «안»으로 들어갔다
```

「사본 둘 중 어느 것이 원본인가」를 기계가 판정하지 못하던 상태를 끝내려면 옮긴 것이
**같은 코드**여야 한다. 그 주장을 값으로 뒷받침한다.

## 부하 flake 를 기준선이 갈랐다

`test_token_revocation_custody::…rotation_flood…` 가 두 번 red 를 냈다(기준선 1회 ·
`cutover_bundle` 이관 후 1회). **단독 재실행은 두 번 다 73 passed.** `lane_check` 은
「선언에 없는 실패 — 새로 깨진 것이다」라고 말하지만, 부하와 새 결함은 같은 모양이라
그 도구가 «웨이브가 만들었나»를 답할 수 없다. 기준선에서 그 이름을 미리 잡아 둔 것이
여기서 값을 했다. 판정은 둘로 한다 — ① 기준선에 있었나 ② 단독 재실행이 초록인가.

## 상환 도구 자신이 두 번 틀렸다

| 가정 | 증상 | 처방 |
|---|---|---|
| 「알맹이가 이미 옮겨졌다」 | 없는 모듈을 `moved_module_source` 에 넘겨 수집 에러 | 전제를 **검사**로 |
| 「import 는 한 줄이다」 | 여러 줄 `from X import (` 의 **괄호 안**에 삽입 → SyntaxError | 정규식 대신 **AST 의 `end_lineno`** |

둘째가 드라이버를 멈췄고, 그 정지는 **대상 파일 탓이 아니라 내 상환 탓**이었다.
「드라이버가 멈췄다 = 그 파일이 문제다」로 읽으면 엉뚱한 것을 고친다.
