"""P6L-3 — central standard-time catalog read adapter seal (SQLite shim).

Round-trips the catalog through the write adapter (upsert_catalog) and the new
read adapter (load_catalog) against a temp SQLite DB via the %s→? QmarkConnection
shim. Seals: minutes/version/source recovered, unknown provider → None, an
unknown canonical token is skipped (never crashes pricing).
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
from fcc_test_platform.application.central_progress_catalog_read_adapter import (  # noqa: E402
    PostgresCentralProgressCatalogReadAdapter,
)
from fcc_test_platform.application.central_progress_write_adapter import (  # noqa: E402
    PostgresCentralProgressWriteAdapter,
)
from fcc_test_contracts.common.sqlite_connection_factory import SqliteConnectionFactory  # noqa: E402
from tests.support.central_pg_sqlite_shim import QmarkConnection  # noqa: E402

PROVIDER_ID = "prov-unl-cond"
OTHER_PROVIDER = "prov-unl-rad"
MIGRATION_004 = resolve_repo_artifact(__file__, 'docs/platform/migrations/004_progress_tables.sql')


def _make_central_db() -> str:
    fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    fd.close()
    conn = SqliteConnectionFactory(fd.name).create()
    shim = (
        MIGRATION_004.read_text(encoding="utf-8")
        .replace("UUID", "TEXT").replace("NUMERIC", "REAL").replace("TIMESTAMPTZ", "TEXT")
    )
    conn.executescript('CREATE TABLE "providers" ("id" TEXT PRIMARY KEY);')
    conn.executescript('CREATE TABLE "projects" ("id" TEXT PRIMARY KEY);')
    conn.executescript(
        'CREATE TABLE "measurement_attempts" ("id" TEXT PRIMARY KEY, '
        '"project_id" TEXT, "provider_id" TEXT, "condition_hash" TEXT, "is_latest" INTEGER);'
    )
    for pid in (PROVIDER_ID, OTHER_PROVIDER):
        conn.execute('INSERT INTO "providers" ("id") VALUES (?)', (pid,))
    conn.executescript(shim)
    conn.commit()
    conn.close()
    return fd.name


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.db_path = _make_central_db()
        self._counter = iter(range(1, 100000))
        self.writer = PostgresCentralProgressWriteAdapter(
            lambda: QmarkConnection(self.db_path),
            id_factory=lambda: f"id-{next(self._counter):05d}",
        )
        self.reader = PostgresCentralProgressCatalogReadAdapter(
            lambda: QmarkConnection(self.db_path)
        )

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass


class TestCatalogRead(_Fixture):
    def test_round_trips_minutes_version_source(self):
        catalog = StandardTimeCatalog.from_mapping(
            {MeasurementType.PSD: 3.0, MeasurementType.DUTY: 12.0},
            version=7, source=CatalogSource.MANUAL,
        )
        self.writer.upsert_catalog(provider_id=PROVIDER_ID, catalog=catalog, now="t0")

        loaded = self.reader.load_catalog(PROVIDER_ID)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.version, 7)
        self.assertEqual(loaded.source, CatalogSource.MANUAL)
        self.assertEqual(loaded.minutes_for(MeasurementType.PSD), 3.0)
        self.assertEqual(loaded.minutes_for(MeasurementType.DUTY), 12.0)
        self.assertEqual(
            set(loaded.priced_types()), {MeasurementType.PSD, MeasurementType.DUTY}
        )

    def test_unknown_provider_returns_none(self):
        self.writer.upsert_catalog(
            provider_id=PROVIDER_ID,
            catalog=StandardTimeCatalog.from_mapping(
                {MeasurementType.PSD: 3.0}, version=1, source=CatalogSource.WORKBOOK_SEED
            ),
            now="t0",
        )
        self.assertIsNone(self.reader.load_catalog("does-not-exist"))

    def test_unknown_canonical_token_skipped(self):
        # A stray catalog row with a non-MeasurementType token must not crash the
        # read; it is simply absent from the catalog (un-priceable anyway).
        self.writer.upsert_catalog(
            provider_id=PROVIDER_ID,
            catalog=StandardTimeCatalog.from_mapping(
                {MeasurementType.PSD: 3.0}, version=2, source=CatalogSource.WORKBOOK_SEED
            ),
            now="t0",
        )
        conn = SqliteConnectionFactory(self.db_path).create()
        try:
            conn.execute(
                'INSERT INTO "standard_time_catalog" '
                '("id","provider_id","test_type_canonical","planned_minutes",'
                '"version","source","updated_at","updated_by") '
                "VALUES ('x','prov-unl-cond','not_a_real_type',5.0,2,'manual','t0',NULL)"
            )
            conn.commit()
        finally:
            conn.close()

        loaded = self.reader.load_catalog(PROVIDER_ID)
        self.assertIsNotNone(loaded)
        self.assertEqual(set(loaded.priced_types()), {MeasurementType.PSD})


if __name__ == "__main__":
    unittest.main()
