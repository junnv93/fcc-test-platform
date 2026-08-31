"""The registry document is the platform's CONTENT axis.

⚠️ **The checker and the artifact-resolution assertions left this file on
2026-08-31.** ``scripts/check_headless_provider_registry.py`` moved to
``fcc-test-contracts`` -- it needs the contract artifacts and the batch checker,
and after the split only the registry document was platform-owned, so in this box
it died at its first import while both boxes reported green. The artifacts it
resolves are published by that lane and are not in this tree, so asking *does
this artifact exist* here has no honest answer.

What stays here is the question this lane can answer: **who is registered, and is
the document well formed.** The complementary question -- *does what the document
names actually check out against the contract SSOT* -- is answered by running the
contracts-owned checker with this document's path:

    python3 scripts/check_headless_provider_registry.py \
        <platform>/config/headless_provider_registry.json
"""
import json
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / 'src'))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402


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

    def test_registry_does_not_duplicate_contract_routes_or_schemas(self):
        registry = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
        text = json.dumps(registry)

        self.assertNotIn('/headless/jobs', text)
        self.assertNotIn('schemas', registry)
        self.assertNotIn('routes', registry)
        self.assertNotIn('operations', registry)

    def test_named_artifacts_are_addressed_in_the_publishing_lanes_shape(self):
        """⚠️ This is the seal for the 2026-08-31 repair, and it is not cosmetic.

        ``contract_artifact`` is resolved by the contracts-owned checker against
        **the contracts tree**, where the artifacts are published under
        ``artifacts/``. This document said ``docs/api/`` -- the monorepo's shape,
        carried over by the packager -- and the checker's fallback then looked
        beside the registry, reported ``config/docs/api/...`` and refused.

        The wrong spelling is not detectable by any check inside this box (the
        artifacts are not here either way), so the spelling itself is what this
        lane can hold. ⚠️ Note the fallback makes the failure mode *silent* in the
        other direction: a copy of an artifact placed next to this document would
        resolve, and a copy diverges without anything turning red.
        """
        registry = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))

        self.assertTrue(registry['providers'], 'registry is empty')
        for provider in registry['providers']:
            artifact = provider['contract_artifact']
            self.assertTrue(
                artifact.startswith('artifacts/'),
                f"{provider['provider_id']}.contract_artifact must be addressed in "
                f'the publishing lane (artifacts/...), got {artifact!r}',
            )
            self.assertFalse(
                Path(artifact).is_absolute(),
                f"{provider['provider_id']}.contract_artifact must stay relative",
            )


if __name__ == '__main__':
    unittest.main()
