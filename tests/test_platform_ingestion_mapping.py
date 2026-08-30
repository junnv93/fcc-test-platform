import ast
import json
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402


class TestPlatformIngestionMapping(unittest.TestCase):
    def test_measurement_result_envelope_maps_to_schema_record(self):
        from fcc_test_platform.provider_ingestion import map_measurement_result_record

        record = map_measurement_result_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            envelope={
                'result_id': 'provider-result-1',
                'test_name': 'OBW',
                'technology': 'DTS',
                'condition': {'channel': '36', 'bandwidth': '20MHz'},
                'result': {'result1': '12.5', 'unit': 'MHz'},
                'verdict': 'Pass',
                'measured_at': '2026-05-14T00:00:00Z',
            },
        )

        self.assertEqual(record['provider_result_id'], 'provider-result-1')
        self.assertEqual(record['test_name'], 'OBW')
        self.assertEqual(json.loads(record['condition_json'])['channel'], '36')
        self.assertEqual(json.loads(record['result_json'])['unit'], 'MHz')
        self.assertNotIn('obw_value', record)

    def test_artifact_metadata_maps_relative_path_without_file_bytes(self):
        from fcc_test_platform.provider_ingestion import map_artifact_metadata_record

        record = map_artifact_metadata_record(
            provider_id='provider-uuid',
            session_id='session-uuid',
            artifact={
                'result_id': 'provider-result-1',
                'artifact_type': 'plot_png',
                'relative_path': 'session/result/plot.png',
                'original_filename': 'plot.png',
                'sha256': 'abc',
                'byte_size': 123,
                'storage_backend': 'filesystem',
            },
            result_record_ids_by_provider_result_id={'provider-result-1': 'result-uuid'},
        )

        self.assertEqual(record['measurement_result_id'], 'result-uuid')
        self.assertEqual(record['relative_path'], 'session/result/plot.png')
        self.assertEqual(record['byte_size'], 123)
        self.assertNotIn('path_or_uri', record)
        self.assertNotIn('blob', ''.join(record))

    def test_report_output_maps_to_metadata_record(self):
        from fcc_test_platform.provider_ingestion import map_report_output_record

        record = map_report_output_record(
            report_run_id='report-run-uuid',
            output={
                'file_name': 'dts.docx',
                'relative_path': 'reports/dts.docx',
                'byte_size': 456,
            },
        )

        self.assertEqual(record['report_run_id'], 'report-run-uuid')
        self.assertEqual(record['storage_backend'], 'filesystem')
        self.assertEqual(record['relative_path'], 'reports/dts.docx')

    def test_batch_mapping_groups_records(self):
        from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch

        batch = build_platform_ingestion_batch(
            provider_id='provider-uuid',
            session_id='session-uuid',
            result_envelopes=[{
                'result_id': 'r1',
                'test_name': 'Power',
                'technology': 'UNII',
                'condition': {},
                'result': {},
            }],
            artifact_metadata=[{
                'artifact_type': 'plot_png',
                'relative_path': 'plot.png',
            }],
            report_outputs=[{
                'file_name': 'unii.pdf',
                'relative_path': 'reports/unii.pdf',
            }],
            report_run_id='report-run-uuid',
            report_run_evidence={'status': 'completed'},
        ).to_dict()

        self.assertEqual(len(batch['measurement_results']), 1)
        self.assertEqual(len(batch['artifacts']), 1)
        self.assertEqual(len(batch['report_outputs']), 1)

    def test_non_empty_report_outputs_emit_one_parent_from_report_evidence(self):
        from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch

        batch = build_platform_ingestion_batch(
            provider_id='provider-uuid',
            session_id='session-uuid',
            result_envelopes=[],
            report_outputs=[
                {'file_name': 'dts.docx', 'relative_path': 'reports/dts.docx'},
                {'file_name': 'dts.pdf', 'relative_path': 'reports/dts.pdf'},
            ],
            report_run_id='report-run-uuid',
            report_run_evidence={
                'status': 'completed',
                'provider_report_request_id': 'provider-request-7',
                'report_types': ['docx', 'pdf'],
                'warnings': [{'code': 'none'}],
            },
        ).to_dict()

        self.assertEqual(len(batch['report_runs']), 1)
        self.assertEqual(
            batch['report_runs'][0],
            {
                'id': 'report-run-uuid',
                'provider_id': 'provider-uuid',
                'session_id': 'session-uuid',
                'provider_report_request_id': 'provider-request-7',
                'status': 'completed',
                'report_types_json': '["docx","pdf"]',
                'warnings_json': '[{"code":"none"}]',
            },
        )
        self.assertNotIn('created_at', batch['report_runs'][0])
        self.assertEqual(len(batch['report_outputs']), 2)

    def test_empty_report_outputs_emit_neither_parent_nor_children(self):
        from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch

        batch = build_platform_ingestion_batch(
            provider_id='provider-uuid',
            session_id='session-uuid',
            result_envelopes=[],
            report_outputs=(),
        )

        self.assertEqual(batch.report_runs, [])
        self.assertEqual(batch.report_outputs, [])

    def test_report_output_one_shot_iterable_is_consumed_once(self):
        from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch

        class OneShotOutputs:
            def __init__(self):
                self.iterations = 0

            def __iter__(self):
                self.iterations += 1
                if self.iterations > 1:
                    raise AssertionError('report output iterable was consumed twice')
                yield {'file_name': 'dts.docx', 'relative_path': 'reports/dts.docx'}

        outputs = OneShotOutputs()
        batch = build_platform_ingestion_batch(
            provider_id='provider-uuid',
            session_id='session-uuid',
            result_envelopes=[],
            report_outputs=outputs,
            report_run_id='report-run-uuid',
            report_run_evidence={'status': 'completed'},
        )

        self.assertEqual(outputs.iterations, 1)
        self.assertEqual(len(batch.report_runs), 1)
        self.assertEqual(len(batch.report_outputs), 1)

    def test_non_empty_report_outputs_require_terminal_status_evidence(self):
        from fcc_test_platform.provider_ingestion import build_platform_ingestion_batch

        with self.assertRaisesRegex(ValueError, 'status'):
            build_platform_ingestion_batch(
                provider_id='provider-uuid',
                session_id='session-uuid',
                result_envelopes=[],
                report_outputs=[{
                    'file_name': 'dts.docx',
                    'relative_path': 'reports/dts.docx',
                }],
                report_run_id='report-run-uuid',
                report_run_evidence={},
            )

    def test_missing_required_fields_are_rejected(self):
        from fcc_test_platform.provider_ingestion import map_measurement_result_record

        with self.assertRaisesRegex(ValueError, 'result_id is required'):
            map_measurement_result_record(
                provider_id='provider-uuid',
                session_id='session-uuid',
                envelope={'test_name': 'OBW', 'technology': 'DTS'},
            )

    def test_mapper_has_no_provider_private_or_infrastructure_imports(self):
        path = resolve_repo_artifact(__file__, 'src/application/headless/platform_ingestion.py')
        tree = ast.parse(path.read_text(encoding='utf-8'))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        forbidden_prefixes = (
            'infrastructure',
            'reporting',
            'measurements',
            'fastapi',
            'PySide6',
            'sqlalchemy',
        )
        for imported in imports:
            self.assertFalse(
                imported.startswith(forbidden_prefixes),
                f'forbidden import: {imported}',
            )


if __name__ == '__main__':
    unittest.main()
