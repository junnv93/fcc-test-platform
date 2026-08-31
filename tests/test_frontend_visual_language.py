"""FE Visual Language & Responsive Spec — machine-checkable seals.

Contract SSOT: ``.claude/contracts/fe-visual-language-and-responsive-spec.md``.

This module grows one milestone at a time. **W1 (structural debt, contract
§6 — M9/M10/M11), W2 (token SSOT, contract §3 — M1–M5), W3 (responsive
contract, §4 — M6/M7) and W4 (state design, §5 — M8) are sealed here.**
Each milestone's classes sit under its own banner comment below, in landing
order, so a reader can tell which defect each seal was written against.

Why these three:

M9  — Fifteen route workbenches each carried a verbatim copy of the same
      layout skeleton (~1,100 lines, 30% of ``global.css``). Copy-paste is
      not merely verbose: the copies had already drifted into five different
      rail widths and two sticky behaviours that nobody decided on, and one
      route (``progress``) was silently missing a rule its markup used. The
      seal is *definition uniqueness*, not line count — a future route that
      pastes a sixteenth copy fails here.
M10 — Two ``@media (max-width: 1023.98px)`` blocks defined the same four
      header selectors; the earlier one could never win the cascade, so it
      was dead code that still read as live configuration. The seal is one
      block per media condition.
M11 — Route markup must not reference a class ``global.css`` does not define
      (``ui-design-sync-loop.md`` rule 2), and the stylesheet must not carry
      workbench definitions no route uses.

Why the W2 seals take the shape they do:

M1  — The scale had two names for 14px and a top rung only 1.43× body, so
      "make the title bigger" had no token to reach for. The seals are rung
      *distinctness* and a minimum h1/body ratio, not a list of blessed sizes.
M2  — 46 ``font-weight: 600`` declarations are not a hierarchy, they are the
      absence of one. Zero-literal alone would be satisfied by renaming all 46
      to one token, so the seal also requires all three rungs to be consumed.
M3  — Radius literals were split 8/6/4px along no rule at all; the seal is on
      the literal (which carries no role and so cannot be reviewed) rather
      than on any particular value.
M4  — Two drop shadows were raw ``rgba(0, 0, 0, …)`` and therefore invisible
      on the ``#0b0d10`` dark surface. Routing depth through ``--shadow-color``
      is what lets the dark remap reach it — hence also the theme-channel
      symmetry seal, which covers *every* custom property rather than a name
      prefix, since prefix filters are how this drift hides.
M5  — Restraint is a product decision (contract §1), so "no spring/bounce" is
      enforced by the geometry of the curve, not by a banned-name list.

Why the W3/W4 seals take the shape they do:

M6  — ``--bp-md`` was declared and consumed by nothing for two milestones. A
      declared-but-unwired breakpoint is worse than a missing one: it reads as
      "the tablet band is handled". The seal is *consumption*, paired with the
      architecture suite's existing no-orphan-literal rule so the scale and the
      implemented bands become one fact instead of two.
M7  — The fold is sealed as *lossless*, not as "detail columns are hidden":
      hiding a value is only acceptable because the overflow line and the card
      body re-render it. And the transition is sealed as CSS-only by banning
      viewport reads in JS — two sources of truth for one boundary is how a
      responsive layout starts disagreeing with itself.
M8  — Loading, empty and error are three different facts. The seals target the
      three ways they get conflated: a text line standing in for a shaped
      placeholder, an ``EmptyState`` rendered while a request is still in
      flight, and a generic retry button standing in for six distinct FCC
      failure modes.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from support.parity import strip_ts_comments
from support.frontend_route_registry import collect_route_entries

ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = ROOT / "apps" / "web" / "src"
GLOBAL_CSS = WEB_SRC / "styles" / "global.css"

#: Route prefixes that own a ``<prefix>-workbench`` family in ``global.css``.
#: Derived from the stylesheet at import time so a new route cannot bypass the
#: seal by simply not being listed here.
_WORKBENCH_SELECTOR = re.compile(r"\.([a-z0-9-]+?)-workbench[a-z0-9_-]*")

#: Declarations that made up the per-route copy-pasted skeleton. Each must be
#: declared exactly once in the file — a second occurrence means a route
#: re-introduced its own copy instead of joining the shared selector list.
_SKELETON_PROBES = (
    "display: grid;\n  grid-template-columns: repeat(3, minmax(0, 1fr));",
    "grid-template-columns: minmax(0, 1fr) minmax(0, var(--workbench-rail-width));",
    "position: sticky;\n  top: var(--space-4);",
)


def _read_css() -> str:
    return GLOBAL_CSS.read_text(encoding="utf-8")


def _top_level_blocks(text: str) -> list[tuple[str, str]]:
    """Split CSS into ``(prelude, body)`` pairs at brace depth 0.

    ``prelude`` keeps any leading comment so at-rule detection has to strip it
    explicitly — that is deliberate: a commented-out condition should not be
    able to hide a duplicate ``@media`` from :class:`TestMediaConditionUnique`.
    """
    blocks: list[tuple[str, str]] = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                chunk = text[start : i + 1]
                head, _, rest = chunk.partition("{")
                blocks.append((head, rest[:-1]))
                start = i + 1
    return blocks


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _strip_ts_comments(text: str) -> str:
    """Drop block and line comments from a TS/TSX source.

    Every seal below reads CODE, never prose. Without this, a doc comment that
    NAMES the banned construct ("never reads window.innerWidth") fails the very
    seal it is documenting — which trains authors to stop explaining bans.

    Delegates to the shared lexer (``tests/test_ts_comment_stripper_ssot.py``).
    ``_strip_comments`` above stays as-is on purpose: it strips **CSS** block
    comments, and CSS is a different language. Running a TS lexer over a CSS
    prelude would not be a repair, it would be a new defect surface.
    """
    return strip_ts_comments(text)


def _selector_set(prelude: str) -> frozenset[str]:
    return frozenset(
        part.strip() for part in _strip_comments(prelude).split(",") if part.strip()
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_comments(text)).strip()


class TestFixturePresent(unittest.TestCase):
    """Guard against a vacuous PASS if the stylesheet moves or empties."""

    def test_global_css_is_present_and_substantial(self) -> None:
        self.assertTrue(GLOBAL_CSS.is_file(), f"missing {GLOBAL_CSS}")
        self.assertGreater(len(_read_css().splitlines()), 500)


class TestWorkbenchDefinitionUniqueness(unittest.TestCase):
    """M9.1 — one definition of the workbench skeleton, not one per route."""

    def test_no_route_scoped_duplicate_of_a_workbench_rule(self) -> None:
        css = _read_css()
        seen: dict[tuple[str, str], list[str]] = {}
        for prelude, body in _top_level_blocks(css):
            head = _strip_comments(prelude).strip()
            if head.startswith("@"):
                continue
            selectors = _selector_set(prelude)
            routes = {
                m.group(1)
                for sel in selectors
                for m in _WORKBENCH_SELECTOR.finditer(sel)
            }
            if not routes:
                continue
            # Erase the route name so two per-route copies collapse onto the
            # same key. A rule that already lists every route collapses onto
            # itself and therefore cannot collide.
            key_selectors = sorted(
                _WORKBENCH_SELECTOR.sub(
                    lambda m: m.group(0).replace(m.group(1) + "-workbench", "X-workbench", 1),
                    sel,
                )
                for sel in selectors
            )
            key = ("|".join(key_selectors), _normalize(body))
            seen.setdefault(key, []).append(sorted(routes)[0])

        duplicates = {k: v for k, v in seen.items() if len(v) > 1}
        self.assertEqual(
            duplicates,
            {},
            "DRIFT — per-route copies of a workbench rule detected. Add the "
            "route to the shared selector list in the 'Workbench layout SSOT' "
            "section of global.css and express any difference as a "
            "--workbench-* override, not as a duplicated block:\n"
            + "\n".join(f"  {k[0]} -> routes {v}" for k, v in duplicates.items()),
        )

    def test_skeleton_declarations_are_declared_once(self) -> None:
        css = _read_css()
        for probe in _SKELETON_PROBES:
            self.assertEqual(
                css.count(probe),
                1,
                f"workbench skeleton declaration appears {css.count(probe)}x "
                f"(expected exactly 1): {probe!r}",
            )


class TestWorkbenchRailWidthSingleToken(unittest.TestCase):
    """M9.2 — the rail width is one token, not five drifted literals."""

    def test_rail_width_token_declared_once(self) -> None:
        css = _read_css()
        self.assertEqual(
            len(re.findall(r"--workbench-rail-width:", css)),
            1,
            "--workbench-rail-width must have exactly one declaration (SSOT).",
        )

    def test_no_route_scoped_rail_width_literal(self) -> None:
        css = _strip_comments(_read_css())
        offenders = [
            line.strip()
            for line in css.splitlines()
            if "workbench" not in line
            and re.search(r"grid-template-columns:.*minmax\((0|\d+px), *\d+(rem|px)\)", line)
        ]
        # The shared rule consumes the token; any *other* rule reintroducing a
        # hard rail width would be a fresh drift source.
        offenders = [o for o in offenders if "var(--workbench-rail-width)" not in o]
        self.assertEqual(
            offenders,
            [],
            "rail-width literal(s) reintroduced outside the token:\n  "
            + "\n  ".join(offenders),
        )


#: A split of one media condition across several blocks is allowed only when
#: *every* block carrying that condition states why in its leading comment.
#: An undocumented second block is the M10.1 failure mode: it reads as live
#: configuration while the cascade guarantees one of them can never win.
_DELIBERATE_SPLIT_MARKER = "deliberate @media split:"


class TestMediaConditionUnique(unittest.TestCase):
    """M10.2 — one ``@media`` block per condition unless the split is argued."""

    def test_no_undocumented_duplicate_media_condition(self) -> None:
        blocks: dict[str, list[str]] = {}
        for prelude, _body in _top_level_blocks(_read_css()):
            head = _strip_comments(prelude).strip()
            if not head.startswith("@media"):
                continue
            blocks.setdefault(_normalize(head), []).append(prelude)

        offenders = {
            cond: len(preludes)
            for cond, preludes in blocks.items()
            if len(preludes) > 1
            and not all(_DELIBERATE_SPLIT_MARKER in p for p in preludes)
        }
        self.assertEqual(
            offenders,
            {},
            "duplicate @media condition(s) — the earlier block can never win "
            "the cascade for any selector the later one also declares, so it "
            f"reads as live configuration while being dead code: {offenders}. "
            "Merge them, or (if the split is genuinely load-bearing) mark "
            f"every block with a leading comment containing "
            f"{_DELIBERATE_SPLIT_MARKER!r} and say why.",
        )

    def test_deliberate_split_marker_is_not_hollow(self) -> None:
        """The escape hatch must not silently become the norm."""
        marked = _read_css().count(_DELIBERATE_SPLIT_MARKER)
        self.assertLessEqual(
            marked,
            2,
            "the deliberate-@media-split escape hatch is a ratchet: the only "
            "documented split today is the two-part reduced-motion guarantee "
            "(token neutralisation next to `:root`, keyframe stop next to the "
            "component). Raising this bound needs a contract amendment.",
        )


class TestNoDeadWorkbenchClass(unittest.TestCase):
    """M11 — route markup ↔ stylesheet workbench classes agree both ways."""

    @staticmethod
    def _defined() -> set[str]:
        return {
            m.group(0)[1:]
            for m in re.finditer(
                r"\.[a-z0-9-]+-workbench[a-z0-9_-]*", _strip_comments(_read_css())
            )
        }

    @staticmethod
    def _used() -> set[str]:
        used: set[str] = set()
        for path in list(WEB_SRC.rglob("*.tsx")) + list(WEB_SRC.rglob("*.ts")):
            if "api/generated" in path.as_posix():
                continue
            used.update(
                re.findall(r"[a-z0-9-]+-workbench[a-z0-9_-]*", path.read_text(encoding="utf-8"))
            )
        return used

    def test_every_referenced_workbench_class_is_defined(self) -> None:
        missing = sorted(self._used() - self._defined())
        self.assertEqual(
            missing,
            [],
            "route markup references workbench class(es) global.css does not "
            f"define (ui-design-sync-loop rule 2): {missing}",
        )

    def test_every_defined_workbench_class_is_referenced(self) -> None:
        unused = sorted(self._defined() - self._used())
        self.assertEqual(
            unused,
            [],
            f"global.css defines unused workbench class(es): {unused}",
        )


# ── W2 (contract §3 — M1–M5): token SSOT ────────────────────────────────────
#
# W1 collapsed fifteen copies of the workbench skeleton into shared selector
# lists. That is what makes W2 affordable: a token change now edits one site
# instead of fifteen, so "literal count 0" is a maintainable state rather than
# a one-off cleanup that decays.
#
# Every seal below is a *count-zero* or *symmetry* assertion, never a line
# budget — W2 adds tokens, so global.css legitimately grows.

#: ``inherit`` is exempt everywhere: it delegates to the cascade rather than
#: hardcoding a value, which is precisely the behaviour the seals want.
_EXEMPT_VALUE = "inherit"


def _declarations(prop: str, css: str) -> list[str]:
    """Every ``prop: <value>`` occurrence outside comments, as raw values."""
    body = _strip_comments(css)
    return [
        m.group(1).strip()
        for m in re.finditer(rf"(?<![-\w]){re.escape(prop)}\s*:([^;{{}}]*);", body)
    ]


def _custom_property_declarations(block_body: str) -> frozenset[str]:
    """Names of custom properties declared at the top level of ``block_body``."""
    depth = 0
    names: set[str] = set()
    for line in _strip_comments(block_body).splitlines():
        stripped = line.strip()
        if depth == 0:
            m = re.match(r"(--[a-z0-9-]+)\s*:", stripped)
            if m:
                names.add(m.group(1))
        depth += stripped.count("{") - stripped.count("}")
    return frozenset(names)


class TestRadiusTokenSSOT(unittest.TestCase):
    """M3.2 — ``border-radius`` reads from ``--radius-*``, never a literal.

    Pre-W2 there were 44 literals split 8px/6px/4px with no rule behind the
    split: ``.auth-failure`` used 8px and its own retry button 6px. A literal
    cannot be reviewed for consistency because it carries no role; a token
    can, so the seal is on the literal, not on the value.
    """

    def test_no_border_radius_literal(self) -> None:
        offenders = [
            v
            for v in _declarations("border-radius", _read_css())
            if v != _EXEMPT_VALUE and not v.startswith("var(--")
        ]
        self.assertEqual(
            offenders,
            [],
            f"border-radius must use a --radius-* token: {offenders}",
        )

    def test_radius_scale_is_declared_and_capped_at_prd_maximum(self) -> None:
        css = _strip_comments(_read_css())
        scale = dict(re.findall(r"(--radius-[a-z]+)\s*:\s*(\d+)px;", css))
        self.assertEqual(
            sorted(scale),
            ["--radius-lg", "--radius-md", "--radius-pill", "--radius-sm"],
            f"unexpected radius scale: {sorted(scale)}",
        )
        # PRD "radius 8px 이하" — the pill is the deliberate exception (a fully
        # rounded chip is not a large-radius rectangle).
        boxy = {k: v for k, v in scale.items() if k != "--radius-pill"}
        self.assertTrue(
            all(int(v) <= 8 for v in boxy.values()),
            f"PRD caps box radius at 8px: {boxy}",
        )


class TestFontWeightTokenSSOT(unittest.TestCase):
    """M2.2/M2.3 — three weight rungs, and all three are actually used.

    A zero-literal check alone would pass if every one of the 46 pre-W2
    ``600``s became ``var(--font-weight-semibold)`` — the same flat hierarchy
    with extra indirection. So the tier-usage assertion is the load-bearing
    half: the mass has to actually split.
    """

    _TIERS = ("normal", "medium", "semibold")

    def test_no_font_weight_literal(self) -> None:
        offenders = [
            v
            for v in _declarations("font-weight", _read_css())
            if v != _EXEMPT_VALUE and not v.startswith("var(--")
        ]
        self.assertEqual(
            offenders,
            [],
            f"font-weight must use a --font-weight-* token: {offenders}",
        )

    def test_every_weight_tier_is_declared_and_consumed(self) -> None:
        css = _strip_comments(_read_css())
        for tier in self._TIERS:
            with self.subTest(tier=tier):
                self.assertIn(f"--font-weight-{tier}:", css)
                self.assertIn(f"var(--font-weight-{tier})", css)


class TestElevationTokenSSOT(unittest.TestCase):
    """M4.3/M4.4 — depth is theme-aware and reserved for overlays.

    The two raw ``rgba(0, 0, 0, …)`` drop shadows this replaces were invisible
    in dark mode: a hardcoded black shadow on a ``#0b0d10`` surface separates
    nothing. Routing them through ``--shadow-color`` is what makes the dark
    remap reach them.
    """

    def test_no_raw_colour_in_box_shadow(self) -> None:
        """Every shadow colour comes from a token.

        Stated as "must reference a token" rather than "must not look like a
        colour": enumerating colour syntaxes is a losing game — the first
        draft of this seal rejected ``rgba()`` and hex but happily accepted
        ``0 1px 2px red`` (found by mutation-testing the seal itself).
        Requiring the token makes the check exhaustive by construction.
        """
        offenders = [
            v
            for v in _declarations("box-shadow", _read_css())
            if v not in (_EXEMPT_VALUE, "none") and "var(--" not in v
        ]
        self.assertEqual(
            offenders,
            [],
            "box-shadow must take its colour from a token "
            f"(--shadow-color / --datatable-overflow-shadow): {offenders}",
        )

    def test_elevation_scale_derives_from_shadow_color(self) -> None:
        css = _strip_comments(_read_css())
        for rung in ("1", "2", "3"):
            with self.subTest(rung=rung):
                m = re.search(rf"--elevation-{rung}\s*:([^;]*);", css)
                self.assertIsNotNone(m, f"--elevation-{rung} is not declared")
                assert m is not None
                self.assertIn(
                    "var(--shadow-color)",
                    m.group(1),
                    f"--elevation-{rung} must tint from --shadow-color so the "
                    "dark remap reaches it",
                )
        self.assertRegex(css, r"--elevation-0\s*:\s*none;")

    def test_no_elevation_on_a_collapsed_border_table_cell(self) -> None:
        """A shadow on a ``border-collapse: collapse`` cell computes but never
        paints — the collapsed cell has no border box to cast from. Such a
        declaration is worse than none: it reads as live configuration while
        doing nothing. Found by pixel-diffing an actual sticky header in W2.
        """
        css = _read_css()
        self.assertIn("border-collapse: collapse;", _strip_comments(css))
        offenders = {
            selector
            for prelude, body in _top_level_blocks(css)
            if "@" not in _strip_comments(prelude)
            for selector in _selector_set(prelude)
            if re.match(r"\.data-table\b.*\b(th|td)\b", selector)
            and "box-shadow" in _strip_comments(body)
        }
        self.assertEqual(
            offenders,
            set(),
            "box-shadow on a collapsed-border table cell is never painted: "
            f"{offenders}",
        )

    def test_heavy_elevation_is_not_used_on_everyday_surfaces(self) -> None:
        """M4.4 — ``elevation-3`` is overlay-only (PRD excludes heavy shadow)."""
        consumers = {
            selector
            for prelude, body in _top_level_blocks(_read_css())
            if "@" not in _strip_comments(prelude)
            for selector in _selector_set(prelude)
            if "var(--elevation-3)" in _strip_comments(body)
        }
        allowed = {".shortcut-help", ".grid-poc__row--dragging"}
        self.assertLessEqual(
            consumers,
            allowed,
            f"--elevation-3 is for overlays/drag only, found: {consumers - allowed}",
        )


class TestTypeScaleHierarchy(unittest.TestCase):
    """M1 — one name per size, and a title that outranks body text."""

    def test_font_size_base_is_fully_retired(self) -> None:
        css = _strip_comments(_read_css())
        self.assertNotRegex(
            css,
            r"--font-size-base\s*:",
            "--font-size-base duplicated --font-size-sm (both 14px) — one fact, "
            "one name",
        )
        self.assertNotIn("var(--font-size-base)", css)

    def test_h1_outranks_body_by_the_contracted_ratio(self) -> None:
        css = _strip_comments(_read_css())
        sizes = {
            m.group(1): int(m.group(2))
            for m in re.finditer(r"(--font-size-[a-z0-9]+)\s*:\s*(\d+)px;", css)
        }
        self.assertEqual(
            sorted(sizes),
            [
                "--font-size-2xs",
                "--font-size-lg",
                "--font-size-md",
                "--font-size-sm",
                "--font-size-xl",
                "--font-size-xs",
            ],
            f"unexpected type scale: {sorted(sizes)}",
        )
        # Values are distinct — a duplicated rung is how --font-size-base
        # became dead weight in the first place.
        self.assertEqual(
            len(set(sizes.values())), len(sizes), f"duplicate rung value: {sizes}"
        )
        # The two rungs W2 introduces are pinned to their contracted values
        # here rather than in test_fe_phase1_ui_foundation.py's
        # TYPE_SCALE_TOKENS: that list is the Phase-1 promise, and a milestone
        # seals what it adds. M1.1 (xl = 26px) and M1.5 (2xs = 11px).
        self.assertEqual(sizes["--font-size-xl"], 26, "M1.1 pins h1 at 26px")
        self.assertEqual(
            sizes["--font-size-2xs"], 11, "M1.5 pins the dense metadata rung at 11px"
        )
        self.assertGreaterEqual(
            sizes["--font-size-xl"] / sizes["--font-size-sm"],
            1.8,
            "h1 must clear body text by ≥1.8× (pre-W2 it was 1.43×)",
        )

    def test_headings_consume_the_restored_rungs(self) -> None:
        blocks = {
            selector: _strip_comments(body)
            for prelude, body in _top_level_blocks(_read_css())
            if "@" not in _strip_comments(prelude)
            for selector in _selector_set(prelude)
        }
        for selector, rung in (
            (".page-header__title", "xl"),
            (".section-band__title", "lg"),
        ):
            with self.subTest(selector=selector):
                self.assertIn(f"var(--font-size-{rung})", blocks[selector])
                # M2.5 — Korean headings must not fragment mid-word.
                self.assertIn("word-break: keep-all", blocks[selector])
                # M2.4 — optical tracking on ≥20px titles.
                self.assertIn("var(--tracking-tight)", blocks[selector])

    def test_dense_metadata_rung_is_not_used_for_body_or_controls(self) -> None:
        """M1.5 — ``2xs`` (11px) is metadata-only; it is below the comfortable
        reading floor for prose, labels and button text."""
        consumers = {
            selector
            for prelude, body in _top_level_blocks(_read_css())
            if "@" not in _strip_comments(prelude)
            for selector in _selector_set(prelude)
            if "var(--font-size-2xs)" in _strip_comments(body)
        }
        self.assertTrue(consumers, "--font-size-2xs is declared but never used")
        forbidden = [
            s
            for s in consumers
            if re.search(r"(button|__label\b|body|__description)", s)
        ]
        self.assertEqual(
            forbidden,
            [],
            f"--font-size-2xs must not carry body/label/button text: {forbidden}",
        )


class TestTrackingVocabulary(unittest.TestCase):
    """M2.4 — two tracking tokens, no ad-hoc em literals."""

    def test_no_letter_spacing_literal(self) -> None:
        offenders = [
            v
            for v in _declarations("letter-spacing", _read_css())
            if v != _EXEMPT_VALUE and not v.startswith("var(--")
        ]
        self.assertEqual(
            offenders,
            [],
            f"letter-spacing must use a --tracking-* token: {offenders}",
        )

    def test_both_tracking_tokens_are_consumed(self) -> None:
        css = _strip_comments(_read_css())
        for name in ("--tracking-tight", "--tracking-wide"):
            with self.subTest(token=name):
                self.assertIn(f"{name}:", css)
                self.assertIn(f"var({name})", css)


class TestMotionRestraint(unittest.TestCase):
    """M5.2 — exactly two easings added, and neither overshoots.

    Contract §1 excludes spring/bounce as a standing decision: this UI is
    watched for hours beside a chamber, so motion that pulls the eye back is
    a cost. A cubic-bezier overshoots iff a control-point ordinate leaves
    [0, 1], which is checkable without naming every "bouncy" curve.
    """

    def test_no_overshooting_easing_is_declared(self) -> None:
        css = _strip_comments(_read_css())
        offenders = []
        for name, curve in re.findall(
            r"(--motion-easing[a-z-]*)\s*:\s*cubic-bezier\(([^)]*)\)", css
        ):
            y1, y2 = (float(p) for p in curve.split(",")[1::2])
            if not (0.0 <= y1 <= 1.0 and 0.0 <= y2 <= 1.0):
                offenders.append((name, curve))
        self.assertEqual(
            offenders, [], f"spring/bounce easing is excluded by contract §1: {offenders}"
        )
        self.assertNotRegex(css, r"--motion-[a-z-]*(spring|bounce|elastic|back)")

    def test_entry_and_exit_easings_are_the_only_additions(self) -> None:
        css = _strip_comments(_read_css())
        declared = set(re.findall(r"(--motion-easing[a-z-]*)\s*:", css))
        self.assertEqual(
            declared,
            {
                "--motion-easing",
                "--motion-easing-decelerate",
                "--motion-easing-accelerate",
            },
            f"motion vocabulary drifted: {sorted(declared)}",
        )

    def test_reduced_motion_still_neutralises_every_duration_token(self) -> None:
        """M5.3 — new motion must ride the duration tokens, which the
        ``prefers-reduced-motion`` block zeroes."""
        css = _read_css()
        durations = set(re.findall(r"(--motion-(?:fast|base|emphasis))\s*:", css))
        reduce_block = next(
            body
            for prelude, body in _top_level_blocks(css)
            if "prefers-reduced-motion" in prelude
        )
        for name in durations:
            with self.subTest(token=name):
                self.assertRegex(reduce_block, rf"{name}\s*:\s*0ms;")


class TestThemeChannelSymmetry(unittest.TestCase):
    """M4.2 — the two dark trigger channels must declare the SAME tokens.

    Dark is delivered twice: ``@media (prefers-color-scheme: dark)`` for the
    OS preference and ``:root[data-theme='dark']`` for the explicit toggle.
    A token added to only one silently makes OS-dark and toggled-dark
    different products, and nobody notices because most developers exercise
    one path. The check is over *every* custom property, not a name prefix —
    a prefix filter is exactly how this class of drift hides.
    """

    def _dark_blocks(self) -> tuple[frozenset[str], frozenset[str]]:
        css = _read_css()
        media = next(
            body
            for prelude, body in _top_level_blocks(css)
            if "prefers-color-scheme: dark" in prelude
        )
        explicit = next(
            body
            for prelude, body in _top_level_blocks(css)
            if "data-theme='dark'" in _strip_comments(prelude)
        )
        # The media form nests `:root:not([data-theme='light'])`; unwrap it so
        # both sides are compared at the same depth.
        inner = media[media.index("{") + 1 : media.rindex("}")]
        return _custom_property_declarations(inner), _custom_property_declarations(
            explicit
        )

    def test_both_dark_channels_declare_the_same_tokens(self) -> None:
        media, explicit = self._dark_blocks()
        self.assertTrue(media, "dark media block declared no tokens")
        self.assertEqual(
            sorted(media ^ explicit),
            [],
            "OS-dark and toggled-dark would diverge for these tokens: "
            f"{sorted(media ^ explicit)}",
        )

    def test_no_dark_only_token(self) -> None:
        media, _ = self._dark_blocks()
        root = next(
            _custom_property_declarations(body)
            for prelude, body in _top_level_blocks(_read_css())
            if _strip_comments(prelude).strip() == ":root"
        )
        orphans = sorted(media - root)
        self.assertEqual(
            orphans,
            [],
            f"dark remaps a token light never declares: {orphans}",
        )


class TestDeadTokenRatchet(unittest.TestCase):
    """§8 — declared-but-unconsumed tokens may only ever decrease.

    A hard zero is not honest here: three pre-W2 families are dead for
    reasons W2 must not touch (see the exemption note and the tech-debt
    tracker entry). A ratchet still makes the number visible and stops the
    next author from adding speculative tokens.
    """

    #: Declared without a consumer *by design*. Both complete a scale the
    #: contract mandates, so a route author reaches for a token instead of
    #: inventing a value:
    #:   --elevation-0            the named zero of the depth scale (M4.4 makes
    #:                            "no shadow" the everyday default, so it needs
    #:                            a word)
    #:   --motion-easing-accelerate  the exit half of the entry/exit pair the
    #:                            contract fixes as a pair (M5.2); no exit
    #:                            transition exists yet to consume it
    _VOCABULARY_COMPLETENESS = frozenset(
        {"--elevation-0", "--motion-easing-accelerate"}
    )

    #: Measured at W2 landing, ratcheted at W3. Ratchet DOWN only. Composed of:
    #:   9  --status-*-icon  glyphs duplicated in StatusBadge's TS map
    #:   3  --status-pass/warn/na  legacy 4-status palette, last consumer gone
    #: W3 removed the 14th (`--bp-md`): it now has a real `@media` consumer, and
    #: `--bp-sm` arrived already consumed (contract M6.2).
    _MAX_DEAD_TOKENS = 12

    @staticmethod
    def _consumed_tokens(css: str) -> set[str]:
        """Tokens with a real consumer.

        `var()` is the usual channel, but a breakpoint token can never be one:
        plain CSS cannot read a custom property inside a `@media` condition, so
        the value is restated as the `token - 0.02` literal. That literal IS the
        consumption, and the correspondence is already sealed both ways
        (`test_every_maxwidth_media_derives_from_a_bp_token` +
        `TestBreakpointScaleIsFullyConsumed`), so counting a breakpoint token as
        dead merely because it lacks a `var()` would punish the one token family
        that structurally cannot have one.
        """
        consumed = set(re.findall(r"var\((--[a-z0-9-]+)", css))
        media_thresholds = {
            round(float(value) + 0.02, 2)
            for value in re.findall(
                r"@media[^{]*\(\s*max-width\s*:\s*(\d+(?:\.\d+)?)px", css
            )
        }
        for name, value in re.findall(r"(--bp-[a-z]+)\s*:\s*(\d+(?:\.\d+)?)px", css):
            if round(float(value), 2) in media_thresholds:
                consumed.add(name)
        for path in sorted(WEB_SRC.rglob("*.ts")) + sorted(WEB_SRC.rglob("*.tsx")):
            consumed |= set(re.findall(r"(--[a-z0-9-]+)", path.read_text(encoding="utf-8")))
        return consumed

    def test_unconsumed_token_count_does_not_grow(self) -> None:
        css = _read_css()
        declared = set(re.findall(r"^\s*(--[a-z0-9-]+)\s*:", _strip_comments(css), re.M))
        consumed = self._consumed_tokens(css)
        dead = sorted(declared - consumed - self._VOCABULARY_COMPLETENESS)
        self.assertLessEqual(
            len(dead),
            self._MAX_DEAD_TOKENS,
            f"unconsumed tokens grew to {len(dead)} (cap {self._MAX_DEAD_TOKENS}): {dead}",
        )

    def test_ratchet_is_not_slack(self) -> None:
        """A cap far above the real count would silently stop ratcheting."""
        css = _read_css()
        declared = set(re.findall(r"^\s*(--[a-z0-9-]+)\s*:", _strip_comments(css), re.M))
        consumed = self._consumed_tokens(css)
        dead = declared - consumed - self._VOCABULARY_COMPLETENESS
        self.assertEqual(
            len(dead),
            self._MAX_DEAD_TOKENS,
            "ratchet drifted from the measured count — lower _MAX_DEAD_TOKENS to "
            f"{len(dead)} in the same commit that removed the token(s)",
        )


# ─────────────────────────────────────────────────────────────────────────────
# W3 — responsive contract (§4, M6/M7)
# ─────────────────────────────────────────────────────────────────────────────

WEB_ROUTES = WEB_SRC / "routes"
WEB_UI = WEB_SRC / "ui"

#: Routes this milestone may not edit (supervisor deny-path). They handle
#: `isPending` and still render a bare `<p aria-busy>`; the skeleton-coverage
#: seal therefore excludes them EXPLICITLY rather than by a pattern that would
#: also silently excuse a future route.
_SKELETON_DENY_PATH = frozenset(
    {
        "chambers/ChambersWorkbench.tsx",
        "chambers/ChamberProgress.tsx",
        "chambers/ChamberAdminPanel.tsx",
        "chambers/MeasurementStarter.tsx",
    }
)

#: Primitives that constitute a real loading placeholder. A route that handles
#: a loading flag must render at least one of them.
_SKELETON_PRIMITIVES = ("DataTableSkeleton", "BlockSkeleton", "RefetchRegion", "RunProgress")

_MEDIA_MAXWIDTH = re.compile(r"@media[^{]*\(\s*max-width\s*:\s*(\d+(?:\.\d+)?)px")
_BP_TOKEN = re.compile(r"(--bp-[a-z]+)\s*:\s*(\d+(?:\.\d+)?)px")


def _route_sources() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in sorted(WEB_ROUTES.rglob("*.tsx")):
        out.append((path.relative_to(WEB_ROUTES).as_posix(), path.read_text(encoding="utf-8")))
    return out


class TestBreakpointScaleIsFullyConsumed(unittest.TestCase):
    """M6.2 — a declared breakpoint must actually change something.

    ``--bp-md`` sat in the stylesheet for two milestones with zero consumers:
    it read as "the tablet band is handled" while the tablet band did not
    exist. The architecture suite already seals the other direction (no media
    literal without a token); this is the missing half, and the pair together
    make the breakpoint scale and the implemented bands the same fact.
    """

    def test_every_declared_breakpoint_has_a_media_consumer(self) -> None:
        css = _read_css()
        thresholds = {round(float(v) + 0.02, 2) for v in _MEDIA_MAXWIDTH.findall(css)}
        declared = _BP_TOKEN.findall(css)
        self.assertTrue(declared, "no --bp-* tokens declared")
        orphans = [
            f"{name} ({value}px)"
            for name, value in declared
            if round(float(value), 2) not in thresholds
        ]
        self.assertEqual(
            orphans,
            [],
            "breakpoint token(s) declared with no `@media` consumer — either "
            f"wire the band or delete the token: {orphans}",
        )

    def test_the_scale_is_three_rungs(self) -> None:
        """A single rung would satisfy the seal above vacuously."""
        names = {name for name, _ in _BP_TOKEN.findall(_read_css())}
        self.assertEqual(names, {"--bp-sm", "--bp-md", "--bp-lg"}, sorted(names))


class TestFourBandResponsiveScale(unittest.TestCase):
    """M7 — the contract names four bands; the stylesheet must implement four.

    Before W3 the file had exactly one working width query, so "responsive"
    meant "the rail folds once at 1024". Phone and compact behaviour did not
    exist to be reviewed. Asserting the three boundaries (which, with the
    implicit desktop default, make four bands) keeps a future refactor from
    quietly collapsing them back into one.
    """

    _EXPECTED = {639.98, 767.98, 1023.98}

    def test_all_three_boundaries_are_implemented(self) -> None:
        found = {round(float(v), 2) for v in _MEDIA_MAXWIDTH.findall(_read_css())}
        missing = sorted(self._EXPECTED - found)
        self.assertEqual(missing, [], f"missing responsive band boundary/-ies: {missing}")


class TestTouchTargetTokenSSOT(unittest.TestCase):
    """M7.3 — hit-area sizing is a token, mobile-only, and never a min-height.

    Three separate mistakes are sealed here because they are the three the
    reference implementation actually made:
      (a) px literals scattered across components, so raising the floor from
          44 to 48 meant a find-and-replace instead of one edit;
      (b) a blanket ``min-height`` that deformed square controls;
      (c) a two-directional rule, which let a desktop-density regression ride
          in on a mobile change.
    """

    _TOKENS = ("--touch-target-min", "--touch-target-glove")

    def test_both_rungs_are_declared_and_consumed(self) -> None:
        css = _read_css()
        for token in self._TOKENS:
            self.assertRegex(css, rf"{token}\s*:\s*\d+px", f"{token} not declared")
            self.assertIn(f"var({token})", css, f"{token} declared but never consumed")

    def test_touch_target_rules_carry_no_px_literal(self) -> None:
        offenders = [
            f"{_normalize(prelude)} {{ {_normalize(body)} }}"
            for prelude, body in _top_level_blocks(_read_css())
            if "touch-target" in prelude and re.search(r"\d+px", _strip_comments(body))
        ]
        # Nested rules inside the media blocks are checked below.
        for prelude, body in _top_level_blocks(_read_css()):
            if not prelude.lstrip().startswith("@media"):
                continue
            for inner_prelude, inner_body in _top_level_blocks(body):
                if "touch-target" in inner_prelude and re.search(
                    r"\d+px", _strip_comments(inner_body)
                ):
                    offenders.append(f"{_normalize(inner_prelude)} {{ {_normalize(inner_body)} }}")
        self.assertEqual(
            offenders,
            [],
            "touch-target rule(s) restate a px literal — delegate to "
            f"--touch-target-* (the sizing SSOT): {offenders}",
        )

    def test_expansion_is_an_overlay_not_a_forced_control_height(self) -> None:
        offenders = []
        for prelude, body in _top_level_blocks(_read_css()):
            blocks = [(prelude, body)]
            if prelude.lstrip().startswith("@media"):
                blocks = _top_level_blocks(body)
            for inner_prelude, inner_body in blocks:
                if "touch-target" not in inner_prelude:
                    continue
                if "::after" in inner_prelude:
                    continue
                if re.search(r"min-height\s*:", _strip_comments(inner_body)):
                    offenders.append(_normalize(inner_prelude))
        self.assertEqual(
            offenders,
            [],
            "touch-target rule forces a control height — expand the hit area "
            f"with the transparent ::after overlay instead: {offenders}",
        )

    def test_expansion_is_declared_mobile_only(self) -> None:
        """No touch-target overlay rule outside a max-width band."""
        offenders = []
        for prelude, body in _top_level_blocks(_read_css()):
            head = _strip_comments(prelude).strip()
            if head.startswith("@media"):
                continue
            if "touch-target" in head and "::after" in head:
                offenders.append(_normalize(prelude))
            del body
        self.assertEqual(
            offenders,
            [],
            "touch-target overlay declared outside a narrow-band `@media` — a "
            "two-directional rule is how desktop density regresses: "
            f"{offenders}",
        )


class TestDensityToggleSurvivesCompactViewports(unittest.TestCase):
    """M7.4 — the density switch is not hidden where density matters most.

    It used to be in the ``display: none`` list of the ``--bp-lg`` collapse
    block alongside the theme/locale labels. Those are labels; this is the
    control itself, and a tester on a tablet at the chamber is exactly the
    person who needs to compact the table.
    """

    def test_density_toggle_is_not_display_none_in_any_band(self) -> None:
        offenders = []
        for prelude, body in _top_level_blocks(_read_css()):
            if not _strip_comments(prelude).strip().startswith("@media"):
                continue
            for inner_prelude, inner_body in _top_level_blocks(body):
                selectors = _selector_set(inner_prelude)
                if not any(s.strip() == ".density-toggle" for s in selectors):
                    continue
                if re.search(r"display\s*:\s*none", _strip_comments(inner_body)):
                    offenders.append(f"{_normalize(prelude)} → {_normalize(inner_prelude)}")
        self.assertEqual(
            offenders,
            [],
            f"`.density-toggle` hidden in a responsive band: {offenders}",
        )


class TestNoViewportBranchingInJs(unittest.TestCase):
    """M7.2a — the table/card swap is CSS, so no viewport state exists in JS.

    Reading ``window.innerWidth`` into React state re-renders on every resize
    frame and desynchronises against the stylesheet's own thresholds, giving
    two sources of truth for one boundary. The width queries stay in the
    stylesheet; ``matchMedia`` for user PREFERENCES (colour scheme, reduced
    motion) is untouched — those are not viewport branches.
    """

    _BANNED = (
        re.compile(r"window\.innerWidth"),
        re.compile(r"window\.innerHeight"),
        re.compile(r"addEventListener\(\s*['\"]resize['\"]"),
        re.compile(r"matchMedia\(\s*[`'\"]\(?\s*(?:max|min)-width"),
    )

    def test_no_viewport_branch_in_app_sources(self) -> None:
        offenders = []
        for path in sorted(WEB_SRC.rglob("*.ts")) + sorted(WEB_SRC.rglob("*.tsx")):
            text = _strip_ts_comments(path.read_text(encoding="utf-8"))
            for pattern in self._BANNED:
                if pattern.search(text):
                    offenders.append(f"{path.relative_to(WEB_SRC).as_posix()}: {pattern.pattern}")
        self.assertEqual(
            offenders,
            [],
            "viewport branching found in JS — express the breakpoint in "
            f"global.css and let both views live in the markup: {offenders}",
        )


class TestResponsiveTableFoldIsLossless(unittest.TestCase):
    """M7.2 — a folded column reappears; it is never merely hidden.

    The compact band hides ``[data-priority='detail']`` cells. That is only
    acceptable because the same values are re-rendered in the per-row overflow
    line, and because the phone card body carries EVERY column. If a future
    edit drops either projection, hiding becomes deletion.
    """

    def test_compact_band_hides_detail_only_alongside_the_overflow_row(self) -> None:
        css = _read_css()
        self.assertIn(
            "[data-priority='detail']",
            css,
            "no priority fold rule — M7.2 column priority is not implemented",
        )
        self.assertRegex(
            css,
            r"\.data-table__overflow-row\s*\{\s*display:\s*table-row",
            "detail columns are hidden with no overflow line to receive them",
        )

    def test_the_card_projection_renders_every_column(self) -> None:
        source = (WEB_UI / "DataTable.tsx").read_text(encoding="utf-8")
        self.assertIn(
            "const fieldColumns = columns.filter((column) => column !== titleColumn);",
            source,
            "the card body must be derived from ALL columns minus the title, "
            "so no priority can be dropped from the phone projection",
        )

    def test_the_slot_form_stays_byte_identical(self) -> None:
        """A caller that did not opt in must not gain the responsive modifier."""
        source = (WEB_UI / "DataTable.tsx").read_text(encoding="utf-8")
        self.assertIn(
            "if (descriptor !== null) classNames.push('data-table--responsive');",
            source,
            "the responsive modifier must be gated on the descriptor form — "
            "the 17 legacy call sites (3 of them unmodifiable) rely on it",
        )


class TestDescriptorMigrationTargets(unittest.TestCase):
    """M7.2 — the routes the contract names actually reached the descriptor.

    A responsive fold that only exists in the primitive is not a responsive
    table; the seal is on the CALL SITES. ``projects`` is in the list because
    its 1:N coverage matrix was the one call site the flat descriptor could not
    express — it is carried by the additive ``expansion`` descriptor, so
    "cannot be migrated" is no longer an available answer for a 1:N table.
    """

    #: Supervisor-confirmed scope. `chambers/*` is a deny path and must stay on
    #: the slot form, which is exactly what makes the API additive.
    _TARGETS = (
        "sessions.tsx",
        "jobs.tsx",
        "projects.tsx",
        "reports.tsx",
        "membership.tsx",
    )

    def test_each_target_route_passes_a_column_descriptor(self) -> None:
        offenders = []
        for rel in self._TARGETS:
            text = (WEB_ROUTES / rel).read_text(encoding="utf-8")
            if "columns={" not in text:
                offenders.append(rel)
        self.assertEqual(
            offenders,
            [],
            "route(s) still render DataTable through the slot API — the "
            f"§M7.2 fold never reaches them: {offenders}",
        )

    def test_the_chambers_deny_path_stays_on_the_slot_form(self) -> None:
        """Proof the descriptor API is additive, not a migration everyone owes."""
        chambers = WEB_ROUTES / "chambers"
        consumers = [
            path
            for path in sorted(chambers.rglob("*.tsx"))
            if "<DataTable" in path.read_text(encoding="utf-8")
        ]
        self.assertNotEqual(consumers, [], "no chambers DataTable consumer found — stale seal")
        offenders = [
            path.name
            for path in consumers
            if "columns={" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(
            offenders,
            [],
            f"deny-path route was migrated — the API stopped being additive: {offenders}",
        )


class TestExpansionPanelIsLossless(unittest.TestCase):
    """M7.2 — the 1:N panel survives the band it would otherwise vanish in.

    Below ``--bp-sm`` the table (and with it the expansion row) is
    ``display: none``. If the card projection did not re-offer the panel, the
    phone band would silently lose every condition row — the exact information
    loss the contract bans, dressed up as a layout rule.
    """

    def test_the_card_reoffers_the_expansion_panel(self) -> None:
        source = (WEB_UI / "DataTable.tsx").read_text(encoding="utf-8")
        self.assertIn(
            "expansion.render(row, 'row')",
            source,
            "no expansion row on the table surface",
        )
        self.assertIn(
            "expansion.render(row, 'card')",
            source,
            "the expansion panel is not re-offered on the card — the phone "
            "band loses it entirely when the table is hidden",
        )
        self.assertIn(
            "<details",
            source,
            "the card disclosure must be the platform's `<details>`, so the "
            "panel opens with no JS and no viewport branch",
        )

    def test_the_expansion_row_is_never_a_keyboard_stop(self) -> None:
        source = (WEB_UI / "DataTable.tsx").read_text(encoding="utf-8")
        self.assertIn(
            ":not(.data-table__expansion-row)",
            source,
            "the roving-tabindex walk must skip the non-interactive panel row",
        )


class TestResponsiveRouteCoverage(unittest.TestCase):
    """M7.6 — the width sweep covers every operator route, by construction.

    The e2e route list started as a hand-copied subset and had already drifted
    (``/my-projects`` and ``/control`` were registered but never swept). Pinning
    it to the router registration makes an omission impossible to land quietly;
    an intentional exclusion has to be named here AND justified in the spec.
    """

    _APP = WEB_SRC / "app.tsx"
    _SPEC = ROOT / "apps" / "web" / "tests" / "e2e" / "responsive-layout.spec.ts"

    #: Registered paths that are deliberately NOT operator screens. Each one
    #: must also carry its reason next to the route list in the spec.
    _EXCLUDED = {
        "grid-poc": "dev-only POC, exempted from the responsive contract (M7.5)",
        "auth/callback": "redirect shim rendered outside the app shell",
    }

    def _registered_routes(self) -> set[str]:
        """등록 집합 — ``support.frontend_route_registry`` **파생**.

        ⚠️ 이 자리는 `re.findall(r"path:\\s*'([^']+)'", app.tsx)` 였고, 그것은 같은
        저장소에 **여섯 번째** 사본이었다. 형제 다섯을 모듈 그래프 파생으로 접은
        웨이브(`conformance-gate-proposition-axis`, 2026-08-21)가 이 하나를 빠뜨렸고,
        독립 적대 평가가 그 사실을 실행으로 지적했다 — 그 시점에 이 사본은 *유일하게*
        어떤 반례를 잡는 검사였고, 동시에 다른 모듈에서 ``...spread`` 로 들어온
        라우트에 대해 **같은 사각지대**를 갖고 있었다(교차 모듈 라우트를 더해도 답이
        19→19 로 움직이지 않는다).

        두 사실은 모순이 아니다: 사본은 형제가 놓친 것을 잡았고, 자기 몫의 결함은
        그대로 갖고 있었다. 정공은 둘 중 하나를 고르는 것이 아니라 **양쪽 다** 파생에
        올리는 것이다.
        """
        routes: set[str] = set()
        for entry in collect_route_entries(WEB_SRC):
            normalized = entry.address.lstrip("/")
            if normalized == "*" or normalized in self._EXCLUDED:
                continue
            routes.add(entry.address)
        return routes

    def _swept_routes(self) -> set[str]:
        text = self._SPEC.read_text(encoding="utf-8")
        block = re.search(r"const ROUTES = \[(.*?)\] as const;", text, re.S)
        self.assertIsNotNone(block, "ROUTES array not found in the responsive spec")
        assert block is not None  # narrowing for type checkers
        # Shared lexer, not an inline regex: a route path is a string literal
        # and `'//foo'` inside one is not a comment.
        body = strip_ts_comments(block.group(1))
        return set(re.findall(r"'([^']+)'", body))

    def test_every_registered_route_is_swept_at_every_width(self) -> None:
        registered = self._registered_routes()
        swept = self._swept_routes()
        self.assertEqual(
            sorted(registered - swept),
            [],
            "registered route(s) never checked for document overflow",
        )
        self.assertEqual(
            sorted(swept - registered),
            [],
            "the sweep names route(s) the router does not register",
        )

    def test_each_exclusion_is_justified_next_to_the_route_list(self) -> None:
        text = self._SPEC.read_text(encoding="utf-8")
        offenders = [name for name in sorted(self._EXCLUDED) if name not in text]
        self.assertEqual(
            offenders,
            [],
            "route(s) excluded from the sweep with no recorded reason in the "
            f"spec: {offenders}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# W4 — state design (§5, M8)
# ─────────────────────────────────────────────────────────────────────────────


class TestSkeletonCoverage(unittest.TestCase):
    """M8.1 — every route that can be loading renders a shaped placeholder.

    ``<p aria-busy="true">불러오는 중…</p>`` reserves one text line for content
    that arrives as a six-row table, so every load ended in a jump. Coverage
    was 2 routes out of 17 when W4 started.
    """

    #: A QUERY's loading flag. `someMutation.isPending` is excluded on purpose:
    #: a write in flight is expressed by disabling its own button, not by
    #: replacing the surrounding content with a placeholder.
    _LOADING_FLAG = re.compile(r"\b(?!\w*[Mm]utation)\w+\.is(?:Loading|Pending)\b")

    def test_every_loading_route_uses_a_skeleton_primitive(self) -> None:
        offenders = []
        for rel, text in _route_sources():
            if rel in _SKELETON_DENY_PATH:
                continue
            if not self._LOADING_FLAG.search(text):
                continue
            if not any(primitive in text for primitive in _SKELETON_PRIMITIVES):
                offenders.append(rel)
        self.assertEqual(
            offenders,
            [],
            "route(s) handle a loading flag with no skeleton primitive "
            f"({', '.join(_SKELETON_PRIMITIVES)}): {offenders}",
        )

    def test_no_bare_aria_busy_paragraph_outside_the_deny_path(self) -> None:
        offenders = [
            rel
            for rel, text in _route_sources()
            if rel not in _SKELETON_DENY_PATH and '<p aria-busy="true"' in text
        ]
        self.assertEqual(
            offenders,
            [],
            f"bare `<p aria-busy>` loading treatment survives in: {offenders}",
        )

    def test_the_deny_path_list_is_not_stale(self) -> None:
        """An exclusion that no longer names a real file is a silent hole."""
        missing = [rel for rel in sorted(_SKELETON_DENY_PATH) if not (WEB_ROUTES / rel).is_file()]
        self.assertEqual(missing, [], f"deny-path exclusion names a missing file: {missing}")


class TestSkeletonDimensionsAreDerived(unittest.TestCase):
    """M8.2 — the reserved shape comes from the arriving shape.

    A hardcoded ``columns={6}`` is correct until someone adds a column, and
    then it is a layout jump nobody notices in review. Deriving the count from
    the same descriptor/token list the table renders makes the two impossible
    to disagree.
    """

    _LITERAL_COLUMNS = re.compile(r"<DataTableSkeleton[^>]*columns=\{\d+\}", re.S)

    def test_no_hardcoded_skeleton_column_count(self) -> None:
        offenders = [
            rel for rel, text in _route_sources() if self._LITERAL_COLUMNS.search(text)
        ]
        self.assertEqual(
            offenders,
            [],
            "DataTableSkeleton column count hardcoded — derive it from the "
            f"column descriptor / header token list: {offenders}",
        )


class TestStateHeadingRungSeparation(unittest.TestCase):
    """M8.3 — a placeholder must not forge a section in the outline.

    ``EmptyState`` rendered ``<h2>``, the same rung ``SectionBand`` owns, so a
    section that happened to be empty announced two peer headings and screen
    reader users navigating by heading landed on "데이터가 없습니다" as if it
    were a new section of the page.
    """

    def test_the_two_rungs_are_declared_once_and_differ(self) -> None:
        source = (WEB_UI / "heading-levels.ts").read_text(encoding="utf-8")
        section = re.search(r"SECTION_HEADING_LEVEL\s*=\s*(\d+)", source)
        state = re.search(r"STATE_HEADING_LEVEL\s*=\s*(\d+)", source)
        self.assertIsNotNone(section, "SECTION_HEADING_LEVEL missing")
        self.assertIsNotNone(state, "STATE_HEADING_LEVEL missing")
        assert section is not None and state is not None  # narrows for mypy-style readers
        self.assertNotEqual(
            section.group(1),
            state.group(1),
            "the section rung and the state rung are the same level again",
        )

    def test_both_primitives_derive_their_rung_from_the_ssot(self) -> None:
        for name, token in (
            ("SectionBand.tsx", "SECTION_HEADING_LEVEL"),
            ("EmptyState.tsx", "STATE_HEADING_LEVEL"),
        ):
            source = _strip_ts_comments((WEB_UI / name).read_text(encoding="utf-8"))
            with self.subTest(primitive=name):
                self.assertIn(token, source, f"{name} does not consume {token}")
                self.assertNotRegex(
                    source,
                    r"<h[1-6][\s>]",
                    f"{name} still hardcodes a heading tag instead of deriving it",
                )


class TestErrorVariantContract(unittest.TestCase):
    """M8.6 — every FCC failure mode has a glyph, an explanation and a way out.

    The generic "다시 시도" button is the wrong answer to "the analyzer is not
    on the LAN". Each variant is bound in three places at once (TS contract,
    both message bundles, one CSS glyph rule); this seal is what stops a
    seventh mode from being added in one of them only.
    """

    def _variants(self) -> list[str]:
        source = (WEB_UI / "error-variants.ts").read_text(encoding="utf-8")
        body = source[source.index("ERROR_VARIANTS = [") : source.index("] as const;")]
        return re.findall(r"^\s*'([a-z-]+)',", body, re.M)

    def test_the_domain_failure_modes_are_all_declared(self) -> None:
        self.assertEqual(
            sorted(self._variants()),
            sorted(
                [
                    "antenna-gain-missing",
                    "dccf-missing",
                    "forbidden",
                    "instrument-offline",
                    "sample-not-selected",
                    "scpi-timeout",
                ]
            ),
        )

    def test_every_variant_binds_a_glyph_token_and_a_rule(self) -> None:
        css = _read_css()
        missing = []
        for variant in self._variants():
            token = f"--error-{variant}-icon"
            if not re.search(rf"{token}\s*:", css):
                missing.append(f"{token} not declared")
            if f"[data-variant='{variant}']" not in css:
                missing.append(f"no glyph rule for {variant}")
        self.assertEqual(missing, [], f"error variant glyph contract incomplete: {missing}")

    def test_every_variant_offers_a_recovery_label(self) -> None:
        source = (WEB_UI / "error-variants.ts").read_text(encoding="utf-8")
        missing = [
            variant
            for variant in self._variants()
            if f"'{variant}'" not in source and variant not in source
        ]
        self.assertEqual(missing, [], f"variant missing from the contract map: {missing}")
        self.assertEqual(
            source.count("recoveryToken: 'ui.errorState"),
            len(self._variants()),
            "a failure mode without a recovery label is a dead end — the "
            "contract bans leaving the operator with nowhere to go",
        )

    def test_the_primitive_still_delegates_wording_to_describe_api_error(self) -> None:
        """M8.5 — adding variants must not smuggle status-code branching back."""
        source = (WEB_UI / "ErrorState.tsx").read_text(encoding="utf-8")
        for banned in ("status === 4", "status === 5", "statusCode", "response.status"):
            self.assertNotIn(
                banned,
                source,
                "ErrorState must contain no status-code branching (FD-D); the "
                "sentence comes from describeApiError and the variant is a "
                "domain classification chosen by the route",
            )


class TestErrorStateDeadEndBanIsTypeLevel(unittest.TestCase):
    """M8.6 — ``variant`` alone must not COMPILE, not merely render oddly.

    The previous shape accepted ``<ErrorState variant="dccf-missing" />``: all
    three recovery slots were optional, so a route could classify a failure and
    still leave the operator with a glyph, a hint and nowhere to go. Only the
    primitive's runtime branch noticed, and it noticed by rendering nothing —
    the exact dead end §M8.6 bans, shipped silently.

    A discriminated union moves the ban to the type checker: picking a variant
    forces one of ``onRecover`` / ``action`` / ``noActionReason``. This seal
    watches the union's shape, because the TypeScript check that actually
    enforces it lives in another toolchain (``npm run typecheck``) and could be
    quietly regressed back into one all-optional interface.
    """

    def _source(self) -> str:
        return (WEB_UI / "ErrorState.tsx").read_text(encoding="utf-8")

    def test_props_are_a_union_not_one_all_optional_interface(self) -> None:
        source = self._source()
        self.assertNotIn(
            "export interface ErrorStateProps",
            source,
            "ErrorStateProps must be a discriminated union — a single interface "
            "cannot express 'variant requires a recovery' (§M8.6 dead-end ban)",
        )
        union = re.search(
            r"export type ErrorStateProps\s*=\s*((?:\s*\|\s*\w+)+);", source
        )
        self.assertIsNotNone(union, "ErrorStateProps must be an exported union type")
        assert union is not None  # narrowing for type checkers
        members = re.findall(r"\|\s*(\w+)", union.group(1))
        self.assertEqual(
            sorted(members),
            sorted(
                [
                    "ErrorStateCustomRecoveryProps",
                    "ErrorStateHandlerRecoveryProps",
                    "ErrorStateNoRecoveryProps",
                    "ErrorStatePlainProps",
                ]
            ),
            "the union must be exactly: no-variant, handler recovery, custom "
            "action, and explained-no-action",
        )

    def test_each_variant_branch_requires_exactly_one_way_out(self) -> None:
        source = self._source()
        expected = {
            "ErrorStateHandlerRecoveryProps": "onRecover",
            "ErrorStateCustomRecoveryProps": "action",
            "ErrorStateNoRecoveryProps": "noActionReason",
        }
        problems: list[str] = []
        for interface, required in expected.items():
            block = re.search(
                rf"interface {interface} extends ErrorStateCommonProps \{{(.*?)\n\}}",
                source,
                re.S,
            )
            if block is None:
                problems.append(f"{interface} missing")
                continue
            body = block.group(1)
            if not re.search(r"readonly variant:\s*ErrorVariant;", body):
                problems.append(f"{interface} must make variant REQUIRED")
            if not re.search(rf"readonly {required}:\s*\S", body):
                problems.append(f"{interface} must require {required}")
            for closed in set(expected.values()) - {required}:
                if not re.search(rf"readonly {closed}\?:\s*undefined;", body):
                    problems.append(f"{interface} must close {closed} off")
        self.assertEqual(problems, [], f"dead-end ban union drifted: {problems}")

    def test_the_plain_branch_closes_every_recovery_slot(self) -> None:
        """No variant → no recovery: the button's label lives in the contract."""
        block = re.search(
            r"interface ErrorStatePlainProps extends ErrorStateCommonProps \{(.*?)\n\}",
            self._source(),
            re.S,
        )
        self.assertIsNotNone(block, "ErrorStatePlainProps must exist")
        assert block is not None  # narrowing for type checkers
        for slot in ("variant", "onRecover", "action", "noActionReason"):
            self.assertRegex(
                block.group(1),
                rf"readonly {slot}\?:\s*undefined;",
                f"the no-variant branch must close {slot} off",
            )

    def test_the_custom_action_slot_rejects_render_nothing_nodes(self) -> None:
        """``NonNullable<ReactNode>`` is not a dead-end ban — it only bans null.

        ``ReactNode`` minus ``null``/``undefined`` still admits ``false``,
        ``''``, ``0`` and ``[]``. Every one of them type-checks and renders
        NOTHING, so ``action={isAdmin && <Btn/>}`` compiles and then strands
        exactly the operator who lacks the permission — the §M8.6 dead end,
        reintroduced through the slot that was supposed to close it. Only a
        node type that must render (an element or a fragment) makes the ban
        real, so this seal pins the annotation itself.
        """
        block = re.search(
            r"interface ErrorStateCustomRecoveryProps extends ErrorStateCommonProps \{(.*?)\n\}",
            self._source(),
            re.S,
        )
        self.assertIsNotNone(block, "ErrorStateCustomRecoveryProps must exist")
        assert block is not None  # narrowing for type checkers
        body = block.group(1)
        self.assertRegex(
            body,
            r"readonly action:\s*ReactElement;",
            "the custom recovery slot must be typed ReactElement — ReactNode "
            "(even NonNullable) admits false/''/0/[], which render nothing",
        )
        self.assertNotRegex(
            body,
            r"readonly action:[^\n]*ReactNode",
            "ReactNode in the action slot re-opens the render-nothing dead end",
        )

    def test_render_nothing_actions_are_pinned_as_negative_compile_cases(self) -> None:
        """The type is the enforcement; this checks the enforcement is exercised."""
        spec = (
            ROOT / "apps" / "web" / "tests" / "ui" / "error-state.test.tsx"
        ).read_text(encoding="utf-8")
        for literal in ("action={false}", "action={''}", "action={[]}"):
            self.assertIn(
                literal,
                spec,
                f"the vitest suite must pin {literal} as a @ts-expect-error "
                "case, or a widened action type would regress unnoticed",
            )
            self.assertRegex(
                spec,
                rf"@ts-expect-error[^\n]*\n(?:[^\n]*\n){{0,4}}?[^\n]*{re.escape(literal)}",
                f"{literal} must sit under @ts-expect-error (it INVERTS: it "
                "fails typecheck once the value starts compiling again)",
            )

    def test_a_negative_compile_case_is_pinned_in_the_vitest_suite(self) -> None:
        """``@ts-expect-error`` inverts: it FAILS typecheck if the code compiles."""
        spec = (
            ROOT / "apps" / "web" / "tests" / "ui" / "error-state.test.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "@ts-expect-error",
            spec,
            "the dead-end ban needs a negative compile case; without it a "
            "regression to all-optional props would go unnoticed by npm run typecheck",
        )
        self.assertRegex(
            spec,
            r"@ts-expect-error[^\n]*\n(?:[^\n]*\n){0,4}?[^\n]*<ErrorState[^\n]*variant=",
            "the ts-expect-error case must be a bare <ErrorState variant=...>",
        )


class TestNativeControlFoundation(unittest.TestCase):
    """UI hardening — ordinary form controls share one semantic baseline."""

    def test_control_tokens_are_declared_once_and_consume_semantic_tokens(self) -> None:
        css = _read_css()
        expected = {
            "--control-height": "var(--row-height)",
            "--control-pad-inline": "var(--cell-pad-x)",
            "--control-bg": "var(--surface-bg)",
            "--control-bg-hover": "var(--surface-bg-alt)",
            "--control-bg-active": "var(--surface-border)",
            "--control-bg-readonly": "var(--surface-bg-alt)",
            "--control-fg-disabled": "var(--fg-disabled)",
            "--control-fg": "var(--fg-primary)",
            "--control-focus-ring": "var(--accent)",
        }
        for token, value in expected.items():
            matches = re.findall(rf"{re.escape(token)}:\s*([^;]+);", css)
            self.assertEqual(matches, [value], f"{token} must be a one-source semantic alias")
        self.assertEqual(
            re.findall(r"--control-border:\s*([^;]+);", css),
            ["var(--p-light-control-border)", "var(--p-dark-control-border)", "var(--p-dark-control-border)"],
            "control border must map once for light and through the single dark primitive in both theme channels",
        )

    def test_focus_visible_contract_covers_all_interactive_control_families(self) -> None:
        css = _normalize(_read_css())
        self.assertIn(
            ":where(a, button, input, select, textarea, summary, [tabindex]):focus-visible",
            css,
        )
        self.assertIn("outline: 2px solid var(--control-focus-ring)", css)
        self.assertIn("outline-offset: 2px", css)

    def test_blanket_native_appearance_reset_is_forbidden(self) -> None:
        css = _strip_comments(_read_css())
        self.assertNotRegex(
            css,
            r"(?s):where\([^)]*(checkbox|radio|range|file|date|color)[^)]*\)\s*\{[^}]*appearance\s*:\s*none",
            "special native widgets must retain platform semantics and rendering",
        )
        self.assertNotRegex(css, r"(?:-webkit-|-moz-)appearance\s*:")

    def test_state_and_density_contract_is_token_driven(self) -> None:
        css = _read_css()
        for state in (
            ":hover:not(:disabled)",
            "[aria-invalid='true']",
            ":user-invalid",
            ":disabled",
            ":read-only",
            ":active:not(",
            "::placeholder",
        ):
            self.assertIn(state, css)
        self.assertIn("--control-height: var(--row-height);", css)
        self.assertIn(":root[data-density='compact']", css)


class TestStylesheetParsesAsCss(unittest.TestCase):
    """Every other seal in this module reads the stylesheet as TEXT.

    That is a real blind spot, and W3 walked into it: a token comment was
    closed early and its remaining prose spilled into the declaration block,
    with a stray ``*/`` after it. A regex still "found" ``--bp-sm: 640px``, so
    the whole suite stayed green — while Chromium discarded the declaration and
    the phone band silently did nothing. Only the browser noticed.

    These checks are cheap structural ones (comment nesting, brace balance,
    declaration termination) that would have failed loudly in the same commit —
    the stray `*/` alone is caught by the first of them. They are not a CSS
    parser and do not try to be; a full parse would need a dependency, and the
    class of damage that makes every OTHER seal in this file vacuous is
    delimiter damage specifically.
    """

    def test_comments_are_balanced(self) -> None:
        css = _read_css()
        residue = _strip_comments(css)
        self.assertEqual(
            (residue.count("/*"), residue.count("*/")),
            (0, 0),
            "unbalanced CSS comment — an early `*/` spills prose into the "
            "declaration block and the browser drops everything after it",
        )

    def test_braces_are_balanced(self) -> None:
        residue = _strip_comments(_read_css())
        self.assertEqual(
            residue.count("{") - residue.count("}"),
            0,
            "unbalanced braces in global.css",
        )

    def test_every_custom_property_declaration_is_terminated(self) -> None:
        """A `--token: value` line must end in `;` (or close its block)."""
        offenders = []
        for line_no, line in enumerate(_strip_comments(_read_css()).splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith("--") or ":" not in stripped:
                continue
            # A value may legitimately wrap across lines (shorthand lists,
            # grid templates) — such a line ends in a separator, not a `;`.
            if not stripped.endswith((";", "}", ",")):
                offenders.append(f"{line_no}: {stripped[:60]}")
        self.assertEqual(offenders, [], f"unterminated custom property: {offenders}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
