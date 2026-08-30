"""Central PostgreSQL Phase 6 progress write adapter (P6-B.3).

``PostgresCentralProgressWriteAdapter`` implements ``CentralProgressWritePort``
against ``standard_time_catalog`` + ``published_plan_expectation``.

* ``upsert_catalog`` writes one row per priced test type, upserting on
  ``(provider_id, test_type_canonical)`` — re-running with a bumped version
  refreshes the minutes; the unique index makes it race-safe.
* ``write_expectations`` upserts denominator rows on the F1 natural key
  ``(project_id, provider_id, plan_id, condition_hash)``. Each call pre-loads
  the existing natural keys inside the transaction to report insert-vs-update
  counts portably (no PG-only ``xmax`` trick), then upserts the
  provider-derived + snapshot fields. The whole call is ONE transaction
  (loud-fail rollback on error).

Mirrors ``PostgresCentralSampleWriteAdapter``: injected ``connection_factory``
(``() -> DbConnection``), ``%s`` paramstyle (psycopg), atomic body before commit.
No new write logic in routes/composition — this is the sole progress write path.
"""
from __future__ import annotations

import uuid
from typing import Callable, Optional, Sequence

from domain.models.progress_expectation import ProgressExpectationAtom
from domain.models.progress_time_catalog import StandardTimeCatalog
from domain.ports.output.central_progress_write_port import (
    CentralProgressWriteError,
    CentralProgressWritePort,
    ExpectationWriteSummary,
)
from domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'CATALOG_INSERT_COLUMNS',
    'CATALOG_UPDATE_COLUMNS',
    'EXPECTATION_INSERT_COLUMNS',
    'EXPECTATION_UPDATE_COLUMNS',
    'PostgresCentralProgressWriteAdapter',
]


CATALOG_INSERT_COLUMNS: tuple[str, ...] = (
    'id', 'provider_id', 'test_type_canonical', 'planned_minutes',
    'version', 'source', 'updated_at', 'updated_by',
)
# Refreshed on a re-seed/edit of an existing (provider, test_type) row. Identity
# columns (id/provider_id/test_type_canonical) are never updated.
CATALOG_UPDATE_COLUMNS: tuple[str, ...] = (
    'planned_minutes', 'version', 'source', 'updated_at', 'updated_by',
)

EXPECTATION_INSERT_COLUMNS: tuple[str, ...] = (
    'id', 'project_id', 'provider_id', 'plan_id', 'condition_hash',
    'coverage_technology', 'raw_test_type', 'test_type_canonical',
    'progress_bucket_id', 'progress_area', 'planned_minutes_snapshot',
    'catalog_version', 'pricing_status', 'created_at', 'plan_published_at',
)
# Refreshed when the same (project, provider, plan, condition) is re-ingested.
# Identity (id/project_id/provider_id/plan_id/condition_hash) and created_at are
# preserved. plan_published_at is plan-immutable but kept in UPDATE so a re-sync
# backfills pre-P6L-3 rows that were ingested NULL (read window needs it).
EXPECTATION_UPDATE_COLUMNS: tuple[str, ...] = (
    'coverage_technology', 'raw_test_type', 'test_type_canonical',
    'progress_bucket_id', 'progress_area', 'planned_minutes_snapshot',
    'catalog_version', 'pricing_status', 'plan_published_at',
)


def _insert_with_upsert(
    table: str,
    insert_columns: tuple[str, ...],
    conflict_columns: tuple[str, ...],
    update_columns: tuple[str, ...],
) -> str:
    cols = ', '.join(f'"{c}"' for c in insert_columns)
    placeholders = ', '.join(['%s'] * len(insert_columns))
    conflict = ', '.join(f'"{c}"' for c in conflict_columns)
    assignments = ', '.join(f'"{c}" = EXCLUDED."{c}"' for c in update_columns)
    return (
        f'INSERT INTO "{table}" ({cols}) VALUES ({placeholders}) '
        f'ON CONFLICT ({conflict}) DO UPDATE SET {assignments}'
    )


_CATALOG_UPSERT_SQL = _insert_with_upsert(
    'standard_time_catalog', CATALOG_INSERT_COLUMNS,
    ('provider_id', 'test_type_canonical'), CATALOG_UPDATE_COLUMNS,
)
_EXPECTATION_UPSERT_SQL = _insert_with_upsert(
    'published_plan_expectation', EXPECTATION_INSERT_COLUMNS,
    ('project_id', 'provider_id', 'plan_id', 'condition_hash'),
    EXPECTATION_UPDATE_COLUMNS,
)


def _canonical_value(atom: ProgressExpectationAtom) -> Optional[str]:
    return atom.test_type_canonical.value if atom.test_type_canonical else None


class PostgresCentralProgressWriteAdapter(CentralProgressWritePort):
    def __init__(
        self,
        connection_factory: Callable[[], DbConnection],
        *,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._connect = connection_factory
        self._id = id_factory

    def upsert_catalog(
        self,
        *,
        provider_id: str,
        catalog: StandardTimeCatalog,
        now: str,
    ) -> int:
        written = 0
        try:
            conn = self._connect()
        except Exception as exc:  # noqa: BLE001 — loud-fail boundary
            raise CentralProgressWriteError(str(exc)) from exc
        try:
            cur = conn.cursor()
            for test_type in catalog.priced_types():
                minutes = catalog.minutes_for(test_type)
                cur.execute(_CATALOG_UPSERT_SQL, (
                    self._id(), provider_id, test_type.value, minutes,
                    catalog.version, catalog.source.value, now, None,
                ))
                written += 1
            cur.close()
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise CentralProgressWriteError(str(exc)) from exc
        return written

    def write_expectations(
        self,
        atoms: Sequence[ProgressExpectationAtom],
        *,
        now: str,
    ) -> ExpectationWriteSummary:
        if not atoms:
            return ExpectationWriteSummary()
        try:
            conn = self._connect()
        except Exception as exc:  # noqa: BLE001
            raise CentralProgressWriteError(str(exc)) from exc
        try:
            cur = conn.cursor()
            existing = self._existing_keys(cur, atoms)
            inserted = updated = 0
            for atom in atoms:
                key = (
                    atom.project_id, atom.provider_id, atom.plan_id,
                    atom.condition_hash,
                )
                if key in existing:
                    updated += 1
                else:
                    inserted += 1
                    existing.add(key)
                cur.execute(_EXPECTATION_UPSERT_SQL, (
                    self._id(), atom.project_id, atom.provider_id, atom.plan_id,
                    atom.condition_hash, atom.coverage_technology,
                    atom.raw_test_type, _canonical_value(atom),
                    atom.progress_bucket_id, atom.progress_area,
                    atom.planned_minutes_snapshot, atom.catalog_version,
                    atom.pricing_status.value, now, atom.plan_published_at,
                ))
            cur.close()
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise CentralProgressWriteError(str(exc)) from exc
        return ExpectationWriteSummary(inserted=inserted, updated=updated)

    def _existing_keys(self, cursor, atoms: Sequence[ProgressExpectationAtom]) -> set:
        """Natural keys already present, loaded in-transaction for insert/update counts."""
        keys: set = set()
        # Only the (project, plan) pairs actually present in the batch (index-backed
        # read), then filter to the exact condition_hashes in Python — avoids a
        # cartesian product and a giant IN list.
        pairs = {(a.project_id, a.provider_id, a.plan_id) for a in atoms}
        wanted = {
            (a.project_id, a.provider_id, a.plan_id, a.condition_hash)
            for a in atoms
        }
        for project_id, provider_id, plan_id in pairs:
            cursor.execute(
                'SELECT "project_id", "provider_id", "plan_id", "condition_hash" '
                'FROM "published_plan_expectation" '
                'WHERE "project_id" = %s AND "provider_id" = %s '
                'AND "plan_id" = %s',
                (project_id, provider_id, plan_id),
            )
            for row in cursor.fetchall():
                key = (row[0], row[1], row[2], row[3])
                if key in wanted:
                    keys.add(key)
        return keys
