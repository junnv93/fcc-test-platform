import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.backup_restore_drill import backup_restore_drill_errors
from fcc_test_platform.backup_restore_runner_cli import run_backup_restore_drill


class TestPlatformBackupRestoreRunner(unittest.TestCase):
    def test_runner_creates_archive_restores_files_and_emits_valid_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'source'
            backup = root / 'backup'
            restore = root / 'restore'
            db_dump = root / 'db.dump'
            db_dump.write_bytes(b'pg-dump')
            artifact_path = 'projects/p/sessions/s/results/r/plot_png/plot.png'
            report_path = 'projects/p/sessions/s/reports/r/report.pdf'
            (source / artifact_path).parent.mkdir(parents=True)
            (source / report_path).parent.mkdir(parents=True)
            (source / artifact_path).write_bytes(b'plot')
            (source / report_path).write_bytes(b'report')

            manifest = run_backup_restore_drill(
                restore_point_id='rp-1',
                database_name='fcc_platform',
                db_backup_file=db_dump,
                source_root=source,
                backup_root=backup,
                restore_root=restore,
                artifact_paths=[artifact_path],
                report_output_paths=[report_path],
            )

            self.assertTrue((backup / 'backups/rp-1/artifacts.zip').is_file())
            self.assertEqual((restore / artifact_path).read_bytes(), b'plot')
            self.assertEqual((restore / report_path).read_bytes(), b'report')

        self.assertEqual(backup_restore_drill_errors(manifest), [])

    def test_runner_requires_artifact_and_report_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_dump = root / 'db.dump'
            db_dump.write_bytes(b'pg-dump')

            with self.assertRaisesRegex(ValueError, 'artifact'):
                run_backup_restore_drill(
                    restore_point_id='rp-1',
                    database_name='fcc_platform',
                    db_backup_file=db_dump,
                    source_root=root / 'source',
                    backup_root=root / 'backup',
                    restore_root=root / 'restore',
                    artifact_paths=[],
                    report_output_paths=['reports/r/report.pdf'],
                )

    def test_runner_rejects_missing_source_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_dump = root / 'db.dump'
            db_dump.write_bytes(b'pg-dump')

            with self.assertRaises(FileNotFoundError):
                run_backup_restore_drill(
                    restore_point_id='rp-1',
                    database_name='fcc_platform',
                    db_backup_file=db_dump,
                    source_root=root / 'source',
                    backup_root=root / 'backup',
                    restore_root=root / 'restore',
                    artifact_paths=['missing.png'],
                    report_output_paths=['missing.pdf'],
                )


if __name__ == '__main__':
    unittest.main()
