"""Frontend optimistic mutation hook SSOT seal (fe-optimistic-mutation-hook, Inc 3).

Before Increment 3 every ``apps/web`` write route (claim acquire/release,
membership assign/revoke) was *central-write-then-refresh*: it fired the
mutation and only on success ``invalidateQueries``'d the read, so the operator
saw nothing change until a full server round-trip completed. Increment 3
introduces ``apps/web/src/shared/use-optimistic-mutation.ts`` as the single
source of truth for the cancel → snapshot → ``setQueryData`` → rollback →
invalidate dance (mirroring the equipment-management platform's proven hook).

This Python invariant seals the migration from the backend-only CI lane so a
refactor that re-introduces a direct ``setQueryData`` cache write in a route (the
bug class the hook exists to centralize), or drops the hook adoption, fails CI
even when ``npm run lint``/``npm run test`` is skipped. It is *complementary* to
the ``apps/web`` vitest suite (``tests/use-optimistic-mutation.test.tsx``) and
``npm run typecheck``.

The scans are docstring-aware: TypeScript block comments (``/* … */``) and line
comments (``//``) are stripped before the literal scan so a comment that merely
mentions ``setQueryData`` is not a false positive. Companion skill:
``/verify-frontend-optimistic-mutation``.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from support.parity import strip_ts_comments


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "apps" / "web"
ROUTES_DIR = WEB_ROOT / "src" / "routes"
HOOK = WEB_ROOT / "src" / "shared" / "use-optimistic-mutation.ts"

# Routes that own claim / membership writes and MUST go through the hook.
ADOPTING_ROUTES: frozenset[str] = frozenset({"projects.tsx", "membership.tsx"})


def _strip_ts_comments(src: str) -> str:
    """Delegate to the shared lexer — see
    ``tests/test_ts_comment_stripper_ssot.py``."""
    return strip_ts_comments(src)


def _route_files() -> list[Path]:
    return sorted(p for p in ROUTES_DIR.glob("*.tsx"))


class TestOptimisticHookSsotExists(unittest.TestCase):
    """The optimistic mutation hook SSOT module exists with the right surface."""

    def test_hook_module_present(self) -> None:
        self.assertTrue(HOOK.is_file(), f"missing optimistic mutation hook SSOT: {HOOK}")

    def test_exports_hook(self) -> None:
        src = HOOK.read_text(encoding="utf-8")
        self.assertRegex(
            src,
            r"export function useOptimisticMutation\b",
            "use-optimistic-mutation.ts must export `useOptimisticMutation`",
        )

    def test_hook_implements_optimistic_lifecycle(self) -> None:
        # The three TanStack mutation lifecycle hooks that encode the optimistic
        # contract (apply / rollback / reconcile) must all be present.
        src = _strip_ts_comments(HOOK.read_text(encoding="utf-8"))
        for member in ("onMutate", "onError", "onSettled"):
            self.assertIn(
                member,
                src,
                f"use-optimistic-mutation.ts must implement `{member}` "
                "(optimistic apply / rollback / reconcile lifecycle)",
            )

    def test_hook_owns_cache_primitives(self) -> None:
        # The hook is the single home for these QueryClient cache primitives:
        # cancel (so an in-flight refetch can't clobber), setQueryData (the
        # optimistic write), invalidateQueries (settle reconciliation).
        src = _strip_ts_comments(HOOK.read_text(encoding="utf-8"))
        for primitive in ("cancelQueries", "setQueryData", "invalidateQueries"):
            self.assertIn(
                primitive,
                src,
                f"use-optimistic-mutation.ts must call `{primitive}`",
            )


class TestNoDirectSetQueryDataInRoutes(unittest.TestCase):
    """Routes must never call `setQueryData`/`setQueriesData` directly — the
    optimistic cache write lives ONLY in the hook SSOT (the bug class this
    increment centralizes: a route hand-writing the cache and drifting from the
    read shape / forgetting rollback)."""

    PATTERN = re.compile(r"\.(setQueryData|setQueriesData)\s*\(")

    def test_no_direct_cache_write_in_routes(self) -> None:
        offenders: list[str] = []
        for route in _route_files():
            src = _strip_ts_comments(route.read_text(encoding="utf-8"))
            for m in self.PATTERN.finditer(src):
                line = src.count("\n", 0, m.start()) + 1
                offenders.append(f"{route.name}:{line} ({m.group(1)})")
        self.assertEqual(
            offenders,
            [],
            "direct setQueryData/setQueriesData call(s) in routes — the "
            "optimistic cache write must go through useOptimisticMutation "
            f"(@/shared/use-optimistic-mutation): {offenders}",
        )


class TestAdoptingRoutesUseHook(unittest.TestCase):
    """projects (claim acquire/release) + membership (assign/revoke) go through
    the hook, not a bare useMutation."""

    def test_adopting_routes_import_and_use_hook(self) -> None:
        missing: list[str] = []
        for route in _route_files():
            if route.name not in ADOPTING_ROUTES:
                continue
            src = route.read_text(encoding="utf-8")
            imports = "from '@/shared/use-optimistic-mutation'" in src
            # The call site carries generic type args: `useOptimisticMutation<…>({`.
            uses = bool(
                re.search(r"useOptimisticMutation\s*[<(]", _strip_ts_comments(src))
            )
            if not (imports and uses):
                missing.append(f"{route.name} (import={imports}, use={uses})")
        self.assertEqual(
            missing,
            [],
            "claim/membership write route(s) must import AND call "
            f"useOptimisticMutation: {missing}",
        )

    def test_adopting_routes_pass_factory_query_key_to_hook(self) -> None:
        # The hook must be given a `queryKey:` option. Inline key arrays are
        # already forbidden by the query-key SSOT seal, so the key is necessarily
        # factory-sourced (queryKeys.*). Here we only assert the call wires a key.
        missing: list[str] = []
        for route in _route_files():
            if route.name not in ADOPTING_ROUTES:
                continue
            src = _strip_ts_comments(route.read_text(encoding="utf-8"))
            if "queryKey:" not in src or "queryKeys." not in src:
                missing.append(route.name)
        self.assertEqual(
            missing,
            [],
            "adopting route(s) must pass a queryKeys-factory `queryKey:` to the "
            f"hook (Increment 1 SSOT): {missing}",
        )

    def test_adopting_routes_do_not_import_bare_use_mutation(self) -> None:
        # A regression to a hand-rolled useMutation in these routes would bypass
        # the optimistic SSOT. control/reports legitimately use bare useMutation
        # (non-optimistic session/download writes) and are out of scope.
        offenders: list[str] = []
        for route in _route_files():
            if route.name not in ADOPTING_ROUTES:
                continue
            src = _strip_ts_comments(route.read_text(encoding="utf-8"))
            # Match a react-query import line that pulls in useMutation.
            for m in re.finditer(r"import\s*\{[^}]*\buseMutation\b[^}]*\}\s*from\s*'@tanstack/react-query'", src):
                line = src.count("\n", 0, m.start()) + 1
                offenders.append(f"{route.name}:{line}")
        self.assertEqual(
            offenders,
            [],
            "claim/membership write route(s) import bare `useMutation` from "
            "@tanstack/react-query — use useOptimisticMutation instead: "
            f"{offenders}",
        )


class TestOptimisticClaimReleaseGuard(unittest.TestCase):
    """The projects route mints an optimistic placeholder claim_id
    (``optimistic:<hash>``) when an acquire is applied optimistically. That id is
    a client-only identity the central server never issued, so the release path
    must NEVER send it back via ``releaseClaim``. This seal proves the SSOT prefix
    constant + guard exist, so a refactor that drops the guard (re-introducing the
    bug where a just-acquired, not-yet-reconciled lock is released with a
    fabricated id) fails CI even without the vitest suite."""

    PROJECTS = ROUTES_DIR / "projects.tsx"

    def test_optimistic_prefix_is_ssot_constant(self) -> None:
        src = self.PROJECTS.read_text(encoding="utf-8")
        self.assertRegex(
            src,
            r"export const OPTIMISTIC_CLAIM_ID_PREFIX\s*=\s*'optimistic:'",
            "projects.tsx must define OPTIMISTIC_CLAIM_ID_PREFIX = 'optimistic:' "
            "as the single source for the placeholder claim_id prefix",
        )

    def test_optimistic_prefix_literal_not_scattered(self) -> None:
        # The raw 'optimistic:' literal must appear exactly once (the SSOT const
        # definition). The acquire transform and any other use go through the
        # constant — a second literal would be a drift site.
        src = _strip_ts_comments(self.PROJECTS.read_text(encoding="utf-8"))
        occurrences = src.count("'optimistic:'")
        self.assertEqual(
            occurrences,
            1,
            "the 'optimistic:' literal must appear once (the "
            "OPTIMISTIC_CLAIM_ID_PREFIX SSOT) — found "
            f"{occurrences}; route through the constant instead",
        )

    def test_release_path_guards_optimistic_id(self) -> None:
        # The release action must skip optimistic placeholder ids. Both the
        # predicate and its use in the release (onRelease) path must be present.
        src = _strip_ts_comments(self.PROJECTS.read_text(encoding="utf-8"))
        self.assertRegex(
            src,
            r"export function isOptimisticClaimId\b",
            "projects.tsx must export isOptimisticClaimId predicate",
        )
        # The guard must appear in the onRelease handler so a placeholder id is
        # never forwarded to releaseMutation.mutate / releaseClaim.
        onrelease = re.search(
            r"onRelease:\s*\([^)]*\)\s*=>\s*\{.*?\}", src, flags=re.DOTALL
        )
        self.assertIsNotNone(
            onrelease, "projects.tsx onRelease must be a guarded block body"
        )
        assert onrelease is not None  # for type-checkers
        self.assertIn(
            "isOptimisticClaimId",
            onrelease.group(0),
            "onRelease must guard with isOptimisticClaimId so an optimistic "
            "placeholder claim_id is never sent to releaseClaim",
        )


if __name__ == "__main__":
    unittest.main()
