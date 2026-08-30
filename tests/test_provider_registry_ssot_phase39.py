import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / 'src'))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402


class TestProviderRegistrySSOT(unittest.TestCase):
    def test_checked_in_registry_loads_structured_provider_entries(self):
        from fcc_test_platform.provider_registry import load_provider_registry

        registry = load_provider_registry(
            resolve_repo_artifact(__file__, 'docs/api/headless_provider_registry.json'),
            project_root,
        )

        self.assertEqual(registry.registry_version, 1)
        self.assertEqual(
            [provider.provider_id for provider in registry.providers],
            ['fcc-unlicensed-conducted', 'fcc-mmwave-headless', 'fcc-licensed-headless'],
        )
        for artifact_path in registry.artifact_paths:
            self.assertTrue(Path(artifact_path).exists())

    def test_registry_rejects_duplicate_provider_id(self):
        from fcc_test_platform.provider_registry import (
            ProviderRegistryError,
            load_provider_registry,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact = root / 'contract.json'
            artifact.write_text('{}', encoding='utf-8')
            registry = root / 'registry.json'
            registry.write_text(json.dumps({
                'providers': [
                    {
                        'provider_id': 'same',
                        'product_line': 'unlicensed-conducted',
                        'contract_family': 'fcc-conducted-headless',
                        'contract_artifact': str(artifact),
                    },
                    {
                        'provider_id': 'same',
                        'product_line': 'mmwave',
                        'contract_family': 'fcc-conducted-headless',
                        'contract_artifact': str(artifact),
                    },
                ],
            }), encoding='utf-8')

            with self.assertRaisesRegex(ProviderRegistryError, 'duplicate.*provider_id'):
                load_provider_registry(registry, project_root)

    def test_registry_rejects_duplicate_product_line(self):
        from fcc_test_platform.provider_registry import (
            ProviderRegistryError,
            load_provider_registry,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact = root / 'contract.json'
            artifact.write_text('{}', encoding='utf-8')
            registry = root / 'registry.json'
            registry.write_text(json.dumps({
                'providers': [
                    {
                        'provider_id': 'first',
                        'product_line': 'same',
                        'contract_family': 'fcc-conducted-headless',
                        'contract_artifact': str(artifact),
                    },
                    {
                        'provider_id': 'second',
                        'product_line': 'same',
                        'contract_family': 'fcc-conducted-headless',
                        'contract_artifact': str(artifact),
                    },
                ],
            }), encoding='utf-8')

            with self.assertRaisesRegex(ProviderRegistryError, 'duplicate.*product_line'):
                load_provider_registry(registry, project_root)

    def test_registry_rejects_contract_detail_duplication(self):
        from fcc_test_platform.provider_registry import (
            ProviderRegistryError,
            load_provider_registry,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact = root / 'contract.json'
            artifact.write_text('{}', encoding='utf-8')
            registry = root / 'registry.json'
            registry.write_text(json.dumps({
                'providers': [{
                    'provider_id': 'fcc-unlicensed-conducted',
                    'product_line': 'unlicensed-conducted',
                    'contract_family': 'fcc-conducted-headless',
                    'contract_artifact': str(artifact),
                    'routes': {},
                }],
            }), encoding='utf-8')

            with self.assertRaisesRegex(ProviderRegistryError, 'must not duplicate'):
                load_provider_registry(registry, project_root)

    def test_registry_rejects_missing_artifact_path(self):
        from fcc_test_platform.provider_registry import (
            ProviderRegistryError,
            load_provider_registry,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = Path(tmpdir) / 'registry.json'
            registry.write_text(json.dumps({
                'providers': [{
                    'provider_id': 'fcc-unlicensed-conducted',
                    'product_line': 'unlicensed-conducted',
                    'contract_family': 'fcc-conducted-headless',
                    'contract_artifact': 'missing.json',
                }],
            }), encoding='utf-8')

            with self.assertRaisesRegex(ProviderRegistryError, 'does not exist'):
                load_provider_registry(registry, project_root)

    def test_registry_script_reports_model_validation_errors(self):
        from scripts.check_headless_provider_registry import main

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = Path(tmpdir) / 'registry.json'
            registry.write_text(json.dumps({
                'routes': {},
                'providers': [],
            }), encoding='utf-8')
            with mock.patch('builtins.print') as mocked_print:
                exit_code = main([str(registry)])

        payload = json.loads(mocked_print.call_args.args[0])
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload['error']['code'], 'registry_usage_error')
        self.assertIn('must not duplicate', payload['error']['message'])

    def test_registry_identity_validation_rejects_wrong_provider_artifact(self):
        from fcc_test_contracts.headless.api_contracts import ApiContractSnapshot
        from fcc_test_platform.provider_registry import (
            ProviderRegistryError,
            load_provider_registry,
            validate_registry_contract_identities,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact = root / 'contract.json'
            artifact.write_text(json.dumps(ApiContractSnapshot(provider={
                'provider_id': 'fcc-unlicensed-conducted',
                'product_line': 'unlicensed-conducted',
                'contract_family': 'fcc-conducted-headless',
            }).to_dict()), encoding='utf-8')
            registry_path = root / 'registry.json'
            registry_path.write_text(json.dumps({
                'providers': [{
                    'provider_id': 'fcc-mmwave-headless',
                    'product_line': 'mmwave',
                    'contract_family': 'fcc-conducted-headless',
                    'contract_artifact': str(artifact),
                }],
            }), encoding='utf-8')

            registry = load_provider_registry(registry_path, project_root)

            with self.assertRaisesRegex(ProviderRegistryError, 'provider_id mismatch'):
                validate_registry_contract_identities(registry)

    def test_registry_script_rejects_wrong_provider_artifact(self):
        from fcc_test_contracts.headless.api_contracts import ApiContractSnapshot
        from scripts.check_headless_provider_registry import main

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact = root / 'contract.json'
            artifact.write_text(json.dumps(ApiContractSnapshot(provider={
                'provider_id': 'fcc-unlicensed-conducted',
                'product_line': 'unlicensed-conducted',
                'contract_family': 'fcc-conducted-headless',
            }).to_dict()), encoding='utf-8')
            registry = root / 'registry.json'
            registry.write_text(json.dumps({
                'providers': [{
                    'provider_id': 'fcc-mmwave-headless',
                    'product_line': 'mmwave',
                    'contract_family': 'fcc-conducted-headless',
                    'contract_artifact': str(artifact),
                }],
            }), encoding='utf-8')

            with mock.patch('builtins.print') as mocked_print:
                exit_code = main([str(registry)])

        payload = json.loads(mocked_print.call_args.args[0])
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload['error']['code'], 'registry_usage_error')
        self.assertIn('provider_id mismatch', payload['error']['message'])


if __name__ == '__main__':
    unittest.main()
