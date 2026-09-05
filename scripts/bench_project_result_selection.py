"""Benchmark the central cross-session result-selection query contract.

The benchmark is deliberately PostgreSQL-only and refuses to fall back to the
operational Compose database.  A run uses the dedicated
``FCC_CENTRAL_DB_BENCHMARK_URL`` lane (or an explicitly supplied disposable
DSN), seeds one generated project, and removes only the rows generated for
that project unless ``--keep-seed`` is requested.

The seed shape is the contract's bounded performance fixture:

* one project;
* two provider natural keys, each backed by a distinct UUID;
* 16,000 conditions × three completed attempts per provider;
* 100 sessions per provider; and
* a manual selection event for 10% of each provider's conditions.

The measured calls go through ``PostgresCentralResultSelectionAdapter``.  The
benchmark therefore exercises the production natural-key resolver and the
same effective/candidate SQL used by the Web route.  ``--explain`` emits
redacted JSON ``EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`` plans; no DSN or
opaque result payload is written to the receipt.

Usage::

    FCC_CENTRAL_DB_BENCHMARK_URL="$FCC_CENTRAL_DB_BENCHMARK_URL" \
      PYTHONPATH=src:. python scripts/bench_project_result_selection.py \
      --seed --explain --json-output path/to/receipt.json

When the dedicated DSN is unavailable the command exits 2 and emits a
machine-readable ``BLOCKED`` receipt instead of silently passing.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
# ⚠️ **2026-09-03 — 여기 있던 주석이 틀렸다.** *"The sibling benchmark harness
# lives beside this file"* 라고 적혀 있었는데 **이 저장소에는 그런 적이 없다.**
# 모노레포에서는 참이었고 추출(2026-08-30)이 그것을 데려오지 않았다 — 그래서
# `tests/test_project_result_selection_performance.py` 가 배송 이래 수집 오류였고
# 기준선이 그것을 선언된 부채로 지고 있었다.
#
# 지금은 계약 레인이 그 모듈을 **배포한다**(v0.1.12). 형제 파일이 아니라
# 설치된 배포판에서 온다 — 세 레인이 같은 하나를 쓴다.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / 'src'))

from fcc_test_contracts.common.benchmark_harness import (  # noqa: E402
    LatencyBudget,
    measure_latency_us_robust,
)

try:  # noqa: E402 - the runner is importable both as a package and as a script
    from scripts.cross_session_result_selection_evidence import repository_metadata
except ImportError:  # pragma: no cover - direct script invocation fallback
    from cross_session_result_selection_evidence import repository_metadata  # noqa: E402


# A receipt that binds a cutoff must actually contain a measurement. These are
# the fields a real run always writes; a hand-assembled object that omits any of
# them is not evidence, however confident its `code_cutoff` string looks.
_BOUND_RECEIPT_REQUIRED_FIELDS = (
    'command', 'started_at', 'completed_at', 'fixture', 'seed',
    'samples', 'latency_budgets', 'p95_ratios', 'cleanup',
)
_HEX = frozenset('0123456789abcdef')


def receipt_binds_cutoff(receipt: Mapping[str, Any]) -> bool:
    """Is this receipt usable as evidence *for the SHA it names*?

    Good numbers are not evidence, and neither is a good-looking SHA.

    The first version of this predicate asked only whether a 40-character
    ``code_cutoff`` was present and the tree had been clean and at HEAD. An
    independent reviewer showed that ``{'code_cutoff': 'a'*40, 'repository':
    {'cutoff_matches_head': True, 'clean': True}}`` satisfied it — an object with
    no measurement in it at all — and that the accompanying test *asserted* that
    shape was acceptable. The predicate was checking the label, not the thing.

    So it now asks three questions that a fabricated object cannot answer by
    accident: does the SHA look like a git object id (40 lowercase hex), did the
    run actually succeed and clean up after itself, and does the receipt carry
    the fields only a real measurement produces — the command, both timestamps,
    the fixture and seed, the samples, the budgets, the ratios, and the cleanup
    result. Structure alone is still not proof of honesty; it is the floor below
    which a receipt is certainly not evidence.

    Kept as a predicate rather than a comment so the rule can be executed — by
    the performance test, and by anyone assembling a manifest.
    """
    repository = receipt.get('repository')
    if not isinstance(repository, Mapping):
        return False

    cutoff = receipt.get('code_cutoff')
    if not isinstance(cutoff, str) or len(cutoff) != 40 or not set(cutoff) <= _HEX:
        return False

    if not repository.get('cutoff_matches_head') or not repository.get('clean'):
        return False
    # The tree state must be the runner's own observation, not a bare pair of
    # booleans: a real repository_metadata() call always reports these too.
    if not all(key in repository for key in ('head', 'requested_cutoff', 'status_sha256')):
        return False
    if repository.get('head') != cutoff:
        return False

    # A failed or blocked run measured something, but it is not evidence that the
    # cutoff meets the budget.
    if receipt.get('status') != 'PASS':
        return False
    if not all(receipt.get(field) for field in _BOUND_RECEIPT_REQUIRED_FIELDS):
        return False
    cleanup = receipt.get('cleanup')
    if not isinstance(cleanup, Mapping) or cleanup.get('status') != 'PASS':
        return False

    ratios = receipt.get('p95_ratios')
    if not isinstance(ratios, Mapping) or not ratios:
        return False
    return all(
        isinstance(entry, Mapping) and entry.get('within_ratio') is True
        for entry in ratios.values()
    )


PROJECT_COUNT = 1
PROVIDER_COUNT = 2
CONDITIONS_PER_PROVIDER = 16_000
ATTEMPTS_PER_CONDITION = 3
SESSIONS_PER_PROVIDER = 100
MANUAL_PIN_RATIO = 0.10
EXPECTED_ATTEMPTS = (
    PROJECT_COUNT
    * PROVIDER_COUNT
    * CONDITIONS_PER_PROVIDER
    * ATTEMPTS_PER_CONDITION
)
EXPECTED_MANUAL_PINS = int(
    CONDITIONS_PER_PROVIDER * PROVIDER_COUNT * MANUAL_PIN_RATIO
)
PAGE_LIMIT = 1000
ATTEMPT_PAGE_LIMIT = 100

EFFECTIVE_PAGE_BUDGET = LatencyBudget(
    name='project-result-selection.effective-page',
    metric='p95_us',
    limit_us=250_000.0,
)
ATTEMPT_PAGE_BUDGET = LatencyBudget(
    name='project-result-selection.attempt-page',
    metric='p95_us',
    limit_us=150_000.0,
)
P95_RATIO_LIMIT = 1.5

# Same-host comparison: this is the deterministic latest-result query before
# the append-only selection ledger is joined. It deliberately keeps the same
# bounded condition page and project/provider/session joins as the feature
# query, so the recorded ratio measures the selection-event overhead rather
# than comparing unrelated work. The benchmark still exercises the production
# feature query through ``PostgresCentralResultSelectionAdapter``.
BASELINE_EFFECTIVE_RESULTS_QUERY_SQL = (
    'WITH condition_page AS MATERIALIZED ( '
    'SELECT DISTINCT a."condition_hash" '
    'FROM "measurement_attempts" a '
    'WHERE a."project_id" IS NOT DISTINCT FROM %s '
    'AND a."provider_id" = %s AND a."status" = \'completed\' '
    'ORDER BY a."condition_hash" ASC LIMIT %s '
    '), ranked AS ( '
    'SELECT DISTINCT ON (a."condition_hash") a."id" AS "attempt_id", a."project_id" AS "project_id", '
    'p."provider_id" AS "provider_id", a."condition_hash" AS "condition_hash", '
    'a."session_id" AS "session_id", s."provider_session_id" AS "provider_session_id", '
    's."sample_id" AS "sample_id", s."chamber_id" AS "chamber_id", '
    'a."operator" AS "operator", a."measured_at" AS "measured_at", '
    'a."created_at" AS "created_at", a."verdict" AS "verdict", '
    'a."status" AS "status", a."attempt_number" AS "attempt_number", '
    'a."result_json" AS "result_json", a."provenance_json" AS "provenance_json", '
    'a."test_name" AS "test_name", a."technology" AS "technology", '
    'a."margin" AS "margin", a."run_id" AS "run_id", '
    'a."idempotency_key" AS "idempotency_key", a."recorded_by" AS "recorded_by" '
    'FROM condition_page page '
    'JOIN "measurement_attempts" a ON a."condition_hash" = page."condition_hash" '
    'AND a."project_id" IS NOT DISTINCT FROM %s '
    'AND a."provider_id" = %s AND a."status" = \'completed\' '
    'JOIN "test_sessions" s ON s."id" = a."session_id" '
    'AND s."provider_id" = a."provider_id" '
    'AND s."project_id" IS NOT DISTINCT FROM a."project_id" '
    'JOIN "providers" p ON p."id" = a."provider_id" AND p."enabled" = TRUE '
    'ORDER BY a."condition_hash" ASC, a."measured_at" DESC NULLS LAST, '
    'a."created_at" DESC, a."id" DESC '
    ') SELECT "attempt_id", "project_id", "provider_id", "condition_hash", '
    '"session_id", "provider_session_id", "sample_id", "chamber_id", '
    '"operator", "measured_at", "created_at", "verdict", "status", '
    '"attempt_number", "result_json", "provenance_json", "test_name", '
    '"technology", "margin", "run_id", "idempotency_key", "recorded_by" '
    'FROM ranked ORDER BY "condition_hash" ASC'
)


class BenchmarkBlocked(RuntimeError):
    """The requested live benchmark lane is not safely available."""


@dataclass(frozen=True)
class SeedManifest:
    run_id: str
    project_id: str
    project_code: str
    provider_ids: tuple[str, str]
    provider_uuids: tuple[str, str]
    attempt_count: int
    manual_pin_count: int
    session_count: int
    incomplete_attempt_count: int
    equal_timestamp_attempt_count: int
    null_timestamp_attempt_count: int
    out_of_order_timestamp_attempt_count: int
    replay_case_count: int


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _run_id() -> str:
    return uuid.uuid4().hex[:12]


def _resolve_dsn(explicit_dsn: str | None) -> tuple[str, str]:
    if explicit_dsn:
        return explicit_dsn.strip(), 'explicit --dsn'
    benchmark_dsn = os.environ.get('FCC_CENTRAL_DB_BENCHMARK_URL', '').strip()
    if benchmark_dsn:
        return benchmark_dsn, 'FCC_CENTRAL_DB_BENCHMARK_URL'
    raise BenchmarkBlocked(
        'FCC_CENTRAL_DB_BENCHMARK_URL is not configured; the benchmark lane '
        'requires a separately provisioned disposable PostgreSQL DSN'
    )


def _connect(dsn: str):
    try:
        import psycopg  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        raise BenchmarkBlocked('psycopg is not installed') from exc
    try:
        return psycopg.connect(
            dsn,
            connect_timeout=5,
            application_name='fcc-cross-session-result-selection-benchmark',
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        raise BenchmarkBlocked('benchmark PostgreSQL DSN is unreachable') from exc


def _database_identity(connection) -> dict[str, Any]:
    """Return redacted database/server metadata for the benchmark receipt."""
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT current_database(), '
            "COALESCE(inet_server_addr()::text, '<local-socket>'), "
            'COALESCE(inet_server_port(), 0), version()'
        )
        database, server, port, version = cursor.fetchone()
    return {
        'database_name': str(database),
        'server': str(server),
        'port': int(port),
        'postgres_version': str(version),
    }


def _json_value(value: Mapping[str, Any]):
    from psycopg.types.json import Jsonb  # type: ignore

    return Jsonb(dict(value))


def _uuid(namespace: uuid.UUID, label: str) -> uuid.UUID:
    return uuid.uuid5(namespace, label)


def _seed(connection, *, run_id: str) -> SeedManifest:
    """Insert the complete disposable fixture in one transaction."""
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f'fcc-benchmark:{run_id}')
    project_uuid = _uuid(namespace, 'project')
    project_code = f'BENCH-CROSS-SESSION-{run_id}'
    provider_ids = (f'bench-{run_id}-a', f'bench-{run_id}-b')
    provider_uuids = tuple(_uuid(namespace, f'provider:{key}') for key in provider_ids)
    started = _now()

    with connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO providers '
            '(id, provider_id, product_line, contract_family, contract_version, '
            'base_url, capabilities_json, enabled, created_at, updated_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)',
            (
                provider_uuids[0], provider_ids[0], 'benchmark-a',
                'fcc-benchmark', '1.0.0', 'http://benchmark.invalid/a',
                _json_value({'benchmark': True}), started, started,
            ),
        )
        cursor.execute(
            'INSERT INTO providers '
            '(id, provider_id, product_line, contract_family, contract_version, '
            'base_url, capabilities_json, enabled, created_at, updated_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)',
            (
                provider_uuids[1], provider_ids[1], 'benchmark-b',
                'fcc-benchmark', '1.0.0', 'http://benchmark.invalid/b',
                _json_value({'benchmark': True}), started, started,
            ),
        )
        cursor.execute(
            'INSERT INTO projects '
            '(id, project_code, status, created_at, updated_at) '
            'VALUES (%s, %s, %s, %s, %s)',
            (project_uuid, project_code, 'active', started, started),
        )

        sessions: dict[tuple[int, int], uuid.UUID] = {}
        session_rows: list[tuple[Any, ...]] = []
        for provider_index, provider_uuid in enumerate(provider_uuids):
            for session_index in range(SESSIONS_PER_PROVIDER):
                session_uuid = _uuid(
                    namespace, f'session:{provider_index}:{session_index}'
                )
                sessions[(provider_index, session_index)] = session_uuid
                session_rows.append((
                    session_uuid,
                    provider_uuid,
                    f'{run_id}-provider-{provider_index}-session-{session_index:03d}',
                    f'bench-chamber-{session_index % 2}',
                    project_uuid,
                    'completed',
                    started,
                    started + timedelta(minutes=1),
                    _json_value({'benchmark_run_id': run_id}),
                ))
        cursor.executemany(
            'INSERT INTO test_sessions '
            '(id, provider_id, provider_session_id, chamber_id, project_id, '
            'status, started_at, completed_at, metadata_json) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
            session_rows,
        )

        attempt_rows: list[tuple[Any, ...]] = []
        event_rows: list[tuple[Any, ...]] = []
        attempt_count = 0
        pin_count = 0
        incomplete_attempt_count = 0
        equal_timestamp_attempt_count = 0
        null_timestamp_attempt_count = 0
        out_of_order_timestamp_attempt_count = 0
        replay_case_count = 0
        for provider_index, provider_uuid in enumerate(provider_uuids):
            for condition_index in range(CONDITIONS_PER_PROVIDER):
                condition_hash = f'benchmark-condition-{condition_index:05d}'
                attempt_ids: list[uuid.UUID] = []
                session_uuid = sessions[(provider_index, condition_index % SESSIONS_PER_PROVIDER)]
                for attempt_number in range(1, ATTEMPTS_PER_CONDITION + 1):
                    attempt_uuid = _uuid(
                        namespace,
                        f'attempt:{provider_index}:{condition_index}:{attempt_number}',
                    )
                    attempt_ids.append(attempt_uuid)
                    equal_timestamp = condition_index % 101 == 0
                    null_timestamp = condition_index % 107 == 0 and attempt_number == 1
                    out_of_order_timestamp = condition_index % 103 == 0
                    if null_timestamp:
                        measured_at = None
                        null_timestamp_attempt_count += 1
                    elif equal_timestamp:
                        measured_at = started + timedelta(seconds=condition_index)
                        equal_timestamp_attempt_count += 1
                    elif out_of_order_timestamp:
                        measured_at = started + timedelta(
                            seconds=condition_index * ATTEMPTS_PER_CONDITION
                            + ATTEMPTS_PER_CONDITION - attempt_number,
                        )
                        out_of_order_timestamp_attempt_count += 1
                    else:
                        measured_at = started + timedelta(
                            seconds=condition_index * ATTEMPTS_PER_CONDITION + attempt_number
                        )
                    incomplete = condition_index % 97 == 0 and attempt_number == 1
                    if incomplete:
                        incomplete_attempt_count += 1
                    created_at = started + timedelta(
                        seconds=condition_index * ATTEMPTS_PER_CONDITION + attempt_number,
                        milliseconds=attempt_number,
                    )
                    attempt_rows.append((
                        attempt_uuid,
                        provider_uuid,
                        session_uuid,
                        project_uuid,
                        'benchmark-selection',
                        'BENCH',
                        condition_hash,
                        attempt_number,
                        attempt_number == ATTEMPTS_PER_CONDITION and not incomplete,
                        'benchmark-operator',
                        'running' if incomplete else 'completed',
                        None if incomplete else 'pass',
                        _json_value({
                            'provider': provider_ids[provider_index],
                            'condition_index': condition_index,
                            'attempt_number': attempt_number,
                            'edge_case': {
                                'equal_timestamp': equal_timestamp,
                                'null_timestamp': null_timestamp,
                                'out_of_order_timestamp': out_of_order_timestamp,
                                'incomplete': incomplete,
                            },
                        }),
                        f'benchmark:{run_id}',
                        f'benchmark:{run_id}:{provider_index}:{condition_index}:{attempt_number}',
                        'benchmark-seed',
                        _json_value({'benchmark_run_id': run_id}),
                        measured_at,
                        created_at,
                    ))
                    attempt_count += 1
                if condition_index % 10 == 0:
                    event_id = _uuid(namespace, f'event:{provider_index}:{condition_index}')
                    event_rows.append((
                        event_id,
                        project_uuid,
                        provider_uuid,
                        condition_hash,
                        'selected',
                        attempt_ids[1],
                        1,
                        None,
                        0,
                        'benchmark-examiner',
                        started + timedelta(seconds=condition_index),
                        'benchmark manual pin',
                    ))
                    pin_count += 1
                    if condition_index % 20 == 0:
                        # A second identical selection is an append-only replay
                        # case. It must not create a second effective source or
                        # change the selected attempt; the revision ledger is
                        # still allowed to advance because this is a benchmark
                        # of the production event semantics, not a dedup shortcut.
                        event_rows.append((
                            _uuid(namespace, f'event-replay:{provider_index}:{condition_index}'),
                            project_uuid,
                            provider_uuid,
                            condition_hash,
                            'selected',
                            attempt_ids[1],
                            2,
                            event_id,
                            1,
                            'benchmark-replay',
                            started + timedelta(seconds=condition_index, milliseconds=1),
                            'benchmark selection replay',
                        ))
                        replay_case_count += 1

        cursor.executemany(
            'INSERT INTO measurement_attempts '
            '(id, provider_id, session_id, project_id, test_name, technology, '
            'condition_hash, attempt_number, is_latest, operator, status, verdict, '
            'result_json, run_id, idempotency_key, recorded_by, provenance_json, '
            'measured_at, created_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '
            '%s, %s, %s, %s, %s, %s)',
            attempt_rows,
        )
        cursor.executemany(
            'INSERT INTO project_result_selection_events '
            '(id, project_id, provider_id, condition_hash, action, attempt_id, '
            'revision, predecessor_event_id, expected_revision, actor_subject, '
            'occurred_at, reason) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            event_rows,
        )
        # The disposable lane may have been empty immediately before this
        # fixture. Refresh planner statistics before measuring so PostgreSQL
        # does not choose a stale nested-loop plan that is unrelated to the
        # selection query itself.
        cursor.execute(
            'ANALYZE "providers", "projects", "test_sessions", '
            '"measurement_attempts", "project_result_selection_events"'
        )
    connection.commit()
    return SeedManifest(
        run_id=run_id,
        project_id=str(project_uuid),
        project_code=project_code,
        provider_ids=provider_ids,
        provider_uuids=tuple(str(value) for value in provider_uuids),
        attempt_count=attempt_count,
        manual_pin_count=pin_count,
        session_count=PROVIDER_COUNT * SESSIONS_PER_PROVIDER,
        incomplete_attempt_count=incomplete_attempt_count,
        equal_timestamp_attempt_count=equal_timestamp_attempt_count,
        null_timestamp_attempt_count=null_timestamp_attempt_count,
        out_of_order_timestamp_attempt_count=out_of_order_timestamp_attempt_count,
        replay_case_count=replay_case_count,
    )


def _cleanup(connection, manifest: SeedManifest) -> dict[str, Any]:
    """Remove only rows owned by the generated project and providers."""
    deleted: dict[str, int] = {}
    with connection.cursor() as cursor:
        cursor.execute(
            'DELETE FROM project_result_selection_events WHERE project_id = %s',
            (manifest.project_id,),
        )
        deleted['project_result_selection_events'] = int(cursor.rowcount or 0)
        cursor.execute(
            'DELETE FROM measurement_attempts WHERE project_id = %s',
            (manifest.project_id,),
        )
        deleted['measurement_attempts'] = int(cursor.rowcount or 0)
        cursor.execute(
            'DELETE FROM test_sessions WHERE project_id = %s',
            (manifest.project_id,),
        )
        deleted['test_sessions'] = int(cursor.rowcount or 0)
        cursor.execute('DELETE FROM projects WHERE id = %s', (manifest.project_id,))
        deleted['projects'] = int(cursor.rowcount or 0)
        cursor.executemany(
            'DELETE FROM providers WHERE id = %s',
            [(provider_uuid,) for provider_uuid in manifest.provider_uuids],
        )
        deleted['providers'] = int(cursor.rowcount or 0)
    connection.commit()
    remaining: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table, column, value in (
            ('project_result_selection_events', 'project_id', manifest.project_id),
            ('measurement_attempts', 'project_id', manifest.project_id),
            ('test_sessions', 'project_id', manifest.project_id),
            ('projects', 'id', manifest.project_id),
        ):
            cursor.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = %s', (value,))
            remaining[table] = int(cursor.fetchone()[0])
        cursor.execute(
            'SELECT COUNT(*) FROM providers WHERE id = ANY(%s)',
            (list(manifest.provider_uuids),),
        )
        remaining['providers'] = int(cursor.fetchone()[0])
    return {
        'status': 'PASS' if not any(remaining.values()) else 'FAIL',
        'deleted_rows': deleted,
        'remaining_rows': remaining,
    }


def _run_baseline_effective_page(manifest: SeedManifest, provider_uuid: str) -> None:
    """Run the same-host pre-ledger latest-result baseline once."""
    connection = _connect(_CURRENT_DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                BASELINE_EFFECTIVE_RESULTS_QUERY_SQL,
                (
                    manifest.project_id, provider_uuid, PAGE_LIMIT + 1,
                    manifest.project_id, provider_uuid,
                ),
            )
            cursor.fetchall()
    finally:
        connection.close()


def _measure(manifest: SeedManifest, *, iterations: int, warmup: int, trials: int) -> dict[str, dict[str, float]]:
    from fcc_test_platform.application.central_result_selection_adapter import (
        PostgresCentralResultSelectionAdapter,
    )

    def connection_factory():
        return _connect(_CURRENT_DSN)

    adapter = PostgresCentralResultSelectionAdapter(connection_factory)
    samples: dict[str, dict[str, float]] = {}
    for provider_id in manifest.provider_ids:
        effective = measure_latency_us_robust(
            lambda provider_id=provider_id: adapter.list_effective_results(
                manifest.project_id, provider_id, limit=PAGE_LIMIT,
            ),
            iters=iterations,
            warmup=warmup,
            trials=trials,
        )
        samples[f'effective_page:{provider_id}'] = effective
        provider_index = manifest.provider_ids.index(provider_id)
        baseline = measure_latency_us_robust(
            lambda provider_uuid=manifest.provider_uuids[provider_index]: _run_baseline_effective_page(
                manifest, provider_uuid,
            ),
            iters=iterations,
            warmup=warmup,
            trials=trials,
        )
        samples[f'baseline_effective_page:{provider_id}'] = baseline
    candidate_provider = manifest.provider_ids[0]
    candidate = measure_latency_us_robust(
        lambda: adapter.list_attempts(
            manifest.project_id,
            candidate_provider,
            'benchmark-condition-00000',
            limit=ATTEMPT_PAGE_LIMIT,
        ),
        iters=iterations,
        warmup=warmup,
        trials=trials,
    )
    samples[f'attempt_page:{candidate_provider}'] = candidate
    return samples


def _explain(connection, manifest: SeedManifest) -> dict[str, Any]:
    from fcc_test_platform.application.central_result_selection_adapter import (
        CANDIDATE_ATTEMPTS_QUERY_SQL,
        EFFECTIVE_RESULTS_QUERY_SQL,
    )

    plans: dict[str, Any] = {}
    with connection.cursor() as cursor:
        for provider_id, provider_uuid in zip(
            manifest.provider_ids, manifest.provider_uuids
        ):
            cursor.execute(
                'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '
                + BASELINE_EFFECTIVE_RESULTS_QUERY_SQL,
                (
                    manifest.project_id, provider_uuid, PAGE_LIMIT + 1,
                    manifest.project_id, provider_uuid,
                ),
            )
            plans[f'baseline_effective_page:{provider_id}'] = cursor.fetchone()[0]
            cursor.execute(
                'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '
                + EFFECTIVE_RESULTS_QUERY_SQL,
                (
                    manifest.project_id, provider_uuid, PAGE_LIMIT + 1,
                    manifest.project_id, provider_uuid,
                    manifest.project_id, provider_uuid,
                ),
            )
            plans[f'effective_page:{provider_id}'] = cursor.fetchone()[0]
        cursor.execute(
            'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '
            + CANDIDATE_ATTEMPTS_QUERY_SQL,
            (
                manifest.project_id, manifest.provider_uuids[0],
                'benchmark-condition-00000', ATTEMPT_PAGE_LIMIT + 1,
            ),
        )
        plans[f'attempt_page:{manifest.provider_ids[0]}'] = cursor.fetchone()[0]
    return plans


def _budget_result(samples: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, stats in samples.items():
        budget = (
            ATTEMPT_PAGE_BUDGET
            if name.startswith('attempt_page:')
            else EFFECTIVE_PAGE_BUDGET
        )
        out[name] = {
            'budget': asdict(budget),
            'observed': dict(stats),
            'within_budget': stats[budget.metric] <= budget.limit_us,
        }
    return out


def _p95_ratios(samples: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    """Compare feature p95 with the same-host latest-only baseline p95."""
    out: dict[str, Any] = {}
    for provider_id in (key.split(':', 1)[1] for key in samples if key.startswith('effective_page:')):
        feature_key = f'effective_page:{provider_id}'
        baseline_key = f'baseline_effective_page:{provider_id}'
        feature_p95 = float(samples[feature_key]['p95_us'])
        baseline_p95 = float(samples[baseline_key]['p95_us'])
        ratio = feature_p95 / baseline_p95 if baseline_p95 > 0 else float('inf')
        out[provider_id] = {
            'feature_p95_us': feature_p95,
            'baseline_p95_us': baseline_p95,
            'ratio': ratio,
            'limit': P95_RATIO_LIMIT,
            'within_ratio': ratio <= P95_RATIO_LIMIT,
        }
    return out


def _redacted_command(argv: Sequence[str] | None, *, dsn: str) -> str:
    """The command, verbatim, minus anything that could carry a credential."""
    raw = list(argv) if argv is not None else list(sys.argv[1:])
    redacted: list[str] = []
    redact_next = False
    for item in raw:
        if redact_next:
            redacted.append('<redacted-dsn>')
            redact_next = False
            continue
        if item == '--dsn':
            redacted.append(item)
            redact_next = True
            continue
        redacted.append('<redacted-dsn>' if dsn and item == dsn else item)
    return shlex.join(['python', 'scripts/bench_project_result_selection.py', *redacted])


def _write_receipt(path: str | None, receipt: Mapping[str, Any]) -> None:
    rendered = json.dumps(receipt, indent=2, sort_keys=True, default=str)
    if path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + '\n', encoding='utf-8')
    else:
        print(rendered)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsn', help='explicit disposable DSN; never an operational DSN')
    parser.add_argument(
        '--allow-explicit-dsn', action='store_true',
        help='confirm that --dsn names a disposable benchmark database',
    )
    parser.add_argument('--seed', action='store_true', help='seed the generated fixture')
    parser.add_argument('--keep-seed', action='store_true', help='retain generated rows')
    parser.add_argument('--explain', action='store_true', help='emit JSON EXPLAIN plans')
    parser.add_argument('--iterations', type=int, default=10)
    parser.add_argument('--warmup', type=int, default=2)
    parser.add_argument('--trials', type=int, default=5)
    parser.add_argument('--json-output', help='write a redacted JSON receipt to this path')
    parser.add_argument(
        '--cutoff', default=os.environ.get('FCC_CODE_CUTOFF_SHA', ''),
        help='the immutable code SHA this measurement is evidence for',
    )
    args = parser.parse_args(argv)
    if args.iterations < 1 or args.warmup < 0 or args.trials < 1:
        parser.error('iterations/trials must be positive and warmup cannot be negative')
    if not args.seed:
        raise SystemExit('--seed is required for the disposable benchmark fixture')
    if args.dsn and not args.allow_explicit_dsn:
        parser.error('--dsn requires --allow-explicit-dsn')

    cutoff = args.cutoff.strip()
    started_at = datetime.now(timezone.utc).isoformat()
    receipt: dict[str, Any] = {
        'schema_version': 1,
        'status': 'BLOCKED',
        'code_cutoff': cutoff or 'UNFROZEN',
        'repository': repository_metadata(cutoff or None),
        'command': _redacted_command(argv, dsn=str(args.dsn or '')),
        'started_at': started_at,
        'fixture': {
            'project_count': PROJECT_COUNT,
            'provider_count': PROVIDER_COUNT,
            'conditions_per_provider': CONDITIONS_PER_PROVIDER,
            'attempts_per_condition': ATTEMPTS_PER_CONDITION,
            'expected_attempts': EXPECTED_ATTEMPTS,
            'sessions_per_provider': SESSIONS_PER_PROVIDER,
            'manual_pin_ratio': MANUAL_PIN_RATIO,
            'expected_manual_pins': EXPECTED_MANUAL_PINS,
            'edge_cases': {
                'equal_timestamps': 'seeded deterministically by condition index',
                'null_timestamps': 'seeded deterministically by condition index',
                'out_of_order_timestamps': 'seeded deterministically by condition index',
                'incomplete_attempts': 'seeded as non-completed attempt rows',
                'selection_replays': 'seeded as append-only revision replays',
            },
            'no_offset': True,
        },
    }
    connection = None
    manifest: SeedManifest | None = None
    return_code = 1
    global _CURRENT_DSN
    try:
        _CURRENT_DSN, dsn_source = _resolve_dsn(args.dsn)
        receipt['dsn_source'] = dsn_source
        if args.dsn and not args.keep_seed:
            # An explicit DSN is not self-identifying. Require the caller to
            # opt into the generated-row lifecycle by using --keep-seed only
            # when they intentionally own cleanup; normal explicit-DSN runs
            # still clean their generated project automatically.
            receipt['explicit_dsn_cleanup'] = 'generated project only'
        connection = _connect(_CURRENT_DSN)
        receipt['database'] = _database_identity(connection)
        manifest = _seed(connection, run_id=_run_id())
        receipt['seed'] = asdict(manifest)
        samples = _measure(
            manifest,
            iterations=args.iterations,
            warmup=args.warmup,
            trials=args.trials,
        )
        receipt['samples'] = samples
        receipt['latency_budgets'] = _budget_result(samples)
        receipt['p95_ratios'] = _p95_ratios(samples)
        if args.explain:
            receipt['explain'] = _explain(connection, manifest)
        receipt['status'] = (
            'PASS'
            if (
                all(item['within_budget'] for item in receipt['latency_budgets'].values())
                and all(item['within_ratio'] for item in receipt['p95_ratios'].values())
            )
            else 'FAIL'
        )
        return_code = 0 if receipt['status'] == 'PASS' else 1
    except BenchmarkBlocked as exc:
        receipt['blocked_reason'] = str(exc)
        return_code = 2
    except Exception as exc:  # pragma: no cover - live DB/schema dependent
        receipt['status'] = 'FAIL'
        receipt['error'] = type(exc).__name__
        receipt['error_message'] = str(exc)[:300]
        return_code = 1
    finally:
        if connection is not None and manifest is not None and not args.keep_seed:
            try:
                receipt['cleanup'] = _cleanup(connection, manifest)
            except Exception as exc:  # pragma: no cover - live DB failure
                receipt['cleanup'] = f'FAILED: {type(exc).__name__}'
                return_code = 1
        if connection is not None:
            connection.close()
        receipt['completed_at'] = datetime.now(timezone.utc).isoformat()
        # Recorded, not asserted: the numbers stand on their own, but whether
        # they are evidence *for this cutoff* is a separate question with its own
        # answer in the receipt.
        receipt['binds_cutoff'] = receipt_binds_cutoff(receipt)
        _write_receipt(args.json_output, receipt)
    return return_code


_CURRENT_DSN = ''


if __name__ == '__main__':
    raise SystemExit(main())
