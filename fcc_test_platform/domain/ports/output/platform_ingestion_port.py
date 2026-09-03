"""Output ports for platform ingestion transactions.

FE-P0c Phase C+F (2026-05-26) extends the Protocol with the FE-P0a
ingestion_contract surface so concrete adapters (e.g.
``PostgresIngestionTransaction``) and the generic worker
(``execute_platform_ingestion_plan``) share a typed contract — no duck
typing at the production runtime path.

Phase F follow-up to the external review:
    - ``set_serializable_isolation`` is invoked BEFORE the first executed
      statement (worker decides up-front based on the plan), because
      PostgreSQL rejects ``SET TRANSACTION ISOLATION LEVEL ...`` once the
      transaction has issued any statement.
    - ``refresh_coverage_materialized_view`` is a Writer-level capability,
      executed on a SEPARATE autocommit connection because PostgreSQL
      disallows ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` inside a
      transaction block. Concrete writer (Postgres) sets ``connection.autocommit
      = True`` on the dedicated connection used only for this statement.
"""
from __future__ import annotations

from typing import Mapping, Optional, Protocol, runtime_checkable


@runtime_checkable
class PlatformIngestionTransaction(Protocol):
    def set_serializable_isolation(self) -> None:
        """Set SERIALIZABLE isolation. MUST be invoked before any other SQL
        statement is issued on this transaction (PostgreSQL constraint).
        Idempotent — second invocations on the same transaction are no-ops.
        """
        ...

    def upsert(self, table: str, record: Mapping, idempotency_key: tuple[str, ...]) -> Optional[int]:
        ...

    def upsert_attempt(
        self,
        record: Mapping,
        idempotency_key: tuple[str, ...],
        *,
        fk_resolution_hint: Optional[Mapping] = None,
    ) -> Optional[int]:
        """Insert an attempt with the 3-statement FE-P0a Rule 1 body:
        (1) UPDATE prior is_latest=false (toggle), (2) INSERT new with
        is_latest=true and (3) FK back-fill via subquery against
        ``measurement_results.(provider_id, provider_result_id)``.

        SERIALIZABLE isolation MUST have been set already (call
        ``set_serializable_isolation`` first).
        """
        ...

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
    ) -> Optional[int]:
        """Same-transaction projection (FE-P0a ingestion_contract Rule 2)."""
        ...

    def commit(self) -> None:
        ...

    def rollback(self) -> None:
        ...


@runtime_checkable
class PlatformIngestionWriter(Protocol):
    def begin_transaction(self) -> PlatformIngestionTransaction:
        ...

    def refresh_coverage_materialized_view(self) -> None:
        """Issue ``REFRESH MATERIALIZED VIEW CONCURRENTLY coverage_by_condition_hash``
        on a SEPARATE autocommit connection (FE-P0a Rule 4 — PostgreSQL
        disallows REFRESH CONCURRENTLY inside a transaction block).

        Refresh failures MUST be loud (the writer raises) — the caller is
        responsible for swallowing / logging so the attempt fact (already
        committed) stays durable.
        """
        ...
