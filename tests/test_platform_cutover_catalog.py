import json
import unittest
from pathlib import Path
from types import MappingProxyType

from fcc_test_platform.application.platform_cutover_catalog import (
    EVIDENCE_CATALOG,
    catalog_cli_arguments,
    catalog_completion_groups,
    catalog_entries,
    catalog_filenames,
    catalog_keys,
    catalog_validator_bindings,
)
from fcc_test_platform.cutover_readiness import (
    CUTOVER_REQUIRED_EVIDENCE,
    validate_collector_manifest,
)
from fcc_test_platform.cutover_bundle_cli import EVIDENCE_ARGUMENTS, EVIDENCE_FILENAMES
from fcc_test_platform.cutover_completion_audit_cli import REQUIREMENTS
from fcc_test_platform.cutover_live_workflow_cli import build_workflow_template
from fcc_test_platform.cutover_workflow_hints import suggested_command


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestPlatformCutoverCatalog(unittest.TestCase):
    def test_catalog_is_immutable_and_total(self):
        entries = catalog_entries()
        keys = catalog_keys()

        self.assertIs(entries, EVIDENCE_CATALOG)
        self.assertEqual(len(entries), 14)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len({entry.canonical_filename for entry in entries}), len(entries))
        self.assertEqual(len({entry.cli_argument for entry in entries}), len(entries))
        self.assertIsInstance(catalog_filenames(), MappingProxyType)
        self.assertIsInstance(catalog_cli_arguments(), MappingProxyType)
        self.assertIsInstance(catalog_validator_bindings(), MappingProxyType)
        with self.assertRaises(TypeError):
            catalog_filenames()['new'] = 'new.json'

        for entry in entries:
            self.assertTrue(callable(entry.validator), entry.key)
            self.assertEqual(catalog_validator_bindings()[entry.key], entry.validator)
            self.assertIn(entry.key, catalog_completion_groups()[entry.completion_group])

    def test_consumers_are_derived_from_the_catalog(self):
        keys = list(catalog_keys())
        self.assertEqual(CUTOVER_REQUIRED_EVIDENCE, tuple(keys))
        self.assertEqual(dict(EVIDENCE_FILENAMES), dict(catalog_filenames()))
        self.assertEqual(
            EVIDENCE_ARGUMENTS,
            tuple((entry.key, entry.cli_argument) for entry in catalog_entries()),
        )
        workflow = build_workflow_template(
            evidence_root='artifacts/cutover/evidence',
            provider_id='fcc-unlicensed-conducted',
            cutover_candidate_id='catalog-test',
            evaluated_at='2026-08-23T00:00:00Z',
            collected_at='2026-08-23T00:00:00Z',
            central_db_schema='docs/platform/central_db_schema.v1.json',
            extraction_manifest='docs/api/headless_contract_extraction_manifest.v1.json',
        )
        self.assertEqual([step['evidence_key'] for step in workflow['steps']], keys)
        self.assertEqual(
            {step['output'] for step in workflow['steps']},
            set(catalog_filenames().values()),
        )
        self.assertEqual(
            {requirement_key for requirement in REQUIREMENTS for requirement_key in requirement['evidence_keys']},
            set(keys),
        )

    def test_every_catalog_entry_has_a_command_builder_and_validator_binding(self):
        for entry in catalog_entries():
            with self.subTest(evidence_key=entry.key):
                command = suggested_command(entry.key, f'/tmp/{entry.canonical_filename}')
                self.assertTrue(command)
                self.assertEqual(command[0], 'python')
                self.assertIn('/tmp/' + entry.canonical_filename, command)
                issues = validate_collector_manifest(
                    entry.key,
                    {'schema_version': 1},
                    central_db_schema=None,
                    extraction_manifest=None,
                )
                self.assertIsInstance(issues, list)

    def test_context_requiring_entries_fail_closed_without_context(self):
        for entry in catalog_entries():
            if not entry.required_contexts:
                continue
            with self.subTest(evidence_key=entry.key):
                issues = validate_collector_manifest(
                    entry.key,
                    {'schema_version': 1},
                    central_db_schema=None,
                    extraction_manifest=None,
                )
                codes = {issue.code for issue in issues}
                for context in entry.required_contexts:
                    self.assertIn(f'missing_{context}', codes)

    def test_checked_in_catalog_export_matches_runtime_inventory(self):
        schema = json.loads(
            (PROJECT_ROOT / 'docs' / 'platform' / 'cutover_readiness_evidence.schema.v1.json')
            .read_text(encoding='utf-8')
        )
        self.assertEqual(schema['required_evidence_keys'], list(catalog_keys()))
        self.assertEqual(schema['manifest_required_evidence_keys'], list(catalog_keys()))
        self.assertEqual(
            schema['canonical_filenames'],
            dict(catalog_filenames()),
        )
        self.assertEqual(
            schema['cli_arguments'],
            dict(catalog_cli_arguments()),
        )
        self.assertEqual(schema['receipt_policy']['resume_metadata_is_not_evidence'], True)
        self.assertEqual(schema['status'], 'planning_contract')
        self.assertIn('database_write', schema['forbidden_operations'])


if __name__ == '__main__':
    unittest.main()
