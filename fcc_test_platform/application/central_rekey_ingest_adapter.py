"""Central PostgreSQL re-key ingestion adapter (ADR-0005 B-1, 2026-06-01).

``PostgresCentralRekeyIngestAdapter`` implements ``CentralRekeyIngestPort``: it
applies a provider-produced ``(old_hash -> new_hash)`` mapping envelope to the
central ``measurement_attempts.condition_hash_v2`` parallel column, verbatim.

Central never recomputes: this module reads the envelope's pairs + declared
checksum strings and applies / records them as-is. It does NOT derive any hash,
nor build or re-verify the envelope — that is the provider's responsibility
before transport. (Enforced by the platform never-recompute guard in
``tests/test_platform_read_api_fe_p0d.py`` — this module's text carries none of
the forbidden hashing / envelope-builder tokens.)

Design (mirrors ``PostgresCentralClaimWriteAdapter``):

- **injected ``connection_factory``** (``() -> DbConnection``). The concrete
  PostgreSQL connection is built lazily by the composition root; this module
  imports no driver (frozen-exe safe).
- **verbatim UPDATE only**: the sole mutation of ``measurement_attempts`` is
  ``SET condition_hash_v2 = %s WHERE condition_hash = %s AND condition_hash_v2
  IS NULL`` — idempotent (rows already filled are skipped) and the live
  ``condition_hash`` column is never touched. No DDL / no INSERT / no DELETE of
  measurement rows (the parallel column is created by a separate expand
  migration, NOT executed here — contract/plan/test only). ``updated_count`` is
  the driver's actual ``cursor.rowcount`` after each UPDATE (not an eligible
  pre-count) so a concurrent same-value fill cannot inflate the audit.
- **conflict pre-scan AND post-write re-verification**: every pair is scanned
  for an existing ``condition_hash_v2`` that differs from the expected new hash
  BEFORE any UPDATE runs (cheap early abort); the SAME scan is repeated AFTER the
  UPDATEs, still inside the open transaction, before commit. The ``WHERE
  condition_hash_v2 IS NULL`` guard already makes overwrite physically
  impossible, but the pre-scan alone is a TOCTOU check: a concurrent transaction
  could fill a row with a DIFFERENT v2 between the pre-scan and our UPDATE, and
  without the post-write re-scan the run would commit a *success* audit
  (``conflict_count=0``) while the central state actually diverges. Under READ
  COMMITTED the post-write re-scan sees the other transaction's committed write
  and aborts with ``RekeyIngestConflictError`` (write 0 — our own updates roll
  back) so the evidence stays truthful. Full serialization against a conflict
  committed *after* the re-scan is the connection factory's isolation
  responsibility (SERIALIZABLE / ``SELECT ... FOR UPDATE`` at the live-PG layer —
  not embedded here, to keep the SQLite Qmark test shim portable). Mirrors the
  local backfill conflict policy (overwrite-forbidden).
- **declared checksums string-copied to audit**: when an audit writer is wired
  and ``audit_meta`` is supplied, the propagation audit row carries the
  envelope's declared ``old_hash_checksum`` / ``new_hash_checksum`` /
  ``mapping_checksum`` as plain strings (no recompute, no comparison) inside the
  SAME transaction as the UPDATEs — a failed audit rolls the propagation back.
- **``%s`` paramstyle** (psycopg) — every value is a bound parameter.
- **loud-fail**: a connection/query failure raises ``RekeyIngestError``.
"""
from __future__ import annotations

import json
from typing import Callable, List, Mapping, Optional, Tuple

from domain.ports.output.central_audit_write_port import CentralAuditWritePort
from domain.ports.output.central_rekey_ingest_port import (
    RekeyIngestAudit,
    RekeyIngestConflict,
    RekeyIngestConflictError,
    RekeyIngestError,
    RekeyMappingEnvelopeLike,
)
from domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'MEASUREMENT_ATTEMPTS_TABLE',
    'REKEY_PROPAGATION_EVENT_TYPE',
    'UPDATE_CONDITION_HASH_V2_SQL',
    'CONFLICT_SCAN_SQL',
    'ELIGIBLE_COUNT_SQL',
    'ALREADY_SET_COUNT_SQL',
    'build_propagation_audit_detail',
    'PostgresCentralRekeyIngestAdapter',
]


MEASUREMENT_ATTEMPTS_TABLE = 'measurement_attempts'

#: audit_events event_type for a central re-key propagation. The CHECK
#: constraint extension to allow this value is a DEFERRED expand migration
#: (docs/architecture/adr-0005-central-rekey-propagation-plan.md §5) — NOT
#: executed in this sprint (contract/plan/test only). The test fixture's
#: audit_events table omits the CHECK so the contract is exercisable now.
REKEY_PROPAGATION_EVENT_TYPE = 'rekey.propagation.ingested'


#: verbatim, idempotent re-key UPDATE — the only mutation of measurement rows.
UPDATE_CONDITION_HASH_V2_SQL = (
    f'UPDATE "{MEASUREMENT_ATTEMPTS_TABLE}" SET "condition_hash_v2" = %s '
    'WHERE "condition_hash" = %s AND "condition_hash_v2" IS NULL'
)
#: conflict pre-scan — rows already carrying a DIFFERENT v2 (overwrite-forbidden).
CONFLICT_SCAN_SQL = (
    f'SELECT COUNT(*) FROM "{MEASUREMENT_ATTEMPTS_TABLE}" '
    'WHERE "condition_hash" = %s AND "condition_hash_v2" IS NOT NULL '
    'AND "condition_hash_v2" <> %s'
)
#: rows this run would newly fill (condition_hash_v2 still NULL).
ELIGIBLE_COUNT_SQL = (
    f'SELECT COUNT(*) FROM "{MEASUREMENT_ATTEMPTS_TABLE}" '
    'WHERE "condition_hash" = %s AND "condition_hash_v2" IS NULL'
)
#: rows already filled with the expected new hash (idempotent skip).
ALREADY_SET_COUNT_SQL = (
    f'SELECT COUNT(*) FROM "{MEASUREMENT_ATTEMPTS_TABLE}" '
    'WHERE "condition_hash" = %s AND "condition_hash_v2" = %s'
)


def build_propagation_audit_detail(
    envelope: RekeyMappingEnvelopeLike, *, applied_count: int,
) -> dict:
    """propagation audit detail — envelope 의 declared 값을 **문자열 그대로** 복사.

    어떤 checksum 도 재계산하지 않는다 (attribute string copy + applied row count).
    local↔central 연결은 이 declared checksum 문자열 동등성으로 성립한다.
    """
    return {
        'provider': envelope.provider,
        'record_count': envelope.record_count,
        'applied_count': applied_count,
        'old_hash_checksum': envelope.old_hash_checksum,
        'new_hash_checksum': envelope.new_hash_checksum,
        'mapping_checksum': envelope.mapping_checksum,
    }


class PostgresCentralRekeyIngestAdapter:
    """``CentralRekeyIngestPort`` over a central PostgreSQL connection factory."""

    def __init__(
        self,
        connection_factory: Callable[[], DbConnection],
        audit_writer: Optional[CentralAuditWritePort] = None,
    ) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory
        self._audit = audit_writer

    def ingest_mapping_envelope(
        self,
        envelope: RekeyMappingEnvelopeLike,
        *,
        audit_meta: Optional[Mapping] = None,
    ) -> RekeyIngestAudit:
        pairs: Tuple[Tuple[str, str], ...] = tuple(envelope.pairs)

        def _txn(cursor) -> Tuple[int, int]:
            # (1) conflict pre-scan ALL pairs BEFORE any write → cheap early abort (write 0).
            pre_conflicts = self._scan_conflicts(cursor, pairs)
            if pre_conflicts:
                raise RekeyIngestConflictError(pre_conflicts)

            # (2) verbatim UPDATE (NULL-only — overwrite physically impossible). updated_count
            #     is the driver's actual rowcount, skipped is the idempotent already-set count.
            updated = 0
            skipped = 0
            for old, new in pairs:
                skipped += self._scalar(cursor, ALREADY_SET_COUNT_SQL, (old, new))
                cursor.execute(UPDATE_CONDITION_HASH_V2_SQL, (new, old))
                updated += self._affected_rows(cursor)

            # (3) post-write conflict RE-verification (same transaction, before commit). A
            #     concurrent writer may have filled a row with a DIFFERENT v2 between the
            #     pre-scan and our UPDATE (TOCTOU); the re-scan catches it under READ
            #     COMMITTED so we abort with write 0 instead of committing false success.
            post_conflicts = self._scan_conflicts(cursor, pairs)
            if post_conflicts:
                raise RekeyIngestConflictError(post_conflicts)

            # (4) same-transaction propagation audit (declared checksum string copy).
            self._maybe_audit(cursor, envelope, audit_meta, applied_count=updated)
            return updated, skipped

        updated, skipped = self._run_once(_txn)
        return RekeyIngestAudit(
            record_count=envelope.record_count,
            updated_count=updated,
            skipped_count=skipped,
            old_hash_checksum=envelope.old_hash_checksum,
            new_hash_checksum=envelope.new_hash_checksum,
            mapping_checksum=envelope.mapping_checksum,
        )

    def _maybe_audit(
        self,
        cursor,
        envelope: RekeyMappingEnvelopeLike,
        audit_meta: Optional[Mapping],
        *,
        applied_count: int,
    ) -> None:
        """INSERT the propagation audit row inside the open transaction when wired.

        Both ``audit_writer`` (composition) and ``audit_meta`` (per-call non-derivable
        fields: id / actor_subject / project_id / occurred_at / created_at) must be
        present — either missing skips the audit. The detail carries the envelope's
        declared checksum strings verbatim (no recompute). A failed audit propagates
        so the surrounding transaction rolls the propagation back.
        """
        if self._audit is None or audit_meta is None:
            return
        detail = build_propagation_audit_detail(envelope, applied_count=applied_count)
        record = {
            'id': audit_meta.get('id'),
            'event_type': REKEY_PROPAGATION_EVENT_TYPE,
            'project_id': audit_meta.get('project_id'),
            'actor_subject': audit_meta.get('actor_subject'),
            'target_user_subject': None,
            'target_claim_id': None,
            'role_key': None,
            'detail_json': json.dumps(detail, sort_keys=True, ensure_ascii=False),
            'occurred_at': audit_meta.get('occurred_at'),
            'created_at': audit_meta.get('created_at'),
        }
        self._audit.append_event_in_transaction(cursor, record)

    # ── transaction plumbing (mirrors PostgresCentralClaimWriteAdapter) ─────────

    def _run_once(self, body: Callable[[object], Tuple[int, int]]) -> Tuple[int, int]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 — wrap as loud RekeyIngestError
            raise RekeyIngestError(
                f'central re-key ingest connection failed: {exc}'
            ) from exc
        try:
            cursor = connection.cursor()
            try:
                result = body(cursor)
            finally:
                cursor.close()
            connection.commit()
            return result
        except (RekeyIngestConflictError, RekeyIngestError):
            _safe_rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(connection)
            raise RekeyIngestError(f'central re-key ingest failed: {exc}') from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()

    def _scan_conflicts(
        self, cursor, pairs: Tuple[Tuple[str, str], ...],
    ) -> Tuple[RekeyIngestConflict, ...]:
        """Scan every pair for a present-but-different ``condition_hash_v2``.

        Used identically as the pre-scan (before any write) and the post-write
        re-verification (after the UPDATEs, before commit) — a single source of
        the overwrite-forbidden conflict predicate so both checks stay in lockstep.
        """
        conflicts: List[RekeyIngestConflict] = []
        for old, new in pairs:
            existing_diff = self._scalar(cursor, CONFLICT_SCAN_SQL, (old, new))
            if existing_diff:
                conflicts.append(
                    RekeyIngestConflict(
                        old_hash=old, expected_new_hash=new,
                        existing_v2_count=existing_diff,
                    ),
                )
        return tuple(conflicts)

    @staticmethod
    def _affected_rows(cursor) -> int:
        """Actual rows changed by the last UPDATE (DB-API ``rowcount``).

        Truthful — a concurrent same-value fill between count and write cannot
        inflate this the way an eligible pre-count could.
        """
        rowcount = getattr(cursor, 'rowcount', None)
        return int(rowcount) if isinstance(rowcount, int) and rowcount >= 0 else 0

    @staticmethod
    def _scalar(cursor, statement: str, params: tuple) -> int:
        cursor.execute(statement, params)
        rows = list(cursor.fetchall())
        if not rows or rows[0] is None:
            return 0
        value = rows[0][0]
        return int(value) if value is not None else 0


def _safe_rollback(connection) -> None:
    rollback = getattr(connection, 'rollback', None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001 — never mask the original error
            pass
