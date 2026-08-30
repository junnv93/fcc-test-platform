import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))


class TestApiContractPublicSurface(unittest.TestCase):
    def test_api_contracts_public_surface_excludes_private_helpers(self):
        import fcc_test_contracts.headless.api_contracts as contracts

        exported = set(contracts.__all__)

        self.assertIn('ApiContractSnapshot', exported)
        self.assertIn('HealthCheckResponse', exported)
        self.assertIn('HEADLESS_API_PERMISSIONS', exported)
        self.assertIn('SubmitMeasurementJobRequest', exported)
        self.assertIn('HEADLESS_API_ROUTES', exported)
        self.assertNotIn('_optional_dict', exported)
        self.assertNotIn('_copy_contract_dict', exported)

    def test_api_contract_checker_public_surface_excludes_private_checks(self):
        import fcc_test_contracts.headless.api_contract_checker as checker

        exported = set(checker.__all__)

        self.assertEqual(
            exported,
            {
                'ApiContractCompatibilityResult',
                'ApiContractIssue',
                'check_api_contract_compatibility',
            },
        )

    def test_provider_registry_public_surface_excludes_private_helpers(self):
        import fcc_test_platform.provider_registry as registry

        exported = set(registry.__all__)

        self.assertEqual(
            exported,
            {
                'ProviderRegistry',
                'ProviderRegistryEntry',
                'ProviderReferenceResolverRegistry',
                'ProviderRegistryError',
                'load_provider_registry',
                'validate_registry_contract_identities',
            },
        )
        self.assertNotIn('_parse_registry', exported)
        self.assertNotIn('_resolve_artifact_path', exported)

    def test_public_exports_are_dependency_free_runtime_objects(self):
        import fcc_test_contracts.headless.api_contracts as contracts
        import fcc_test_contracts.headless.api_contract_checker as checker
        import fcc_test_platform.provider_registry as registry

        for name in contracts.__all__:
            self.assertTrue(hasattr(contracts, name), name)
        for name in checker.__all__:
            self.assertTrue(hasattr(checker, name), name)
        for name in registry.__all__:
            self.assertTrue(hasattr(registry, name), name)


if __name__ == '__main__':
    unittest.main()
