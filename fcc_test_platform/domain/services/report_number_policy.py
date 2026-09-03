"""Report number derivation policy (pure domain, stdlib only).

The FCC test report (성적서) number is **derived**, never stored as its own DB
column — mirror of the derived ``fcc_id`` (see :mod:`fcc_id_policy`). It is the
project's ``management_number`` joined with the report's ``edition`` token under a
fixed prefix/separator:

    report_number = ``S-{management_number}-{edition}``   (e.g. ``S-4792232056-E2V1``)

The prefix (``S-``) and separator (``-``) live here as the single SSOT so no call
site hardcodes the format. When the project has no ``management_number`` the
report number cannot be formed and ``None`` is returned (mirror of ``fcc_id``
returning ``None`` when the grantee code is absent). Phase G (test_reports).

Examples
--------
- ``report_number('4792232056', 'E2V1')`` → ``'S-4792232056-E2V1'``
- ``report_number('  4792232056 ', 'E2V1')`` → ``'S-4792232056-E2V1'``  (trimmed)
- ``report_number(None, 'E2V1')`` → ``None``  (mgmt unknown ⇒ no report number)
- ``report_number('4792232056', '')`` → ``None``  (edition required)
"""
from __future__ import annotations

from typing import Optional

__all__ = ['report_number', 'REPORT_NUMBER_PREFIX', 'REPORT_NUMBER_SEPARATOR']

# Format SSOT — the only place the 'S-{mgmt}-{edition}' shape is encoded.
REPORT_NUMBER_PREFIX = 'S-'
REPORT_NUMBER_SEPARATOR = '-'


def report_number(management_number: Optional[str], edition: Optional[str]) -> Optional[str]:
    """Return the derived report number, or ``None`` when it cannot be formed.

    Both the management number (the Report Number basis, PM-assigned) and the
    edition token (e.g. ``'E2V1'``) are used verbatim after trimming. When either
    is absent/blank the report number cannot be formed and ``None`` is returned —
    a partial ``S--E2V1`` / ``S-4792232056-`` is never emitted.
    """
    mgmt = '' if management_number is None else str(management_number).strip()
    edition_token = '' if edition is None else str(edition).strip()
    if not mgmt or not edition_token:
        return None
    return f'{REPORT_NUMBER_PREFIX}{mgmt}{REPORT_NUMBER_SEPARATOR}{edition_token}'
