#!/usr/bin/env python3
"""Process entry point that composes and drives the full-generation worker.

Composition, and nothing else: resolve the database path, call the composition
root, drive the loop until a signal arrives. It is listed in
``governance.composition_roots`` for the same reason its sibling
``src/*_composition.py`` modules are — wiring across lanes is its entire job,
and this file lives in the platform lane's ``apps/web`` tree while the worker it
starts is provider code (2026-08-15, platform-provider-crossing-closure).

The exemption is not a general escape hatch: it buys exactly the one import
below. Environment resolution moved *into* the composition root in the same
change, so this file no longer names a provider module for its configuration
either — the composition root owns env-to-typed-config, as it does everywhere
else in this repository.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


_stop_requested = False


def _request_stop(signum: int, _frame: object) -> None:
    global _stop_requested
    _stop_requested = True
    print(
        f'[test-plan-generation-worker] stop requested by signal {signum}',
        flush=True,
    )


def _required_headless_db_path() -> Path:
    raw_path = os.environ.get('FCC_HEADLESS_DB_PATH', '').strip()
    if not raw_path:
        raise RuntimeError(
            'FCC_HEADLESS_DB_PATH is required for the live generation worker; '
            'use the same explicitly seeded SQLite path as the headless API',
        )
    db_path = Path(raw_path)
    if not db_path.is_file():
        raise RuntimeError(
            f'FCC_HEADLESS_DB_PATH must point to an existing seeded SQLite database: '
            f'{raw_path}',
        )
    return db_path


# ⚠️ 2026-09-06 — 아래 상수와 헬퍼는 **결함 수리**다, 새 설계가 아니다.
#
# 2026-08-15 `platform-provider-crossing-closure` 는 이 파일에서
#   from application.services.test_plan_generation.web_full_generation.limits import (
#       TestPlanGenerationLimits,
#   )
#   limits = TestPlanGenerationLimits.from_env()
# 를 지우고 `create_test_plan_generation_worker(limits=...)` 인자도 없앴다. 그것은
# 의도한 변경이고 위 docstring 이 사유를 적는다 (`@ apps` crossing 2 → 0).
#
# **그런데 아래 루프의 `time.sleep(limits.poll_interval_seconds)` 를 함께 고치지
# 않았다.** 그래서 이 파일은 그날부터 `limits` 를 어디에서도 정의하지 않은 채
# 읽고 있었다 — 그리고 그 자리는 `worker.run_once()` 가 `None` 을 돌려주는 **첫
# 유휴 순간**, 즉 대기열이 비는 순간에만 닿는다. 작업이 계속 있으면 영원히 안
# 터지고, 부하가 걷히면 `NameError` 로 죽는다. `ruff --select F821` 이 이것을
# 이름으로 잡았다(2026-09-06).
#
# ── 왜 import 를 되돌리지 않는가 ────────────────────────────────────────────
# 되돌리면 그 웨이브가 닫은 cross-lane crossing 이 그대로 다시 열린다
# (`docs/api/headless_contract_extraction_manifest.v1.json` 이 `@ apps 2 -> 0` 을
# 기록하고, 위 docstring 은 *"exemption 이 사는 import 는 정확히 아래 하나"* 라고
# 적는다). 그리고 그 provider 모듈은 **이 레포에 아예 없다**.
#
# ── 왜 새 env 키를 만들지 않는가 ────────────────────────────────────────────
# 합성 루트의 docstring 이 *"the same FCC_TEST_PLAN_GENERATION_* set this call used
# to read here"* 라고 적는다. 즉 이 키는 이미 **공개된 환경 계약**이고, 그것을 읽는
# 것은 provider 내부를 들여다보는 것이 아니다. 새 키를 만들면 같은 값을 두 이름으로
# 튜닝하게 된다.
#
# ⚠️ **봉인되지 않는 한계 (명문화).** 아래 기본값 2 는 provider 의
# `TestPlanGenerationLimits.poll_interval_seconds` 기본값을 비추는 사본이고, 그
# dataclass 는 이 레포에 없으므로 **여기서 그 일치를 재는 검사를 쓸 수 없다.**
# provider 가 기본값을 바꾸면 이 러너의 유휴 주기만 옛 값에 남는다 — 잃는 것은
# 폴링 간격 하나이고, 두 레포에 걸친 이 축의 봉인은 소유 웨이브가 정해질 때 붙인다.
_POLL_INTERVAL_ENV = 'FCC_TEST_PLAN_GENERATION_POLL_INTERVAL_SECONDS'
_DEFAULT_POLL_INTERVAL_SECONDS = 2


def _poll_interval_seconds() -> int:
    """유휴 폴링 주기 — 합성 루트가 읽는 것과 **같은** 환경 키에서 해소한다."""
    raw = os.environ.get(_POLL_INTERVAL_ENV)
    if raw is None:
        return _DEFAULT_POLL_INTERVAL_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f'{_POLL_INTERVAL_ENV} must be an integer') from exc
    if value <= 0:
        raise RuntimeError(f'{_POLL_INTERVAL_ENV} must be a positive integer')
    return value


def main() -> int:
    db_path = _required_headless_db_path()
    # 잘못된 값이면 작업을 시작하기 전에 죽는다 (설정 결함을 유휴 순간까지 숨기지 않는다).
    poll_interval = _poll_interval_seconds()
    from test_plan_generation_worker_composition import (
        create_test_plan_generation_worker,
    )

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    worker_id = os.environ.get('FCC_TEST_PLAN_GENERATION_WORKER_ID', '').strip()
    if not worker_id:
        worker_id = f'live-test-plan-generation-worker-{os.getpid()}'
    # limits omitted on purpose: the composition root resolves them from the
    # environment, which is the same FCC_TEST_PLAN_GENERATION_* set this call
    # used to read here.
    # ⚠️ 그 위임은 worker 의 limits 에만 유효하다 — 이 루프의 유휴 주기는 위
    #    `_poll_interval_seconds()` 가 같은 env 계약에서 따로 해소한다(수리 사유는
    #    그 헬퍼 위 주석).
    worker = create_test_plan_generation_worker(
        db_path=db_path,
        worker_id=worker_id,
    )
    recovered = worker.recover_expired()
    print(
        f'[test-plan-generation-worker] db={db_path} worker_id={worker_id} '
        f'recovered={recovered}',
        flush=True,
    )

    while not _stop_requested:
        completed = worker.run_once()
        if completed is None:
            time.sleep(poll_interval)
            continue
        print(
            f'[test-plan-generation-worker] job={completed.job_id} '
            f'status={completed.status.value}',
            flush=True,
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
