"""Apps/web scaffold invariant — Sprint S1 (ADR-0001 ~ ADR-0006).

The frontend (React + TS + Vite under ``apps/web/``) lives in this
monorepo per ADR-0001.  Backend pytest enforces structural invariants
on the scaffold so a refactor that breaks the SSOT chain (e.g., removes
the OpenAPI codegen script, demotes TypeScript ``strict``, or drops a
required ADR file) fails CI from the Python side as well as the JS side.

The intent is *complementary* to ``apps/web/`` own ``npm run lint`` and
``npm run typecheck``: this invariant catches drift from a backend-only
CI run and from a developer who never invokes the frontend toolchain.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse

# ``tests/conftest.py`` puts ``src`` on sys.path, so the backend API contract
# modules import directly — the dev gateway seal below derives its proxy prefix
# set from them instead of restating any path literal.
from support.api_prefixes import BACKEND_ROUTE_TABLES, top_level_prefixes


project_root = Path(__file__).parent.parent
WEB_ROOT = project_root / "apps" / "web"
ADR_ROOT = project_root / "docs" / "architecture" / "frontend" / "adr"


def _web_permission_constant(name: str) -> str:
    """Return the literal value of an exported permission mirror constant."""
    text = (WEB_ROOT / "src" / "api" / "permissions.ts").read_text(encoding="utf-8")
    match = re.search(rf"export const {re.escape(name)} = '([^']+)';", text)
    if match is None:
        raise AssertionError(f"permissions.ts missing export const {name}")
    return match.group(1)


def _assert_route_uses_permission_constant(testcase: unittest.TestCase, route: str, name: str) -> None:
    testcase.assertIn(name, route, f"route must use {name} from @/api/permissions")


def _assert_app_shell_nav_item(
    testcase: unittest.TestCase,
    *,
    route_key: str,
    route_path: str,
    label_key: str,
) -> None:
    """Seal a global nav entry at its two real SSOT layers.

    ``routes/_layout.tsx`` only renders ``APP_SHELL_NAV_GROUPS``.  Inspecting
    its old inline ``to=`` literals after navigation moved to the shared module
    tests an implementation detail, not reachability.  The route registry owns
    the value and the app-shell registry owns the globally reachable item.
    """
    route_links = (WEB_ROOT / "src" / "shared" / "route-links.ts").read_text(encoding="utf-8")
    app_shell = (WEB_ROOT / "src" / "shared" / "app-shell-navigation.ts").read_text(
        encoding="utf-8"
    )
    testcase.assertRegex(
        route_links,
        rf"\b{re.escape(route_key)}:\s*'{re.escape(route_path)}'",
        f"ROUTE_PATHS.{route_key} must own {route_path}",
    )
    testcase.assertRegex(
        app_shell,
        rf"\{{\s*to:\s*ROUTE_PATHS\.{re.escape(route_key)},"
        rf"\s*labelKey:\s*'{re.escape(label_key)}',\s*end:\s*false,?\s*\}}",
        f"APP_SHELL_NAV_GROUPS must expose ROUTE_PATHS.{route_key}",
    )


REQUIRED_FILES = (
    "package.json",
    "tsconfig.json",
    "tsconfig.node.json",
    "vite.config.ts",
    "eslint.config.js",
    ".prettierrc.json",
    ".gitignore",
    "index.html",
    "playwright.config.ts",
    "README.md",
    "public/runtime-config.template.json",
    "scripts/codegen.mjs",
    "src/main.tsx",
    "src/app.tsx",
    "src/config/runtime.ts",
    "src/observability/tracing.ts",
    "src/observability/sentry.ts",
    "src/observability/web-vitals.ts",
    "src/api/session-client.ts",
    "src/api/generated/.gitkeep",
    "src/routes/_layout.tsx",
    "src/routes/overview.tsx",
    "src/routes/not-found.tsx",
    "src/shared/error-boundary.tsx",
    "src/shared/query-client.ts",
    "src/styles/global.css",
    "tests/setup.ts",
    "tests/runtime.test.ts",
    "tests/e2e/smoke.spec.ts",
)


REQUIRED_ADR_FILES = (
    "README.md",
    "0001-frontend-repo-location.md",
    "0002-stack-selection.md",
    "0003-openapi-ts-client-generator.md",
    "0004-distributed-tracing-sdk.md",
    "0005-central-db-read-model.md",
    "0006-observability-backend.md",
)


class TestAppsWebDirectoryShape(unittest.TestCase):
    """apps/web/ shape — ADR-0001 monorepo layout."""

    def test_apps_web_directory_exists(self):
        self.assertTrue(WEB_ROOT.is_dir(), "apps/web/ directory missing (ADR-0001)")

    def test_required_files_exist(self):
        for relative in REQUIRED_FILES:
            path = WEB_ROOT / relative
            self.assertTrue(path.is_file(), f"apps/web/{relative} missing")


class TestPackageJsonInvariants(unittest.TestCase):
    """package.json invariants — engines + scripts + deps + ADR pins."""

    def setUp(self):
        self.payload = json.loads((WEB_ROOT / "package.json").read_text(encoding="utf-8"))

    def test_is_private_package(self):
        self.assertIs(self.payload.get("private"), True, "package.json must be private (not publishable to npm)")

    def test_engines_pin_node_lts(self):
        engines = self.payload.get("engines", {})
        self.assertIn("node", engines, "engines.node missing")
        self.assertIn(">=22", engines["node"], "engines.node must pin a current LTS major (>=22)")

    def test_required_scripts_present(self):
        scripts = self.payload.get("scripts", {})
        for required in (
            "dev",
            "build",
            "preview",
            "codegen",
            "codegen:check",
            "typecheck",
            "lint",
            "format",
            "test",
            "test:e2e",
            "test:e2e:visual",
            "test:e2e:visual:update",
            "test:e2e:visual:verify",
        ):
            self.assertIn(required, scripts, f"package.json scripts missing '{required}'")

    def test_visual_update_command_is_not_ci_safe(self):
        scripts = self.payload.get("scripts", {})
        update = scripts.get("test:e2e:visual:update", "")
        self.assertIn("--update-snapshots", update)
        self.assertIn("process.env.CI", update)
        self.assertIn("forbidden in CI", update)

        workflow_dir = project_root / ".github" / "workflows"
        for workflow in workflow_dir.glob("*.yml"):
            source = workflow.read_text(encoding="utf-8")
            self.assertNotIn(
                "--update-snapshots",
                source,
                f"CI workflow must verify visual baselines, never update them: {workflow}",
            )

    def test_adr_pinned_runtime_deps(self):
        deps = self.payload.get("dependencies", {})
        # ADR-0002 stack pins
        for required in (
            "react",
            "react-dom",
            "react-router-dom",
            "@tanstack/react-query",
            "zod",
            "openapi-fetch",
        ):
            self.assertIn(required, deps, f"ADR-0002 dependency missing: {required}")
        # ADR-0004 tracing
        for required in (
            "@opentelemetry/sdk-trace-web",
            "@opentelemetry/exporter-trace-otlp-http",
            "@opentelemetry/instrumentation-fetch",
        ):
            self.assertIn(required, deps, f"ADR-0004 tracing dependency missing: {required}")
        # ADR-0006 observability
        self.assertIn("@sentry/browser", deps, "ADR-0006 Sentry dependency missing")
        self.assertIn("web-vitals", deps, "ADR-0006 web-vitals dependency missing")

    def test_adr_pinned_dev_deps(self):
        dev = self.payload.get("devDependencies", {})
        # ADR-0003 codegen + ADR-0002 test tools
        for required in (
            "openapi-typescript",
            "typescript",
            "vite",
            "vitest",
            "@playwright/test",
            "eslint",
            "prettier",
        ):
            self.assertIn(required, dev, f"ADR-required dev dependency missing: {required}")


class TestTypescriptStrictInvariants(unittest.TestCase):
    """tsconfig.json — ADR-0002 mandates strict full options."""

    def setUp(self):
        raw = (WEB_ROOT / "tsconfig.json").read_text(encoding="utf-8")
        # tsconfig allows comments but Sprint S1 keeps it pure JSON
        self.payload = json.loads(raw)

    def test_strict_full_options_enabled(self):
        opts = self.payload["compilerOptions"]
        for flag in (
            "strict",
            "noImplicitAny",
            "noUnusedLocals",
            "noUnusedParameters",
            "noFallthroughCasesInSwitch",
            "noUncheckedIndexedAccess",
            "exactOptionalPropertyTypes",
            "noImplicitOverride",
            "noImplicitReturns",
            "forceConsistentCasingInFileNames",
        ):
            self.assertIs(opts.get(flag), True, f"tsconfig.compilerOptions.{flag} must be true (ADR-0002)")


class TestAdrSeriesPresent(unittest.TestCase):
    """ADR-0001 ~ ADR-0006 must all be committed before any scaffold work."""

    def test_adr_dir_exists(self):
        self.assertTrue(ADR_ROOT.is_dir(), "docs/architecture/frontend/adr/ missing")

    def test_all_adrs_present(self):
        for name in REQUIRED_ADR_FILES:
            self.assertTrue((ADR_ROOT / name).is_file(), f"ADR file missing: {name}")

    def test_adr_index_references_each(self):
        index = (ADR_ROOT / "README.md").read_text(encoding="utf-8")
        for name in REQUIRED_ADR_FILES:
            if name == "README.md":
                continue
            self.assertIn(name, index, f"ADR index README.md does not reference {name}")


class TestRuntimeConfigSsotInvariants(unittest.TestCase):
    """runtime.ts must remain the single Zod-validated source for runtime config."""

    def setUp(self):
        self.source = (WEB_ROOT / "src" / "config" / "runtime.ts").read_text(encoding="utf-8")

    def test_zod_schema_export(self):
        self.assertIn("runtimeConfigSchema", self.source, "Zod schema export missing")
        # `z.object(...)` may be written across multiple lines (Prettier wrap).
        # Use a regex so the assertion survives formatting.
        self.assertRegex(
            self.source,
            r"z\s*\.\s*object\s*\(",
            "Zod object schema missing",
        )

    def test_fail_fast_helper(self):
        self.assertIn("RuntimeConfigError", self.source, "RuntimeConfigError class missing")
        self.assertIn("getRuntimeConfig", self.source, "getRuntimeConfig helper missing")

    def test_no_oidc_client_secret_property(self):
        # Public PKCE client — secret must never appear as a schema property or
        # property access. Comments mentioning the ban-rule are allowed.
        property_decl = re.compile(r"oidcClientSecret\s*[:=]")
        property_access = re.compile(r"\.oidcClientSecret\b")
        self.assertIsNone(
            property_decl.search(self.source),
            "oidcClientSecret declaration found — public-client runtime config must not expose a client secret",
        )
        self.assertIsNone(
            property_access.search(self.source),
            "oidcClientSecret property access found — public-client config must not expose a client secret",
        )


class TestCodegenScriptSsot(unittest.TestCase):
    """scripts/codegen.mjs must source from the @fcc/api-artifacts package
    (B3 / P14). The artifact path list is no longer hardcoded here — it is
    derived from the package's manifest SSOT (OPENAPI_SPECS). The package mirror
    is byte-identical to the canonical docs/api/*.openapi.json SSOT (sealed by
    tests/test_api_artifacts_package.py)."""

    def setUp(self):
        self.source = (WEB_ROOT / "scripts" / "codegen.mjs").read_text(encoding="utf-8")

    def test_consumes_api_artifacts_package(self):
        # SSOT moved from hardcoded docs paths to the @fcc/api-artifacts package.
        self.assertIn(
            "packages/api-artifacts/index.mjs",
            self.source,
            "codegen must import the @fcc/api-artifacts package (single artifact SSOT)",
        )
        self.assertIn(
            "OPENAPI_SPECS",
            self.source,
            "codegen must derive specs from the package OPENAPI_SPECS manifest, not a hardcoded list",
        )

    def test_no_hardcoded_docs_api_path_list(self):
        # The B3 change removes the per-spec docs/api/*.openapi.json resolve()
        # calls; the package owns that mapping now.
        self.assertNotIn(
            "'docs', 'api', 'session-api.openapi.json'",
            self.source,
            "codegen must not hardcode docs/api artifact paths after B3 (package SSOT)",
        )

    def test_check_mode_present(self):
        # CI gate — backend OpenAPI change without frontend regen must fail.
        self.assertIn("--check", self.source, "codegen --check mode missing")
        self.assertIn("checkMode", self.source, "checkMode branch missing")


class TestObservabilityModulesPresent(unittest.TestCase):
    """ADR-0004 tracing + ADR-0006 observability modules."""

    def test_tracing_uses_w3c_propagator(self):
        source = (WEB_ROOT / "src" / "observability" / "tracing.ts").read_text(encoding="utf-8")
        self.assertIn("W3CTraceContextPropagator", source, "ADR-0004 W3C propagator missing")
        self.assertIn("ParentBasedSampler", source, "ADR-0004 ParentBased sampler missing")

    def test_sentry_module_uses_browser_sdk(self):
        # 웨이브 fe-w4-bundle-observability-cost (2026-07-31) — Sentry 부트스트랩이
        # `sentry.ts` → `sentry-runtime.ts` 로 이동했다. `sentry.ts` 는 초기 로드
        # 경로에 남는 **경량 capture 파사드**가 되었고, `@sentry/browser` 를 정적으로
        # 참조하는 유일한 모듈이 on-demand 청크(`sentry-runtime.ts`)다. 이 봉인의
        # 의도(= ADR-0006 브라우저 SDK + replay 기본 마스킹)는 그대로이고 대상 파일만
        # 옮겼다 — 파일명을 고정하는 것은 이 봉인의 목적이 아니었다.
        source = (
            WEB_ROOT / "src" / "observability" / "sentry-runtime.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("@sentry/browser", source, "ADR-0006 @sentry/browser import missing")
        # Privacy default — replay masks all text
        self.assertIn("maskAllText: true", source, "Sentry replay must default to maskAllText")

    def test_web_vitals_metrics(self):
        source = (WEB_ROOT / "src" / "observability" / "web-vitals.ts").read_text(encoding="utf-8")
        for metric in ("onCLS", "onINP", "onLCP", "onTTFB", "onFCP"):
            self.assertIn(metric, source, f"web-vitals metric {metric} missing")


class TestIndexHtmlSecurityHeaders(unittest.TestCase):
    """index.html ships defence-in-depth security headers as meta tags."""

    def setUp(self):
        self.source = (WEB_ROOT / "index.html").read_text(encoding="utf-8")

    def test_csp_meta_tag(self):
        self.assertIn("Content-Security-Policy", self.source, "CSP meta tag missing")
        self.assertIn("frame-ancestors 'none'", self.source, "frame-ancestors must be locked")
        self.assertIn("object-src 'none'", self.source, "object-src must be locked")

    def test_referrer_policy(self):
        self.assertIn('name="referrer"', self.source, "referrer policy missing")
        self.assertIn("strict-origin-when-cross-origin", self.source, "referrer policy too permissive")

    def test_x_content_type_options(self):
        self.assertIn("X-Content-Type-Options", self.source, "X-Content-Type-Options meta missing")

    def test_runtime_config_loaded_before_bundle(self):
        # Loading order: runtime-config.js MUST appear before src/main.tsx
        runtime_pos = self.source.find("runtime-config.js")
        main_pos = self.source.find("src/main.tsx")
        self.assertGreater(runtime_pos, 0, "runtime-config.js script tag missing from index.html")
        self.assertGreater(main_pos, 0, "main.tsx script tag missing from index.html")
        self.assertLess(runtime_pos, main_pos, "runtime-config.js must be loaded BEFORE the React bundle")

    def test_react_root_does_not_override_document_landmark_navigation(self):
        root = re.search(r'<div\s+id="root"([^>]*)>', self.source)
        self.assertIsNotNone(root, "React root element missing")
        self.assertNotIn(
            'role="application"',
            root.group(1),
            "the app shell already owns banner/main/complementary landmarks; "
            "wrapping them in role=application breaks top-level landmark navigation",
        )


class TestRootGitignoreCoversAppsWeb(unittest.TestCase):
    """Root .gitignore must cover apps/web build/test artefacts."""

    def setUp(self):
        self.source = (project_root / ".gitignore").read_text(encoding="utf-8")

    def test_covers_node_modules(self):
        self.assertIn("apps/**/node_modules", self.source)

    def test_covers_dist(self):
        self.assertIn("apps/**/dist", self.source)

    def test_covers_coverage_and_playwright(self):
        self.assertIn("apps/**/coverage", self.source)
        self.assertIn("apps/**/playwright-report", self.source)

    def test_covers_generated_types(self):
        self.assertIn("apps/**/src/api/generated/*.ts", self.source)

    def test_covers_runtime_session_logs(self):
        """src/logger_config.py resolves its session-log root cwd-relative,
        so the tree can land under any app's launch directory (not just the
        repo root / src/). The rule must not be cwd-anchored."""
        self.assertIn("apps/**/logs/", self.source)
        self.assertNotIn(
            "/apps/web/logs/",
            self.source,
            "an anchored one-off rule (e.g. `/apps/web/logs/`) only covers the "
            "cwd someone happened to test from; src/logger_config.py resolves "
            "the session-log root relative to the launching cwd, so any other "
            "app or launch directory reopens the same untracked-directory "
            "defect. The rule must stay unanchored (`apps/**/logs/`).",
        )


class TestAdrCommitPolicyNoHardcodedThreshold(unittest.TestCase):
    """No hardcoded numeric magic in observability budget — Sprint S6/S8 owns measurement-driven baselines.

    Sprint S1 must NOT inline render-budget thresholds into runtime modules
    (the contract-only sprint's mistake: MAX_INITIAL_RENDER_MS_P95=1500 etc.
    with no measured basis). The scaffold stays free of those magic numbers.
    """

    BANNED_LITERAL_PATTERNS = (
        re.compile(r"MAX_INITIAL_RENDER_MS_P95\s*[:=]"),
        re.compile(r"MIN_LARGE_TABLE_ROWS\s*[:=]"),
        re.compile(r"MAX_DOWNLOAD_GRANT_TTL_SECONDS\s*[:=]"),
    )

    def test_no_hardcoded_budget_constants_in_src(self):
        for path in (WEB_ROOT / "src").rglob("*.ts"):
            text = path.read_text(encoding="utf-8")
            for pattern in self.BANNED_LITERAL_PATTERNS:
                self.assertIsNone(
                    pattern.search(text),
                    f"{path.relative_to(WEB_ROOT)} contains banned hardcoded budget constant "
                    f"({pattern.pattern!r}). Budgets belong to Sprint S6/S8 measurement-driven SSOT.",
                )
        for path in (WEB_ROOT / "src").rglob("*.tsx"):
            text = path.read_text(encoding="utf-8")
            for pattern in self.BANNED_LITERAL_PATTERNS:
                self.assertIsNone(
                    pattern.search(text),
                    f"{path.relative_to(WEB_ROOT)} contains banned hardcoded budget constant.",
                )


class TestFeP5RemoteControl(unittest.TestCase):
    """FE-P5 (2026-05-26) — remote measurement control route seal.

    The Session API backend (start/stop/progress + WS ``/session/events``)
    already exists (Sprint F-2); FE-P5 adds the ``apps/web`` control surface.
    This Python invariant seals the route presence + RBAC permission strings,
    cross-checked against the backend ``SESSION_PERMISSION_*`` SSOT so a
    frontend hardcode cannot silently drift from the backend contract.
    """

    def _read(self, *parts: str) -> str:
        return WEB_ROOT.joinpath(*parts).read_text(encoding="utf-8")

    def test_control_feature_files_exist(self):
        for rel in (
            "src/routes/control.tsx",
            "src/api/session-events.ts",
            "tests/control.test.tsx",
            "tests/session-events.test.ts",
        ):
            self.assertTrue((WEB_ROOT / rel).is_file(), f"FE-P5 file missing: apps/web/{rel}")

    def test_control_route_wired_in_router(self):
        app = self._read("src", "app.tsx")
        self.assertIn("import('@/routes/control')", app)
        self.assertRegex(app, r"path:\s*'control'")

    def test_control_nav_item_present(self):
        _assert_app_shell_nav_item(
            self,
            route_key="control",
            route_path="/control",
            label_key="routes.layout.nav.control",
        )

    def test_session_events_uses_runtime_ws_base_url(self):
        # No hardcoded ws:// endpoint — the URL must come from the runtime
        # config ``wsBaseUrl`` SSOT (deployment-injected), never a literal.
        stream = self._read("src", "api", "session-events.ts")
        self.assertIn("wsBaseUrl", stream)
        self.assertNotRegex(stream, r"new WebSocket\(\s*['\"]wss?://")

    def test_control_permissions_mirror_backend_ssot(self):
        # Cross-language SSOT: the frontend RBAC tokens must equal the backend
        # SESSION_PERMISSION_CONTROL / SESSION_PERMISSION_EVENTS constants.
        import sys

        sys.path.insert(0, str(project_root / "src"))
        from application.session.api_contracts import (  # noqa: E402
            SESSION_PERMISSION_CONTROL,
            SESSION_PERMISSION_EVENTS,
        )

        control = self._read("src", "routes", "control.tsx")
        self.assertEqual(_web_permission_constant("PERMISSION_SESSION_CONTROL"), SESSION_PERMISSION_CONTROL)
        self.assertEqual(_web_permission_constant("PERMISSION_SESSION_EVENTS"), SESSION_PERMISSION_EVENTS)
        _assert_route_uses_permission_constant(self, control, "PERMISSION_SESSION_CONTROL")
        _assert_route_uses_permission_constant(self, control, "PERMISSION_SESSION_EVENTS")


class TestFeP6ReportsView(unittest.TestCase):
    """FE-P6 (2026-05-26) — report / artifact view route seal.

    The Headless API report-automation + artifact reads already exist; FE-P6
    adds the ``apps/web`` reports view. Seals route presence + RBAC permission
    strings cross-checked against the backend ``HEADLESS_API_PERMISSIONS`` SSOT.
    """

    def _read(self, *parts: str) -> str:
        return WEB_ROOT.joinpath(*parts).read_text(encoding="utf-8")

    def test_reports_feature_files_exist(self):
        for rel in ("src/routes/reports.tsx", "tests/reports.test.tsx"):
            self.assertTrue((WEB_ROOT / rel).is_file(), f"FE-P6 file missing: apps/web/{rel}")

    def test_reports_route_wired_in_router(self):
        app = self._read("src", "app.tsx")
        self.assertIn("import('@/routes/reports')", app)
        self.assertRegex(app, r"path:\s*'reports'")

    def test_reports_permissions_mirror_backend_ssot(self):
        # Cross-language SSOT: the frontend RBAC tokens must equal the backend
        # HEADLESS_API_PERMISSIONS report-automation read + headless read tokens.
        import sys

        sys.path.insert(0, str(project_root / "src"))
        from fcc_test_contracts.headless.api_contracts import (  # noqa: E402
            HEADLESS_API_PERMISSIONS,
        )

        reports = self._read("src", "routes", "reports.tsx")
        self.assertEqual(
            _web_permission_constant("PERMISSION_REPORT_AUTOMATION_READ"),
            HEADLESS_API_PERMISSIONS["report_automation_stats"],
        )
        self.assertEqual(
            _web_permission_constant("PERMISSION_HEADLESS_READ"),
            HEADLESS_API_PERMISSIONS["list_session_artifacts"],
        )
        _assert_route_uses_permission_constant(self, reports, "PERMISSION_REPORT_AUTOMATION_READ")
        _assert_route_uses_permission_constant(self, reports, "PERMISSION_HEADLESS_READ")


class TestFeP7ProductionGate(unittest.TestCase):
    """FE-P7 (2026-05-26) — production checklist gate seal.

    Seals the CI/CD workflows + Lighthouse config + the measured-derived bundle
    budget. The budget MUST NOT be a magic number: ``maxGzipBytes`` has to equal
    ``ceil(measuredGzipBytes * headroomFactor)`` so a hand-inflated ceiling
    (decoupled from a re-measured baseline) fails the build. Rationale:
    ``docs/architecture/frontend/performance-budget.md``.
    """

    WORKFLOWS = (
        # ci-minutes-root-cause (2026-08-07): one workflow, one build.
        "frontend.yml",
        "frontend-deploy.yml",
        "frontend-qa-evidence.yml",
    )

    def test_ci_cd_workflows_exist(self):
        wf_dir = project_root / ".github" / "workflows"
        for name in self.WORKFLOWS:
            self.assertTrue((wf_dir / name).is_file(), f"missing CI workflow: {name}")

    def test_bundle_budget_is_measured_derived_not_magic(self):
        import math

        budget = json.loads((WEB_ROOT / "bundle-budget.json").read_text(encoding="utf-8"))
        measured = budget["measuredGzipBytes"]
        factor = budget["headroomFactor"]
        self.assertIsInstance(measured, int)
        self.assertGreater(measured, 0)
        self.assertGreaterEqual(factor, 1.0)
        self.assertEqual(
            budget["maxGzipBytes"],
            math.ceil(measured * factor),
            "bundle budget maxGzipBytes must be DERIVED from the measured baseline "
            "(ceil(measuredGzipBytes * headroomFactor)) — re-measure, do not hand-edit.",
        )

    def test_bundle_budget_gate_script_present(self):
        self.assertTrue((WEB_ROOT / "scripts" / "check-bundle-budget.mjs").is_file())
        gate = (WEB_ROOT / "scripts" / "check-bundle-budget.mjs").read_text(encoding="utf-8")
        # The gate must re-verify the derivation, not just compare totals.
        self.assertIn("headroomFactor", gate)
        self.assertIn("measuredGzipBytes", gate)

    def test_lighthouse_accessibility_is_error_gated(self):
        lhci = json.loads((WEB_ROOT / "lighthouserc.json").read_text(encoding="utf-8"))
        assertions = lhci["ci"]["assert"]["assertions"]
        a11y = assertions["categories:accessibility"]
        # WCAG AA gate: accessibility category must be a blocking ``error``.
        self.assertEqual(a11y[0], "error")
        self.assertGreaterEqual(a11y[1]["minScore"], 0.9)

    def test_performance_budget_doc_present(self):
        doc = project_root / "docs" / "architecture" / "frontend" / "performance-budget.md"
        self.assertTrue(doc.is_file(), "missing performance-budget.md rationale SSOT")

    def test_lighthouse_audits_real_app_not_crashed_shell(self):
        # Review P0-1: lhci serves dist/ statically; index.html requires
        # /runtime-config.js or getRuntimeConfig() throws at boot, making the
        # WCAG AA accessibility score a measurement of a crashed shell. The
        # lighthouse job must write a runtime-config stub before auditing.
        self.assertTrue(
            (WEB_ROOT / "scripts" / "write-runtime-config-stub.mjs").is_file(),
            "missing Lighthouse runtime-config stub writer (P0-1)",
        )
        e2e = (project_root / ".github" / "workflows" / "frontend.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("write-runtime-config-stub.mjs", e2e, "lhci job must write the config stub")

    def test_lhci_version_is_pinned_not_floating(self):
        # Review P1-1: a floating @lhci/cli@X.Y.x minor range is non-reproducible.
        e2e = (project_root / ".github" / "workflows" / "frontend.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotRegex(
            e2e, r"@lhci/cli@\d+\.\d+\.x", "lhci version must be pinned (no .x floating range)"
        )


class TestFeP4SessionBrowser(unittest.TestCase):
    """FE-P4 frontend (2026-05-26) — session / result browser route seal.

    Consumes the FE-P4 backend attempt-history API
    (``GET /headless/sessions/{session_id}/attempts``). Seals route presence +
    that the read-model RBAC token mirrors the backend ``HEADLESS_API_PERMISSIONS``
    (``list_session_attempts`` → ``headless:read``).
    """

    def _read(self, *parts: str) -> str:
        return WEB_ROOT.joinpath(*parts).read_text(encoding="utf-8")

    def test_sessions_feature_files_exist(self):
        for rel in ("src/routes/sessions.tsx", "tests/sessions.test.tsx"):
            self.assertTrue((WEB_ROOT / rel).is_file(), f"FE-P4 fe file missing: apps/web/{rel}")

    def test_sessions_route_wired_in_router(self):
        app = self._read("src", "app.tsx")
        self.assertIn("import('@/routes/sessions')", app)
        self.assertRegex(app, r"path:\s*'sessions'")

    def test_sessions_consumes_attempts_api_with_read_permission(self):
        import sys

        sys.path.insert(0, str(project_root / "src"))
        from fcc_test_contracts.headless.api_contracts import (  # noqa: E402
            HEADLESS_API_PERMISSIONS,
            HEADLESS_API_ROUTES,
        )

        sessions = self._read("src", "routes", "sessions.tsx")
        # consumes the FE-P4 backend attempts route
        self.assertIn(HEADLESS_API_ROUTES["list_session_attempts"][1], sessions)
        # RBAC token mirrors backend SSOT
        self.assertEqual(
            _web_permission_constant("PERMISSION_HEADLESS_READ"),
            HEADLESS_API_PERMISSIONS["list_session_attempts"],
        )
        _assert_route_uses_permission_constant(self, sessions, "PERMISSION_HEADLESS_READ")


class TestFeP2CoverageDashboard(unittest.TestCase):
    """FE-P2 (2026-05-27) — project coverage dashboard route seal. ★1순위.

    Consumes the FE-P0d Platform read API (central coverage_by_condition_hash)
    via the typed generated client — NOT local per-target SQLite (no stopgap).
    Seals route presence + that the RBAC token mirrors the backend
    ``PLATFORM_API_PERMISSIONS`` and the coverage path matches ``PLATFORM_API_ROUTES``.
    """

    def _read(self, *parts: str) -> str:
        return WEB_ROOT.joinpath(*parts).read_text(encoding="utf-8")

    def test_projects_feature_files_exist(self):
        for rel in ("src/routes/projects.tsx", "tests/projects.test.tsx"):
            self.assertTrue((WEB_ROOT / rel).is_file(), f"FE-P2 file missing: apps/web/{rel}")

    def test_projects_route_wired_in_router(self):
        app = self._read("src", "app.tsx")
        self.assertIn("import('@/routes/projects')", app)
        self.assertRegex(app, r"path:\s*'projects'")

    def test_dashboard_consumes_central_platform_read_api(self):
        # SSOT: coverage source = central platform read API (NOT local SQLite),
        # RBAC token mirrors the backend PLATFORM_API_PERMISSIONS.
        import sys

        sys.path.insert(0, str(project_root / "src"))
        from application.central_contract.api_contracts import (  # noqa: E402
            PLATFORM_API_PERMISSIONS,
            PLATFORM_API_ROUTES,
        )

        projects = self._read("src", "routes", "projects.tsx")
        client = self._read("src", "api", "platform-client.ts")
        # Keyset pagination adoption (2026-05-27): the coverage route literal is
        # sealed in the typed page helper (platform-client); the dashboard
        # consumes the helper (no hand-built fetch / query params).
        self.assertIn(PLATFORM_API_ROUTES["get_project_coverage"][1], client)
        self.assertIn("fetchCoveragePage", projects)
        self.assertEqual(
            _web_permission_constant("PERMISSION_PLATFORM_READ"),
            PLATFORM_API_PERMISSIONS["get_project_coverage"],
        )
        _assert_route_uses_permission_constant(self, projects, "PERMISSION_PLATFORM_READ")
        # no local per-target SQLite stopgap leaking into the project dashboard
        self.assertNotIn("headless-client", projects)
        self.assertNotIn("session-client", projects)

    def test_dashboard_overlays_claims_and_sync_boundary(self):
        # FE-P3: claim/lock visualization reads active_claims via the same
        # central platform read path (over FE-P2 coverage). FE-SYNC: the honest
        # online-only duplicate-prevention boundary note (ADR-0005) is surfaced.
        import sys

        sys.path.insert(0, str(project_root / "src"))
        from application.central_contract.api_contracts import PLATFORM_API_ROUTES  # noqa: E402

        projects = self._read("src", "routes", "projects.tsx")
        client = self._read("src", "api", "platform-client.ts")
        # Claims route literal sealed in the typed client; the dashboard consumes
        # the claims page helper (keyset pagination adoption, 2026-05-27).
        self.assertIn(PLATFORM_API_ROUTES["list_project_claims"][1], client)  # FE-P3 claims read
        self.assertIn("fetchClaimsPage", projects)  # dashboard consumes the claims helper
        self.assertIn("dedup-boundary", projects)  # FE-SYNC online-only boundary note

    def test_dashboard_adopts_technology_facet_filter(self):
        # Phase B adoption (2026-05-27): the dashboard narrows the read by the
        # technology facet server-side (dedup workflow reaches a technology
        # without paging the full 16k+ set). Cross-language seal — the contract
        # SSOT owns the facet, and the frontend must actually consume it (typed
        # client query + a data-derived datalist control, no hardcoded enum).
        import sys

        sys.path.insert(0, str(project_root / "src"))
        from application.central_contract.api_contracts import (  # noqa: E402
            PLATFORM_API_OPERATION_QUERY,
            PLATFORM_API_QUERY_PARAMS,
        )

        # Contract SSOT: the optional technology facet is declared once and wired
        # onto both project read ops (OpenAPI builder auto-emits it).
        self.assertIn("technology", PLATFORM_API_QUERY_PARAMS)
        for op in ("get_project_coverage", "list_project_claims"):
            self.assertIn("technology", PLATFORM_API_OPERATION_QUERY[op])

        projects = self._read("src", "routes", "projects.tsx")
        client = self._read("src", "api", "platform-client.ts")
        # Typed client helpers accept the facet (forwarded verbatim, not hand-built).
        self.assertIn("technology?: string", client)
        # Dashboard adopts a distinct URL param + forwards the facet to the page
        # helpers (queryKey-driven pagination reset on filter change).
        self.assertIn("techFilter", projects)
        self.assertIn("techQuery", projects)
        # Data-derived datalist suggestions (populated from the loaded coverage,
        # not a hardcoded enum). The "no hardcoded BT/BLE/UNII/DTS enum" property
        # is sealed behaviourally by the vitest test
        # ``ProjectsRoute technology facet filter > derives the datalist
        # suggestions from the loaded coverage`` (asserts the rendered <option>
        # values equal exactly the technologies present in the data), which is
        # the correct layer — a substring grep here would false-trip on a comment
        # or docstring that merely mentions a technology token.
        self.assertIn("tech-filter-options", projects)
        self.assertIn("techOptions", projects)  # the derived (not literal) source


class TestFeP8MembershipRoute(unittest.TestCase):
    """FE-P8 (2026-06-13) — project membership / RBAC roster route seal.

    The backend FE-P8 (membership union-authorize + assign/revoke write service +
    ``audit_events`` atomicity) is shipped & sealed in
    ``test_platform_rbac_membership_audit_fe_p8.py``; the typed client helpers
    (``fetchMembershipsPage`` / ``assignMembership`` / ``revokeMembership``)
    already exist. This route is the missing UI. The seal pins route presence +
    router/nav wiring + that the RBAC permission literals mirror the backend
    ``PLATFORM_API_PERMISSIONS`` and the route consumes ONLY the platform client.
    """

    def _read(self, *parts: str) -> str:
        return WEB_ROOT.joinpath(*parts).read_text(encoding="utf-8")

    def test_membership_feature_files_exist(self):
        for rel in ("src/routes/membership.tsx", "tests/membership.test.tsx"):
            self.assertTrue((WEB_ROOT / rel).is_file(), f"FE-P8 file missing: apps/web/{rel}")

    def test_membership_route_wired_in_router_and_nav(self):
        app = self._read("src", "app.tsx")
        self.assertIn("import('@/routes/membership')", app)
        self.assertRegex(app, r"path:\s*'membership'")
        _assert_app_shell_nav_item(
            self,
            route_key="membership",
            route_path="/membership",
            label_key="routes.layout.nav.membership",
        )

    def test_roster_consumes_central_platform_read_api(self):
        # SSOT: roster source = central platform read API (membership routes),
        # RBAC tokens mirror the backend PLATFORM_API_PERMISSIONS.
        import sys

        sys.path.insert(0, str(project_root / "src"))
        from application.central_contract.api_contracts import (  # noqa: E402
            PLATFORM_API_PERMISSIONS,
            PLATFORM_API_ROUTES,
        )

        membership = self._read("src", "routes", "membership.tsx")
        client = self._read("src", "api", "platform-client.ts")
        # Route literals sealed in the typed client; the route consumes helpers.
        self.assertIn(PLATFORM_API_ROUTES["list_project_memberships"][1], client)
        self.assertIn("fetchMembershipsPage", membership)
        self.assertIn("assignMembership", membership)
        self.assertIn("revokeMembership", membership)
        # Read gate + admin gate mirror the backend permission SSOT.
        self.assertEqual(
            _web_permission_constant("PERMISSION_PLATFORM_READ"),
            PLATFORM_API_PERMISSIONS["list_project_memberships"],
        )
        self.assertEqual(
            _web_permission_constant("PERMISSION_PLATFORM_ADMIN"),
            PLATFORM_API_PERMISSIONS["assign_project_membership"],
        )
        _assert_route_uses_permission_constant(self, membership, "PERMISSION_PLATFORM_READ")
        _assert_route_uses_permission_constant(self, membership, "PERMISSION_PLATFORM_ADMIN")
        # No local per-target SQLite stopgap leaking into the membership route.
        self.assertNotIn("headless-client", membership)
        self.assertNotIn("session-client", membership)

    def test_role_choices_are_data_derived_not_hardcoded_enum(self):
        # No hardcoded project_viewer/engineer/admin enum in the route — the
        # role suggestions are derived from the loaded roster (a <datalist>) and
        # the backend rbac_role_grants SSOT validates the submitted role_key.
        # The "exactly the loaded roles" property is sealed behaviourally by the
        # vitest test ``derives the datalist options from the roles present in
        # the loaded roster``; here we seal the structural markers (the derived
        # source + the datalist) and the absence of a hardcoded role literal.
        import sys

        sys.path.insert(0, str(project_root / "src"))
        from fcc_test_platform.application.rbac_role_catalog import PROJECT_ROLE_KEYS  # noqa: E402

        membership = self._read("src", "routes", "membership.tsx")
        self.assertIn("assign-role-options", membership)  # the datalist
        self.assertIn("roleOptions", membership)  # the derived (not literal) source
        for role_key in PROJECT_ROLE_KEYS:
            self.assertNotIn(
                f"'{role_key}'", membership,
                f"hardcoded role enum {role_key!r} leaked into membership.tsx",
            )


class TestDevCspSeparation(unittest.TestCase):
    """Phase 0 (2026-05-29) — dev/prod CSP source separation seal.

    Architecture plan §4.2: the prod meta CSP (`connect-src 'self' https:
    wss:`) blocks the dev localhost API. The fix is dev/prod *source*
    separation — prod keeps the conservative meta (byte-unchanged); dev gets a
    Vite dev-server HTTP-header CSP whose connect-src is DERIVED from the dev
    runtime-config origins (no host/port re-hardcoded). This backend invariant
    seals that separation from a backend-only CI lane.
    """

    DEV_CSP_TS = WEB_ROOT / "vite" / "dev-csp.ts"
    RC_SOURCE_MJS = WEB_ROOT / "vite" / "runtime-config-source.mjs"
    DEV_CSP_TEST = WEB_ROOT / "vite" / "dev-csp.test.ts"
    DEV_JSON = WEB_ROOT / "public" / "runtime-config.dev.json"
    DEV_STUB_JS = WEB_ROOT / "public" / "runtime-config.js"
    STUB_WRITER = WEB_ROOT / "scripts" / "write-runtime-config-stub.mjs"
    VITE_CONFIG = WEB_ROOT / "vite.config.ts"
    INDEX_HTML = WEB_ROOT / "index.html"

    @staticmethod
    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _prod_connect_src() -> str:
        text = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        meta = re.search(
            r'http-equiv="Content-Security-Policy"\s*content="([^"]*)"',
            text,
            re.DOTALL,
        )
        assert meta is not None, "prod meta CSP not found in index.html"
        directive = re.search(r"connect-src([^;]*)", meta.group(1))
        assert directive is not None, "prod meta CSP has no connect-src directive"
        return directive.group(1).strip()

    # ── §4.2 prod meta negative assertion (the plan's required test) ──────────
    def test_prod_meta_connect_src_has_no_localhost_or_bare_http(self):
        connect_src = self._prod_connect_src()
        tokens = connect_src.split()
        self.assertNotIn("localhost", connect_src, "prod meta connect-src must not allow localhost")
        self.assertNotIn("127.0.0.1", connect_src, "prod meta connect-src must not allow 127.0.0.1")
        # Standalone `http:` scheme source is forbidden in prod (loose-policy
        # regression). `https:` is fine and is NOT matched by an exact-token
        # comparison.
        self.assertNotIn("http:", tokens, "prod meta connect-src must not allow bare http:")
        self.assertNotIn("*", tokens, "prod meta connect-src must not allow wildcard")

    def test_prod_meta_csp_unchanged_strict_connect_src(self):
        # The conservative prod baseline must remain `'self' https: wss:`.
        self.assertEqual(self._prod_connect_src(), "'self' https: wss:")

    # ── §4.2 dev CSP artifacts exist ─────────────────────────────────────────
    def test_dev_csp_artifacts_exist(self):
        for path in (self.DEV_CSP_TS, self.RC_SOURCE_MJS, self.DEV_CSP_TEST, self.DEV_JSON):
            self.assertTrue(path.is_file(), f"Phase 0 dev CSP artifact missing: {path.relative_to(WEB_ROOT)}")

    # ── §4.2 no host/port hardcoded in the dev CSP helper (derived, SSOT) ─────
    def test_dev_csp_helper_has_no_hardcoded_host_port(self):
        text = self._read(self.DEV_CSP_TS)
        for banned in ("127.0.0.1", "localhost", ":8000", ":8081"):
            self.assertNotIn(
                banned,
                text,
                f"dev-csp.ts hardcodes {banned!r} — connect-src must be DERIVED "
                f"from runtime-config origins via runtime-config-source.mjs",
            )

    def test_dev_csp_helper_forbids_loose_policy(self):
        text = self._read(self.DEV_CSP_TS)
        self.assertNotIn("connect-src *", text, "dev CSP must be host-limited, not connect-src *")

    # ── §4.2 shared origin source (stub writer + dev CSP use one loader) ──────
    def test_origin_source_is_shared(self):
        stub = self._read(self.STUB_WRITER)
        dev_csp = self._read(self.DEV_CSP_TS)
        self.assertIn("runtime-config-source", stub, "stub writer must use the shared origin loader")
        self.assertIn("runtime-config-source", dev_csp, "dev CSP helper must use the shared origin loader")
        self.assertIn("loadRuntimeConfig", stub)
        self.assertIn("deriveConnectSrcOrigins", dev_csp)

    # ── §4.2 dev origin SINGLE SOURCE — served stub is GENERATED from dev.json ──
    # The browser-served dev stub is generated from runtime-config.dev.json (the
    # single authored dev SSOT) — the dev origins are no longer hand-copied into
    # two files. Byte-exact equality is sealed by the vitest gate
    # vite/dev-runtime-config-gen.test.ts; this backend seal asserts the stub is
    # a generated artifact (banner) + the generator/--check wiring exists.
    def test_served_stub_is_generated_artifact(self):
        stub = self._read(self.DEV_STUB_JS)
        self.assertTrue(
            stub.lstrip().startswith("// AUTO-GENERATED"),
            "public/runtime-config.js must be GENERATED from runtime-config.dev.json "
            "(AUTO-GENERATED banner) — single dev origin SSOT, not a hand-copied second file",
        )
        self.assertIn(
            "runtime-config.dev.json",
            stub,
            "generated stub must cite its runtime-config.dev.json source",
        )
        # The generated stub still carries the dev origins it derives.
        dev_json = json.loads(self._read(self.DEV_JSON))
        self.assertIn(dev_json["apiBaseUrl"], stub)
        self.assertIn(dev_json["wsBaseUrl"], stub)

    def test_dev_stub_generator_and_check_wired(self):
        gen = WEB_ROOT / "scripts" / "write-dev-runtime-config.mjs"
        self.assertTrue(gen.is_file(), "dev stub generator missing (single-source SSOT)")
        gen_text = gen.read_text(encoding="utf-8")
        self.assertIn("renderDevStubJs", gen_text)
        self.assertIn("--check", gen_text, "generator must support a drift --check mode")
        pkg = json.loads((WEB_ROOT / "package.json").read_text(encoding="utf-8"))
        scripts = pkg.get("scripts", {})
        # predev regenerates before `npm run dev`; check:dev-config is the CI drift gate.
        self.assertEqual(
            scripts.get("predev"),
            "node scripts/write-dev-runtime-config.mjs",
            "package.json must regenerate the dev stub via the predev hook",
        )
        self.assertIn("check:dev-config", scripts, "package.json must expose the dev-config drift check")
        vitest_gate = WEB_ROOT / "vite" / "dev-runtime-config-gen.test.ts"
        self.assertTrue(vitest_gate.is_file(), "vitest byte-equality gate missing")

    def test_dev_json_is_valid_runtime_config_shape(self):
        dev_json = json.loads(self._read(self.DEV_JSON))
        # dev origins must stay loopback-only — they are the input the dev CSP
        # allowlist is derived from. `localhost` is the browser-facing dev
        # standard; `127.0.0.1` remains acceptable for explicit loopback setups.
        api_url = urlparse(dev_json["apiBaseUrl"])
        ws_url = urlparse(dev_json["wsBaseUrl"])
        self.assertEqual(api_url.scheme, "http")
        self.assertEqual(ws_url.scheme, "ws")
        self.assertIn(api_url.hostname, {"localhost", "127.0.0.1"})
        self.assertIn(ws_url.hostname, {"localhost", "127.0.0.1"})

    # ── §4.2 Vite wiring (header injection + serve-only meta strip) ──────────
    def test_vite_config_wires_dev_csp(self):
        text = self._read(self.VITE_CONFIG)
        self.assertIn("buildDevCsp", text, "vite.config.ts must build the dev CSP")
        self.assertIn("devCspMetaStripPlugin", text, "vite.config.ts must strip meta CSP in dev serve")
        self.assertIn("Content-Security-Policy", text, "vite.config.ts must inject the CSP header")

    # ── Dev API gateway (fe-dev-gateway-proxy, 2026-06-14) ───────────────────
    def test_vite_config_provides_dev_api_gateway(self):
        """The dev server path-routes the three API surfaces to their backend
        ASGI apps (local stand-in for the prod single-gateway topology). The
        surface topology lives in dev-stack.config.json (SSOT shared with the
        scripts/dev-stack.mjs launcher); vite.config derives the proxy from it
        (no inline port/prefix literals) and still honours per-surface env
        overrides."""
        text = self._read(self.VITE_CONFIG)
        self.assertIn("proxy:", text, "vite.config.ts server must declare a proxy gateway")
        # Proxy is derived from the shared SSOT, not inline literals.
        self.assertIn("dev-stack.config.json", text, "proxy must derive from the dev-stack SSOT")
        self.assertIn("ws: true", text, "session proxy must enable WebSocket (ws: true)")

        # The SSOT config owns prefixes / ports / factories / env-override keys.
        dev_stack = json.loads(self._read(WEB_ROOT / "dev-stack.config.json"))
        surfaces = {s["key"]: s for s in dev_stack["surfaces"]}
        self.assertEqual(set(surfaces), {"session", "headless", "platform"})
        self.assertTrue(surfaces["session"]["ws"], "session surface must carry the WebSocket")
        # Each surface's OWN prefix must be present; the full required set is
        # derived from the backend route tables by
        # TestDevStackProxyCoversEveryBackendPrefix (a surface may carry more).
        for key, prefix, factory, env_key in (
            ("session", "/session", "session_api_app:create_app", "VITE_SESSION_API_TARGET"),
            ("headless", "/headless", "headless_api_app:create_app", "VITE_HEADLESS_API_TARGET"),
            ("platform", "/platform", "platform_api_app:create_app", "VITE_PLATFORM_API_TARGET"),
        ):
            self.assertIn(prefix, surfaces[key]["pathPrefixes"])
            self.assertEqual(surfaces[key]["uvicornFactory"], factory)
            self.assertEqual(surfaces[key]["targetEnv"], env_key)
            # Backend port must not be re-hardcoded in vite.config (SSOT-derived).
            self.assertNotIn(f":{surfaces[key]['port']}'", text)

    def test_dev_origins_are_vite_same_origin_for_gateway(self):
        """With the dev gateway, the browser calls a single same-origin base and
        Vite proxies onward — so apiBaseUrl/wsBaseUrl point at the Vite dev
        origin, not a backend port. (Keeps the 127.0.0.1 locality the CSP
        derivation depends on.)"""
        dev_json = json.loads(self._read(self.DEV_JSON))
        self.assertEqual(dev_json["apiBaseUrl"], "http://localhost:5173")
        self.assertEqual(dev_json["wsBaseUrl"], "ws://localhost:5173")


# --------------------------------------------------------------------------- #
# Dev gateway prefix coverage (dev-environment-contract-parity, 2026-08-01)
# --------------------------------------------------------------------------- #

#: Surface key (``dev-stack.config.json``) → the backend route-table SSOT that
#: surface's ASGI app serves, and the derivation over it.
#:
#: Both live in ``tests/support/api_prefixes.py`` (gate-and-deploy-path-parity,
#: 2026-08-01) because the **production** gateway seal in
#: ``tests/test_central_docker_compose.py`` needs the identical derivation. When
#: this module owned it privately, the prod seal grew its own separate notion of
#: "which prefixes exist" — which is exactly why fixing ``/report-automation``
#: here left the same hole open in ``infra/central/nginx.conf``. One derivation,
#: two gateways.


#: Directories under ``apps/web`` that are NOT hand-written source. Excluded by
#: pruning the walk rather than by filtering afterwards — `node_modules` alone is
#: ~100k entries, and a build artifact or a Playwright trace that happens to
#: contain a scanned token would otherwise turn a non-defect into a red.
_WEB_NON_SOURCE_DIRS = frozenset(
    {"node_modules", "dist", "coverage", "playwright-report", "test-results", ".vite"}
)


def _web_source_files(suffixes: frozenset[str] | set[str]) -> list[Path]:
    """Hand-written ``apps/web`` files with one of ``suffixes``, walk pruned."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(WEB_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _WEB_NON_SOURCE_DIRS]
        for name in filenames:
            if Path(name).suffix in suffixes:
                found.append(Path(dirpath) / name)
    return sorted(found)


def _spa_route_paths() -> set[str]:
    """Every path declared in ``src/shared/route-links.ts::ROUTE_PATHS``.

    Parsed from the SSOT the router itself reads, so a new screen joins the
    collision check automatically.
    """
    text = (WEB_ROOT / "src" / "shared" / "route-links.ts").read_text(encoding="utf-8")
    match = re.search(r"export const ROUTE_PATHS = \{(.*?)\n\} as const;", text, re.S)
    if match is None:
        raise AssertionError(
            "could not locate ROUTE_PATHS in src/shared/route-links.ts — the "
            "SPA route seal below cannot be evaluated"
        )
    return set(re.findall(r"^\s*\w+:\s*'(/[^']*)'", match.group(1), re.M))


class TestDevStackProxyCoversEveryBackendPrefix(unittest.TestCase):
    """The dev proxy must forward EVERY top-level prefix the backends expose.

    Defect this seals (dev-environment-contract-parity, 2026-08-01): the headless
    surface exposes three top-level prefixes (``/headless``, ``/health`` and
    ``/report-automation``) but ``dev-stack.config.json`` modelled exactly one
    per surface, so ``/report-automation/*`` was never proxied. In dev that made
    ``GET /report-automation/stats`` a 404 and left the W3-6 report-request
    CANCEL button structurally unable to succeed — a shipped feature that could
    not be reached. The existing comment in that config promises the proxy and
    the launcher "cannot drift"; that only ever covered proxy↔launcher, never
    proxy↔backend.

    Deliberately NO exclusion allowlist: all five derived prefixes are proxied,
    including ``/health``. An allowlist is the channel by which the next session
    quietly re-opens this hole, and excluding ``/health`` would buy nothing —
    proxying it is harmless (it is a headless-only contract entry that the SPA
    never calls) whereas the allowlist would be permanent.
    """

    def setUp(self) -> None:
        self.dev_stack = json.loads(
            (WEB_ROOT / "dev-stack.config.json").read_text(encoding="utf-8")
        )
        self.surfaces = {s["key"]: s for s in self.dev_stack["surfaces"]}

    def test_route_table_map_covers_every_configured_surface(self):
        """A 4th dev surface must not be able to appear without a route table.

        Without this, adding a surface to the config would silently leave its
        backend prefixes underived — the coverage assertion below would pass
        vacuously for it.
        """
        self.assertEqual(
            set(BACKEND_ROUTE_TABLES),
            set(self.surfaces),
            "dev-stack.config.json surfaces and the backend route-table map "
            "diverged — add the new surface's route table to "
            "BACKEND_ROUTE_TABLES (or drop the stale surface).",
        )

    def test_every_backend_prefix_is_proxied_by_its_own_surface(self):
        """Per-surface coverage: a prefix must reach the backend that serves it.

        Asserted per surface rather than globally because routing ``/health`` to
        the platform app would be "covered" globally yet still wrong.
        """
        for key, routes in BACKEND_ROUTE_TABLES.items():
            with self.subTest(surface=key):
                configured = set(self.surfaces[key]["pathPrefixes"])
                derived = top_level_prefixes(routes)
                missing = sorted(derived - configured)
                self.assertEqual(
                    missing,
                    [],
                    f"dev proxy does not forward {missing} to the {key} backend "
                    f"— requests to those prefixes 404 in dev while working in "
                    f"production. Add them to the {key} surface's pathPrefixes "
                    f"in apps/web/dev-stack.config.json.",
                )

    def test_prefix_key_is_plural_everywhere(self):
        """The singular ``pathPrefix`` key must be gone from apps/web.

        Keeping both spellings would mean two SSOTs; and a consumer still
        reading the singular key would silently proxy nothing after the config
        went plural.
        """
        offenders = []
        for path in _web_source_files({".json", ".ts", ".mjs"}):
            text = path.read_text(encoding="utf-8")
            if re.search(r"pathPrefix\b(?!e)", text):
                offenders.append(str(path.relative_to(WEB_ROOT)))
        self.assertEqual(
            offenders,
            [],
            f"singular `pathPrefix` still present in {offenders} — a surface can "
            "carry several prefixes, so the plural `pathPrefixes` array is the "
            "only shape. Two spellings = two sources of truth.",
        )

    def test_vite_proxy_consumes_the_plural_key(self):
        """vite.config.ts must expand every prefix, not just the first one."""
        text = (WEB_ROOT / "vite.config.ts").read_text(encoding="utf-8")
        self.assertIn(
            "pathPrefixes",
            text,
            "vite.config.ts must read the plural pathPrefixes array",
        )
        self.assertIn(
            "flatMap",
            text,
            "vite.config.ts must flatMap surfaces → one proxy entry per prefix "
            "(a plain map would yield one entry per SURFACE and drop the extras)",
        )

    def test_proxy_prefixes_match_at_a_path_boundary(self):
        """A proxy prefix must not swallow a longer SPA route that shares it.

        Second defect found while running this wave's own e2e gate: a plain Vite
        proxy key matches every URL that merely STARTS WITH it, so `/session`
        also captured the SPA route `/sessions`. A deep link or reload of 측정
        이력 was forwarded to the session backend and answered
        `{"detail":"Not Found"}` — the app never loaded. Production never had
        this failure mode (nginx matches `location /headless/`, separator
        included), so it was a pure dev-vs-prod divergence, the same family as
        the unproxied `/report-automation` prefix above.

        The assertion is in two parts, and the first is what keeps the second
        honest: it proves a real collision exists in the CURRENT route table, so
        the boundary anchoring is load-bearing rather than decorative. If a
        future rename removes every collision, this test fails loudly and asks
        to be re-justified instead of quietly guarding nothing.
        """
        spa_routes = _spa_route_paths()
        prefixes = sorted(
            {prefix for s in self.surfaces.values() for prefix in s["pathPrefixes"]}
        )
        collisions = sorted(
            f"{route} vs {prefix}"
            for route in spa_routes
            for prefix in prefixes
            if route != prefix
            and route.startswith(prefix)
            and not route.startswith(prefix + "/")
        )
        self.assertTrue(
            collisions,
            "no SPA route currently extends a proxy prefix, so this seal guards "
            "nothing. Either a route was renamed (then delete this test and say "
            "why) or the parse of ROUTE_PATHS broke (then fix the parse).",
        )

        text = (WEB_ROOT / "vite.config.ts").read_text(encoding="utf-8")
        self.assertIn(
            "(?:/|$)",
            text,
            "vite.config.ts must anchor each proxy key at a path boundary "
            f"(a bare prefix key would capture: {collisions}). Vite reads a key "
            "starting with `^` as a RegExp.",
        )
        self.assertIn(
            "`^${prefix}",
            text,
            "the boundary-anchored proxy key must be derived from the prefix, "
            "not written out per surface",
        )


if __name__ == "__main__":
    unittest.main()
