"""Who owns each runtime lookup table — the workbook, or the catalog.

THE DIRECTION THIS REVERSES
---------------------------
Historically the workbook was the sole source of truth for the runtime lookup
tables: ``seed_lookup_tables`` deleted all five and re-read them from Excel
whenever the file's mtime changed. That makes the database a cache, and it makes
any edit to the database temporary — the next workbook touch destroys it.

Electrifying the workbook into the UI requires the opposite direction: the
database owns the values and the workbook becomes a one-time import source.
Flipping that globally would be a single enormous cutover across five tables
that feed measurement arithmetic. This module makes the flip **per family**
instead, so it can happen one family at a time with the rest untouched.

A family is workbook-owned by default. It becomes catalog-owned only when a
PUBLISHED catalog revision exists for it. With zero published revisions — the
state of every existing database and every existing test — the catalog-owned
set is empty and the seeding path is exactly what it was.

WHY CORRECTION AND SWITCH PORT MAPPING TRANSFER TOGETHER
--------------------------------------------------------
`Switch port mapping` answers *which path the signal takes*; `Correction`
answers *how lossy that path is*. Re-cabling the chamber changes both. If only
one of them moved to catalog ownership, the runtime would pair antenna 1's
signal with antenna 2's path loss — and the measurement would complete
normally, produce a verdict, and be silently wrong by the difference between
two cables. Nothing downstream can detect that; the number looks plausible.

So the two are declared a coupled group and ownership transfers as a unit.
Publishing only one leaves BOTH workbook-owned with a typed reason, which is
the conservative direction: the operator sees an unapplied publication rather
than a wrong number.

PURITY
------
Domain-pure: stdlib typing + ``domain.models.reference_catalog`` only. No
infrastructure, sqlalchemy, pandas, openpyxl or PySide6 import. The ORM column
sets this module's field contract must match live in infrastructure, so the
correspondence is asserted from the test side rather than imported here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from domain.models.reference_catalog import CatalogFamily


__all__ = [
    'LookupFamilyOwner',
    'SEED_MANAGED_FAMILIES',
    'COUPLED_FAMILY_GROUPS',
    'PROJECTION_FIELD_CONTRACT',
    'PROJECTION_VALUE_KIND_CONTRACT',
    'AUXILIARY_FIELD_CONTRACT',
    'OwnershipRefusalCode',
    'OwnershipRefusal',
    'OwnershipResolution',
    'resolve_lookup_ownership',
    'workbook_ownership_fingerprint',
    'projection_fields_for',
    'projection_value_kinds_for',
    'table_contracts_for',
    'DEFAULT_REFERENCE_PROFILE_ID',
    'IDENTITY_FIELD_CONTRACT',
    'identity_fields_for',
    'identity_key_for',
    'ReferenceSourceVerdict',
    'ReferenceUnresolutionGrade',
    'ReferenceUnresolutionCost',
    'ReferenceSourceDecision',
    'ReferenceSourceResolution',
    'REFERENCE_UNRESOLUTION_COST',
    'resolve_reference_sources',
]


class LookupFamilyOwner(str, Enum):
    """Which writer is allowed to populate a runtime lookup table."""

    WORKBOOK = 'workbook'
    CATALOG = 'catalog'

    def __str__(self) -> str:
        return self.value


# The catalog models eight families, but only these six have a runtime lookup
# table that ``seed_lookup_tables`` populates. The two that do not are absent for
# TWO DIFFERENT REASONS, and collapsing them into one sentence gets both wrong:
#
#   * POWER_METER_SETTINGS is a REAL workbook sheet. It has no runtime table
#     because it has no CONSUMER — nothing under ``src/`` reads that sheet's
#     values. Promoting it would add an empty table and force a frozen-digest
#     recapture: it buys nothing while moving the oracle.
#   * TEST_CONDITION is NOT A SHEET at all (no ``WorkbookSheetName`` member). It
#     is the catalog's internal cross-reference axis — which test conditions an
#     entry cites — so it is not the KIND of thing a lookup table holds.
#
# Both are also refused at authoring time: neither has a scope axis, so
# ``scope_kind_for`` raises and ``create_candidate`` rejects them outright.
# Sealed by ``tests/test_reference_ownership_policy.py::
# TestCatalogOnlyFamiliesHaveNoMeasurementReader``, which pins the REASONS (not
# just the membership) and trips if either family gains a scope axis.
SEED_MANAGED_FAMILIES: frozenset[CatalogFamily] = frozenset({
    CatalogFamily.FREQUENCY_TABLE,
    CatalogFamily.ANT_GAIN,
    CatalogFamily.ANALYZER_SETTINGS,
    CatalogFamily.CORRECTION,
    CatalogFamily.SWITCH_PORT_MAPPING,
    # Wave 4 Phase 6. Test Info was the last reference sheet with no runtime
    # table, so a published revision could never reach it — the electrification
    # stopped one sheet short of the set it was meant to cover.
    CatalogFamily.TEST_INFO,
})

# Families that describe two halves of one physical fact and therefore cannot
# be owned independently. See the module docstring for why splitting them is
# silently wrong rather than loudly wrong.
COUPLED_FAMILY_GROUPS: tuple[frozenset[CatalogFamily], ...] = (
    frozenset({CatalogFamily.CORRECTION, CatalogFamily.SWITCH_PORT_MAPPING}),
)

# The runtime row shape per family, in column order. A published entry's payload
# IS a runtime row — deliberately not a second schema, because a second schema
# drifts from the ORM the moment either side is edited. The test asserts each
# tuple equals the corresponding ORM model's column set minus the surrogate id.
PROJECTION_FIELD_CONTRACT: Mapping[CatalogFamily, tuple[str, ...]] = {
    CatalogFamily.FREQUENCY_TABLE: (
        'technologies', 'band', 'bandwidth', 'channel', 'center_frequency',
        'keystring_channel',
    ),
    CatalogFamily.ANT_GAIN: (
        'band', 'antenna', 'directional_gain',
    ),
    CatalogFamily.ANALYZER_SETTINGS: (
        'test_type', 'technology', 'band', 'bandwidth', 'channel', 'tone',
        'attenuation', 'reference_level', 'sweep_points', 'sweep_time', 'rbw',
        'vbw', 'span', 'average_type', 'sweep_counts', 'limit_value',
        'trace_mode', 'det_type', 'ndb_down', 'start_frequency',
        'stop_frequency', 'be_direction', 'threshold', 'wait_time',
        'offset_sweep_time',
    ),
    CatalogFamily.CORRECTION: (
        'correction_index', 'frequency_hz', 'correction_db',
    ),
    CatalogFamily.SWITCH_PORT_MAPPING: (
        'technology', 'antenna', 'band', 'port',
    ),
    # Wave 4 Phase 6. The sheet is sparse by design — a WLAN row carries
    # preamble/payload/tx_rate and no core select, a BT row the reverse — so the
    # contract is the UNION of what the three consumers read, not the
    # intersection. A narrower contract would drop the column one consumer needs.
    CatalogFamily.TEST_INFO: (
        'technology', 'modulation', 'band', 'bandwidth', 'antenna', 'tone',
        'preamble', 'payload', 'tx_rate', 'core_select', 'sar_tx_power',
    ),
}

# The second coordinate of the runtime-row contract: the exact tokens emitted
# by ``reference_entry_edit_policy._json_kind``. Fields stay parallel to
# ``PROJECTION_FIELD_CONTRACT`` so the domain does not duplicate field names or
# import the ORM. The test derives these tokens from ORM column ``python_type``
# and asserts parity; this declaration is the pure value contract used by the
# authoring boundary.
PROJECTION_VALUE_KIND_CONTRACT: Mapping[CatalogFamily, tuple[str, ...]] = {
    CatalogFamily.FREQUENCY_TABLE: (
        'text', 'text', 'text', 'text', 'text', 'text',
    ),
    CatalogFamily.ANT_GAIN: (
        'text', 'text', 'number',
    ),
    CatalogFamily.ANALYZER_SETTINGS: (
        'text', 'text', 'text', 'text', 'text', 'text', 'text', 'text',
        'number', 'text', 'text', 'text', 'text', 'text', 'number', 'number',
        'text', 'text', 'number', 'number', 'number', 'text', 'number',
        'number', 'text',
    ),
    CatalogFamily.CORRECTION: (
        'number', 'number', 'number',
    ),
    CatalogFamily.SWITCH_PORT_MAPPING: (
        'text', 'text', 'text', 'number',
    ),
    CatalogFamily.TEST_INFO: (
        'text', 'text', 'text', 'text', 'text', 'text', 'text', 'text',
        'text', 'text', 'text',
    ),
}

# A family may own MORE than one runtime table. PROJECTION_FIELD_CONTRACT above
# declares the PRIMARY one — the table that carries the measurement values — and
# this declares the rest, keyed by table name.
#
# Correction is the case that forced it. Each `Correction{n}` sheet declares six
# fields about itself (file type, description, comment, frequency unit,
# transducer unit, interpolation) which become the header of the CSV uploaded to
# the analyzer. They are per-SHEET, not per-row, so they cannot live in
# `corrections` without duplicating a single fact across every frequency point —
# and widening that three-column contract would change the shape of a
# measurement row.
#
# The alternative, a seventh CatalogFamily, is worse and for a specific reason:
# the metadata and the points are the header and the body of ONE CSV, one
# physical fact. Splitting them across families would make it possible to publish
# the metadata alone and produce a file whose units come from one revision and
# whose points come from the workbook — exactly the mismatch COUPLED_FAMILY_GROUPS
# exists to prevent, reproduced by the very mechanism meant to prevent it. Kept
# under one family, projection replaces both tables inside one transaction and
# that state is structurally unreachable.
#
# Entries do NOT carry a discriminator. A payload IS a runtime row, and adding a
# table tag would break that. Routing is by SHAPE: the projection matches a
# payload's key set against each of the family's contracts, which is unambiguous
# because no two contracts of one family may share a key set (asserted below).
# Deliberately UNNAMED. Table names are an infrastructure concept, and naming
# them here would put storage vocabulary in the domain — some of those names even
# coincide with family values, so a reader could not tell which was meant. A
# contract is identified by its field set, which is the same key routing uses.
AUXILIARY_FIELD_CONTRACT: Mapping[CatalogFamily, tuple[tuple[str, ...], ...]] = {
    CatalogFamily.CORRECTION: (
        (
            'correction_index', 'file_type', 'description', 'comment',
            'freq_unit', 'transducer_unit', 'interpolation',
        ),
    ),
}


class OwnershipRefusalCode(str, Enum):
    """Why a published family did not take ownership."""

    #: A coupled group was only partially published.
    COUPLED_GROUP_INCOMPLETE = 'coupled_group_incomplete'
    #: Published, but the family has no runtime lookup table to own.
    NOT_SEED_MANAGED = 'not_seed_managed'

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class OwnershipRefusal:
    """One published family that stays workbook-owned, and why."""

    family: CatalogFamily
    code: OwnershipRefusalCode
    detail: str


@dataclass(frozen=True)
class OwnershipResolution:
    """The per-family ownership decision for one seeding run."""

    catalog_owned: frozenset[CatalogFamily]
    workbook_owned: frozenset[CatalogFamily]
    refusals: tuple[OwnershipRefusal, ...]

    def owner_of(self, family: CatalogFamily) -> LookupFamilyOwner:
        return (
            LookupFamilyOwner.CATALOG
            if family in self.catalog_owned
            else LookupFamilyOwner.WORKBOOK
        )


def _group_containing(family: CatalogFamily) -> frozenset[CatalogFamily] | None:
    for group in COUPLED_FAMILY_GROUPS:
        if family in group:
            return group
    return None


#: Separator for the ownership fingerprint. A comma cannot occur in a
#: ``CatalogFamily`` value (they are lowercase snake_case), so the joined string
#: is unambiguous and no escaping rule is needed.
OWNERSHIP_FINGERPRINT_SEPARATOR = ','


def workbook_ownership_fingerprint(families: Iterable[CatalogFamily]) -> str:
    """A stable, order-free identity for a workbook-owned family set.

    WHY THE SEEDER NEEDS THIS
    -------------------------
    ``seed_lookup_tables`` skips its work when the workbook's mtime is unchanged.
    That gate is correct for the question it was written to answer — "has the
    workbook changed?" — and silently wrong for the one that decides what the
    seeder does: WHICH FAMILIES the workbook still owns. Retiring a published
    revision hands a family back to the workbook without touching any file, so
    the gate short-circuits and the rows the retired revision projected keep
    being served. Comparing this fingerprint alongside the mtime closes that.

    It is a set identity, not a revision identity. Which revision a family was
    published from does not change what the seeder must do; only whether the
    workbook owns the family does, and that is what decides the delete-and-reload
    set. Recording more would invite re-seeding on changes that cannot affect
    the outcome.

    Sorting is what makes it order-free — ``frozenset`` iteration order is not
    guaranteed across processes, and an unsorted join would make an unchanged
    set look changed on some runs and not others.
    """
    return OWNERSHIP_FINGERPRINT_SEPARATOR.join(
        sorted(family.value for family in families)
    )


def resolve_lookup_ownership(
    published_families: Iterable[CatalogFamily],
) -> OwnershipResolution:
    """Decide who owns each seed-managed lookup family.

    ``published_families`` names the families that currently have a PUBLISHED
    catalog revision. Everything else — and anything refused below — stays
    workbook-owned, which is the pre-existing behaviour.

    Passing an empty iterable (the state of every database that has never
    published a revision) yields an empty catalog-owned set. That is the
    contract the byte-identical no-regression guarantee rests on.
    """
    published = frozenset(published_families)
    refusals: list[OwnershipRefusal] = []
    catalog_owned: set[CatalogFamily] = set()

    for family in sorted(published, key=lambda f: f.value):
        if family not in SEED_MANAGED_FAMILIES:
            refusals.append(OwnershipRefusal(
                family=family,
                code=OwnershipRefusalCode.NOT_SEED_MANAGED,
                detail=(
                    f'{family.value} has no runtime lookup table; publishing it '
                    'does not change seeding'
                ),
            ))
            continue

        group = _group_containing(family)
        if group is not None and not group.issubset(published):
            missing = sorted(f.value for f in group - published)
            refusals.append(OwnershipRefusal(
                family=family,
                code=OwnershipRefusalCode.COUPLED_GROUP_INCOMPLETE,
                detail=(
                    f'{family.value} is coupled to {missing} which are not '
                    'published; applying one half would pair a signal path with '
                    'the wrong path loss and the measurement would be silently '
                    'wrong. The whole group stays workbook-owned.'
                ),
            ))
            continue

        catalog_owned.add(family)

    return OwnershipResolution(
        catalog_owned=frozenset(catalog_owned),
        workbook_owned=frozenset(SEED_MANAGED_FAMILIES - catalog_owned),
        refusals=tuple(refusals),
    )


def projection_fields_for(family: CatalogFamily) -> tuple[str, ...]:
    """Return the PRIMARY runtime row field order for ``family``.

    Raises ``KeyError`` for a family with no runtime lookup table — callers must
    consult ``SEED_MANAGED_FAMILIES`` first rather than receive a silent empty
    tuple that would project zero columns.

    This deliberately stays single-table. It is the accessor the extraction
    equivalence oracle uses, and its meaning — "the columns of the table that
    carries this family's measurement values" — did not change when a family
    gained a second table. Callers that need every table use
    :func:`table_contracts_for`.
    """
    return PROJECTION_FIELD_CONTRACT[family]


def projection_value_kinds_for(family: CatalogFamily) -> tuple[str, ...]:
    """Return expected JSON-kind tokens parallel to ``projection_fields_for``."""
    return PROJECTION_VALUE_KIND_CONTRACT[family]


def _assert_value_kind_contracts_are_parallel() -> None:
    """Keep the value axis total and positionally aligned with the field axis."""
    if set(PROJECTION_VALUE_KIND_CONTRACT) != set(PROJECTION_FIELD_CONTRACT):
        raise KeyError(
            'every projectable family must declare value kinds: missing '
            f'{sorted(f.value for f in set(PROJECTION_FIELD_CONTRACT) - set(PROJECTION_VALUE_KIND_CONTRACT))}'
        )
    for family, fields in PROJECTION_FIELD_CONTRACT.items():
        kinds = PROJECTION_VALUE_KIND_CONTRACT[family]
        if len(fields) != len(kinds):
            raise ValueError(
                f'{family.value} declares {len(fields)} projection fields but '
                f'{len(kinds)} value kinds; the two axes are parallel by position'
            )


_assert_value_kind_contracts_are_parallel()


def table_contracts_for(family: CatalogFamily) -> tuple[tuple[str, ...], ...]:
    """Return every row contract ``family`` owns, PRIMARY FIRST.

    Order is the correspondence to the infrastructure's model list, so it is
    load-bearing: position 0 is the table carrying measurement values. It is not
    load-bearing for atomicity — projection replaces all of a family's tables
    inside one transaction, so no partial state is observable regardless of the
    order they are written in.
    """
    return (
        (PROJECTION_FIELD_CONTRACT[family],)
        + tuple(AUXILIARY_FIELD_CONTRACT.get(family, ()))
    )


def _assert_table_contracts_are_unambiguous() -> None:
    """Import-time checks for the two ways multi-table families can rot.

    A payload is routed to a table by matching its key set, so two tables of the
    same family sharing a key set would make routing a coin flip. And an
    auxiliary declared for a family with no primary table would project columns
    into nothing.
    """
    for family in AUXILIARY_FIELD_CONTRACT:
        if family not in PROJECTION_FIELD_CONTRACT:
            raise KeyError(
                f'{family.value} declares auxiliary tables but has no primary '
                'row contract; there is nothing for them to be auxiliary to'
            )
    for family in PROJECTION_FIELD_CONTRACT:
        seen: set[frozenset[str]] = set()
        for index, fields in enumerate(table_contracts_for(family)):
            if not fields:
                raise ValueError(
                    f'{family.value} contract {index} declares no fields'
                )
            key = frozenset(fields)
            if key in seen:
                raise ValueError(
                    f'{family.value} declares two row contracts with the same '
                    'field set; projection routes payloads by shape, so a row '
                    'could not be assigned to one of them'
                )
            seen.add(key)


_assert_table_contracts_are_unambiguous()


# --------------------------------------------------------------------------- #
# Identity — which fields of a row NAME it (Wave 4, 2026-08-07)
# --------------------------------------------------------------------------- #

#: The profile a workbook import files its revisions under. One profile exists
#: today; the column is in the schema because a second one is foreseeable (a
#: chamber running two cabling configurations), not because anything varies it
#: yet. Spelled once so an importer cannot invent a different default and file a
#: revision nobody looks up.
DEFAULT_REFERENCE_PROFILE_ID = 'default'


#: Per table contract, the subset of fields that IDENTIFY a row — parallel to
#: ``table_contracts_for(family)``, position for position.
#:
#: These are DERIVED FROM THE ACTUAL LOOKUP MATCHING, not chosen for tidiness,
#: and getting them wrong is wrong in both directions. Too narrow and an importer
#: reports a healthy workbook as having duplicate rows and refuses it. Too wide
#: and a genuine duplicate passes — which matters because every consumer here
#: takes the FIRST match, so a second row with the same key is not an alternative
#: reading, it is a row that can never be reached.
#:
#: Where the match is fuzzy the identity is still the row's LITERAL stored value:
#: ``ant_gain`` matches ``band in split(';')`` and ``antenna`` by substring, so two
#: rows literally equal in those cells are duplicates regardless of how many
#: queries each would have answered.
IDENTITY_FIELD_CONTRACT: Mapping[CatalogFamily, tuple[tuple[str, ...], ...]] = {
    # lookup_store.get_frequency_entry: bandwidth == , channel == (canonical),
    # technologies == or LIKE. Band is carried for the caller's own filtering.
    CatalogFamily.FREQUENCY_TABLE: (
        ('technologies', 'band', 'bandwidth', 'channel'),
    ),
    # power_judgment.calculate_antenna_gain_with_status: band in split(';') and
    # antenna by substring. Directional gain is the value being looked up.
    CatalogFamily.ANT_GAIN: (
        ('band', 'antenna'),
    ),
    # lookup_store.get_analyzer_settings: test_type == , technology/band/bandwidth
    # LIKE-or-NULL, channel/tone ';'-membership-or-NULL. Everything after these
    # six is an instrument setting, i.e. the answer rather than the question.
    CatalogFamily.ANALYZER_SETTINGS: (
        ('test_type', 'technology', 'band', 'bandwidth', 'channel', 'tone'),
    ),
    # lookup_store.get_corrections: correction_index ==, ordered by frequency.
    # The sheet metadata row is keyed by the sheet alone — one header per sheet.
    CatalogFamily.CORRECTION: (
        ('correction_index', 'frequency_hz'),
        ('correction_index',),
    ),
    # test_plan_lookup.get_switch_port: technology == , antenna == , and band by
    # ';'-membership for 11ax/11be with a band-free recursive fallback. Band is
    # part of the identity because two rows differing only in band are two
    # distinct mappings, not a duplicate.
    CatalogFamily.SWITCH_PORT_MAPPING: (
        ('technology', 'antenna', 'band'),
    ),
    # Every axis the three consumers match on, and nothing they return.
    # ``select_best_wlan_index`` scores technology/modulation/band/bandwidth with
    # antenna and tone as optional refinements; ``get_core_select`` and
    # ``get_sar_tx_power`` filter on technology and antenna and then test band
    # membership. The union of those is the identity; preamble, payload, tx_rate,
    # core_select and sar_tx_power are answers, so including one would make two
    # rows that answer differently for the same question look distinct.
    CatalogFamily.TEST_INFO: (
        ('technology', 'modulation', 'band', 'bandwidth', 'antenna', 'tone'),
    ),
}


def identity_fields_for(family: CatalogFamily) -> tuple[tuple[str, ...], ...]:
    """Identity subsets for ``family``, parallel to ``table_contracts_for``."""
    return IDENTITY_FIELD_CONTRACT[family]


def identity_key_for(
    family: CatalogFamily, row: Mapping[str, Any], *, contract_index: int = 0,
) -> str:
    """Name one row by the fields that identify it. Pure and deterministic.

    The rendering is deliberately boring — ``family|field=value|…`` with fields in
    declared order — because this string is a database key and a diff join key,
    not a display value. Values are rendered with ``str`` and never normalized:
    the row was already canonicalized by the shared row builders, and a second
    normalizer here would be free to disagree with the seeding path about what
    ``0`` and ``'0.0'`` mean.
    """
    fields = identity_fields_for(family)[contract_index]
    missing = [field for field in fields if field not in row]
    if missing:
        raise KeyError(
            f'{family.value} row is missing identity field(s) {missing}; the row '
            'builders produce every contract field, so a row without one did not '
            'come from them'
        )
    rendered = '|'.join(f'{field}={row[field]}' for field in fields)
    return f'{family.value}|{rendered}'


def _assert_identity_contract_is_total_and_grounded() -> None:
    """Import-time checks that the identity declaration cannot silently rot.

    Two failure modes, both quiet: a family that gains a table without gaining an
    identity for it (so the importer would key every row of that table the same
    way), and an identity naming a field the row contract does not contain (so
    the key would be built from something the builders never produce).
    """
    if set(IDENTITY_FIELD_CONTRACT) != set(PROJECTION_FIELD_CONTRACT):
        raise KeyError(
            'every projectable family must declare an identity: missing '
            f'{sorted(f.value for f in set(PROJECTION_FIELD_CONTRACT) - set(IDENTITY_FIELD_CONTRACT))}'
        )
    for family, identities in IDENTITY_FIELD_CONTRACT.items():
        contracts = table_contracts_for(family)
        if len(identities) != len(contracts):
            raise ValueError(
                f'{family.value} declares {len(contracts)} row contract(s) but '
                f'{len(identities)} identity subset(s); they are parallel by '
                'position, so a mismatch would key a table by another table\'s fields'
            )
        for index, (identity, fields) in enumerate(zip(identities, contracts)):
            if not identity:
                raise ValueError(
                    f'{family.value} contract {index} declares an empty identity; '
                    'every row would then be the same row'
                )
            extra = set(identity) - set(fields)
            if extra:
                raise ValueError(
                    f'{family.value} contract {index} identity names field(s) '
                    f'{sorted(extra)} that the row contract does not contain'
                )


_assert_identity_contract_is_total_and_grounded()


# --------------------------------------------------------------------------- #
# Boot-time source resolution (2026-08-13)
# --------------------------------------------------------------------------- #
#
# THE DECISION THIS SECTION SUPPORTS
# -----------------------------------
# A node no longer requires a workbook to boot -- the plan arrives by
# ``published_plan_id``, reference values by the central PULL, and equipment
# endpoints/storage root by chamber settings. But the workbook was also the
# LAST-RESORT FALLBACK for the runtime lookup tables (see
# ``reference_lookup_materializer``): every materializer site falls back to
# the workbook frame when its table reads zero rows. Remove the workbook
# without a replacement for that fallback and "table empty, no workbook" -- a
# state no real session could previously reach -- becomes reachable, with
# every antenna gain 0.0, every path loss 0, and the session completing and
# reporting success. This section names that state so a typed refusal can be
# raised at it instead of a wrong number.
#
# FOUR VERDICTS, NOT THREE
# -------------------------
# A family's table row count answers "is there something to read" and
# ``workbook_present`` answers "is there a fallback". Crossing them gives
# three answers -- TABLE, WORKBOOK, UNRESOLVED -- but one family
# (``ANALYZER_SETTINGS``) has no row-count reader at all (see
# ``reference_lookup_materializer.REFERENCE_ROW_COUNT_READERS``), so its row
# count is not "zero", it is "unknown". Folding unknown into UNRESOLVED would
# permanently block every session on a family this module cannot actually
# observe -- treating "cannot see" as "looked and found nothing" is the same
# collapse ``resolve_ant_gain_source`` warns against for the opposite pair. So
# UNOBSERVED is its own verdict, and it is excluded from
# ``ReferenceSourceResolution.unresolved`` -- the set a boot-time gate blocks
# on.
#
# GRADES DESCRIBE, THEY DO NOT DECIDE
# --------------------------------------
# Every seed-managed family's cost-if-unresolved is declared once, whether or
# not it is actually unresolved this run, so a decision carries its own cost
# and a caller building a message never looks the family up a second time.
# The grade is TAGGED when the wrongness announces itself in the output
# (``ant_gain``'s per-row missing-gain tag), HALTING when an unresolved family
# stops a test item rather than mis-measuring it (``FREQUENCY_TABLE`` /
# ``ANALYZER_SETTINGS`` -- no center frequency or instrument settings, no
# dispatch; ``TEST_INFO`` -- the item is popped off the queue unmeasured), and
# QUIET when neither is true: ``CORRECTION`` moves every reading in a test by
# the uncorrected path loss with no per-row trace, and
# ``SWITCH_PORT_MAPPING`` is QUIET for the reason ``COUPLED_FAMILY_GROUPS``
# exists -- an unresolved mapping pairs one antenna's signal with another
# antenna's path loss. This wave does not branch on grade (that is a later,
# separate decision); it only names the cost so a boot log line can be honest
# about it.


class ReferenceSourceVerdict(str, Enum):
    """Where a family's runtime rows would come from at session boot."""

    TABLE = 'table'
    WORKBOOK = 'workbook'
    UNRESOLVED = 'unresolved'
    UNOBSERVED = 'unobserved'

    def __str__(self) -> str:
        return self.value


class ReferenceUnresolutionGrade(str, Enum):
    """How loudly an unresolved family's wrongness would show up."""

    #: Visible per row (e.g. a missing-gain tag on the affected entries).
    TAGGED = 'tagged'
    #: Invisible -- a number moves with no trace of why.
    QUIET = 'quiet'
    #: The item stops rather than being mis-measured.
    HALTING = 'halting'

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ReferenceUnresolutionCost:
    """What being unresolved would cost, for one family. Declared, not derived."""

    family: CatalogFamily
    grade: ReferenceUnresolutionGrade
    reason: str


@dataclass(frozen=True)
class ReferenceSourceDecision:
    """One family's resolved verdict for one boot, and its declared cost."""

    family: CatalogFamily
    verdict: ReferenceSourceVerdict
    rows: int | None
    cost: ReferenceUnresolutionCost


@dataclass(frozen=True)
class ReferenceSourceResolution:
    """The per-family boot-time source decision for one session."""

    decisions: tuple[ReferenceSourceDecision, ...]
    workbook_present: bool

    @property
    def unresolved(self) -> tuple[CatalogFamily, ...]:
        return tuple(
            d.family for d in self.decisions
            if d.verdict is ReferenceSourceVerdict.UNRESOLVED
        )

    @property
    def unobserved(self) -> tuple[CatalogFamily, ...]:
        return tuple(
            d.family for d in self.decisions
            if d.verdict is ReferenceSourceVerdict.UNOBSERVED
        )

    def verdict_of(self, family: CatalogFamily) -> ReferenceSourceVerdict:
        for decision in self.decisions:
            if decision.family is family:
                return decision.verdict
        raise KeyError(f'{family.value} was not part of this resolution')


#: Declared, reasoned cost-if-unresolved for every seed-managed family. See the
#: "GRADES DESCRIBE, THEY DO NOT DECIDE" note above for what each grade means.
REFERENCE_UNRESOLUTION_COST: Mapping[CatalogFamily, ReferenceUnresolutionCost] = {
    CatalogFamily.ANT_GAIN: ReferenceUnresolutionCost(
        family=CatalogFamily.ANT_GAIN,
        grade=ReferenceUnresolutionGrade.TAGGED,
        reason=(
            'surfaces per row -- emit_ant_gain_validation_warning_for_records '
            'tags missing entries, so an unresolved family is visible in the '
            'output a session produces, not only in the log'
        ),
    ),
    CatalogFamily.CORRECTION: ReferenceUnresolutionCost(
        family=CatalogFamily.CORRECTION,
        grade=ReferenceUnresolutionGrade.QUIET,
        reason=(
            'no curve to upload means every reading in the test moves by the '
            'uncorrected path loss, with no per-row trace of why -- the '
            'quiet-wrong-number case this whole axis exists to remove'
        ),
    ),
    CatalogFamily.SWITCH_PORT_MAPPING: ReferenceUnresolutionCost(
        family=CatalogFamily.SWITCH_PORT_MAPPING,
        grade=ReferenceUnresolutionGrade.QUIET,
        reason=(
            'coupled to CORRECTION via COUPLED_FAMILY_GROUPS -- an unresolved '
            "mapping pairs one antenna's signal with another antenna's path "
            'loss, which is exactly the mismatch that coupling exists to '
            'prevent, reproduced by the boot path instead of the publish path'
        ),
    ),
    CatalogFamily.TEST_INFO: ReferenceUnresolutionCost(
        family=CatalogFamily.TEST_INFO,
        grade=ReferenceUnresolutionGrade.HALTING,
        reason=(
            'no Test Info row means no core select / preamble / payload to '
            'score against, so the WLAN item is popped off the queue '
            'unmeasured rather than measured wrong'
        ),
    ),
    CatalogFamily.FREQUENCY_TABLE: ReferenceUnresolutionCost(
        family=CatalogFamily.FREQUENCY_TABLE,
        grade=ReferenceUnresolutionGrade.HALTING,
        reason=(
            'no center frequency to tune to -- the orchestrator logs '
            "'Missing parameters' and returns without dispatching the item"
        ),
    ),
    CatalogFamily.ANALYZER_SETTINGS: ReferenceUnresolutionCost(
        family=CatalogFamily.ANALYZER_SETTINGS,
        grade=ReferenceUnresolutionGrade.HALTING,
        reason=(
            'no instrument settings to apply -- the same Missing-parameters '
            'halt as FREQUENCY_TABLE, and today unobservable at boot (see '
            'REFERENCE_ROW_COUNT_READERS) rather than resolved to zero'
        ),
    ),
}


def _assert_unresolution_cost_is_total_and_reasoned() -> None:
    """Import-time check: every seed-managed family has a declared, reasoned cost.

    Two failure modes, both quiet: a family added to ``SEED_MANAGED_FAMILIES``
    with no cost declared here (a boot-time decision about it would have
    nothing to report), and a declared cost with an empty ``reason`` (the
    declaration exists but explains nothing -- indistinguishable from having
    forgotten it).
    """
    declared = frozenset(REFERENCE_UNRESOLUTION_COST)
    if declared != SEED_MANAGED_FAMILIES:
        raise KeyError(
            'every seed-managed family must declare an unresolution cost: '
            f'missing {sorted(f.value for f in SEED_MANAGED_FAMILIES - declared)}'
        )
    for family, cost in REFERENCE_UNRESOLUTION_COST.items():
        if not cost.reason.strip():
            raise ValueError(
                f'{family.value} declares an unresolution cost with no reason'
            )


_assert_unresolution_cost_is_total_and_reasoned()


def resolve_reference_sources(
    observations: Mapping[CatalogFamily, int | None], *, workbook_present: bool,
) -> ReferenceSourceResolution:
    """Decide, per seed-managed family, where this boot's rows come from.

    ``observations`` must cover exactly ``SEED_MANAGED_FAMILIES``. A partial
    observation is a caller defect -- an observer that skipped a family -- not
    a session state, so it raises rather than defaulting the missing family to
    any verdict.

    ============  =================  ==========
    rows          workbook_present   verdict
    ============  =================  ==========
    > 0           any                TABLE
    0             True               WORKBOOK
    0             False              UNRESOLVED
    None          any                UNOBSERVED
    ============  =================  ==========
    """
    observed = set(observations)
    if observed != SEED_MANAGED_FAMILIES:
        raise KeyError(
            'resolve_reference_sources requires an observation for every '
            'seed-managed family: missing '
            f'{sorted(f.value for f in SEED_MANAGED_FAMILIES - observed)}'
        )

    decisions: list[ReferenceSourceDecision] = []
    for family in sorted(SEED_MANAGED_FAMILIES, key=lambda f: f.value):
        rows = observations[family]
        if rows is None:
            verdict = ReferenceSourceVerdict.UNOBSERVED
        elif rows > 0:
            verdict = ReferenceSourceVerdict.TABLE
        elif workbook_present:
            verdict = ReferenceSourceVerdict.WORKBOOK
        else:
            verdict = ReferenceSourceVerdict.UNRESOLVED
        decisions.append(ReferenceSourceDecision(
            family=family, verdict=verdict, rows=rows,
            cost=REFERENCE_UNRESOLUTION_COST[family],
        ))

    return ReferenceSourceResolution(
        decisions=tuple(decisions), workbook_present=workbook_present,
    )
