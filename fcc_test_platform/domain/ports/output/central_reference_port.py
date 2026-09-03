"""Output ports — the central reference catalog (Wave 3, 2026-08-07).

The central platform is the authoritative ORIGIN of measurement reference data
(correction, switch port mapping, frequency table, analyzer settings, antenna
gain). Chamber PCs hold replicas. These ports are the origin's storage boundary:

- ``CentralReferenceReadPort`` — list revisions by identity facet, read one
  revision's entries, and assemble the delivery bundle a chamber pulls.
- ``CentralReferenceWritePort`` — create a CANDIDATE revision, and publish one.

WHY PUBLISH IS ITS OWN CAPABILITY
----------------------------------
Publishing is not "update state = PUBLISHED". At most one revision may be
published per identity, and the central DDL enforces that with a partial unique
index, so publish is the operation that can lose a race and must report it as a
conflict rather than as a backend failure. Modelling it as a generic update would
lose that distinction at the port boundary, and the route would have to guess.

WHY THE BUNDLE IS ONE CAPABILITY AND NOT TWO
---------------------------------------------
A chamber needs revisions AND their entries as ONE consistent answer. Fetching
them separately opens a window where a publish lands between the two calls and
the chamber applies half of a coupled family group — which pairs a signal path
with another path's loss and produces a plausible wrong number. So the bundle is
a single capability whose result carries the tag identifying the cut it came
from.

dependency-free: stdlib typing only (no infrastructure / psycopg / sqlalchemy /
fastapi / pandas / openpyxl / PySide6 imports).
"""
from __future__ import annotations

from typing import Mapping, Optional, Protocol, Sequence, runtime_checkable


__all__ = [
    'CentralReferenceError',
    'ReferenceCoupledPublishError',
    'ReferencePublishConflictError',
    'ReferenceProviderNotFoundError',
    'ReferenceProviderNotRegisteredError',
    'ReferenceRevisionNotFoundError',
    'ReferenceStateConflictError',
    'CentralReferenceReadPort',
    'CentralReferenceWritePort',
]


class CentralReferenceError(RuntimeError):
    """A central reference read/write failed at the infrastructure level (→ 503).

    Loud, never a silently empty list. An empty list means "this room has nothing
    published"; a backend outage that rendered as an empty list would tell a
    chamber it is correctly holding nothing, which is the difference between a
    known gap and a measurement made with no correction at all.
    """


class ReferenceProviderNotFoundError(RuntimeError):
    """No central ``providers`` row owns this reference family (→ 404).

    A client error, not a backend failure: providers are operator-registered
    reference data, so an unknown one is a bad request rather than an outage.
    """


class ReferenceProviderNotRegisteredError(ReferenceProviderNotFoundError):
    """The screen offers this provider but no central row registers it (→ 404).

    A **subclass**, because it is a refinement of "unknown provider" rather than
    a rival fact: everything that already handles the parent keeps handling this,
    and the wire status is the same 404. What differs is *who fixes it*. The
    parent says the caller named an id nobody knows — check the id. This one says
    the deployment's own provider picker offers the id and the central
    ``providers`` table has no row for it, so the caller did nothing wrong and
    cannot do anything about it; an operator must register the provider.

    Kept distinct instead of folded into one 404 because folding would hand the
    client a single status carrying two remedies, and the only way back out would
    be parsing a sentence written for a person.

    Being a subclass makes ORDER load-bearing wherever exceptions are mapped
    most-specific-first: listed after its parent, it would never be reached and
    the distinction would silently vanish.
    """


class ReferenceRevisionNotFoundError(RuntimeError):
    """No central ``reference_revisions`` row with that id (→ 404)."""


class ReferencePublishConflictError(RuntimeError):
    """Another revision already holds the published slot for this identity (→ 409).

    Distinct from ``CentralReferenceError`` (503) and ``ValueError`` (400) so the
    platform error table maps it to a conflict, mirroring
    ``ReportEditionConflictError``. The conflict is detected by the partial unique
    index rather than by a read-then-write check, so two operators publishing at
    once cannot both succeed.
    """


class ReferenceCoupledPublishError(RuntimeError):
    """A coupled family group was asked to publish only one of its halves (→ 409).

    Correction and switch-port mapping describe two halves of one physical fact.
    Publishing one alone leaves the chamber pairing one revision's signal path
    with another's path loss — the measurement completes, yields a verdict, and
    is wrong by the difference between two cables, with nothing downstream able
    to notice.

    Refused rather than accepted-and-warned because the two sides of that outcome
    are different sides: the origin would answer 200, the chamber would change
    nothing, and the only trace would be an ERROR line on a PC nobody watches.
    The message names the sibling family so the refusal is actionable.
    """


class ReferenceStateConflictError(RuntimeError):
    """An operation was asked of a revision whose state does not permit it (→ 409).

    Deliberately NOT ``ReferencePublishConflictError``. Both render 409, but they
    answer different questions — "someone else already published this identity"
    versus "this revision is a candidate, and only a published one may be forked".
    Folding them together would hand the tester one status code carrying two
    facts, and the only way back out would be parsing the message, which is a
    sentence written for a person.
    """


@runtime_checkable
class CentralReferenceReadPort(Protocol):
    """Read the origin's reference catalog."""

    def provider_exists(self, provider_id: str) -> bool:
        """Whether the central ``providers`` table holds this natural key.

        Its own capability rather than a by-product of the other reads, because
        every other read *filters* on the provider and so answers "no rows" for
        an unknown one and for a known-but-empty one alike. Those are different
        facts: the first needs an operator to register a provider row, the
        second needs nothing at all. A catalog surface that cannot tell them
        apart reports the first as the second, and the screen shows a tester an
        empty table that will never fill.

        Returns a bool rather than the internal uuid on purpose. Every statement
        in this catalog keys on the **text natural key**, so handing the service
        a uuid would introduce a second representation of one identity, and the
        two would have to be kept in agreement forever for no gain.

        Raises ``CentralReferenceError`` on an outage — never ``False``. An
        unreachable database has not told us the provider is absent, and
        borrowing "absent" for "unknown" is the same conflation this method
        exists to end.
        """

    def list_revisions(
        self,
        provider_id: str,
        *,
        family: Optional[str] = None,
        scope_kind: Optional[str] = None,
        scope_id: Optional[str] = None,
        state: Optional[str] = None,
        limit: Optional[int] = None,
        cursor_values: Optional[Sequence] = None,
    ) -> list[dict]:
        """Revision summaries for one provider, newest identity first."""

    def read_revision(self, revision_id: str) -> Optional[dict]:
        """One revision summary row, or ``None`` when no such revision exists.

        Deliberately NOT expressed as an id filter on ``list_revisions``: that
        listing pages over the ``(family, profile, scope, number, id)`` keyset,
        and a filter on a column outside that axis would make the cursor describe
        a position in a set the caller never asked for.

        Needed because reviewing a candidate before publishing it requires the
        revision's own row — who authored it, which identity slot it targets,
        what state it is in — and the listing response carries summaries for a
        page, not for a named revision.
        """

    def read_entries(self, revision_id: str) -> list[dict]:
        """Every entry of one revision, in ``entry_order``."""

    def read_bundle_identity(
        self,
        provider_id: str,
        *,
        scope_ids: Sequence[str],
    ) -> list[dict]:
        """Just the identity of every published revision in the cut.

        ``{'revision_id', 'etag'}`` per row, unpaged and without payloads. The
        bundle tag is derived from this, which is what lets the tag be the same
        on every page: if it were derived from a page's contents, each page would
        carry a different tag and the mid-walk-change check would fire on every
        walk of an unchanged bundle.
        """

    def read_bundle(
        self,
        provider_id: str,
        *,
        scope_ids: Sequence[str],
        limit: Optional[int] = None,
        cursor_values: Optional[Sequence] = None,
    ) -> list[dict]:
        """One page of published revisions for the given scopes, with entries.

        The scopes are the room a chamber occupies plus, when supplied, the
        project running in it. Passing them together rather than in two calls is
        what makes a coupled family group indivisible at the boundary.

        The page boundary falls between REVISIONS, never inside one. A revision
        is the unit projection replaces, so half of one has no meaning a chamber
        could act on; paging inside it would only move the "apply half a thing"
        risk from the group level down to the revision level.
        """


@runtime_checkable
class CentralReferenceWritePort(Protocol):
    """Create and publish revisions at the origin."""

    def create_candidate(
        self,
        provider_id: str,
        *,
        revision: Mapping,
        entries: Sequence[Mapping],
    ) -> dict:
        """Insert a CANDIDATE revision and its entries in one transaction.

        Never publishes. Publication is a human review step, and keeping the two
        apart is what makes "no published revision" — the state of every existing
        database — a provable no-op for measurement.
        """

    def update_candidate_entries(
        self,
        revision_id: str,
        *,
        expected_etag: str,
        etag: str,
        content_sha256: str,
        provenance_kind: str,
        updated_by: str,
        payloads: Mapping[str, str],
        entry_hashes: Mapping[str, str],
    ) -> dict:
        """Rewrite the payloads of named rows in ONE candidate, or raise.

        ``payloads`` and ``entry_hashes`` are keyed by ``reference_id`` and hold
        ONLY the rows that changed — the unedited rows of a thousand-point
        correction curve are never re-sent, so one number does not cost a full
        round trip of the curve.

        ``expected_etag`` is the concurrency control, and it belongs in the
        UPDATE's WHERE clause rather than in a preceding read. Two testers
        editing the same candidate is the ordinary case here, not the exotic
        one, and a read-then-write check would leave a window in which the
        second write silently discards the first tester's number.

        The token is the ``etag``, not ``version``. ADR-0019 declined blanket
        version-CAS because a version column is a counter somebody must remember
        to bump, and no central entity had a lost-update surface worth that
        upkeep. This one does have such a surface — but it does not need the
        counter, because the etag is DERIVED from content: a write that changes
        a value changes the tag by construction, and two identical concurrent
        edits converge on the same tag rather than fighting. ``version`` is left
        untouched here exactly as publish leaves it untouched.
        """

    def update_candidate_rows(
        self,
        revision_id: str,
        *,
        expected_etag: str,
        etag: str,
        content_sha256: str,
        provenance_kind: str,
        updated_by: str,
        additions: Sequence[Mapping[str, object]],
        removals: Sequence[str],
    ) -> dict:
        """Add and remove ROWS of ONE candidate in a single transaction, or raise.

        Separate from :meth:`update_candidate_entries` because the two answer
        different questions. That one rewrites values in rows that stay; this one
        changes which rows exist — the operation the value-edit policy refuses
        precisely because *"changing an identity field is an add plus a remove,
        not an edit"*.

        Both halves must land together. An add that commits without its paired
        removal leaves two rows the lookup cannot tell apart; a removal without
        its add can empty a table the measurement path reads. The concurrency
        control is the same ``expected_etag`` CAS for the same reason as above.

        ``additions`` carry a server-assigned ``entry_order`` that continues past
        the current maximum. Removals leave gaps, and that is fine: the unique
        index wants uniqueness, not density, and renumbering would rewrite every
        surviving row of a curve to save nothing.
        """

    def publish(
        self,
        revision_id: str,
        *,
        published_by: str,
        published_at: str,
        publish_reason: Optional[str] = None,
    ) -> dict:
        """Move one candidate to PUBLISHED, or raise on a lost race."""

    def publish_coupled(
        self,
        revision_id: str,
        coupled_revision_id: str,
        *,
        published_by: str,
        published_at: str,
        publish_reason: Optional[str] = None,
    ) -> list[dict]:
        """Publish two revisions of a coupled family group in ONE transaction.

        Correction and switch-port mapping describe two halves of one physical
        fact: which cable carries the signal, and how much that cable costs in dB.
        Publishing one without the other pairs antenna 1's signal with antenna 2's
        path loss, and the measurement still completes, still yields a verdict,
        and is silently wrong by the difference between two cables.

        The projection already refuses a half-published group and leaves BOTH
        families workbook-owned, so this is not the safety fix — it is the
        dead-end fix. Without it the origin answers 200 to a half publish, the
        chamber changes nothing, and the only trace is an ERROR line on a PC
        nobody is watching: the side that reports success and the side that knows
        it failed are different sides.

        Either both rows move or neither does. A partial success here would be
        exactly the state the coupling exists to forbid, produced by the
        mechanism meant to prevent it.
        """
