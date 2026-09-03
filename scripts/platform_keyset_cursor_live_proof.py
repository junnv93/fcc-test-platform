"""Repeatable live-PostgreSQL proof for the platform keyset cursor axes
(플랫폼 백엔드 부채 청산 M3, 2026-07-30).

W3 proved the *projects* axis on a live database and then **inferred** that the
claims axis (``ACTIVE_CLAIM_KEYSET = (occurred_at timestamptz, claim_id uuid)``)
and the coverage axis work "for the same reason". Inference is not evidence: the
routine test lane runs these queries on SQLite, where ``claim_id`` is TEXT and a
row-value comparison is a lexicographic string compare — a type-binding defect is
structurally invisible there. This script closes that gap by running the *real*
adapter/service against a real PostgreSQL server and emitting a JSON evidence
bundle.

What it proves, in order:

1. **type inference** — the string cursor values are inferred by the server as
   the column types (``pg_prepared_statements.parameter_types``), the same
   methodology W3 used on the projects axis. psycopg3 sends text parameters as
   ``unknown`` (oid 0) and PostgreSQL resolves them from context; if it did not,
   the query would fail with ``operator does not exist: timestamp with time zone
   < text``.
2. **page-boundary correctness** — a multi-page walk through the real
   ``CentralReadService`` visits every row exactly once (0 duplicates, 0 gaps)
   and preserves the declared sort order, on both the claims and coverage axes.
3. **tie stability** — rows sharing one ``occurred_at`` still come back in a
   deterministic total order thanks to the ``claim_id`` tie-breaker.
4. **out-of-domain cursor** — a forged cursor whose values the keyset columns
   cannot hold is rejected at the *application boundary* (``CursorError`` → 400).
   The same values sent straight to PostgreSQL raise ``22007`` / ``22P02``, which
   the adapter can only report as ``CentralReadError`` → 503. That contrast is
   the recorded justification for the boundary validation.
5. **conditional heartbeat INSERT** (M2 companion) — ``INSERT ... SELECT %s, ...
   WHERE EXISTS`` resolves its parameter types from the INSERT target list on a
   real server, writes 1 row for a registered chamber and 0 for an unregistered
   one, so ``rowcount`` is a sound existence verdict.

SSOT / no hardcoding:
- Every SQL statement under test comes from the production adapter constants —
  this script never re-types a query it is supposed to be proving.
- The DSN comes from ``--dsn`` or ``FCC_KEYSET_PROOF_DB_URL`` only. The variable
  is deliberately NOT ``FCC_CENTRAL_DB_URL``: opting in must be a separate act so
  an environment configured to talk to the operational database cannot be swept
  into a write-mode proof.
- Seed identities are ``uuid5`` derived from ``--proof-seed``, so re-runs
  converge on the same rows (idempotent) and the fixture cleans up after itself.

Target must be a **throwaway database** (see the migration-runner precedent):

    createdb fcc_keyset_proof
    FCC_CENTRAL_DB_URL=postgresql://.../fcc_keyset_proof \\
        python scripts/platform_db_migrate.py migrate
    FCC_KEYSET_PROOF_DB_URL=postgresql://.../fcc_keyset_proof \\
        python scripts/platform_keyset_cursor_live_proof.py
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _path in (PROJECT_ROOT, PROJECT_ROOT / 'src'):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fcc_test_platform.application.central_chamber_write_adapter import (  # noqa: E402
    HEARTBEAT_EVENT_COLUMNS,
    INSERT_HEARTBEAT_EVENT_SQL,
    SELECT_CHAMBER_EQUIPMENT_CONFIG_FOR_UPDATE_SQL,
    UPDATE_CHAMBER_EQUIPMENT_CONFIG_SQL,
    PostgresCentralChamberWriteAdapter,
)
from fcc_test_platform.application.central_read_adapter import (  # noqa: E402
    ACTIVE_CLAIMS_QUERY_SQL_PAGED,
    ACTIVE_CLAIM_KEYSET,
    COVERAGE_KEYSET,
    COVERAGE_QUERY_SQL_PAGED,
    PostgresCentralReadAdapter,
)
from fcc_test_platform.application.central_read_service import CentralReadService  # noqa: E402
from fcc_test_kernel.application.central_contract.envelope_helpers import parse_timestamp  # noqa: E402
from fcc_test_kernel.application.central_contract.pagination import CursorError, encode_cursor  # noqa: E402
from domain.ports.output.central_chamber_write_port import (  # noqa: E402
    ChamberNotFoundError,
)


ENV_DSN = 'FCC_KEYSET_PROOF_DB_URL'

#: Materialized view that backs the coverage axis — must be refreshed after
#: seeding attempts (the ingestion writer owns the refresh in production).
COVERAGE_MATERIALIZED_VIEW = 'coverage_by_condition_hash'

#: How many rows / rows-per-page the walk uses. Deliberately co-prime-ish so the
#: last page is partial (a full final page hides an off-by-one in has_more).
SEED_ROW_COUNT = 7
PAGE_SIZE = 2

#: Rows that deliberately share one occurred_at, to exercise the tie-breaker.
TIED_ROW_COUNT = 3

#: Stable clock for the seeded ledger — the proof asserts ordering, not wall time.
_BASE_TIMESTAMP = '2026-07-30T00:00:00+00:00'

#: ``FOR UPDATE`` 접미사 — 반사실을 **프로덕션 상수에서 파생**하기 위한 것이고,
#: 이 스크립트가 SQL 을 재타이핑하지 않는다는 규칙(모듈 독스트링)의 연장이다.
#: 정규식 자체는 ``tests/support/central_pg_sqlite_shim.py`` 가 같은 이유로 쓰는
#: 것과 같은 형태다 — 문장 끝에 앵커해 컬럼/리터럴 속의 같은 단어를 잘라내지 않는다.
_FOR_UPDATE_SUFFIX = re.compile(
    r'\s+FOR\s+UPDATE(\s+(NOWAIT|SKIP\s+LOCKED))?\s*$', re.IGNORECASE,
)
#: 두 writer 의 읽기가 실제로 교차하도록 벌리는 시간. 짧으면 교차가 일어나지 않아
#: 반사실이 **아무것도 증명하지 못한 채 green** 이 된다 — 그래서 반사실이 두 키를
#: 모두 지키면 이 증명은 성공이 아니라 실패로 답한다.
_LOCK_PROOF_INTERLEAVE_SECONDS = 0.4
_LOCK_PROOF_TIMEOUT_SECONDS = 30.0


class LiveProofError(RuntimeError):
    """The live proof could not be completed (setup failure or failed assertion)."""


def _uuid5(seed: str, label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'keyset-cursor-proof/{seed}/{label}'))


def _occurred_at(index: int) -> str:
    """Distinct, ordered timestamps — except the last ``TIED_ROW_COUNT`` which
    intentionally collide so the ``claim_id`` tie-breaker has something to do."""
    if index >= SEED_ROW_COUNT - TIED_ROW_COUNT:
        hour = SEED_ROW_COUNT - TIED_ROW_COUNT
    else:
        hour = index
    return f'2026-07-30T{hour:02d}:00:00+00:00'


# ── seeding ─────────────────────────────────────────────────────────────────


def _seed(connection, seed: str) -> dict:
    """Create the minimal identity graph + N claim/attempt rows. Idempotent."""
    project_id = _uuid5(seed, 'project')
    provider_id = _uuid5(seed, 'provider')
    session_id = _uuid5(seed, 'session')
    chamber_id = f'proof-{seed}'
    claim_ids = [_uuid5(seed, f'claim-{i}') for i in range(SEED_ROW_COUNT)]

    cursor = connection.cursor()
    try:
        # Reference data first — providers/projects are operator-registered in
        # production (runbook S4), so the proof provisions them explicitly.
        cursor.execute(
            'INSERT INTO providers (id, provider_id, product_line, contract_family,'
            ' contract_version, base_url, capabilities_json, enabled, created_at,'
            " updated_at) VALUES (%s, %s, 'proof', 'proof', 'v1', 'http://proof',"
            " '{}', true, now(), now()) ON CONFLICT (id) DO NOTHING",
            (provider_id, f'proof-{seed}'),
        )
        cursor.execute(
            'INSERT INTO projects (id, project_code, name, created_at, updated_at)'
            ' VALUES (%s, %s, %s, now(), now()) ON CONFLICT (id) DO NOTHING',
            (project_id, f'PRF-{seed}'[:32], 'keyset cursor live proof'),
        )
        cursor.execute(
            'INSERT INTO test_sessions (id, provider_id, provider_session_id,'
            " project_id, status) VALUES (%s, %s, %s, %s, 'completed')"
            ' ON CONFLICT (id) DO NOTHING',
            (session_id, provider_id, f'proof-{seed}', project_id),
        )
        # Idempotent re-run: clear this proof's own ledger rows only.
        cursor.execute('DELETE FROM claim_events WHERE project_id = %s', (project_id,))
        cursor.execute(
            'DELETE FROM measurement_attempts WHERE project_id = %s', (project_id,),
        )
        for index, claim_id in enumerate(claim_ids):
            cursor.execute(
                'INSERT INTO claim_events (id, claim_id, project_id, technology,'
                ' condition_hash, operator, action, occurred_at, created_at)'
                " VALUES (%s, %s, %s, %s, %s, 'proof-op', 'acquired', %s, now())",
                (
                    _uuid5(seed, f'claim-event-{index}'), claim_id, project_id,
                    'WLAN' if index % 2 else 'BLE', f'cond-{index:03d}',
                    _occurred_at(index),
                ),
            )
            cursor.execute(
                'INSERT INTO measurement_attempts (id, provider_id, session_id,'
                ' project_id, test_name, technology, condition_hash, attempt_number,'
                " is_latest, operator, status, verdict, result_json, measured_at)"
                " VALUES (%s, %s, %s, %s, 'proof', %s, %s, 1, true, 'proof-op',"
                " 'completed', 'PASS', '{}', %s)",
                (
                    _uuid5(seed, f'attempt-{index}'), provider_id, session_id,
                    project_id, 'WLAN' if index % 2 else 'BLE', f'cond-{index:03d}',
                    _BASE_TIMESTAMP,
                ),
            )
        connection.commit()
        cursor.execute(f'REFRESH MATERIALIZED VIEW "{COVERAGE_MATERIALIZED_VIEW}"')
        connection.commit()
    finally:
        cursor.close()
    return {
        'project_id': project_id,
        'provider_id': provider_id,
        'session_id': session_id,
        'chamber_id': chamber_id,
        'claim_ids': claim_ids,
    }


def _cleanup(connection, ids: dict) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute(
            'DELETE FROM chamber_heartbeat_events WHERE chamber_id = %s',
            (ids['chamber_id'],),
        )
        cursor.execute(
            'DELETE FROM chamber_nodes WHERE chamber_id = %s', (ids['chamber_id'],),
        )
        cursor.execute('DELETE FROM claim_events WHERE project_id = %s', (ids['project_id'],))
        cursor.execute(
            'DELETE FROM measurement_attempts WHERE project_id = %s', (ids['project_id'],),
        )
        cursor.execute('DELETE FROM test_sessions WHERE id = %s', (ids['session_id'],))
        cursor.execute('DELETE FROM projects WHERE id = %s', (ids['project_id'],))
        cursor.execute('DELETE FROM providers WHERE id = %s', (ids['provider_id'],))
        connection.commit()
        cursor.execute(f'REFRESH MATERIALIZED VIEW "{COVERAGE_MATERIALIZED_VIEW}"')
        connection.commit()
    finally:
        cursor.close()


# ── proof steps ─────────────────────────────────────────────────────────────


def _prove_parameter_types(connect, project_id: str, seed: str) -> dict:
    """Observe the server-side inferred parameter types for both paged queries.

    psycopg3 promotes a statement to a server-side prepared statement after
    ``prepare_threshold`` executions on the same connection; only then does
    ``pg_prepared_statements`` expose the resolved ``parameter_types``.
    """
    observed: dict = {}
    probes = (
        ('active_claims', ACTIVE_CLAIMS_QUERY_SQL_PAGED, ACTIVE_CLAIM_KEYSET,
         (_occurred_at(SEED_ROW_COUNT), _uuid5(seed, 'claim-0'))),
        ('coverage', COVERAGE_QUERY_SQL_PAGED, COVERAGE_KEYSET, ('', '')),
    )
    for label, sql, keyset, cursor_values in probes:
        # One fresh session per probe: ``pg_prepared_statements`` is session-scoped,
        # so a virgin connection that has run exactly one statement leaves exactly
        # one row — no fragile matching on the statement text (psycopg rewrites the
        # ``%s`` placeholders to ``$n`` before the server ever sees them).
        with connect() as connection:
            cursor = connection.cursor()
            for _ in range(8):  # exceed psycopg3's prepare_threshold
                cursor.execute(sql, (project_id, *cursor_values, PAGE_SIZE))
                cursor.fetchall()
            cursor.execute(
                'SELECT statement, parameter_types::text[] FROM pg_prepared_statements'
            )
            rows = cursor.fetchall()
            cursor.close()
        if len(rows) != 1:
            raise LiveProofError(
                f'{label}: expected exactly 1 server-prepared statement on a fresh '
                f'session, found {len(rows)} (psycopg prepare_threshold changed?)'
            )
        statement, parameter_types = rows[0]
        observed[label] = {
            'keyset': list(keyset),
            'prepared_statement': statement,
            # Params are (project_id, *cursor values, limit); the cursor slice is
            # what this proof is about.
            'parameter_types': list(parameter_types),
            'cursor_parameter_types': list(parameter_types)[1:1 + len(keyset)],
        }
    return observed


def _walk(page_fn, project_id: str) -> list:
    """Page through every row following next_cursor; return the ordered items."""
    collected: list = []
    cursor = None
    for _ in range(SEED_ROW_COUNT + 5):  # loop guard for a broken cursor
        page = page_fn(project_id, limit=PAGE_SIZE, cursor=cursor)
        collected.extend(page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            return collected
    raise LiveProofError('pagination did not terminate — cursor is not advancing')


def _prove_page_boundaries(service: CentralReadService, ids: dict) -> dict:
    project_id = ids['project_id']
    claims = _walk(service.project_claims, project_id)
    coverage = _walk(service.project_coverage, project_id)

    claim_keys = [tuple(row[column] for column in ACTIVE_CLAIM_KEYSET) for row in claims]
    coverage_keys = [
        tuple(row[column] for column in COVERAGE_KEYSET) for row in coverage
    ]
    unbounded = service.project_claims(project_id)['items']

    errors = []
    if len(claim_keys) != len(set(claim_keys)):
        errors.append('claims walk produced duplicate rows across pages')
    if len(claim_keys) != SEED_ROW_COUNT:
        errors.append(
            f'claims walk saw {len(claim_keys)} of {SEED_ROW_COUNT} rows (gap)'
        )
    if claim_keys != sorted(claim_keys, reverse=True):
        errors.append('claims walk did not preserve occurred_at DESC, claim_id DESC')
    if [row['claim_id'] for row in claims] != [row['claim_id'] for row in unbounded]:
        errors.append('paged walk and unbounded read disagree on row order')
    if len(coverage_keys) != len(set(coverage_keys)):
        errors.append('coverage walk produced duplicate rows across pages')
    if coverage_keys != sorted(coverage_keys):
        errors.append('coverage walk did not preserve technology, condition_hash ASC')
    if errors:
        raise LiveProofError('; '.join(errors))

    # The envelope carries the driver's rendering of the timestamp column (psycopg
    # yields a ``datetime``, stringified as ``YYYY-MM-DD HH:MM:SS+00:00``), so the
    # comparison goes through the shared tolerant-parse SSOT rather than string
    # equality against the ISO literal this script seeded.
    tie_instant = parse_timestamp(_occurred_at(SEED_ROW_COUNT - 1))
    tied = [key for key in claim_keys if parse_timestamp(key[0]) == tie_instant]
    if len(tied) != TIED_ROW_COUNT:
        raise LiveProofError(
            f'expected {TIED_ROW_COUNT} rows sharing one occurred_at, saw {len(tied)}'
        )
    return {
        'page_size': PAGE_SIZE,
        'rows_seeded': SEED_ROW_COUNT,
        'claims_rows_walked': len(claim_keys),
        'claims_duplicates': 0,
        'coverage_rows_walked': len(coverage_keys),
        'coverage_duplicates': 0,
        'tied_occurred_at_rows': len(tied),
        'tie_breaker_order_stable': True,
        'paged_matches_unbounded_order': True,
    }


def _prove_out_of_domain_cursor(connect, service: CentralReadService,
                                project_id: str) -> dict:
    """A forged cursor is a 400 at the boundary — and would be a 503 without it."""
    forged = {
        'not-a-timestamp': ('definitely-not-a-timestamp', _uuid5('x', 'claim-0')),
        'not-a-uuid': (_occurred_at(0), 'definitely-not-a-uuid'),
    }
    boundary: dict = {}
    for label, values in forged.items():
        token = encode_cursor(list(values))
        try:
            service.project_claims(project_id, limit=PAGE_SIZE, cursor=token)
        except CursorError as exc:
            boundary[label] = {'rejected_at_boundary': True, 'error': type(exc).__name__}
        else:
            raise LiveProofError(
                f'{label}: forged cursor was accepted by the boundary — it would '
                'reach PostgreSQL and return 503 instead of 400'
            )

    # Counterfactual: what the database does with those same values. This is the
    # recorded reason the boundary check exists, not a redundant assertion.
    database: dict = {}
    with connect() as connection:
        cursor = connection.cursor()
        for label, values in forged.items():
            try:
                cursor.execute(
                    ACTIVE_CLAIMS_QUERY_SQL_PAGED, (project_id, *values, PAGE_SIZE),
                )
            except Exception as exc:  # noqa: BLE001 — sqlstate is the evidence
                connection.rollback()
                database[label] = {
                    'sqlstate': getattr(exc, 'sqlstate', None),
                    'exception': type(exc).__name__,
                }
            else:
                cursor.fetchall()
                database[label] = {'sqlstate': None, 'exception': None}
        cursor.close()
    missing = [label for label, info in database.items() if info['sqlstate'] is None]
    if missing:
        raise LiveProofError(
            f'expected PostgreSQL to reject out-of-domain cursor values for {missing} '
            '— if the server now accepts them, re-derive the boundary policy'
        )
    return {'boundary': boundary, 'database_counterfactual': database}


def _prove_conditional_heartbeat_insert(connect, chamber_id: str, seed: str) -> dict:
    """M2 companion — the existence gate resolves its own parameter types on a
    real server, and ``rowcount`` discriminates registered from unregistered."""
    adapter = PostgresCentralChamberWriteAdapter(connect)
    adapter.register_chamber({
        'id': _uuid5(seed, 'chamber'), 'chamber_id': chamber_id, 'name': 'proof',
        'base_url': 'http://proof:8000', 'enabled': True,
        'heartbeat_ttl_seconds': 90,
        'created_at': _BASE_TIMESTAMP, 'updated_at': _BASE_TIMESTAMP,
    })

    def _record(target: str, label: str) -> dict:
        record = {column: None for column in HEARTBEAT_EVENT_COLUMNS}
        record.update({
            'id': _uuid5(seed, f'heartbeat-{label}'),
            'chamber_id': target,
            'reported_status': 'idle',
            'occurred_at': _BASE_TIMESTAMP,
            'created_at': _BASE_TIMESTAMP,
        })
        return record

    adapter.append_heartbeat(_record(chamber_id, 'registered'))
    unknown_rejected = False
    try:
        adapter.append_heartbeat(_record(f'{chamber_id}-ghost', 'unregistered'))
    except ChamberNotFoundError:
        unknown_rejected = True
    if not unknown_rejected:
        raise LiveProofError(
            'unregistered chamber heartbeat was accepted on live PostgreSQL — the '
            'WHERE EXISTS gate is not doing what the unit lane claims'
        )

    with connect() as connection:
        cursor = connection.cursor()
        cursor.execute(
            'SELECT count(*) FROM chamber_heartbeat_events WHERE chamber_id LIKE %s',
            (f'{chamber_id}%',),
        )
        written = cursor.fetchone()[0]
        cursor.close()
    if written != 1:
        raise LiveProofError(
            f'expected exactly 1 ledger row (registered only), found {written}'
        )
    return {
        'sql': INSERT_HEARTBEAT_EVENT_SQL,
        'registered_chamber_rows_written': 1,
        'unregistered_chamber_rows_written': 0,
        'unregistered_raises_not_found': True,
    }


def _prove_equipment_config_row_lock(connect, chamber_id: str, seed: str) -> dict:
    """The per-key PATCH merge survives two concurrent writers — and only because
    of ``FOR UPDATE``.

    WHY THIS PROOF EXISTS AT ALL
        ``tests/support/central_pg_sqlite_shim.py`` says it in its own comment:
        SQLite has no row-level locking, a write transaction locks the database,
        and therefore that shim **cannot reproduce a lost update caused by a
        missing FOR UPDATE**. The unit lane can only assert that the string
        appears in the adapter's SQL. That is an assertion about source, not
        about behaviour, and the operator asked (2026-08-10) for the real thing
        before the web is deployed.

    WHAT IS ACTUALLY SHOWN
        Two connections PATCH **different keys** of the same chamber's config at
        the same time. Under the production statement both keys survive. Under a
        counterfactual that differs in exactly one way — the ``FOR UPDATE``
        suffix removed — the second writer overwrites the first and one
        operator's edit disappears with no error anywhere.

    THE COUNTERFACTUAL IS DERIVED, NOT RETYPED
        This script's own rule (module docstring) is that every statement under
        test comes from the production adapter constant. The counterfactual is
        that same constant with the suffix stripped, so it cannot drift away from
        what it is a counterfactual OF.
    """
    adapter = PostgresCentralChamberWriteAdapter(connect)
    adapter.register_chamber({
        'id': _uuid5(seed, 'lock-chamber'), 'chamber_id': chamber_id,
        'name': 'row-lock proof', 'base_url': 'http://proof:8000', 'enabled': True,
        'heartbeat_ttl_seconds': 90,
        'created_at': _BASE_TIMESTAMP, 'updated_at': _BASE_TIMESTAMP,
    })

    def _interleaved(select_sql: str) -> dict:
        """Run the read-modify-write of two writers with their reads interleaved.

        Writer A reads, then writer B reads (this is the interleaving that makes
        a lost update possible), then A writes, then B writes. With the row lock
        B's read BLOCKS until A commits, so the interleaving cannot happen and
        B merges onto A's value.
        """
        _reset_config(connect, chamber_id)
        results: dict = {}
        barrier = threading.Barrier(2, timeout=_LOCK_PROOF_TIMEOUT_SECONDS)

        def _writer(key: str, value: str, delay: float) -> None:
            try:
                with connect() as connection:
                    cursor = connection.cursor()
                    barrier.wait()
                    time.sleep(delay)
                    cursor.execute(select_sql, (chamber_id,))
                    row = cursor.fetchone()
                    current = dict(_decode_config(row[0] if row else None))
                    time.sleep(_LOCK_PROOF_INTERLEAVE_SECONDS)
                    current[key] = value
                    cursor.execute(UPDATE_CHAMBER_EQUIPMENT_CONFIG_SQL, (
                        json.dumps(current, sort_keys=True),
                        _BASE_TIMESTAMP,
                        chamber_id,
                    ))
                    cursor.close()
                    connection.commit()
            except Exception as exc:  # noqa: BLE001 — reported, not swallowed
                results[key] = repr(exc)

        threads = [
            threading.Thread(target=_writer, args=('analyzer', 'A', 0.0)),
            threading.Thread(target=_writer, args=('switchbox', 'B', 0.05)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=_LOCK_PROOF_TIMEOUT_SECONDS)
        if results:
            raise LiveProofError(f'a writer failed during the row-lock proof: {results}')
        return _read_config(connect, chamber_id)

    locked = _interleaved(SELECT_CHAMBER_EQUIPMENT_CONFIG_FOR_UPDATE_SQL)
    unlocked = _interleaved(
        _FOR_UPDATE_SUFFIX.sub('', SELECT_CHAMBER_EQUIPMENT_CONFIG_FOR_UPDATE_SQL)
    )

    if sorted(locked) != ['analyzer', 'switchbox']:
        raise LiveProofError(
            'the production statement lost an edit on live PostgreSQL — the row '
            f'lock is not doing what the unit lane claims (kept: {sorted(locked)})'
        )
    if sorted(unlocked) == ['analyzer', 'switchbox']:
        raise LiveProofError(
            'the counterfactual kept both keys, so this run proved nothing: the '
            'interleaving never happened. Widen _LOCK_PROOF_INTERLEAVE_SECONDS '
            'rather than accepting a green that means "not measured".'
        )
    return {
        'sql': SELECT_CHAMBER_EQUIPMENT_CONFIG_FOR_UPDATE_SQL,
        'locked_keys_kept': sorted(locked),
        'counterfactual_keys_kept': sorted(unlocked),
        'counterfactual_lost_an_edit': True,
        'shim_can_reproduce_this': False,
    }


def _reset_config(connect, chamber_id: str) -> None:
    with connect() as connection:
        cursor = connection.cursor()
        cursor.execute(UPDATE_CHAMBER_EQUIPMENT_CONFIG_SQL, (
            None, _BASE_TIMESTAMP, chamber_id,
        ))
        cursor.close()
        connection.commit()


def _read_config(connect, chamber_id: str) -> dict:
    with connect() as connection:
        cursor = connection.cursor()
        cursor.execute(
            _FOR_UPDATE_SUFFIX.sub('', SELECT_CHAMBER_EQUIPMENT_CONFIG_FOR_UPDATE_SQL),
            (chamber_id,),
        )
        row = cursor.fetchone()
        cursor.close()
    return dict(_decode_config(row[0] if row else None))


def _decode_config(raw) -> dict:
    if raw in (None, ''):
        return {}
    if isinstance(raw, (dict,)):
        return raw
    return json.loads(raw)


# ── orchestration ───────────────────────────────────────────────────────────


def run_live_proof(dsn: str, *, proof_seed: str = 'default') -> dict:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - environment gate
        raise LiveProofError('psycopg (v3) is required for the live proof') from exc

    def _connect():
        return psycopg.connect(dsn)

    with _connect() as connection:
        ids = _seed(connection, proof_seed)
    try:
        service = CentralReadService(PostgresCentralReadAdapter(_connect))
        evidence = {
            'ok': True,
            'proof_seed': proof_seed,
            'parameter_types': _prove_parameter_types(
                _connect, ids['project_id'], proof_seed,
            ),
            'page_boundaries': _prove_page_boundaries(service, ids),
            'out_of_domain_cursor': _prove_out_of_domain_cursor(
                _connect, service, ids['project_id'],
            ),
            'conditional_heartbeat_insert': _prove_conditional_heartbeat_insert(
                _connect, ids['chamber_id'], proof_seed,
            ),
            'equipment_config_row_lock': _prove_equipment_config_row_lock(
                _connect, f"{ids['chamber_id']}-lock", proof_seed,
            ),
        }
    finally:
        with _connect() as connection:
            _cleanup(connection, ids)
    return evidence


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dsn', default=os.environ.get(ENV_DSN, ''),
                        help=f'PostgreSQL DSN (default: {ENV_DSN} env)')
    parser.add_argument('--proof-seed', default='default',
                        help='namespace for the deterministic seed identities')
    parser.add_argument('--output', default='',
                        help='write the evidence bundle to this path as well')
    args = parser.parse_args(argv)

    if not args.dsn:
        print(json.dumps({
            'ok': False,
            'skipped': True,
            'reason': f'{ENV_DSN} not set — live PostgreSQL keyset proof skipped',
        }, indent=2))
        return 0
    try:
        evidence = run_live_proof(args.dsn, proof_seed=args.proof_seed)
    except LiveProofError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, indent=2))
        return 1
    rendered = json.dumps(evidence, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + '\n', encoding='utf-8')
    print(rendered)
    return 0


if __name__ == '__main__':
    sys.exit(main())
