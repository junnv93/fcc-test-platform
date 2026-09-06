# 배포판이 「없는 파일을 실행하라」고 말하던 마지막 자리들 — 기준선을 비운다

*2026-09-06 · `fix/the-box-stops-naming-scripts-paths-20260906` · base `origin/main@5893d7e`*

## Why

`tests/test_the_package_never_tells_anyone_to_run_scripts.py` 는 2026-09-06 오전에
**ratchet 으로** 섰다. 예외 셋을 이름으로 들고 있었고, 그 주석이 후속을 이렇게 적었다 —
「마지막 항목이 빠지는 순간 봉인이 자동으로 전면 적용된다」. 이 커밋이 그 후속이다.

남아 있던 이유는 하나였다: `cutover_workflow_hints.suggested_command` 를 옮기려면
**이 레인의 시험 계약 하나가 통째로 바뀐다.** `command[0] == 'python'` 이고 `command[1]` 이
실재 파일이라는 계약을 갈라야 했고, 그것은 값 치환이 아니라 웨이브였다.

실측(설치된 v0.1.11): 배포판 안의 `scripts/*.py` 리터럴 32종 · 그중 **상자 안에 실재하는
것 0/32**. 이 레인 «안»에서는 껍데기가 남아 있어 존재 판정이 초록이었다 — 소비 레인이
설치본으로 그 값을 단언하고 나서야 드러난 부류다.

## What

### 갈래는 실행 «기계»이고, 그 분류는 이미 있었다

`EVIDENCE_RUNS_ON` 이 중앙 10 · 챔버 4 로 이미 갈라 두고 있었다. 그리고 위반 10건이
**중앙 10개와 정확히 1:1** 이었다 — 즉 새 분류를 발명할 필요가 없었다.

    중앙 PC 단계 10  →  `[project.scripts]` 에 «선언된» 콘솔 명령 이름
    챔버 PC 단계 4   →  provider 저장소의 «경로» (그쪽 소유라 경로가 맞는 값)

이름 10개는 지어내지 않고 `[project.scripts]` 와 대조해 확인했다. 이름 규칙이
`scripts/platform_X.py` → `fcc-platform-<X>` 로 정확히 성립한다.

⚠️ 방향이 **양쪽**이라는 점이 요지다. 챔버 단계를 콘솔 명령으로 바꾸면 그것도 결함이다 —
운영자에게 **없는 명령**을 치라고 말하게 된다. 그래서 두 방향을 각각 붙잡는다.

### 예외 목록의 분류 하나가 틀려 있었다

예외 목록은 셋을 「두 docstring 의 운영자 절차 — 값이 아니라 산문」으로 묶어 두었다.
⚠️ **한 파일이 두 종을 다 갖고 있었다** (작성 세션 `-5c` 와 상호 실측):

    central_db_live_proof_cli.py:49    docstring 산문        ← 분류 맞음
    central_db_live_proof_cli.py:520   invocation 리스트     ← 분류 **틀림**
                                       `evidence['command'] = shlex.join(invocation)`
    check_auth_mode_pairing_cli.py:17,20,23   docstring 예시  ← 셋 다 산문, 분류 맞음

즉 「두 docstring」이라는 낱말 자체가 거짓이었다 — 첫 hit 만 보고 파일 «전체»를 산문으로
라벨한 것이다. 그리고 봉인이 **파일 단위**로 예외를 잡으므로 그 오분류는 아무것도 빨갛게
만들지 않았다. **틀린 채로 초록**이었다.

⚠️ 한 겹 더 있다 — **예외 목록의 «사유»는 아무도 검사하지 않는다.** 목록에 이름이 있으면
봉인은 통과시키고 그 옆 주석이 참인지는 묻지 않는다. 목록을 비우며 하나씩 열어 본 것이
그 사유를 처음으로 검사한 셈이다. (같은 계급: 자가감사 17행 중 15행.)

(실행 자체는 안 바꿨다 — `actual_command` 는 이 레인 안에서 트리 산출물을 찾아 돌린다.
바뀐 것은 «영수증에 적히는 재현 지시»뿐이고, 그것을 받는 쪽이 설치본이다.)

### 소비자 셋을 함께 옮겼다

`test_platform_cutover_catalog.py` · `test_platform_cutover_live_workflow.py` ·
`test_platform_cutover_completion_audit.py`, 그리고 체크인된 예시
`docs/examples/cutover_live_workflow.example.json`. 예시는 손으로 고치지 않고
**생성기에서 파생**해 다시 썼다(10/14 단계 변경, 순증감 정확히 −10줄 = 중앙 10개가
두 줄에서 한 줄로).

시험 쪽 「명령 이름 목록」은 **적어 두지 않는다** — `_declared_console_commands()` 가
`[project.scripts]` 를 읽는다. 진입점을 개명하면 그 자리에서 빨개진다. 템플릿 시험에서
열넷을 손으로 나열하던 자리도 분류표 순회로 바꿨다(새 단계가 들어와도 조용하지 않게).

## Verification

| 재주입한 위반 | 착지 확인 | 결과 |
|---|---|---|
| 중앙 힌트 하나를 `python` + 경로로 되돌린다 | 패턴 1건 | **5 failed** |
| 챔버 힌트를 콘솔 명령으로 바꾼다(반대 방향) | 패턴 1건 | **4 failed** |
| 선언에 없는 명령 이름(개명·오타) | 패턴 1건 | **4 failed** |
| 복구 | 바이트 동일(`diff -q`) | **43 passed** (기준선과 같은 수) |

봉인 자체: 예외 목록을 비운 뒤 `2 passed`. 비공허성은 기존
`test_the_detector_would_see_a_violation` 과 `scanned > 0` · 루트별 비지 않음이 함께 선다.

전량 회귀: `pytest tests/ --continue-on-collection-errors`, 양쪽 `__pycache__` 를 지워
캐시 상태를 맞춘 뒤 실패 **이름 집합**을 `comm` 으로 비교 —
**브랜치에만 있는 빨강 0건**, base 에만 있는 빨강 0건. (선재 46 failed · 27 errors 는
base 와 동일. 첫 판에서는 브랜치에만 있는 빨강이 **1건** 나왔고 그것이 내가 놓친
소비자였다 — 영향 파일만 돌렸으면 못 봤다.)

## pre-push 우회를 왜 했는지 (훅이 요구하는 기록)

`FCC_SKIP_LANE_CHECK=1` 로 밀었다. 근거는 «내가 안 깼다»가 아니라 **측정**이다:

    wt-boxbase (origin/main)   lane_check 실패 72개
    wt-box     (이 브랜치)      lane_check 실패 72개
    comm -13 / comm -23        **둘 다 공집합** — 이름 집합까지 동일
    gh run list --branch main  최근 5건 전부 success

즉 이 72는 이 기계 `.venv` 의 산물이지 트리의 상태가 아니다(또 「트리 × 설치본」축이다 —
같은 커밋이 rig 에 따라 다른 수를 낸다). 서버측 CI 가 이 PR 에서 진짜 판정을 낸다.
⚠️ 공유 `.venv` 는 살아 있는 다른 세션들이 쓰는 물건이라 **고치지 않았다** — 재설치하면
그쪽 측정을 깨뜨린다. 그 불일치 자체는 별도 항목이다.

## 후속 — 고치지 «않은» 것과 그 이유

⚠️ **이 봉인의 소유 판정에 구멍이 있다.** `_platform_owned_stems()` 는
`[project.scripts]` 에서 역산하므로, 진입점이 없거나 껍데기 이름이 모듈 이름과 다른
상자 도구를 **못 본다.** 실측 — 봉인이 「소유 아님」으로 놓아준 7종 중 둘이 실은
이 레인의 `scripts/` 에 실재한다:

    scripts/platform_migration_deploy_class.py     진입점 없음         (184줄, 알맹이)
    scripts/check_central_migration_readiness.py   모듈명이 다름       (22줄, 얇은 껍데기)

더 강한 술어는 **「이 레인의 `scripts/` 에 그 파일이 실재하는가」**다 — provider 소유
도구는 여기 없으므로 정확히 갈린다.

그런데 그 술어로 바꾸면 `central_migration_readiness_cli.py:354` 가 즉시 빨개진다:

    result = runner([sys.executable, 'scripts/platform_migration_deploy_class.py'])

이건 힌트가 아니라 **실제 실행**이고, 그 184줄은 휠에 안 실린다. 설치본을 받은 소비자에게는
`result.ok` 가 False 가 되어 `collect_deploy_class` 가 `None` 을 돌려준다 — **크래시가 아니라
조용한 UNKNOWN**. 그 모듈이 자기 docstring 에 「UNKNOWN 을 통과와 같은 코드로 만들지
않는다」고 적어 둔 바로 그 축이, 상자 밖에서는 언제나 UNKNOWN 이 된다.

**술어를 넓히지 않았다.** 넓히면 내가 안 고칠 결함으로 봉인이 즉시 빨개지고, 그러면 방금
비운 예외 목록을 다시 채우게 된다 — 「새 봉인이 드러낸 오늘의 갈라짐을 다 고치려다
봉인을 끄는」 형태다. 그 알맹이를 패키지로 옮기는 웨이브와 **함께** 넓히는 것이 맞고,
그 파일은 오늘(PR #118·#120) 다른 세션이 살아서 만지고 있다. 형제 세션
`fcc-delivery-final-5c` 에 실측을 전달했다.

⚠️ `central_migration_readiness_cli.py:41` 의 `python3 scripts/check_central_migration_readiness.py`
는 「설치 전, 저장소 안에서만 성립」이라고 **명시적으로 한정**돼 있어 위 둘과 다른 판단이
필요하다 — 이 커밋은 그것도 건드리지 않았다.

⚠️ **실제 실행으로는 확인하지 않았다.** 이 커밋이 보장하는 것은 「배포판이 내놓는 문자열이
설치본에서 실행 가능한 이름인가」까지다. `fcc-platform-*` 명령들이 그 인자로 실제로
도는지는 별도 축이고, 그 확인에는 중앙 PC 와 DSN 이 필요하다(승인 지점).
