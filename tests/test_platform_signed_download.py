import ast
from datetime import datetime, timezone
import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fcc_test_platform.download_policy import build_download_grant
from fcc_test_platform.signed_download import (
    SIGNED_URL_STORAGE_BACKENDS,
    build_signed_download_url,
    verify_signed_download_request,
)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact


project_root = Path(__file__).parent.parent
MODULE_PATH = resolve_repo_artifact(__file__, 'src/application/headless/platform_signed_download.py')
POLICY_PATH = project_root / 'docs' / 'platform' / 'signed_download_policy.v1.json'


def _grant(storage_backend: str = 'object_store') -> dict:
    return build_download_grant(
        record_type='report_output',
        record={
            'id': 'report-1',
            'relative_path': 'projects/p/sessions/s/reports/r/report.pdf',
            'storage_backend': storage_backend,
            'exists': True,
            'sha256': 'a' * 64,
            'byte_size': 123,
        },
        principal_permissions=['report_automation:read'],
        now=datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc),
        disposition='attachment',
    ).to_dict()


class TestPlatformSignedDownload(unittest.TestCase):
    def test_builds_bounded_signed_url_without_exposing_secret(self):
        signed = build_signed_download_url(
            grant=_grant(),
            base_url='https://files.platform.example.com',
            signing_secret='s' * 32,
            now=datetime(2026, 5, 15, 0, 1, tzinfo=timezone.utc),
        )
        query = parse_qs(urlparse(signed.url).query)

        self.assertEqual(signed.storage_backend, 'object_store')
        self.assertEqual(query['path'], ['projects/p/sessions/s/reports/r/report.pdf'])
        self.assertEqual(query['backend'], ['object_store'])
        self.assertEqual(len(query['signature'][0]), 64)
        self.assertNotIn('s' * 32, signed.url)
        self.assertEqual(signed.headers['X-Content-Type-Options'], 'nosniff')

    def test_verifies_signed_url_with_same_canonical_payload(self):
        signed = build_signed_download_url(
            grant=_grant(),
            base_url='https://files.platform.example.com',
            signing_secret='s' * 32,
            now=datetime(2026, 5, 15, 0, 1, tzinfo=timezone.utc),
        )

        verified = verify_signed_download_request(
            query_string=urlparse(signed.url).query,
            signing_secret='s' * 32,
            now=datetime(2026, 5, 15, 0, 2, tzinfo=timezone.utc),
        )

        self.assertEqual(verified.relative_path, signed.relative_path)
        self.assertEqual(verified.storage_backend, 'object_store')
        self.assertEqual(verified.disposition, 'attachment')
        self.assertEqual(verified.sha256, 'a' * 64)

    def test_rejects_tampered_or_duplicate_signed_url_parameters(self):
        signed = build_signed_download_url(
            grant=_grant(),
            base_url='https://files.platform.example.com',
            signing_secret='s' * 32,
            now=datetime(2026, 5, 15, 0, 1, tzinfo=timezone.utc),
        )
        tampered = urlparse(signed.url).query.replace('report.pdf', 'other.pdf')

        with self.assertRaises(PermissionError):
            verify_signed_download_request(
                query_string=tampered,
                signing_secret='s' * 32,
                now=datetime(2026, 5, 15, 0, 2, tzinfo=timezone.utc),
            )

        with self.assertRaisesRegex(ValueError, 'duplicate'):
            verify_signed_download_request(
                query_string=f'{urlparse(signed.url).query}&path=other.pdf',
                signing_secret='s' * 32,
                now=datetime(2026, 5, 15, 0, 2, tzinfo=timezone.utc),
            )

    def test_rejects_filesystem_grants_and_insecure_base_url(self):
        with self.assertRaisesRegex(ValueError, 'object-store'):
            build_signed_download_url(
                grant=_grant('filesystem'),
                base_url='https://files.platform.example.com',
                signing_secret='s' * 32,
                now=datetime(2026, 5, 15, 0, 1, tzinfo=timezone.utc),
            )

        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            build_signed_download_url(
                grant=_grant(),
                base_url='http://files.platform.example.com',
                signing_secret='s' * 32,
                now=datetime(2026, 5, 15, 0, 1, tzinfo=timezone.utc),
            )

    def test_rejects_expired_grant_and_weak_secret(self):
        with self.assertRaises(PermissionError):
            build_signed_download_url(
                grant=_grant(),
                base_url='https://files.platform.example.com',
                signing_secret='s' * 32,
                now=datetime(2026, 5, 15, 0, 6, tzinfo=timezone.utc),
            )

        with self.assertRaisesRegex(ValueError, 'signing_secret'):
            build_signed_download_url(
                grant=_grant(),
                base_url='https://files.platform.example.com',
                signing_secret='short',
                now=datetime(2026, 5, 15, 0, 1, tzinfo=timezone.utc),
            )

    def test_policy_document_matches_supported_backends(self):
        policy = json.loads(POLICY_PATH.read_text(encoding='utf-8'))

        self.assertEqual(policy['owner_repository'], 'fcc-test-platform')
        self.assertEqual(set(policy['supported_storage_backends']), set(SIGNED_URL_STORAGE_BACKENDS))
        self.assertIn('filesystem grants continue to use platform_download_proxy rather than signed URLs.', policy['rules'])
        self.assertIn('signature covers path, backend, expires, disposition, and sha256.', policy['rules'])

    def test_module_import_boundary_excludes_cloud_sdks_and_provider_runtime(self):
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
            'boto3',
            'azure',
            'google',
            'fastapi',
            'sqlalchemy',
            'requests',
            'httpx',
            'os',
        )
        self.assertFalse([
            module for module in imports
            if module == 'infrastructure' or module.startswith(forbidden_prefixes)
        ])


if __name__ == '__main__':
    unittest.main()
