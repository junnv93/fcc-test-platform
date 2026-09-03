"""P6-B.3 — progress ingest write adapter + service seal (SQLite shim).

Exercises the central progress write path end-to-end against a temp SQLite DB
through the %s→? QmarkConnection shim (the same shim the sample-write tests use),
with the progress tables created from the 004 migration. Seals:

- catalog upsert (insert then refresh on version bump);
- expectation upsert by natural key (insert then idempotent re-ingest = update);
- provider-scoped rows persist the F1 columns + snapshot + pricing_status;
- unpriced / unbucketable conditions are stored (not dropped) with NULLs;
- loud-fail on a bad connection.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

from fcc_test_kernel.domain.models.enums import MeasurementType  # noqa: E402
from fcc_test_platform.domain.models.progress_time_catalog import (  # noqa: E402
    CatalogSource,
    StandardTimeCatalog,
)
from fcc_test_platform.domain.ports.output.central_progress_write_port import (  # noqa: E402
    CentralProgressWriteError,
)
from fcc_test_platform.application.central_progress_write_adapter import (  # noqa: E402
    PostgresCentralProgressWriteAdapter,
)
from fcc_test_platform.application.progress_ingest_service import (  # noqa: E402
    ProgressIngestService,
    PublishedConditionRow,
)
from fcc_test_platform.domain.services.progress_expectation import build_expectation_atom  # noqa: E402
from fcc_test_contracts.common.sqlite_connection_factory import SqliteConnectionFactory  # noqa: E402
from tests.support.central_pg_sqlite_shim import QmarkConnection  # noqa: E402

PROVIDER_ID = "prov-unl-cond"
OTHER_PROVIDER = "prov-unl-rad"
PROJECT_ID = "proj-1"
MIGRATION_004 = resolve_repo_artifact(__file__, 'docs/platform/migrations/004_progress_tables.sql')
MIGRATION_006 = resolve_repo_artifact(__file__, 'docs/platform/migrations/006_progress_plan_published_at.sql')


def _shim(sql: str) -> str:
    return (
        sql.replace("UUID", "TEXT")
        .replace("NUMERIC", "REAL")
        .replace("TIMESTAMPTZ", "TEXT")
        .replace("ADD COLUMN IF NOT EXISTS", "ADD COLUMN")
    )


def _make_central_db() -> str:
    fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    fd.close()
    conn = SqliteConnectionFactory(fd.name).create()
    conn.executescript('CREATE TABLE "providers" ("id" TEXT PRIMARY KEY);')
    conn.executescript('CREATE TABLE "projects" ("id" TEXT PRIMARY KEY);')
    conn.executescript(
        'CREATE TABLE "measurement_attempts" ('
        '"project_id" TEXT, "provider_id" TEXT, '
        '"condition_hash" TEXT, "is_latest" INTEGER);'
    )
    conn.execute('INSERT INTO "providers" ("id") VALUES (?)', (PROVIDER_ID,))
    conn.execute('INSERT INTO "providers" ("id") VALUES (?)', (OTHER_PROVIDER,))
    conn.execute('INSERT INTO "projects" ("id") VALUES (?)', (PROJECT_ID,))
    conn.executescript(_shim(MIGRATION_004.read_text(encoding="utf-8")))
    conn.executescript(_shim(MIGRATION_006.read_text(encoding="utf-8")))
    conn.commit()
    conn.close()
    return fd.name


def _catalog(version=1, **minutes):
    return StandardTimeCatalog.from_mapping(
        {MeasurementType(k): v for k, v in minutes.items()},
        version=version,
        source=CatalogSource.WORKBOOK_SEED,
    )


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.db_path = _make_central_db()
        self._counter = iter(range(1, 100000))
        self.adapter = PostgresCentralProgressWriteAdapter(
            lambda: QmarkConnection(self.db_path),
            id_factory=lambda: f"id-{next(self._counter):05d}",
        )
        self.service = ProgressIngestService(
            self.adapter, clock=lambda: "2026-06-23T00:00:00+00:00"
        )

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _query(self, sql, params=()):
        conn = SqliteConnectionFactory(self.db_path).create()
        try:
            return list(conn.execute(sql, params).fetchall())
        finally:
            conn.close()


class TestCatalogUpsert(_Fixture):
    def test_insert_then_refresh_on_version_bump(self):
        n = self.adapter.upsert_catalog(
            provider_id=PROVIDER_ID, catalog=_catalog(version=1, psd=3.0, duty=12.0),
            now="t0",
        )
        self.assertEqual(n, 2)
        rows = self._query(
            'SELECT test_type_canonical, planned_minutes, version '
            'FROM standard_time_catalog ORDER BY test_type_canonical'
        )
        self.assertEqual(rows, [("duty", 12.0, 1), ("psd", 3.0, 1)])

        # re-seed with bumped version + changed minutes → same 2 rows, refreshed
        self.adapter.upsert_catalog(
            provider_id=PROVIDER_ID, catalog=_catalog(version=2, psd=4.5, duty=12.0),
            now="t1",
        )
        rows = self._query(
            'SELECT planned_minutes, version FROM standard_time_catalog '
            'WHERE test_type_canonical = ?', ("psd",)
        )
        self.assertEqual(rows, [(4.5, 2)])
        # still exactly 2 rows (upsert, not duplicate insert)
        self.assertEqual(
            self._query('SELECT COUNT(*) FROM standard_time_catalog')[0][0], 2
        )


def _conditions():
    return [
        PublishedConditionRow("h-psd-u1", "11ax", "UNII-1", "PSD"),     # priced+bucketed
        PublishedConditionRow("h-obw-u3", "NII", "UNII-3", "OBW"),      # unpriced (no catalog)
        PublishedConditionRow("h-psd-sar", "SAR", "2.4GHz", "PSD"),     # unbucketable
    ]


class TestExpectationIngest(_Fixture):
    def test_ingest_inserts_then_idempotent_update(self):
        cat = _catalog(version=5, psd=3.0)
        report = self.service.ingest_published_plan(
            project_id=PROJECT_ID, plan_id="plan-A", provider_id=PROVIDER_ID,
            progress_area="unlicensed_conducted", conditions=_conditions(), catalog=cat,
        )
        self.assertEqual(report.condition_count, 3)
        self.assertEqual(report.write_summary.inserted, 3)
        self.assertEqual(report.write_summary.updated, 0)
        # pricing ⟂ bucketing: both PSD rows price (catalog has psd) even though
        # the SAR row is unbucketable; only the OBW row is unpriced.
        self.assertEqual(report.priced_count, 2)
        self.assertEqual(report.unpriced_count, 1)
        self.assertEqual(report.unbucketable_count, 1)

        # re-ingest same plan → all updates, no new rows
        report2 = self.service.ingest_published_plan(
            project_id=PROJECT_ID, plan_id="plan-A", provider_id=PROVIDER_ID,
            progress_area="unlicensed_conducted", conditions=_conditions(), catalog=cat,
        )
        self.assertEqual(report2.write_summary.inserted, 0)
        self.assertEqual(report2.write_summary.updated, 3)
        self.assertEqual(
            self._query('SELECT COUNT(*) FROM published_plan_expectation')[0][0], 3
        )

    def test_provider_is_part_of_natural_key(self):
        cat = _catalog(version=5, psd=3.0)
        self.service.ingest_published_plan(
            project_id=PROJECT_ID, plan_id="plan-A", provider_id=PROVIDER_ID,
            progress_area="unlicensed_conducted",
            conditions=[PublishedConditionRow("same-hash", "11ax", "UNII-1", "PSD")],
            catalog=cat,
        )
        report = self.service.ingest_published_plan(
            project_id=PROJECT_ID, plan_id="plan-A", provider_id=OTHER_PROVIDER,
            progress_area="unlicensed_radiated",
            conditions=[PublishedConditionRow("same-hash", "11ax", "UNII-1", "PSD")],
            catalog=cat,
        )
        self.assertEqual(report.write_summary.inserted, 1)
        self.assertEqual(report.write_summary.updated, 0)
        rows = self._query(
            'SELECT provider_id, progress_area FROM published_plan_expectation '
            'WHERE project_id = ? AND plan_id = ? AND condition_hash = ? '
            'ORDER BY provider_id',
            (PROJECT_ID, "plan-A", "same-hash"),
        )
        self.assertEqual(
            rows,
            [
                (PROVIDER_ID, "unlicensed_conducted"),
                (OTHER_PROVIDER, "unlicensed_radiated"),
            ],
        )

    def test_persisted_columns(self):
        cat = _catalog(version=5, psd=3.0)
        self.service.ingest_published_plan(
            project_id=PROJECT_ID, plan_id="plan-A", provider_id=PROVIDER_ID,
            progress_area="unlicensed_conducted", conditions=_conditions(), catalog=cat,
        )
        priced = self._query(
            'SELECT provider_id, progress_bucket_id, progress_area, '
            'planned_minutes_snapshot, catalog_version, pricing_status, '
            'test_type_canonical, coverage_technology '
            'FROM published_plan_expectation WHERE condition_hash = ?',
            ("h-psd-u1",),
        )[0]
        self.assertEqual(priced[0], PROVIDER_ID)            # F1 provider scope
        self.assertEqual(priced[1], "unii_1_11ax")          # bucket
        self.assertEqual(priced[2], "unlicensed_conducted")  # area
        self.assertEqual(priced[3], 3.0)                    # snapshot minutes
        self.assertEqual(priced[4], 5)                      # catalog_version
        self.assertEqual(priced[5], "priced")
        self.assertEqual(priced[6], "psd")
        self.assertEqual(priced[7], "11ax")

    def test_unpriced_and_unbucketable_stored_with_nulls(self):
        cat = _catalog(version=5, psd=3.0)
        self.service.ingest_published_plan(
            project_id=PROJECT_ID, plan_id="plan-A", provider_id=PROVIDER_ID,
            progress_area="unlicensed_conducted", conditions=_conditions(), catalog=cat,
        )
        unpriced = self._query(
            'SELECT planned_minutes_snapshot, pricing_status, progress_bucket_id '
            'FROM published_plan_expectation WHERE condition_hash = ?', ("h-obw-u3",)
        )[0]
        self.assertIsNone(unpriced[0])           # no snapshot minutes
        self.assertEqual(unpriced[1], "unpriced")
        self.assertEqual(unpriced[2], "unii_3")  # still bucketed
        unbucketable = self._query(
            'SELECT progress_bucket_id, pricing_status '
            'FROM published_plan_expectation WHERE condition_hash = ?', ("h-psd-sar",)
        )[0]
        self.assertIsNone(unbucketable[0])       # unbucketable → NULL bucket
        self.assertEqual(unbucketable[1], "priced")


class TestLoudFail(unittest.TestCase):
    def test_bad_connection_factory_raises(self):
        def boom():
            raise RuntimeError("no db")

        adapter = PostgresCentralProgressWriteAdapter(boom)
        with self.assertRaises(CentralProgressWriteError):
            adapter.upsert_catalog(
                provider_id=PROVIDER_ID, catalog=_catalog(psd=1.0), now="t0"
            )
        with self.assertRaises(CentralProgressWriteError):
            adapter.write_expectations(
                [
                    build_expectation_atom(
                        project_id=PROJECT_ID, plan_id="p", provider_id=PROVIDER_ID,
                        progress_area="a", condition_hash="h", technology="11ax",
                        band="UNII-1", raw_test_type="PSD", catalog=_catalog(psd=1.0),
                    )
                ],
                now="t0",
            )


if __name__ == "__main__":
    unittest.main()
