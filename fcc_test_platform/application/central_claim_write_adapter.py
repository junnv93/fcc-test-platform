"""Central PostgreSQL claim-ledger write adapter (FE-P3-write, 2026-05-27).

``PostgresCentralClaimWriteAdapter`` implements ``CentralClaimWritePort`` against
the central ``claim_events`` append-only ledger + the ``active_claims`` view
(docs/platform/central_db_schema.v1.json SSOT). It is the single write path for
FE-P3 claim acquire/release.

Design (mirrors ``PostgresCentralReadAdapter`` + ``PostgresIngestionWriter``):

- **injected ``connection_factory``** (``() -> DbConnection``). The concrete
  psycopg connection is built lazily by the composition root; this module never
  imports a PostgreSQL driver (frozen-exe safe — enforced by
  ``tests/test_platform_claim_write_fe_p3.py``).
- **append-only**: the only write verb is ``INSERT INTO claim_events``. There is
  no UPDATE/DELETE of historical rows — a release is a new ``released`` row, not a
  mutation of the ``acquired`` row.
- **atomic check-and-append**: ``acquire_claim_if_unclaimed`` /
  ``release_open_claim`` run their ``active_claims`` lookup and the conditional
  INSERT inside ONE transaction with best-effort SERIALIZABLE isolation, so two
  concurrent writers cannot both observe "free"/"open" and both append. On
  PostgreSQL the loser's commit fails with a serialization error and the caller
  retries into a conflict; on a backend that rejects ``SET TRANSACTION ISOLATION``
  (the SQLite test fixture) the single connection still serializes writes.
- **``%s`` paramstyle** (psycopg) — every value is a bound parameter (never
  interpolated) so the ledger write is injection-safe.
- **loud-fail**: a connection/query failure raises ``ClaimWriteError`` (never a
  silent no-op that would mask a dropped lock).
- column SSOT: ``CLAIM_EVENT_COLUMNS`` mirrors the ledger table columns; the
  open-claim lookup reuses ``ACTIVE_CLAIM_COLUMNS`` / ``ACTIVE_CLAIMS_VIEW`` from
  the read adapter so both surfaces agree on the view shape.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional

from fcc_test_platform.application.central_read_adapter import (
    ACTIVE_CLAIM_COLUMNS,
    ACTIVE_CLAIMS_VIEW,
)
from domain.ports.output.central_audit_write_port import CentralAuditWritePort
from domain.ports.output.central_claim_write_port import ClaimWriteError
from domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'CLAIM_EVENTS_TABLE',
    'CLAIM_EVENT_COLUMNS',
    'INSERT_CLAIM_EVENT_SQL',
    'OPEN_CLAIM_BY_CONDITION_SQL',
    'OPEN_CLAIM_BY_ID_SQL',
    'PostgresCentralClaimWriteAdapter',
]


CLAIM_EVENTS_TABLE = 'claim_events'

#: Bounded retries when a SERIALIZABLE commit aborts on a concurrent writer
#: (SQLSTATE 40001). Small — the contention window is one INSERT, and each retry
#: re-reads the now-committed winner, so it resolves on the first retry in
#: practice (the extra headroom covers a 3-way pile-up).
_MAX_SERIALIZATION_RETRIES = 3


class _SerializationRetry(Exception):
    """Internal sentinel — a SERIALIZABLE commit hit SQLSTATE 40001; retry."""

#: Ledger columns written on every event (docs/platform/central_db_schema.v1.json
#: claim_events). Order defines the INSERT column list + the positional params.
CLAIM_EVENT_COLUMNS: tuple[str, ...] = (
    'id',
    'claim_id',
    'project_id',
    'technology',
    'condition_hash',
    'operator',
    'action',
    'reason',
    'occurred_at',
    'expires_at',
    'session_id',
    'created_at',
)


def _build_insert(table: str, columns: tuple[str, ...]) -> str:
    column_sql = ', '.join(f'"{column}"' for column in columns)
    placeholders = ', '.join(['%s'] * len(columns))
    return f'INSERT INTO "{table}" ({column_sql}) VALUES ({placeholders})'


def _open_claim_select(filter_column: str) -> str:
    """``SELECT <active_claim cols> FROM active_claims WHERE project_id = %s AND
    "<filter_column>" = %s`` — read-only lookup of the still-open claim. The
    ``active_claims`` view already dedups to the latest still-held claim per
    (project, condition_hash), so a returned row means "currently held"."""
    select_cols = ', '.join(f'"{column}"' for column in ACTIVE_CLAIM_COLUMNS)
    return (
        f'SELECT {select_cols} FROM "{ACTIVE_CLAIMS_VIEW}" '
        f'WHERE "project_id" = %s AND "{filter_column}" = %s'
    )


#: append-only INSERT (no ON CONFLICT — each event is a fresh-id ledger row).
INSERT_CLAIM_EVENT_SQL = _build_insert(CLAIM_EVENTS_TABLE, CLAIM_EVENT_COLUMNS)
#: open-claim lookups for the two atomic guards (acquire conflict / release pairing).
OPEN_CLAIM_BY_CONDITION_SQL = _open_claim_select('condition_hash')
OPEN_CLAIM_BY_ID_SQL = _open_claim_select('claim_id')


class PostgresCentralClaimWriteAdapter:
    """``CentralClaimWritePort`` over a central PostgreSQL connection factory.

    FE-P8 (2026-05-28): accepts an optional ``audit_writer`` (CentralAuditWritePort).
    When set AND the caller passes ``audit_record=``, the audit INSERT joins
    the SAME transaction as the primary claim INSERT — atomic with the
    audited event (a failed audit rolls the claim back). When audit_writer
    is None (existing tests / bare construction), the legacy un-audited path
    runs (no audit row written). Production composition root always sets
    audit_writer + the ClaimWriteService always supplies audit_record so the
    in-process invariant is "every API-driven claim write is audited".
    """

    def __init__(
        self,
        connection_factory: Callable[[], DbConnection],
        audit_writer: Optional[CentralAuditWritePort] = None,
    ) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory
        self._audit = audit_writer

    def acquire_claim_if_unclaimed(
        self,
        record: Mapping,
        audit_record: Optional[Mapping] = None,
    ) -> Optional[dict]:
        project_id = record.get('project_id')
        condition_hash = record.get('condition_hash')
        if not project_id or not condition_hash:
            raise ValueError('acquire record requires project_id + condition_hash')
        values = tuple(record.get(column) for column in CLAIM_EVENT_COLUMNS)

        def _txn(cursor) -> Optional[dict]:
            existing = self._fetch_one(
                cursor, OPEN_CLAIM_BY_CONDITION_SQL, (project_id, condition_hash),
            )
            if existing is not None:
                # Contended — do NOT insert (caller decides; no audit either
                # because no state changed).
                return existing
            cursor.execute(INSERT_CLAIM_EVENT_SQL, values)
            self._maybe_audit(cursor, audit_record)
            return None

        return self._in_transaction(_txn)

    def release_open_claim(
        self,
        project_id: str,
        claim_id: str,
        *,
        event_id: str,
        operator: Optional[str],
        reason: Optional[str],
        occurred_at: str,
        created_at: str,
        action: str = 'released',
        audit_record: Optional[Mapping] = None,
    ) -> Optional[dict]:
        def _txn(cursor) -> Optional[dict]:
            open_claim = self._fetch_one(
                cursor, OPEN_CLAIM_BY_ID_SQL, (project_id, claim_id),
            )
            if open_claim is None:
                return None  # no open claim with this id → caller raises pairing error
            release_record = {
                'id': event_id,
                'claim_id': claim_id,
                'project_id': project_id,
                'technology': open_claim.get('technology'),
                'condition_hash': open_claim.get('condition_hash'),
                'operator': operator if operator else open_claim.get('operator'),
                'action': action,
                'reason': reason,
                'occurred_at': occurred_at,
                'expires_at': None,
                'session_id': open_claim.get('session_id'),
                'created_at': created_at,
            }
            values = tuple(release_record[column] for column in CLAIM_EVENT_COLUMNS)
            cursor.execute(INSERT_CLAIM_EVENT_SQL, values)
            self._maybe_audit(cursor, audit_record)
            return release_record

        return self._in_transaction(_txn)

    def _maybe_audit(self, cursor, audit_record: Optional[Mapping]) -> None:
        """INSERT the audit row inside the open transaction when audit is wired.

        Both ``audit_writer`` (composition) and ``audit_record`` (per-call)
        must be present — either being missing skips the audit. AuditWriteError
        propagates so the surrounding ``_in_transaction`` rolls the primary
        write back (atomicity contract).
        """
        if self._audit is None or audit_record is None:
            return
        self._audit.append_event_in_transaction(cursor, audit_record)

    # ── transaction plumbing ────────────────────────────────────────────────

    def _in_transaction(self, body: Callable[[object], Optional[dict]]) -> Optional[dict]:
        """Run ``body`` in one SERIALIZABLE transaction, retrying on a serialization
        failure.

        Under SERIALIZABLE, two concurrent acquires of a free condition both read
        "unclaimed" then INSERT; PostgreSQL aborts the loser's commit with SQLSTATE
        40001. Rather than surfacing that as a 503, we retry (a fresh transaction):
        the loser now reads the winner's committed claim → the conditional INSERT is
        skipped → the caller (ClaimWriteService) maps it to a 409 conflict /
        idempotent re-acquire. This is the correct "caller retries" pattern (mirrors
        PostgresIngestionWriter Rule 1). Bounded by ``_MAX_SERIALIZATION_RETRIES``.
        """
        last_exc: Optional[Exception] = None
        for _attempt in range(_MAX_SERIALIZATION_RETRIES):
            try:
                return self._run_once(body)
            except _SerializationRetry as exc:
                last_exc = exc.__cause__ or exc
                continue
        # Exhausted retries — contention never cleared; surface loud.
        raise ClaimWriteError(
            f'central claim write failed after {_MAX_SERIALIZATION_RETRIES} '
            f'serialization retries: {last_exc}'
        ) from last_exc

    def _run_once(self, body: Callable[[object], Optional[dict]]) -> Optional[dict]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 — wrap as loud ClaimWriteError
            raise ClaimWriteError(f'central claim write connection failed: {exc}') from exc
        try:
            cursor = connection.cursor()
            try:
                _set_serializable_best_effort(cursor)
                result = body(cursor)
            finally:
                cursor.close()
            connection.commit()
            return result
        except ClaimWriteError:
            _safe_rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(connection)
            if _is_serialization_error(exc):
                # Retryable — re-evaluate against the now-committed winner.
                raise _SerializationRetry from exc
            raise ClaimWriteError(f'central claim write failed: {exc}') from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()

    @staticmethod
    def _fetch_one(cursor, statement: str, params: tuple) -> Optional[dict]:
        # fetchall()[0] (not fetchone) to match PostgresCentralReadAdapter._query
        # + the SQLite test fixture cursor, which only implements fetchall.
        cursor.execute(statement, params)
        rows = list(cursor.fetchall())
        if not rows:
            return None
        return dict(zip(ACTIVE_CLAIM_COLUMNS, rows[0]))


def _set_serializable_best_effort(cursor) -> None:
    """Issue ``SET TRANSACTION ISOLATION LEVEL SERIALIZABLE`` best-effort.

    On PostgreSQL this makes the check-and-append race-safe (a concurrent
    acquire/release aborts on commit with a serialization error). A backend that
    rejects the statement (the SQLite test fixture) raises — swallowed here
    because that backend serializes the single connection's writes anyway, so the
    in-transaction check-then-insert is still atomic. Mirrors the duck-typed
    ``autocommit`` toggle in ``PostgresIngestionWriter``.
    """
    try:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE', ())
    except Exception:  # noqa: BLE001 — backend without SET TRANSACTION (e.g. SQLite)
        pass


def _is_serialization_error(exc: Exception) -> bool:
    """True for a PostgreSQL serialization failure (SQLSTATE 40001), duck-typed so
    this module imports no PostgreSQL driver. psycopg/psycopg2 expose ``sqlstate``
    / ``pgcode``; as a last resort the exception class name is matched."""
    code = getattr(exc, 'sqlstate', None) or getattr(exc, 'pgcode', None)
    if code == '40001':
        return True
    return 'serializ' in type(exc).__name__.lower()


def _safe_rollback(connection) -> None:
    rollback = getattr(connection, 'rollback', None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001 — never mask the original error
            pass
