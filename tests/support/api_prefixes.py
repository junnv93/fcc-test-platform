"""Backend top-level path prefixes — the one derivation both gateways check.

FCC path-routes browser traffic to three ASGI surfaces through **two different
gateways** that must agree on the same topology:

  * **dev**  — ``apps/web/vite.config.ts`` ``server.proxy``, configured from
    ``apps/web/dev-stack.config.json``.
  * **prod** — ``infra/central/nginx.conf`` ``location`` blocks in the central
    hub container.

``dev-environment-contract-parity`` (PR #79, 2026-08-01) sealed the dev side by
*deriving* the required prefix set from the backend route tables instead of
trusting a hand-written list, after ``/report-automation`` turned out never to
have been proxied. The very next wave found the identical hole still open in
prod — and the reason it survived is instructive: the prod seal had its own,
separately written notion of "which prefixes exist", so fixing dev taught it
nothing.

This module exists so that cannot happen a third time. Both gateway seals import
the same derivation from here; there is no second place where "what prefixes does
the backend expose" is decided. Following the ``tests/support/parity.py``
precedent, the shared logic lives in a support module rather than being imported
test-module-to-test-module, which would make one seal's refactor break the other.

Deliberately **not** derived from ``docs/api/*.openapi.json``: those artifacts are
a generated projection that can lag a regenerate step, so a seal built on them
would compare a copy against a copy. These dicts are what the routers mount.
"""
from __future__ import annotations

from fcc_test_contracts.headless.api_contracts import HEADLESS_API_ROUTES
from fcc_test_kernel.application.central_contract.api_contracts import PLATFORM_API_ROUTES
from fcc_test_kernel.application.session.api_contracts import SESSION_API_ROUTES


#: Surface key → the route table its ASGI app mounts. The surface keys match
#: ``apps/web/dev-stack.config.json``'s ``surfaces[].key``, which is what lets
#: the dev seal assert per-surface coverage (routing ``/health`` to the platform
#: app would satisfy a global check yet still be wrong).
BACKEND_ROUTE_TABLES: dict[str, dict[str, tuple[str, str]]] = {
    'session': SESSION_API_ROUTES,
    'headless': HEADLESS_API_ROUTES,
    'platform': PLATFORM_API_ROUTES,
}


def top_level_prefixes(routes: dict[str, tuple[str, str]]) -> set[str]:
    """Return the distinct first path segments a route table exposes.

    Both gateways path-route on the first segment, so that segment — not the
    full path — is the unit of proxy coverage.
    """
    return {'/' + path.lstrip('/').split('/', 1)[0] for _method, path in routes.values()}


def prefixes_by_surface() -> dict[str, set[str]]:
    """Every surface's top-level prefixes, keyed by surface."""
    return {key: top_level_prefixes(routes) for key, routes in BACKEND_ROUTE_TABLES.items()}


def all_backend_prefixes() -> set[str]:
    """The union across every surface — what a single-origin gateway must cover.

    The prod hub terminates one origin for all surfaces, so it reasons about the
    union; dev proxies per surface and uses :func:`prefixes_by_surface`.
    """
    return set().union(*prefixes_by_surface().values())
