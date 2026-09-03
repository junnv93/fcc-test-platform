"""P6L-3 — expectation sync service seal (fakes).

Drives ``ProgressExpectationSyncService.run_once`` over fake ports to seal the
watermark/derive pull:

- happy path: derive → resolve → price → ingest → watermark synced (central
  project uuid + plan_published_at carried; condition_hash verbatim);
- catalog un-seeded → mark_failed, no ingest (no fake denominator);
- project unresolvable → mark_failed;
- ingest raises → mark_failed (retry), isolated per plan;
- empty pending → no-op.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fcc_test_kernel.domain.models.enums import MeasurementType  # noqa: E402
from fcc_test_kernel.domain.models.expectation_sync import PendingExpectationPlan  # noqa: E402
from fcc_test_platform.domain.models.progress_time_catalog import CatalogSource, StandardTimeCatalog  # noqa: E402
from fcc_test_kernel.domain.models.test_plan_authoring import RowOrigin, TestPlanRow  # noqa: E402
from fcc_test_platform.domain.ports.output.central_progress_write_port import ExpectationWriteSummary  # noqa: E402
from fcc_test_platform.application.progress_ingest_service import ProgressIngestService  # noqa: E402
from fcc_test_platform.application.progress_expectation_sync_service import (  # noqa: E402
    ProgressExpectationSyncService,
)

PROVIDER = "prov-unl-cond"
AREA = "unlicensed_conducted"


def _row(cond_hash, test_type="PSD"):
    return TestPlanRow(
        capability_path=("WLAN", "802.11ax_5", "5G", "WLAN|UNII-1|80MHz|Lower", "80M"),
        origin=RowOrigin.GENERATED, scope_revision=1,
        condition_hash=cond_hash, test_type=test_type,
    )


def _catalog():
    return StandardTimeCatalog.from_mapping(
        {MeasurementType.PSD: 3.0}, version=5, source=CatalogSource.WORKBOOK_SEED
    )


class _FakeSyncState:
    def __init__(self, pending):
        self._pending = list(pending)
        self.synced = []
        self.failed = []

    def list_pending(self):
        return list(self._pending)

    def mark_synced(self, *, plan_id, project_id, synced_at, catalog_version):
        self.synced.append((plan_id, project_id, catalog_version))

    def mark_failed(self, *, plan_id, project_id, synced_at, error):
        self.failed.append((plan_id, project_id, error))


class _FakeCatalogReader:
    def __init__(self, catalog):
        self._catalog = catalog

    def load_catalog(self, provider_id):
        return self._catalog


class _FakeIdResolver:
    def __init__(self, mapping):
        self._mapping = mapping

    def resolve_session_uuid(self, local_session_id):  # pragma: no cover
        return "sess"

    def resolve_project_uuid(self, local_project_id):
        return self._mapping.get(local_project_id)


class _RecordingWritePort:
    def __init__(self, raise_on_write=False):
        self.atoms = []
        self._raise = raise_on_write

    def upsert_catalog(self, *, provider_id, catalog, now):  # pragma: no cover
        return 0

    def write_expectations(self, atoms, *, now):
        if self._raise:
            raise RuntimeError("central down")
        self.atoms.extend(atoms)
        return ExpectationWriteSummary(inserted=len(atoms), updated=0)


def _service(sync_state, *, catalog, id_map, write_port):
    return ProgressExpectationSyncService(
        sync_state=sync_state,
        catalog_reader=_FakeCatalogReader(catalog),
        id_resolver=_FakeIdResolver(id_map),
        ingest_service=ProgressIngestService(write_port, clock=lambda: "t-now"),
        provider_id=PROVIDER, progress_area=AREA, clock=lambda: "t-now",
    )


class TestRunOnce(unittest.TestCase):
    def test_happy_path_synced_with_central_project_and_published_at(self):
        plan = PendingExpectationPlan(
            "plan-A", "proj-local", "2026-06-20T00:00:00+00:00",
            (_row("h1", "PSD"), _row("h2", "OBW")),  # OBW unpriced
        )
        ss = _FakeSyncState([plan])
        wp = _RecordingWritePort()
        report = _service(
            ss, catalog=_catalog(), id_map={"proj-local": "uuid-central"}, write_port=wp
        ).run_once()

        self.assertEqual(report.plans_synced, 1)
        self.assertEqual(report.plans_failed, 0)
        self.assertEqual(ss.synced, [("plan-A", "proj-local", 5)])
        # central project uuid + plan_published_at carried onto every atom
        self.assertTrue(wp.atoms)
        for atom in wp.atoms:
            self.assertEqual(atom.project_id, "uuid-central")
            self.assertEqual(atom.plan_published_at, "2026-06-20T00:00:00+00:00")
            self.assertEqual(atom.provider_id, PROVIDER)
        # condition_hash verbatim
        self.assertEqual({a.condition_hash for a in wp.atoms}, {"h1", "h2"})
        self.assertEqual(report.unpriced, 1)  # OBW

    def test_catalog_unseeded_marks_failed_without_ingest(self):
        plan = PendingExpectationPlan("plan-A", "proj-local", "t", (_row("h1"),))
        ss = _FakeSyncState([plan])
        wp = _RecordingWritePort()
        report = _service(
            ss, catalog=None, id_map={"proj-local": "uuid"}, write_port=wp
        ).run_once()
        self.assertEqual(report.plans_failed, 1)
        self.assertEqual(ss.synced, [])
        self.assertEqual(wp.atoms, [])
        self.assertIn("not seeded", ss.failed[0][2])

    def test_unresolvable_project_marks_failed(self):
        plan = PendingExpectationPlan("plan-A", "proj-local", "t", (_row("h1"),))
        ss = _FakeSyncState([plan])
        wp = _RecordingWritePort()
        report = _service(
            ss, catalog=_catalog(), id_map={}, write_port=wp
        ).run_once()
        self.assertEqual(report.plans_failed, 1)
        self.assertEqual(wp.atoms, [])
        self.assertIn("not resolvable", ss.failed[0][2])

    def test_ingest_failure_marks_failed_for_retry(self):
        plan = PendingExpectationPlan("plan-A", "proj-local", "t", (_row("h1"),))
        ss = _FakeSyncState([plan])
        report = _service(
            ss, catalog=_catalog(), id_map={"proj-local": "uuid"},
            write_port=_RecordingWritePort(raise_on_write=True),
        ).run_once()
        self.assertEqual(report.plans_failed, 1)
        self.assertEqual(ss.synced, [])
        self.assertEqual([f[0] for f in ss.failed], ["plan-A"])

    def test_empty_pending_is_noop(self):
        ss = _FakeSyncState([])
        report = _service(
            ss, catalog=_catalog(), id_map={}, write_port=_RecordingWritePort()
        ).run_once()
        self.assertEqual(report.plans_attempted, 0)
        self.assertEqual(ss.synced, [])
        self.assertEqual(ss.failed, [])


if __name__ == "__main__":
    unittest.main()
