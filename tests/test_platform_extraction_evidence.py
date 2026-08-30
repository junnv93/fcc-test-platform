import ast
import json
import unittest
from pathlib import Path

from fcc_test_platform.extraction_evidence import (
    EXTRACTION_REQUIRED_REPOSITORIES,
    extraction_package_errors,
    is_extraction_package_valid,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_extraction_evidence.py')
SCHEMA_PATH = project_root / 'docs' / 'platform' / 'extraction_package_evidence.schema.v1.json'


def _valid_manifest() -> dict:
    return {
        'schema_version': 1,
        'evidence_id': 'extract-1',
        'collected_at': '2026-05-15T13:00:00+09:00',
        'manifest_path': 'docs/api/headless_contract_extraction_manifest.v1.json',
        'compatible': True,
        'issues': [],
        'repositories': {
            repo_name: {
                'target_repository': repo_name,
                'target_ref': f'{repo_name}-commit',
                'extracted_at': '2026-05-15T13:00:00+09:00',
                'package_compatible': True,
                'extracted': True,
                'entries': [
                    {
                        'current_path': f'src/{repo_name}/source.py',
                        'future_path': f'pkg/{repo_name}/source.py',
                        'kind': 'python_module',
                        'byte_size': 123,
                        'source_sha256': 'a' * 64,
                        'destination_sha256': 'a' * 64,
                        'copied': True,
                    }
                ],
            }
            for repo_name in EXTRACTION_REQUIRED_REPOSITORIES
        },
    }


class TestPlatformExtractionEvidence(unittest.TestCase):
    def test_valid_manifest_passes(self):
        manifest = _valid_manifest()

        self.assertTrue(is_extraction_package_valid(manifest))
        self.assertEqual(extraction_package_errors(manifest), [])

    def test_rejects_unfinished_or_unexplained_mismatched_copy(self):
        manifest = _valid_manifest()
        repo = manifest['repositories']['fcc-test-contracts']
        repo['extracted'] = False
        repo['entries'][0]['destination_sha256'] = 'b' * 64
        repo['entries'][0]['copied'] = False

        issues = extraction_package_errors(manifest)
        codes = {issue.code for issue in issues}

        self.assertIn('not_extracted', codes)
        self.assertIn('hash_mismatch', codes)
        self.assertIn('entry_not_copied', codes)

    def test_allows_mismatched_copy_when_import_rewrite_transform_is_declared(self):
        manifest = _valid_manifest()
        entry = manifest['repositories']['fcc-test-contracts']['entries'][0]
        entry['destination_sha256'] = 'b' * 64
        entry['transforms'] = [{'type': 'python_import_rewrite', 'count': 2}]

        issues = extraction_package_errors(manifest)

        self.assertNotIn('hash_mismatch', {issue.code for issue in issues})

    def test_rejects_invalid_hash_placeholders(self):
        manifest = _valid_manifest()
        manifest['repositories']['fcc-test-contracts']['entries'][0]['source_sha256'] = '<source sha256>'
        manifest['repositories']['fcc-test-contracts']['entries'][0]['destination_sha256'] = '<source sha256>'

        issues = extraction_package_errors(manifest)

        self.assertIn('invalid_sha256', {issue.code for issue in issues})

    def test_rejects_missing_manifest_entry_when_source_manifest_supplied(self):
        manifest = _valid_manifest()
        extraction_manifest = {
            'repositories': {
                'fcc-test-contracts': {
                    'entries': [
                        {
                            'current_path': 'src/application/headless/api_contracts.py',
                            'future_path': 'fcc_test_contracts/headless/api_contracts.py',
                            'kind': 'python_module',
                        }
                    ]
                },
                'fcc-test-platform': {
                    'entries': [
                        {
                            'current_path': 'docs/platform/cutover_readiness_evidence.schema.v1.json',
                            'future_path': 'docs/cutover_readiness_evidence.schema.v1.json',
                            'kind': 'platform_evidence_schema',
                        }
                    ]
                },
            }
        }

        issues = extraction_package_errors(manifest, extraction_manifest=extraction_manifest)

        self.assertIn('missing_manifest_entry', {issue.code for issue in issues})

    def test_schema_document_matches_required_repositories(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))

        self.assertEqual(schema['owner_repository'], 'fcc-test-platform')
        self.assertEqual(schema['required_repositories'], list(EXTRACTION_REQUIRED_REPOSITORIES))
        self.assertIn('source_sha256 and destination_sha256 must match for every copied entry unless a supported transform is declared', schema['rules'])
        self.assertIn('supported transforms are explicitly declared per entry; current supported transform type is python_import_rewrite', schema['rules'])
        self.assertIn('source_sha256 and destination_sha256 values must be 64 lowercase hex characters', schema['rules'])

    def test_module_import_boundary_excludes_runtime_dependencies(self):
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
            'shutil',
            'hashlib',
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


if __name__ == '__main__':
    unittest.main()
