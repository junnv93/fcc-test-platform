import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.extraction_evidence import EXTRACTION_REQUIRED_REPOSITORIES


project_root = Path(__file__).parent.parent
CLI_PATH = project_root / 'scripts' / 'platform_extraction_evidence.py'


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


class TestPlatformExtractionEvidenceCli(unittest.TestCase):
    def test_valid_manifest_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'extraction.json'
            path.write_text(json.dumps(_valid_manifest()), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(json.loads(completed.stdout), {'valid': True, 'issues': []})

    def test_invalid_manifest_returns_one_with_issues(self):
        manifest = _valid_manifest()
        manifest['repositories']['fcc-test-platform']['extracted'] = False
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'extraction.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')

            completed = _run_cli('validate', str(path))

        self.assertEqual(completed.returncode, 1)
        self.assertIn('not_extracted', {issue['code'] for issue in json.loads(completed.stdout)['issues']})

    def test_read_error_returns_two(self):
        completed = _run_cli('validate', 'missing.json')

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)['issues'][0]['code'], 'read_error')

    def test_template_outputs_required_repositories_without_claiming_extraction(self):
        completed = _run_cli('template', '--evidence-id', 'extract-template')

        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['evidence_id'], 'extract-template')
        self.assertEqual(set(payload['repositories']), set(EXTRACTION_REQUIRED_REPOSITORIES))
        self.assertFalse(payload['compatible'])
        self.assertFalse(payload['repositories']['fcc-test-contracts']['extracted'])

    def test_cli_import_boundary_excludes_file_copy_or_runtime_dependencies(self):
        tree = ast.parse(CLI_PATH.read_text(encoding='utf-8'))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden = {'hashlib', 'shutil', 'requests', 'httpx', 'fastapi', 'subprocess', 'sqlite3', 'os'}
        self.assertFalse(sorted(forbidden.intersection(imports)))


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, 'scripts/platform_extraction_evidence.py', *args],
        cwd=str(project_root),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


if __name__ == '__main__':
    unittest.main()
