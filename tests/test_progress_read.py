"""P6-C.1 — progress rollup read adapter seal (SQLite shim).

Exercises the time-weighted rollup query against a temp SQLite DB: ingest
expectations (B.3), insert measurement coverage, read the per-(area, bucket)
rollup. Seals:

- planned/completed minutes use priced snapshots only; percent = completed/planned;
- a bucket with no priced time → percent None (no fake 0%);
- F1: coverage from a DIFFERENT provider does NOT complete a condition;
- unpriced / unbucketable surfaced as counts.
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

from domain.models.enums import MeasurementType  # noqa: E402
from domain.models.progress_time_catalog import CatalogSource, StandardTimeCatalog  # noqa: E402
from fcc_test_platform.application.central_progress_read_adapter import (  # noqa: E402
    PostgresCentralProgressReadAdapter,
)
from fcc_test_platform.application.central_progress_write_adapter import (  # noqa: E402
    PostgresCentralProgressWriteAdapter,
)
from fcc_test_platform.application.progress_ingest_service import (  # noqa: E402
    ProgressIngestService,
    PublishedConditionRow,
)
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
        # SQLite has no ADD COLUMN IF NOT EXISTS; 004 created the table without the
        # column, so a plain ADD COLUMN is the equivalent additive step.
        .replace("ADD COLUMN IF NOT EXISTS", "ADD COLUMN")
    )


def _make_central_db() -> str:
    fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    fd.close()
    conn = SqliteConnectionFactory(fd.name).create()
    conn.executescript('CREATE TABLE "providers" ("id" TEXT PRIMARY KEY);')
    conn.executescript('CREATE TABLE "projects" ("id" TEXT PRIMARY KEY);')
    # Minimal measurement_attempts (only the columns the rollup joins on).
    conn.executescript(
        'CREATE TABLE "measurement_attempts" ('
        '"id" TEXT PRIMARY KEY, "project_id" TEXT, "provider_id" TEXT, '
        '"condition_hash" TEXT, "is_latest" INTEGER);'
    )
    conn.execute('INSERT INTO "providers" ("id") VALUES (?)', (PROVIDER_ID,))
    conn.execute('INSERT INTO "providers" ("id") VALUES (?)', (OTHER_PROVIDER,))
    conn.execute('INSERT INTO "projects" ("id") VALUES (?)', (PROJECT_ID,))
    # Real migration chain: 004 creates the progress tables, 006 adds the
    # read-side latest-wins window column additively.
    conn.executescript(_shim(MIGRATION_004.read_text(encoding="utf-8")))
    conn.executescript(_shim(MIGRATION_006.read_text(encoding="utf-8")))
    conn.commit()
    conn.close()
    return fd.name


def _catalog(version=5, **minutes):
    return StandardTimeCatalog.from_mapping(
        {MeasurementType(k): v for k, v in minutes.items()},
        version=version,
        source=CatalogSource.WORKBOOK_SEED,
    )


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.db_path = _make_central_db()
        self._counter = iter(range(1, 100000))
        self.writer = PostgresCentralProgressWriteAdapter(
            lambda: QmarkConnection(self.db_path),
            id_factory=lambda: f"id-{next(self._counter):05d}",
        )
        self.service = ProgressIngestService(
            self.writer, clock=lambda: "2026-06-23T00:00:00+00:00"
        )
        self.reader = PostgresCentralProgressReadAdapter(
            lambda: QmarkConnection(self.db_path)
        )

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _cover(self, condition_hash, provider_id=PROVIDER_ID):
        conn = SqliteConnectionFactory(self.db_path).create()
        try:
            conn.execute(
                'INSERT INTO "measurement_attempts" '
                '("id","project_id","provider_id","condition_hash","is_latest") '
                "VALUES (?,?,?,?,1)",
                (f"att-{condition_hash}-{provider_id}", PROJECT_ID, provider_id, condition_hash),
            )
            conn.commit()
        finally:
            conn.close()

    def _ingest(self, conditions, catalog):
        self.service.ingest_published_plan(
            project_id=PROJECT_ID, plan_id="plan-A", provider_id=PROVIDER_ID,
            progress_area="unlicensed_conducted", conditions=conditions, catalog=catalog,
        )

    def _ingest_plan(self, plan_id, conditions, catalog, *, published_at):
        self.service.ingest_published_plan(
            project_id=PROJECT_ID, plan_id=plan_id, provider_id=PROVIDER_ID,
            progress_area="unlicensed_conducted", conditions=conditions,
            catalog=catalog, plan_published_at=published_at,
        )


class TestRollup(_Fixture):
    def test_time_weighted_percent(self):
        self._ingest(
            [
                PublishedConditionRow("h-psd", "11ax", "UNII-1", "PSD"),    # priced 3
                PublishedConditionRow("h-duty", "11ax", "UNII-1", "Duty"),  # priced 12
                PublishedConditionRow("h-obw", "11ax", "UNII-1", "OBW"),    # unpriced
            ],
            _catalog(psd=3.0, duty=12.0),
        )
        self._cover("h-psd")  # only the 3-min PSD is measured
        rollups = self.reader.get_project_progress(PROJECT_ID)
        self.assertEqual(len(rollups), 1)
        r = rollups[0]
        self.assertEqual(r.progress_area, "unlicensed_conducted")
        self.assertEqual(r.progress_bucket_id, "unii_1_11ax")
        self.assertEqual(r.planned_minutes, 15.0)   # 3 + 12 (priced only)
        self.assertEqual(r.completed_minutes, 3.0)
        self.assertAlmostEqual(r.percent, 20.0)
        self.assertEqual(r.total_conditions, 3)
        self.assertEqual(r.priced_conditions, 2)
        self.assertEqual(r.unpriced_conditions, 1)
        self.assertEqual(r.unbucketable_conditions, 0)

    def test_f1_other_provider_coverage_does_not_count(self):
        self._ingest(
            [PublishedConditionRow("h-duty", "11ax", "UNII-1", "Duty")],
            _catalog(duty=12.0),
        )
        # coverage exists, but under a DIFFERENT provider (radiated headless)
        self._cover("h-duty", provider_id=OTHER_PROVIDER)
        r = self.reader.get_project_progress(PROJECT_ID)[0]
        self.assertEqual(r.completed_minutes, 0.0)   # not counted — wrong provider
        self.assertEqual(r.percent, 0.0)             # planned>0, nothing completed

    def test_no_priced_time_percent_none(self):
        self._ingest(
            [PublishedConditionRow("h-obw", "11ax", "UNII-1", "OBW")],  # unpriced only
            _catalog(psd=3.0),  # OBW not priced
        )
        r = self.reader.get_project_progress(PROJECT_ID)[0]
        self.assertEqual(r.planned_minutes, 0.0)
        self.assertIsNone(r.percent)                 # no fake 0% — "시간 미설정"
        self.assertEqual(r.unpriced_conditions, 1)

    def test_unbucketable_counted(self):
        self._ingest(
            [PublishedConditionRow("h-sar", "SAR", "2.4GHz", "PSD")],  # priced, unbucketable
            _catalog(psd=3.0),
        )
        rollups = self.reader.get_project_progress(PROJECT_ID)
        r = rollups[0]
        self.assertIsNone(r.progress_bucket_id)
        self.assertEqual(r.unbucketable_conditions, 1)
        self.assertEqual(r.planned_minutes, 3.0)

    def test_empty_project(self):
        self.assertEqual(list(self.reader.get_project_progress("nope")), [])


class TestLatestWins(_Fixture):
    """P6L-3 read-side latest-wins: only the newest plan's denominator counts."""

    def test_newer_plan_supersedes_older_denominator(self):
        # Old plan: 2 priced conditions (15 min). New plan: 1 priced condition
        # (3 min). Both ingested additively; the read must roll up ONLY the new
        # plan — no double-counting the old plan's denominator.
        self._ingest_plan(
            "plan-old",
            [
                PublishedConditionRow("h-psd", "11ax", "UNII-1", "PSD"),
                PublishedConditionRow("h-duty", "11ax", "UNII-1", "Duty"),
            ],
            _catalog(psd=3.0, duty=12.0),
            published_at="2026-06-01T00:00:00+00:00",
        )
        self._ingest_plan(
            "plan-new",
            [PublishedConditionRow("h-psd2", "11ax", "UNII-1", "PSD")],
            _catalog(psd=3.0, duty=12.0),
            published_at="2026-06-20T00:00:00+00:00",
        )
        r = self.reader.get_project_progress(PROJECT_ID)[0]
        self.assertEqual(r.planned_minutes, 3.0)   # new plan only, not 3+15
        self.assertEqual(r.total_conditions, 1)

    def test_stale_write_after_newer_plan_cannot_regress(self):
        # The P0 the read-side design removes: an OLD plan's expectation sync
        # commits AFTER the newer plan (reversed insert order). Because the window
        # ranks by plan_published_at — not insert order — the late old write still
        # loses; the denominator stays the new plan's.
        self._ingest_plan(
            "plan-new",
            [PublishedConditionRow("h-psd2", "11ax", "UNII-1", "PSD")],
            _catalog(psd=3.0, duty=12.0),
            published_at="2026-06-20T00:00:00+00:00",
        )
        self._ingest_plan(  # stale write lands later but is older
            "plan-old",
            [
                PublishedConditionRow("h-psd", "11ax", "UNII-1", "PSD"),
                PublishedConditionRow("h-duty", "11ax", "UNII-1", "Duty"),
            ],
            _catalog(psd=3.0, duty=12.0),
            published_at="2026-06-01T00:00:00+00:00",
        )
        r = self.reader.get_project_progress(PROJECT_ID)[0]
        self.assertEqual(r.planned_minutes, 3.0)   # still the new plan
        self.assertEqual(r.total_conditions, 1)

    def test_single_plan_null_published_at_byte_identical(self):
        # Single plan with no publish time (untracked caller) → window keeps it,
        # identical to the pre-P6L-3 rollup.
        self._ingest(
            [
                PublishedConditionRow("h-psd", "11ax", "UNII-1", "PSD"),
                PublishedConditionRow("h-duty", "11ax", "UNII-1", "Duty"),
            ],
            _catalog(psd=3.0, duty=12.0),
        )
        r = self.reader.get_project_progress(PROJECT_ID)[0]
        self.assertEqual(r.planned_minutes, 15.0)
        self.assertEqual(r.total_conditions, 2)


if __name__ == "__main__":
    unittest.main()
