"""FE Phase 2 — Route UX Rework invariant seal (2026-05-30).

Sprint slug ``fe-phase2-route-ux-rework``. The architecture plan §6 re-organises
the five operator routes (``projects`` / ``sessions`` / ``reports`` /
``providers`` / ``control``) on top of the Phase 1 primitive library — Toolbar /
PageHeader / FieldGroup / StatusBadge / MetricStrip / DataTable / EmptyState /
ErrorState / DataTableSkeleton / MeasurementValueCell / SectionBand +
``describeApiError``. The Phase 2 acceptance lives in five places:

1.  every route imports from ``@/ui`` (primitive adoption — M1);
2.  inline 4xx switch arms (``error.status === 403`` / ``=== 404`` / etc.) are
    replaced by ``describeApiError`` delegation (M4);
3.  ``providers.tsx`` carries the read-only banner + zero edit / save / publish
    affordances (M3.1);
4.  ``reports.tsx`` never composes a raw filesystem path (M3.2);
5.  the route-workflow e2e specs (§8.3) exist on disk so a missing surface fails
    backend CI independently of the playwright run.

Companion skill: ``/verify-fe-phase2-route-rework``.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
WEB_ROOT = PROJECT_ROOT / "apps" / "web"
ROUTES_DIR = WEB_ROOT / "src" / "routes"
E2E_DIR = WEB_ROOT / "tests" / "e2e"


# §6 — every operator route consumes the Phase 1 primitive library + the
# describeApiError taxonomy. Per-route minimum primitive set comes from the
# §6 mapping matrix in
# ``.claude/exec-plans/completed/2026-05-30-fe-phase2-route-ux-rework.md``.
ROUTE_PRIMITIVE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "projects.tsx": (
        "PageHeader",
        "Toolbar",
        "FieldGroup",
        "StatusBadge",
        "DataTable",
        "EmptyState",
        "ErrorState",
        "describeApiError",
    ),
    "sessions.tsx": (
        "PageHeader",
        "Toolbar",
        "FieldGroup",
        "SectionBand",
        "DataTable",
        "MeasurementValueCell",
        "EmptyState",
        "ErrorState",
        "describeApiError",
    ),
    "reports.tsx": (
        "PageHeader",
        "SectionBand",
        "MetricStrip",
        "DataTable",
        "StatusBadge",
        "ErrorState",
        "describeApiError",
    ),
    "providers.tsx": (
        "PageHeader",
        "SectionBand",
        "DataTable",
        "StatusBadge",
        "ErrorState",
        "describeApiError",
    ),
    "control.tsx": (
        "PageHeader",
        "SectionBand",
        "MetricStrip",
        "ErrorState",
        "describeApiError",
    ),
    "jobs.tsx": (
        "PageHeader",
        "SectionBand",
        "MetricStrip",
        "DataTable",
        "StatusBadge",
        "EmptyState",
        "ErrorState",
        "describeApiError",
    ),
    # FE-P8 membership roster (sealed 2026-06-14, fe-error-id-ssot) — the route
    # shipped its primitives in FE-P8 but was missing from this CI gate.
    "membership.tsx": (
        "PageHeader",
        "DataTable",
        "StatusBadge",
        "EmptyState",
        "ErrorState",
        "describeApiError",
    ),
}

E2E_WORKFLOW_SPECS = (
    "projects-workflow.spec.ts",
    "sessions-workflow.spec.ts",
    "reports-workflow.spec.ts",
    "providers-workflow.spec.ts",
    "control-workflow.spec.ts",
    # FE-P8 (2026-06-13) — membership / RBAC roster route workflow smoke.
    "membership-workflow.spec.ts",
    # FE-JOBS (2026-06-14) — headless measurement jobs console smoke.
    "jobs-workflow.spec.ts",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestPrimitiveAdoption(unittest.TestCase):
    """M1 — every operator route imports the required Phase 1 primitives."""

    def test_each_route_imports_from_ui_barrel(self):
        for filename in ROUTE_PRIMITIVE_REQUIREMENTS:
            source = _read(ROUTES_DIR / filename)
            self.assertRegex(
                source,
                r"from\s+['\"]@/ui['\"]",
                f"{filename} must import from '@/ui' (Phase 2 primitive adoption)",
            )

    def test_each_route_imports_required_primitives(self):
        for filename, required in ROUTE_PRIMITIVE_REQUIREMENTS.items():
            source = _read(ROUTES_DIR / filename)
            for name in required:
                # The import is `import { A, B, C, ... } from '@/ui'`; we assert
                # the symbol appears at least once in the file (covers both the
                # import line and the JSX site).
                self.assertRegex(
                    source,
                    rf"\b{re.escape(name)}\b",
                    f"{filename} missing required Phase 1 primitive: {name}",
                )


class TestDescribeApiErrorDelegation(unittest.TestCase):
    """M4 — inline status branching is replaced by describeApiError delegation."""

    # Sites that already had a `describeError` helper in Phase 1 — they MUST
    # delegate to `describeApiError` (the central 6-arm taxonomy) instead of
    # re-implementing the switch in their own bodies.
    DELEGATING_ROUTES = ("reports.tsx", "providers.tsx", "membership.tsx")

    def test_describing_routes_delegate_to_describe_api_error(self):
        for filename in self.DELEGATING_ROUTES:
            source = _read(ROUTES_DIR / filename)
            self.assertRegex(
                source,
                r"describeApiError\s*\(",
                f"{filename} must call describeApiError(...) (Phase 2 SSOT delegation)",
            )

    def test_sessions_inline_403_branching_is_purged(self):
        # sessions.tsx had an inline `error.status === 403 ? '권한이 없습니다.' : ...`
        # branch in FE-P4; Phase 2 collapses it through describeApiError.
        source = _read(ROUTES_DIR / "sessions.tsx")
        self.assertNotRegex(
            source,
            r"\.status\s*===\s*403",
            "sessions.tsx must NOT contain an inline `status === 403` branch — "
            "delegate to describeApiError(...) instead.",
        )

    def test_reports_describe_error_body_delegates(self):
        # The legacy `describeError` wrapper stays as a stable named export, but
        # its body must call describeApiError — the 6-arm switch lives in ONE
        # place (errors.ts), not duplicated here.
        source = _read(ROUTES_DIR / "reports.tsx")
        match = re.search(
            r"export\s+function\s+describeError\s*\([^)]*\)\s*:\s*string\s*\{([^}]+?)\}",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "reports.tsx `describeError` export not found")
        body = match.group(1)
        self.assertIn(
            "describeApiError",
            body,
            "reports.tsx `describeError` must delegate to describeApiError(...)",
        )
        # The body must NOT carry its own 4xx switch any more.
        self.assertNotIn("status === 403", body)
        self.assertNotIn("status === 404", body)
        self.assertNotIn("status === 409", body)

    def test_membership_purges_inline_status_switch(self):
        # membership.tsx::membershipErrorMessage delegated its inline
        # `switch (error.status)` to describeApiError (fe-error-id-ssot,
        # 2026-06-14). A regression would re-introduce the switch.
        source = _read(ROUTES_DIR / "membership.tsx")
        self.assertNotRegex(
            source,
            r"switch\s*\(\s*error\.status\s*\)",
            "membership.tsx must NOT branch on error.status inline — delegate to "
            "describeApiError(...) (7-arm taxonomy SSOT) instead.",
        )


class TestNumericIdSsot(unittest.TestCase):
    """fe-error-id-ssot (2026-06-14) — the positive-integer id validation lives
    once in ``@/shared/numeric-id`` and both consuming routes delegate to it
    instead of re-declaring the ``^\\d+$`` + isSafeInteger guard inline."""

    SHARED = WEB_ROOT / "src" / "shared" / "numeric-id.ts"
    # The two routes that previously each inlined the parser (reports.tsx::
    # parsePositiveId, sessions.tsx::parseSessionParam).
    CONSUMERS = ("reports.tsx", "sessions.tsx")

    def test_ssot_module_owns_the_regex(self):
        source = _read(self.SHARED)
        self.assertRegex(
            source,
            r"export\s+function\s+parsePositiveId\s*\(",
            "numeric-id.ts must export parsePositiveId()",
        )
        self.assertIn(r"/^\d+$/u", source, "numeric-id.ts must own the ^\\d+$ guard")

    def test_consumers_do_not_inline_the_regex(self):
        for filename in self.CONSUMERS:
            source = _read(ROUTES_DIR / filename)
            self.assertNotIn(
                r"/^\d+$/u",
                source,
                f"{filename} must delegate id parsing to @/shared/numeric-id "
                "(no inline ^\\d+$ regex)",
            )

    def test_consumers_import_from_the_ssot(self):
        for filename in self.CONSUMERS:
            source = _read(ROUTES_DIR / filename)
            self.assertRegex(
                source,
                r"from\s+['\"]@/shared/numeric-id['\"]",
                f"{filename} must import parsePositiveId from @/shared/numeric-id",
            )


class TestProvidersReadOnlySeal(unittest.TestCase):
    """M3.1 — providers.tsx must remain edit-free until ADR-0005 lands.

    The DOM-wide e2e seal (`providers-workflow.spec.ts`) asserts the same
    contract behaviourally; this AST guard catches a source-level regression
    in a backend-only CI lane.
    """

    @classmethod
    def setUpClass(cls):  # noqa: D401
        cls.source = _read(ROUTES_DIR / "providers.tsx")

    def test_no_edit_save_publish_button_labels(self):
        # Korean labels (편집 / 저장 / 발행) AND English labels (Edit / Save /
        # Publish) — both are banned regardless of casing or surrounding copy.
        for label in ("편집", "저장", "발행"):
            self.assertNotRegex(
                self.source,
                rf">\s*{label}\s*</",
                f"providers.tsx must not render a '{label}' button (read-only seal)",
            )
        # English variants — match any token boundary, not just the JSX text.
        for label in ("Edit", "Save", "Publish"):
            self.assertNotRegex(
                self.source,
                rf">\s*{label}\s*</",
                f"providers.tsx must not render an '{label}' button (read-only seal)",
            )

    def test_readonly_banner_is_rendered(self):
        # The read-only banner MUST exist so a future maintainer cannot silently
        # drop it. The workbench redesign moved the banner copy into i18n tokens
        # (`routes.providers.readonlyBanner*`) — the seal therefore verifies the
        # DOM hook + the token references in source + that ko/en supply non-empty
        # banner copy, NOT a stale inline English literal.
        import json

        self.assertIn('data-testid="readonly-banner"', self.source)
        banner_tokens = (
            "readonlyBannerPrefix",
            "readonlyBannerEmphasis",
            "readonlyBannerSuffix",
        )
        for token in banner_tokens:
            self.assertIn(
                token,
                self.source,
                f"providers.tsx must reference the '{token}' i18n token",
            )
        locales_dir = WEB_ROOT / "src" / "locales"
        for locale in ("ko.json", "en.json"):
            providers = (
                json.loads(_read(locales_dir / locale))
                .get("routes", {})
                .get("providers", {})
            )
            for token in banner_tokens:
                self.assertTrue(
                    str(providers.get(token, "")).strip(),
                    f"{locale} must supply non-empty routes.providers.{token}",
                )


class TestReportsRawPathSeal(unittest.TestCase):
    """M3.2 — reports.tsx must not compose a raw filesystem path. Downloads
    go through the signed grant endpoint; the FE never inlines a storage root
    or an absolute path."""

    @classmethod
    def setUpClass(cls):  # noqa: D401
        cls.source = _read(ROUTES_DIR / "reports.tsx")

    def test_no_storage_root_reference(self):
        # The backend's storage_root is private — its name must not leak to the
        # FE under any guise.
        self.assertNotIn("storage_root", self.source)
        self.assertNotIn("STORAGE_ROOT", self.source)

    def test_no_absolute_path_inlining(self):
        # No absolute Unix path (e.g. `/var/lib/...`) — note we exclude
        # API routes (`/headless/...`, `/report-automation/...`) which legitimately
        # start with `/`.
        for forbidden in ("/var/", "/home/", "/opt/", "/srv/", "/tmp/", "/root/"):
            self.assertNotIn(
                forbidden,
                self.source,
                f"reports.tsx leaks an absolute path component '{forbidden}'",
            )

    def test_download_uses_signed_grant_endpoint(self):
        # The download mutation MUST hit `.../outputs/download` — the
        # self-authorizing grant endpoint — and never construct its own URL.
        self.assertIn("/outputs/download", self.source)


class TestControlBoundedBuffer(unittest.TestCase):
    """M2.8 — control.tsx preserves the bounded event buffer + 5-state
    failure taxonomy contract from FE-P5."""

    @classmethod
    def setUpClass(cls):  # noqa: D401
        cls.source = _read(ROUTES_DIR / "control.tsx")

    def test_max_buffered_events_constant_preserved(self):
        self.assertIn("MAX_BUFFERED_EVENTS", self.source)
        self.assertRegex(
            self.source,
            r"MAX_BUFFERED_EVENTS\s*=\s*200",
            "control.tsx must keep MAX_BUFFERED_EVENTS = 200",
        )

    def test_classify_control_outcome_preserved(self):
        self.assertIn("classifyControlOutcome", self.source)
        # Five outcomes — idle / pending / forbidden / unreachable / error.
        for kind in ("idle", "pending", "forbidden", "unreachable", "error"):
            self.assertRegex(
                self.source,
                rf"['\"]{kind}['\"]",
                f"control.tsx ControlOutcome must include '{kind}' (5-state taxonomy)",
            )


class TestE2eWorkflowSpecsPresent(unittest.TestCase):
    """M6 — the five route workflow specs exist on disk so a backend-only CI
    lane catches a missing scenario without running playwright."""

    def test_each_workflow_spec_present(self):
        for name in E2E_WORKFLOW_SPECS:
            self.assertTrue(
                (E2E_DIR / name).is_file(),
                f"missing route workflow e2e spec: tests/e2e/{name}",
            )


class TestProvidersConsumesDescriptorTokens(unittest.TestCase):
    """M2.6 / M2.7 / P1-2 — providers.tsx consumes the existing descriptor
    schema (``ProviderFeature.status``) — it does not invent a new readiness
    vocabulary.

    SUPERSEDED in part by R2 (tester-ux-frontend-hardening-followup): the
    earlier M2.7 rule REQUIRED the operator view to *render* the internal
    ``row_identity_source`` token. R2 reverses that — ``row_identity_source`` is
    an internal descriptor identifier, not operator copy, so the operator view
    drops it (the backend descriptor *payload* still carries it, sealed by
    ``test_provider_ui_descriptor_web_ui_0.py``). The operator-surface curation
    is now sealed by
    ``test_frontend_architecture_conformance.py::TestProviderOperatorSurface``;
    here we keep only the still-valid feature.status consumption check."""

    @classmethod
    def setUpClass(cls):  # noqa: D401
        cls.source = _read(ROUTES_DIR / "providers.tsx")

    def test_consumes_feature_status_field(self):
        self.assertIn("feature.status", self.source)

    def test_internal_row_identity_source_not_in_operator_view(self):
        # R2: the internal row-identity identifier must NOT be rendered in the
        # operator view (superseding the prior M2.7 "must surface" rule).
        self.assertNotIn("row_identity_source", self.source)


if __name__ == "__main__":
    unittest.main()
