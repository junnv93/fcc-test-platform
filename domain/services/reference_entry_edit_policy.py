"""Which edits to a reference candidate's rows are edits at all — pure policy.

A tester who re-cables a chamber and re-measures the loss needs to change a
number in a candidate revision.  This module answers the only question that has
to be answered before that number reaches a published curve: *is what arrived
an edit to an existing row, or is it something else wearing an edit's shape?*

Three refusals live here, and each of them is a measurement outcome rather than
a matter of tidiness:

1.  **An unknown ``reference_id`` is refused, never skipped.**  Silently
    dropping it would make the screen say "saved" while the tester's number
    went nowhere, and the next time anyone looks the old value is still there
    with no record that anyone tried.

2.  **The payload's key set must equal the family's runtime row contract.**  A
    payload here is not a document about a row — per
    ``PROJECTION_FIELD_CONTRACT`` it *is* the runtime row.  A payload missing a
    column, or carrying one that does not exist, projects a malformed row into
    the table the measurement path reads.

3.  **Identity fields cannot change.**  Those fields are what makes a row *that
    row*; changing one is an add plus a remove, not an edit.  Worse, the
    identity is also stored separately as ``identity_key`` and used as the
    diff join key, so an edit that moved an identity field would leave the two
    disagreeing — and the disagreement would surface as a mysteriously wrong
    diff long after the edit.

The comparison for (3) is deliberately ``str``-based, because that is exactly
how ``identity_key_for`` renders the stored key.  Comparing any other way would
let a value change that the key notices slip through the check that exists to
protect the key.

Pure: no I/O, no infrastructure imports, no clock.  The caller supplies the
current rows and receives the new ones.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from domain.models.reference_catalog import CatalogFamily
from domain.services.reference_ownership_policy import (
    identity_fields_for,
    projection_fields_for,
    projection_value_kinds_for,
)


__all__ = [
    'MAX_ENTRY_EDITS_PER_REQUEST',
    'EntryEdit',
    'EntryEditOutcome',
    'ReferenceEntryEditError',
    'ReferenceEntryPayloadValueError',
    'apply_entry_edits',
    'validate_entry_payload_shape',
    'validate_entry_payload_values',
]


#: How many rows one request may edit.
#:
#: A tester changes cells by hand. A request naming more rows than this is a
#: program, and a program that wants to replace a whole curve should import a
#: revision rather than edit one — that path exists, carries provenance, and
#: does not hold thousands of rows in one transaction.
#:
#: This bounds the WRITE, not the parse: the web framework has already decoded
#: the body by the time any of this runs. Bounding the request body itself is a
#: platform-wide concern (every route shares it) and belongs in middleware or
#: the reverse proxy, not here — recorded in the debt ledger rather than
#: half-solved at one operation.
MAX_ENTRY_EDITS_PER_REQUEST = 1000


class ReferenceEntryEditError(ValueError):
    """A proposed entry edit is not an edit of an existing row (→ 400).

    A ``ValueError`` subclass, like ``ReferenceScopeError``, because the caller
    can fix it: the request named a row that is not there, or described a row
    shape the family does not have. The platform error table already maps
    ``ValueError`` to 400, so this needs no new mapping — and 400 rather than
    404 because the addressed resource (this revision's entry collection) does
    exist. Answering 404 would say the collection is missing.
    """


class ReferenceEntryPayloadValueError(ValueError):
    """A payload field has the wrong JSON kind for its runtime column (→ 400)."""


@dataclass(frozen=True)
class EntryEdit:
    """One proposed row change, addressed by the key that is actually unique.

    ``reference_id`` and not ``identity_key``: only ``(revision_id,
    reference_id)`` carries a UNIQUE index. ``identity_key`` has a plain index,
    so addressing a write target by it would be choosing a key that permits
    two answers.
    """

    reference_id: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class EntryEditOutcome:
    """The rows after the edits, and whether any value actually moved.

    ``changed`` is not bookkeeping. It decides whether provenance is promoted
    to ``FORK_EDIT``, and that promotion must mean "a person's number is in
    here" exactly — so re-submitting identical values must not claim an edit
    that did not happen.
    """

    payloads: Mapping[str, Mapping[str, Any]]
    changed: bool


def apply_entry_edits(
    family: CatalogFamily,
    entries: Sequence[Mapping[str, Any]],
    edits: Sequence[EntryEdit],
) -> EntryEditOutcome:
    """Validate ``edits`` against ``entries`` and return the resulting payloads.

    ``entries`` are the revision's current rows, each carrying at least
    ``reference_id`` and ``payload``. The returned mapping contains ONLY the
    edited rows — untouched rows are the caller's to leave alone, which is what
    keeps a thousand-point correction curve from being re-sent to change one
    number.
    """

    if not edits:
        raise ReferenceEntryEditError('no edits were supplied')

    if len(edits) > MAX_ENTRY_EDITS_PER_REQUEST:
        raise ReferenceEntryEditError(
            f'{len(edits)} edits in one request exceeds the '
            f'{MAX_ENTRY_EDITS_PER_REQUEST}-row limit. A tester changes cells '
            f'by hand; replacing a whole curve is an import, not an edit, and '
            f'that path keeps its own provenance.'
        )

    seen: set[str] = set()
    for edit in edits:
        if edit.reference_id in seen:
            # Two edits to one row cannot both be honoured, and picking one
            # silently would make the result depend on list order.
            raise ReferenceEntryEditError(
                f'reference_id {edit.reference_id!r} appears more than once; '
                'one row cannot take two edits in a single request'
            )
        seen.add(edit.reference_id)

    current = {str(entry['reference_id']): entry for entry in entries}
    unknown = sorted(edit.reference_id for edit in edits
                     if edit.reference_id not in current)
    if unknown:
        raise ReferenceEntryEditError(
            f'no row in this revision has reference_id {unknown!r}; the edit '
            'was refused rather than skipped, because a skipped edit reports '
            'success while the value never changes'
        )

    expected_fields = tuple(projection_fields_for(family))
    identity_fields = tuple(identity_fields_for(family)[0])

    payloads: dict[str, Mapping[str, Any]] = {}
    changed = False
    for edit in edits:
        existing = dict(current[edit.reference_id]['payload'] or {})
        proposed = dict(edit.payload)

        missing = sorted(set(expected_fields) - set(proposed))
        extra = sorted(set(proposed) - set(expected_fields))
        if missing or extra:
            raise ReferenceEntryEditError(
                f'payload for {edit.reference_id!r} does not match the '
                f'{family.value} runtime row: missing {missing!r}, '
                f'unexpected {extra!r}. The payload IS the row the measurement '
                f'path reads, so a different shape projects a malformed row.'
            )

        retyped = sorted(
            f'{field} ({_json_kind(existing[field])} → '
            f'{_json_kind(proposed[field])})'
            for field in expected_fields
            if existing.get(field) is not None
            and proposed.get(field) is not None
            and _json_kind(existing[field]) != _json_kind(proposed[field])
        )
        if retyped:
            raise ReferenceEntryEditError(
                f'edit to {edit.reference_id!r} changes the KIND of value in '
                f'{retyped!r}, not just the value. An edit replaces a number '
                f'with a number; a cable loss that arrives as text is not a '
                f'smaller number, it is a row the measurement path cannot use, '
                f'and nothing downstream re-parses it. A UI that could not '
                f'read what the tester typed must say so rather than send the '
                f'raw text.'
            )

        moved = sorted(
            field for field in identity_fields
            if _rendered(existing.get(field)) != _rendered(proposed.get(field))
        )
        if moved:
            raise ReferenceEntryEditError(
                f'edit to {edit.reference_id!r} would change identity '
                f'field(s) {moved!r}. Those fields are what makes this row '
                f'that row — changing one is an add plus a remove, and it '
                f'would leave the stored identity_key describing a row that no '
                f'longer exists.'
            )

        payloads[edit.reference_id] = proposed
        if any(_rendered(existing.get(field)) != _rendered(proposed.get(field))
               for field in expected_fields):
            changed = True

    return EntryEditOutcome(payloads=payloads, changed=changed)


def validate_entry_payload_shape(
    family: CatalogFamily, reference_id: str, payload: Any,
) -> Mapping[str, Any]:
    """Refuse a payload that is not this family's runtime row. Returns it.

    The edit path enforced this from the start; **creation did not**, and that
    asymmetry was worse than a missing check (adversarial review, 2026-08-09).
    A revision could be created with a payload that is a list — or any JSON at
    all — because the route binds a bare ``dict`` and nothing downstream looks.
    The row then became **unrepairable through the API**: every later edit,
    including one meant to fix it, crashed reading it, and the crash surfaced
    as a generic 400 that named nothing.

    A malformed row is cheap to refuse at the door and impossible to remove
    afterwards, so the door is where it is refused.

    **Only the type is checked here, not the field set.** The field set has an
    owner already — the projection refuses a payload whose keys do not match
    the runtime row, and the edit path refuses it too. Adding a third judge at
    creation would change what the workbook importer is allowed to submit,
    which is a different decision from closing this defect. What makes the
    wrong TYPE special is that it is the one shape that cannot be repaired
    afterwards: reading it crashes the very operation that would fix it.
    """
    if not isinstance(payload, Mapping):
        raise ReferenceEntryEditError(
            f'payload for {reference_id!r} is {type(payload).__name__}, not an '
            f'object. A payload IS the runtime row the measurement path reads; '
            f'anything else cannot be projected, and a row stored this way '
            f'could not be repaired through this API afterwards — every edit, '
            f'including the one meant to fix it, fails reading it.'
        )
    return payload


def validate_entry_payload_values(
    family: CatalogFamily, reference_id: str, payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Refuse non-null values whose JSON kind disagrees with the runtime row.

    The field contract is already checked by the row-shape policy. This pure
    value-axis check deliberately ignores missing fields and ``None`` so the
    caller can keep shape validation and nullable workbook cells as separate
    facts. Every non-null comparison uses the edit path's ``_json_kind`` SSOT;
    authoring and editing therefore speak the same kind vocabulary.
    """
    fields = projection_fields_for(family)
    expected_kinds = projection_value_kinds_for(family)
    for field, expected_kind in zip(fields, expected_kinds):
        if field not in payload:
            continue
        value = payload[field]
        if value is None:
            continue
        actual_kind = _json_kind(value)
        if actual_kind != expected_kind:
            raise ReferenceEntryPayloadValueError(
                f'payload for {reference_id!r} field {field!r} has JSON kind '
                f'{actual_kind!r}; expected {expected_kind!r} for the '
                f'{family.value} runtime column'
            )
    return payload


def _json_kind(value: Any) -> str:
    """The JSON kind of a payload value — the axis an edit must not move.

    ``bool`` is checked before ``int`` because it IS an ``int`` in Python, and
    folding the two would let ``True`` pass as a number.

    ``int`` and ``float`` are one kind deliberately: 1 → 1.5 is a tester
    refining a measurement, not changing what the column holds. JSON does not
    distinguish them either, so treating them separately would refuse an edit
    the wire cannot even represent as different.

    A ``None`` on either side is not compared at all (see the caller): an empty
    cell has no kind to preserve, and clearing one is a meaningful edit.
    """
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, (int, float)):
        return 'number'
    if isinstance(value, str):
        return 'text'
    if isinstance(value, (list, tuple)):
        return 'list'
    return 'object'


def _rendered(value: Any) -> str:
    """Render a payload value for identity comparison.

    Two corrections to the first version, both found by adversarial review:

    **Numbers compare as numbers.** ``str`` said ``1`` and ``1.0`` were
    different rows. They are the same row, and JSON cannot even tell them
    apart — a browser holds every number as a double, so a stored ``1.0``
    comes back as ``1`` on the very first round trip. Rendering by ``str``
    therefore refused an ordinary re-save of any row whose identity field is
    numeric, which is most correction rows. The identity key stored beside the
    payload keeps its original text; what this guard protects is *which row
    this is*, and two spellings of one number are one row.

    **``None`` is not the empty string.** The first version mapped both to
    ``''``, so an identity field could move ``None → ''`` while the guard saw
    no change — a silent identity move, which is the one thing this function
    exists to prevent. ``identity_key_for`` renders ``None`` as ``'None'``,
    and a distinct token restores the distinction either way.
    """
    if value is None:
        return '\x00null'
    if isinstance(value, bool):
        return f'\x00bool:{value}'
    if isinstance(value, (int, float)):
        # ``repr(float(...))`` is stable and round-trips; the point is only
        # that 1 and 1.0 land on the same token.
        return f'\x00num:{float(value)!r}'
    return str(value)
