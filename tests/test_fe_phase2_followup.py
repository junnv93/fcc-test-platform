"""FE Phase 2 follow-up — honest self-audit cascade 정공 seal (2026-05-30).

Sprint slug ``fe-phase2-followup``. After the Phase 2 commit (``0c7d3d1``) the
user pushed for an honest self-audit; the cascade-residuals analysis surfaced
8 단편 임시방편 + 3 SSOT 미통합. This sprint closes the P0 + P1 subset:

* **A** — playwright e2e switched from `vite preview` to `vite dev` to dodge
  the prod meta CSP, silently skipping prod build artifact regression. Fix:
  keep preview as the e2e server + env-gated (`VITE_E2E=1`) CSP header +
  meta-strip plugin so the prod artifact is unchanged.
* **B** — `<span data-testid="…" hidden />` legacy testid hack at two sites.
  Fix: EmptyState carries a `testId` props override; the hacks are gone.
* **C** — three near-identical status-mapping functions inline in four
  routes. Fix: `apps/web/src/ui/status-mapping.ts` SSOT + barrel export +
  the four routes import + consume.
* **D** — `verify-provider-ui-descriptor` substring banlist was patched
  to word-boundary in the Phase 2 sprint, still fragile. Fix: match JSX
  text nodes + quoted string literals exactly (no identifier false-positives,
  no JSX-text false-negatives).
* **G** — Phase 2 plan §6.1 / §6.2 acceptance required sync freshness /
  online / claim permission INSIDE the Projects Toolbar and loaded count /
  truncated status INSIDE the Sessions Toolbar. Phase 2 left them as
  separate banners. Fix: right-aligned status pill group inside both
  Toolbars.

Companion skill: ``/verify-fe-phase2-followup``.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
WEB_ROOT = PROJECT_ROOT / "apps" / "web"
SRC_ROUTES = WEB_ROOT / "src" / "routes"
SRC_UI = WEB_ROOT / "src" / "ui"
SRC_VITE = WEB_ROOT / "vite"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestPreviewE2eCspArchitecture(unittest.TestCase):
    """A — preview e2e CSP architecture: prod artifact validated, mock origins
    reachable via env-gated header + meta strip."""

    @classmethod
    def setUpClass(cls):  # noqa: D401
        cls.vite_config = _read(WEB_ROOT / "vite.config.ts")
        cls.dev_csp = _read(SRC_VITE / "dev-csp.ts")
        cls.playwright_config = _read(WEB_ROOT / "playwright.config.ts")

    def test_vite_config_gates_preview_csp_on_vite_e2e_env(self):
        # The preview-mode CSP header MUST be env-gated. A non-e2e
        # `npm run preview` MUST keep serving the prod meta CSP unchanged.
        self.assertIn("VITE_E2E", self.vite_config)
        self.assertIn("previewE2eCsp", self.vite_config)
        # The preview headers spread is conditional on the env gate.
        self.assertRegex(
            self.vite_config,
            r"previewE2eCsp\s*\?\s*\{\s*headers\s*:",
            "preview.headers must be conditional on the VITE_E2E env",
        )

    def test_preview_e2e_meta_strip_plugin_present(self):
        # CSPs combine as an intersection — the dev header alone cannot
        # weaken the prod meta. The plugin MUST strip the meta from the
        # preview-served HTML for the e2e mock origins to be reachable.
        self.assertIn("previewE2eCspMetaStripPlugin", self.dev_csp)
        self.assertIn("previewE2eCspMetaStripPlugin", self.vite_config)
        # The strip plugin must itself gate on VITE_E2E so a non-e2e
        # `npm run preview` keeps the prod meta as the served policy.
        self.assertRegex(
            self.dev_csp,
            r"VITE_E2E.*?[!=]==\s*['\"]1['\"]",
            "previewE2eCspMetaStripPlugin must gate on VITE_E2E",
        )

    def test_playwright_uses_preview_with_vite_e2e_env(self):
        # webServer.command must be `npm run preview` (NOT `npm run dev`) so
        # the prod build artifact is the surface under test.
        self.assertRegex(
            self.playwright_config,
            r"command\s*:\s*[`'\"]npm run preview",
            "playwright webServer.command must launch `npm run preview`",
        )
        # The VITE_E2E env must be forwarded so the preview server receives
        # the gate (cross-env / shell prefix is not portable on Windows).
        self.assertRegex(
            self.playwright_config,
            r"env\s*:\s*\{[^}]*VITE_E2E\s*:\s*['\"]1['\"]",
            "playwright webServer.env must forward VITE_E2E=1",
        )

    def test_e2e_cannot_silently_reuse_a_dev_server(self):
        """The prod artifact under test must not be swappable for a dev server.

        ``command: npm run preview`` said the e2e suite validates the prod build,
        but ``reuseExistingServer: !process.env['CI']`` handed that guarantee
        back locally: ``npm run dev`` listens on the SAME port the config derives
        (5173, from ``E2E_BASE_URL``), so a dev server already running meant
        preview was never spawned. The suite then exercised unminified, unhashed
        dev output with ``VITE_E2E`` unset — and passed, because dev strips the
        meta CSP on its own. A silent mis-validation, local-only, which is why it
        stayed invisible in CI.

        Reuse must therefore be an explicit opt-in ("that listener is my own
        preview"), never the default. The port cannot be separated instead: 5173
        is pinned by the OIDC redirect URI in ``runtime-config.dev.json``, the
        Keycloak realm, ``auth-flow.spec.ts`` and ``vite/e2e-server-port.test.ts``
        — so the honest fix is the reuse condition itself.
        """
        self.assertNotRegex(
            self.playwright_config,
            r"reuseExistingServer\s*:\s*!\s*process\.env\[['\"]CI['\"]\]",
            "reuseExistingServer must not default to true outside CI — a running "
            "`npm run dev` on the same port would silently replace the prod build "
            "artifact the e2e suite claims to validate.",
        )
        self.assertRegex(
            self.playwright_config,
            r"reuseExistingServer\s*:\s*[^,]*E2E_REUSE_SERVER",
            "reuse must be an explicit opt-in via E2E_REUSE_SERVER so the "
            "developer states that the existing listener is the intended server.",
        )
        # CI must still never reuse — the opt-in may not become a CI loophole.
        self.assertRegex(
            self.playwright_config,
            r"reuseExistingServer\s*:\s*[^,]*!\s*process\.env\[['\"]CI['\"]\]",
            "the reuse opt-in must remain conjoined with `!CI` so setting "
            "E2E_REUSE_SERVER in a CI environment cannot re-open the hole.",
        )

    def test_playwright_never_launches_the_dev_server(self):
        """``npm run dev`` must not appear as the webServer command.

        Companion to the reuse seal: blocking implicit reuse is pointless if the
        command itself can be switched to dev.
        """
        self.assertNotRegex(
            self.playwright_config,
            r"command\s*:\s*[`'\"][^`'\"]*npm run dev",
            "playwright webServer.command must never launch the dev server — the "
            "prod build artifact is the surface under test.",
        )

    def test_prod_meta_csp_is_byte_unchanged(self):
        # The on-disk index.html prod meta CSP MUST stay byte-identical to
        # the Phase 0 contract — the e2e relaxation is server-side only.
        index_html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("connect-src 'self' https: wss:", index_html)


class TestEmptyStateTestIdOverride(unittest.TestCase):
    """B — EmptyState testId override + the two routes drop the hidden span hack."""

    def test_empty_state_carries_test_id_props(self):
        source = _read(SRC_UI / "EmptyState.tsx")
        self.assertRegex(
            source,
            r"testId\?\s*:\s*string",
            "EmptyStateProps must carry an optional `testId` props (B fix)",
        )
        # The rendered data-testid must respect the override (default fallback OK).
        self.assertIn("data-testid={testId ?? 'empty-state'}", source)

    def test_projects_uses_coverage_empty_via_empty_state(self):
        source = _read(SRC_ROUTES / "projects.tsx")
        # The hidden span hack must be gone.
        self.assertNotIn('<span data-testid="coverage-empty" hidden />', source)
        # And the EmptyState must carry the legacy testid.
        self.assertIn('testId="coverage-empty"', source)

    def test_providers_uses_descriptor_empty_via_empty_state(self):
        source = _read(SRC_ROUTES / "providers.tsx")
        self.assertNotIn('<span data-testid="descriptor-empty" hidden />', source)
        self.assertIn('testId="descriptor-empty"', source)


class TestStatusMappingSsot(unittest.TestCase):
    """C — status-mapping is now a single bridge module; the four routes
    that used to define their own mapping function consume it."""

    @classmethod
    def setUpClass(cls):  # noqa: D401
        cls.ssot = _read(SRC_UI / "status-mapping.ts")
        cls.barrel = _read(SRC_UI / "index.ts")

    # c3-status-kind (2026-06-17): draftStatusKind joined the bridge so the
    # test-plan lifecycle stops borrowing stale/pass in the route layer.
    MAPPING_FUNCTIONS = (
        "verdictToStatusKind",
        "featureStatusKind",
        "streamStatusKind",
        "jobStatusToStatusKind",
        "draftStatusKind",
    )

    def test_status_mapping_module_exports_mapping_functions(self):
        for name in self.MAPPING_FUNCTIONS:
            self.assertRegex(
                self.ssot,
                rf"export\s+function\s+{name}\s*\(",
                f"status-mapping.ts must export {name}()",
            )

    def test_barrel_re_exports_mapping_functions(self):
        for name in self.MAPPING_FUNCTIONS:
            self.assertRegex(
                self.barrel,
                rf"\bexport\s*\{{[^}}]*\b{name}\b",
                f"ui/index.ts must re-export {name} from status-mapping",
            )

    def test_test_plans_consumes_draft_status_kind_without_borrowing(self):
        # The test-plans route must derive the badge kind from draftStatusKind(),
        # NOT the old `publishable ? 'stale' : 'pass'` borrowing that conflated
        # the draft lifecycle with measurement verdict / staleness semantics.
        # C4 (route-component-decomposition) split the route into a directory of
        # sibling modules — scan the whole route tree so the seal survives the
        # decomposition (draftStatusKind now lives in DraftRow/DraftDetail).
        source = "\n".join(
            _read(p) for p in sorted((SRC_ROUTES / "test-plans").rglob("*.tsx"))
        )
        self.assertRegex(
            source,
            r"\bdraftStatusKind\s*\(",
            "test-plans route must consume draftStatusKind() from @/ui",
        )
        self.assertNotRegex(
            source,
            r"\?\s*'stale'\s*:\s*'pass'",
            "test-plans route must not borrow stale/pass for the draft badge — "
            "use draftStatusKind() (c3-status-kind).",
        )

    INLINE_BANNED = (
        # Each (route, locally-defined mapping function) pair that previously
        # lived inline. Phase 2 follow-up removed all four — a regression
        # would add the function definition back.
        ("projects.tsx", "verdictToStatusKind"),
        ("sessions.tsx", "verdictToStatusKind"),
        ("sessions.tsx", "attemptVerdictKind"),  # Phase 2 alias — purged
        ("providers.tsx", "featureStatusKind"),
        ("control.tsx", "streamStatusKind"),
        ("jobs.tsx", "jobStatusToStatusKind"),
    )

    def test_no_route_redefines_a_mapping_function(self):
        for filename, fn_name in self.INLINE_BANNED:
            source = _read(SRC_ROUTES / filename)
            self.assertNotRegex(
                source,
                rf"\bfunction\s+{fn_name}\s*\(",
                f"{filename} must not redefine `{fn_name}` — import it from @/ui "
                f"(status-mapping SSOT, C fix).",
            )

    ROUTE_CONSUMERS = (
        ("projects.tsx", "verdictToStatusKind"),
        ("sessions.tsx", "verdictToStatusKind"),
        ("providers.tsx", "featureStatusKind"),
        ("control.tsx", "streamStatusKind"),
        ("jobs.tsx", "jobStatusToStatusKind"),
    )

    def test_each_route_imports_its_mapping_from_ui(self):
        for filename, fn_name in self.ROUTE_CONSUMERS:
            source = _read(SRC_ROUTES / filename)
            # The import line lives in the `from '@/ui'` block (already sealed
            # by verify-fe-phase2-route-rework). Here we only check the symbol
            # is referenced — its definition site is sealed elsewhere.
            self.assertRegex(
                source,
                rf"\b{fn_name}\b",
                f"{filename} must consume {fn_name} from @/ui (status-mapping SSOT)",
            )


class TestProviderInvariantUpgrade(unittest.TestCase):
    """D — provider literal banlist matches JSX text and quoted literals only,
    no longer trips on legitimate identifiers like `SectionBand`."""

    # ⚠️ `test_invariant_uses_jsx_text_and_quoted_regex` 는 2026-08-31 에 모노레포로 돌아갔다 — 읽던 대상이
    #    `test_provider_ui_descriptor_web_ui_0.py` 이고 그 파일은 사용자의 측정 자산(`column_names` 등)을
    #    임포트하므로 공개 레포에 실을 수 없다.


    def test_section_band_identifier_does_not_false_trip(self):
        # Smoke: a fake-source-line containing only the identifier `SectionBand`
        # MUST NOT match either pattern for the token `Band`. (The full live
        # invariant runs against the real source; this smoke proves the
        # regex pair is sound.)
        token = "Band"
        esc = re.escape(token)
        jsx_text_re = re.compile(
            rf">[^<{{}}]*(?<![A-Za-z0-9_]){esc}(?![A-Za-z0-9_])[^<{{}}]*<"
        )
        quoted_re = re.compile(
            rf"(?:\"|'|`)[^\"'`]*(?<![A-Za-z0-9_]){esc}(?![A-Za-z0-9_])[^\"'`]*(?:\"|'|`)"
        )
        identifier_only = "import { SectionBand } from '@/ui';"
        self.assertIsNone(jsx_text_re.search(identifier_only))
        self.assertIsNone(quoted_re.search(identifier_only))
        # Conversely, an actual JSX text node MUST match.
        jsx_provider_literal = "<th scope=\"col\">Band</th>"
        self.assertIsNotNone(jsx_text_re.search(jsx_provider_literal))


class TestToolbarStatusIntegration(unittest.TestCase):
    """G — projects + sessions Toolbars carry the plan §6.1 / §6.2 status pills."""

    @classmethod
    def setUpClass(cls):  # noqa: D401
        cls.projects = _read(SRC_ROUTES / "projects.tsx")
        cls.sessions = _read(SRC_ROUTES / "sessions.tsx")
        cls.css = _read(WEB_ROOT / "src" / "styles" / "global.css")

    def test_toolbar_status_group_class_present_in_css(self):
        self.assertIn(".toolbar-status-group", self.css)

    @staticmethod
    def _contains_testid(source: str, testid: str) -> bool:
        # Match either the JSX `data-testid="…"` attribute (used on plain
        # elements) OR the primitive `testId="…"` prop (used when forwarded
        # to a StatusBadge / EmptyState / DataTable, which emits the
        # `data-testid` at the DOM layer).
        return (
            f'data-testid="{testid}"' in source
            or f'testId="{testid}"' in source
        )

    def test_projects_toolbar_carries_sync_online_claim_pills(self):
        # plan §6.1: "상단 Toolbar: Project UUID / Technology filter / Sync
        # freshness / Online·offline / Claim permission 상태"
        for testid in (
            "projects-toolbar-status",
            "toolbar-sync-pill",
            "toolbar-online-pill",
            "toolbar-claim-pill",
        ):
            self.assertTrue(
                self._contains_testid(self.projects, testid),
                f"projects.tsx must surface data-testid `{testid}` (G fix)",
            )

    def test_sessions_toolbar_carries_loaded_and_truncated_pills(self):
        # plan §6.2: "compact Toolbar: Session ID / Tech / loaded count /
        # more·truncated status"
        for testid in (
            "sessions-toolbar-status",
            "toolbar-loaded-pill",
            "toolbar-truncated-pill",
        ):
            self.assertTrue(
                self._contains_testid(self.sessions, testid),
                f"sessions.tsx must surface data-testid `{testid}` (G fix)",
            )


if __name__ == "__main__":
    unittest.main()
