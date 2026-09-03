"""CentralReportService — list/create/citation over fake ports (Phase G).

Seals the application logic with in-memory fakes (no PostgreSQL): derived
report_number on list/create, edition validation, project-not-found → 404,
duplicate edition → ReportEditionConflictError (409), and citation assembly
delegating to the report_citation domain SSOT.
"""
from __future__ import annotations

import sys
import unittest
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fcc_test_platform.application.central_project_service import ProjectNotFoundError  # noqa: E402
from fcc_test_platform.application.central_report_service import CentralReportService  # noqa: E402
from fcc_test_kernel.domain.models.sample_inventory import SNAPSHOT_SCHEMA_VERSION  # noqa: E402
from domain.ports.output.central_report_port import ReportSessionNotFoundError  # noqa: E402
from domain.ports.output.central_report_port import ReportEditionConflictError  # noqa: E402

_PID = "11111111-1111-1111-1111-111111111111"


class _FakeProjectRead:
    def __init__(self, detail=None):
        self._detail = detail

    def list_projects(self, *, status=None, q=None, limit=None, after=None):
        # CentralProjectReadPort signature (W3 백엔드). This suite never reads the
        # directory — it exists so the fake stays structurally conformant.
        return []

    def read_project_detail(self, project_id):
        return self._detail


class _FakeReportRead:
    def __init__(self, rows=None, session=None):
        self.rows = rows or []
        self.session = session

    def list_reports(self, project_id):
        return list(self.rows)

    def get_session_snapshot(self, session_id):
        return self.session


class _FakeReportWrite:
    def __init__(self, conflict_editions=()):
        self.created = []
        self._conflict = set(conflict_editions)

    def create_report(self, report_record):
        if report_record["edition"] in self._conflict:
            return None
        self.created.append(dict(report_record))
        return {"report_id": report_record["id"], "edition": report_record["edition"]}


def _detail(**overrides):
    base = {
        "project_id": _PID,
        "model_name": "SM-X940",
        "management_number": "4792232056",
        "fcc_grantee_code": "A3L",
        "applicant_name": "Samsung",
        "applicant_address": "Suwon",
        "eut_description": "Tablet",
        "test_standard": "FCC PART 15C",
        "samples": [
            # Compact read-back (2026-06-23) — the adapter pre-selects the latest
            # intake into ``latest_intake`` (created_at DESC); report_citation
            # consumes it directly (no index-0 re-selection).
            {"sample_number": "1", "serial_number": "R3GL1", "intake_count": 2,
             "latest_intake": {
                 "bl": "BL2", "ap": "AP2", "cp": "CP2", "csc": "CSC2",
                 "rf_cal": "OK", "hw_rev": "REV2"},
             },
        ],
    }
    base.update(overrides)
    return base


_UNSET = object()


def _service(*, detail=_UNSET, rows=None, conflict=(), session=None):
    project_detail = _detail() if detail is _UNSET else detail
    return CentralReportService(
        _FakeReportRead(rows, session=session),
        _FakeReportWrite(conflict),
        _FakeProjectRead(project_detail),
        clock=lambda: "2026-06-23T00:00:00+00:00",
        id_factory=lambda: "rep-id-1",
    )


class TestListReports(unittest.TestCase):
    def test_derives_report_number(self):
        rows = [{"report_id": "r1", "project_id": _PID, "edition": "E2V1",
                 "date_of_issue": None, "date_tested_start": None, "date_tested_end": None,
                 "prepared_by": None, "prepared_site": None, "rev_history_json": None,
                 "created_at": "2026-06-23"}]
        out = _service(rows=rows).list_reports(_PID)
        self.assertEqual(out[0]["report_number"], "S-4792232056-E2V1")
        self.assertEqual(out[0]["edition"], "E2V1")

    def test_report_number_none_without_management_number(self):
        rows = [{"report_id": "r1", "project_id": _PID, "edition": "E2V1",
                 "created_at": "2026-06-23"}]
        out = _service(detail=_detail(management_number=None), rows=rows).list_reports(_PID)
        self.assertIsNone(out[0]["report_number"])

    def test_unknown_project_raises_not_found(self):
        with self.assertRaises(ProjectNotFoundError):
            _service(detail=None).list_reports(_PID)

    def test_malformed_uuid_raises_value_error(self):
        with self.assertRaises(ValueError):
            _service().list_reports("not-a-uuid")


class TestCreateReport(unittest.TestCase):
    def test_create_returns_envelope_with_report_number(self):
        svc = _service()
        out = svc.create_report(_PID, edition="E2V1", prepared_by="Tester A")
        self.assertEqual(out["report_number"], "S-4792232056-E2V1")
        self.assertEqual(out["edition"], "E2V1")
        self.assertEqual(out["prepared_by"], "Tester A")

    def test_blank_edition_rejected(self):
        with self.assertRaises(ValueError):
            _service().create_report(_PID, edition="   ")

    def test_duplicate_edition_conflict(self):
        with self.assertRaises(ReportEditionConflictError):
            _service(conflict={"E2V1"}).create_report(_PID, edition="E2V1")

    def test_unknown_project_rejected(self):
        with self.assertRaises(ProjectNotFoundError):
            _service(detail=None).create_report(_PID, edition="E2V1")


class TestReportCitation(unittest.TestCase):
    def test_citation_assembles_meta_and_samples(self):
        out = _service().get_report_citation(_PID, edition="E2V1")
        self.assertEqual(out["report_number"], "S-4792232056-E2V1")
        self.assertEqual(out["fcc_id"], "A3LSMX940")
        self.assertEqual(out["project_id"], _PID)
        self.assertEqual(len(out["samples"]), 1)
        self.assertEqual(out["samples"][0]["sample_number"], "1")
        self.assertEqual(out["samples"][0]["serial_number"], "R3GL1")
        # latest intake (index 0) firmware cited
        self.assertEqual(out["samples"][0]["latest_firmware"]["bl"], "BL2")
        self.assertEqual(out["samples"][0]["latest_firmware"]["hw_rev"], "REV2")

    def test_citation_without_edition_has_no_report_number(self):
        out = _service().get_report_citation(_PID)
        self.assertIsNone(out["report_number"])
        # SN/firmware still assembled
        self.assertEqual(out["samples"][0]["serial_number"], "R3GL1")

    def test_unknown_project_rejected(self):
        with self.assertRaises(ProjectNotFoundError):
            _service(detail=None).get_report_citation(_PID, edition="E2V1")

    def test_session_citation_survives_hard_delete_fk_set_null(self):
        snapshot = {
            'schema_version': SNAPSHOT_SCHEMA_VERSION,
            'project': {'project_id': _PID},
            'sample': {
                'sample_id': '22222222-2222-2222-8222-222222222222',
                'sample_number': 'captured-sample',
                'serial_number': 'SN-CAPTURED',
            },
            'latest_intake': {'bl': 'BL-CAPTURED'},
        }
        session = {
            'project_id': _PID,
            'sample_id': None,  # hard delete applied ON DELETE SET NULL
            'session_origin': 'WEB_SESSION',
            'sample_snapshot_schema_version': SNAPSHOT_SCHEMA_VERSION,
            'sample_snapshot_json': json.dumps(snapshot),
        }
        current_detail = _detail(samples=[{
            'sample_number': 'current-sample',
            'serial_number': 'SN-CURRENT',
            'latest_intake': {'bl': 'BL-CURRENT'},
        }])

        out = _service(detail=current_detail, session=session).get_report_citation(
            _PID,
            edition='E2V1',
            session_id='33333333-3333-3333-8333-333333333333',
        )

        self.assertEqual(out['samples'][0]['sample_number'], 'captured-sample')
        self.assertEqual(out['samples'][0]['serial_number'], 'SN-CAPTURED')
        self.assertEqual(out['samples'][0]['latest_firmware']['bl'], 'BL-CAPTURED')

    def test_session_citation_rejects_present_conflicting_fk(self):
        snapshot = {
            'schema_version': SNAPSHOT_SCHEMA_VERSION,
            'project': {'project_id': _PID},
            'sample': {'sample_id': 'captured-sample', 'sample_number': '1'},
        }
        session = {
            'project_id': _PID,
            'sample_id': 'different-sample',
            'session_origin': 'WEB_SESSION',
            'sample_snapshot_schema_version': SNAPSHOT_SCHEMA_VERSION,
            'sample_snapshot_json': json.dumps(snapshot),
        }
        with self.assertRaises(ReportSessionNotFoundError):
            _service(session=session).get_report_citation(
                _PID,
                session_id='33333333-3333-3333-8333-333333333333',
            )


if __name__ == "__main__":
    unittest.main()
