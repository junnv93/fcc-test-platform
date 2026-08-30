"""Typed reference-catalog revision models.

The catalog is intentionally a collection of typed reference entries and
stable IDs, not a 298-column workbook mirror.  Workbook snapshots are import
provenance; a catalog revision is the independently reviewable operational
candidate that may eventually be published.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from importlib.resources import files as _resource_files
from typing import Any, Mapping, Optional


_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
_REFERENCE_CATALOG_CURSOR_VERSION = 'reference-catalog/v2'
_REFERENCE_CATALOG_CURSOR_KINDS = frozenset({'entries', 'diff'})

# Administration reads are deliberately bounded.  The contract's deep-page
# budget is 250 rows; callers may choose a smaller page but cannot opt into an
# unbounded catalog or diff hydration.
REFERENCE_CATALOG_DEFAULT_LIMIT = 100
REFERENCE_CATALOG_MAX_LIMIT = 250


class _ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class RevisionState(_ValueEnum):
    CANDIDATE = 'CANDIDATE'
    PUBLISHED = 'PUBLISHED'
    RETIRED = 'RETIRED'


class RevisionProvenanceKind(_ValueEnum):
    """How this revision's values came to exist — stated, never inferred.

    ``source_snapshot_id`` answers *where this edition started*, and for every
    revision that has ever existed that was also *where the values came from*,
    because the only way to make one was to import a workbook.  Web authoring
    splits those two questions: fork workbook snapshot X, change one port's
    cable loss, and the link to X is still the honest starting point while some
    values are no longer X's.

    Recording the distinction in its own column rather than leaving the reader
    to infer it from ``forked_from_revision_id`` is the whole point.  A field
    that quietly acquired a second meaning is the shape this repository has
    paid for repeatedly, and this axis carries audit evidence.

    The lattice is monotone and each step is a fact, not an assumption:

    * a workbook import is ``WORKBOOK``;
    * a fork **inherits** — a copy nobody edited genuinely still holds the
      workbook's values, and claiming otherwise would overstate;
    * an entry edit **promotes** to ``FORK_EDIT`` at the moment a value
      actually changes, so the column means "a person's number is in here"
      exactly and never approximately.

    Inheritance is what makes a grandchild honest: forked from an edited
    edition, its values did not come from the workbook either.

    ``WEB_AUTHORED`` (2026-08-11) is the third and last kind the lattice needs.
    Once the workbook stops being something a tester opens, a revision can be
    born on the web with no workbook behind it at all — and that revision's
    values did not come from a workbook, nor from editing one.  Leaving it
    labelled ``WORKBOOK`` would be a false audit record for exactly the values
    that have the least paper trail; labelling it ``FORK_EDIT`` would claim an
    ancestry it does not have.

    It is a **terminal** state, not a step: editing a web-authored candidate
    does not promote it to ``FORK_EDIT``, because its values still did not come
    from a workbook edit.  ``promote_provenance`` below is the single place that
    knows this, so no call site has to remember it.
    """

    WORKBOOK = 'WORKBOOK'
    FORK_EDIT = 'FORK_EDIT'
    WEB_AUTHORED = 'WEB_AUTHORED'


def promote_provenance(current: 'RevisionProvenanceKind') -> 'RevisionProvenanceKind':
    """Where the lattice moves when a value in a candidate actually changes.

    ``WORKBOOK`` → ``FORK_EDIT``; everything else stays put.  Before the third
    kind existed, the edit path could hardcode ``FORK_EDIT`` and be right by
    accident; with ``WEB_AUTHORED`` in the enum that hardcode becomes a lie the
    moment someone edits a web-authored candidate, and the lie is written into
    the audit column rather than raised.  One function, so the rule cannot be
    half-remembered at the second call site (row add/delete) that now needs it.
    """
    if current is RevisionProvenanceKind.WORKBOOK:
        return RevisionProvenanceKind.FORK_EDIT
    return current


class CatalogFamily(_ValueEnum):
    FREQUENCY_TABLE = 'frequency_table'
    ANALYZER_SETTINGS = 'analyzer_settings'
    TEST_INFO = 'test_info'
    SWITCH_PORT_MAPPING = 'switch_port_mapping'
    CORRECTION = 'correction'
    ANT_GAIN = 'ant_gain'
    POWER_METER_SETTINGS = 'power_meter_settings'
    TEST_CONDITION = 'test_condition'


class ValidationSeverity(_ValueEnum):
    INFO = 'info'
    WARNING = 'warning'
    BLOCKING = 'blocking'


class ValidationIssueCode(_ValueEnum):
    MISSING_SOURCE_SNAPSHOT = 'missing_source_snapshot'
    MISSING_FAMILY_PROFILE = 'missing_family_profile'
    EMPTY_CATALOG = 'empty_catalog'
    DUPLICATE_REFERENCE_ID = 'duplicate_reference_id'
    DUPLICATE_REFERENCE_IDENTITY = 'duplicate_reference_identity'
    INVALID_REFERENCE_PAYLOAD = 'invalid_reference_payload'
    MISSING_TEST_CONDITION_ID = 'missing_test_condition_id'
    UNKNOWN_TEST_CONDITION_REFERENCE = 'unknown_test_condition_reference'
    INVALID_EFFECTIVE_INTERVAL = 'invalid_effective_interval'
    UNRESOLVED_DECISION = 'unresolved_decision'
    OFFICIAL_MANIFEST_REQUIRED = 'official_manifest_required'
    APPROVAL_REQUIRED = 'approval_required'


class DecisionTodoCode(_ValueEnum):
    PROFILE_PRECEDENCE_UNRESOLVED = 'profile_precedence_unresolved'
    RBAC_ROLE_MAPPING_UNRESOLVED = 'rbac_role_mapping_unresolved'
    CALIBRATION_OWNER_UNRESOLVED = 'calibration_owner_unresolved'
    COMPLIANCE_BASELINE_UNRESOLVED = 'compliance_baseline_unresolved'
    RAW_ARTIFACT_RETENTION_SECURITY_UNRESOLVED = 'raw_artifact_retention_security_unresolved'


class ReferenceCatalogError(ValueError):
    """Base error for invalid catalog lifecycle operations."""


class ReferenceCatalogConflictError(ReferenceCatalogError):
    """A revision/version/ETag or identity constraint was concurrently changed."""


class ReferenceCatalogValidationError(ReferenceCatalogError):
    """A candidate contains one or more blocking validation issues."""

    def __init__(self, message: str, issues: tuple['ReferenceValidationIssue', ...] = ()) -> None:
        super().__init__(message)
        self.issues = issues


class PublishedRevisionImmutableError(ReferenceCatalogError):
    """Published entries cannot be edited; fork before changing them."""


class ReferenceStateTransitionError(ReferenceCatalogError):
    """The requested state transition is not allowed by the lifecycle."""


class ReferenceCatalogNotFoundError(ReferenceCatalogError):
    """The requested revision does not exist."""


def validate_reference_catalog_limit(limit: int) -> int:
    """Validate the bounded page-size policy shared by service and store."""

    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ReferenceCatalogError('reference catalog limit must be an integer')
    if limit < 1 or limit > REFERENCE_CATALOG_MAX_LIMIT:
        raise ReferenceCatalogError(
            'reference catalog limit must be between '
            f'1 and {REFERENCE_CATALOG_MAX_LIMIT}'
        )
    return limit


@dataclass(frozen=True)
class ReferenceCatalogRevisionPin:
    """The immutable revision metadata carried by a catalog read cursor."""

    revision_id: str
    version: int
    etag: str

    def __post_init__(self) -> None:
        if not isinstance(self.revision_id, str) or not self.revision_id:
            raise ReferenceCatalogError(
                'reference catalog cursor revision ID is malformed'
            )
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ReferenceCatalogError(
                'reference catalog cursor revision version is malformed'
            )
        _require_sha256(self.etag, 'reference catalog cursor ETag')

    def to_list(self) -> list[object]:
        """Return the canonical wire order: revision ID, version, ETag."""

        return [self.revision_id, self.version, self.etag]


@dataclass(frozen=True)
class ReferenceCatalogCursor:
    """Opaque, scope-bound keyset position for catalog administration reads.

    Entry pages carry one revision pin; diff pages carry the ordered
    before/after revision pins.  Including the version and ETag prevents a
    continuation from silently mixing candidate versions after an edit.  The
    stable key itself is the opaque ``reference_id`` and is compared with the
    same SQLite text ordering used by the store's ``ORDER BY`` clause.
    """

    kind: str
    revision_pins: tuple[ReferenceCatalogRevisionPin, ...]
    reference_id: str

    def __post_init__(self) -> None:
        if self.kind not in _REFERENCE_CATALOG_CURSOR_KINDS:
            raise ReferenceCatalogError('unsupported reference catalog cursor kind')
        expected_revision_count = 1 if self.kind == 'entries' else 2
        if len(self.revision_pins) != expected_revision_count:
            raise ReferenceCatalogError('reference catalog cursor scope is malformed')
        if any(
            not isinstance(value, ReferenceCatalogRevisionPin)
            for value in self.revision_pins
        ):
            raise ReferenceCatalogError('reference catalog cursor revision pins are malformed')
        if not isinstance(self.reference_id, str) or not self.reference_id:
            raise ReferenceCatalogError('reference catalog cursor ID is malformed')

    @classmethod
    def for_entries(
        cls,
        revision_id: str,
        version: int,
        etag: str,
        reference_id: str,
    ) -> 'ReferenceCatalogCursor':
        return cls(
            'entries',
            (ReferenceCatalogRevisionPin(revision_id, version, etag),),
            reference_id,
        )

    @classmethod
    def for_diff(
        cls,
        before_revision_id: str,
        before_version: int,
        before_etag: str,
        after_revision_id: str,
        after_version: int,
        after_etag: str,
        reference_id: str,
    ) -> 'ReferenceCatalogCursor':
        return cls(
            'diff',
            (
                ReferenceCatalogRevisionPin(
                    before_revision_id,
                    before_version,
                    before_etag,
                ),
                ReferenceCatalogRevisionPin(
                    after_revision_id,
                    after_version,
                    after_etag,
                ),
            ),
            reference_id,
        )

    def encode(self) -> str:
        payload = json.dumps(
            [
                _REFERENCE_CATALOG_CURSOR_VERSION,
                self.kind,
                [pin.to_list() for pin in self.revision_pins],
                self.reference_id,
            ],
            ensure_ascii=True,
            separators=(',', ':'),
        ).encode('ascii')
        return base64.urlsafe_b64encode(payload).decode('ascii')

    @classmethod
    def decode(cls, token: str) -> 'ReferenceCatalogCursor':
        """Decode only tokens produced by :meth:`encode`.

        Python's permissive URL-safe decoder otherwise ignores non-alphabet
        bytes, which could turn a malformed cursor into a valid-looking first
        page.  The alphabet, padding, JSON shape, version, kind, and scope are
        all checked before constructing the value object.
        """

        if not isinstance(token, str) or not token:
            raise ReferenceCatalogError(
                'reference catalog cursor must be a non-empty string'
            )
        if len(token) > 1024 or len(token) % 4 != 0:
            raise ReferenceCatalogError('malformed reference catalog cursor')
        if not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', token):
            raise ReferenceCatalogError('malformed reference catalog cursor')
        try:
            raw = base64.b64decode(
                token.encode('ascii'),
                altchars=b'-_',
                validate=True,
            ).decode('ascii')
            payload = json.loads(raw)
        except (binascii.Error, UnicodeDecodeError, ValueError, TypeError) as exc:
            raise ReferenceCatalogError('malformed reference catalog cursor') from exc
        if (
            not isinstance(payload, list)
            or len(payload) != 4
            or payload[0] != _REFERENCE_CATALOG_CURSOR_VERSION
            or not isinstance(payload[1], str)
            or not isinstance(payload[2], list)
            or not isinstance(payload[3], str)
        ):
            raise ReferenceCatalogError('malformed reference catalog cursor payload')
        try:
            revision_pins = tuple(
                ReferenceCatalogRevisionPin(
                    revision_id=pin[0],
                    version=pin[1],
                    etag=pin[2],
                )
                for pin in payload[2]
                if isinstance(pin, list) and len(pin) == 3
            )
            if len(revision_pins) != len(payload[2]):
                raise ReferenceCatalogError(
                    'reference catalog cursor revision pins are malformed'
                )
            return cls(
                kind=payload[1],
                revision_pins=revision_pins,
                reference_id=payload[3],
            )
        except (ReferenceCatalogError, TypeError) as exc:
            raise ReferenceCatalogError('malformed reference catalog cursor scope') from exc

    def require_entries_scope(self, revision_id: str) -> None:
        if self.kind != 'entries' or self.revision_pins[0].revision_id != revision_id:
            raise ReferenceCatalogError('reference catalog cursor scope does not match entries')

    def require_diff_scope(self, before_revision_id: str, after_revision_id: str) -> None:
        if self.kind != 'diff' or tuple(
            pin.revision_id for pin in self.revision_pins
        ) != (before_revision_id, after_revision_id):
            raise ReferenceCatalogError('reference catalog cursor scope does not match diff')


def _require_sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ReferenceCatalogError(f'{name} must be a lowercase SHA-256 hex digest')


def _validate_json_value(value: Any, *, path: str = 'payload') -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReferenceCatalogError(f'{path} contains a non-finite number')
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ReferenceCatalogError(f'{path} keys must be non-empty strings')
            _validate_json_value(item, path=f'{path}.{key}')
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f'{path}[{index}]')
        return
    raise ReferenceCatalogError(
        f'{path} contains unsupported value type {type(value).__name__}'
    )


def normalize_effective_instant(value: Optional[str]) -> Optional[str]:
    """Normalize date/datetime input to one UTC instant representation.

    Date-only values mean midnight UTC at the start of that date.  Date-times
    may carry an offset; naive values are interpreted as UTC because a catalog
    entry cannot be compared safely using the host's local timezone.  The
    canonical form is fixed-width UTC ISO-8601 with a ``Z`` suffix, so lexical
    comparison is chronological for both ``effective_from`` and
    ``effective_to``.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        raise ReferenceCatalogError('effective interval values must be strings')
    raw = value.strip()
    if not raw:
        return None
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
            parsed = datetime.combine(
                date.fromisoformat(raw),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
        else:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ReferenceCatalogError(
            'effective interval values must be ISO dates or datetimes'
        ) from exc
    return parsed.isoformat(timespec='microseconds').replace('+00:00', 'Z')


def normalize_catalog_timestamp(value: str, *, field_name: str = 'catalog timestamp') -> str:
    """Normalize one catalog timestamp to canonical UTC ISO-8601.

    Date-only values are deliberately rejected for event/lifecycle fields.
    Naive ISO datetimes are interpreted as UTC; offset-aware values are
    converted to UTC before persistence. Every accepted value therefore has
    the exact ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` shape enforced by SQLite.
    """

    if not isinstance(value, str):
        raise ReferenceCatalogError(f'{field_name} must be an ISO datetime')
    raw = value.strip()
    if not raw:
        raise ReferenceCatalogError(f'{field_name} must not be blank')
    if len(raw) < 11 or raw[10] != 'T':
        raise ReferenceCatalogError(
            f'{field_name} must be an ISO datetime with a T separator'
        )
    try:
        parsed = datetime.fromisoformat(
            raw[:-1] + '+00:00' if raw.endswith('Z') else raw
        )
    except (TypeError, ValueError) as exc:
        raise ReferenceCatalogError(
            f'{field_name} must be an ISO datetime'
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec='microseconds').replace('+00:00', 'Z')


def normalize_decision_resolution_instant(value: str) -> str:
    """Backward-compatible name for decision-resolution timestamp input."""

    return normalize_catalog_timestamp(
        value,
        field_name='decision resolution resolved_at',
    )


@dataclass(frozen=True)
class CatalogFamilyIdentity:
    """Typed family/profile/scope identity; no precedence is inferred."""

    family: CatalogFamily
    profile_id: str
    scope_id: str = ''

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ReferenceCatalogError('catalog profile_id must not be blank')

    @property
    def identity_key(self) -> str:
        scope = self.scope_id if self.scope_id.strip() else '-'
        return f'{self.family.value}|{self.profile_id}|{scope}'

    def to_dict(self) -> dict[str, str]:
        return {
            'family': self.family.value,
            'profile_id': self.profile_id,
            'scope_id': self.scope_id,
            'identity_key': self.identity_key,
        }


@dataclass(frozen=True)
class ReferenceEntry:
    """One typed catalog record addressed by an opaque stable reference ID."""

    reference_id: str
    identity_key: str
    payload: Mapping[str, Any]
    test_condition_ids: tuple[str, ...] = ()
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    source_sheet_name: Optional[str] = None
    source_row_number: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.reference_id.strip():
            raise ReferenceCatalogError('reference_id must not be blank')
        if not self.identity_key.strip():
            raise ReferenceCatalogError('reference entry identity_key must not be blank')
        if not isinstance(self.payload, Mapping):
            raise ReferenceCatalogError('reference payload must be a mapping')
        _validate_json_value(self.payload)
        if len(set(self.test_condition_ids)) != len(self.test_condition_ids):
            raise ReferenceCatalogError('test_condition_ids must be unique')
        if any(not value.strip() for value in self.test_condition_ids):
            raise ReferenceCatalogError('test_condition_ids must not contain blanks')
        if self.source_row_number is not None and self.source_row_number < 1:
            raise ReferenceCatalogError('source_row_number must be positive')
        # Preserve invalid values for the validator to report as a typed
        # blocking issue, while canonicalizing every valid interval at the
        # domain boundary so persistence and comparison share one form.
        for field_name in ('effective_from', 'effective_to'):
            value = getattr(self, field_name)
            try:
                normalized = normalize_effective_instant(value)
            except ReferenceCatalogError:
                continue
            object.__setattr__(self, field_name, normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            'reference_id': self.reference_id,
            'identity_key': self.identity_key,
            'payload': dict(self.payload),
            'test_condition_ids': list(self.test_condition_ids),
            'effective_from': self.effective_from,
            'effective_to': self.effective_to,
            'source_sheet_name': self.source_sheet_name,
            'source_row_number': self.source_row_number,
        }


@dataclass(frozen=True)
class TestConditionReference:
    """Link from a typed TestCondition ID to one family reference ID."""

    test_condition_id: str
    catalog_family: CatalogFamily
    reference_id: str

    def __post_init__(self) -> None:
        if not self.test_condition_id.strip():
            raise ReferenceCatalogError('test_condition_id must not be blank')
        if not self.reference_id.strip():
            raise ReferenceCatalogError('test-condition reference_id must not be blank')

    def to_dict(self) -> dict[str, str]:
        return {
            'test_condition_id': self.test_condition_id,
            'catalog_family': self.catalog_family.value,
            'reference_id': self.reference_id,
        }


@dataclass(frozen=True)
class DecisionTodo:
    """Typed unresolved policy decision; it is never silently defaulted."""

    code: DecisionTodoCode
    description: str
    blocking: bool = True

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ReferenceCatalogError('decision TODO description must not be blank')

    def to_dict(self) -> dict[str, Any]:
        return {
            'code': self.code.value,
            'description': self.description,
            'blocking': self.blocking,
        }


@dataclass(frozen=True)
class DecisionCatalogueEntry:
    """Machine-readable definition of a policy decision blocker."""

    code: DecisionTodoCode
    description: str
    blocking: bool = True
    resolution_fields: tuple[str, ...] = (
        'actor',
        'resolved_at',
        'reason',
        'evidence_reference',
        'evidence_sha256',
    )

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ReferenceCatalogError('decision catalogue description must not be blank')
        if not self.blocking:
            raise ReferenceCatalogError('required decision catalogue entries must block by default')

    def to_dict(self) -> dict[str, Any]:
        return {
            'code': self.code.value,
            'description': self.description,
            'blocking': self.blocking,
            'resolution_fields': list(self.resolution_fields),
        }


def _load_decision_catalogue() -> tuple[DecisionCatalogueEntry, ...]:
    """Load the shared resource so domain and migration use one SSOT."""

    try:
        raw = json.loads(
            _resource_files(__package__)
            .joinpath('decision_catalogue.json')
            .read_text(encoding='utf-8')
        )
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise ReferenceCatalogError('decision catalogue resource is unavailable') from exc
    if not isinstance(raw, list) or not raw:
        raise ReferenceCatalogError('decision catalogue resource must be a non-empty list')
    entries = tuple(
        DecisionCatalogueEntry(
            code=DecisionTodoCode(item['code']),
            description=item['description'],
            blocking=item['blocking'],
            resolution_fields=tuple(item['resolution_fields']),
        )
        for item in raw
        if isinstance(item, dict)
    )
    if len(entries) != len(raw) or len({entry.code for entry in entries}) != len(entries):
        raise ReferenceCatalogError('decision catalogue resource contains invalid or duplicate entries')
    return entries


# This tuple is the sole blocker catalogue. Contract/plan/runbook documents
# refer to these codes rather than maintaining another list that can drift.
DECISION_CATALOGUE: tuple[DecisionCatalogueEntry, ...] = _load_decision_catalogue()
DECISION_CATALOGUE_BY_CODE: Mapping[DecisionTodoCode, DecisionCatalogueEntry] = {
    entry.code: entry for entry in DECISION_CATALOGUE
}


@dataclass(frozen=True)
class DecisionResolution:
    """Append-only, evidence-backed resolution of one catalogue blocker."""

    code: DecisionTodoCode
    actor: str
    resolved_at: str
    reason: str
    evidence_reference: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        for field_name in (
            'actor',
            'resolved_at',
            'reason',
            'evidence_reference',
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ReferenceCatalogError(
                    f'decision resolution {field_name} must not be blank'
                )
        object.__setattr__(
            self,
            'resolved_at',
            normalize_decision_resolution_instant(self.resolved_at),
        )
        _require_sha256(self.evidence_sha256, 'decision resolution evidence_sha256')
        if self.code not in DECISION_CATALOGUE_BY_CODE:
            raise ReferenceCatalogError(
                f'decision resolution code is not in the decision catalogue: {self.code!r}'
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            'code': self.code.value,
            'actor': self.actor,
            'resolved_at': self.resolved_at,
            'reason': self.reason,
            'evidence_reference': self.evidence_reference,
            'evidence_sha256': self.evidence_sha256,
        }


DEFAULT_DECISION_TODOS: tuple[DecisionTodo, ...] = tuple(
    DecisionTodo(
        entry.code,
        entry.description,
        entry.blocking,
    )
    for entry in DECISION_CATALOGUE
)


def normalize_decision_todos(
    decision_todos: Optional[tuple[DecisionTodo, ...]],
    decision_resolutions: tuple[DecisionResolution, ...] = (),
) -> tuple[DecisionTodo, ...]:
    """Retain every unresolved catalogue blocker in deterministic order.

    An omitted/empty TODO list is never treated as a resolution.  A catalogue
    code leaves the unresolved set only when an explicit typed resolution with
    complete evidence is present.
    """

    resolved_codes = {resolution.code for resolution in decision_resolutions}
    return tuple(
        DecisionTodo(
            code=entry.code,
            description=entry.description,
            blocking=entry.blocking,
        )
        for entry in DECISION_CATALOGUE
        if entry.code not in resolved_codes
    )


@dataclass(frozen=True)
class ReferenceValidationIssue:
    code: ValidationIssueCode
    severity: ValidationSeverity
    message: str
    reference_id: Optional[str] = None
    field_path: Optional[str] = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ReferenceCatalogError('validation issue message must not be blank')
        _validate_json_value(self.details, path='validation.details')

    @property
    def blocking(self) -> bool:
        return self.severity is ValidationSeverity.BLOCKING

    def to_dict(self) -> dict[str, Any]:
        return {
            'code': self.code.value,
            'severity': self.severity.value,
            'message': self.message,
            'reference_id': self.reference_id,
            'field_path': self.field_path,
            'details': dict(self.details),
        }


@dataclass(frozen=True)
class ReferenceValidationReport:
    issues: tuple[ReferenceValidationIssue, ...] = ()

    @property
    def blocking_issues(self) -> tuple[ReferenceValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    @property
    def is_publishable(self) -> bool:
        return not self.blocking_issues


@dataclass(frozen=True)
class ReferenceRevisionSummary:
    """Redaction-safe administration projection for one revision.

    This projection intentionally contains no catalog entries, payloads,
    workbook filenames, URIs, or cell values.  Counts and blocker codes come
    from the current validation/TODO projections; lifecycle and provenance
    metadata remain visible so an administrator can make an informed
    decision without reading unrestricted reference data.
    """

    revision_id: str
    revision_number: int
    family_identity: CatalogFamilyIdentity
    state: RevisionState
    version: int
    etag: str
    source_snapshot_id: str
    source_manifest_sha256: str
    created_by: str
    created_at: str
    updated_by: str
    updated_at: str
    entry_count: int
    test_condition_ref_count: int
    validation_issue_count: int
    blocking_issue_count: int
    validation_run_count: int
    current_validation_run_id: Optional[str]
    decision_todo_count: int
    blocking_decision_count: int
    decision_resolution_count: int
    blocking_issue_codes: tuple[ValidationIssueCode, ...] = ()
    unresolved_decision_codes: tuple[DecisionTodoCode, ...] = ()
    forked_from_revision_id: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    approval_reason: Optional[str] = None
    published_by: Optional[str] = None
    published_at: Optional[str] = None
    publish_reason: Optional[str] = None
    retired_by: Optional[str] = None
    retired_at: Optional[str] = None
    retirement_reason: Optional[str] = None
    official_manifest_sha256: Optional[str] = None

    def __post_init__(self) -> None:
        for name in (
            'revision_id',
            'source_snapshot_id',
            'created_by',
            'created_at',
            'updated_by',
            'updated_at',
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ReferenceCatalogError(f'{name} must not be blank')
        if self.revision_number < 1 or self.version < 1:
            raise ReferenceCatalogError('revision summary numbers must be positive')
        _require_sha256(self.etag, 'etag')
        _require_sha256(self.source_manifest_sha256, 'source_manifest_sha256')
        if self.official_manifest_sha256 is not None:
            _require_sha256(self.official_manifest_sha256, 'official_manifest_sha256')
        for name in (
            'entry_count',
            'test_condition_ref_count',
            'validation_issue_count',
            'blocking_issue_count',
            'validation_run_count',
            'decision_todo_count',
            'blocking_decision_count',
            'decision_resolution_count',
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ReferenceCatalogError(f'{name} must be a non-negative integer')
        if self.blocking_issue_count > self.validation_issue_count:
            raise ReferenceCatalogError('blocking issue count exceeds issue count')
        if self.blocking_decision_count > self.decision_todo_count:
            raise ReferenceCatalogError('blocking decision count exceeds TODO count')
        for name in ('approved_at', 'published_at', 'retired_at'):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    normalize_catalog_timestamp(value, field_name=name),
                )

    @property
    def has_blockers(self) -> bool:
        return bool(self.blocking_issue_count or self.blocking_decision_count)

    def to_dict(self) -> dict[str, Any]:
        """Serialize only the safe summary fields; never include payload data."""

        return {
            'revision_id': self.revision_id,
            'revision_number': self.revision_number,
            'family_identity': self.family_identity.to_dict(),
            'state': self.state.value,
            'version': self.version,
            'etag': self.etag,
            'source_snapshot_id': self.source_snapshot_id,
            'source_manifest_sha256': self.source_manifest_sha256,
            'created_by': self.created_by,
            'created_at': self.created_at,
            'updated_by': self.updated_by,
            'updated_at': self.updated_at,
            'entry_count': self.entry_count,
            'test_condition_ref_count': self.test_condition_ref_count,
            'validation_issue_count': self.validation_issue_count,
            'blocking_issue_count': self.blocking_issue_count,
            'validation_run_count': self.validation_run_count,
            'current_validation_run_id': self.current_validation_run_id,
            'decision_todo_count': self.decision_todo_count,
            'blocking_decision_count': self.blocking_decision_count,
            'decision_resolution_count': self.decision_resolution_count,
            'blocking_issue_codes': [code.value for code in self.blocking_issue_codes],
            'unresolved_decision_codes': [
                code.value for code in self.unresolved_decision_codes
            ],
            'forked_from_revision_id': self.forked_from_revision_id,
            'approved_by': self.approved_by,
            'approved_at': self.approved_at,
            'approval_reason': self.approval_reason,
            'published_by': self.published_by,
            'published_at': self.published_at,
            'publish_reason': self.publish_reason,
            'retired_by': self.retired_by,
            'retired_at': self.retired_at,
            'retirement_reason': self.retirement_reason,
            'official_manifest_sha256': self.official_manifest_sha256,
        }


@dataclass(frozen=True)
class ReferenceEntrySummary:
    """One redaction-safe entry row; the typed payload is deliberately absent."""

    revision_id: str
    reference_id: str
    identity_key: str
    content_sha256: str
    effective_from: Optional[str]
    effective_to: Optional[str]
    source_sheet_name: Optional[str]
    source_row_number: Optional[int]
    test_condition_count: int

    def __post_init__(self) -> None:
        for name in ('revision_id', 'reference_id', 'identity_key'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ReferenceCatalogError(f'{name} must not be blank')
        _require_sha256(self.content_sha256, 'content_sha256')
        if self.source_row_number is not None and self.source_row_number < 1:
            raise ReferenceCatalogError('source_row_number must be positive')
        if (
            isinstance(self.test_condition_count, bool)
            or not isinstance(self.test_condition_count, int)
            or self.test_condition_count < 0
        ):
            raise ReferenceCatalogError('test_condition_count must be non-negative')

    def to_dict(self) -> dict[str, Any]:
        return {
            'revision_id': self.revision_id,
            'reference_id': self.reference_id,
            'identity_key': self.identity_key,
            'content_sha256': self.content_sha256,
            'effective_from': self.effective_from,
            'effective_to': self.effective_to,
            'source_sheet_name': self.source_sheet_name,
            'source_row_number': self.source_row_number,
            'test_condition_count': self.test_condition_count,
        }


class ReferenceDiffChange(_ValueEnum):
    ADDED = 'added'
    REMOVED = 'removed'
    CHANGED = 'changed'


@dataclass(frozen=True)
class ReferenceDiffEntry:
    """One redaction-safe diff item.

    A diff exposes only the stable record ID, semantic hashes, change kind, and
    source coordinates.  It never returns identity payloads, condition lists,
    workbook values, source URIs, or filenames.
    """

    reference_id: str
    change: ReferenceDiffChange
    before_hash: Optional[str]
    after_hash: Optional[str]
    before_source_sheet_name: Optional[str] = None
    before_source_row_number: Optional[int] = None
    after_source_sheet_name: Optional[str] = None
    after_source_row_number: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.reference_id, str) or not self.reference_id.strip():
            raise ReferenceCatalogError('diff reference_id must not be blank')
        if self.before_hash is not None:
            _require_sha256(self.before_hash, 'before_hash')
        if self.after_hash is not None:
            _require_sha256(self.after_hash, 'after_hash')
        for name in ('before_source_row_number', 'after_source_row_number'):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ReferenceCatalogError(f'{name} must be positive')
        if self.change is ReferenceDiffChange.ADDED and (
            self.before_hash is not None or self.after_hash is None
        ):
            raise ReferenceCatalogError('added diff must have only an after hash')
        if self.change is ReferenceDiffChange.REMOVED and (
            self.before_hash is None or self.after_hash is not None
        ):
            raise ReferenceCatalogError('removed diff must have only a before hash')
        if self.change is ReferenceDiffChange.CHANGED and (
            self.before_hash is None or self.after_hash is None
        ):
            raise ReferenceCatalogError('changed diff must have both hashes')

    def to_dict(self) -> dict[str, Any]:
        return {
            'reference_id': self.reference_id,
            'change': self.change.value,
            'before_hash': self.before_hash,
            'after_hash': self.after_hash,
            'before_provenance': {
                'source_sheet_name': self.before_source_sheet_name,
                'source_row_number': self.before_source_row_number,
            },
            'after_provenance': {
                'source_sheet_name': self.after_source_sheet_name,
                'source_row_number': self.after_source_row_number,
            },
        }


@dataclass(frozen=True)
class ReferenceEntryPage:
    """Bounded keyset page of redaction-safe entry summaries."""

    items: tuple[ReferenceEntrySummary, ...]
    next_cursor: Optional[str] = None


@dataclass(frozen=True)
class ReferenceDiffPage:
    """Bounded keyset page of redaction-safe reference changes."""

    items: tuple[ReferenceDiffEntry, ...]
    next_cursor: Optional[str] = None


@dataclass(frozen=True)
class ReferenceRevision:
    """Immutable value representation of one catalog revision."""

    revision_id: str
    revision_number: int
    family_identity: CatalogFamilyIdentity
    state: RevisionState
    version: int
    etag: str
    source_snapshot_id: str
    source_manifest_sha256: str
    created_by: str
    created_at: str
    updated_by: str
    updated_at: str
    entries: tuple[ReferenceEntry, ...] = ()
    test_condition_refs: tuple[TestConditionReference, ...] = ()
    validation_issues: tuple[ReferenceValidationIssue, ...] = ()
    decision_todos: tuple[DecisionTodo, ...] = DEFAULT_DECISION_TODOS
    decision_resolutions: tuple[DecisionResolution, ...] = ()
    forked_from_revision_id: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    approval_reason: Optional[str] = None
    published_by: Optional[str] = None
    published_at: Optional[str] = None
    publish_reason: Optional[str] = None
    retired_by: Optional[str] = None
    retired_at: Optional[str] = None
    retirement_reason: Optional[str] = None
    official_manifest_sha256: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.revision_id.strip():
            raise ReferenceCatalogError('revision_id must not be blank')
        if self.revision_number < 0:
            raise ReferenceCatalogError('revision_number must not be negative')
        if self.version < 1:
            raise ReferenceCatalogError('revision version must be positive')
        _require_sha256(self.etag, 'etag')
        if not self.source_snapshot_id.strip():
            raise ReferenceCatalogError('source_snapshot_id must not be blank')
        _require_sha256(self.source_manifest_sha256, 'source_manifest_sha256')
        for name, value in (
            ('created_by', self.created_by),
            ('created_at', self.created_at),
            ('updated_by', self.updated_by),
            ('updated_at', self.updated_at),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ReferenceCatalogError(f'{name} must not be blank')
            if name.endswith('_at'):
                object.__setattr__(
                    self,
                    name,
                    normalize_catalog_timestamp(value, field_name=name),
                )
        for name in (
            'approved_at',
            'published_at',
            'retired_at',
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    normalize_catalog_timestamp(value, field_name=name),
                )
        if self.official_manifest_sha256 is not None:
            _require_sha256(self.official_manifest_sha256, 'official_manifest_sha256')
        todo_codes = [todo.code for todo in self.decision_todos]
        if len(todo_codes) != len(set(todo_codes)):
            raise ReferenceCatalogError('decision TODO codes must be unique')
        if len(self.decision_resolutions) != len(set(self.decision_resolutions)):
            raise ReferenceCatalogError('decision resolutions must be unique in a revision value')

    @property
    def blocking_issues(self) -> tuple[ReferenceValidationIssue, ...]:
        return tuple(issue for issue in self.validation_issues if issue.blocking)

    @property
    def is_published(self) -> bool:
        return self.state is RevisionState.PUBLISHED

    def etag_payload(self) -> dict[str, Any]:
        """Return typed catalog content used to compute the optimistic ETag."""

        return {
            'revision_id': self.revision_id,
            'revision_number': self.revision_number,
            'family_identity': self.family_identity.to_dict(),
            'state': self.state.value,
            'version': self.version,
            'source_snapshot_id': self.source_snapshot_id,
            'source_manifest_sha256': self.source_manifest_sha256,
            'entries': [entry.to_dict() for entry in self.entries],
            'test_condition_refs': [ref.to_dict() for ref in self.test_condition_refs],
            'validation_issues': [issue.to_dict() for issue in self.validation_issues],
            'decision_todos': [todo.to_dict() for todo in self.decision_todos],
            'decision_resolutions': [
                resolution.to_dict() for resolution in self.decision_resolutions
            ],
            'forked_from_revision_id': self.forked_from_revision_id,
            'approved_by': self.approved_by,
            'approved_at': self.approved_at,
            'approval_reason': self.approval_reason,
            'published_by': self.published_by,
            'published_at': self.published_at,
            'publish_reason': self.publish_reason,
            'retired_by': self.retired_by,
            'retired_at': self.retired_at,
            'retirement_reason': self.retirement_reason,
            'official_manifest_sha256': self.official_manifest_sha256,
        }

    def with_updates(self, **changes: Any) -> 'ReferenceRevision':
        """Return a new value; lifecycle mutation remains service/store-owned."""

        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            'revision_id': self.revision_id,
            'revision_number': self.revision_number,
            'family_identity': self.family_identity.to_dict(),
            'state': self.state.value,
            'version': self.version,
            'etag': self.etag,
            'source_snapshot_id': self.source_snapshot_id,
            'source_manifest_sha256': self.source_manifest_sha256,
            'created_by': self.created_by,
            'created_at': self.created_at,
            'updated_by': self.updated_by,
            'updated_at': self.updated_at,
            'entries': [entry.to_dict() for entry in self.entries],
            'test_condition_refs': [ref.to_dict() for ref in self.test_condition_refs],
            'validation_issues': [issue.to_dict() for issue in self.validation_issues],
            'decision_todos': [todo.to_dict() for todo in self.decision_todos],
            'decision_resolutions': [
                resolution.to_dict() for resolution in self.decision_resolutions
            ],
            'forked_from_revision_id': self.forked_from_revision_id,
            'approved_by': self.approved_by,
            'approved_at': self.approved_at,
            'approval_reason': self.approval_reason,
            'published_by': self.published_by,
            'published_at': self.published_at,
            'publish_reason': self.publish_reason,
            'retired_by': self.retired_by,
            'retired_at': self.retired_at,
            'retirement_reason': self.retirement_reason,
            'official_manifest_sha256': self.official_manifest_sha256,
        }


__all__ = [
    'CatalogFamily',
    'CatalogFamilyIdentity',
    'DECISION_CATALOGUE',
    'DECISION_CATALOGUE_BY_CODE',
    'DecisionTodo',
    'DecisionCatalogueEntry',
    'DecisionTodoCode',
    'DecisionResolution',
    'DEFAULT_DECISION_TODOS',
    'REFERENCE_CATALOG_DEFAULT_LIMIT',
    'REFERENCE_CATALOG_MAX_LIMIT',
    'ReferenceCatalogCursor',
    'ReferenceCatalogRevisionPin',
    'ReferenceDiffChange',
    'ReferenceDiffEntry',
    'ReferenceDiffPage',
    'ReferenceEntryPage',
    'ReferenceEntrySummary',
    'ReferenceRevisionSummary',
    'normalize_catalog_timestamp',
    'promote_provenance',
    'normalize_decision_todos',
    'normalize_decision_resolution_instant',
    'normalize_effective_instant',
    'PublishedRevisionImmutableError',
    'ReferenceCatalogConflictError',
    'ReferenceCatalogError',
    'ReferenceCatalogNotFoundError',
    'ReferenceCatalogValidationError',
    'ReferenceEntry',
    'ReferenceRevision',
    'ReferenceStateTransitionError',
    'ReferenceValidationIssue',
    'ReferenceValidationReport',
    'RevisionProvenanceKind',
    'RevisionState',
    'TestConditionReference',
    'ValidationIssueCode',
    'ValidationSeverity',
    'validate_reference_catalog_limit',
]
