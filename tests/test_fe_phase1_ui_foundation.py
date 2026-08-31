"""FE Phase 1 — UI Foundation invariant seal (2026-05-30).

Sprint slug ``fe-phase1-ui-foundation``. Frontend Phase 1 of the
``web_ui_excel_replacement_architecture_plan_2026-05-29.md`` introduces

* §5.0 Design Identity tokens — type/spacing/density/motion + status
  SSOT (7 base + draft/published lifecycle kinds) + tabular-nums + light-default
  (dark via prefers-color-scheme / [data-theme='dark']) in
  ``apps/web/src/styles/global.css``.
* §5.1 UI primitives — 11 React components + ``describeApiError`` taxonomy
  under ``apps/web/src/ui/``.
* §5.1 DataTable Phase 1 spec — horizontal overflow + numeric tabular-nums
  + StatusBadge cell + TableSkeleton.

This Python invariant seals all three from the backend-only CI lane so a
refactor that drops a token, deletes a primitive, or re-introduces inline
hex colors fails CI even when ``npm run lint`` is skipped. Companion skill:
``/verify-fe-phase1-ui-foundation``.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent
WEB_ROOT = PROJECT_ROOT / "apps" / "web"
GLOBAL_CSS = WEB_ROOT / "src" / "styles" / "global.css"
UI_DIR = WEB_ROOT / "src" / "ui"


# § 5.1 — sealed primitive set. Adding a primitive requires updating this
# tuple AND the barrel export tuple below.
EXPECTED_PRIMITIVES = (
    "Button",
    "Card",
    "WorkbenchLayout",
    "PageHeader",
    "Toolbar",
    "FieldGroup",
    "StatusBadge",
    "MetricStrip",
    # a2-fe-progress-bar (2026-06-17) — token-driven WAI-ARIA progress indicator
    # consumed by the chambers progress UI (determinate/indeterminate).
    "ProgressBar",
    "DataTable",
    "DataTableSkeleton",
    "EmptyState",
    "ErrorState",
    "MeasurementValueCell",
    "SectionBand",
    "StatusMessage",
    "LoadMoreButton",
    # fe-lookup-form-primitive (2026-06-14) — numeric id lookup scaffold (form +
    # numeric field + submit + role=alert) extracted from reports.tsx's two
    # near-identical inline lookups.
    "NumericLookupForm",
    # project-picker-ssot (2026-06-26) — presentational project selector (native
    # <select> + FieldGroup) that replaced five routes' raw project_id(UUID)
    # text inputs. Data binding lives in @/shared/ProjectSelectField.
    "ProjectPicker",
)

# Tokens that MUST exist in global.css (architecture plan §5.0).
#
# One row was deleted here, and only one. `--font-size-base` was 14px — the
# same value as `--font-size-sm`, i.e. two names for one fact, which is what
# let it rot down to two consumers while `sm` took 39. Contract
# `fe-visual-language-and-responsive-spec` M1.4 retires it outright, so this
# row asserted the exact duplication the contract removes: the two cannot both
# hold. The contract is the requirements SSOT and wins (W2 plan preamble).
# Rungs W2 *adds* (`2xs`, `xl`) are deliberately NOT listed here — they are
# W2's to seal, in `test_frontend_visual_language.py::TestTypeScaleHierarchy`,
# which pins their values and additionally forbids any two rungs sharing a
# value. That is a strictly stronger guard than this list gave.
TYPE_SCALE_TOKENS = (
    ("--font-size-xs", "12px"),
    ("--font-size-sm", "14px"),
    ("--font-size-md", "16px"),
    ("--font-size-lg", "20px"),
)
SPACING_SCALE_TOKENS = (
    ("--space-1", "4px"),
    ("--space-2", "8px"),
    ("--space-3", "12px"),
    ("--space-4", "16px"),
    ("--space-5", "20px"),
    ("--space-6", "24px"),
    ("--space-8", "32px"),
)
DENSITY_TOKENS = ("--row-height", "--cell-pad-y", "--cell-pad-x")
STATUS_KIND_TOKENS = (
    "pass",
    "fail",
    "running",
    "stale",
    "missing",
    "claimed",
    "duplicate",
    # c3-status-kind (2026-06-17) — test-plan lifecycle promoted to first-class
    # status kinds so test-plans.tsx stops borrowing stale/pass. Each needs a
    # `--status-{name}-fg/bg` token + a StatusBadge union member.
    "draft",
    "published",
)
MOTION_TOKENS = ("--motion-fast", "--motion-base", "--motion-emphasis")


class TestDesignTokensSsot(unittest.TestCase):
    """§5.0 design tokens live in ``global.css`` as named CSS variables."""

    @classmethod
    def setUpClass(cls):  # noqa: D401 — unittest hook
        cls.css = GLOBAL_CSS.read_text(encoding="utf-8")

    # ── M1.1 type scale ────────────────────────────────────────────────────
    def test_type_scale_tokens_declared(self):
        for name, value in TYPE_SCALE_TOKENS:
            self.assertRegex(
                self.css,
                rf"{re.escape(name)}\s*:\s*{re.escape(value)}\s*;",
                f"type scale token missing or wrong value: {name} = {value}",
            )

    # ── M1.2 spacing scale ─────────────────────────────────────────────────
    def test_spacing_scale_tokens_declared(self):
        for name, value in SPACING_SCALE_TOKENS:
            self.assertRegex(
                self.css,
                rf"{re.escape(name)}\s*:\s*{re.escape(value)}\s*;",
                f"spacing scale token missing or wrong value: {name} = {value}",
            )

    # ── M1.3 density tokens + compact override ─────────────────────────────
    def test_density_tokens_declared_and_compact_override_present(self):
        for token in DENSITY_TOKENS:
            self.assertIn(token, self.css, f"density token missing: {token}")
        # compact override must redefine row-height + paddings.
        self.assertRegex(
            self.css,
            r":root\[data-density=['\"]compact['\"]\]",
            "compact density override selector missing",
        )

    # ── M1.4 status SSOT (7 base + draft/published lifecycle) ──────────────
    def test_status_tokens_declared(self):
        for status in STATUS_KIND_TOKENS:
            for suffix in ("fg", "bg"):
                token = f"--status-{status}-{suffix}"
                self.assertIn(
                    token,
                    self.css,
                    f"status SSOT token missing: {token}",
                )

    # ── M1.5 motion tokens + reduced-motion guarantee ──────────────────────
    def test_motion_tokens_declared(self):
        for token in MOTION_TOKENS:
            self.assertIn(token, self.css, f"motion token missing: {token}")

    def test_reduced_motion_collapses_motion_tokens(self):
        # prefers-reduced-motion media rule must set every motion token to 0.
        self.assertRegex(
            self.css,
            r"@media\s*\(prefers-reduced-motion:\s*reduce\)",
            "prefers-reduced-motion media query missing",
        )
        # Capture the block content; assert each motion token resets to 0(ms|s).
        block = re.search(
            r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([^@]+?)\}\s*\}",
            self.css,
            re.DOTALL,
        )
        self.assertIsNotNone(block, "reduced-motion media block could not be parsed")
        body = block.group(1)
        for token in MOTION_TOKENS:
            self.assertRegex(
                body,
                rf"{re.escape(token)}\s*:\s*0(ms|s)?\s*;",
                f"reduced-motion does not zero out {token}",
            )

    # ── M1.6 tabular-nums declared on data cells ──────────────────────────
    def test_tabular_nums_declared_on_data_cells(self):
        self.assertRegex(
            self.css,
            r"font-variant-numeric\s*:\s*tabular-nums",
            "tabular-nums declaration missing (FD-P0-1)",
        )

    # ── M1.7 light default + dark overrides ────────────────────────────────
    def test_light_default_with_dark_override(self):
        # :root base must declare a light surface (sample assertion: light
        # surface-bg). Dark is delivered via prefers-color-scheme: dark
        # AND/OR an explicit [data-theme='dark'] selector.
        root_block = re.search(
            r":root\s*\{([^{}]+(?:\{[^{}]*\}[^{}]+)*)\}",
            self.css,
            re.DOTALL,
        )
        self.assertIsNotNone(root_block, ":root block not found")
        root_body = root_block.group(1)
        # Light default surface — the operator-tool default is now light. The
        # semantic token deliberately aliases the primitive palette so the
        # primitive→semantic→component graph remains machine-checkable.
        self.assertRegex(
            root_body,
            r"--p-light-surface-bg\s*:\s*#[fF]{6}",
            ":root light surface primitive must default to #ffffff",
        )
        self.assertRegex(
            root_body,
            r"--surface-bg\s*:\s*var\(--p-light-surface-bg\)",
            ":root surface-bg must alias the light surface primitive",
        )
        self.assertRegex(
            self.css,
            r"@media\s*\(prefers-color-scheme:\s*dark\)",
            "dark prefers-color-scheme override missing",
        )
        self.assertRegex(
            self.css,
            r":root\[data-theme=['\"]dark['\"]\]",
            "explicit [data-theme='dark'] override missing",
        )


class TestThemeTogglePrePaintSsot(unittest.TestCase):
    """Phase T (tester-ux-frontend-redesign §6.1) — the pre-paint theme script
    and the React theme store share one storage key / attribute SSOT.

    ``public/theme-init.js`` (a CSP ``script-src 'self'`` external file) applies
    ``<html data-theme>`` before first paint; ``src/theme/index.ts`` owns the
    same constants for the in-app toggle + persistence. A drift between the two
    (e.g. one writes ``fcc-theme`` and the other reads ``theme``) would silently
    break persistence, so this seals the equality cross-language.
    """

    def _read(self, rel: str) -> str:
        return (WEB_ROOT / rel).read_text(encoding="utf-8")

    def test_theme_store_constants(self) -> None:
        ts = self._read("src/theme/index.ts")
        self.assertIn("THEME_STORAGE_KEY = 'fcc-theme'", ts)
        self.assertIn("THEME_ATTRIBUTE = 'data-theme'", ts)
        self.assertRegex(
            ts, r"SUPPORTED_THEMES\s*=\s*\[\s*'light',\s*'dark'\s*\]"
        )
        self.assertIn("DEFAULT_THEME: Theme = 'light'", ts)

    def test_prepaint_script_matches_store_ssot(self) -> None:
        init = self._read("public/theme-init.js")
        self.assertIn("var STORAGE_KEY = 'fcc-theme';", init)
        self.assertIn("var ATTRIBUTE = 'data-theme';", init)

    def test_index_html_loads_prepaint_script_before_bundle(self) -> None:
        html = self._read("index.html")
        self.assertIn('<script src="/theme-init.js"></script>', html)
        # Pre-paint must precede the app module bundle.
        self.assertLess(
            html.index("/theme-init.js"),
            html.index("/src/main.tsx"),
            "theme-init.js must load before the app bundle to avoid a flash",
        )


class TestLightStatusWcagAa(unittest.TestCase):
    """Phase T (tester-ux-frontend-redesign §6.1) — the light theme status SSOT
    foreground tokens meet WCAG AA (contrast ≥ 4.5:1 against the light surface).

    The pre-flip light palette *claimed* AA in a comment but nothing enforced it,
    and the light-default re-check found ``stale`` (3.38) and ``published`` (4.00)
    below 4.5. This seals the corrected values so a future tweak can't silently
    reintroduce an inaccessible status colour.
    """

    LIGHT_SURFACE = "#ffffff"

    @staticmethod
    def _luminance(hex_color: str) -> float:
        r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))

        def chan(c: float) -> float:
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)

    @classmethod
    def _ratio(cls, fg: str, bg: str) -> float:
        lf, lb = cls._luminance(fg), cls._luminance(bg)
        hi, lo = max(lf, lb), min(lf, lb)
        return (hi + 0.05) / (lo + 0.05)

    def test_light_status_fg_meets_aa(self) -> None:
        css = GLOBAL_CSS.read_text(encoding="utf-8")
        # Strip comments (they mention "@media" + sample hex) then take the FIRST
        # :root {...} block. Status and text foregrounds are light primitives;
        # semantic aliases below them deliberately carry only var() references.
        no_comment = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        root_match = re.search(r":root\s*\{([^}]*)\}", no_comment)
        self.assertIsNotNone(root_match, ":root block not found")
        light_root = root_match.group(1)
        fgs = dict(
            re.findall(
                r"(--p-light-status-[a-z]+-fg|--p-light-fg-primary|--p-light-fg-secondary|--p-light-accent)\s*:\s*(#[0-9a-fA-F]{6})",
                light_root,
            )
        )
        self.assertGreaterEqual(len(fgs), 10, "expected the light status fg token set")
        failures = {
            token: round(self._ratio(value, self.LIGHT_SURFACE), 2)
            for token, value in fgs.items()
            if self._ratio(value, self.LIGHT_SURFACE) < 4.5
        }
        self.assertEqual(
            failures,
            {},
            f"light theme foreground tokens below WCAG AA 4.5:1 vs {self.LIGHT_SURFACE}: {failures}",
        )


class TestUiPrimitivesPresent(unittest.TestCase):
    """§5.1 primitives — file existence + barrel export."""

    def test_ui_directory_exists(self):
        self.assertTrue(UI_DIR.is_dir(), "apps/web/src/ui/ missing")

    def test_all_primitives_present(self):
        for name in EXPECTED_PRIMITIVES:
            path = UI_DIR / f"{name}.tsx"
            self.assertTrue(path.is_file(), f"primitive missing: ui/{name}.tsx")

    def test_errors_module_present(self):
        self.assertTrue(
            (UI_DIR / "errors.ts").is_file(),
            "ui/errors.ts (describeApiError) missing",
        )

    def test_barrel_index_exports_every_primitive_and_describe_api_error(self):
        index = (UI_DIR / "index.ts").read_text(encoding="utf-8")
        for name in EXPECTED_PRIMITIVES:
            self.assertRegex(
                index,
                rf"\bexport\s*\{{[^}}]*\b{re.escape(name)}\b",
                f"ui/index.ts does not export {name}",
            )
        self.assertIn(
            "describeApiError",
            index,
            "ui/index.ts does not export describeApiError",
        )


class TestDescribeApiErrorTaxonomy(unittest.TestCase):
    """§5.1 FD-D — describeApiError covers the 6-arm taxonomy."""

    @classmethod
    def setUpClass(cls):  # noqa: D401
        cls.source = (UI_DIR / "errors.ts").read_text(encoding="utf-8")

    def test_describe_function_exported(self):
        self.assertRegex(
            self.source,
            r"export\s+function\s+describeApiError\s*\(",
            "describeApiError function export missing",
        )

    def test_six_status_arms_present(self):
        # 403/404/409/410 explicit cases + an undefined-status (network) arm
        # + the default arm. The switch literal text is checked, NOT the
        # operator-facing copy (which is i18n-mobile).
        for arm in ("case 403", "case 404", "case 409", "case 410", "case undefined", "default"):
            self.assertIn(arm, self.source, f"taxonomy arm missing: {arm}")

    def test_context_specialises_forbidden(self):
        # 403 must be context-specialisable (platform/headless/session)
        # without forcing route forks. The per-context map name is sealed.
        # fe-i18n-en-ko-parity (2026-06-13): the default copy moved into the
        # i18n bundles, so the map now resolves context → i18n key
        # (`FORBIDDEN_KEY_BY_CONTEXT`) instead of context → inline literal
        # (`DEFAULT_FORBIDDEN_BY_CONTEXT`). The intent — a single per-context
        # 403 specialisation table — is unchanged.
        self.assertIn("FORBIDDEN_KEY_BY_CONTEXT", self.source)
        for context in ("platform", "headless", "session"):
            self.assertIn(context, self.source, f"403 context arm missing: {context}")


class TestErrorStateDisplayOnly(unittest.TestCase):
    """§5.1 ErrorState is display-only (no status branching of its own)."""

    def test_error_state_has_no_status_branching(self):
        source = (UI_DIR / "ErrorState.tsx").read_text(encoding="utf-8")
        # The primitive must NOT contain status switches or HTTP code
        # literals — the taxonomy is owned by describeApiError.
        self.assertNotIn("case 403", source)
        self.assertNotIn("case 404", source)
        self.assertNotIn("case 409", source)
        self.assertNotIn("status === 403", source)


class TestStatusBadgeStatusUnion(unittest.TestCase):
    """§5.0-4 + §5.1 — StatusBadge enforces the status SSOT in TS."""

    @classmethod
    def setUpClass(cls):  # noqa: D401
        cls.source = (UI_DIR / "StatusBadge.tsx").read_text(encoding="utf-8")

    def test_status_kind_union_members(self):
        # The literal union "type StatusKind =" must mention every status
        # in STATUS_KIND_TOKENS exactly once (defends against typos like
        # 'duplicated' or 'expired' creeping in).
        for kind in STATUS_KIND_TOKENS:
            self.assertRegex(
                self.source,
                rf"['\"]{kind}['\"]",
                f"StatusBadge literal union missing kind: {kind}",
            )

    def test_status_kinds_constant_exported(self):
        self.assertIn("export const STATUS_KINDS", self.source)


class TestMeasurementValueCellSplitsNumberAndUnit(unittest.TestCase):
    """§5.1 FD-C — value/unit split + best-effort fallback."""

    def test_split_helper_exported(self):
        source = (UI_DIR / "MeasurementValueCell.tsx").read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"export\s+function\s+splitValueUnit\s*\(",
            "splitValueUnit helper must be exported for unit testing",
        )

    def test_renders_separate_number_and_unit_classes(self):
        source = (UI_DIR / "MeasurementValueCell.tsx").read_text(encoding="utf-8")
        self.assertIn("measurement-value__number", source)
        self.assertIn("measurement-value__unit", source)


class TestDataTablePhase1Spec(unittest.TestCase):
    """§5.1 DataTable Phase 1 — overflow wrapper + caption + classes."""

    @classmethod
    def setUpClass(cls):  # noqa: D401
        cls.table = (UI_DIR / "DataTable.tsx").read_text(encoding="utf-8")
        cls.css = GLOBAL_CSS.read_text(encoding="utf-8")

    def test_overflow_wrapper_present(self):
        self.assertIn("data-table-overflow", self.table)
        self.assertIn(".data-table-overflow", self.css)

    def test_table_caption_required_prop(self):
        # `caption: string` keyword on the props interface.
        self.assertRegex(
            self.table,
            r"caption\s*:\s*string",
            "DataTable must require a caption prop (a11y)",
        )

    def test_data_cell_numeric_class_defined(self):
        # The right-alignment + tabular-nums class is the contract for
        # measurement cells.
        self.assertRegex(
            self.css,
            r"\.data-cell-numeric\s*\{[^}]*tabular-nums",
            ".data-cell-numeric must apply tabular-nums",
        )
        self.assertRegex(
            self.css,
            r"\.data-cell-numeric\s*\{[^}]*text-align\s*:\s*right",
            ".data-cell-numeric must right-align",
        )


class TestProgressBarPrimitive(unittest.TestCase):
    """A2 fe-progress-bar — ProgressBar WAI-ARIA + token contract.

    Seals the role + value attributes, the determinate/indeterminate split, and
    that the fill/track styling is token-driven (no new colour/spacing/motion
    literals). The no-inline-hex rule is enforced by
    ``TestPrimitivesNoInlineHexColors`` (globs ``ui/*.tsx``)."""

    @classmethod
    def setUpClass(cls):  # noqa: D401 — unittest hook
        cls.source = (UI_DIR / "ProgressBar.tsx").read_text(encoding="utf-8")
        cls.css = GLOBAL_CSS.read_text(encoding="utf-8")

    def test_role_progressbar_and_aria_value_contract(self):
        self.assertIn('role="progressbar"', self.source)
        for attr in (
            "aria-label",
            "aria-valuemin",
            "aria-valuemax",
            "aria-valuenow",
            "aria-valuetext",
        ):
            self.assertIn(attr, self.source, f"ProgressBar missing {attr}")

    def test_indeterminate_omits_valuenow(self):
        # The indeterminate branch must yield `undefined` for the sanitized value
        # so `aria-valuenow={clampedNow}` is omitted (ARIA spec: absent valuenow
        # ⇒ indeterminate).
        self.assertRegex(
            self.source,
            r"const clampedNow\s*=\s*isIndeterminate",
            "the sanitized value must collapse to undefined when indeterminate",
        )
        self.assertIn(
            "aria-valuenow={clampedNow}",
            self.source,
            "aria-valuenow must be bound to the sanitized clampedNow value",
        )

    def test_aria_valuenow_shares_fill_clamp_ssot(self):
        # aria-valuenow and the fill width must derive from the SAME sanitize SSOT
        # (clampValue), so a NaN/Infinity/out-of-range input can never make the
        # announcement and the visual disagree, nor leave [valueMin, valueMax].
        self.assertRegex(
            self.source,
            r"function clampValue\(",
            "ProgressBar must define the clampValue sanitize SSOT",
        )
        # clampedNow is produced by clampValue; the fill % derives from clampedNow.
        self.assertRegex(
            self.source,
            r"clampValue\(valueNow,\s*min,\s*max\)",
            "clampedNow must be produced by clampValue over the sanitized bounds",
        )
        self.assertRegex(
            self.source,
            r"toPercent\(clampedNow,",
            "the fill percentage must derive from the same clampedNow value",
        )
        # The sanitizer rejects non-finite input (no NaN announcement/width).
        self.assertIn("!Number.isFinite(value)", self.source)

    def test_public_bounds_sanitized_before_aria(self):
        # A non-finite public valueMin/valueMax must NOT leak into the declared
        # range. The bounds are sanitized through finiteBound() and only the
        # sanitized min/max reach aria-valuemin/aria-valuemax + the clamp math.
        self.assertRegex(
            self.source,
            r"function finiteBound\(",
            "ProgressBar must define the finiteBound bound-sanitize SSOT",
        )
        self.assertRegex(
            self.source,
            r"const min\s*=\s*finiteBound\(valueMin,\s*0\)",
            "valueMin must be sanitized to a finite bound",
        )
        self.assertRegex(
            self.source,
            r"const max\s*=\s*finiteBound\(valueMax,\s*100\)",
            "valueMax must be sanitized to a finite bound",
        )
        # The sanitized bounds (not the raw props) feed the ARIA contract.
        self.assertIn("aria-valuemin={min}", self.source)
        self.assertIn("aria-valuemax={max}", self.source)

    def test_css_classes_are_token_driven(self):
        for selector in (
            ".progress-bar",
            ".progress-bar__track",
            ".progress-bar__fill",
            ".progress-bar--indeterminate",
        ):
            self.assertIn(selector, self.css, f"missing CSS class: {selector}")
        # Fill colour comes from --accent; tone variants from --status-* tokens.
        self.assertRegex(
            self.css,
            r"\.progress-bar__fill\s*\{[^}]*background:\s*var\(--accent\)",
            ".progress-bar__fill must fill with the --accent token",
        )
        self.assertIn("var(--status-running-fg)", self.css)
        # The track + spacing use the spacing scale, not raw px magic numbers.
        self.assertRegex(
            self.css,
            r"\.progress-bar__track\s*\{[^}]*height:\s*var\(--space-",
            ".progress-bar__track height must use a --space-* token",
        )

    def test_indeterminate_sweep_uses_motion_token(self):
        # The animation must consume a --motion-* token so the reduced-motion
        # cascade (which zeroes those tokens) disables it automatically.
        self.assertRegex(
            self.css,
            r"\.progress-bar--indeterminate\s+\.progress-bar__fill\s*\{[^}]*"
            r"animation:[^;]*var\(--motion-",
            "indeterminate sweep must run on a --motion-* token",
        )

    def test_indeterminate_sweep_animation_consumes_motion_token_ssot(self):
        # Both the animation DURATION and the TIMING FUNCTION must come from the
        # motion token SSOT — duration from --motion-emphasis and easing from
        # --motion-easing — so neither drifts from the global motion scale. A raw
        # `ease-in-out` timing literal (or any raw cubic-bezier) on the sweep
        # would re-introduce a hardcoded easing that the --motion-easing token
        # already owns.
        start = self.css.index("/* ProgressBar")
        end = self.css.index("/* DataTable", start)
        section = self.css[start:end]
        m = re.search(
            r"\.progress-bar--indeterminate\s+\.progress-bar__fill\s*\{"
            r"([^}]*)\}",
            section,
        )
        self.assertIsNotNone(
            m, "indeterminate sweep rule must exist in the ProgressBar section"
        )
        rule = m.group(1)
        anim = re.search(r"animation:([^;]*);", rule)
        self.assertIsNotNone(anim, "indeterminate sweep must declare an animation")
        anim_value = anim.group(1)
        self.assertIn(
            "var(--motion-emphasis)",
            anim_value,
            "sweep duration must come from the --motion-emphasis token",
        )
        self.assertIn(
            "var(--motion-easing)",
            anim_value,
            "sweep easing must come from the --motion-easing token, not a raw "
            "timing function",
        )
        self.assertNotRegex(
            anim_value,
            r"ease-in-out|ease-in|ease-out|\bease\b|cubic-bezier",
            "the sweep must not hardcode a raw easing literal — consume "
            "--motion-easing instead",
        )
        # Section-wide seal: NO raw easing literal may appear anywhere in the
        # ProgressBar CSS section (fill transition, sweep animation, keyframes,
        # reduced-motion). Every timing function must resolve through the
        # --motion-easing token (`var(--motion-easing)` keeps the word "easing"
        # whose embedded "ease" is not a `\bease\b` whole-word match). This is
        # stricter than the anim_value check above and catches a raw `ease-in-out`
        # reintroduced on the fill `transition` or any other rule body.
        self.assertNotRegex(
            section,
            r"ease-in-out|ease-in|ease-out|\bease\b|cubic-bezier",
            "the ProgressBar CSS section must not hardcode any raw easing "
            "literal — every timing function consumes --motion-easing",
        )

    def test_geometry_literals_promoted_to_named_custom_properties(self):
        # Every ProgressBar-specific geometry magic number must be declared once
        # as a named --progress-bar-* custom property and consumed via var() — no
        # raw geometry literals in the rule bodies (sealed: no new hardcoding).
        start = self.css.index("/* ProgressBar")
        end = self.css.index("/* DataTable", start)
        section = self.css[start:end]
        for literal, prop in (
            ("999px", "--progress-bar-track-radius"),
            ("1px", "--progress-bar-track-border"),
            ("35%", "--progress-bar-sliver-size"),
            ("-20%", "--progress-bar-sweep-from"),
            ("285%", "--progress-bar-sweep-to"),
            ("3ch", "--progress-bar-value-min-size"),
        ):
            # The literal is declared exactly once — only on its custom property.
            self.assertIn(
                f"{prop}: {literal};",
                section,
                f"{literal} must be declared on the {prop} custom property",
            )
            self.assertEqual(
                section.count(literal),
                1,
                f"{literal!r} must appear only in its --progress-bar-* "
                f"declaration, not as a raw literal in a rule body",
            )
            # And it is consumed via var(), not re-hardcoded.
            self.assertIn(
                f"var({prop})",
                section,
                f"{prop} must be consumed via var() in the rule bodies",
            )

    def test_reduced_motion_pins_static_indeterminate_bar(self):
        # Under prefers-reduced-motion the indeterminate bar must not animate and
        # must not misread as a partial percentage (pinned full-width + inert).
        self.assertRegex(
            self.css,
            r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[^@]*"
            r"\.progress-bar--indeterminate\s+\.progress-bar__fill\s*\{"
            r"[^}]*animation:\s*none",
            "reduced-motion must disable the indeterminate animation",
        )


class TestPrimitivesNoInlineHexColors(unittest.TestCase):
    """§5.0-4 — primitives consume tokens only; never inline a color hex."""

    # Allowed colour-ish tokens. `#root` is the React mount selector, NOT a
    # hex literal — only flagged in `style={{ color: '#abc' }}` shaped sites.
    HEX_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b")

    def test_no_hex_color_literals_in_primitive_source(self):
        # Walk ui/*.tsx + ui/*.ts (errors.ts).
        offenders = []
        for path in sorted(UI_DIR.glob("*.tsx")) + sorted(UI_DIR.glob("*.ts")):
            text = path.read_text(encoding="utf-8")
            # Strip strings that are obvious icon glyphs / unicode escapes.
            # We DO want to flag e.g. `#0b66ff` literals.
            for match in self.HEX_LITERAL.finditer(text):
                lit = match.group(0)
                # Whitelist: 1-char abbreviations < 3 hex are not matched by
                # the regex; everything matched here is a real colour.
                offenders.append((path.name, lit))
        self.assertEqual(
            offenders,
            [],
            "primitive source files contain inline hex color literals — "
            "they must consume --status-*/--accent/--fg-* tokens from "
            f"global.css instead. Offenders: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
