"""Platform test-report API surface (Phase G, 2026-06-23).

Seals the PlatformApiAdapter report boundary + the contract wiring with in-memory
fakes (no live PostgreSQL):

- the read/write ports structurally satisfy their @runtime_checkable Protocols,
- the adapter delegates list/create/citation to the report service (auth off) and
  raises loudly when the service is unwired (mirror of project/membership),
- the platform error table maps ReportEditionConflictError → 409, CentralReportError
  → 503, and reuses ProjectNotFoundError → 404 / ValueError → 400,
- the operation/route/permission/handler contract is complete for the 3 ops.

Owned by ``/verify-test-report-central``.
"""
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fcc_test_kernel.application.central_contract.api_contracts import (  # noqa: E402
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_PERMISSIONS,
    PLATFORM_API_ROUTES,
)
from fcc_test_platform.application.central_project_service import ProjectNotFoundError  # noqa: E402
from fcc_test_platform.application.central_report_read_adapter import (  # noqa: E402
    PostgresCentralReportReadAdapter,
)
from fcc_test_platform.application.central_report_write_adapter import (  # noqa: E402
    PostgresCentralReportWriteAdapter,
)
from fcc_test_platform.domain.ports.output.central_report_port import (  # noqa: E402
    CentralReportError,
    CentralReportReadPort,
    CentralReportWritePort,
    ReportEditionConflictError,
)
from fcc_test_platform.api.platform_routes import (  # noqa: E402
    PlatformApiAdapter,
    api_error_status,
)

_REPORT_OPS = ('list_reports', 'create_report', 'get_report_citation')


class _FakeReportService:
    def __init__(self):
        self.calls = []

    def list_reports(self, project_id):
        self.calls.append(('list', project_id))
        return [{'report_id': 'r1', 'report_number': 'S-1-E2V1'}]

    def create_report(self, project_id, **kwargs):
        self.calls.append(('create', project_id, kwargs))
        return {'report_id': 'r1', 'edition': kwargs.get('edition')}

    def get_report_citation(self, project_id, *, edition=None, session_id=None):
        self.calls.append(('citation', project_id, edition, session_id))
        return {'project_id': project_id, 'samples': []}


def _adapter(report_service=None):
    # read_service is unused by the report methods; auth disabled (access_policy
    # None) so authorize() is a no-op — we exercise delegation, not RBAC here.
    return PlatformApiAdapter(read_service=object(), access_policy=None,
                              report_service=report_service)


class TestPortProtocols(unittest.TestCase):
    def test_read_adapter_satisfies_protocol(self):
        self.assertIsInstance(
            PostgresCentralReportReadAdapter(lambda: None), CentralReportReadPort
        )

    def test_write_adapter_satisfies_protocol(self):
        self.assertIsInstance(
            PostgresCentralReportWriteAdapter(lambda: None), CentralReportWritePort
        )


class TestSessionSnapshotAdapter(unittest.TestCase):
    def test_postgres_uuid_values_are_normalized_for_service_identity_checks(self):
        session_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
        project_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
        sample_id = uuid.UUID("33333333-3333-4333-8333-333333333333")

        class _Cursor:
            def execute(self, statement, params):
                self.statement = statement
                self.params = params

            def fetchall(self):
                return [[
                    session_id,
                    project_id,
                    sample_id,
                    "WEB_SESSION",
                    '{"project": {}, "sample": {}}',
                    "fcc.sample.inventory.snapshot.v1",
                ]]

            def close(self):
                pass

        class _Connection:
            def __init__(self):
                self.cursor_instance = _Cursor()

            def cursor(self):
                return self.cursor_instance

            def close(self):
                pass

        connection = _Connection()
        row = PostgresCentralReportReadAdapter(lambda: connection).get_session_snapshot(
            str(session_id)
        )

        self.assertEqual(row["session_id"], str(session_id))
        self.assertEqual(row["project_id"], str(project_id))
        self.assertEqual(row["sample_id"], str(sample_id))


class TestAdapterDelegation(unittest.TestCase):
    def test_list_delegates(self):
        svc = _FakeReportService()
        out = _adapter(svc).list_reports("pid")
        self.assertEqual(out[0]["report_number"], "S-1-E2V1")
        self.assertEqual(svc.calls[0], ("list", "pid"))

    def test_create_delegates_body(self):
        svc = _FakeReportService()
        out = _adapter(svc).create_report("pid", {"edition": "E2V1", "prepared_by": "T"})
        self.assertEqual(out["edition"], "E2V1")
        self.assertEqual(svc.calls[0][0], "create")
        self.assertEqual(svc.calls[0][2]["edition"], "E2V1")
        self.assertEqual(svc.calls[0][2]["prepared_by"], "T")

    def test_citation_delegates_edition(self):
        svc = _FakeReportService()
        _adapter(svc).get_report_citation(
            "pid", edition="E2V1", session_id="session-captured",
        )
        self.assertEqual(
            svc.calls[0],
            ("citation", "pid", "E2V1", "session-captured"),
        )

    def test_unwired_raises_loudly(self):
        for method in (
            lambda a: a.list_reports("pid"),
            lambda a: a.create_report("pid", {"edition": "E2V1"}),
            lambda a: a.get_report_citation("pid", edition="E2V1"),
        ):
            with self.assertRaises(RuntimeError):
                method(_adapter(None))


class TestErrorMapping(unittest.TestCase):
    def test_edition_conflict_is_409(self):
        self.assertEqual(api_error_status(ReportEditionConflictError("dup")), 409)

    def test_report_backend_failure_is_503(self):
        self.assertEqual(api_error_status(CentralReportError("down")), 503)

    def test_unknown_project_is_404(self):
        self.assertEqual(api_error_status(ProjectNotFoundError("x")), 404)

    def test_validation_is_400(self):
        self.assertEqual(api_error_status(ValueError("edition required")), 400)


class TestContractCompleteness(unittest.TestCase):
    def test_operations_routes_permissions_present(self):
        for op in _REPORT_OPS:
            self.assertIn(op, PLATFORM_API_OPERATIONS, f"{op} missing operation")
            self.assertIn(op, PLATFORM_API_ROUTES, f"{op} missing route")
            self.assertIn(op, PLATFORM_API_PERMISSIONS, f"{op} missing permission")

    def test_permission_tiers_reuse_existing_tokens(self):
        # No new grantable token introduced (bijection unchanged).
        self.assertEqual(PLATFORM_API_PERMISSIONS['list_reports'], 'platform:read')
        self.assertEqual(PLATFORM_API_PERMISSIONS['get_report_citation'], 'platform:read')
        self.assertEqual(PLATFORM_API_PERMISSIONS['create_report'], 'platform:admin')

    def test_routes_under_project_path(self):
        self.assertEqual(
            PLATFORM_API_ROUTES['list_reports'],
            ('GET', '/platform/projects/{project_id}/reports'),
        )
        self.assertEqual(
            PLATFORM_API_ROUTES['create_report'],
            ('POST', '/platform/projects/{project_id}/reports'),
        )
        self.assertEqual(
            PLATFORM_API_ROUTES['get_report_citation'],
            ('GET', '/platform/projects/{project_id}/report-citation'),
        )

    def test_create_report_has_request_schema(self):
        self.assertEqual(
            PLATFORM_API_OPERATIONS['create_report']['request'], 'CreateReportRequest'
        )
        self.assertEqual(
            PLATFORM_API_OPERATIONS['create_report']['response'], 'ReportEnvelope'
        )


if __name__ == "__main__":
    unittest.main()
