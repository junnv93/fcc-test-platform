"""Platform AsyncAPI (chamber progress WS) schema + artifact drift (P7/B4, 2026-06-18).

중앙 진행 릴레이 WS 채널의 계약 문서 봉인:

  - build_platform_asyncapi_schema 가 AsyncAPI 3.0 + chamber_progress 채널 + 메시지.
  - WS operation 이 PLATFORM_API_ROUTES('WEBSOCKET') + OPERATIONS + PERMISSIONS SSOT 등재.
  - OpenAPI 빌더가 WEBSOCKET 을 real path 에서 제외 + x-fcc-websocket-paths 노출.
  - docs/api/platform-api.asyncapi.json 아티팩트가 빌더 출력과 byte-identical(drift gate).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / 'src'
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from fcc_test_contracts.common.tree_artifacts import resolve_dependency_artifact  # noqa: E402

from application.central_contract.api_contracts import (  # noqa: E402
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_PERMISSIONS,
    PLATFORM_API_ROUTES,
)
from fcc_test_platform.application.api_schema import (  # noqa: E402
    build_platform_asyncapi_schema,
    build_platform_openapi_schema,
)

_ASYNCAPI_ARTIFACT = resolve_dependency_artifact('docs/api/platform-api.asyncapi.json')
_WS_PATH = '/platform/chambers/events'


class TestWsOperationContract(unittest.TestCase):
    def test_ws_route_is_websocket_method(self):
        method, path = PLATFORM_API_ROUTES['subscribe_chamber_progress']
        self.assertEqual(method, 'WEBSOCKET')
        self.assertEqual(path, _WS_PATH)

    def test_ws_permission_is_platform_read(self):
        self.assertEqual(
            PLATFORM_API_PERMISSIONS['subscribe_chamber_progress'], 'platform:read',
        )
        self.assertEqual(
            PLATFORM_API_OPERATIONS['subscribe_chamber_progress']['permission'],
            'platform:read',
        )


class TestOpenapiExcludesWebsocket(unittest.TestCase):
    def test_ws_not_in_real_paths(self):
        oa = build_platform_openapi_schema(None)
        self.assertNotIn(_WS_PATH, oa['paths'])

    def test_ws_in_x_fcc_websocket_paths(self):
        oa = build_platform_openapi_schema(None)
        ws_paths = oa['x-fcc-websocket-paths']
        self.assertIn(_WS_PATH, ws_paths)
        self.assertEqual(ws_paths[_WS_PATH]['operationId'], 'subscribe_chamber_progress')
        self.assertEqual(ws_paths[_WS_PATH]['x-fcc-permission'], 'platform:read')


class TestAsyncapiSchema(unittest.TestCase):
    def setUp(self):
        self.aa = build_platform_asyncapi_schema(None)

    def test_asyncapi_version(self):
        self.assertEqual(self.aa['asyncapi'], '3.0.0')

    def test_channel_is_ws_path(self):
        self.assertIn(_WS_PATH, self.aa['channels'])
        self.assertEqual(self.aa['channels'][_WS_PATH]['address'], _WS_PATH)

    def test_messages_catalog(self):
        msgs = self.aa['components']['messages']
        self.assertIn('ChamberProgressEvent', msgs)
        self.assertIn('ChamberProgressPing', msgs)

    def test_progress_event_payload_refs_shared_schema(self):
        payload = self.aa['components']['messages']['ChamberProgressEvent']['payload']
        # normalized to components/schemas by build_components_schemas.
        self.assertEqual(payload['$ref'], '#/components/schemas/ChamberProgressEvent')
        schemas = self.aa['components']['schemas']
        self.assertIn('ChamberSessionProgress', schemas)
        self.assertIn('ChamberProgressEvent', schemas)

    def test_receive_operation(self):
        op = self.aa['operations']['subscribeChamberProgress']
        self.assertEqual(op['action'], 'receive')
        self.assertEqual(op['x-fcc-permission'], 'platform:read')


class TestArtifactNoDrift(unittest.TestCase):
    def test_asyncapi_artifact_byte_identical(self):
        self.assertTrue(
            _ASYNCAPI_ARTIFACT.exists(),
            'platform-api.asyncapi.json missing — run '
            'python scripts/export_session_api_schemas.py',
        )
        canonical = json.dumps(
            build_platform_asyncapi_schema(None),
            indent=2, sort_keys=True, ensure_ascii=False,
        ) + '\n'
        on_disk = _ASYNCAPI_ARTIFACT.read_text(encoding='utf-8')
        self.assertEqual(
            on_disk, canonical,
            'platform-api.asyncapi.json drifted from build_platform_asyncapi_schema() — '
            'run python scripts/export_session_api_schemas.py',
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
