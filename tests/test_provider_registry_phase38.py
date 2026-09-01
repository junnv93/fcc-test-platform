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

    def test_named_artifacts_are_not_addressed_the_monorepo_way(self):
        """⚠️ 이 검사가 무엇을 **묻지 않는지**가 요점이다.

        초판은 `contract_artifact` 가 `artifacts/` 로 시작하는지 물었다. 그것이
        **하루 만에 틀렸다** — 계약 레인이 아티팩트를 상자 루트에서 importable 패키지
        안(`fcc_test_contracts/artifacts/`)으로 옮겼기 때문이다(휠은 패키지 안의 것만
        나른다). 그쪽 봉인은 그 경로를 **철자로 적지 않고 포장기 기록에서 파생**하며,
        자기 주석에 *"여섯 리터럴이 조용히 낡아 「경로가 낡았다」가 아니라 「아티팩트가
        없다」로 실패했다"* 고 적는다.

        레지스트리는 **데이터라 파생할 수 없다.** 그러므로 이 상자가 물을 수 있는 것은
        *발행 레인의 현재 내부 배치* 가 아니라 **이 상자가 저지른 실제 회귀** 하나다 —
        모노레포의 `docs/api/` 철자가 배송을 타고 넘어오는 것. 그 철자는
        해소기의 폴백(`registry_path.parent`) 때문에 `config/docs/api/…` 를 답하며
        거절됐고, 그것이 2026-08-31 의 실측 결함이다.

        ⚠️ 양의 접두사를 다시 박지 마라 — 그러면 발행 레인이 안을 정리할 때마다 이 상자가
        red 가 되고, 그 red 는 이 상자의 결함이 아니다.
        """
        registry = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))

        self.assertTrue(registry['providers'], 'registry is empty')
        for provider in registry['providers']:
            artifact = provider['contract_artifact']
            self.assertFalse(
                artifact.startswith('docs/'),
                f"{provider['provider_id']}.contract_artifact 가 모노레포 철자다 "
                f'({artifact!r}) — 발행 레인 기준으로 적어야 한다',
            )
            self.assertFalse(
                Path(artifact).is_absolute(),
                f"{provider['provider_id']}.contract_artifact must stay relative",
            )
            self.assertTrue(
                artifact.endswith('.json'),
                f"{provider['provider_id']}.contract_artifact must name a JSON artifact",
            )


if __name__ == '__main__':
    unittest.main()
