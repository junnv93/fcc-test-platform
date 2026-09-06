# 정의되지 않은 이름 축 — 죽은 봉인 둘, 살아 있는 결함 하나 (2026-09-06)

## 무엇을 쟀나

`ruff 0.16.6 --select F821` (격리 venv, 어떤 프로젝트 설정도 읽지 않는 `--isolated`)
을 기준 `074d588` 트리에 돌려 **11건**을 얻었다. 세 파일이고, 셋 다 서로 다른
형태의 결함이다. 그리고 셋 다 **이 저장소의 어떤 게이트에도 걸리지 않은 채**
main 에 있었다.

왜 안 걸렸나는 두 문장으로 끝난다:

* `mypy.ini` 는 범위를 `fcc_test_platform.domain.*` 로 한정한다 — `tests/` 도
  `apps/web/scripts/` 도 사정권 밖이다.
* `.importlinter` 는 모듈 간 의존 **방향**만 본다. 이름의 존재는 묻지 않는다.

그리고 **실행은 이것을 잡을 수 없다.** 셋 중 둘은 아무도 부르지 않는 코드에
있었고(부르지 않는 코드의 이름 오류는 영원히 조용하다), 나머지 하나는 큐가
비는 첫 순간에만 닿는 자리에 있었다.

## 실측 1 — 「봉인이 있다」와 「봉인이 아무것도 안 본다」가 같은 값이었다

`tests/test_dependency_audit_workflow.py` 는 **338줄 · 헬퍼 18개 · 테스트 0개**로
도착해 있었다. 네 봉인 섹션의 헤더 주석은 그대로 남고 클래스만 사라졌으며,
`_Token` 정의마저 없었다(F821 8건 전부 여기). 파서를 부르는 순간 `NameError` 인데,
**부르는 곳이 0개라** 모듈은 수집되어 0개를 돌리고 초록이었다.

## 실측 2 — 세 문서가 서로를 가리키고 있었다 (「이사」가 아니라 「소멸」)

| 어디에 적혔나 | 무엇이라 적혔나 |
|---|---|
| 모노레포 `tests/test_dependency_audit_workflow.py` | 두 클래스는 2026-08-31 에 `fcc-test-platform` 으로 **옮겼다** |
| 이 레포의 같은 파일 | 두 클래스는 이 레포로 **오지 못했다** |
| 모노레포 `tests/RETIRED_WITH_THE_FRONTEND.md` §5 | 저쪽 산출물이 미비해 **싣지 않았다** |

셋 다 상대가 갖고 있다고 적었고 **어느 쪽에도 없었다.** 그 RETIRED 문서 자신이
*"이 문서가 사라지면 다음 세션이 «원래 없었다»로 읽는다"* 고 경고한 형태이고,
문서가 사라지지 않았는데도 그 일이 일어났다 — 세 기록이 각자 참인 문장을 적었고
**교차 대조를 아무도 하지 않았기** 때문이다.

원형은 도입 커밋(모노레포 `1b6f6f1e`)에 온전히 있었다. 재작성이 아니라 회수다.

## 실측 3 — 제외 사유가 **만료**돼 있었다

§5 가 적은 제외 사유는 *"`.github` 워크플로/락파일 등 저쪽 미비 산출물을 요구"* 다.
2026-09-06 실측:

| 요구 산출물 | 상태 |
|---|---|
| `.github/workflows/dependency-audit.yml` | 실재 (7,840 B, 모노레포 사본과 **바이트 동일**) |
| `apps/web/package-lock.json` | 실재 (450,584 B) |
| `requirements.txt` · `requirements-central.txt` | 실재 |

그리고 그 워크플로는 죽어 있지 않다 — GitHub Actions 에서 `active` 이고 직전 실행
10건이 전부 `success` 다(트리거: `pull_request`). **봉인 대상이 살아서 돌고 있는데
봉인만 비어 있었다.**

⚠️ 그리고 **아무도 대신 지키지 않았다.** 모노레포의 같은 이름 모듈은
`REPO_ROOT = Path(__file__).resolve().parents[1]` 로 **자기 레포의** 워크플로만 본다.
이 레포의 `continue-on-error` 드리프트를 보는 눈은 그날 0개였다.

## 실측 4 — 반대 방향의 사례: 대상이 **떠난** 게이트

`tests/test_architecture_conformance.py` 의 GodObject 추출 감시 장치(155줄)는 같은
F821 축에 걸렸지만 판정이 **반대**다.

* `_baseline_lookup()` 이 읽는 `TestGodObjectGuard` 가 이 레포에 없다.
* 감시 대상 6개 모듈이 **전부** 이 레포에 없다 — 이 레포에는 `src/` 트리 자체가 없다.
* `.py` 441개 AST 전수 조사: 그 장치의 여섯 이름은 **서로만** 참조하고 바깥
  소비자가 0개다. 닫힌 죽은 고리였다.
* 모노레포에는 `TestGodObjectGuard` 와 `_baseline_lookup` 을 **실제로 부르는**
  검사 3건이 있고 감시 대상 모듈도 그쪽에 있다.

즉 이사는 이미 끝났고 여기 남은 것은 소비자만 잘려 나간 잔해다. **대상이 없는 곳에서
봉인을 되살리는 방법은 없다** — 없는 모듈의 baseline 을 지키는 검사는 정의상 공허하다.
그래서 지웠고, 복원처(모노레포의 온전한 판)를 비석에 이름으로 남겼다.

⚠️ 실측 1·2·3 과 실측 4 는 **같은 F821 신호에서 갈라진 반대 판정**이다. 신호가
같다고 처분이 같지 않다. 가르는 질문은 하나다 — **봉인 대상이 이 레포에 있는가.**

## 실측 5 — 살아 있는 런타임 결함 하나 (절반만 끝난 삭제)

`apps/web/scripts/run-test-plan-generation-worker.py:89` 의 `limits` 는
`time.sleep(limits.poll_interval_seconds)` 로 읽히는데 어디에도 정의가 없다.

원인은 추측이 아니라 **문서에 적혀 있었다**.
`docs/api/headless_contract_extraction_manifest.v1.json` 의 crossing baseline 주석:

> *"The exemption buys exactly one import, because the second (the provider's
> generation limits) was **removed** rather than exempted: env-to-typed-config
> resolution moved into the composition root …"*

2026-08-15 `platform-provider-crossing-closure` 가 import 와 `limits = ...` 대입과
`limits=` 인자를 지웠고(`@ apps` crossing 2 → 0, 의도한 변경), **89줄의 사용처만
남겼다.** 모노레포 히스토리가 그 삭제를 줄 단위로 보여 준다.

이 자리가 위험한 이유는 **도달 조건**이다: `worker.run_once()` 가 `None` 을 돌려주는
첫 순간, 즉 **대기열이 비는 순간**에만 닿는다. 작업이 계속 있으면 영원히 안 터지고
부하가 걷히면 `NameError` 로 죽는다 — 부하 시험이 가장 잡기 어려운 형태다.

그리고 이 러너는 죽은 파일이 아니다: `apps/web/scripts/run-test-plans-live-stack.mjs`
가 자식 프로세스로 띄운다.

**수리 방향의 제약이 둘이었다.**

* import 를 되돌리면 그 웨이브가 닫은 cross-lane crossing 이 그대로 다시 열린다.
  그리고 그 provider 모듈은 이 레포에 아예 없다.
* `WebFullGenerationWorker` 는 limits 를 `self._limits` 로만 들고 공개 접근자가
  없으며, 그 클래스도 이 레포에 없다.

그래서 합성 루트가 읽는 것과 **같은 공개 env 계약**
(`FCC_TEST_PLAN_GENERATION_POLL_INTERVAL_SECONDS`)에서 스칼라 하나를 해소한다.
새 키를 만들지 않은 이유는 같은 값을 두 이름으로 튜닝하게 되기 때문이다.

⚠️ **봉인되지 않는 한계를 명문화했다.** 기본값 `2` 는 provider dataclass 기본값의
사본이고, 그 dataclass 가 이 레포에 없으므로 **여기서 그 일치를 재는 검사를 쓸 수
없다.** 두 레포에 걸친 이 축의 봉인은 소유 웨이브가 정해질 때 붙인다.

## 실측 6 — 게이트: 서드파티 없이 ruff 와 **같은 집합**을 낸다

`tests/test_undefined_name_conformance.py` 를 새로 붙였다. 워크플로 YAML 이 아니라
테스트 파일인 이유: 실질 게이트가 `githooks/pre-push` → `scripts/lane_check.py` →
pytest 이고, `.github/workflows/checks.yml` 도 **같은** `lane_check.py` 를 부른다.
검사를 pytest 에 붙이면 훅과 CI 양쪽에서 동시에 발화하고, 게이트가 둘로 갈라지지 않는다.

ruff 를 부르지 않은 이유: 선언된 의존성이 아니고, 넣으려면 `pyproject.toml` 을
고쳐야 하는데 그 파일은 `.extraction-layout.json` 이 예약한 배송 경로다. 도구 부재 시
`skipTest` 로 도망가면 **초록으로 보이는 무검증**이 된다. 그래서 형제 모듈이 PyYAML
대신 stdlib 미니 파서를 쓴 것과 같은 선택을 했다 — `ast` 만 쓴다.

**동등성은 주장이 아니라 실측이다:**

| | ruff 0.16.6 `--select F821` | 이 검사기 |
|---|---|---|
| 기준 `074d588` | **11건** | **11건** (같은 파일 · 같은 줄 · 같은 이름) |
| 처분 후 | 0건 | 0건 |

설계는 의도적으로 **확실한 것만** 잡는다: 모듈 안 어디에서든 묶이는 이름을 전부 모아
**그 어디에도 없는** 이름만 보고한다. 스코프 오류(다른 함수의 지역 이름 읽기)는
놓치지만, 거짓 양성이 0이다. 게이트가 무고한 red 를 내면 다음 사람이 그것을 끈다.

## 실측 7 — 판별력 (주입 5건, 전부 red 확인)

「초록만 보지 마라」는 이 웨이브에서 여섯 번 적용됐다.

| 주입 | 어디에 | 결과 |
|---|---|---|
| 감사 step 의 `continue-on-error` → `false` | 워크플로 | 1 failed |
| `pull_request.paths` 의 manifest 이름 부패 | 워크플로 55줄 | 4 failed |
| 실재 파일에 정의되지 않은 이름 | `scripts/lane_check.py` | 1 failed (파일·줄·이름 정확) |
| 새 `from os.path import *` | 〃 | 1 failed (사각지대 래칫) |
| 문법 오류 | 〃 | 3 failed (파싱 봉인) |

⚠️ **그리고 첫 주입은 공허했다.** paths 부패 주입의 1차 시도가 `18 passed` 를 냈다 —
`requirements*.txt` 라는 **같은 문자열이 헤더 주석에도 있어** 거기 착지했기 때문이다.
「문자열이 파일에 들어갔다」를 착지 확인으로 삼은 것이 잘못이었고, 55줄을 좌표로 다시
쟀다. **공허한 주입의 출력은 이빨 있는 봉인의 출력과 구별되지 않는다.**

## 실측 8 — 수 회계 (전후, 같은 rig)

같은 venv(선언대로 설치: contracts v0.1.22 · kernel v0.5.0 · PyYAML 6.0.3),
같은 트리, `QT_QPA_PLATFORM=offscreen`.

| | 처분 전 | 처분 후 |
|---|---:|---:|
| `lane_check` exit | 0 | 0 |
| passed | 3,110 | **3,138** (+28) |
| skipped | **27** | **27** (변화 없음) |
| subtests | 828 | 828 |
| ruff F821 | 11 | **0** |
| 선언된 실패 / 관측된 실패 | 0 / 0 | 0 / 0 |

+28 = 되살린 봉인 18 + 새 게이트 10. **skipped 가 27 에서 움직이지 않은 것이
`skipTest` 로 도망가지 않았다는 증거다** — 새 검사가 `skipping` 으로 초록을
만들었다면 이 줄이 올라갔을 것이다.

경량 CI 레인(`-m "invariant and not hardware and not gui and not bench"`) 수집:
두 파일 28개 전부 편입 — 파일명의 `conformance` 토큰이 `tests/conftest.py` 의
자동 부착을 태운다.

## 부수 관측 (이 웨이브 범위 밖, 처분하지 않음)

`ast.parse` 에 파일명을 넘기도록 고치자 `SyntaxWarning: invalid escape sequence` 4건이
`<unknown>` 대신 이름을 갖게 됐다 — 전부 `tests/test_frontend_architecture_conformance.py`
(237 · 3545 · 3575 · 7513줄)의 docstring 안 미이스케이프 정규식이다. 실제 결함이지만
이 축이 아니고, 그 파일은 다른 세션의 작업 영역과 겹칠 수 있어 손대지 않았다.

## 근거

* 기준 커밋 `074d588` (PR #82 병합 후 — S1·S2 실행 축이 CI 에서 실제로 도는 상태).
* ruff 0.16.6, 격리 venv, `--isolated --no-cache`.
* 전용 워크트리 `fix/f821-dead-seals-20260906`, base `origin/main`.
* 원형 회수: 모노레포 `git show 1b6f6f1e:tests/test_dependency_audit_workflow.py`.
* 워크플로 실행 이력: `gh run list --workflow=dependency-audit.yml`.
