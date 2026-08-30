"""Two registries, one keyspace — is this provider id coherent? (2026-08-25).

TWO THINGS ARE BOTH CALLED "THE PROVIDER ID"
--------------------------------------------
The reference-data screen fills its provider picker from the **provider UI
descriptor registry** (what this deployment renders). Every reference read and
write resolves against **``providers.provider_id``** in the central database (a
text natural key an operator registers). They are meant to be one keyspace, and
until now nothing said so at runtime.

When they disagree the failure is invisible in the worst way. Listing answers
``200`` with an empty result, because the list statement filters on a join that
simply matches no rows — so the screen looks merely unprovisioned, and a tester
waits for data that will never arrive. The first authoring attempt 404s with
"unknown provider_id", long after anyone would connect it to registration.

WHY THREE TOKENS AND NOT TWO
-----------------------------
"Not coherent" is not one fact, because the remedy differs and different people
perform it:

``NOT_REGISTERED``
    The picker offers this provider; the central table has no row for it. The
    remedy is **an operator registering the provider row**. The caller did
    nothing wrong and cannot fix it.

``UNKNOWN``
    Neither side knows this id. The remedy is **the caller correcting the id**.

Folding them into one refusal would hand the client a single status carrying two
facts, and the only way back out would be parsing a sentence written for a
person — the failure mode this repository forbids everywhere else.

THE DEGRADE DIRECTION IS ASYMMETRIC, AND THAT IS THE DESIGN
------------------------------------------------------------
``offered`` may be unavailable (a composition without the descriptor registry).
An unregistered provider then classifies as ``NOT_REGISTERED``, never
``UNKNOWN``. ``UNKNOWN`` is an *assertion that the caller typed a bad id*, and
that is precisely what cannot be known without the offering. Claiming it would
send a tester to fix something that is not theirs to fix, while
``NOT_REGISTERED`` names the action an operator must take in either case.

OBSERVATIONS ARE ARGUMENTS
---------------------------
This module opens nothing. It does not read the registry, the database, or the
environment; the boundary observes both sides and passes them in. That is what
keeps the judgement independent of either oracle — the same reason the artifact
custody judgement takes its observations rather than reading the filesystem.

Domain-pure: stdlib ``enum`` / ``typing`` only.
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable, Optional


__all__ = [
    'ProviderIdentityStatus',
    'classify_provider_identity',
]


class ProviderIdentityStatus(Enum):
    """Whether a provider id means the same thing on both sides."""

    #: Registered centrally — every reference operation can resolve it.
    COHERENT = 'COHERENT'
    #: The screen offers it (or the offering is unknown) but the central
    #: ``providers`` table has no row. An operator must register it.
    NOT_REGISTERED = 'NOT_REGISTERED'
    #: Neither side knows this id. The caller must correct it.
    UNKNOWN = 'UNKNOWN'

    @property
    def is_coherent(self) -> bool:
        return self is ProviderIdentityStatus.COHERENT


#: ⚠️ The comparison is EXACT — no stripping, no case folding (2026-08-25).
#:
#: An earlier draft folded whitespace here on the reasoning that "a trailing
#: space copied out of a spreadsheet is the same provider". It is not, and
#: folding it made this module the only place in the system that thought so.
#: Every statement downstream keys on the text natural key exactly: the
#: existence probe, the listing JOIN, the insert guard. So a folded match would
#: classify `'x '` as COHERENT and then hand it to SQL that matches nothing —
#: reproducing the silent empty result this whole module exists to end, one
#: layer lower and harder to see.
#:
#: Exact comparison also produces the RIGHT remedy. With `'x'` offered and
#: `'x '` named, the caller gets UNKNOWN — "you named an id nobody knows",
#: which is true and which they can fix — rather than NOT_REGISTERED, which
#: would send an operator to register a provider that is already registered.


def classify_provider_identity(
    provider_id: object,
    *,
    offered: Optional[Iterable[object]],
    registered: bool,
) -> ProviderIdentityStatus:
    """Classify one provider id against both registries.

    Args:
        provider_id: the id the caller named (path segment or configuration).
        offered: provider ids this deployment's descriptor registry offers, or
            ``None`` when the offering cannot be observed. ``None`` is not the
            empty set — see the module docstring on the degrade direction.
        registered: whether the central ``providers`` table holds a row for it.

    Returns:
        ``COHERENT`` when registered. Otherwise ``UNKNOWN`` only when the
        offering is observable *and* excludes the id; ``NOT_REGISTERED`` in
        every other unregistered case. Membership is decided by **exact** string
        comparison — see the note above ``classify_provider_identity``.

    Total — never raises. Callers pass ids that came off a URL, and a classifier
    that could raise would turn a malformed path segment into a 500.
    """
    if registered:
        return ProviderIdentityStatus.COHERENT
    if offered is None:
        return ProviderIdentityStatus.NOT_REGISTERED
    wanted = str(provider_id)
    for candidate in offered:
        if str(candidate) == wanted:
            return ProviderIdentityStatus.NOT_REGISTERED
    return ProviderIdentityStatus.UNKNOWN
