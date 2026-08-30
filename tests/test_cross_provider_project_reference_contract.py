from __future__ import annotations

import unittest

from domain.services.unlicensed.project_result_reference import (
    ConductedDutyReferenceAdapter,
)
from fcc_test_platform.provider_registry import (
    ProviderReferenceResolverRegistry,
    ProviderRegistryError,
)
from tests.fakes.fake_project_result_reference_provider import (
    FakeRadiatedReferenceAdapter,
)


class CrossProviderProjectReferenceContractTests(unittest.TestCase):
    def test_provider_axes_are_not_interchangeable(self) -> None:
        duty = ConductedDutyReferenceAdapter()
        radiated = FakeRadiatedReferenceAdapter()
        self.assertNotEqual(duty.provider_id, radiated.provider_id)
        self.assertFalse(duty.accepts(radiated.reference_type, radiated.schema_version))
        self.assertTrue(radiated.accepts(radiated.reference_type, radiated.schema_version))

    def test_fake_provider_can_export_without_platform_imports(self) -> None:
        result = FakeRadiatedReferenceAdapter().export({
            'attempt_id': 'radiated-attempt',
            'condition_hash': 'same-condition-token',
            'status': 'completed',
            'result_json': {'field_strength': '42'},
        })
        self.assertEqual(result['provider_id'], 'fake-radiated')
        self.assertEqual(result['payload']['field_strength'], '42')
        self.assertEqual(len(result['content_sha256']), 64)

    def test_reference_resolver_registry_is_identity_checked_and_mapping_shaped(self) -> None:
        adapter = ConductedDutyReferenceAdapter()
        registry = ProviderReferenceResolverRegistry({adapter.provider_id: adapter})
        self.assertEqual(registry.provider_ids(), (adapter.provider_id,))
        self.assertIs(registry[adapter.provider_id], adapter)

        with self.assertRaises(ProviderRegistryError):
            ProviderReferenceResolverRegistry({'wrong-id': adapter})


if __name__ == '__main__':
    unittest.main()
