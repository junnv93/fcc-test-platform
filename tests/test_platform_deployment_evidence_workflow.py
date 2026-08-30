import json
import subprocess
import sys
import tempfile
import unittest
import ast
from pathlib import Path
from unittest.mock import patch

from scripts.platform_deployment_evidence_workflow import assemble_deployment_evidence
from tests.test_platform_frontend_deployment_collect import _probe
from tests.test_platform_frontend_browser_qa_collector import _Driver
# Not tests.test_provider_service_deployment_evidence_cli: that file exercises a
# provider CLI, and a platform test must not depend on provider-lane test code.
# The test-root-rooted spelling is required — the delivery closure indexes names
# rooted at the test root, so a tests.-prefixed import is invisible to it.
from support.provider_service_evidence_manifest import (
    valid_provider_service_deployment_manifest as _service_manifest,
)
from scripts.platform_frontend_browser_qa import Viewport, collect_manifest as collect_browser_qa
from scripts.platform_frontend_deployment_collect import collect_frontend_deployment_evidence
from scripts.platform_idp_deployment_collect import (
    ClientRegistration,
    collect_idp_deployment_evidence,
)
from tests.test_platform_idp_deployment_collect import _fetcher


project_root = Path(__file__).parent.parent
SCRIPT_PATH = project_root / 'scripts' / 'platform_deployment_evidence_workflow.py'


class TestPlatformDeploymentEvidenceWorkflow(unittest.TestCase):
    def test_assembles_valid_deployment_evidence_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sources = _write_valid_sources(root)
            evidence_root = root / 'cutover'

            summary = assemble_deployment_evidence(evidence_root=evidence_root, source_paths=sources)

            self.assertTrue(summary['valid'])
            self.assertTrue(summary['promoted'])
            self.assertTrue((evidence_root / 'provider_service_deployment.json').is_file())
            self.assertTrue((evidence_root / 'idp_deployment.json').is_file())
            self.assertTrue((evidence_root / 'frontend_deployment.json').is_file())
            self.assertTrue((evidence_root / 'frontend_browser_qa.json').is_file())
            self.assertEqual(
                summary['slots']['idp_deployment']['source_sha256'],
                summary['slots']['idp_deployment']['destination_sha256'],
            )
            self.assertEqual(summary['slots']['frontend_browser_qa']['issues'], [])

    def test_missing_input_reports_machine_readable_issue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sources = _write_valid_sources(root)
            sources['idp_deployment'] = root / 'missing.json'

            summary = assemble_deployment_evidence(evidence_root=root / 'cutover', source_paths=sources)

        self.assertFalse(summary['readable'])
        self.assertFalse(summary['valid'])
        self.assertFalse(summary['promoted'])
        self.assertEqual(summary['slots']['idp_deployment']['issues'][0]['code'], 'missing_evidence_file')

    def test_invalid_input_does_not_promote_partial_cutover_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sources = _write_valid_sources(root)
            invalid_service = json.loads(sources['service_deployment'].read_text(encoding='utf-8'))
            invalid_service['health_check']['status_code'] = 503
            sources['service_deployment'].write_text(json.dumps(invalid_service), encoding='utf-8')
            evidence_root = root / 'cutover'

            summary = assemble_deployment_evidence(evidence_root=evidence_root, source_paths=sources)
            self.assertFalse(summary['valid'])
            self.assertFalse(summary['promoted'])
            self.assertFalse(summary['slots']['service_deployment']['copied'])
            self.assertFalse(summary['slots']['idp_deployment']['copied'])
            self.assertFalse((evidence_root / 'provider_service_deployment.json').exists())
            self.assertFalse((evidence_root / 'idp_deployment.json').exists())

    def test_invalid_input_does_not_overwrite_existing_promoted_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sources = _write_valid_sources(root)
            evidence_root = root / 'cutover'
            stale_path = evidence_root / 'provider_service_deployment.json'
            evidence_root.mkdir()
            stale_path.write_text('{"stale": true}', encoding='utf-8')
            invalid_service = json.loads(sources['service_deployment'].read_text(encoding='utf-8'))
            invalid_service['health_check']['status_code'] = 503
            sources['service_deployment'].write_text(json.dumps(invalid_service), encoding='utf-8')

            summary = assemble_deployment_evidence(evidence_root=evidence_root, source_paths=sources)
            stale_payload = json.loads(stale_path.read_text(encoding='utf-8'))

        self.assertFalse(summary['promoted'])
        self.assertEqual(stale_payload, {'stale': True})

    def test_cli_require_valid_returns_one_for_invalid_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sources = _write_valid_sources(root)
            invalid_service = json.loads(sources['service_deployment'].read_text(encoding='utf-8'))
            invalid_service['health_check']['status_code'] = 503
            sources['service_deployment'].write_text(json.dumps(invalid_service), encoding='utf-8')

            completed = subprocess.run(
                [
                    sys.executable,
                    'scripts/platform_deployment_evidence_workflow.py',
                    '--evidence-root', str(root / 'cutover'),
                    '--service-deployment', str(sources['service_deployment']),
                    '--idp-deployment', str(sources['idp_deployment']),
                    '--frontend-deployment', str(sources['frontend_deployment']),
                    '--frontend-browser-qa', str(sources['frontend_browser_qa']),
                    '--require-valid',
                ],
                cwd=str(project_root),
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout)
        self.assertIn('health_check_failed', {issue['code'] for issue in payload['slots']['service_deployment']['issues']})

    def test_workflow_import_boundary_excludes_runtime_side_effect_dependencies(self):
        tree = ast.parse(SCRIPT_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'requests', 'httpx', 'selenium', 'subprocess', 'urllib', 'fastapi', 'sqlalchemy', 'psycopg'}
        self.assertFalse(sorted(forbidden.intersection(imports)))


def _write_valid_sources(root: Path) -> dict[str, Path]:
    build_root = root / 'dist'
    build_root.mkdir()
    (build_root / 'index.html').write_text('<script src="/app.123.js"></script>', encoding='utf-8')
    (build_root / 'app.123.js').write_text('console.log("ok")', encoding='utf-8')
    artifacts_root = root / 'artifacts'
    with patch('scripts.platform_frontend_browser_qa._probe_provider_routes', return_value=[]):
        browser_manifest = collect_browser_qa(
            driver=_Driver(),
            app_url='http://127.0.0.1:3000',
            provider_api_url='http://127.0.0.1:8000',
            evidence_id='qa-1',
            artifact_root=artifacts_root,
            screenshot_dir=artifacts_root / 'frontend' / 'browser-qa',
            auth_flow_verified=True,
            bearer_token='',
            viewports=[Viewport(name='desktop', width=1440, height=900)],
            route_timeout_seconds=1.0,
        )
    frontend_manifest = collect_frontend_deployment_evidence(
        evidence_id='frontend-deploy-1',
        app_url='https://platform.example.com/',
        backend_base_url='https://api.platform.example.com/health',
        hosting_provider='managed-static-hosting',
        build_version='2026.05.15.1',
        build_root=build_root,
        environment_name='production',
        environment_variables={'API_BASE_URL': 'https://api.platform.example.com'},
        immutable_asset_urls=['https://platform.example.com/app.123.js'],
        secret_scan_json=None,
        timeout_seconds=1.0,
        url_probe=_probe,
    )
    idp_manifest = collect_idp_deployment_evidence(
        evidence_id='idp-deploy-1',
        provider_key='company-idp',
        issuer='https://login.example.com/tenant',
        client=ClientRegistration(
            client_id='fcc-platform-shell',
            redirect_uris=['https://platform.example.com/'],
            post_logout_redirect_uris=['https://platform.example.com/'],
            scopes=['openid', 'profile', 'api://fcc-platform/access'],
            public_client=True,
            client_secret_present=False,
            pkce_required=True,
        ),
        login_flow={
            'browser_login_verified': True,
            'redirect_state_verified': True,
            'token_exchange_verified': True,
            'access_token_audience_verified': True,
            'role_claim_verified': True,
            'errors': [],
        },
        session_persistence={
            'access_token_storage': 'sessionStorage',
            'local_storage_used': False,
            'logout_clears_session': True,
        },
        timeout_seconds=1.0,
        json_fetcher=_fetcher,
    )
    manifests = {
        'service_deployment': _service_manifest(),
        'idp_deployment': idp_manifest,
        'frontend_deployment': frontend_manifest,
        'frontend_browser_qa': browser_manifest,
    }
    paths = {}
    for key, manifest in manifests.items():
        path = root / f'{key}.json'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        paths[key] = path
    return paths


if __name__ == '__main__':
    unittest.main()
