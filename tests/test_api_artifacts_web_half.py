"""`@fcc/api-artifacts` 의 **웹 쪽 절반** (2026-08-31).

⚠️ 모노레포 `tests/test_api_artifacts_package.py` 에서 왔다. 그 파일의 명제는
「아티팩트 패키지가 정본과 어긋나지 않는다」이고, 그 절반은 **`apps/web` 과
`.github/workflows/frontend.yml`** 을 읽는다 — 둘 다 이 레포에 있다.

⚠️ 미러/매니페스트 절반은 모노레포에 남았다. `docs/api/*.openapi.json` 이
정본이고 그 쓰기 주체가 거기 있기 때문이다 — 한쪽만 검사하면 나머지가
갈라져도 조용하다.
"""
from __future__ import annotations

from __future__ import annotations
import json
import unittest
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PKG_ROOT = PROJECT_ROOT / "packages" / "api-artifacts"
CODEGEN = PROJECT_ROOT / "apps" / "web" / "scripts" / "codegen.mjs"


class TestPackageJsonMetadata(unittest.TestCase):
    def setUp(self):
        self.pkg = json.loads((PKG_ROOT / "package.json").read_text(encoding="utf-8"))
    def test_engines_node_matches_monorepo_policy(self):
        # Monorepo Node runtime floor alignment (node-runtime-policy-closure P2
        # follow-up): every JS package in the monorepo must advertise the accepted
        # Node 22.13 LTS policy, identical to apps/web's `engines.node`, so no
        # package silently advertises a stale (e.g. >=20.11) floor.
        engines = self.pkg.get("engines", {})
        self.assertIn("node", engines, "engines.node missing")
        web_pkg = json.loads((PROJECT_ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(
            engines["node"],
            web_pkg["engines"]["node"],
            "api-artifacts engines.node must match apps/web policy (Node 22.13 LTS band)",
        )

class TestCodegenConsumesPackage(unittest.TestCase):
    def setUp(self):
        self.src = CODEGEN.read_text(encoding="utf-8")
    def test_imports_package(self):
        # ⚠️ 2026-08-31 — import 가 bare specifier 로 바뀌었다(PR #7).
        #    명제(«codegen 은 그 패키지에서 파생한다»)는 더 나은 방법으로 참이다.
        self.assertTrue(
            "packages/api-artifacts/index.mjs" in self.src
            or "@fcc/api-artifacts" in self.src,
            "codegen 이 아티팩트 패키지를 소비하지 않는다 — 상대 경로도 "
            "bare specifier 도 없다",
        )
        self.assertIn("OPENAPI_SPECS", self.src)
    def test_no_hardcoded_docs_paths(self):
        self.assertNotIn("'docs', 'api', 'session-api.openapi.json'", self.src)
        self.assertNotIn("'docs', 'api', 'headless-api.openapi.json'", self.src)
        self.assertNotIn("'docs', 'api', 'platform-api.openapi.json'", self.src)

class TestCiDriftGateCoverage(unittest.TestCase):
    """The frontend.yml CI path filter must trigger on EVERY canonical
    artifact source so the mirror drift gate (``sync.mjs --check``) actually
    runs when a contract source changes.

    Codex review (2026-06-18) found the central DB schema — the 4th
    @fcc/api-artifacts artifact — was absent from the path filter, so a change
    to it would not fire the drift gate in CI. This seals all four sources.
    """
    def setUp(self):
        self.workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "frontend.yml"
        ).read_text(encoding="utf-8")
    def test_runs_sync_check_gate(self):
        self.assertIn("sync.mjs --check", self.workflow)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
