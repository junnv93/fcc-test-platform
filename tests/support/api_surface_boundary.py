"""Resolve an :class:`ApiSurface` to the boundary module that classifies its errors.

Two axes now ask the same question and must not answer it twice:

* the **publication** axis (`test_error_code_publication_axis_invariants.py`) —
  "does every code this surface can emit appear in its OpenAPI artifact?"
* the **classification** axis (`test_headless_boundary_default_honesty.py`) —
  "do the surfaces agree on what an unclassified exception is?"

A second copy of the resolution would drift, and the copy that drifts is whichever
one is edited less — so it lives here once, next to the other shared harness SSOT
(`tests/support/parity.py`).

Convention, not a hand-written registry: the module is
``infrastructure.adapters.driving.api.{surface.value}{suffix}`` and it owns
``_{SURFACE}_ERROR_CODE_TABLE`` and ``_{SURFACE}_DEFAULT_ERROR_CODE``. A violation
fails loudly with ``ModuleNotFoundError``/``AttributeError``, which is itself a
directed signal — a hand-written ``{surface: module}`` dict would be a second
register of surfaces, and keeping surfaces in one place is the whole point.
"""
from __future__ import annotations

import importlib
from typing import Any

from fcc_test_contracts.common.api_error_codes import ApiSurface, ErrorCode

__all__ = [
    'ROUTES_MODULE_SUFFIXES',
    'routes_module',
    'boundary_table',
    'boundary_default',
    'codes_from_table',
    'emittable_codes',
]


#: Both spellings are the convention. ``{surface}_routes`` is the original; the
#: session surface's module is historically ``session_router`` (singular). Trying
#: the spellings in order avoids a per-surface hand mapping — this is two
#: notations of one rule, not a register, and a new surface need only match one.
ROUTES_MODULE_SUFFIXES = ('_routes', '_router')

#: Where a surface's boundary module can live. ⚠️ Two entries because on
#: 2026-08-31 the platform surface left this repository for the ``fcc-test-platform``
#: package -- the surface did not disappear, it moved, and a resolver that knows
#: one prefix reads those two states as the same thing. Same reasoning as the
#: suffixes above: notations of one rule, not a per-surface register.
ROUTES_MODULE_PREFIXES = (
    'infrastructure.adapters.driving.api',
    'fcc_test_platform.api',
)


def routes_module(surface: ApiSurface):
    """Import and return the boundary module that owns ``surface``'s error table."""
    errors = []
    for prefix in ROUTES_MODULE_PREFIXES:
        for suffix in ROUTES_MODULE_SUFFIXES:
            try:
                return importlib.import_module(
                    f'{prefix}.{surface.value}{suffix}'
                )
            except ModuleNotFoundError as exc:  # pragma: no cover - loud on real drift
                errors.append(f'{prefix}.{surface.value}{suffix}: {exc}')
    raise ModuleNotFoundError(
        f'no boundary module for surface {surface.value!r}; tried '
        + ', '.join(errors)
    )


def boundary_table(surface: ApiSurface) -> tuple:
    """Return ``surface``'s declarative ``(exception_type, ErrorCode)`` table."""
    module = routes_module(surface)
    return getattr(module, f'_{surface.value.upper()}_ERROR_CODE_TABLE')


def boundary_default(surface: ApiSurface) -> ErrorCode:
    """Return the code ``surface`` answers when nothing in its table matched."""
    module = routes_module(surface)
    return getattr(module, f'_{surface.value.upper()}_DEFAULT_ERROR_CODE')


def codes_from_table(table: tuple, default: ErrorCode) -> frozenset:
    """Pure reader: table tuple + default code -> emittable ``ErrorCode`` set.

    Kept separate from :func:`emittable_codes` so a mutation test can push a
    **mutated table** through the exact reader production checks use, instead of
    union-ing a code onto an already-computed set (which would stay green even if
    this reader were itself broken).
    """
    codes = {code for _, code in table}
    codes.add(default)
    return frozenset(codes)


def emittable_codes(surface: ApiSurface) -> frozenset:
    """Every ``ErrorCode`` ``surface``'s boundary can put on the wire."""
    return codes_from_table(boundary_table(surface), boundary_default(surface))


def resolve_with(surface: ApiSurface, exc: BaseException) -> Any:
    """Resolve ``exc`` exactly as ``surface``'s boundary would.

    Behavioural, not structural: callers get the code the surface would actually
    emit rather than a constant read off the module.
    """
    from fcc_test_contracts.common.api_error_codes import resolve_error_code

    return resolve_error_code(
        exc, boundary_table(surface), default=boundary_default(surface),
    )
