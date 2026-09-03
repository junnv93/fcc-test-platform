"""PostgreSQL writer adapter for platform ingestion plans.

The adapter accepts a DB-API compatible connection factory. It does not import
psycopg directly, so deployment code can choose the concrete PostgreSQL driver.

FE-P0c Phase C (2026-05-26) — fulfills FE-P0a ingestion_contract:

Rule 1 (``attempts_insert_and_is_latest_toggle_atomic``):
    ``upsert_attempt(record, fk_resolution_hint)`` runs as a single transaction
    that (a) recomputes is_latest for the exact (project_id, provider_id,
    condition_hash) partition,
    (b) INSERTs the new attempt with is_latest=true, and (c) acquires SERIALIZABLE
    isolation OR ``SELECT ... FOR UPDATE`` on the prior latest row. Concurrent
    ingestions collide on ``ux_measurement_attempts_session_condition_attempt``
    UNIQUE constraint and the loser retries.

Rule 2 (``measurement_results_is_projection_of_latest_attempt``):
    Inside the SAME transaction as the attempt INSERT, ``measurement_results``
    is upserted with the latest attempt's (verdict / result_json / operator /
    measured_at / condition_hash) so non-coverage browse queries observe a
    consistent latest projection. The writer also resolves
    ``measurement_result_id`` via RETURNING id and back-fills the attempt row
    so the FK to measurement_results is valid.

Rule 4 (``coverage_refresh_within_same_unit_of_work``):
    After ``commit()`` succeeds, ``refresh_coverage_view()`` issues
    ``REFRESH MATERIALIZED VIEW CONCURRENTLY coverage_by_condition_hash``.
    Refresh failure is logged on a hook and does NOT roll back the attempt
    transaction (measurement fact is durable; PT1H fallback cron retries).
"""
from __future__ import annotations

import re
import logging
from typing import Callable, Iterable, Mapping, Optional

from fcc_test_platform.provider_ingestion_plan import (
    CHAMBER_SCOPED_IDEMPOTENCY_KEYS_BY_TABLE,
    IDEMPOTENCY_KEYS_BY_TABLE,
    PROVIDER_SCOPED_IDEMPOTENCY_KEYS_BY_TABLE,
    idempotency_fields_for_record,
)
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'CONFLICT_FILL_ONLY_COLUMNS',
    'COVERAGE_REFRESH_STATEMENT',
    'INSERT_ONLY_TABLES',
    'PostgresIngestionTransaction',
    'PostgresIngestionWriter',
    'build_postgres_attempt_transaction_statements',
    'build_postgres_results_projection_update',
    'build_postgres_upsert',
]


#: Tables whose upsert must NOT blanket-overwrite an existing row.
#:
#: ``test_sessions`` is ingested only so the measurement ``session_id`` FKs
#: resolve — the batch carries the neutral ingest-time status, not session
#: lifecycle truth. With the default ``DO UPDATE`` behaviour a re-sync of an
#: old outbox event would overwrite a session that central had since moved to
#: 'completed' back to 'active'. Existence is what ingestion guarantees;
#: lifecycle belongs to the central session APIs.
INSERT_ONLY_TABLES = frozenset({'test_sessions'})

#: Per-table columns that an :data:`INSERT_ONLY_TABLES` conflict may still fill
#: — but only where the stored value is NULL (``COALESCE(existing, excluded)``).
#:
#: Without this, a session first synced before its project resolved would keep
#: ``project_id`` NULL forever: the later batch that *does* carry the project
#: hits the same natural key and a plain ``DO NOTHING`` discards it, leaving the
#: session project-less while its own measurement rows carry the project — a
#: read-model split. Filling only NULLs keeps the write monotonic: central can
#: still correct the value later and ingestion will never clobber it.
#: 세션 출처 두 칸(PC 단위 모드 배타 ①)이 같은 목록에 있는 이유는 **업그레이드 창** 하나다:
#: 선언 이전 빌드가 그 세션의 첫 배치를 이미 유입시켰다면 자연키가 같아 ``DO NOTHING`` 이
#: 되고, 선언을 실은 다음 배치의 값이 버려져 그 세션은 영영 '모름'으로 굳는다. NULL 만
#: 채우므로 이미 기록된 값을 덮지 않는다 — 관측 축이 기존 사실을 고쳐 쓰지 않는다.
CONFLICT_FILL_ONLY_COLUMNS = {
    'test_sessions': (
        'project_id', 'sample_id', 'session_origin', 'workbook_handle',
        'sample_snapshot_json', 'sample_snapshot_schema_version',
        'project_result_reference_snapshot_json',
        'project_result_reference_snapshot_schema_version',
    ),
}

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r'^[a-z_][a-z0-9_]*$')

# Refresh statement used by the post-commit hook. CONCURRENTLY requires the
# materialized view to have a UNIQUE index (FE-P0a schema declares
# ux_coverage_by_condition_hash). Loud failure inside the hook is converted to
# a warning by the caller — the attempt fact is already durable.
COVERAGE_REFRESH_STATEMENT = (
    'REFRESH MATERIALIZED VIEW CONCURRENTLY "coverage_by_condition_hash"'
)


class PostgresIngestionWriter:
    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        self._connection_factory = connection_factory

    def begin_transaction(self) -> 'PostgresIngestionTransaction':
        return PostgresIngestionTransaction(self._connection_factory())

    def refresh_coverage_materialized_view(self) -> None:
        """Run ``REFRESH MATERIALIZED VIEW CONCURRENTLY coverage_by_condition_hash``
        on a dedicated autocommit connection.

        Phase F (2026-05-26) external review fix — PostgreSQL rejects
        ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` inside a transaction block.
        psycopg's default cursor.execute starts an implicit transaction, so the
        previous post-commit hook inside ``PostgresIngestionTransaction.commit``
        actually issued the REFRESH inside a fresh transaction block — silently
        rejected by Postgres. This dedicated path opens a NEW connection,
        toggles ``connection.autocommit = True`` (psycopg / asyncpg / pg8000
        common attribute), executes the REFRESH at the top-level, then closes.

        Failure is loud — caller (worker) MUST swallow if the attempt fact is
        already committed (durability before convenience).
        """
        connection = self._connection_factory()
        try:
            # Duck-typed autocommit toggle — psycopg2/psycopg3/pg8000/asyncpg all
            # expose ``connection.autocommit``. If a connection wrapper lacks it
            # the REFRESH will fail inside a transaction block (loud) — that is
            # the desired behaviour: misconfigured composition should not be
            # silently downgraded to a no-op refresh.
            try:
                setattr(connection, 'autocommit', True)
            except AttributeError:
                pass
            cursor = connection.cursor()
            try:
                cursor.execute(COVERAGE_REFRESH_STATEMENT, ())
            finally:
                cursor.close()
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()


class PostgresIngestionTransaction:
    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection
        self._cursor = connection.cursor()
        self._closed = False
        self._serializable_set = False
        # Rule 4 — coverage refresh is only required when at least one
        # measurement_attempts row was actually written. A transaction that
        # only upserts artifacts/report_outputs MUST NOT issue REFRESH (it
        # would be a wasted O(N) over the entire attempt table). The flag is
        # surfaced via ``attempt_was_written`` so the worker (caller) can
        # decide whether to invoke ``writer.refresh_coverage_materialized_view()``
        # AFTER ``commit()`` on a SEPARATE autocommit connection.
        # Phase F (2026-05-26) — refresh used to be issued inside commit() on
        # the same connection, which silently failed because psycopg's default
        # cursor.execute starts an implicit transaction and PostgreSQL
        # disallows REFRESH MATERIALIZED VIEW CONCURRENTLY inside a transaction
        # block. The hook responsibility moved up to the worker.
        self._attempt_was_written = False

    def set_serializable_isolation(self) -> None:
        """Acquire SERIALIZABLE isolation for the attempt transaction.

        Mandatory for FE-P0a ingestion_contract Rule 1 — without it, two
        concurrent ingestions can both observe prior is_latest=true rows and
        race to INSERT two new is_latest=true rows for the same partition.
        SERIALIZABLE makes the second commit fail (Postgres
        ``serialization_failure``); the caller retries.

        Issued once per transaction; idempotent.
        """
        if self._serializable_set:
            return
        self._cursor.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE', ())
        self._serializable_set = True

    def upsert(self, table: str, record: Mapping, idempotency_key: tuple[str, ...]) -> int:
        statement, parameters = build_postgres_upsert(table, record, idempotency_key)
        self._cursor.execute(statement, parameters)
        affected = int(getattr(self._cursor, 'rowcount', 0) or 0)
        if (
            table == 'test_sessions'
            and record.get('project_result_reference_snapshot_json')
            and record.get('project_result_reference_snapshot_schema_version')
            and affected == 0
        ):
            # The SQL conflict predicate deliberately refuses a replay whose
            # complete snapshot differs from the first stored bytes. It is not
            # silently treated as success: the caller gets a durable warning
            # while the original history remains untouched.
            logger.warning(
                'central test-session reference snapshot replay conflicted; '
                'first complete snapshot retained; idempotency_key=%s',
                idempotency_key,
            )
        if table == 'measurement_attempts':
            self._attempt_was_written = True
        return affected

    def upsert_attempt(
        self,
        attempt_record: Mapping,
        idempotency_key: tuple[str, ...],
        *,
        fk_resolution_hint: Optional[Mapping] = None,
    ) -> int:
        """Atomically toggle prior is_latest=false, INSERT new attempt with
        is_latest=true (Rule 1), then back-fill measurement_result_id via the
        SAME-transaction (provider_id, provider_result_id) lookup (Phase A).

        Rule 2 (results projection update inside SAME transaction) is the
        caller's responsibility via ``project_results_from_latest_attempt``.

        Phase F (2026-05-26) — SERIALIZABLE isolation must be set BEFORE this
        method is invoked, because PostgreSQL rejects ``SET TRANSACTION
        ISOLATION LEVEL`` once any statement has executed on the transaction.
        The plan order writes ``measurement_results`` rows before
        ``measurement_attempts``, so the worker must call
        ``set_serializable_isolation()`` IMMEDIATELY after ``begin_transaction()``
        when the plan contains an is_latest=true attempt.

        Raises if SERIALIZABLE was not set already and is_latest=true.
        """
        if attempt_record.get('is_latest') is not True:
            # Batch derivation already toggled non-latest rows; only is_latest=true
            # row triggers the prior-row toggle. Non-latest rows are inserted via
            # the generic upsert path with composite key.
            return self.upsert('measurement_attempts', attempt_record, idempotency_key)

        if not self._serializable_set:
            raise ValueError(
                'set_serializable_isolation() must be called BEFORE upsert_attempt '
                'when the plan contains is_latest=true rows (Phase F constraint — '
                'PostgreSQL rejects SET TRANSACTION ISOLATION after the first '
                'statement). Worker is responsible for pre-scanning the plan.'
            )

        for statement, parameters in build_postgres_attempt_transaction_statements(
            attempt_record,
            fk_resolution_hint=fk_resolution_hint or {},
            idempotency_key=idempotency_key,
        ):
            self._cursor.execute(statement, parameters)
        self._attempt_was_written = True
        return int(getattr(self._cursor, 'rowcount', 0) or 0)

    @property
    def attempt_was_written(self) -> bool:
        """True if any measurement_attempts row was written on this transaction.
        Worker queries this AFTER commit() to decide whether to call
        ``writer.refresh_coverage_materialized_view()``.
        """
        return self._attempt_was_written

    def project_results_from_latest_attempt(
        self,
        *,
        provider_id: str,
        provider_result_id: str,
        verdict: Optional[str],
        result_json: str,
        operator: Optional[str],
        measured_at: Optional[str],
        condition_hash: str,
        session_id: Optional[str] = None,
        attempt_number: Optional[int] = None,
    ) -> int:
        """Update measurement_results to reflect the just-INSERTed latest attempt
        (FE-P0a ingestion_contract Rule 2). Runs in the SAME transaction.
        """
        statement, parameters = build_postgres_results_projection_update(
            provider_id=provider_id,
            provider_result_id=provider_result_id,
            verdict=verdict,
            result_json=result_json,
            operator=operator,
            measured_at=measured_at,
            condition_hash=condition_hash,
            session_id=session_id,
            attempt_number=attempt_number,
        )
        self._cursor.execute(statement, parameters)
        return int(getattr(self._cursor, 'rowcount', 0) or 0)

    def commit(self) -> None:
        """Commit the attempt + projection transaction.

        Phase F (2026-05-26) — DOES NOT issue REFRESH MATERIALIZED VIEW here.
        REFRESH CONCURRENTLY is disallowed inside a transaction block by
        PostgreSQL; the worker invokes
        ``writer.refresh_coverage_materialized_view()`` on a SEPARATE
        autocommit connection after ``commit()`` returns. See
        ``attempt_was_written`` property.
        """
        try:
            self._connection.commit()
        finally:
            self._close_cursor()

    def rollback(self) -> None:
        try:
            self._connection.rollback()
        finally:
            self._close_cursor()

    def _close_cursor(self) -> None:
        if self._closed:
            return
        self._cursor.close()
        self._closed = True


def build_postgres_upsert(table: str, record: Mapping, idempotency_key: tuple[str, ...]) -> tuple[str, tuple]:
    _validate_table(table)
    if not isinstance(record, Mapping) or not record:
        raise ValueError('record must be a non-empty mapping')
    legacy_key_fields = idempotency_fields_for_record(table, record)
    provider_key_fields = PROVIDER_SCOPED_IDEMPOTENCY_KEYS_BY_TABLE.get(table)
    if provider_key_fields and len(idempotency_key) == len(provider_key_fields):
        expected_key_fields = provider_key_fields
    else:
        expected_key_fields = legacy_key_fields
    expected_key_values = tuple(str(record.get(field) or '').strip() for field in expected_key_fields)
    supplied_key_values = tuple(str(value or '').strip() for value in idempotency_key)
    if expected_key_values != supplied_key_values:
        # A pre-multichamber caller may omit chamber_id from the INSERT record.
        # The migration supplies the reserved legacy default, so target the new
        # three-column unique index while retaining the old two-value plan key.
        if not (
            table == 'test_sessions'
            and 'chamber_id' not in record
            and supplied_key_values == tuple(
                str(record.get(field) or '').strip()
                for field in IDEMPOTENCY_KEYS_BY_TABLE[table]
            )
        ):
            raise ValueError('idempotency_key values must match table idempotency fields')
    conflict_fields = expected_key_fields
    if table == 'test_sessions' and 'chamber_id' not in record:
        conflict_fields = CHAMBER_SCOPED_IDEMPOTENCY_KEYS_BY_TABLE[table]
    columns = sorted(str(column) for column in record)
    for column in columns:
        _validate_identifier(column)
    missing = [field for field in expected_key_fields if field not in record]
    if missing:
        raise ValueError(f'missing idempotency fields: {missing}')

    placeholders = ', '.join(['%s'] * len(columns))
    column_sql = ', '.join(_quote(column) for column in columns)
    conflict_sql = ', '.join(_quote(column) for column in conflict_fields)
    if table in INSERT_ONLY_TABLES:
        fill_only = [
            column for column in CONFLICT_FILL_ONLY_COLUMNS.get(table, ())
            if column in columns and column not in conflict_fields
        ]
        if fill_only:
            update_sql = ', '.join(
                f'{_quote(column)} = COALESCE({_quote(table)}.{_quote(column)}, '
                f'EXCLUDED.{_quote(column)})'
                for column in fill_only
            )
            conflict_action = f'DO UPDATE SET {update_sql}'
            if (
                table == 'test_sessions'
                and 'project_result_reference_snapshot_json' in columns
                and 'project_result_reference_snapshot_schema_version' in columns
            ):
                conflict_action += (
                    ' WHERE ('
                    f'{_quote(table)}."project_result_reference_snapshot_json" IS NULL '
                    'OR '
                    f'{_quote(table)}."project_result_reference_snapshot_json" = '
                    'EXCLUDED."project_result_reference_snapshot_json"'
                    ') AND ('
                    f'{_quote(table)}."project_result_reference_snapshot_schema_version" '
                    'IS NULL OR '
                    f'{_quote(table)}."project_result_reference_snapshot_schema_version" = '
                    'EXCLUDED."project_result_reference_snapshot_schema_version"'
                    ')'
                )
        else:
            conflict_action = 'DO NOTHING'
    else:
        update_columns = [column for column in columns if column not in conflict_fields]
        if update_columns:
            update_sql = ', '.join(
                f'{_quote(column)} = EXCLUDED.{_quote(column)}' for column in update_columns
            )
            conflict_action = f'DO UPDATE SET {update_sql}'
        else:
            conflict_action = 'DO NOTHING'
    # The target is derived from the table's central idempotency-key SSOT. In
    # particular, test_sessions uses its declared chamber-scoped unique index;
    # PostgreSQL requires an inference target for every DO UPDATE clause.
    conflict_target = f' ({conflict_sql})'
    statement = (
        f'INSERT INTO {_quote(table)} ({column_sql}) '
        f'VALUES ({placeholders}) '
        f'ON CONFLICT{conflict_target} {conflict_action}'
    )
    parameters = tuple(record[column] for column in columns)
    return statement, parameters


def build_postgres_attempt_transaction_statements(
    attempt_record: Mapping,
    *,
    fk_resolution_hint: Mapping,
    idempotency_key: Optional[tuple[str, ...]] = None,
) -> list[tuple[str, tuple]]:
    """Build the transaction body for an ``is_latest=true`` attempt INSERT.

    The INSERT is provider-scoped. The following statements clear the old
    projection and recompute it from the central recency order
    ``measured_at DESC NULLS LAST, created_at DESC, id DESC`` over eligible
    completed attempts. ``attempt_number`` remains a session-local idempotency
    component only; it never decides cross-session recency.

    SERIALIZABLE isolation (set at transaction start) makes two distinct
    attempts racing on the same exact provider partition serialize; a
    concurrent insert changing the ranked set escalates to a serialization
    failure on commit, so the caller retries.
    """
    project_id = attempt_record.get('project_id')
    condition_hash = attempt_record.get('condition_hash')
    if not condition_hash:
        raise ValueError('attempt_record.condition_hash is required for is_latest recompute')
    provider_id = attempt_record.get('provider_id')
    if not provider_id:
        raise ValueError('attempt_record.provider_id is required')

    provider_result_id = fk_resolution_hint.get('provider_result_id') if fk_resolution_hint else None

    statements: list[tuple[str, tuple]] = []

    columns = sorted(str(c) for c in attempt_record)
    for column in columns:
        _validate_identifier(column)

    insert_column_sql_pieces = [_quote(c) for c in columns]
    insert_value_pieces = ['%s'] * len(columns)
    insert_params: list = [attempt_record[c] for c in columns]

    # FK back-fill: append measurement_result_id resolved via subquery.
    if provider_result_id:
        insert_column_sql_pieces.append(_quote('measurement_result_id'))
        insert_value_pieces.append(
            '(SELECT "id" FROM "measurement_results" '
            'WHERE "provider_id" = %s AND "provider_result_id" = %s)'
        )
        insert_params.append(provider_id)
        insert_params.append(provider_result_id)

    conflict_fields = (
        PROVIDER_SCOPED_IDEMPOTENCY_KEYS_BY_TABLE['measurement_attempts']
        if idempotency_key is not None and len(idempotency_key) == 4
        else IDEMPOTENCY_KEYS_BY_TABLE['measurement_attempts']
    )
    conflict_sql = ', '.join(_quote(field) for field in conflict_fields)
    insert_sql = (
        f'INSERT INTO "measurement_attempts" ({", ".join(insert_column_sql_pieces)}) '
        f'VALUES ({", ".join(insert_value_pieces)}) '
        f'ON CONFLICT ({conflict_sql}) DO NOTHING'
    )
    statements.append((insert_sql, tuple(insert_params)))

    # One database-owned UPDATE repairs both the true and false flags. The
    # scalar candidate lookup is exact-partitioned; an out-of-order arrival
    # therefore cannot crown its incoming row merely because the batch marked
    # it as a candidate. This is deliberately independent of the caller's
    # idempotency-key shape: the legacy three-field key remains accepted for
    # replay compatibility, but it must never select a providerless or
    # attempt_number-based latest calculation.
    # NULL project_id uses IS NOT DISTINCT FROM for ANSI-compliant NULL equality.
    recompute_sql = (
        'UPDATE "measurement_attempts" AS target '
        'SET "is_latest" = (target."status" = \'completed\' AND target."id" = ('
        'SELECT candidate."id" FROM "measurement_attempts" AS candidate '
        'WHERE (candidate."project_id" IS NOT DISTINCT FROM %s) '
        'AND candidate."provider_id" = %s '
        'AND candidate."condition_hash" = %s '
        'AND candidate."status" = \'completed\' '
        'ORDER BY candidate."measured_at" DESC NULLS LAST, '
        'candidate."created_at" DESC, candidate."id" DESC LIMIT 1)) '
        'WHERE (target."project_id" IS NOT DISTINCT FROM %s) '
        'AND target."provider_id" = %s AND target."condition_hash" = %s'
    )
    recompute_params = (
        project_id, provider_id, condition_hash,
        project_id, provider_id, condition_hash,
    )
    statements.append((recompute_sql, recompute_params))

    return statements


def build_postgres_results_projection_update(
    *,
    provider_id: str,
    provider_result_id: str,
    verdict: Optional[str],
    result_json: str,
    operator: Optional[str],
    measured_at: Optional[str],
    condition_hash: str,
    session_id: Optional[str] = None,
    attempt_number: Optional[int] = None,
) -> tuple[str, tuple]:
    """Build the same-transaction result projection update.

    Legacy callers retain the original value-driven statement. The provider
    scoped worker supplies ``session_id`` and ``attempt_number``; that path
    reads the values back from the database-owned latest attempt instead of
    trusting the replay payload. This makes an out-of-order arrival and a
    replay with altered payloads unable to move the projection backwards.
    """
    if not provider_id:
        raise ValueError('provider_id is required')
    if not provider_result_id:
        raise ValueError('provider_result_id is required')
    if session_id is not None and attempt_number is not None:
        statement = (
            'UPDATE "measurement_results" AS result '
            'SET "verdict" = latest_attempt."verdict", '
            '    "result_json" = latest_attempt."result_json", '
            '    "operator" = latest_attempt."operator", '
            '    "measured_at" = latest_attempt."measured_at", '
            '    "condition_hash" = latest_attempt."condition_hash" '
            'FROM "measurement_attempts" AS latest_attempt '
            'WHERE result."provider_id" = %s '
            'AND result."provider_result_id" = %s '
            'AND latest_attempt."measurement_result_id" = result."id" '
            'AND latest_attempt."provider_id" = result."provider_id" '
            'AND latest_attempt."session_id" = %s '
            'AND latest_attempt."condition_hash" = %s '
            'AND latest_attempt."attempt_number" = %s '
            'AND latest_attempt."is_latest" = true '
            'AND latest_attempt."status" = \'completed\''
        )
        parameters = (provider_id, provider_result_id, session_id, condition_hash, attempt_number)
        return statement, parameters
    statement = (
        'UPDATE "measurement_results" '
        'SET "verdict" = %s, "result_json" = %s, "operator" = %s, '
        '    "measured_at" = %s, "condition_hash" = %s '
        'WHERE "provider_id" = %s AND "provider_result_id" = %s'
    )
    parameters = (verdict, result_json, operator, measured_at, condition_hash,
                  provider_id, provider_result_id)
    return statement, parameters


def _validate_table(table: str) -> None:
    _validate_identifier(table)
    if table not in IDEMPOTENCY_KEYS_BY_TABLE:
        raise ValueError(f'unsupported ingestion table: {table}')


def _validate_identifier(value: str) -> None:
    if not _IDENTIFIER.fullmatch(str(value or '')):
        raise ValueError(f'unsafe SQL identifier: {value}')


def _quote(value: str) -> str:
    _validate_identifier(value)
    return f'"{value}"'
