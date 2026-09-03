"""PostgreSQL adapter for provider-scoped result selection.

This module contains only generic central columns and opaque JSON. It imports no
provider registry or provider/headless module. All mutations are append-only and
the select/clear CAS is checked inside one SERIALIZABLE transaction.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional

from fcc_test_kernel.application.central_contract.pagination import (
    CursorError,
    CursorValueDomain,
    decode_cursor,
    encode_cursor,
)
from domain.ports.output.central_result_selection_port import (
    CentralResultSelectionError,
    CentralResultSelectionPort,
    SelectionBackendError,
    SelectionCandidateNotFoundError,
    SelectionCrossScopeError,
    SelectionProviderNotFoundError,
    SelectionRevisionConflictError,
)
from domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'SELECTION_ATTEMPT_COLUMNS',
    'CANDIDATE_ATTEMPT_COLUMNS',
    'EFFECTIVE_RESULTS_QUERY_SQL',
    'CANDIDATE_ATTEMPTS_QUERY_SQL',
    'SELECTED_SOURCE_QUERY_SQL',
    'SELECTED_SOURCE_COLUMNS',
    'EFFECTIVE_CURSOR_DOMAINS',
    'CANDIDATE_CURSOR_DOMAINS',
    'PostgresCentralResultSelectionAdapter',
]


SELECTION_ATTEMPT_COLUMNS: tuple[str, ...] = (
    'attempt_id', 'project_id', 'provider_id', 'condition_hash',
    'session_id', 'provider_session_id', 'sample_id', 'chamber_id',
    'operator', 'measured_at', 'created_at', 'verdict', 'status',
    'attempt_number', 'result_json', 'provenance_json',
    'selection_source', 'selected_attempt_id', 'selection_revision',
)

CANDIDATE_ATTEMPT_COLUMNS: tuple[str, ...] = (
    'attempt_id', 'project_id', 'provider_id', 'condition_hash',
    'session_id', 'operator', 'measured_at', 'created_at', 'verdict', 'status',
    'attempt_number', 'result_json', 'provenance_json',
    'provider_session_id', 'sample_id', 'chamber_id',
)

# This is the single row contract consumed by reference publication.  Keep
# the order explicit because lightweight cursor fakes used by unit tests may
# not expose cursor.description; the real PostgreSQL query aliases every
# column to the same names.
SELECTED_SOURCE_COLUMNS: tuple[str, ...] = (
    'selection_event_id', 'selection_action', 'selection_revision',
    'attempt_id', 'project_id', 'provider_id', 'condition_hash', 'session_id',
    'provider_session_id', 'sample_id', 'chamber_id', 'operator',
    'measured_at', 'created_at', 'verdict', 'status', 'attempt_number',
    'result_json', 'provenance_json', 'test_name', 'technology', 'margin',
    'run_id', 'idempotency_key', 'recorded_by',
)

_CANDIDATE_COLUMNS = ', '.join(
    (
        'p."provider_id" AS "provider_id"'
        if column == 'provider_id'
        else f'a."{column}" AS "{("attempt_id" if column == "id" else column)}"'
    )
    for column in (
        'id', 'project_id', 'provider_id', 'condition_hash', 'session_id',
        'operator', 'measured_at', 'created_at', 'verdict', 'status',
        'attempt_number', 'result_json', 'provenance_json',
    )
)
_RANKED_COLUMNS = f'{_CANDIDATE_COLUMNS}, a."provider_id" AS "provider_uuid"'
_SESSION_COLUMNS = (
    's."provider_session_id" AS "provider_session_id", '
    's."sample_id" AS "sample_id", s."chamber_id" AS "chamber_id"'
)

# The effective page is keyed by the text condition axis. Candidate paging has
# an explicit NULL rank followed by a nullable measured timestamp, a typed
# created timestamp, and the UUID tiebreaker. JSON null is the only SQL-NULL
# representation; an empty string is rejected by the cursor boundary.
EFFECTIVE_CURSOR_DOMAINS: tuple[CursorValueDomain, ...] = (
    CursorValueDomain.TEXT,
)
CANDIDATE_CURSOR_DOMAINS: tuple[CursorValueDomain, ...] = (
    CursorValueDomain.INTEGER,
    CursorValueDomain.NULLABLE_TIMESTAMP,
    CursorValueDomain.TIMESTAMP,
    CursorValueDomain.UUID,
)

_PARTITION_LOCK_SQL = (
    'SELECT pg_advisory_xact_lock(hashtextextended('
    'concat_ws(chr(31), %s::text, %s::text, %s::text), 0))'
)

# Exact provider/condition partition is always present in the WHERE clause.
# The cursor compares the same NULLS-LAST normalized recency key; ``limit + 1``
# is used only to determine whether another bounded page exists.
CANDIDATE_ATTEMPTS_QUERY_SQL = (
    f'SELECT {_CANDIDATE_COLUMNS}, {_SESSION_COLUMNS} '
    'FROM "measurement_attempts" a '
    'JOIN "test_sessions" s ON s."id" = a."session_id" '
    'AND s."provider_id" = a."provider_id" '
    'AND s."project_id" IS NOT DISTINCT FROM a."project_id" '
    'JOIN "providers" p ON p."id" = a."provider_id" AND p."enabled" = TRUE '
    'WHERE a."project_id" IS NOT DISTINCT FROM %s '
    'AND a."provider_id" = %s AND a."condition_hash" = %s '
    'AND a."status" = \'completed\' '
    'ORDER BY a."measured_at" DESC NULLS LAST, a."created_at" DESC, a."id" DESC '
    'LIMIT %s'
)

EFFECTIVE_RESULTS_QUERY_SQL = (
    # Page the distinct condition axis before ranking attempts or joining the
    # append-only event ledger.  The old shape ranked every completed attempt
    # in the project and only applied LIMIT after all of that work; on the
    # contracted 96,000-attempt fixture that made the feature cost grow with
    # the entire history instead of the requested keyset page.
    'WITH condition_page AS MATERIALIZED ( '
    'SELECT DISTINCT a."condition_hash" '
    'FROM "measurement_attempts" a '
    'WHERE a."project_id" IS NOT DISTINCT FROM %s '
    'AND a."provider_id" = %s AND a."status" = \'completed\' '
    'ORDER BY a."condition_hash" ASC LIMIT %s '
    '), ranked AS ( '
    f'SELECT DISTINCT ON (a."condition_hash") {_RANKED_COLUMNS}, {_SESSION_COLUMNS} '
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
    '), events_ranked AS ( '
    'SELECT DISTINCT ON (e."condition_hash") e."condition_hash", '
    'e."action", e."revision", e."attempt_id" '
    'FROM condition_page page '
    'JOIN "project_result_selection_events" e '
    'ON e."condition_hash" = page."condition_hash" '
    'WHERE e."project_id" IS NOT DISTINCT FROM %s '
    'AND e."provider_id" = %s '
    'ORDER BY e."condition_hash" ASC, e."revision" DESC '
    '), effective AS ( '
    'SELECT r.*, '
    'CASE WHEN e."action" = \'selected\' AND pinned."id" IS NOT NULL '
    'THEN pinned."id" ELSE r."attempt_id" END AS "effective_id", '
    'CASE WHEN e."action" = \'selected\' AND pinned."id" IS NOT NULL '
    'THEN \'manual\' ELSE \'latest\' END AS "effective_source", '
    'COALESCE(e."revision", 0) AS "effective_revision" '
    'FROM ranked r '
    'LEFT JOIN events_ranked e ON e."condition_hash" = r."condition_hash" '
    'LEFT JOIN LATERAL ( '
    'SELECT pinned."id" FROM "measurement_attempts" pinned '
    'WHERE pinned."id" = e."attempt_id" '
    'AND pinned."project_id" IS NOT DISTINCT FROM r."project_id" '
    'AND pinned."provider_id" = r."provider_uuid" '
    'AND pinned."condition_hash" = r."condition_hash" '
    'AND pinned."status" = \'completed\' LIMIT 1 '
    ') pinned ON TRUE '
    ') SELECT selected."id" AS "attempt_id", selected."project_id", '
    'effective."provider_id" AS "provider_id", selected."condition_hash", selected."session_id", '
    'selected."provider_session_id", selected."sample_id", '
    'selected."chamber_id", '
    'selected."operator", selected."measured_at", selected."created_at", '
    'selected."verdict", selected."status", selected."attempt_number", '
    'selected."result_json", selected."provenance_json", '
    'effective."effective_source" AS "selection_source", '
    'CASE WHEN effective."effective_source" = \'manual\' THEN effective."effective_id" ELSE NULL END '
    'AS "selected_attempt_id", effective."effective_revision" AS "selection_revision" '
    'FROM effective '
    'JOIN LATERAL ( '
    'SELECT selected."id", selected."project_id", selected."condition_hash", '
    'selected."session_id", selected."operator", selected."measured_at", '
    'selected."created_at", selected."verdict", selected."status", '
    'selected."attempt_number", selected."result_json", selected."provenance_json", '
    'selected_session."provider_session_id", selected_session."sample_id", '
    'selected_session."chamber_id" '
    'FROM "measurement_attempts" selected '
    'JOIN "test_sessions" selected_session ON selected_session."id" = selected."session_id" '
    'AND selected_session."provider_id" = selected."provider_id" '
    'AND selected_session."project_id" IS NOT DISTINCT FROM selected."project_id" '
    'WHERE selected."id" = effective."effective_id" LIMIT 1 '
    ') selected ON TRUE '
    'ORDER BY selected."condition_hash" ASC'
)

# The selected source is resolved from the current effective event, not from
# the latest attempt and not from a caller-supplied attempt id.  The
# ``NOT EXISTS`` predicate makes a clear or later selection event remove the
# source.  Joining the completed attempt, its session, and the enabled
# provider in one statement prevents a four-column event-only row from being
# mistaken for a publishable provider result.
SELECTED_SOURCE_QUERY_SQL = (
    'SELECT e."id" AS "selection_event_id", e."action" AS "selection_action", '
    'e."revision" AS "selection_revision", a."id" AS "attempt_id", '
    'a."project_id" AS "project_id", p."provider_id" AS "provider_id", '
    'a."condition_hash" AS "condition_hash", a."session_id" AS "session_id", '
    's."provider_session_id" AS "provider_session_id", '
    's."sample_id" AS "sample_id", s."chamber_id" AS "chamber_id", '
    'a."operator" AS "operator", a."measured_at" AS "measured_at", '
    'a."created_at" AS "created_at", a."verdict" AS "verdict", '
    'a."status" AS "status", a."attempt_number" AS "attempt_number", '
    'a."result_json" AS "result_json", a."provenance_json" AS "provenance_json", '
    'a."test_name" AS "test_name", a."technology" AS "technology", '
    'a."margin" AS "margin", a."run_id" AS "run_id", '
    'a."idempotency_key" AS "idempotency_key", a."recorded_by" AS "recorded_by" '
    'FROM "project_result_selection_events" e '
    'JOIN "measurement_attempts" a ON a."id" = e."attempt_id" '
    'AND a."project_id" IS NOT DISTINCT FROM e."project_id" '
    'AND a."provider_id" = e."provider_id" '
    'AND a."condition_hash" = e."condition_hash" '
    'AND a."status" = \'completed\' '
    'JOIN "test_sessions" s ON s."id" = a."session_id" '
    'AND s."provider_id" = a."provider_id" '
    'AND s."project_id" IS NOT DISTINCT FROM a."project_id" '
    'JOIN "providers" p ON p."id" = a."provider_id" AND p."enabled" = TRUE '
    'WHERE e."project_id" IS NOT DISTINCT FROM %s '
    'AND e."provider_id" = %s AND e."condition_hash" = %s '
    'AND e."action" = \'selected\' '
    'AND NOT EXISTS ( '
    'SELECT 1 FROM "project_result_selection_events" later '
    'WHERE later."project_id" IS NOT DISTINCT FROM e."project_id" '
    'AND later."provider_id" = e."provider_id" '
    'AND later."condition_hash" = e."condition_hash" '
    'AND later."revision" > e."revision" '
    ') '
    'ORDER BY e."revision" DESC, e."created_at" DESC, e."id" DESC LIMIT 1'
)


class PostgresCentralResultSelectionAdapter(CentralResultSelectionPort):
    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def list_effective_results(
        self, project_id: str, provider_id: str, *, limit: int,
        cursor: Optional[str] = None,
    ) -> Mapping:
        # Effective summary is set-based and bounded. Cursor is an opaque
        # condition-hash position; the first version intentionally pages on the
        # unique condition axis instead of fetching all attempts into Python.
        # The provider appears in the bounded condition page, ranked attempt
        # join, and event join.  Keep the UUID substitution positions explicit
        # because the public API accepts the natural provider key.
        params: list = [project_id, provider_id, limit + 1,
                        project_id, provider_id, project_id, provider_id]
        sql = EFFECTIVE_RESULTS_QUERY_SQL
        provider_positions = (1, 4, 6)
        if cursor:
            (last_condition,) = decode_cursor(
                cursor, arity=1, domains=EFFECTIVE_CURSOR_DOMAINS,
            )
            sql = sql.replace(
                'AND a."provider_id" = %s AND a."status" = \'completed\' '
                'ORDER BY a."condition_hash" ASC LIMIT %s',
                'AND a."provider_id" = %s AND a."status" = \'completed\' '
                'AND a."condition_hash" > %s '
                'ORDER BY a."condition_hash" ASC LIMIT %s',
                1,
            )
            # The keyset predicate is in condition_page, before its LIMIT and
            # before the repeated scope values used by ranked/events.
            params = [project_id, provider_id, last_condition, limit + 1,
                      project_id, provider_id, project_id, provider_id]
            provider_positions = (1, 5, 7)
        rows = self._query(
            sql, tuple(params), columns=SELECTION_ATTEMPT_COLUMNS,
            provider_id=provider_id, provider_positions=provider_positions,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            'items': rows,
            'next_cursor': encode_cursor([str(rows[-1].get('condition_hash'))])
            if has_more and rows else None,
        }

    def list_attempts(
        self, project_id: str, provider_id: str, condition_hash: str, *,
        limit: int, cursor: Optional[str] = None,
    ) -> Mapping:
        sql = CANDIDATE_ATTEMPTS_QUERY_SQL
        params: list = [project_id, provider_id, condition_hash]
        if cursor:
            null_rank, measured_at, created_at, attempt_id = decode_cursor(
                cursor, arity=4, domains=CANDIDATE_CURSOR_DOMAINS,
            )
            if null_rank not in {'0', '1'}:
                raise CursorError('invalid pagination cursor: measured timestamp null rank')
            if (null_rank == '1') != (measured_at is None):
                raise CursorError(
                    'invalid pagination cursor: null rank does not match measured timestamp'
                )
            sql = sql.replace(
                'AND a."status" = \'completed\' ',
                'AND a."status" = \'completed\' AND ('
                '(CASE WHEN a."measured_at" IS NULL THEN 1 ELSE 0 END > %s) OR '
                '((CASE WHEN a."measured_at" IS NULL THEN 1 ELSE 0 END = %s) AND ('
                'a."measured_at" < %s OR '
                '(a."measured_at" IS NOT DISTINCT FROM %s AND ('
                'a."created_at" < %s OR '
                '(a."created_at" = %s AND a."id" < %s)))))) ',
            )
            params.extend([
                int(null_rank), int(null_rank), measured_at, measured_at,
                created_at, created_at, attempt_id,
            ])
        params.append(limit + 1)
        rows = self._query(
            sql, tuple(params), columns=CANDIDATE_ATTEMPT_COLUMNS,
            provider_id=provider_id, provider_positions=(1,),
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            measured_at = last.get('measured_at')
            next_cursor = encode_cursor([
                '1' if measured_at is None else '0',
                measured_at,
                last.get('created_at'),
                last.get('attempt_id'),
            ])
        return {'items': rows, 'next_cursor': next_cursor}

    def append_selection_event(self, **record) -> Mapping:
        connection = self._open()
        try:
            cursor = connection.cursor()
            try:
                self._set_serializable(cursor)
                provider_uuid = self._resolve_provider_id(
                    cursor, record['provider_id'],
                )
                self._lock_partition(
                    cursor,
                    record['project_id'], provider_uuid, record['condition_hash'],
                )
                latest = self._fetch_one(
                    cursor,
                    'SELECT "revision", "id" FROM "project_result_selection_events" '
                    'WHERE "project_id" IS NOT DISTINCT FROM %s AND "provider_id" = %s '
                    'AND "condition_hash" = %s ORDER BY "revision" DESC LIMIT 1 FOR UPDATE',
                    (record['project_id'], provider_uuid, record['condition_hash']),
                    ('revision', 'id'),
                )
                current_revision = int(latest.get('revision') or 0) if latest else 0
                expected = int(record['expected_revision'])
                if current_revision != expected:
                    raise SelectionRevisionConflictError('selection revision is stale')
                if record['action'] == 'selected':
                    source = self._fetch_one(
                        cursor,
                        'SELECT a."id" AS "attempt_id" '
                        'FROM "measurement_attempts" a '
                        'JOIN "test_sessions" s ON s."id" = a."session_id" '
                        'AND s."provider_id" = a."provider_id" '
                        'AND s."project_id" IS NOT DISTINCT FROM a."project_id" '
                        'WHERE a."id" = %s '
                        'AND a."project_id" IS NOT DISTINCT FROM %s '
                        'AND a."provider_id" = %s '
                        'AND a."condition_hash" = %s '
                        'AND a."status" = \'completed\' '
                        'AND s."project_id" IS NOT DISTINCT FROM %s '
                        'AND s."provider_id" = %s',
                        (
                            record['attempt_id'], record['project_id'], provider_uuid,
                            record['condition_hash'], record['project_id'], provider_uuid,
                        ),
                        ('attempt_id',),
                    )
                    if source is None:
                        raise SelectionCandidateNotFoundError('selection candidate not found')
                revision = expected + 1
                values = (
                    record['event_id'], record['project_id'], provider_uuid,
                    record['condition_hash'], record['action'], record['attempt_id'],
                    revision, latest.get('id') if latest else None, expected,
                    record['actor_subject'], record.get('reason'),
                )
                try:
                    inserted = self._fetch_one(
                        cursor,
                        'INSERT INTO "project_result_selection_events" '
                        '("id", "project_id", "provider_id", "condition_hash", "action", '
                        '"attempt_id", "revision", "predecessor_event_id", "expected_revision", '
                        '"actor_subject", "occurred_at", "reason") '
                        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s) '
                        'RETURNING "occurred_at"',
                        values,
                        ('occurred_at',),
                    )
                    if inserted is None:
                        raise SelectionBackendError(
                            'central selection insert did not return occurred_at'
                        )
                except Exception as exc:  # noqa: BLE001
                    # The partition advisory lock makes the empty-history case
                    # serialize before the first read. Keep the unique index and
                    # SERIALIZABLE failure as a second backstop: either race is
                    # a stale command (409), never an opaque 503.
                    if self._is_concurrency_conflict(exc):
                        raise SelectionRevisionConflictError(
                            'selection revision is stale'
                        ) from exc
                    raise
                try:
                    connection.commit()
                except Exception as exc:  # noqa: BLE001
                    if self._is_concurrency_conflict(exc):
                        raise SelectionRevisionConflictError(
                            'selection revision is stale'
                        ) from exc
                    raise SelectionBackendError(
                        f'central selection commit failed: {exc}'
                    ) from exc
                return {
                    'id': record['event_id'],
                    'project_id': record['project_id'],
                    'provider_id': record['provider_id'],
                    'condition_hash': record['condition_hash'],
                    'action': record['action'],
                    'attempt_id': record['attempt_id'],
                    'revision': revision,
                    'expected_revision': expected,
                    'actor_subject': record['actor_subject'],
                    'occurred_at': inserted['occurred_at'],
                    'reason': record.get('reason'),
                }
            finally:
                cursor.close()
        except (SelectionRevisionConflictError, SelectionCandidateNotFoundError,
                SelectionCrossScopeError, SelectionProviderNotFoundError,
                SelectionBackendError):
            self._rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rollback(connection)
            if self._is_concurrency_conflict(exc):
                raise SelectionRevisionConflictError(
                    'selection revision is stale'
                ) from exc
            raise SelectionBackendError(f'central selection write failed: {exc}') from exc
        finally:
            self._close(connection)

    def selected_source(
        self, project_id: str, provider_id: str, condition_hash: str,
    ) -> Optional[Mapping]:
        rows = self._query(
            SELECTED_SOURCE_QUERY_SQL,
            (project_id, provider_id, condition_hash),
            columns=SELECTED_SOURCE_COLUMNS,
            provider_id=provider_id, provider_positions=(1,),
        )
        if not rows:
            return None
        source = rows[0]
        if set(source) != set(SELECTED_SOURCE_COLUMNS):
            raise SelectionBackendError(
                'selected source row does not satisfy the full event-attempt-session '
                'provenance contract'
            )
        return source

    def _query(
        self, sql: str, params: tuple, *, columns: Optional[tuple[str, ...]] = None,
        provider_id: Optional[str] = None,
        provider_positions: tuple[int, ...] = (),
    ) -> list[dict]:
        connection = self._open()
        try:
            cursor = connection.cursor()
            try:
                if provider_id is not None:
                    provider_uuid = self._resolve_provider_id(cursor, provider_id)
                    bound_params = list(params)
                    for position in provider_positions:
                        bound_params[position] = provider_uuid
                    params = tuple(bound_params)
                cursor.execute(sql, params)
                rows = list(cursor.fetchall())
                descriptions = getattr(cursor, 'description', None)
                if descriptions:
                    columns = tuple(getattr(item, 'name', item[0]) for item in descriptions)
                else:
                    # SELECT aliases in the effective query are stable; candidate
                    # rows are normalized below when a DB wrapper omits metadata.
                    columns = columns or SELECTION_ATTEMPT_COLUMNS
                return [dict(zip(columns, row)) for row in rows]
            finally:
                cursor.close()
        except CentralResultSelectionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SelectionBackendError(f'central selection read failed: {exc}') from exc
        finally:
            self._close(connection)

    def _open(self):
        try:
            return self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise SelectionBackendError(f'central selection connection failed: {exc}') from exc

    @staticmethod
    def _fetch_one(cursor, sql: str, params: tuple, columns: tuple[str, ...]) -> Optional[dict]:
        cursor.execute(sql, params)
        rows = list(cursor.fetchall())
        return dict(zip(columns, rows[0])) if rows else None

    @staticmethod
    def _resolve_provider_id(cursor, provider_id: str):
        """Resolve the public natural key before it reaches a UUID FK query."""
        row = PostgresCentralResultSelectionAdapter._fetch_one(
            cursor,
            'SELECT "id" FROM "providers" '
            'WHERE "provider_id" = %s AND "enabled" = TRUE',
            (provider_id,),
            ('id',),
        )
        if row is None:
            raise SelectionProviderNotFoundError(
                'provider is unknown or disabled centrally'
            )
        return row['id']

    @staticmethod
    def _set_serializable(cursor) -> None:
        try:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE', ())
        except Exception as exc:  # noqa: BLE001
            # This adapter is PostgreSQL-only. Falling through to a weaker
            # isolation level would make the CAS contract false while looking
            # healthy to callers.
            raise SelectionBackendError(
                'central selection requires PostgreSQL SERIALIZABLE isolation'
            ) from exc

    @staticmethod
    def _lock_partition(cursor, project_id: str, provider_id: str, condition_hash: str) -> None:
        """Lock a partition even when it has no selection-event row yet."""
        cursor.execute(
            _PARTITION_LOCK_SQL,
            (project_id, provider_id, condition_hash),
        )

    @staticmethod
    def _is_concurrency_conflict(exc: BaseException) -> bool:
        """Recognize PostgreSQL uniqueness/serialization races at the CAS write."""
        candidates = [
            getattr(exc, 'sqlstate', None),
            getattr(exc, 'pgcode', None),
            getattr(getattr(exc, 'diag', None), 'sqlstate', None),
        ]
        return any(str(code or '').upper() in {'23505', '40001'} for code in candidates)

    @staticmethod
    def _rollback(connection) -> None:
        rollback = getattr(connection, 'rollback', None)
        if callable(rollback):
            try:
                rollback()
            except Exception:
                pass

    @staticmethod
    def _close(connection) -> None:
        close = getattr(connection, 'close', None)
        if callable(close):
            close()
