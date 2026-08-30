"""Central PostgreSQL standard-time catalog read adapter (P6L-3).

``PostgresCentralProgressCatalogReadAdapter`` implements
``CentralProgressCatalogReadPort`` against ``standard_time_catalog`` — the read
counterpart of ``PostgresCentralProgressWriteAdapter.upsert_catalog``. The
expectation sync service loads the provider's catalog once per run and prices the
published-plan conditions against it.

Mirrors the other central adapters: injected ``connection_factory``
(``() -> DbConnection``), ``%s`` paramstyle (psycopg / QmarkConnection shim),
loud-fail via ``CentralProgressCatalogReadError``. The canonical test-type token
is resolved through the existing ``normalize_dispatch_token`` → ``MeasurementType``
SSOT (no re-implemented canonicalization); a row whose token is not a known
measurement type is skipped (it could never price a condition anyway). The catalog
``version`` is the newest version present (all rows of a provider's catalog share
a version; a partial re-seed is collapsed to its newest).
"""
from __future__ import annotations

from typing import Callable, Optional

from domain.models.enums import MeasurementType, normalize_dispatch_token
from domain.models.progress_time_catalog import CatalogSource, StandardTimeCatalog
from domain.ports.output.central_progress_catalog_read_port import (
    CentralProgressCatalogReadError,
    CentralProgressCatalogReadPort,
)
from domain.ports.output.platform_database_port import DbConnection


__all__ = ['CATALOG_READ_SQL', 'PostgresCentralProgressCatalogReadAdapter']


CATALOG_READ_SQL = (
    'SELECT "test_type_canonical", "planned_minutes", "version", "source" '
    'FROM "standard_time_catalog" WHERE "provider_id" = %s'
)


def _measurement_type(raw: str) -> Optional[MeasurementType]:
    try:
        return MeasurementType(normalize_dispatch_token(raw))
    except ValueError:
        return None


def _catalog_source(raw) -> CatalogSource:
    try:
        return CatalogSource(str(raw))
    except ValueError:
        # Unknown provenance token — default to the seed source (provenance is
        # advisory metadata; it must never block pricing).
        return CatalogSource.WORKBOOK_SEED


class PostgresCentralProgressCatalogReadAdapter(CentralProgressCatalogReadPort):
    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        self._connect = connection_factory

    def load_catalog(self, provider_id: str) -> Optional[StandardTimeCatalog]:
        try:
            conn = self._connect()
        except Exception as exc:  # noqa: BLE001 — loud-fail boundary
            raise CentralProgressCatalogReadError(str(exc)) from exc
        try:
            cur = conn.cursor()
            cur.execute(CATALOG_READ_SQL, (provider_id,))
            rows = cur.fetchall()
            cur.close()
        except Exception as exc:  # noqa: BLE001
            raise CentralProgressCatalogReadError(str(exc)) from exc
        finally:
            close = getattr(conn, 'close', None)
            if callable(close):
                close()

        minutes_by_type: dict[MeasurementType, float] = {}
        version = 0
        source_by_version: dict[int, CatalogSource] = {}
        for raw_type, planned_minutes, row_version, row_source in rows:
            test_type = _measurement_type(raw_type)
            if test_type is None or planned_minutes is None:
                continue
            row_version = int(row_version)
            minutes_by_type[test_type] = float(planned_minutes)
            source_by_version[row_version] = _catalog_source(row_source)
            version = max(version, row_version)
        if not minutes_by_type:
            return None
        return StandardTimeCatalog.from_mapping(
            minutes_by_type,
            version=version,
            source=source_by_version.get(version, CatalogSource.WORKBOOK_SEED),
        )
