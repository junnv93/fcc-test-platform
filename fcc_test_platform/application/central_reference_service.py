"""Turn stored reference rows into what a web client or a chamber can act on.

The adapters below this service hold SQL and nothing else; the routes above it
hold HTTP and nothing else. What lives here is the part that is neither: which
scope a family belongs to, what a delivery cut is identified by, and how a page
boundary is expressed.

THE BUNDLE TAG IS DERIVED, NOT STORED
--------------------------------------
A chamber decides whether to download by comparing a tag. If the tag were a
stored column it would have to be maintained on every write that could affect a
bundle — a publish, a retirement, a scope reassignment — and the day one of those
forgot, chambers would stop noticing a change that had already happened.

So the tag is computed from the content the cut actually contains: the etag of
every revision in it, in a stable order. Two cuts with the same contents produce
the same tag by construction, and no write path can leave it stale because there
is nothing to update.

The hash comes from the domain hashing SSOT. This package may not import hashlib,
which is the mechanical reason; the substantive one is that a chamber compares
etags computed on both sides, and a second hasher would make that comparison
meaningless while looking like it worked.

WHY SCOPE RESOLUTION LIVES HERE AND NOT IN THE ROUTE
-----------------------------------------------------
A chamber pulls by chamber id, and the room-scoped families key on exactly that
identity because one PC serves one shield room. The project-scoped families need
the project currently running there, which the caller supplies. Assembling those
two into the scope set is a rule about the reference axis, so it belongs with the
rest of that axis rather than in an HTTP handler that would then own a rule it
cannot explain.

dependency-free of infrastructure: domain + application.common + sibling platform
modules only.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Optional, Sequence

from fcc_test_contracts.common.logging_channel import get_logger
from fcc_test_kernel.application.central_contract.pagination import (
    CursorValueDomain,
    decode_cursor,
    encode_cursor,
)
from fcc_test_kernel.domain.models.reference_catalog import (
    CatalogFamily,
    RevisionProvenanceKind,
    RevisionState,
    promote_provenance,
)
from fcc_test_platform.domain.ports.output.central_reference_port import (
    ReferenceCoupledPublishError,
    ReferenceProviderNotFoundError,
    ReferenceProviderNotRegisteredError,
    ReferenceRevisionNotFoundError,
    ReferenceStateConflictError,
)
from fcc_test_platform.domain.services.provider_identity_policy import (
    ProviderIdentityStatus,
    classify_provider_identity,
)
from fcc_test_kernel.domain.services.reference_entry_edit_policy import (
    EntryEdit,
    ReferenceEntryEditError,
    apply_entry_edits,
    validate_entry_payload_shape,
    validate_entry_payload_values,
)
from fcc_test_platform.domain.services.reference_row_edit_policy import (
    ReferenceRowEditError,
    apply_row_edits,
)
from fcc_test_kernel.domain.services.reference_hashing import build_reference_entry_hash
from fcc_test_kernel.domain.services.reference_hashing import canonical_semantic_hash
from fcc_test_kernel.domain.services.reference_ownership_policy import (
    COUPLED_FAMILY_GROUPS,
    DEFAULT_REFERENCE_PROFILE_ID,
    identity_fields_for,
    projection_fields_for,
)
from fcc_test_kernel.domain.services.reference_scope_policy import (
    ReferenceScopeError,
    ReferenceScopeKind,
    scope_kind_for,
)


__all__ = [
    'REVISION_CURSOR_ARITY',
    'REVISION_CURSOR_DOMAINS',
    'CentralReferenceService',
    'resolve_bundle_scopes',
]


#: The keyset axis, matching ``ux_reference_revisions_identity_number`` so the
#: seek is index-backed. Declared next to its domains: a cursor whose arity and
#: whose value types are declared apart is a cursor that can drift into accepting
#: a forged token, which the platform boundary already paid for once.
REVISION_CURSOR_ARITY = 5
REVISION_CURSOR_DOMAINS: tuple[CursorValueDomain, ...] = (
    CursorValueDomain.TEXT,     # family
    CursorValueDomain.TEXT,     # profile_id
    CursorValueDomain.TEXT,     # scope_id
    CursorValueDomain.TEXT,     # revision_number (digits, but text-comparable)
    CursorValueDomain.UUID,     # id
)

_PUBLISHED = RevisionState.PUBLISHED.value

logger = get_logger('platform_api')


def resolve_bundle_scopes(
    chamber_id: str, project_id: Optional[str] = None,
) -> tuple[str, ...]:
    """The scope keys a chamber's bundle covers.

    The chamber id IS the room key — one PC per shield room — so the room-scoped
    families resolve from it directly. The project is optional because a chamber
    between jobs still needs its cabling, and delivering only what is known is
    better than refusing the whole bundle for a missing project.

    Duplicates are collapsed and order is stable, so the same request always
    produces the same cut and therefore the same tag.
    """
    if not str(chamber_id).strip():
        raise ReferenceScopeError(
            'a reference bundle requires a chamber id; it is the room key, and '
            'without it there is no way to say whose cabling to deliver'
        )
    scopes = [str(chamber_id).strip()]
    if project_id is not None and str(project_id).strip():
        candidate = str(project_id).strip()
        if candidate not in scopes:
            scopes.append(candidate)
    return tuple(scopes)


def _families_by_scope_kind() -> Mapping[ReferenceScopeKind, tuple[str, ...]]:
    grouped: dict[ReferenceScopeKind, list[str]] = {}
    for family in CatalogFamily:
        try:
            kind = scope_kind_for(family)
        except ReferenceScopeError:
            # A catalog-only family with no runtime table has no scope axis, and
            # that is not an error here — it simply never appears in a bundle.
            continue
        grouped.setdefault(kind, []).append(family.value)
    return {kind: tuple(sorted(values)) for kind, values in grouped.items()}


class CentralReferenceService:
    """Application service over the central reference read/write ports."""

    def __init__(
        self,
        read_port,
        write_port=None,
        *,
        bundle_provider_id: Optional[str] = None,
        offered_provider_ids: Optional[Callable[[], Sequence[str]]] = None,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        self._read = read_port
        self._write = write_port
        # What this deployment's provider picker OFFERS, supplied by the
        # composition root as a callable over the descriptor registry. Injected
        # rather than imported because "which providers this platform renders"
        # is a platform-rendering fact and this service must not acquire an
        # opinion about it — it only needs to know whether the id a caller named
        # is one the screen handed them, which is what separates "an operator
        # must register this" from "you named an id nobody knows".
        #
        # ``None`` is not the empty set. A composition without a registry cannot
        # observe the offering at all, and the classifier degrades toward the
        # actionable answer rather than accusing the caller (see
        # ``provider_identity_policy``).
        self._offered_provider_ids = offered_provider_ids
        # A chamber pulls by chamber id alone — ``chamber_nodes`` carries no
        # provider column — so the provider comes from deployment configuration,
        # exactly as the chamber result-ingestion service already takes it. A node
        # must not be able to name the provider it wants: that would let one
        # room's token fetch another product line's cabling.
        self._bundle_provider_id = (
            str(bundle_provider_id).strip() if bundle_provider_id else None
        )
        self._clock = clock

    # ----------------------------------------------------------------- read

    def list_revisions(
        self,
        provider_id: str,
        *,
        family: Optional[str] = None,
        scope_kind: Optional[str] = None,
        scope_id: Optional[str] = None,
        state: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        """One page of revision summaries plus the cursor for the next.

        Returns ``(rows, next_cursor)``. ``next_cursor`` is ``None`` on the last
        page and on an unbounded read, so a client can tell "there is no more"
        from "you did not ask for pages".

        Resolves the provider FIRST. Without it this listing answers ``200 []``
        for a provider nobody registered, which is the same answer a registered
        provider with nothing published gives — and that equality is what made
        the screen look merely unprovisioned for as long as it did.
        """
        self._require_registered_provider(provider_id)
        cursor_values = None
        if cursor:
            # Decoded with declared value domains, so a forged token is a 400
            # here rather than a driver type error surfacing as a 503 later.
            cursor_values = decode_cursor(
                cursor,
                arity=REVISION_CURSOR_ARITY,
                domains=REVISION_CURSOR_DOMAINS,
            )

        # One extra row decides whether another page exists, without a COUNT.
        fetch_limit = None if limit is None else limit + 1
        rows = self._read.list_revisions(
            provider_id,
            family=family,
            scope_kind=scope_kind,
            scope_id=scope_id,
            state=state,
            limit=fetch_limit,
            cursor_values=cursor_values,
        )

        next_cursor = None
        if limit is not None and len(rows) > limit:
            rows = rows[:limit]
            next_cursor = self._revision_cursor(rows[-1])
        return [self._revision_summary(row) for row in rows], next_cursor

    def peek_revision_scope(self, revision_id: str) -> Optional[dict]:
        """``{'family', 'scope_id'}`` of a stored revision, or ``None``.

        Exists so the route can decide whether a PROJECT membership could grant
        the operation WITHOUT the boundary reaching into the read port itself.
        Returns ``None`` rather than raising for an unknown id: the caller uses
        this only after the token path has already failed, and a distinct answer
        for "no such revision" would turn a refusal into an existence oracle.
        """
        row = self._read.read_revision(revision_id)
        if row is None:
            return None
        return {'family': row['family'], 'scope_id': row['scope_id']}

    def read_revision(self, provider_id: str, revision_id: str) -> dict:
        """One revision, its entries, and the column order to render them in.

        Takes the provider because the route's path asserts one, and until this
        wave nothing checked it: a caller authorized for one provider could read
        another's revision by naming its id. ``fork``/``rows``/``entries``
        already enforced exactly this — three of the six operations verified the
        assertion and three did not, which is the strongest possible evidence
        that the rule was meant to hold everywhere.

        ``payload_columns`` is the provider domain's ``PROJECTION_FIELD_CONTRACT``
        for the family, sent verbatim. A client rendering an entry table needs a
        column ORDER, and the only alternatives are worse:

        * deriving it from a payload's keys — payloads are a free-form mapping and
          a null field may be absent, so each entry would produce its own column
          set and the table would ripple;
        * re-declaring the six families' field lists in TypeScript — the same
          order in two languages, drifting the moment either side gains a column,
          and visible only in what a tester publishes.

        This is the rule the equipment-list surface already follows ("the server
        gives the column order"). The platform is NOT interpreting the payload:
        it already imports ``CatalogFamily`` / ``ReferenceScopeKind`` /
        ``scope_kind_for`` from the provider domain, and a field ORDER is the same
        kind of derivation as those — vocabulary, not values.
        """
        self._require_registered_provider(provider_id)
        return self._revision_detail(provider_id, revision_id)

    def _revision_detail(self, provider_id: str, revision_id: str) -> dict:
        """Render one revision WITHOUT re-resolving the provider.

        The write operations end by returning the fresh detail, and going back
        through the public entry point made every write probe the provider
        twice. The cost was small; the correctness wrinkle was not — a provider
        removed between the write and the trailing read would turn a **committed
        write** into a 404, which a client reads as "nothing happened".

        Ownership is still checked here. That is the axis this method must not
        skip: it answers with a revision's contents, and skipping ownership
        would hand one provider another's rows.
        """
        revision = self._require_owned_revision(provider_id, revision_id)
        entries = self._read.read_entries(revision_id)
        sibling = self._coupled_sibling(revision['family'])
        family = self._require_known_family(revision['family'])
        return {
            'revision': self._revision_summary(revision),
            'entries': [self._entry_record(entry) for entry in entries],
            'payload_columns': list(projection_fields_for(family)),
            # 어느 열이 이 행을 *이 행이게* 하는가 — 편집 화면이 읽기 전용으로
            # 렌더해야 하는 열이고, 서버가 주는 이유는 `payload_columns` 와 같다.
            # 프론트가 `IDENTITY_FIELD_CONTRACT` 를 재선언하면 같은 규칙이 두
            # 언어로 갈라지고, 그 드리프트는 시험원이 식별 열을 고칠 수 있게 된
            # 뒤에야(=거부를 400 으로 받아본 뒤에야) 드러난다.
            'identity_columns': list(identity_fields_for(family)[0]),
            # 결합 그룹의 나머지 반쪽 패밀리 — 없으면 null. 열 순서와 **같은 이유**로
            # 서버가 준다: 결합 사실은 도메인 SSOT(``COUPLED_FAMILY_GROUPS``)에 있고,
            # 클라이언트가 `'correction'`/`'switch_port_mapping'` 을 적으면 그 어휘가
            # 두 곳이 된다. 오류 메시지에서 형제 이름을 파싱하게 두는 것도 같은 결함의
            # 다른 얼굴이다 — 사람이 읽는 문장을 기계가 파싱하는 결합이 생긴다.
            'coupled_with': sibling.value if sibling is not None else None,
        }

    def build_bundle(
        self,
        provider_id: Optional[str],
        chamber_id: str,
        *,
        project_id: Optional[str] = None,
        known_bundle_etag: Optional[str] = None,
        generated_at: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        """One page of the delivery cut for one chamber, tagged by the whole cut.

        When the caller's tag already matches, the revisions list is empty and
        ``unchanged`` is true. That distinction matters: an empty list alone
        cannot tell "you are current" from "this room publishes nothing", and a
        chamber that confused the two would either re-download forever or
        conclude it correctly holds no cabling data.

        ``bundle_etag`` describes the ENTIRE cut, not this page, and that is what
        makes a multi-page walk safe: every page repeats the same tag while the
        origin is still, and a publish landing mid-walk changes it, so the node
        can discard a partial bundle rather than apply half of a coupled family
        group. A per-page tag would be indistinguishable from that change and the
        walk could never converge.

        ``next_cursor`` is ``None`` on the last page AND on an unbounded read, so
        a caller can tell "there is no more" from "you did not ask for pages".
        """
        resolved_provider = provider_id or self._bundle_provider_id
        if not resolved_provider:
            raise RuntimeError(
                'reference bundle delivery is not configured with a provider id; '
                'a chamber cannot name its own provider, so there is nothing to '
                'fall back to'
            )
        # An unregistered configured provider is the worst case this module
        # names: the identity read matches nothing, the tag is the tag of an
        # empty cut, and the node is told — with a 200 — that it correctly holds
        # no correction data. This module's own port docstring warns about
        # exactly that sentence ("an outage that rendered as an empty list would
        # tell a chamber it is correctly holding nothing"), and a misconfigured
        # provider produces it without any outage. Refusing sends the node down
        # the path it already has for a failed pull: workbook fallback, nothing
        # cached, and a recorded sync failure — which is what separates "no
        # change" from "unreachable since Tuesday".
        self._require_registered_provider(resolved_provider)
        scopes = resolve_bundle_scopes(chamber_id, project_id)
        # The tag comes from the unpaged identity read, never from the page.
        bundle_etag = self._bundle_etag(
            self._read.read_bundle_identity(resolved_provider, scope_ids=scopes)
        )

        if known_bundle_etag and known_bundle_etag == bundle_etag:
            return {
                'chamber_id': chamber_id,
                'bundle_etag': bundle_etag,
                'generated_at': generated_at or self._now(),
                'unchanged': True,
                'revisions': [],
                'next_cursor': None,
            }

        cursor_values = None
        if cursor:
            # Decoded with declared value domains, so a forged token is a 400
            # here rather than a driver type error surfacing as a 503 later.
            cursor_values = decode_cursor(
                cursor,
                arity=REVISION_CURSOR_ARITY,
                domains=REVISION_CURSOR_DOMAINS,
            )
        # One extra row decides whether another page exists, without a COUNT.
        fetch_limit = None if limit is None else limit + 1
        rows = self._read.read_bundle(
            resolved_provider,
            scope_ids=scopes,
            limit=fetch_limit,
            cursor_values=cursor_values,
        )

        next_cursor = None
        if limit is not None and len(rows) > limit:
            rows = rows[:limit]
            next_cursor = self._revision_cursor(rows[-1])

        return {
            'chamber_id': chamber_id,
            'bundle_etag': bundle_etag,
            'generated_at': generated_at or self._now(),
            'unchanged': False,
            'revisions': [
                {
                    'revision': self._revision_summary(row),
                    'entries': [
                        self._entry_record(entry) for entry in row.get('entries', [])
                    ],
                }
                for row in rows
            ],
            'next_cursor': next_cursor,
        }

    # ---------------------------------------------------------------- write

    def create_candidate(
        self, provider_id: str, *, request: Mapping, created_by: str,
    ) -> dict:
        """Create a candidate from a validated request.

        ``created_by`` comes from the verified principal, never from the body:
        an actor a client can name is an actor a client can forge, and this row
        is the record of who put a value in front of an instrument.
        """
        if self._write is None:
            raise RuntimeError('central reference writes are not configured')

        self._require_registered_provider(provider_id)

        family = self._require_known_family(request['family'])
        declared = str(request['scope_kind'])
        expected = scope_kind_for(family)
        if declared != expected.value:
            # The axis is a property of the family, so a request that disagrees
            # is telling us the caller believes something false about where the
            # value belongs — and a room-scoped value filed under a project is
            # the mistake the scope policy exists to prevent.
            raise ReferenceScopeError(
                f'{family.value} is {expected.value}-scoped, but the request '
                f'declares {declared!r}'
            )

        entries = [
            {
                'reference_id': entry['reference_id'],
                'identity_key': entry['identity_key'],
                # 창구에서 거부한다 — 저장된 뒤에는 이 API 로 고칠 수 없다.
                'payload': validate_entry_payload_values(
                    family,
                    entry['reference_id'],
                    validate_entry_payload_shape(
                        family, entry['reference_id'], entry['payload'],
                    ),
                ),
                'test_condition_ids': entry.get('test_condition_ids', []),
                'effective_from': entry.get('effective_from'),
                'effective_to': entry.get('effective_to'),
                'source_sheet_name': entry.get('source_sheet_name'),
                'source_row_number': entry.get('source_row_number'),
                'content_sha256': entry['content_sha256'],
            }
            for entry in request['entries']
        ]
        content_sha256 = canonical_semantic_hash(
            [entry['content_sha256'] for entry in entries]
        )
        revision = {
            'family': family.value,
            'profile_id': request['profile_id'],
            'scope_kind': declared,
            'scope_id': request['scope_id'],
            'etag': canonical_semantic_hash({
                'family': family.value,
                'profile_id': request['profile_id'],
                'scope_id': request['scope_id'],
                'content': content_sha256,
            }),
            'content_sha256': content_sha256,
            'source_snapshot_id': request['source_snapshot_id'],
            'source_manifest_sha256': request['source_manifest_sha256'],
            'forked_from_revision_id': request.get('forked_from_revision_id'),
            # Not read from ``request``: which operation ran IS the provenance,
            # and this one is the workbook importer. Accepting it from the body
            # would make a forgeable claim out of an audit fact.
            'provenance_kind': RevisionProvenanceKind.WORKBOOK.value,
            'created_by': created_by,
        }
        return self._write.create_candidate(
            provider_id, revision=revision, entries=entries,
        )

    def fork_published(
        self, provider_id: str, revision_id: str, *, forked_by: str,
    ) -> dict:
        """Copy a published revision into a new candidate for editing.

        This is what makes the tester the author. Until it existed the only way
        to produce a candidate was the operator's workbook importer, so a tester
        who re-cabled a chamber and re-measured the loss had to wait for someone
        else — and the workbook stayed authoritative for as long as the wait.

        Only a PUBLISHED revision may be forked. Forking a candidate would be a
        second copy of something nobody has agreed to yet; the thing to do with
        a candidate is edit it.

        The entries are copied VERBATIM, which is why the child's content hash
        and etag equal the parent's. That is not a collision to be designed
        around — it is what a content-addressed tag means, and it is exactly the
        property that lets the diff view say "nothing has changed yet" without
        computing anything.
        """
        if self._write is None:
            raise RuntimeError('central reference writes are not configured')

        self._require_registered_provider(provider_id)

        source = self._require_owned_revision(provider_id, revision_id)
        if source['state'] != _PUBLISHED:
            raise ReferenceStateConflictError(
                f'revision {revision_id!r} is {source["state"]}, not '
                f'{_PUBLISHED}; only a published revision may be forked, '
                f'because a candidate is already yours to edit'
            )

        entries = [
            {
                'reference_id': entry['reference_id'],
                'identity_key': entry['identity_key'],
                'payload': _decode_payload(entry['payload_json']),
                'test_condition_ids': _decode_id_list(entry.get('test_condition_ids_json')),
                'effective_from': entry.get('effective_from'),
                'effective_to': entry.get('effective_to'),
                'source_sheet_name': entry.get('source_sheet_name'),
                'source_row_number': entry.get('source_row_number'),
                'content_sha256': entry['content_sha256'],
            }
            for entry in self._read.read_entries(revision_id)
        ]
        revision = {
            'family': source['family'],
            'profile_id': source['profile_id'],
            'scope_kind': source['scope_kind'],
            'scope_id': source['scope_id'],
            'etag': source['etag'],
            'content_sha256': source['content_sha256'],
            # The snapshot link travels unchanged: it says where this edition
            # STARTED, and the fork did start there. Whether the values are
            # still that snapshot's is the other question, and provenance_kind
            # is what answers it.
            'source_snapshot_id': source['source_snapshot_id'],
            'source_manifest_sha256': source['source_manifest_sha256'],
            'forked_from_revision_id': str(source['revision_id']),
            # Inherited, not stamped FORK_EDIT. A copy nobody has edited still
            # holds the parent's values, and claiming otherwise would assert a
            # human change that has not happened. The promotion belongs to the
            # edit, at the moment a value actually moves.
            'provenance_kind': (
                source.get('provenance_kind')
                or RevisionProvenanceKind.WORKBOOK.value
            ),
            'created_by': forked_by,
        }
        created = self._write.create_candidate(
            provider_id, revision=revision, entries=entries,
        )
        return self._revision_detail(provider_id, created['revision_id'])

    def update_candidate_entries(
        self, provider_id: str, revision_id: str, *, request: Mapping,
        updated_by: str,
    ) -> dict:
        """Change values in named rows of a candidate.

        Only the rows named in ``edits`` travel, and only their payloads. A
        correction curve carries thousands of points; re-sending all of them to
        change one would be both wasteful and a lost-update channel, because the
        untouched points in the resend would silently overwrite whatever someone
        else had changed.
        """
        if self._write is None:
            raise RuntimeError('central reference writes are not configured')

        self._require_registered_provider(provider_id)

        source = self._require_owned_revision(provider_id, revision_id)
        if source['state'] != RevisionState.CANDIDATE.value:
            raise ReferenceStateConflictError(
                f'revision {revision_id!r} is {source["state"]}, not '
                f'{RevisionState.CANDIDATE.value}; a published revision is '
                f'immutable — fork it and edit the fork'
            )

        expected_etag = str(request.get('expected_etag') or '')
        if not expected_etag:
            # Refused rather than defaulted to "whatever is there now": an edit
            # with no concurrency token is an edit that will happily discard
            # somebody else's number.
            raise ReferenceEntryEditError(
                'expected_etag is required; it is the tag this revision had '
                'when you loaded it, and it is what stops two testers from '
                'silently overwriting each other'
            )

        family = self._require_known_family(source['family'])
        rows = self._read.read_entries(revision_id)
        current = [
            {
                'reference_id': row['reference_id'],
                'payload': _decode_payload(row['payload_json']),
            }
            for row in rows
        ]
        outcome = apply_entry_edits(
            family,
            current,
            [
                EntryEdit(
                    reference_id=str(edit.get('reference_id') or ''),
                    payload=edit.get('payload') or {},
                )
                for edit in request.get('edits') or []
            ],
        )
        if not outcome.changed:
            # Nothing moved, so nothing is written. Stamping a new version and
            # promoting provenance for a resubmission of identical values would
            # make ``FORK_EDIT`` mean "somebody pressed save", when it has to
            # mean "a person's number is in here".
            return self._revision_detail(provider_id, revision_id)

        edited = dict(outcome.payloads)
        entry_hashes: dict[str, str] = {}
        ordered_hashes: list[str] = []
        for row in rows:
            reference_id = str(row['reference_id'])
            if reference_id in edited:
                digest = build_reference_entry_hash({
                    'identity_key': row['identity_key'],
                    'payload': edited[reference_id],
                    'test_condition_ids': _decode_id_list(row.get('test_condition_ids_json')),
                    'effective_from': row.get('effective_from'),
                    'effective_to': row.get('effective_to'),
                })
                entry_hashes[reference_id] = digest
                ordered_hashes.append(digest)
            else:
                ordered_hashes.append(row['content_sha256'])

        content_sha256 = canonical_semantic_hash(ordered_hashes)
        self._write.update_candidate_entries(
            revision_id,
            expected_etag=expected_etag,
            etag=canonical_semantic_hash({
                'family': source['family'],
                'profile_id': source['profile_id'],
                'scope_id': source['scope_id'],
                'content': content_sha256,
            }),
            content_sha256=content_sha256,
            # 하드코딩 FORK_EDIT 는 세 번째 kind 가 생기는 순간 거짓말이 된다 —
            # 웹에서 처음부터 저작한 후보를 편집하면 그 값들은 여전히 워크북 편집에서
            # 온 것이 아닌데 감사 칸에 그렇게 적힌다. 격자는 도메인 SSOT 가 안다.
            provenance_kind=promote_provenance(
                _provenance_of(source)
            ).value,
            updated_by=updated_by,
            payloads={
                reference_id: json.dumps(payload, sort_keys=True)
                for reference_id, payload in edited.items()
            },
            entry_hashes=entry_hashes,
        )
        return self._revision_detail(provider_id, revision_id)

    def list_reference_families(
        self, provider_id: Optional[str] = None,
    ) -> list[dict]:
        """어떤 패밀리를 저작할 수 있고, 각각 어떤 칸이 필요한가.

        ⚠️ **provider 를 받는 이유는 답이 달라져서가 아니라 주소가 주장하기 때문이다.**
        이 투영은 오늘 provider 무관하게 같지만(정책이 하나만 적재돼 있다), 경로는
        `/platform/providers/{id}/reference-families` 이고 그 `{id}` 는 *주장*이다.
        검증하지 않으면 미등록 provider 가 `200` 에 패밀리 목록을 받는다 — 화면은
        저작 폼을 그리고, 시험원이 채워 넣고, **저장 버튼에서** 404 를 만난다.
        형제 operation 이 전부 해소하는데 이 하나만 안 하면 그 자리가 정확히
        이 웨이브가 없애는 침묵의 마지막 조각으로 남는다.

        ``provider_id=None`` 은 **주소 없는 호출**(시드·내부 조립)이고 해소하지
        않는다 — 주장이 없으면 검증할 것도 없다.

        DB 를 보지 않는다 — 순수 도메인 정책(``PROJECTION_FIELD_CONTRACT`` /
        ``IDENTITY_FIELD_CONTRACT`` / ``COUPLED_FAMILY_GROUPS`` / 스코프 축)의
        투영이다. 저작 화면은 **리비전이 하나도 없는 패밀리**에도 이 답이 필요하고,
        그것이 상세 응답만으로는 답할 수 없는 유일한 경우다.

        저작 불가 패밀리는 목록에 없다 — 없는 것의 선택지를 그리면 시험원이 고른 뒤
        400 을 받는다. 판정 기준은 발명하지 않고 ``scope_kind_for`` 가 답을 갖는지로
        본다(그 함수가 이미 "이 패밀리는 저작 축에 없다"를 raise 로 말한다).
        """
        if provider_id is not None:
            self._require_registered_provider(provider_id)
        families: list[dict] = []
        for family in CatalogFamily:
            try:
                scope_kind = scope_kind_for(family)
                payload_columns = list(projection_fields_for(family))
            except (ReferenceScopeError, KeyError, ValueError):
                continue
            families.append({
                'family': family.value,
                'scope_kind': scope_kind.value,
                'payload_columns': payload_columns,
                'identity_columns': list(identity_fields_for(family)[0]),
                'coupled_with': self._coupled_sibling(family),
                'default_profile_id': DEFAULT_REFERENCE_PROFILE_ID,
            })
        return families

    def create_authored_candidate(
        self, provider_id: str, *, request: Mapping, created_by: str,
    ) -> dict:
        """Create a candidate that was authored on the web — no workbook behind it.

        This is a **separate operation** from the workbook importer, not a relaxed
        version of it. Which operation ran IS the provenance (the rule 019 set),
        so making ``source_snapshot_id`` optional on the import path would have
        turned an audit fact into something the request shape decides.

        Nothing derived travels in the request. ``reference_id``, ``identity_key``
        and every hash are minted here from the payloads: a client that supplies
        its own identity can store a row whose ``identity_key`` does not describe
        it, and that mismatch only surfaces when the projection fills the table
        the measurement path reads.
        """
        if self._write is None:
            raise RuntimeError('central reference writes are not configured')

        self._require_registered_provider(provider_id)

        family = self._require_known_family(request['family'])
        declared = str(request['scope_kind'])
        expected = scope_kind_for(family)
        if declared != expected.value:
            raise ReferenceScopeError(
                f'{family.value} is {expected.value}-scoped, but the request '
                f'declares {declared!r}'
            )

        payloads = list(request.get('entries') or [])
        if not payloads:
            # 빈 리비전은 게시되면 그 패밀리의 런타임 테이블을 비운다 — "아직 안 채웠다"와
            # "이 방에는 아무것도 없다"가 같은 값이 되어선 안 된다.
            raise ReferenceRowEditError(
                'an authored revision needs at least one row; publishing an empty '
                'one would empty the runtime table for this family'
            )
        outcome = apply_row_edits(
            family, [], additions=_row_payloads(family, payloads), removals=[],
        )
        entries = [entry.to_entry() for entry in outcome.additions]
        content_sha256 = canonical_semantic_hash(
            [entry['content_sha256'] for entry in entries]
        )
        revision = {
            'family': family.value,
            'profile_id': request['profile_id'],
            'scope_kind': declared,
            'scope_id': request['scope_id'],
            'etag': canonical_semantic_hash({
                'family': family.value,
                'profile_id': request['profile_id'],
                'scope_id': request['scope_id'],
                'content': content_sha256,
            }),
            'content_sha256': content_sha256,
            # 워크북 스냅샷이 **없다**. 지어내지 않고 비운다 — 022 의 CHECK 가
            # 이 조합(WEB_AUTHORED + NULL)만 허용하고 나머지는 계속 막는다.
            'source_snapshot_id': None,
            'source_manifest_sha256': None,
            'forked_from_revision_id': None,
            'provenance_kind': RevisionProvenanceKind.WEB_AUTHORED.value,
            'created_by': created_by,
        }
        return self._write.create_candidate(
            provider_id, revision=revision, entries=entries,
        )

    def update_candidate_rows(
        self, provider_id: str, revision_id: str, *, request: Mapping,
        updated_by: str,
    ) -> dict:
        """Add and remove **rows** of a candidate in one transaction.

        Deliberately not folded into the value-edit operation. That one refuses
        any change to an identity field because *"changing one is an add plus a
        remove, not an edit"* — this is that add and that remove, said out loud,
        so a typo in an identity cell and an intended row replacement never look
        like the same request.
        """
        if self._write is None:
            raise RuntimeError('central reference writes are not configured')

        self._require_registered_provider(provider_id)

        source = self._require_owned_revision(provider_id, revision_id)
        if source['state'] != RevisionState.CANDIDATE.value:
            raise ReferenceStateConflictError(
                f'revision {revision_id!r} is {source["state"]}, not '
                f'{RevisionState.CANDIDATE.value}; a published revision is '
                f'immutable — fork it and edit the fork'
            )
        expected_etag = str(request.get('expected_etag') or '')
        if not expected_etag:
            raise ReferenceRowEditError(
                'expected_etag is required; it is the tag this revision had '
                'when you loaded it, and it is what stops two testers from '
                'silently overwriting each other'
            )

        family = self._require_known_family(source['family'])
        rows = self._read.read_entries(revision_id)
        outcome = apply_row_edits(
            family,
            [
                {
                    'reference_id': row['reference_id'],
                    'identity_key': row['identity_key'],
                }
                for row in rows
            ],
            additions=_row_payloads(family, request.get('additions') or []),
            removals=request.get('removals') or [],
        )

        removed = set(outcome.removals)
        ordered_hashes = [
            row['content_sha256'] for row in rows
            if str(row['reference_id']) not in removed
        ]
        ordered_hashes.extend(entry.content_sha256 for entry in outcome.additions)
        if not ordered_hashes:
            # 마지막 행까지 지우는 것은 거부한다 — 위 create 와 같은 사유다.
            raise ReferenceRowEditError(
                'removing every row would leave a revision that empties the '
                'runtime table for this family when published'
            )
        content_sha256 = canonical_semantic_hash(ordered_hashes)

        # ``entry_order`` 는 이어 붙인다(재번호 없음). 유니크 인덱스는 유일성만
        # 요구하고, 재번호는 남은 전 행을 다시 쓰는 일이라 16k 행 표에서 성립하지 않는다.
        next_order = max(
            (int(row.get('entry_order') or 0) for row in rows), default=-1,
        ) + 1
        additions = []
        for offset, entry in enumerate(outcome.additions):
            record = entry.to_entry()
            record['entry_order'] = next_order + offset
            additions.append(record)

        self._write.update_candidate_rows(
            revision_id,
            expected_etag=expected_etag,
            etag=canonical_semantic_hash({
                'family': source['family'],
                'profile_id': source['profile_id'],
                'scope_id': source['scope_id'],
                'content': content_sha256,
            }),
            content_sha256=content_sha256,
            provenance_kind=promote_provenance(_provenance_of(source)).value,
            updated_by=updated_by,
            additions=additions,
            removals=list(outcome.removals),
        )
        return self._revision_detail(provider_id, revision_id)

    def publish(
        self, provider_id: str, revision_id: str, *, published_by: str,
        publish_reason: Optional[str] = None,
        coupled_revision_id: Optional[str] = None,
    ) -> dict:
        """Publish one revision — or both halves of a coupled group at once.

        Takes the provider for the same reason ``fork`` does, and it matters
        more here because this is a WRITE: until this wave the path asserted a
        provider that nothing read, so naming another provider's candidate id
        published it. The coupled partner is checked too — otherwise one's own
        primary could be published alongside a partner belonging to somebody
        else, which is the cross-provider version of the very pairing this
        coupling exists to prevent.

        The coupling is enforced HERE, at the service boundary, not in the UI.
        The projection already refuses a half-published group and leaves both
        families workbook-owned, so nothing wrong reaches an instrument either
        way; what this closes is the dead end. Without it a tester publishes one
        half, the origin answers 200, the chamber changes nothing, and the only
        signal is an ERROR line on a PC nobody watches. A rule enforced only by
        one screen is a rule that holds for exactly that screen — and the fact
        that this wave writes the only client today makes that weaker, not
        stronger, because the next client will not remember.
        """
        if self._write is None:
            raise RuntimeError('central reference writes are not configured')

        self._require_registered_provider(provider_id)

        revision = self._require_owned_revision(provider_id, revision_id)
        sibling = self._coupled_sibling(revision['family'])

        if sibling is None:
            if coupled_revision_id:
                raise ReferenceCoupledPublishError(
                    f'{revision["family"]} is not part of a coupled family group, '
                    f'so coupled_revision_id must be omitted'
                )
            return self._write.publish(
                revision_id,
                published_by=published_by,
                published_at=self._now(),
                publish_reason=publish_reason,
            )

        if not coupled_revision_id:
            raise ReferenceCoupledPublishError(
                f'{revision["family"]} and {sibling.value} are two halves of one '
                f'physical fact (which cable carries the signal, and what that '
                f'cable costs in dB). Publishing one alone pairs one antenna\'s '
                f'signal path with another\'s path loss and the measurement is '
                f'silently wrong. Supply coupled_revision_id — the '
                f'{sibling.value} candidate for the same scope.'
            )

        try:
            partner = self._require_owned_revision(provider_id, coupled_revision_id)
        except ReferenceRevisionNotFoundError:
            # Re-raised naming the field the caller supplied, so the message
            # says which of the two ids was wrong. A partner belonging to
            # another provider is answered identically to one that does not
            # exist — the same rule the primary follows.
            raise ReferenceRevisionNotFoundError(
                f'unknown coupled_revision_id {coupled_revision_id!r}'
            ) from None
        if partner['family'] != sibling.value:
            raise ReferenceCoupledPublishError(
                f'coupled_revision_id must name a {sibling.value} revision, '
                f'not {partner["family"]}'
            )
        if str(partner['scope_id']) != str(revision['scope_id']):
            # Different rooms. Publishing them together would pair one room's
            # port map with another room's cable loss — the very failure the
            # coupling exists to prevent, produced by the mechanism meant to
            # prevent it.
            raise ReferenceCoupledPublishError(
                f'the coupled revisions target different scopes '
                f'({revision["scope_id"]!r} and {partner["scope_id"]!r}); a '
                f'cable loss belongs to the room whose port map it accompanies'
            )

        published = self._write.publish_coupled(
            revision_id,
            coupled_revision_id,
            published_by=published_by,
            published_at=self._now(),
            publish_reason=publish_reason,
        )
        primary = published[0]
        return {**primary, 'coupled': published[1:]}

    # ------------------------------------------------------- provider identity

    def _require_registered_provider(self, provider_id: str) -> None:
        """Refuse before doing anything else when the provider is not registered.

        Every other statement in this catalog *filters* on the provider, so an
        unregistered one produces the same empty result as a registered one with
        nothing in it. That equality is the whole defect: the screen renders
        "nothing here yet" for a provider whose every write will 404, and the two
        facts only separate at the first authoring attempt — long after anyone
        would connect it to registration.

        Callers must run this AFTER authorization. The registration state of a
        provider is not something an unauthorized caller should be able to
        probe, and every route reaching this method already authorizes first.
        """
        offered = None
        if self._offered_provider_ids is not None:
            offered = tuple(self._offered_provider_ids())
        status = classify_provider_identity(
            provider_id,
            offered=offered,
            registered=self._read.provider_exists(provider_id),
        )
        if status.is_coherent:
            return
        if status is ProviderIdentityStatus.NOT_REGISTERED:
            # WHICH side is missing goes to the log — that is the operator's
            # half and no client needs it. The RFC 9457 ``params`` member stays
            # empty because ``PROBLEM_PARAM_ALLOWLIST`` admits resource KINDS,
            # never a caller-supplied id, and machine-readable context is what
            # ``params`` is for.
            #
            # ⚠️ ``detail`` below DOES name the provider id, and that is
            # deliberate rather than an oversight: it is the caller's own input
            # echoed back in a human sentence, which discloses nothing and is
            # what makes the message actionable when several providers are on
            # screen. An earlier version of this comment claimed the id never
            # reaches the response body; it always did, and a comment that
            # describes code it sits beside inaccurately is worse than none.
            logger.warning(
                'reference provider is offered by the UI descriptor registry '
                'but has no central providers row; authoring is unreachable '
                'until an operator registers it',
                extra={
                    'provider_id': str(provider_id),
                    # Both sides of the fact, so the line answers on its own.
                    # ``offering_observed`` is False when no registry was
                    # injected — then "offered" is an inference the degrade rule
                    # made, not something anyone looked at, and a log that
                    # cannot tell those apart sends an operator hunting.
                    'offering_observed': offered is not None,
                    'registered_centrally': False,
                },
            )
            raise ReferenceProviderNotRegisteredError(
                f'provider {provider_id!r} is offered by this deployment but no '
                'central providers row registers it; an operator must register '
                'the provider before reference data can be listed or authored'
            )
        raise ReferenceProviderNotFoundError(
            f'unknown provider_id {provider_id!r}; providers are '
            'operator-registered reference data'
        )

    def _require_owned_revision(
        self, provider_id: str, revision_id: str,
    ) -> dict:
        """Read a revision and refuse when it belongs to another provider.

        The refusal is byte-identical to the one for a revision that does not
        exist — the rule ``fork_published`` already established, kept verbatim
        here so the surface cannot become an oracle for which revision ids are
        real on one operation while staying closed on its siblings.
        """
        revision = self._read.read_revision(revision_id)
        if revision is None or str(revision['provider_id']) != str(provider_id):
            raise ReferenceRevisionNotFoundError(
                f'unknown revision_id {revision_id!r}'
            )
        return revision

    @staticmethod
    def _coupled_sibling(family_value: str) -> Optional[CatalogFamily]:
        """The other half of ``family_value``'s coupled group, if it has one.

        Derived from the domain SSOT ``COUPLED_FAMILY_GROUPS``. Naming the pair
        here as literals would put the coupling vocabulary in two places, which
        is the defect class ``/verify-channel-match-key-ssot`` exists for.
        """
        for group in COUPLED_FAMILY_GROUPS:
            members = {member.value: member for member in group}
            if family_value in members:
                others = [m for v, m in members.items() if v != family_value]
                return others[0] if len(others) == 1 else None
        return None

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _revision_cursor(row: Mapping) -> str:
        """Encode the keyset position of one revision row.

        Spelled once and shared by the listing and the bundle. Both page over the
        same index, and two encoders would be two chances for a cursor to be
        produced on one axis and decoded on another.
        """
        return encode_cursor([
            row['family'],
            row['profile_id'],
            row['scope_id'],
            row['revision_number'],
            row['revision_id'],
        ])

    @staticmethod
    def _bundle_etag(rows: Sequence[Mapping]) -> str:
        """Derive the cut's tag from the etags it contains.

        Sorted, so the tag depends on the SET of revisions and not on the order
        a query happened to return them in. An order-sensitive tag would make a
        chamber re-download whenever the planner changed its mind.
        """
        return canonical_semantic_hash(sorted(
            str(row.get('etag', '')) for row in rows
        ))

    @staticmethod
    def _require_known_family(value: Any) -> CatalogFamily:
        try:
            return CatalogFamily(str(value))
        except ValueError as exc:
            raise ReferenceScopeError(
                f'unknown reference family {value!r}'
            ) from exc

    def _now(self) -> str:
        if self._clock is not None:
            return self._clock()
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'

    @staticmethod
    def _revision_summary(row: Mapping) -> dict:
        return {
            'revision_id': str(row['revision_id']),
            'provider_id': row['provider_id'],
            'family': row['family'],
            'profile_id': row['profile_id'],
            'scope_kind': row['scope_kind'],
            'scope_id': row['scope_id'],
            'revision_number': row['revision_number'],
            'state': row['state'],
            'version': row['version'],
            'etag': row['etag'],
            'content_sha256': row['content_sha256'],
            'source_snapshot_id': row['source_snapshot_id'],
            'source_manifest_sha256': row['source_manifest_sha256'],
            'official_manifest_sha256': row.get('official_manifest_sha256'),
            'forked_from_revision_id': (
                str(row['forked_from_revision_id'])
                if row.get('forked_from_revision_id') is not None else None
            ),
            # Read back verbatim, with WORKBOOK standing in only for a row read
            # from a DB that has not run migration 017 yet. That is the same fact
            # the migration's backfill records — every pre-017 row was written by
            # the workbook importer, the only writer that has ever existed — so
            # this is a known value rather than a guess dressed as one.
            'provenance_kind': (
                row.get('provenance_kind')
                or RevisionProvenanceKind.WORKBOOK.value
            ),
            'entry_count': row.get('entry_count', 0),
            'created_by': row['created_by'],
            'created_at': str(row['created_at']),
            'updated_by': row['updated_by'],
            'updated_at': str(row['updated_at']),
            'approved_by': row.get('approved_by'),
            'approved_at': _optional_text(row.get('approved_at')),
            'approval_reason': row.get('approval_reason'),
            'published_by': row.get('published_by'),
            'published_at': _optional_text(row.get('published_at')),
            'publish_reason': row.get('publish_reason'),
            'retired_by': row.get('retired_by'),
            'retired_at': _optional_text(row.get('retired_at')),
            'retirement_reason': row.get('retirement_reason'),
        }

    @staticmethod
    def _entry_record(entry: Mapping) -> dict:
        return {
            'reference_id': entry['reference_id'],
            'identity_key': entry['identity_key'],
            'entry_order': entry['entry_order'],
            'payload': _decode_payload(entry['payload_json']),
            'test_condition_ids': _decode_id_list(entry.get('test_condition_ids_json')),
            'effective_from': entry.get('effective_from'),
            'effective_to': entry.get('effective_to'),
            'source_sheet_name': entry.get('source_sheet_name'),
            'source_row_number': entry.get('source_row_number'),
            'content_sha256': entry['content_sha256'],
        }


def _row_payloads(
    family: CatalogFamily, entries: Sequence[Mapping],
) -> list[Mapping]:
    """``[{'payload': {...}}, ...]`` → payload 목록.

    요청 엔트리를 감싼 이유는 나중에 payload 옆에 다른 것(예: 유효기간)이 붙을 수
    있어서이고, 그때 스키마를 바꾸지 않아도 되게 하기 위해서다. 지금은 감싼 것을
    푸는 자리가 여기 하나다 — 정책은 payload 만 본다.
    """
    unwrapped: list[Mapping] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping) or 'payload' not in entry:
            raise ReferenceRowEditError(
                'each added row must be an object with a "payload" — the '
                'identity and every hash are minted by the server'
            )
        payload = validate_entry_payload_shape(
            family, f'<new row {index}>', entry['payload'],
        )
        unwrapped.append(validate_entry_payload_values(
            family, f'<new row {index}>', payload,
        ))
    return unwrapped


def _provenance_of(revision: Mapping) -> RevisionProvenanceKind:
    """저장된 provenance → enum. 미지의 값은 WORKBOOK 으로 읽지 않는다.

    ``fork_published`` 가 쓰는 ``or WORKBOOK`` 관용구는 컬럼이 없던 시절의 행을
    위한 것이고 여기서도 같다. 다만 **모르는 토큰**은 다른 사실이라 loud 하게
    실패한다 — 조용히 WORKBOOK 으로 접으면 감사 칸이 틀린 값을 갖는다.
    """
    raw = revision.get('provenance_kind')
    if raw in (None, ''):
        return RevisionProvenanceKind.WORKBOOK
    return RevisionProvenanceKind(str(raw))


def _optional_text(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _decode_payload(value: Any) -> dict:
    """Return an entry payload as a mapping, whatever the driver handed back.

    Loud on anything that is not an object. ``dict(value or {})`` looked
    harmless and was not: given a list of two-character strings psycopg would
    hand back, it **silently reinterprets them as key/value pairs**
    (``['ab','cd'] → {'a':'b','c':'d'}``) — inventing a row nobody wrote.
    Refusing names the revision instead of manufacturing data (adversarial
    review, 2026-08-09).
    """
    decoded = json.loads(value) if isinstance(value, str) else value
    if decoded is None:
        return {}
    if not isinstance(decoded, Mapping):
        raise ReferenceEntryEditError(
            f'a stored entry payload is {type(decoded).__name__}, not an '
            f'object; it cannot be projected into a runtime row. This row '
            f'predates the creation-time shape check and must be replaced by '
            f'a new revision rather than edited.'
        )
    return dict(decoded)


def _decode_id_list(value: Any) -> list:
    """Return ``test_condition_ids`` as a list, whatever the driver handed back.

    The ``or []`` idiom that reads a decoded column is a trap on an encoded one:
    the empty list arrives as the STRING ``'[]'``, which is truthy, so the
    fallback never fires and the string travels on as if it were the list. It
    then reaches the entry digest, and the same row hashes differently depending
    on which driver read it — a difference that would surface as a diff claiming
    a value changed when nothing did.
    """
    if isinstance(value, str):
        return list(json.loads(value))
    return list(value or [])
