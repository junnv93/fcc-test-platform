"""Registry document validation, delegated to the contracts-owned format axis.

⚠️ **Two tests that invoked ``scripts/check_headless_provider_registry.py``
left this file on 2026-08-31 with the script itself.** The checker moved to
``fcc-test-contracts`` (it needs the artifacts and the batch checker, neither
of which is in this box) and its execution seals moved with it --
``tests/test_provider_registry_check_runs_here.py`` over there RUNS the entry
point rather than reading its source, which is the defect class the old
arrangement hid.

The validation cases below stay: they exercise ``load_provider_registry``,
which this module now re-exports from the contracts lane, so they are this
box's conformance evidence that the re-export really is the format axis and
not a stub.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / 'src'))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402


class TestProviderRegistrySSOT(unittest.TestCase):
    def test_checked_in_registry_declares_the_three_registered_providers(self):
        """⚠️ Reads the document; does NOT resolve the artifacts it names.

        ``load_provider_registry`` refuses an entry whose ``contract_artifact``
        does not exist, and after 2026-08-31 those artifacts are addressed in the
        publishing lane (``artifacts/...``), which is not this tree. Resolving
        them here would need a copy, and a copy diverges silently -- so the
        resolution question is asked where the artifacts are, by running the
        contracts-owned checker against this document's path.
        """
        registry_path = resolve_repo_artifact(
            __file__, 'docs/api/headless_provider_registry.json',
        )
        document = json.loads(registry_path.read_text(encoding='utf-8'))

        self.assertEqual(document['registry_version'], 1)
        self.assertEqual(
            [provider['provider_id'] for provider in document['providers']],
            ['fcc-unlicensed-conducted', 'fcc-mmwave-headless', 'fcc-licensed-headless'],
        )

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


if __name__ == '__main__':
    unittest.main()
