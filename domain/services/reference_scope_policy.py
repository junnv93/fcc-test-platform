"""What a reference value belongs to: the site, the room, or the project.

OPERATOR-CONFIRMED FACTS THIS ENCODES (2026-08-06)
--------------------------------------------------
- One PC per shield room, so the chamber identity IS the room identity.
- A single project may be split across rooms — "A프로젝트 BT 는 1방, A프로젝트
  DTS 는 2방". So a project can need TWO different cable-loss sets at once.
- Cable loss is re-measured when the cabling changes, periodically otherwise, and
  is simply carried over when the setup is untouched between back-to-back
  projects.

Those three facts settle the axis for every family, and they rule out the
intuitive answer. Keying cable loss to the *project* looks right — the operator
first said "프로젝트별로 따로" — but a project spanning two rooms would then have
one correction set where it needs two, and one of the two rooms would silently
measure with the other room's cable loss. Keying it to the ROOM is what makes
the split-room case correct, and it also makes "carry it over when nothing
changed" the default behaviour instead of a re-entry chore: nobody has to do
anything and the room keeps its value.

The converse holds too. Analyzer settings and Save Data must NOT be room-scoped,
because the same room runs project A then project B and those values have to
change when the project does.

WHY THE DOMAIN ONLY DECLARES THE AXIS
--------------------------------------
The room's actual identifier (`chamber_id`) is resolved by
``application/headless/session_identity.py``. Domain cannot import application,
and should not: "correction belongs to a room" is a durable rule, while "this
room is called chamber-3" is deployment configuration. This module owns the
former only.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from domain.models.reference_catalog import CatalogFamily
from domain.services.reference_ownership_policy import (
    COUPLED_FAMILY_GROUPS,
    SEED_MANAGED_FAMILIES,
)


__all__ = [
    'ReferenceScopeKind',
    'FAMILY_SCOPE_KIND',
    'ReferenceScope',
    'ReferenceScopeError',
    'scope_kind_for',
    'resolve_scope',
]


class ReferenceScopeError(ValueError):
    """A scope could not be resolved — raised rather than guessed.

    A wrong scope silently applies the wrong cable loss, so this never falls
    back to a default key.
    """


class ReferenceScopeKind(str, Enum):
    """Which axis a family's values vary along.

    There is deliberately no GLOBAL member. One was declared for the Frequency
    Table on the assumption that ``Channel -> Center Frequency`` is pure
    regulation, but the operator corrected it (see FREQUENCY_TABLE below) and
    nothing else needed it. Keeping an unused kind would be structure added for
    a scenario nobody observed, which this repository has already paid for once:
    "관측 없이 가정한 시나리오를 위해 구조를 넣는 것이 가장 비싼 설계 실수".
    Add it back the day a family actually needs it.
    """

    #: Belongs to the measurement path — the cabling physically in one room.
    ROOM = 'room'
    #: Belongs to the device under test / the job.
    PROJECT = 'project'

    def __str__(self) -> str:
        return self.value


FAMILY_SCOPE_KIND: Mapping[CatalogFamily, ReferenceScopeKind] = {
    # Looks like pure regulation (CH6 = 2437MHz) but the sheet carries a second
    # column that is not: `Keystring_Channel` is the DUT test app's on-screen
    # label (e.g. 'Ch37(2402MHz)'), and the operator confirmed it varies with
    # the firmware a sample is running. The repository already tracks per-sample
    # firmware history, so differing labels are expected, not exceptional.
    # Scoping the whole sheet to the project keeps the two columns together at
    # the operator's request; splitting them is recorded as future work.
    #
    # The apparent cost — "a spec revision must now be applied per project" — is
    # the point, not a tax: rewriting a finished project's settings would make
    # its report false, since the report states the settings it measured with.
    CatalogFamily.FREQUENCY_TABLE: ReferenceScopeKind.PROJECT,
    # Which port the signal takes, and how lossy that port is. Both are
    # properties of the cabling bolted into one room.
    CatalogFamily.SWITCH_PORT_MAPPING: ReferenceScopeKind.ROOM,
    CatalogFamily.CORRECTION: ReferenceScopeKind.ROOM,
    # Instrument setup follows the EUT's signal level.
    CatalogFamily.ANALYZER_SETTINGS: ReferenceScopeKind.PROJECT,
    # The gain of the antenna on the device under test.
    CatalogFamily.ANT_GAIN: ReferenceScopeKind.PROJECT,
    # Wave 4 Phase 6 — PROJECT, like the other per-sample axes. Test Info
    # carries Core Select and SAR Tx Power, which are properties of the
    # sample under test; filing them by ROOM would assert that moving a
    # chamber changes them, which is false.
    CatalogFamily.TEST_INFO: ReferenceScopeKind.PROJECT,
}


@dataclass(frozen=True)
class ReferenceScope:
    """The concrete bucket a family's values live in."""

    kind: ReferenceScopeKind
    #: The room id for ROOM; the project id for PROJECT. Never empty.
    key: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ReferenceScopeError(
                f'a {self.kind.value} scope requires a non-empty key'
            )

    def storage_key(self) -> str:
        """The value persisted as the catalog revision's ``scope_id``.

        The kind is derivable from the family, so the key alone is unambiguous
        and the stored value stays readable (a room id, not 'room:chamber-3').
        """
        return self.key


def scope_kind_for(family: CatalogFamily) -> ReferenceScopeKind:
    """Return the axis ``family`` varies along.

    Raises for a family with no runtime lookup table rather than returning a
    default — callers must consult ``SEED_MANAGED_FAMILIES`` first.
    """
    try:
        return FAMILY_SCOPE_KIND[family]
    except KeyError as exc:
        raise ReferenceScopeError(
            f'{family.value} has no declared reference scope'
        ) from exc


def resolve_scope(
    family: CatalogFamily,
    *,
    room_id: str = '',
    project_id: str = '',
) -> ReferenceScope:
    """Pick the bucket for ``family`` from the identifiers in play.

    Only the identifier the family's axis needs is read. Supplying the other one
    is harmless; omitting the needed one raises, because measuring with a
    silently defaulted cable-loss set produces a plausible wrong number rather
    than an error.
    """
    kind = scope_kind_for(family)
    if kind is ReferenceScopeKind.ROOM:
        if not str(room_id).strip():
            raise ReferenceScopeError(
                f'{family.value} is room-scoped but no room was supplied; '
                'refusing to fall back to a shared bucket because that would '
                'apply another room\'s measurement path'
            )
        return ReferenceScope(kind=kind, key=str(room_id).strip())
    if not str(project_id).strip():
        raise ReferenceScopeError(
            f'{family.value} is project-scoped but no project was supplied'
        )
    return ReferenceScope(kind=kind, key=str(project_id).strip())


def _assert_declaration_is_total_and_coherent() -> None:
    """Import-time structural checks.

    These are cheap and catch the two ways this table can rot: a new seed-managed
    family with no declared axis, and a coupled group whose halves drift onto
    different axes (which would make the group untransferable as a unit).
    """
    missing = SEED_MANAGED_FAMILIES - set(FAMILY_SCOPE_KIND)
    if missing:
        raise ReferenceScopeError(
            f'seed-managed families without a declared scope: '
            f'{sorted(f.value for f in missing)}'
        )
    for group in COUPLED_FAMILY_GROUPS:
        kinds = {FAMILY_SCOPE_KIND[f] for f in group if f in FAMILY_SCOPE_KIND}
        if len(kinds) > 1:
            raise ReferenceScopeError(
                f'coupled group {sorted(f.value for f in group)} spans scopes '
                f'{sorted(k.value for k in kinds)}; a group that transfers as a '
                'unit cannot live in two different buckets'
            )


_assert_declaration_is_total_and_coherent()
