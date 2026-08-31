"""Frontend query-key / cache strategy SSOT seal (web-query-key-cache-ssot,
Increment 1, 2026-06-13).

Sprint slug ``fe-query-key-cache-ssot``. Before this increment ``apps/web``
hand-rolled every TanStack Query key as an inline array at the call site, and
each ``query`` definition re-assembled the same key by hand for its
``invalidateQueries`` counterpart (e.g. ``['project-claims', projectId,
techQuery]`` appeared once for the query and again for the invalidation). A
key shape that changed on one side only silently broke invalidation (a stale
lock overlay never refreshed). Refetch cadence was likewise a scattered magic
number (``control.tsx`` polled with a bare ``refetchInterval: 2000``).

Increment 1 routes every key through the factory SSOT
``apps/web/src/api/query-config.ts`` (``queryKeys`` + ``CACHE_TIMES`` +
``REFETCH_STRATEGIES``). This Python invariant seals that from the backend-only
CI lane so a refactor that re-introduces an inline key array, an inline refetch
magic number, or drops a factory domain fails CI even when ``npm run lint`` is
skipped. Companion skill: ``/verify-frontend-query-key-ssot``.

The behaviour-preservation oracle (keys are BYTE-IDENTICAL to the old inline
arrays) lives in the vitest ``apps/web/tests/query-config.test.ts``; this file
seals the *structural* SSOT (no inline keys / no magic numbers / domain
coverage).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from support.parity import strip_ts_comments



# ⚠️ 2026-08-31 에 이 모듈들은 이사했다. 경로를 적으면 레포마다 다른 문자열이
# 필요하지만 임포트 이름은 양쪽에서 같다 — 모듈에게 자기 위치를 묻는다.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from _moved_module_source import moved_module_source  # noqa: E402
PROJECT_ROOT = Path(__file__).parent.parent
WEB_ROOT = PROJECT_ROOT / "apps" / "web"
ROUTES_DIR = WEB_ROOT / "src" / "routes"
QUERY_CONFIG = WEB_ROOT / "src" / "api" / "query-config.ts"
QUERY_CLIENT = WEB_ROOT / "src" / "shared" / "query-client.ts"

# Ratchet-down allowlist for routes permitted to keep an inline query key or an
# inline refetch magic number. MUST stay empty — any entry is carried debt that
# a follow-up should remove. (Adding an entry is the escape hatch the seal makes
# *explicit* rather than silent.)
ALLOWED_INLINE_KEY_ROUTES: frozenset[str] = frozenset()

# Ratchet-down allowlist for routes that still declare a LOCAL named cadence
# constant instead of reading `REFETCH_STRATEGIES`. This is the named-constant
# bypass of the inline-magic-number rule: `const REQUEST_POLL_INTERVAL_MS =
# 2_000` reads like SSOT discipline but is a second copy of the CRITICAL
# cadence, free to drift from `query-config.ts::CRITICAL_REFETCH_INTERVAL_MS`.
#
# fe-data-layer-robustness M6 (2026-07-19) recorded ONE entry here as debt owned
# by W2 (`REQUEST_POLL_INTERVAL_MS = 2_000` in `reports.tsx`, a second copy of
# REFETCH_STRATEGIES.CRITICAL.refetchInterval).
#
# W2-A (2026-07-28) paid it: the report-request lifecycle poll now reads the
# CRITICAL cadence from the SSOT and layers the error backoff on top of it
# (`errorBackoffPollInterval`), so the route owns no interval literal. Back to
# the intended empty state — an entry is carried debt, never a permanent carve-out.
ALLOWED_NAMED_CADENCE_CONSTANT_ROUTES: frozenset[str] = frozenset()

# The full set of key prefixes the routes use (first element of every key
# array). The factory MUST define every one — an orphan prefix (used by a route
# but absent from the factory) means a route still hand-builds a key.
EXPECTED_KEY_PREFIXES: frozenset[str] = frozenset(
    {
        "session",
        "project-coverage",
        "project-claims",
        "project-sync-status",
        "project-memberships",
        # Phase 6 시간가중 진행률 rollup — progress.tsx + fields.tsx 진행률 배지.
        "project-progress",
        "report-automation",
        "report-request",
        "report-outputs",
        "session-artifacts",
        "session-attempts",
        "headless-jobs",
        "provider-ui-descriptor",
        # 멀티챔버 P6 — chamber availability + measurement progress (central proxy).
        "chambers",
        # 시험 항목표 P6 — test-plan draft list + draft detail (headless API).
        "test-plan-drafts",
        "test-plan-draft",
        # 참조 카탈로그 (2026-08-08) — provider 스코프 목록 + 리비전 상세.
        # 프로젝트가 아니라 provider 스코프인 이유는 리비전 버킷의 scope 가
        # 케이블 관련 패밀리에서 **방**이고, 한 프로젝트가 두 방에 걸치기 때문이다.
        "reference-revisions",
        "reference-revision",
    }
)


def _strip_comments(source: str) -> str:
    """Remove ``/* … */`` block comments and ``//`` line comments so a pattern
    scan does not trip on prose that mentions ``queryKey``.

    Delegates to the shared lexer. This function is the reason the migration
    could not stop at six files: it was a **fourth** independent implementation
    (a per-line quote-balance heuristic), and five of the sibling seals'
    docstrings said they *"mirror ``test_frontend_query_key_ssot.py``"* — so
    leaving it in place would have left the ancestor of the copies behind, still
    named as the thing they mirror. The quote-balance heuristic also cannot see
    a multi-line template literal, since it judges one line at a time.

    Sealed by ``tests/test_ts_comment_stripper_ssot.py``.
    """
    return strip_ts_comments(source)


def _route_files() -> list[Path]:
    """Every route component, **recursively**.

    fe-data-layer-robustness M6 (D6, 2026-07-19) — this used a non-recursive
    ``ROUTES_DIR.glob("*.tsx")``, so the whole ``routes/{test-plans,chambers,
    inventory,…}/**`` subtree (the largest and most query-dense part of the
    route layer) was never scanned. An inline ``queryKey: [...]`` array or an
    inline ``refetchInterval: 2000`` in a nested route passed the seal silently.
    """
    return sorted(ROUTES_DIR.rglob("*.tsx"))


def _route_id(path: Path) -> str:
    """Stable identifier for allowlist entries — repo-relative POSIX path under
    ``routes/`` (nested stems are not unique, so ``path.stem`` cannot key an
    allowlist once the scan is recursive)."""
    return path.relative_to(ROUTES_DIR).as_posix()


class TestQueryConfigFactoryPresent(unittest.TestCase):
    """The factory module exists and exports the three SSOTs."""

    def test_query_config_module_exists(self) -> None:
        self.assertTrue(
            QUERY_CONFIG.is_file(),
            f"query-config SSOT missing: {QUERY_CONFIG}",
        )

    def test_exports_the_three_ssots(self) -> None:
        text = QUERY_CONFIG.read_text(encoding="utf-8")
        for export in ("queryKeys", "CACHE_TIMES", "REFETCH_STRATEGIES"):
            self.assertRegex(
                text,
                rf"export const {export}\b",
                f"query-config must export `{export}`",
            )

    def test_cache_times_and_strategies_are_as_const(self) -> None:
        text = QUERY_CONFIG.read_text(encoding="utf-8")
        # Each of the three top-level exports closes with `} as const;` so the
        # literal key prefixes / durations survive in the type.
        self.assertGreaterEqual(
            text.count("} as const;"),
            3,
            "queryKeys / CACHE_TIMES / REFETCH_STRATEGIES must each be `as const`",
        )

    def test_factory_covers_every_route_key_prefix(self) -> None:
        text = QUERY_CONFIG.read_text(encoding="utf-8")
        missing = sorted(
            prefix for prefix in EXPECTED_KEY_PREFIXES if f"'{prefix}'" not in text
        )
        self.assertEqual(
            missing,
            [],
            f"factory missing key prefix(es) used by routes: {missing}",
        )


class TestNoInlineQueryKeyInRoutes(unittest.TestCase):
    """Routes obtain keys from the factory — no inline `queryKey: [ … ]`."""

    INLINE_KEY = re.compile(r"queryKey\s*:\s*\[")
    QUERY_KEY_VALUE = re.compile(r"queryKey\s*:\s*([A-Za-z_][A-Za-z0-9_$.]*)")
    FACTORY_LOCAL = re.compile(
        r"\bconst\s+([A-Za-z_][A-Za-z0-9_]*Key)\s*=\s*queryKeys\."
    )

    def test_no_inline_query_key_array(self) -> None:
        offenders: list[str] = []
        for path in _route_files():
            if path.stem in ALLOWED_INLINE_KEY_ROUTES:
                continue
            code = _strip_comments(path.read_text(encoding="utf-8"))
            for m in self.INLINE_KEY.finditer(code):
                line_no = code[: m.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line_no}")
        self.assertEqual(
            offenders,
            [],
            "inline `queryKey: [ … ]` literal(s) — route key SSOT broken; "
            f"use queryKeys factory: {offenders}",
        )

    def test_every_query_key_uses_the_factory(self) -> None:
        offenders: list[str] = []
        for path in _route_files():
            code = _strip_comments(path.read_text(encoding="utf-8"))
            factory_locals = set(self.FACTORY_LOCAL.findall(code))
            for m in self.QUERY_KEY_VALUE.finditer(code):
                value = m.group(1)
                if (
                    value.startswith("queryKeys.")
                    or value in factory_locals
                    or value == "query.queryKey"
                ):
                    # TanStack Query supplies `query.queryKey` for exact
                    # eviction of an already-cached page. It is not a second
                    # key construction and must not be mistaken for an
                    # inline factory bypass.
                    continue
                line_no = code[: m.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line_no} ({value})")
        self.assertEqual(
            offenders,
            [],
            "some `queryKey:` sites bypass the queryKeys factory or a local "
            f"`const *Key = queryKeys.*` alias: {offenders}",
        )


class TestNoInlineRefetchMagicNumberInRoutes(unittest.TestCase):
    """Refetch cadence comes from REFETCH_STRATEGIES — no inline magic number."""

    INLINE_REFETCH = re.compile(r"refetchInterval\s*:\s*\d")
    INLINE_STALE = re.compile(r"staleTime\s*:\s*\d")
    INLINE_GC = re.compile(r"gcTime\s*:\s*\d")

    def test_no_inline_refetch_or_cache_magic_number(self) -> None:
        offenders: list[str] = []
        for path in _route_files():
            if path.stem in ALLOWED_INLINE_KEY_ROUTES:
                continue
            code = _strip_comments(path.read_text(encoding="utf-8"))
            for label, pattern in (
                ("refetchInterval", self.INLINE_REFETCH),
                ("staleTime", self.INLINE_STALE),
                ("gcTime", self.INLINE_GC),
            ):
                for m in pattern.finditer(code):
                    line_no = code[: m.start()].count("\n") + 1
                    offenders.append(f"{path.name}:{line_no} ({label})")
        self.assertEqual(
            offenders,
            [],
            "inline refetch/cache magic number(s) — delegate to "
            f"REFETCH_STRATEGIES: {offenders}",
        )


class TestNoNamedCadenceConstantBypassInRoutes(unittest.TestCase):
    """A local named constant must not become a second cadence SSOT.

    fe-data-layer-robustness M6 (D6) — ``TestNoInlineRefetchMagicNumberInRoutes``
    only catches ``refetchInterval: 2000``. Hoisting the literal into a local
    ``const REQUEST_POLL_INTERVAL_MS = 2_000`` and returning it from the
    ``refetchInterval`` closure passes that regex while re-introducing exactly
    the drift the SSOT exists to prevent (two copies of the CRITICAL cadence).
    Both shapes of the bypass are sealed here:

    1. a local ``const <CADENCE-NAME> = <number>`` declaration, and
    2. ``refetchInterval: <identifier>`` that is not rooted at
       ``REFETCH_STRATEGIES``.
    """

    #: Local `const NAME = <numeric literal>` whose NAME reads as a cache/poll
    #: cadence. Matching on the *name* (not the use site) catches the constant
    #: regardless of whether it is used inline or returned from a closure.
    LOCAL_CADENCE_CONST = re.compile(
        r"\bconst\s+([A-Z][A-Z0-9_]*"
        r"(?:REFETCH|POLL|INTERVAL|STALE_TIME|GC_TIME|CACHE_TIME)"
        r"[A-Z0-9_]*)\s*(?::[^=]+)?=\s*[0-9]"
    )
    #: `refetchInterval: someIdentifier` — must be rooted at the strategy SSOT.
    REFETCH_IDENTIFIER = re.compile(
        r"refetchInterval\s*:\s*([A-Za-z_][A-Za-z0-9_$.]*)"
    )

    def test_no_local_named_cadence_constant(self) -> None:
        offenders: list[str] = []
        for path in _route_files():
            if _route_id(path) in ALLOWED_NAMED_CADENCE_CONSTANT_ROUTES:
                continue
            code = _strip_comments(path.read_text(encoding="utf-8"))
            for m in self.LOCAL_CADENCE_CONST.finditer(code):
                line_no = code[: m.start()].count("\n") + 1
                offenders.append(f"{_route_id(path)}:{line_no} ({m.group(1)})")
        self.assertEqual(
            offenders,
            [],
            "route declares a local named cadence constant — a second copy of "
            "the poll/cache policy, free to drift from query-config.ts. Read "
            f"REFETCH_STRATEGIES instead: {offenders}",
        )

    def test_refetch_interval_identifier_is_strategy_rooted(self) -> None:
        offenders: list[str] = []
        for path in _route_files():
            code = _strip_comments(path.read_text(encoding="utf-8"))
            for m in self.REFETCH_IDENTIFIER.finditer(code):
                value = m.group(1)
                # `(query) => …` closures and `false` are not identifiers we
                # can resolve here; the closure body is covered by the
                # named-constant rule above.
                if value in {"false", "true", "undefined"}:
                    continue
                if value.startswith("REFETCH_STRATEGIES."):
                    continue
                line_no = code[: m.start()].count("\n") + 1
                offenders.append(f"{_route_id(path)}:{line_no} ({value})")
        self.assertEqual(
            offenders,
            [],
            "`refetchInterval:` bound to an identifier that is not rooted at "
            f"REFETCH_STRATEGIES: {offenders}",
        )


class TestNamedCadenceAllowlistRatchet(unittest.TestCase):
    """The named-cadence allowlist is carried debt — it must only shrink."""

    #: 2026-07-19 (M6) baseline was 1 (`reports.tsx`); W2-A paid that debt, so
    #: the ceiling ratchets to 0 — no route may declare a local cadence constant.
    CEILING = 0

    def test_allowlist_does_not_grow(self) -> None:
        self.assertLessEqual(
            len(ALLOWED_NAMED_CADENCE_CONSTANT_ROUTES),
            self.CEILING,
            "ALLOWED_NAMED_CADENCE_CONSTANT_ROUTES grew — a NEW route added a "
            "local cadence constant. Ratchet down, never up: "
            f"{sorted(ALLOWED_NAMED_CADENCE_CONSTANT_ROUTES)}",
        )

    def test_allowlist_entries_still_exist_and_still_offend(self) -> None:
        """A stale allowlist entry (route deleted or debt paid) must be removed."""
        stale: list[str] = []
        for entry in ALLOWED_NAMED_CADENCE_CONSTANT_ROUTES:
            path = ROUTES_DIR / entry
            if not path.is_file():
                stale.append(f"{entry} (file gone)")
                continue
            code = _strip_comments(path.read_text(encoding="utf-8"))
            if not TestNoNamedCadenceConstantBypassInRoutes.LOCAL_CADENCE_CONST.search(code):
                stale.append(f"{entry} (debt already paid)")
        self.assertEqual(
            stale,
            [],
            f"remove stale allowlist entries: {stale}",
        )


class TestQueryClientDelegatesToStrategy(unittest.TestCase):
    """The singleton QueryClient default sources cache/refetch from the SSOT."""

    def test_default_options_reference_strategy_ssot(self) -> None:
        text = QUERY_CLIENT.read_text(encoding="utf-8")
        for field in (
            "REFETCH_STRATEGIES.NORMAL.staleTime",
            "REFETCH_STRATEGIES.NORMAL.gcTime",
            "REFETCH_STRATEGIES.NORMAL.refetchOnWindowFocus",
        ):
            self.assertIn(
                field,
                text,
                f"query-client default must delegate to `{field}`",
            )

    def test_no_inline_cache_magic_number_in_client(self) -> None:
        code = _strip_comments(QUERY_CLIENT.read_text(encoding="utf-8"))
        for label, pattern in (
            ("staleTime", re.compile(r"staleTime\s*:\s*\d")),
            ("gcTime", re.compile(r"gcTime\s*:\s*\d")),
        ):
            self.assertIsNone(
                pattern.search(code),
                f"query-client must not hardcode `{label}` — use REFETCH_STRATEGIES",
            )


class TestQueryKeyHierarchy(unittest.TestCase):
    """The factory exposes hierarchical prefix helpers, not just flat leaves.

    The exec-plan requires a hierarchy (``all`` → leaf) so a broad invalidation
    can key on a stable prefix. The hierarchy is applied where the keys
    genuinely nest: ``session`` keys are ``['session', …]`` so ``session.all``
    (``['session']``) is a true TanStack array-prefix; the paginated attempts
    resource is namespaced under ``sessionAttempts.list``. Flat first-element
    keys (e.g. ``['project-coverage', …]``) intentionally get no fake parent —
    ``['project']`` is not an array-prefix of ``['project-coverage']``, so such
    a helper would be dead/misleading code.
    """

    def test_session_namespace_exposes_all_prefix(self) -> None:
        text = QUERY_CONFIG.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"session:\s*\{\s*\n\s*all:\s*\['session'\]\s*as const",
            "session namespace must expose `all: ['session'] as const` prefix",
        )

    def test_session_attempts_is_namespaced_with_list_leaf(self) -> None:
        text = QUERY_CONFIG.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"sessionAttempts:\s*\{\s*\n\s*list:\s*\(",
            "sessionAttempts must be a namespace with a `list` leaf",
        )

    def test_routes_call_the_namespaced_attempts_leaf(self) -> None:
        # The route must reach the paginated resource via the namespaced leaf
        # (`sessionAttempts.list(...)`), never the old flat callable.
        offenders: list[str] = []
        flat = re.compile(r"queryKeys\.sessionAttempts\s*\(")
        for path in _route_files():
            code = _strip_comments(path.read_text(encoding="utf-8"))
            if flat.search(code):
                offenders.append(path.name)
        self.assertEqual(
            offenders,
            [],
            f"routes call the flat sessionAttempts(...) instead of .list(...): {offenders}",
        )


class TestStaleTimePreservesPriorDefault(unittest.TestCase):
    """NORMAL/IMPORTANT staleTime preserves the pre-SSOT 30s default.

    Behaviour preservation is this increment's oracle. The pre-SSOT QueryClient
    default carried ``staleTime: 30_000`` inline; the SSOT must keep that exact
    value via the named ``STANDARD_STALE_TIME_MS`` constant rather than drifting
    up to ``CACHE_TIMES.SHORT`` (60s), which would silently halve refetch
    frequency for every operator view.
    """

    def test_standard_stale_time_constant_is_30s(self) -> None:
        text = QUERY_CONFIG.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"const STANDARD_STALE_TIME_MS\s*=\s*30_000",
            "query-config must define `STANDARD_STALE_TIME_MS = 30_000` (prior default)",
        )

    def test_normal_strategy_uses_the_30s_constant_not_short(self) -> None:
        text = QUERY_CONFIG.read_text(encoding="utf-8")
        normal = re.search(r"NORMAL:\s*\{(.*?)\}", text, flags=re.DOTALL)
        self.assertIsNotNone(normal, "REFETCH_STRATEGIES.NORMAL block not found")
        body = normal.group(1)
        self.assertIn(
            "staleTime: STANDARD_STALE_TIME_MS",
            body,
            "NORMAL.staleTime must delegate to STANDARD_STALE_TIME_MS (30s)",
        )
        self.assertNotIn(
            "staleTime: CACHE_TIMES.SHORT",
            body,
            "NORMAL.staleTime must NOT drift to CACHE_TIMES.SHORT (60s) — "
            "behaviour preservation requires the prior 30s default",
        )


class TestAllowlistRatchetDown(unittest.TestCase):
    """The inline-key allowlist must stay empty (ratchet-down)."""

    def test_allowlist_is_empty(self) -> None:
        self.assertEqual(
            sorted(ALLOWED_INLINE_KEY_ROUTES),
            [],
            "ALLOWED_INLINE_KEY_ROUTES must stay empty — remove carried debt",
        )


class TestChamberHeartbeatTtlMirrorParity(unittest.TestCase):
    """The frontend's heartbeat-TTL mirror tracks the backend domain SSOT.

    fe-w2-b-execution-freshness M4 (2026-07-28) — the chamber availability poll
    cadence is DERIVED from the server-side heartbeat TTL (sample twice per TTL,
    so the screen's recognition lag stays inside the timescale of the transition
    it reports). TypeScript cannot import the Python constant, so
    ``query-config.ts`` declares a mirror.

    An unchecked mirror is how this milestone was planned against ``30`` seconds:
    that number was read off a vitest fixture and the admin panel's stale
    ``ttl: '30'`` draft fallback, while the domain default has been ``90``. The
    planning input was wrong for the entire design phase because nothing
    compared the two. This seal makes the next such divergence a CI failure
    rather than a number nobody re-derives.

    Scope note: this is a DEFAULT used only to size the poll. Per-chamber TTLs
    ride on each availability row and are unaffected.
    """

    #: Backend owner of the value the frontend mirrors.
    CHAMBER_NODE_MODEL = moved_module_source('domain.models.chamber_node')

    BACKEND_TTL = re.compile(
        r"^DEFAULT_HEARTBEAT_TTL_SECONDS\s*=\s*(\d+)", re.MULTILINE
    )
    FRONTEND_TTL = re.compile(
        r"export const CHAMBER_HEARTBEAT_TTL_SECONDS_DEFAULT\s*=\s*(\d+)"
    )

    def _backend_ttl_seconds(self) -> int:
        text = self.CHAMBER_NODE_MODEL.read_text(encoding="utf-8")
        match = self.BACKEND_TTL.search(text)
        self.assertIsNotNone(
            match,
            "backend DEFAULT_HEARTBEAT_TTL_SECONDS not found — the frontend "
            f"mirror has no owner to track ({self.CHAMBER_NODE_MODEL})",
        )
        assert match is not None  # narrowed for the type checker
        return int(match.group(1))

    def _frontend_ttl_seconds(self) -> int:
        text = QUERY_CONFIG.read_text(encoding="utf-8")
        match = self.FRONTEND_TTL.search(text)
        self.assertIsNotNone(
            match,
            "query-config must export CHAMBER_HEARTBEAT_TTL_SECONDS_DEFAULT — "
            "the MONITORED cadence is derived from it",
        )
        assert match is not None  # narrowed for the type checker
        return int(match.group(1))

    def test_frontend_mirror_matches_backend_default(self) -> None:
        backend = self._backend_ttl_seconds()
        frontend = self._frontend_ttl_seconds()
        self.assertEqual(
            frontend,
            backend,
            "apps/web/src/api/query-config.ts::"
            f"CHAMBER_HEARTBEAT_TTL_SECONDS_DEFAULT ({frontend}) has drifted from "
            f"src/domain/models/chamber_node.py::DEFAULT_HEARTBEAT_TTL_SECONDS "
            f"({backend}). The chamber availability poll cadence is derived from "
            "this mirror, so the drift silently mis-sizes the poll.",
        )

    def test_monitored_cadence_is_derived_not_hardcoded(self) -> None:
        """The interval must be COMPUTED from the mirror.

        A literal ``45_000`` would satisfy the parity test above while still
        going stale the moment the mirror is corrected — the derivation is what
        makes the parity meaningful.
        """
        text = QUERY_CONFIG.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"const MONITORED_REFETCH_INTERVAL_MS\s*=\s*\n?\s*"
            r"\(CHAMBER_HEARTBEAT_TTL_SECONDS_DEFAULT\s*\*\s*1_000\)\s*/\s*"
            r"MONITORED_POLLS_PER_TTL",
            "MONITORED_REFETCH_INTERVAL_MS must be derived from "
            "CHAMBER_HEARTBEAT_TTL_SECONDS_DEFAULT, not written as a literal",
        )

    def test_monitored_strategy_actually_polls(self) -> None:
        """MONITORED exists and is not silently another non-polling tier."""
        text = QUERY_CONFIG.read_text(encoding="utf-8")
        body = re.search(r"MONITORED:\s*\{(.*?)\}", text, re.DOTALL)
        self.assertIsNotNone(body, "REFETCH_STRATEGIES.MONITORED block not found")
        assert body is not None  # narrowed for the type checker
        block = body.group(1)
        self.assertIn(
            "refetchInterval: MONITORED_REFETCH_INTERVAL_MS",
            block,
            "MONITORED must poll on the derived cadence",
        )
        # A supervision view is the one left open on a second monitor all shift;
        # background polling is declared OFF explicitly rather than inherited.
        self.assertIn(
            "refetchIntervalInBackground: false",
            block,
            "MONITORED must declare refetchIntervalInBackground: false "
            "explicitly — a hidden tab polling for a whole shift is the "
            "request-volume growth this tier must not cause",
        )


if __name__ == "__main__":
    unittest.main()
