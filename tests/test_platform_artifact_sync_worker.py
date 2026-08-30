import json
import tempfile
import unittest
from pathlib import Path

from fcc_test_platform.artifact_sync_evidence import artifact_sync_evidence_errors
from scripts.platform_artifact_sync_worker import (
    SyncRecord,
    _parse_record,
    build_sync_manifest,
)


class TestPlatformArtifactSyncWorker(unittest.TestCase):
    def test_copy_missing_builds_valid_sync_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'source'
            destination = root / 'destination'
            artifact = source / 'projects/p/sessions/s/results/r/plot_png/plot.png'
            report = source / 'projects/p/sessions/s/reports/r/report.pdf'
            artifact.parent.mkdir(parents=True)
            report.parent.mkdir(parents=True)
            artifact.write_bytes(b'plot')
            report.write_bytes(b'report')

            manifest = build_sync_manifest(
                source_root=source,
                destination_root=destination,
                source_root_id='lab-root',
                destination_root_id='platform-root',
                provider_id='fcc-unlicensed-conducted',
                session_id='session-1',
                sync_job_id='sync-1',
                records=[
                    SyncRecord('artifact', 'projects/p/sessions/s/results/r/plot_png/plot.png'),
                    SyncRecord('report_output', 'projects/p/sessions/s/reports/r/report.pdf'),
                ],
                copy_missing=True,
            )

        self.assertEqual(artifact_sync_evidence_errors(manifest), [])
        self.assertEqual(manifest['artifact_count'], 1)
        self.assertEqual(manifest['report_output_count'], 1)
        self.assertTrue(all(record['sync_status'] == 'synced' for record in manifest['files']))

    def test_without_copy_missing_keeps_manifest_non_claiming(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'source'
            destination = root / 'destination'
            artifact = source / 'projects/p/sessions/s/results/r/plot_png/plot.png'
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b'plot')

            manifest = build_sync_manifest(
                source_root=source,
                destination_root=destination,
                source_root_id='lab-root',
                destination_root_id='platform-root',
                provider_id='fcc-unlicensed-conducted',
                session_id='session-1',
                sync_job_id='sync-1',
                records=[SyncRecord('artifact', 'projects/p/sessions/s/results/r/plot_png/plot.png')],
                copy_missing=False,
            )

        codes = {issue.code for issue in artifact_sync_evidence_errors(manifest)}
        self.assertIn('file_not_synced', codes)
        self.assertIn('destination_missing', codes)

    def test_record_parser_rejects_unsafe_paths(self):
        with self.assertRaises(ValueError):
            _parse_record('artifact=../outside.bin')

    def test_records_json_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'records.json'
            path.write_text(json.dumps([{
                'record_type': 'artifact',
                'relative_path': 'projects/p/sessions/s/results/r/plot_png/plot.png',
            }]), encoding='utf-8')

            from scripts.platform_artifact_sync_worker import _load_records

            records = _load_records([], str(path))

        self.assertEqual(records, [SyncRecord('artifact', 'projects/p/sessions/s/results/r/plot_png/plot.png')])


if __name__ == '__main__':
    unittest.main()
