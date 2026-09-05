import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from fcc_test_platform.frontend_browser_qa_cli import (
    DEFAULT_VIEWPORTS,
    Viewport,
    _parse_viewport,
    collect_manifest,
    main,
)


class _Element:
    def __init__(self):
        self.clicked = False

    def click(self):
        self.clicked = True

    def is_displayed(self):
        return True


class _Driver:
    capabilities = {'browserName': 'chrome', 'browserVersion': '125.0'}

    def __init__(self):
        self.urls = []
        self.sizes = []

    def set_window_size(self, width, height):
        self.sizes.append((width, height))

    def get(self, url):
        self.urls.append(url)

    def find_element(self, by, selector):
        return _Element()

    def execute_script(self, script, *args):
        return True

    def get_log(self, kind):
        return []

    def save_screenshot(self, path):
        Path(path).write_bytes(b'png-bytes')


class TestPlatformFrontendBrowserQaCollector(unittest.TestCase):
    def test_parse_viewport(self):
        viewport = _parse_viewport('desktop=1440x900')

        self.assertEqual(viewport, Viewport(name='desktop', width=1440, height=900))

    def test_parse_viewport_rejects_invalid_shape(self):
        with self.assertRaises(ValueError):
            _parse_viewport('desktop')

    def test_collect_manifest_builds_valid_browser_qa_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch('fcc_test_platform.frontend_browser_qa_cli._probe_provider_routes', return_value=[]):
                manifest = collect_manifest(
                    driver=_Driver(),
                    app_url='http://127.0.0.1:3000',
                    provider_api_url='http://127.0.0.1:8000',
                    evidence_id='qa-1',
                    artifact_root=tmp / 'artifacts',
                    screenshot_dir=tmp / 'artifacts' / 'frontend' / 'browser-qa',
                    auth_flow_verified=True,
                    bearer_token='',
                    viewports=[Viewport(name='desktop', width=1440, height=900)],
                    route_timeout_seconds=1.0,
                )

        self.assertTrue(manifest['auth_flow_verified'])
        self.assertTrue(manifest['central_backend_verified'])
        self.assertTrue(manifest['provider_contract_routes_verified'])
        self.assertEqual(manifest['viewport_results'][0]['views_verified'], ['overview', 'jobs', 'session', 'reports'])
        self.assertEqual(manifest['screenshots'][0]['relative_path'], 'frontend/browser-qa/qa-1/desktop.png')
        self.assertRegex(manifest['screenshots'][0]['sha256'], r'^[0-9a-f]{64}$')

    def test_route_failures_keep_manifest_non_claiming(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch('fcc_test_platform.frontend_browser_qa_cli._probe_provider_routes', return_value=['/health: refused']):
                manifest = collect_manifest(
                    driver=_Driver(),
                    app_url='http://127.0.0.1:3000',
                    provider_api_url='http://127.0.0.1:8000',
                    evidence_id='qa-1',
                    artifact_root=tmp / 'artifacts',
                    screenshot_dir=tmp / 'artifacts' / 'frontend' / 'browser-qa',
                    auth_flow_verified=False,
                    bearer_token='',
                    viewports=[Viewport(name='desktop', width=1440, height=900)],
                    route_timeout_seconds=1.0,
                )

        self.assertFalse(manifest['auth_flow_verified'])
        self.assertFalse(manifest['central_backend_verified'])
        self.assertFalse(manifest['provider_contract_routes_verified'])
        self.assertIn('/health: refused', manifest['viewport_results'][0]['failed_requests'])

    def test_bearer_token_marks_auth_verified_when_route_probe_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch('fcc_test_platform.frontend_browser_qa_cli._probe_provider_routes', return_value=[]) as probe:
                manifest = collect_manifest(
                    driver=_Driver(),
                    app_url='http://127.0.0.1:3000',
                    provider_api_url='http://127.0.0.1:8000',
                    evidence_id='qa-1',
                    artifact_root=tmp / 'artifacts',
                    screenshot_dir=tmp / 'artifacts' / 'frontend' / 'browser-qa',
                    auth_flow_verified=False,
                    bearer_token='token',
                    viewports=[Viewport(name='desktop', width=1440, height=900)],
                    route_timeout_seconds=1.0,
                )

        self.assertTrue(manifest['auth_flow_verified'])
        probe.assert_called_once_with('http://127.0.0.1:8000', 1.0, bearer_token='token')

    def test_cli_dependency_error_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / 'qa.json'
            stderr = StringIO()
            with patch('fcc_test_platform.frontend_browser_qa_cli._create_driver', side_effect=RuntimeError('driver missing')):
                with redirect_stderr(stderr):
                    exit_code = main([
                    '--app-url',
                    'http://127.0.0.1:3000',
                    '--provider-api-url',
                    'http://127.0.0.1:8000',
                    '--evidence-id',
                    'qa-1',
                    '--output',
                    str(output),
                    ])

        self.assertEqual(exit_code, 2)
        self.assertFalse(json.loads(stderr.getvalue())['collected'])

    def test_default_viewports_cover_responsive_breakpoints(self):
        parsed = [_parse_viewport(value) for value in DEFAULT_VIEWPORTS]

        self.assertEqual([viewport.name for viewport in parsed], ['desktop', 'tablet', 'mobile'])


if __name__ == '__main__':
    unittest.main()
