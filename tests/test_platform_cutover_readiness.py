import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.cutover_readiness import (
    CUTOVER_REQUIRED_EVIDENCE,
    cutover_readiness_errors,
    is_cutover_ready,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_cutover_readiness.py')
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'cutover_readiness_evidence.schema.v1.json'
CENTRAL_DB_SCHEMA_PATH = project_root / 'docs' / 'platform' / 'central_db_schema.v1.json'


def _generic_ref(key: str) -> dict:
    return {
        'evidence_id': f'{key}-evidence',
        'collected_at': '2026-05-15T08:15:00+09:00',
        'validated': True,
        'issues': [],
    }


def _bundle() -> dict:
    return {
        'schema_version': 1,
        'provider_id': 'fcc-unlicensed-conducted',
        'cutover_candidate_id': 'cutover-2026-05-15',
        'evaluated_at': '2026-05-15T08:15:00+09:00',
        'evidence': {key: _generic_ref(key) for key in CUTOVER_REQUIRED_EVIDENCE},
    }


class TestPlatformCutoverReadiness(unittest.TestCase):
    def test_generic_references_without_nested_manifests_fail(self):
        bundle = _bundle()

        self.assertFalse(is_cutover_ready(bundle))
        issues = cutover_readiness_errors(bundle)

        self.assertEqual({issue.code for issue in issues}, {'missing_manifest'})
        self.assertEqual(
            {issue.evidence_key for issue in issues},
            set(CUTOVER_REQUIRED_EVIDENCE),
        )

    def test_rejects_missing_required_evidence(self):
        bundle = _bundle()
        del bundle['evidence']['hardware_smoke']

        issues = cutover_readiness_errors(bundle)

        self.assertIn('missing_required_evidence', {issue.code for issue in issues})
        self.assertIn('hardware_smoke', {issue.evidence_key for issue in issues})

    def test_rejects_failed_generic_evidence_ref(self):
        bundle = _bundle()
        bundle['evidence']['performance_smoke']['validated'] = False
        bundle['evidence']['performance_smoke']['issues'] = ['p95 exceeded']

        issues = cutover_readiness_errors(bundle)
        codes = {issue.code for issue in issues}

        self.assertIn('evidence_not_validated', codes)
        self.assertIn('evidence_has_issues', codes)

    def test_nested_manifest_uses_dedicated_validator(self):
        bundle = _bundle()
        bundle['evidence']['artifact_sync']['manifest'] = {
            'schema_version': 1,
            'provider_id': 'fcc-unlicensed-conducted',
            'session_id': 'session-uuid',
            'sync_job_id': 'sync-job-1',
            'source_root_id': 'lab',
            'destination_root_id': 'server',
            'synced_at': '2026-05-15T08:00:00+09:00',
            'artifact_count': 1,
            'report_output_count': 0,
            'files': [{
                'record_type': 'artifact',
                'relative_path': '../plot.png',
                'storage_backend': 'filesystem',
                'sha256': 'short',
                'byte_size': 123,
                'sync_status': 'synced',
                'source_exists': True,
                'destination_exists': True,
            }],
        }

        issues = cutover_readiness_errors(bundle)
        codes = {issue.code for issue in issues}

        self.assertIn('invalid_relative_path', codes)
        self.assertIn('invalid_sha256', codes)
        self.assertIn('artifact_sync', {issue.evidence_key for issue in issues})

    def test_performance_smoke_manifest_uses_dedicated_validator(self):
        bundle = _bundle()
        bundle['evidence']['performance_smoke']['manifest'] = {
            'schema_version': 1,
            'provider_id': 'fcc-unlicensed-conducted',
            'benchmark_id': 'bench-1',
            'collected_at': '2026-05-15T08:00:00+09:00',
            'target_base_url': 'https://platform.example.com',
            'iterations': 10,
            'workers': 4,
            'max_p95_ms': 500.0,
            'compatible': True,
            'within_budget': True,
            'total_requests': 3,
            'total_failures': 0,
            'routes': [{
                'route': '/health',
                'requests': 10,
                'successes': 10,
                'failures': 0,
                'min_ms': 1.0,
                'mean_ms': 2.0,
                'p95_ms': 999.0,
                'max_ms': 999.0,
            }],
        }

        issues = cutover_readiness_errors(bundle)
        codes = {issue.code for issue in issues}

        self.assertIn('route_p95_exceeded', codes)
        self.assertIn('missing_route', codes)
        self.assertIn('performance_smoke', {issue.evidence_key for issue in issues})

    def test_rbac_assignment_manifest_uses_dedicated_validator(self):
        bundle = _bundle()
        bundle['evidence']['rbac_assignment']['manifest'] = {
            'schema_version': 1,
            'evidence_id': 'rbac-1',
            'collected_at': '2026-05-15T18:30:00+09:00',
            'rbac_source': 'docs/platform/rbac_policy.v1.json',
            'permissions': [],
            'roles': [],
            'users': [],
            'user_roles': [],
        }

        issues = cutover_readiness_errors(bundle)
        codes = {issue.code for issue in issues}

        self.assertIn('missing_permission', codes)
        self.assertIn('missing_role', codes)
        self.assertIn('missing_users', codes)
        self.assertIn('rbac_assignment', {issue.evidence_key for issue in issues})

    def test_idp_deployment_manifest_uses_dedicated_validator(self):
        bundle = _bundle()
        bundle['evidence']['idp_deployment']['manifest'] = {
            'schema_version': 1,
            'evidence_id': 'idp-1',
            'collected_at': '2026-05-15T18:00:00+09:00',
            'provider_key': 'company-idp',
            'issuer': 'https://login.example.com/tenant',
            'discovery_document': {
                'issuer': 'https://login.example.com/tenant',
                'authorization_endpoint': 'https://login.example.com/authorize',
                'token_endpoint': 'https://login.example.com/token',
                'jwks_uri': 'https://login.example.com/keys',
                'response_types_supported': ['code'],
                'code_challenge_methods_supported': ['plain'],
            },
            'jwks': {'fetched': False, 'key_count': 0, 'algorithms': ['HS256']},
            'clients': [{
                'client_id': 'fcc-platform-shell',
                'public_client': True,
                'client_secret_present': True,
                'pkce_required': False,
                'redirect_uris': ['https://platform.example.com/'],
                'post_logout_redirect_uris': ['https://platform.example.com/'],
                'scopes': ['openid', 'profile', 'api://fcc-platform/access'],
            }],
            'login_flow': {
                'browser_login_verified': False,
                'redirect_state_verified': False,
                'token_exchange_verified': False,
                'access_token_audience_verified': False,
                'role_claim_verified': False,
                'errors': ['login failed'],
            },
            'session_persistence': {
                'access_token_storage': 'localStorage',
                'local_storage_used': True,
                'logout_clears_session': False,
            },
        }

        issues = cutover_readiness_errors(bundle)
        codes = {issue.code for issue in issues}

        self.assertIn('pkce_s256_not_supported', codes)
        self.assertIn('jwks_not_fetched', codes)
        self.assertIn('client_secret_present', codes)
        self.assertIn('idp_deployment', {issue.evidence_key for issue in issues})

    def test_frontend_deployment_manifest_uses_dedicated_validator(self):
        bundle = _bundle()
        bundle['evidence']['frontend_deployment']['manifest'] = {
            'schema_version': 1,
            'evidence_id': 'frontend-deploy-1',
            'collected_at': '2026-05-15T16:00:00+09:00',
            'app_url': 'http://localhost:3000',
            'backend_base_url': 'https://api.platform.example.com',
            'hosting_provider': 'managed-static-hosting',
            'build_version': '2026.05.15.1',
            'build_sha256': 'short',
            'deployed': False,
            'tls_valid': False,
            'asset_cache_policy': {
                'immutable_assets': False,
                'html_cache_control': 'max-age=3600',
            },
            'environment': {
                'name': 'production',
                'backend_base_url': 'https://wrong.example.com',
                'variables': [{'name': 'OIDC_CLIENT_SECRET', 'value': 'plain-secret'}],
            },
            'secret_scan': {'status': 'fail', 'findings': [{'path': 'dist/app.js'}]},
        }

        issues = cutover_readiness_errors(bundle)
        codes = {issue.code for issue in issues}

        self.assertIn('non_https_url', codes)
        self.assertIn('frontend_not_deployed', codes)
        self.assertIn('secret_scan_not_passed', codes)
        self.assertIn('frontend_deployment', {issue.evidence_key for issue in issues})

    def test_extraction_package_manifest_fails_closed_without_context(self):
        bundle = _bundle()
        bundle['evidence']['extraction_package']['manifest'] = {
            'schema_version': 1,
            'evidence_id': 'extract-1',
            'collected_at': '2026-05-15T08:00:00+09:00',
            'manifest_path': 'docs/api/headless_contract_extraction_manifest.v1.json',
            'compatible': True,
            'issues': [],
            'repositories': {
                'fcc-test-contracts': {
                    'target_repository': 'fcc-test-contracts',
                    'target_ref': 'commit',
                    'extracted_at': '2026-05-15T08:00:00+09:00',
                    'package_compatible': True,
                    'extracted': False,
                    'entries': [],
                },
                'fcc-test-platform': {
                    'target_repository': 'fcc-test-platform',
                    'target_ref': 'commit',
                    'extracted_at': '2026-05-15T08:00:00+09:00',
                    'package_compatible': True,
                    'extracted': True,
                    'entries': [],
                },
            },
        }

        issues = cutover_readiness_errors(bundle)
        codes = {issue.code for issue in issues}

        self.assertIn('missing_extraction_manifest', codes)
        self.assertIn('extraction_package', {issue.evidence_key for issue in issues})

    def test_extraction_package_manifest_uses_source_manifest_context(self):
        bundle = _bundle()
        bundle['evidence']['extraction_package']['manifest'] = {
            'schema_version': 1,
            'evidence_id': 'extract-1',
            'collected_at': '2026-05-15T08:00:00+09:00',
            'manifest_path': 'docs/api/headless_contract_extraction_manifest.v1.json',
            'compatible': True,
            'issues': [],
            'repositories': {
                'fcc-test-contracts': {
                    'target_repository': 'fcc-test-contracts',
                    'target_ref': 'commit',
                    'extracted_at': '2026-05-15T08:00:00+09:00',
                    'package_compatible': True,
                    'extracted': True,
                    'entries': [{
                        'current_path': 'other.py',
                        'future_path': 'other.py',
                        'kind': 'python_module',
                        'byte_size': 1,
                        'source_sha256': 'a' * 64,
                        'destination_sha256': 'a' * 64,
                        'copied': True,
                    }],
                },
                'fcc-test-platform': {
                    'target_repository': 'fcc-test-platform',
                    'target_ref': 'commit',
                    'extracted_at': '2026-05-15T08:00:00+09:00',
                    'package_compatible': True,
                    'extracted': True,
                    'entries': [{
                        'current_path': 'other.py',
                        'future_path': 'other.py',
                        'kind': 'python_module',
                        'byte_size': 1,
                        'source_sha256': 'a' * 64,
                        'destination_sha256': 'a' * 64,
                        'copied': True,
                    }],
                },
            },
        }
        extraction_manifest = {
            'repositories': {
                'fcc-test-contracts': {
                    'entries': [{
                        'current_path': 'src/application/headless/api_contracts.py',
                        'future_path': 'fcc_test_contracts/headless/api_contracts.py',
                        'kind': 'python_module',
                    }],
                },
                'fcc-test-platform': {
                    'entries': [{
                        'current_path': 'docs/platform/cutover_readiness_evidence.schema.v1.json',
                        'future_path': 'docs/cutover_readiness_evidence.schema.v1.json',
                        'kind': 'platform_evidence_schema',
                    }],
                },
            }
        }

        issues = cutover_readiness_errors(bundle, extraction_manifest=extraction_manifest)

        self.assertIn('missing_manifest_entry', {issue.code for issue in issues})

    def test_hardware_smoke_manifest_uses_dedicated_validator(self):
        bundle = _bundle()
        bundle['evidence']['hardware_smoke']['manifest'] = {
            'schema_version': 1,
            'db_path': 'headless.fcc.db',
            'job': {
                'id': 7,
                'status': 'failed',
                'excel_path': 'smoke.xlsx',
                'session_id': 42,
            },
            'events': [{'event_type': 'job_execution_started'}],
            'artifacts': [],
            'report_requests': [],
        }

        issues = cutover_readiness_errors(bundle)
        codes = {issue.code for issue in issues}

        self.assertIn('job_not_completed', codes)
        self.assertIn('missing_event_types', codes)
        self.assertIn('hardware_smoke', {issue.evidence_key for issue in issues})

    def test_db_migration_manifest_requires_schema_context(self):
        bundle = _bundle()
        bundle['evidence']['db_migration']['manifest'] = {
            'schema_version': 1,
            'migration_id': '001_initial_central_db',
            'database_engine': 'postgresql',
            'database_name': 'fcc_platform',
            'migration_status': 'applied',
            'applied_at': '2026-05-15T08:00:00+09:00',
            'applied_by': 'platform-ci',
            'schema_contract_sha256': 'a' * 64,
            'ddl_sha256': 'b' * 64,
            'tables': {},
        }

        issues = cutover_readiness_errors(bundle)
        self.assertIn('missing_central_db_schema', {issue.code for issue in issues})

        schema = json.loads(CENTRAL_DB_SCHEMA_PATH.read_text(encoding='utf-8'))
        issues_with_schema = cutover_readiness_errors(bundle, central_db_schema=schema)
        self.assertIn('missing_evidence_tables', {issue.code for issue in issues_with_schema})

    def test_ingestion_execution_manifest_uses_dedicated_validator(self):
        bundle = _bundle()
        bundle['evidence']['ingestion_execution']['manifest'] = {
            'schema_version': 1,
            'evidence_id': 'ingest-1',
            'collected_at': '2026-05-15T19:00:00+09:00',
            'provider_id': 'fcc-unlicensed-conducted',
            'session_id': 'session-uuid',
            'database_name': 'fcc_platform',
            'writer_adapter': 'postgres',
            'plan_sha256': 'short',
            'executed': False,
            'transaction_committed': False,
            'transaction_rolled_back': True,
            'attempts': 0,
            'errors': ['deadlock'],
            'steps': [],
        }

        issues = cutover_readiness_errors(bundle)
        codes = {issue.code for issue in issues}

        self.assertIn('invalid_plan_sha256', codes)
        self.assertIn('transaction_not_committed', codes)
        self.assertIn('missing_steps', codes)
        self.assertIn('ingestion_execution', {issue.evidence_key for issue in issues})

    def test_rejects_manifest_with_unresolved_placeholder_values(self):
        bundle = _bundle()
        bundle['evidence']['ingestion_execution']['manifest'] = {
            'schema_version': 1,
            'evidence_id': '<ingestion-evidence-id>',
            'collected_at': '2026-05-15T19:00:00+09:00',
            'provider_id': '<provider-id>',
            'session_id': 'session-uuid',
            'database_name': 'fcc_platform',
            'writer_adapter': 'postgres',
            'steps': [{'target_table': '<table>', 'operation': 'upsert'}],
        }

        issues = cutover_readiness_errors(bundle)
        placeholder = [i for i in issues if i.code == 'placeholder_evidence_value']

        self.assertTrue(placeholder, 'placeholder leaves must be rejected as non-real evidence')
        self.assertEqual({i.evidence_key for i in placeholder}, {'ingestion_execution'})
        flagged_paths = {i.path for i in placeholder}
        self.assertIn('evidence.ingestion_execution.manifest.evidence_id', flagged_paths)
        self.assertIn('evidence.ingestion_execution.manifest.provider_id', flagged_paths)
        self.assertTrue(any('steps[0].target_table' in path for path in flagged_paths))

    def test_clean_manifest_emits_no_placeholder_issue(self):
        bundle = _bundle()
        bundle['evidence']['rbac_assignment']['manifest'] = {
            'schema_version': 1,
            'evidence_id': 'rbac-1',
            'collected_at': '2026-05-15T18:30:00+09:00',
            'rbac_source': 'docs/platform/rbac_policy.v1.json',
            'permissions': [],
            'roles': [],
            'users': [],
            'user_roles': [],
        }

        codes = {issue.code for issue in cutover_readiness_errors(bundle)}

        self.assertNotIn('placeholder_evidence_value', codes)

    def test_schema_document_matches_required_evidence(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(schema['owner_repository'], 'fcc-test-platform')
        self.assertEqual(schema['required_evidence_keys'], list(CUTOVER_REQUIRED_EVIDENCE))
        self.assertEqual(schema['manifest_required_evidence_keys'], list(CUTOVER_REQUIRED_EVIDENCE))
        self.assertIn('hardware_run', schema['forbidden_operations'])
        self.assertIn('artifact_sync', schema['nested_manifest_validators'])
        self.assertIn('ingestion_execution', schema['nested_manifest_validators'])
        self.assertIn('extraction_package', schema['nested_manifest_validators'])
        self.assertIn('hardware_smoke', schema['nested_manifest_validators'])
        self.assertIn('performance_smoke', schema['nested_manifest_validators'])
        self.assertIn('idp_deployment', schema['nested_manifest_validators'])
        self.assertIn('frontend_deployment', schema['nested_manifest_validators'])
        self.assertIn('frontend_browser_qa', schema['nested_manifest_validators'])
        self.assertIn('rbac_assignment', schema['nested_manifest_validators'])

    def test_module_import_boundary_excludes_runtime_side_effect_dependencies(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden_prefixes = (
            'application.reporting',
            'reporting',
            'infrastructure',
            'fastapi',
            'sqlalchemy',
            'pandas',
            'openpyxl',
            'sqlite3',
            'requests',
            'httpx',
            'subprocess',
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


if __name__ == '__main__':
    unittest.main()
