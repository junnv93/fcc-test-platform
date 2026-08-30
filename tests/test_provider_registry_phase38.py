import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / 'src'))

from fcc_test_contracts.common.tree_artifacts import (
    resolve_dependency_artifact,
    resolve_repo_artifact,
)  # noqa: E402


REGISTRY_PATH = resolve_repo_artifact(__file__, 'docs/api/headless_provider_registry.json')


class TestProviderRegistry(unittest.TestCase):
    def test_registry_lists_unlicensed_mmwave_and_licensed_providers(self):
        registry = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))

        product_lines = {provider['product_line'] for provider in registry['providers']}

        self.assertEqual(
            product_lines,
            {'unlicensed-conducted', 'mmwave', 'licensed-conducted'},
        )
        for provider in registry['providers']:
            self.assertIn('provider_id', provider)
            self.assertIn('contract_artifact', provider)
            self.assertIn('contract_family', provider)
            artifact = project_root / provider['contract_artifact']
            self.assertTrue(
                artifact.exists(),
                f"registry artifact does not exist: {artifact}",
            )

    def test_registry_does_not_duplicate_contract_routes_or_schemas(self):
        registry = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
        text = json.dumps(registry)

        self.assertNotIn('/headless/jobs', text)
        self.assertNotIn('schemas', registry)
        self.assertNotIn('routes', registry)
        self.assertNotIn('operations', registry)

    def test_registry_script_expands_artifacts_into_batch_checker(self):
        from scripts.check_headless_provider_registry import main

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = root / 'registry.json'
            artifact = resolve_dependency_artifact('docs/api/headless_api_contract.v1.json')
            registry.write_text(json.dumps({
                'registry_version': 1,
                'providers': [{
                    'provider_id': 'fcc-unlicensed-conducted',
                    'product_line': 'unlicensed-conducted',
                    'contract_family': 'fcc-conducted-headless',
                    'contract_artifact': str(artifact),
                }],
            }), encoding='utf-8')

            with mock.patch('scripts.check_headless_api_contracts_batch.main') as batch:
                batch.return_value = 0
                exit_code = main([str(registry)])

        self.assertEqual(exit_code, 0)
        batch.assert_called_once_with([str(artifact)])

    def test_registry_script_returns_usage_error_for_empty_registry(self):
        from scripts.check_headless_provider_registry import main

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = Path(tmpdir) / 'registry.json'
            registry.write_text(json.dumps({'providers': []}), encoding='utf-8')
            with mock.patch('builtins.print') as mocked_print:
                exit_code = main([str(registry)])

        payload = json.loads(mocked_print.call_args.args[0])
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload['error']['code'], 'registry_usage_error')

    def test_checked_in_registry_runs_batch_compatibility_check(self):
        from scripts.check_headless_provider_registry import main

        with mock.patch('builtins.print') as mocked_print:
            exit_code = main([str(REGISTRY_PATH)])

        self.assertEqual(exit_code, 0)
        payload = json.loads(mocked_print.call_args.args[0])
        self.assertTrue(payload['compatible'])
        self.assertEqual(len(payload['providers']), 3)

    def test_registered_provider_artifacts_match_contract_ssot_with_metadata(self):
        from fcc_test_contracts.headless.api_contracts import ApiContractSnapshot
        from fcc_test_platform.provider_registry import load_provider_registry

        registry = load_provider_registry(REGISTRY_PATH, project_root)

        for provider in registry.providers:
            actual = json.loads(
                provider.resolved_contract_artifact.read_text(encoding='utf-8')
            )
            expected = ApiContractSnapshot(provider={
                'provider_id': provider.provider_id,
                'product_line': provider.product_line,
                'contract_family': provider.contract_family,
            }).to_dict()

            self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
