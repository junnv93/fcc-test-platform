"""Work-claim status vocabulary — single source of truth.

``.claude/work-claims/`` is the registry that lets parallel supervisor sessions
avoid stepping on each other's scope. Every consumer of that registry needs to
answer one question: *"does this claim still own its scope?"* Until this
module existed, that answer was a single inline literal
(``claim.status in {"active", "blocked", "review"}``) while the registry itself
had accumulated far more than three status strings over time — synonyms of
"done" (``complete``/``completed``/``closed``/``merged``), synonyms of "still
in flight" (``in_review``/``merged_pending``/``active/...-pending-review``),
and files with no ``status`` key at all. None of that vocabulary was declared
anywhere in code, so every new claim file could introduce a fifteenth spelling
that would silently read as "not owning scope" — the false-negative direction
that lets two sessions collide on the same files.

This module is the fix: the vocabulary is declared once (:class:`ClaimStatus`),
"does it own scope" is derived from that vocabulary instead of re-declared by
each caller (:func:`claim_owns_scope`), and callers do not invent their own
tolerant parsing (:func:`parse_claim_status` is strict; :func:`owns_scope_or_warn`
is the one sanctioned tolerant wrapper, documented below).

⚠️ Runtime/gate asymmetry is intentional, not an oversight — and it is not
scaffolding for the one-off migration that landed alongside this module. The
registry was migrated to this vocabulary in the same change, so today every
file resolves strictly; the tolerant runtime path exists because *tomorrow's*
claim file is written by hand, by a session that may not have read this
module, and the two call sites below read the vocabulary under two different
safety requirements:

* **Runtime** (``supervisor_status.py`` / ``supervisor_preflight.py`` via
  :func:`owns_scope_or_warn`) degrades an unrecognized or missing status to
  "owns scope" (``True``) and prints a loud ``stderr`` warning instead of
  raising. Misreading an unknown claim as *not* owning its scope is the
  expensive failure this registry exists to prevent (two sessions editing the
  same files); misreading it as owning scope costs one person a manual
  double-check. Only one of those two mistakes is affordable at this call
  site, so the safe default is the conservative one.
* **Gates** (pytest invariants) resolve every claim strictly via
  :func:`parse_claim_status` and fail red on an unrecognized token. A gate
  that never turns red cannot pull the registry back to a clean vocabulary.

This is not a contradiction of the fail-open hygiene hooks elsewhere in this
repository (branch-mismatch guard, merge-readiness guard, self-audit
commit-msg gate). Those hooks are fail-open because a false positive there
blocks something that should have been allowed, and a hook that blocks too
often gets disabled. Here the unsafe direction is silence, not blocking, so
the runtime layer leans loud-and-permissive while the gate stays strict.
"""

from __future__ import annotations

from enum import Enum
import json
from pathlib import Path
import sys


class ClaimStatus(str, Enum):
    """The declared work-claim status vocabulary.

    These five tokens are exactly the ones already declared in
    ``.claude/work-claims/README.md`` — that declaration existed before this
    module did; what was missing was enforcement, not a new vocabulary. Adding
    a sixth token here to match some observed-but-undeclared spelling in the
    registry would repeat the defect this module fixes: today's variants get
    absorbed and tomorrow's still slip through silently.
    """

    ACTIVE = "active"
    BLOCKED = "blocked"
    REVIEW = "review"
    MERGED = "merged"
    ABANDONED = "abandoned"


#: Statuses under which a claim still owns its declared scope. This is a
#: *derivation* over :class:`ClaimStatus`, not a second vocabulary — callers
#: must not re-declare this set themselves (that duplication is exactly what
#: made the original inline literal impossible to keep in sync with reality).
OWNS_SCOPE: frozenset[ClaimStatus] = frozenset(
    {ClaimStatus.ACTIVE, ClaimStatus.BLOCKED, ClaimStatus.REVIEW}
)


class UnknownClaimStatusError(ValueError):
    """Raised when a claim's status string is not in :class:`ClaimStatus`.

    The message names the offending file, the token actually received, and
    the canonical vocabulary — so the person reading the error does not have
    to go spelunking in this module to know what to fix.
    """


def claim_owns_scope(status: ClaimStatus) -> bool:
    """Whether a claim in ``status`` still owns its declared scope."""
    return status in OWNS_SCOPE


def parse_claim_status(raw: str | None, *, source: object) -> ClaimStatus:
    """Strictly resolve ``raw`` to a :class:`ClaimStatus`.

    No aliases, no case-folding, no synonyms. A missing status (``raw is
    None``, e.g. a claim file with no ``status`` key) is treated the same as
    an unrecognized string — "unknown" is not "finished", and both must be
    named explicitly rather than defaulted away.

    ``source`` identifies where ``raw`` came from (typically the claim file's
    :class:`~pathlib.Path`) purely for the error message; it is not
    interpreted.
    """
    if raw is not None:
        try:
            return ClaimStatus(raw)
        except ValueError:
            pass
    canonical = ", ".join(member.value for member in ClaimStatus)
    received = "<missing>" if raw is None else repr(raw)
    raise UnknownClaimStatusError(
        f"unknown work-claim status {received} in {source} — expected one of: {canonical}"
    )


def owns_scope_or_warn(raw: str | None, *, source: object) -> bool:
    """Runtime-only tolerant wrapper — see the module docstring for why this
    degrades to "owns scope" instead of raising.

    This is the *only* sanctioned tolerant reading of claim status in this
    codebase. Do not write a second one next to a call site that finds this
    inconvenient — that duplication is how the vocabulary drifted the first
    time.
    """
    try:
        return claim_owns_scope(parse_claim_status(raw, source=source))
    except UnknownClaimStatusError as exc:
        print(f"[work_claim_status] WARNING: {exc} — treating as scope-owning", file=sys.stderr)
        return True


def read_claim_document(path: Path) -> dict:
    """Single read point for claim JSON files.

    ``utf-8-sig`` tolerates a UTF-8 BOM (at least one claim file in the
    registry has one); a bare ``utf-8`` read would raise on that file instead
    of parsing it.
    """
    return json.loads(path.read_text(encoding="utf-8-sig"))
