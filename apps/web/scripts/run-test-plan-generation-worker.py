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


def main() -> int:
    db_path = _required_headless_db_path()
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
            time.sleep(limits.poll_interval_seconds)
            continue
        print(
            f'[test-plan-generation-worker] job={completed.job_id} '
            f'status={completed.status.value}',
            flush=True,
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
