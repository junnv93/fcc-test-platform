"""FCC ID derivation policy (pure domain, stdlib only).

The FCC ID printed on a grant/certificate is the grantee code concatenated with
the equipment *product code*. It is **derived** from the project's
``fcc_grantee_code`` + model name — never stored as its own DB column — so the
rule lives in a single domain SSOT here. Phase B (project-report-meta).

Examples
--------
- ``product_code('SM-X940')``   → ``'SMX940'``
- ``product_code('sm x940')``   → ``'SMX940'``
- ``fcc_id('A3L', 'SM-X940')``  → ``'A3LSMX940'``
- ``fcc_id(None, 'SM-X940')``   → ``None``  (grantee unknown ⇒ no FCC ID)
"""
from __future__ import annotations

import re
from typing import Optional

__all__ = ['product_code', 'fcc_id']

# Strip everything that is not an ASCII letter or digit; the product code is the
# alphanumeric remainder, upper-cased (FCC grant convention).
_NON_ALNUM = re.compile(r'[^A-Za-z0-9]+')


def product_code(model_name: str) -> str:
    """Return the FCC product code for ``model_name``.

    Keeps only alphanumeric characters and upper-cases the result so that
    ``'SM-X940'`` / ``'sm x940'`` both normalize to ``'SMX940'``.
    """
    if model_name is None:
        return ''
    return _NON_ALNUM.sub('', str(model_name)).upper()


def fcc_id(grantee_code: Optional[str], model_name: str) -> Optional[str]:
    """Return the derived FCC ID, or ``None`` when the grantee code is absent.

    The grantee code is used verbatim (trimmed); the product code is derived
    from ``model_name`` via :func:`product_code`. When ``grantee_code`` is
    ``None``/blank the FCC ID cannot be formed and ``None`` is returned.
    """
    grantee = '' if grantee_code is None else str(grantee_code).strip()
    if not grantee:
        return None
    return grantee + product_code(model_name)
