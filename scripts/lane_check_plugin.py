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

_ENV_OUT = 'FCC_LANE_CHECK_OUT'

_failed: set[str] = set()


def pytest_runtest_logreport(report) -> None:
    if report.failed and not getattr(report, 'wasxfail', False):
        _failed.add(report.nodeid)


def pytest_collectreport(report) -> None:
    # 수집 에러는 runtest 리포트를 만들지 않는다. 여기서만 보인다.
    if report.failed:
        _failed.add(report.nodeid)


def pytest_sessionfinish(session, exitstatus) -> None:
    out = os.environ.get(_ENV_OUT)
    if not out:
        return
    pathlib.Path(out).write_text(
        json.dumps(sorted(_failed), ensure_ascii=False, indent=1) + '\n',
        encoding='utf-8',
    )
