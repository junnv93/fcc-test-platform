"""P6-C.2 — GET /platform/projects/{id}/progress endpoint seal.

Seals the platform progress endpoint wiring: contract declaration (operation /
route / permission / schema), the adapter handler (authorize + serialize via the
injected CentralProgressReadPort), and the platform:read authorization gate.
"""
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fcc_test_contracts.common.access_policy import ApiAccessPolicy, ApiPrincipal  # noqa: E402
from application.central_contract.api_contracts import (  # noqa: E402
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_PERMISSIONS,
    PLATFORM_API_ROUTES,
    PLATFORM_API_SCHEMAS,
)
from domain.models.progress_rollup import ProgressBucketRollup  # noqa: E402
from fcc_test_platform.api.platform_routes import PlatformApiAdapter  # noqa: E402


class _FakeProgressReadPort:
    def __init__(self, rollups):
        self._rollups = rollups
        self.calls = []

    def get_project_progress(self, project_id):
        self.calls.append(project_id)
        return self._rollups


_ROLLUPS = [
    ProgressBucketRollup(
        progress_area="unlicensed_conducted", progress_bucket_id="unii_1_11ax",
        planned_minutes=15.0, completed_minutes=3.0, total_conditions=3,
        priced_conditions=2, unpriced_conditions=1, unbucketable_conditions=0,
    ),
    ProgressBucketRollup(
        progress_area="unlicensed_conducted", progress_bucket_id=None,
        planned_minutes=0.0, completed_minutes=0.0, total_conditions=1,
        priced_conditions=0, unpriced_conditions=1, unbucketable_conditions=1,
    ),
]


class TestContractDeclares(unittest.TestCase):
    def test_operation_route_permission_schema(self):
        self.assertIn('get_project_progress', PLATFORM_API_OPERATIONS)
        self.assertEqual(
            PLATFORM_API_ROUTES['get_project_progress'],
            ('GET', '/platform/projects/{project_id}/progress'),
        )
        self.assertEqual(
            PLATFORM_API_PERMISSIONS['get_project_progress'], 'platform:read'
        )
        self.assertIn('ProjectProgressList', PLATFORM_API_SCHEMAS)
        self.assertIn('ProgressBucketEnvelope', PLATFORM_API_SCHEMAS)

    def test_no_new_grantable_token(self):
        # progress reuses platform:read — no new token (rbac bijection unchanged)
        self.assertEqual(
            PLATFORM_API_PERMISSIONS['get_project_progress'],
            PLATFORM_API_PERMISSIONS['get_project_coverage'],
        )


class TestHandler(unittest.TestCase):
    def _adapter(self, principal, port):
        return PlatformApiAdapter(
            None,
            access_policy=ApiAccessPolicy(PLATFORM_API_OPERATIONS),
            principal=principal,
            progress_read_port=port,
        )

    def test_serializes_rollups(self):
        port = _FakeProgressReadPort(_ROLLUPS)
        adapter = self._adapter(
            ApiPrincipal.from_permissions('eng-1', ['platform:read']), port
        )
        pid = str(uuid.uuid4())
        result = adapter.get_project_progress(pid)
        self.assertEqual(port.calls, [pid])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['progress_bucket_id'], 'unii_1_11ax')
        self.assertAlmostEqual(result[0]['percent'], 20.0)
        # bucket with no priced time → percent null (no fake 0%)
        self.assertIsNone(result[1]['percent'])
        self.assertIsNone(result[1]['progress_bucket_id'])
        self.assertEqual(result[1]['unpriced_conditions'], 1)

    def test_authorization_gate(self):
        from fcc_test_platform.api.platform_routes import (
            PlatformAuthorizationError,
        )
        port = _FakeProgressReadPort(_ROLLUPS)
        denied = self._adapter(
            ApiPrincipal.from_permissions('eng-2', ['headless:read']), port
        )
        with self.assertRaises(PlatformAuthorizationError):
            denied.get_project_progress(str(uuid.uuid4()))
        self.assertEqual(port.calls, [])  # not reached when denied

    def test_unwired_port_raises(self):
        adapter = PlatformApiAdapter(None, access_policy=None, progress_read_port=None)
        with self.assertRaises(RuntimeError):
            adapter.get_project_progress(str(uuid.uuid4()))


if __name__ == '__main__':
    unittest.main()
