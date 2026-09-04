# 테스트 의존성 선언 축 — PyYAML 미선언이 계약 가드 21건을 껐다 (2026-09-05)

## Why

`main` 의 lane-check CI 가 빨갛다. 표면 증상은 `ModuleNotFoundError: No module named
'yaml'` 이고, 손쉬운 처방은 그 한 곳에 `try/except → skipTest` 를 다는 것이다.

**그 처방이 정확히 틀린 이유가 이 작업의 전부다.** 실측(2026-09-05, CI 와 동일한
`pip install '.[test]'` 환경):

| | PyYAML 있음 | PyYAML 없음 |
|---|---|---|
| `test_central_docker_compose.py` + `test_auth_mode_pairing.py` + `test_platform_ops_health_probe.py` | **166 passed / 0 skipped** | 24 failed / 126 passed / **21 skipped** |
| `scripts/lane_check.py` | 선언 0 == 관측 0 ✅ | 선언 0 / 관측 **24** ❌ |

⚠️ **24건의 빨강보다 21건의 skip 이 더 나빴다.** 그 21건은 이미
`try/except → skipTest` 로 감싸여 있던 것들이고, 접힌 대상이 하필

* 서비스 census (`test_compose_defines_exactly_the_central_services`)
* healthcheck 배선 (`TestComposeHealthcheckWiring`)
* 빌드 대상 파생 (`test_build_targets_derive_from_the_real_compose_file`)
* auth-mode pairing 전량 (`TestTheOperatorPreflight` 15건)

이었다. **계약이 돌지 않는데 CI 는 초록으로 보고했다.** 무방비였던 한 곳
(`_load_compose()`)이 그 침묵을 빨강으로 터뜨린 것이 유일하게 다행한 부분이다.

## What

1. `PyYAML>=6.0` 을 `pyproject.toml` 의 `[test]` 에 **선언**했다. 선택 의존성이 아니라
   실제 의존성이다 — compose 가 읽는 것이 YAML 이므로 그것을 YAML 로 읽는 것이 유일하게
   옳은 판정이고, 정규식으로 읽던 시절의 실패가 해당 파일 docstring 에 남아 있다.
2. `try/except → skipTest` 가드 **4곳을 걷어냈다**(compose 3 + pairing 1). 선언된
   의존성의 부재는 「이 환경에서는 검사하지 않는다」가 아니라 **환경 결함**이다.
   모듈 최상단 `import yaml` 로 바꿨다.
3. `tests/test_test_dependency_declaration_axis.py` 를 세워 재발을 막았다.

## How

새 축은 **하드코딩하지 않는다.**

* 모듈→배포판 매핑은 `importlib.metadata.packages_distributions()`(표준 라이브러리)가
  답한다. 이름 표를 시험 파일에 적으면 그것이 곧 다음 드리프트의 씨앗이다.
* 선언 집합은 `pyproject.toml` 에서 파생한다(런타임 + 모든 extra), 이름 정규화는
  PEP 503 의 SSOT 인 `packaging.utils.canonicalize_name` 에 위임한다.
* 1차 모듈 판정도 트리에서 파생한다 — `conftest` 가 `scripts/` 와 `tests/` 를
  `sys.path` 에 넣으므로 그 두 곳의 파일명이 근거다. 손 목록 0개.
* 비-공허성 시험을 함께 뒀다. 1차 판정이 넓어져 전부를 걸러내면 이 축은 아무것도
  지키지 않으면서 영원히 초록이 된다.

⚠️ **이 축은 자기 자신의 미선언을 먼저 잡았다.** 처음 작성본이 `packaging` 을 쓰면서
선언하지 않았고, 게이트가 그것을 이름으로 지적했다. 예외를 두지 않고 선언했다 —
「pytest 가 끌어오니 늘 있다」에 기대는 것이 바로 이 축이 막으려는 상태다.

## Verification

```
수정 전  CI 조건(pip install '.[test]')  →  lane_check 선언 0 / 관측 24  ❌  + 21 skip
수정 후  CI 조건(동일)                    →  lane_check 선언 0 / 관측 0   ✅
수정 후  계약 가드 3파일 (CI 조건)         →  166 passed, **skip 0**
수정 후  PyYAML 관련 skip 문자열           →  0건
```

⚠️ 측정 도중 게이트가 **측정자의 오염**도 한 번 잡았다 — 비-editable 설치가 만든
`build/` 가 트리에 남아 테스트가 원본과 사본을 함께 세면서 107건이 빨갛게 나왔다.
`.gitignore` 가 덮으므로 `git status` 는 깨끗하다고 답한다. `rm -rf build *.egg-info`
후 재측정한 값이 위 표다. **낡은 핀의 인터프리터로 잰 값도 한 번 폐기했다**
(`contracts 0.1.14 / kernel 0.3.0` vs main 핀 `v0.1.16 / kernel-v0.4.0`).

## 후속

1. `apps/web/src/api/permissions.ts` 의 docstring 이 `tests/test_rbac_parity.py` 를
   SSOT 봉인으로 가리키는데 **그 파일이 이 상자에 없다**(모노레포 잔여). 프론트 권한
   상수와 백엔드 권한 우주의 set-equality 가 이 레포에서는 강제되지 않는다.
2. `test_plan:author` 는 **의도적으로 defined-but-unassigned** 다
   (`tests/test_platform_rbac_policy.py`: *"draft authoring is headless token-scoped —
   D-2 deferred for platform-membership union"*). 결함이 아니다. 다만 운영상
   **오늘 어떤 로그인 경로도 그 권한을 주지 않으므로** 웹의 테스트플랜 작성 UI 는
   도달 불가다. D-2 를 푸는 것은 아키텍처 결정이라 이 커밋의 범위가 아니다.
   ⚠️ 1 과 2 를 함께 막는 축(「웹이 게이트하는 권한은 어떤 역할이든 획득 가능해야
   하며, 아니라면 사유와 함께 선언돼 있어야 한다」)이 다음 후보다.
