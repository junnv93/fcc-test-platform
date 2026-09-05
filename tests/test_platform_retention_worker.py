from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.retention_worker_cli import execute_retention_plan


class TestPlatformRetentionWorker(unittest.TestCase):
    def test_dry_run_does_not_move_or_delete_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'storage'
            archive = root / 'archive'
            file_path = source / 'projects/p/sessions/s/results/r/old.png'
            file_path.parent.mkdir(parents=True)
            file_path.write_bytes(b'old')

            result = execute_retention_plan(
                plan=_plan(dry_run=True),
                storage_root=source,
                archive_root=archive,
                mode='archive',
                execute=False,
            )

            self.assertTrue(file_path.exists())

        self.assertFalse(result['executed'])
        self.assertFalse(result['actions'][0]['executed'])
        self.assertEqual(result['actions'][0]['archive_path'], 'projects/p/sessions/s/results/r/old.png')

    def test_archive_execute_moves_file_under_archive_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'storage'
            archive = root / 'archive'
            file_path = source / 'projects/p/sessions/s/results/r/old.png'
            file_path.parent.mkdir(parents=True)
            file_path.write_bytes(b'old')

            result = execute_retention_plan(
                plan=_plan(dry_run=False),
                storage_root=source,
                archive_root=archive,
                mode='archive',
                execute=True,
            )

            archived = archive / 'projects/p/sessions/s/results/r/old.png'
            self.assertFalse(file_path.exists())
            self.assertEqual(archived.read_bytes(), b'old')

        self.assertTrue(result['executed'])
        self.assertTrue(result['actions'][0]['executed'])
        self.assertFalse(result['actions'][0]['source_exists_after'])

    def test_delete_execute_unlinks_file_only_when_plan_not_dry_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'storage'
            file_path = source / 'projects/p/sessions/s/results/r/old.png'
            file_path.parent.mkdir(parents=True)
            file_path.write_bytes(b'old')

            result = execute_retention_plan(
                plan=_plan(dry_run=False),
                storage_root=source,
                archive_root=None,
                mode='delete',
                execute=True,
            )

            self.assertFalse(file_path.exists())

        self.assertEqual(result['actions'][0]['action'], 'delete')
        self.assertTrue(result['actions'][0]['executed'])

    def test_rejects_executing_dry_run_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, 'dry_run'):
                execute_retention_plan(
                    plan=_plan(dry_run=True),
                    storage_root=Path(tmpdir),
                    archive_root=Path(tmpdir) / 'archive',
                    mode='archive',
                    execute=True,
                )

    def test_rejects_unsafe_candidate_path(self):
        plan = _plan(dry_run=False)
        plan['candidates'][0]['relative_path'] = '../outside.png'
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, 'traversal'):
                execute_retention_plan(
                    plan=plan,
                    storage_root=Path(tmpdir),
                    archive_root=Path(tmpdir) / 'archive',
                    mode='archive',
                    execute=False,
                )


def _plan(*, dry_run: bool) -> dict:
    return {
        'dry_run': dry_run,
        'approved_by': 'qa.manager@example.com',
        'approved_at': datetime(2026, 5, 15, tzinfo=timezone.utc).isoformat(),
        'reason': 'monthly retention review',
        'candidates': [
            {
                'record_type': 'artifact',
                'relative_path': 'projects/p/sessions/s/results/r/old.png',
                'storage_backend': 'filesystem',
                'reason': 'artifact age 90d >= retention 30d',
                'age_days': 90,
            }
        ],
    }


if __name__ == '__main__':
    unittest.main()
