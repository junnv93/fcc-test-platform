"""배송된 상자의 실패 **이름 집합**을 받아 적는 pytest 플러그인.

왜 플러그인인가 — 요약줄(`grep '^FAILED '`)은 이 저장소가 이미 이름 붙인 함정이다:
subtest 실패와 **수집 에러**를 보지 못한다. 수집 에러는 특히 중요하다. 상자의
`test_auth_mode_local_jwt.py` 가 오늘 정확히 그 형태로 넘어지기 때문이다
(`import jwt` 가 `oidc` extra 에 있는데 설치 안내는 `test` extra 를 시킨다).

써드파티 의존 0 — 이 레인은 `dependencies = []` 를 P0 로 선언한다. pytest 의
`-p` 로 실려 붙는다.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

_ENV_OUT = 'FCC_LANE_CHECK_OUT'

_failed: set[str] = set()

#: ⚠️ **수집 개수를 함께 적는다** (2026-09-03).
#:
#: 이 플러그인은 오래 **실패 집합만** 적었다. 그동안은 기준선이 비어 있지 않아서
#: 「0건 관측」이 *「선언됐는데 통과 N개」* 로 나타나 red 였다 — 즉 **비어 있지 않은
#: 기준선이 우연히 비-공허성 팔 노릇을 하고 있었다.**
#:
#: 2026-09-03 에 선언된 부채가 0 이 되면서 그 보호가 사라졌다. 그 순간부터
#: 「전부 통과」와 **「0건 수집」**이 이 축에서 같은 값이 된다 — 상자가 깨져
#: 아무것도 못 모아도 `0/0 일치 ✅` 다.
#:
#: `.claude/rules/check-axis-blindness.md` §서식 1 그대로다:
#: *「실패가 0건인가, **0건 실행**인가?」*
_collected = 0


def pytest_runtest_logreport(report) -> None:
    if report.failed and not getattr(report, 'wasxfail', False):
        _failed.add(report.nodeid)


def pytest_collectreport(report) -> None:
    # 수집 에러는 runtest 리포트를 만들지 않는다. 여기서만 보인다.
    if report.failed:
        _failed.add(report.nodeid)


#: ⚠️ `trylast` — 선택 해제(`-k` · `-m` · 다른 플러그인) **뒤에** 센다.
#: 앞에서 세면 「전부 걸러졌다」가 「다 모았다」와 같은 값이 된다.
#: 실측 2026-09-03: `trylast` 없이 `-k ZZZ` 로 재니 2,934 가 나왔다.
@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items) -> None:
    global _collected
    _collected = len(items)


def pytest_sessionfinish(session, exitstatus) -> None:
    out = os.environ.get(_ENV_OUT)
    if not out:
        return
    # ⚠️ **모양을 바꿨다** — 옛 판(리스트)도 읽히도록 소비 쪽이 둘 다 받는다.
    # 한쪽만 고치면 낡은 체크아웃이 조용히 「0건 수집」을 통과시킨다.
    pathlib.Path(out).write_text(
        json.dumps(
            {'failed': sorted(_failed), 'collected': _collected},
            ensure_ascii=False, indent=1,
        ) + '\n',
        encoding='utf-8',
    )
