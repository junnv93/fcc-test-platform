import hashlib
from datetime import datetime, timezone
import unittest
from pathlib import Path
import tempfile

from fcc_test_platform.download_policy import build_download_grant
from fcc_test_platform.download_proxy import open_verified_stream, resolve_download_grant
from fcc_test_contracts.common.report_output_download import DownloadIntegrityError


class TestPlatformDownloadProxy(unittest.TestCase):
    def test_resolves_valid_grant_under_injected_storage_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            relative_path = 'projects/p/sessions/s/reports/r/report.pdf'
            file_path = root / relative_path
            file_path.parent.mkdir(parents=True)
            file_path.write_bytes(b'report bytes')
            digest = hashlib.sha256(b'report bytes').hexdigest()
            grant = build_download_grant(
                record_type='report_output',
                record={
                    'relative_path': relative_path,
                    'storage_backend': 'filesystem',
                    'exists': True,
                    'sha256': digest,
                    'byte_size': len(b'report bytes'),
                },
                principal_permissions=['report_automation:read'],
                now=datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc),
                disposition='attachment',
            )

            resolution = resolve_download_grant(
                grant=grant.to_dict(),
                storage_roots={'filesystem': root},
                now=datetime(2026, 5, 15, 0, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(resolution.relative_path, relative_path)
        self.assertEqual(resolution.sha256, digest)
        self.assertEqual(resolution.byte_size, len(b'report bytes'))
        self.assertEqual(resolution.headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn('attachment;', resolution.headers['Content-Disposition'])

    def test_rejects_expired_grant(self):
        grant = {
            'relative_path': 'a/b.txt',
            'storage_backend': 'filesystem',
            'expires_at': '2026-05-15T00:00:00Z',
            'disposition': 'attachment',
            'metadata': {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(PermissionError):
                resolve_download_grant(
                    grant=grant,
                    storage_roots={'filesystem': Path(tmpdir)},
                    now=datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc),
                )

    def test_resolve_carries_expected_sha_without_rehashing(self):
        # FE-P6 (2026-05-29) single-pass: resolve no longer hashes the file —
        # it carries the grant's signed sha as expected_sha; verification happens
        # once at stream time. So a mismatching grant still resolves (the bytes
        # are rejected at the stream boundary, before any byte is sent).
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / 'a/b.txt'
            path.parent.mkdir(parents=True)
            path.write_text('actual', encoding='utf-8')
            expected = hashlib.sha256(b'expected').hexdigest()
            resolution = resolve_download_grant(
                grant={
                    'relative_path': 'a/b.txt',
                    'storage_backend': 'filesystem',
                    'expires_at': '2026-05-15T00:05:00Z',
                    'disposition': 'attachment',
                    'metadata': {'sha256': expected},
                },
                storage_roots={'filesystem': root},
                now=datetime(2026, 5, 15, 0, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(resolution.expected_sha, expected)

    def test_stream_rejects_hash_mismatch_before_sending(self):
        # The integrity gate is now the streaming layer (single fd hash) — a
        # dedicated DownloadIntegrityError (→ HTTP 409), raised before any byte
        # is streamed, not a generic ValueError (→ misleading 404).
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'b.txt'
            path.write_text('actual', encoding='utf-8')
            with self.assertRaises(DownloadIntegrityError):
                open_verified_stream(path, expected_sha=hashlib.sha256(b'expected').hexdigest())

    def test_rejects_unsafe_relative_path_before_root_resolution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, 'traversal'):
                resolve_download_grant(
                    grant={
                        'relative_path': '../secret.txt',
                        'storage_backend': 'filesystem',
                        'expires_at': '2026-05-15T00:05:00Z',
                        'disposition': 'attachment',
                        'metadata': {},
                    },
                    storage_roots={'filesystem': Path(tmpdir)},
                    now=datetime(2026, 5, 15, 0, 1, tzinfo=timezone.utc),
                )


if __name__ == '__main__':
    unittest.main()
