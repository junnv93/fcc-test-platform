# 한 파일이 여덟 줄 사이에서 자기 자신과 모순했다 — 그리고 그 방향은 정해져 있다

> 2026-09-07 실측. base `origin/main@0b8461d`. 리그: 워크트리 전용 venv,
> `pip install -e '.[test]'`(CI 와 같은 설치), contracts 핀 `0.1.26`.

## Why — 인계문이 지목한 작업은 이미 끝나 있었다

인계문은 `main@9565be6` 기준으로 **「최상위 75모듈이 게이트 밖, 실제 타입 오류 45건」**
을 본체로 지목했다. 착수 시점의 `origin/main@0b8461d` 에서 재니:

    mypy -p fcc_test_platform  →  Success: no issues found in 215 source files
    tests/test_architecture_gate_conformance.py  →  18 passed · 9 subtests

형제 세션이 PR `#158`(호출 축) · `#159`(선언 축)로 그 사이에 닫았다. 인계문이 「45건」
이라 말할 때 그 값은 **이미 0** 이었다 — 이 레포가 이름 붙인
[수치 주장은 재고 나서 손대기까지 사이에 낡는다] 의 재현이다.

인계문이 명시한 **두 구멍**도 코드에서는 닫혀 있었다. 주입으로 확인했다(§Verification).

## What — 그런데 코드가 움직인 자리의 «산문»은 안 움직였다

### ① 한 파일 «안»의 모순 — 그리고 낡는 쪽이 정해져 있다

`mypy.ini` 는 `[mypy]` 절을 사이에 두고 두 서술을 갖는다. 아래쪽만 다시 쓰였다:

| 줄 | 문장 | 오늘 |
|---|---|---|
| `:10` | *"저장소 전체는 아직 strict 가 아니다. **아래 층만** 강제한다"* | 🔴 거짓 |
| `:25` | *"**application 나머지는 아직이다**"* | 🔴 거짓(0건) |
| `:48` | 표제 *"범위 **밖**으로 남는 것"* + 첫 항목 「최상위 75모듈」 | 🔴 오늘은 범위 «안» |
| `:77` | *"**다음** 웨이브는 그 183건이고"* | 🔴 그 웨이브가 `#159` |
| `:85` | *"즉 **오늘 참인 문장**은 … 두 축이 아직 다르다"* | 🔴 두 축은 같다 |
| `:111` | *"네 층 절이 여기서 **한 절로 접힌다**"* | ✅ 참 |

`:85` 와 `:111` 은 **여덟 줄** 떨어져 있고 서로를 반증한다.

⚠️ **이 갈라짐에는 방향이 있다.** 설정을 고치는 사람은 «절 옆»을 고친다. 그러므로
같은 정책이 두 곳에 적히면 **언제나 위쪽(=머리말)이 낡는다.** 「같이 고쳐라」는
부탁은 답이 아니다 — 답은 «오늘»을 한 곳에서만 말하고, 나머지는 시제를 과거로
못박는 것이다. 그렇게 고쳤다.

### ② 자매 형태 — **전제가 죽었는데 결론은 참**

`tests/test_undefined_name_conformance.py:17` 이 이렇게 적었다:

> *"`mypy.ini` 는 범위를 `fcc_test_platform.domain.*` 로 한정하므로 `tests/` 도
> `apps/web/scripts/` 도 사정권 밖이고"*

전제는 죽었다(범위는 전량이 됐다). **결론은 여전히 참이다** — 그 두 디렉터리는
애초에 `-p fcc_test_platform` 아래에 있지 않으므로 범위가 얼마나 넓어지든 밖이다.

⚠️ **이 형태가 가장 오래 산다.** 결론이 맞으니 아무도 전제를 다시 읽지 않고, 어떤
검사도 빨개지지 않는다. 그리고 다음 사람은 그 «전제»를 근거로 다른 판단을 한다.
정정은 문장을 뒤집는 것이 아니라 **사유를 안 낡는 축으로 옮기는 것**이었다 —
「범위의 크기」가 아니라 「트리의 위치」로.

### ③ 인계문이 지목한 곳은 셋, 실제로는 여섯

[하루에 열한 곳이 거짓이 됐다] 와 같은 결론이 **다른 사실 위에서 또** 나왔다.
찾는 법도 같다: 파일이 아니라 **문장**으로 grep 하고, 형제 레포도 돌린다.

    지목받은 곳   mypy.ini · api/platform_routes.py · api_composition.py
    실제로        + tests/test_undefined_name_conformance.py
                  + tests/test_operation_table_read_form_axis.py  (없는 절 이름을 가리킴)
                  + docs/architecture/…설계서.md §S1               (dated 정정 부착)

⚠️ **첫 훑기는 틀린 답을 냈다.** 공유 체크아웃(`/home/kmjkds/fcc-delivery-final/
fcc-test-platform`)의 `main` 이 `b3c619e` 로 **낡아** `origin/main@0b8461d` 에서
이미 고쳐진 자리를 미수정으로 보였다. 훑기는 «내 워크트리»에서 다시 했다.

## How — 그리고 왜 새 봉인을 «안» 붙였는가

정정만 했고 동작은 안 바꿨다. 그것을 **기계로 증명**했다:

* `mypy.ini` — `configparser` 로 파싱한 설정이 전후 **동일**
* 변경된 `.py` 넷 — docstring 을 제거한 AST 가 전후 **동일**

즉 「이번엔 같았다」가 아니라 **「다를 수 없다」**다. 대조군 실행보다 강하다 — 대조군은
발생률을 말할 뿐이다. 그 논증 밖에 있는 것은 «자기 소스 텍스트를 읽는 검사» 하나뿐이라,
그것만 따로 찾아 돌렸다.

### 봉인을 안 붙인 이유 — 기제가 «구성상» 사라졌다

`_PrincipalResolver` 가 검사 밖이던 진짜 이유는 호출 축이 아니라 **선언 축**이었다:
`create_router` 가 선언 없는 `def` 라 mypy 가 본문을 통째로 건너뛰었다.

⚠️ **호출만 고쳤다면 게이트는 그 모듈을 성실히 «검사하고» 이 자리를 못 본 채 초록을
냈을 것이다** — 이 레포가 이름 붙인 *공허 통과의 둘째 종류(집합이 비는 것이 아니라
집합이 «틀린» 것)*.

`[mypy-fcc_test_platform.*] disallow_untyped_defs = True` 가 그 기제를 없앴다.
선언 없는 `def` **자체가** red 다. 그러니 여기 새로 붙일 봉인은 없고, 되돌리려면
누군가 게이트 범위를 좁혀야 하는데 그 자리는 `TestTheStrictScopeCoversThePackage`
가 이미 지킨다. **봉인을 «안 더한 것»도 판정이므로 여기 적는다.**

## Verification

| 축 | 값 |
|---|---|
| `mypy -p fcc_test_platform` | `Success` · 215 source files — 전후 동일 |
| `test_architecture_gate_conformance.py` | 18 passed · 9 subtests — 전후 동일 |
| `lane_check.py` 전량 | 3380 passed · 859 subtests · 선언된 실패 0 (§리그 함정 뒤) |
| `mypy.ini` 설정 동일성 | `configparser` 파싱 결과 전후 동일 |
| 변경 `.py` ×4 실행코드 동일성 | docstring 제거 AST 전후 동일 |

### 주입 — 인계문이 「게이트가 없다」고 적은 축들이 이제 red 다

| 주입 | 기대 | 관측 |
|---|---|---|
| A. `progress_broadcaster: ChamberProgressBroadcastPort` → `object` | dispose 축 red | `"object" has no attribute "dispose"  [attr-defined]` |
| B. `principal_resolver=object()` | Protocol 적합성 red | `[arg-type] … expected "_PrincipalResolver \| None"` |
| C. `api_adapter: PlatformApiAdapter` → `object` | 조립 루트 red | `[arg-type]` ×2 + `[attr-defined]` ×1 |

복원 후 소스 바이트 동일(`__pycache__` 제거 포함).

⚠️ **주입 A 가 처음엔 «엉뚱한» 필드에 갔다.** `api_adapter` 를 되돌렸더니 red 는 났는데
`dispose` 오류가 «아니었다» — 그 축을 지고 있는 것은 이웃 필드 `progress_broadcaster`
였다. 두 필드를 한 문단에서 설명하는 주석이 그것을 섞어 보이게 했다. 주입을 안 했으면
C 의 red 를 보고 「dispose 축 확인」이라 잘못 적었을 것이다 — **「주입이 착지했는가」와
「주입이 «내가 묻는 것»을 red 로 만들었는가」는 다른 질문이다.**

## 후속

### 🔴 리그 함정 — 다음 사람이 반드시 밟는다

첫 전량 실행이 red 3건을 냈고 **편집과 무관했다**:

    tests/test_undefined_name_conformance.py::TestNoUndefinedNames::test_no_python_file_reads_an_undefined_name
    tests/test_undefined_name_conformance.py::TestTheCorpusIsReadable::test_the_star_import_blind_spot_has_not_grown
    tests/test_silent_defaults_conformance.py::TestNoSilentDefaults::test_no_file_relies_on_a_silent_default

원인은 `tests/test_undefined_name_conformance.py:76` 의 제외 목록이 디렉터리
**«이름»의 정확 일치**라는 것이다:

    _EXCLUDED_DIR_PARTS = frozenset({'.git', '.venv', 'venv', 'node_modules',
                                     'build', 'dist', '__pycache__', …})

venv 를 트리 «안»에 `.venv-ci` 라는 이름으로 만들면 그 안의 서드파티 소스가 「이
저장소의 파이썬 파일」로 세어진다. `.venv` 였으면 조용했다. venv 를 트리 밖으로 옮기니
셋 다 초록(21 passed). **CI 는 트리 안에 venv 를 두지 않으므로 이 축을 영영 안 겪는다** —
[로컬 red 는 「트리 × 설치본」이다] 의 또 한 사례.

→ 미해결 판정: 그 집합을 이름 열거 대신 **`pyvenv.cfg` 가 있는 디렉터리**로 파생하면
구성상 안 새지만, 그것은 «동작 변경»이라 이 판(산문 전용)에 넣지 않았다. 소유 웨이브 미정.

### 손대지 않은 것

* `.claude/evaluations/*` · `work-claims/*` · `docs/education/*` — 「그때의 관측」이라
  고치지 않는다. 낡은 것이 아니라 **날짜가 붙은 실측**이다.
* `intent/top-level-modules-in-the-type-gate/` 삼종세트 — 그 웨이브의 산출물이다.
* 설계서 §9(`platform_routes.py` 분해 여부) — 여전히 걸린 작업이 없는 순수 설계 질문.
