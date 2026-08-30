"""Central PostgreSQL reference-catalog write adapter (Wave 3, 2026-08-07).

``PostgresCentralReferenceWriteAdapter`` implements ``CentralReferenceWritePort``
against the central ``reference_revisions`` / ``reference_entries`` tables.

Design (mirrors ``PostgresCentralReportWriteAdapter``):

- **injected ``connection_factory``** — no PostgreSQL driver imported here.
- **``%s`` paramstyle**, one transaction per operation.
- **loud-fail** → ``CentralReferenceError`` (503), distinct from the typed
  conflict and not-found errors the routes map to 409 and 404.

PUBLISHING IS ONE CONDITIONAL STATEMENT
----------------------------------------
At most one revision may be PUBLISHED per ``(provider, family, profile, scope)``.
Both preconditions — this revision is still a candidate, and the slot is free —
live in the UPDATE's WHERE clause, so there is no window between checking and
acting. A check-then-set would let two operators publishing at the same moment
both pass the check, and a chamber would then pull an ambiguous answer that its
local reader can only diagnose after the damage exists.

When nothing moves, a single diagnostic statement says why, and the adapter never
reads a driver error message to find out. Parsing constraint text is forbidden
here: it binds behaviour to a DDL naming convention, and this table carries
several unique indexes so an error class alone cannot say which one lost.
``ux_reference_revisions_published`` remains as the backstop for a genuinely
simultaneous second writer; that case surfaces as a backend error, and a retry
immediately receives the deterministic conflict.

The revision number is likewise assigned by the database, not by the caller —
``SELECT COALESCE(MAX(...)+1, 1)`` inside the same statement — so two importers
racing on one identity cannot both claim number 3. The unique index on
``(identity, revision_number)`` is the backstop if they somehow do.

WHAT THIS ADAPTER DOES NOT DO
------------------------------
It never derives values. Etags, content hashes and identity keys arrive already
computed by the domain hashing SSOT, because this package may not import hashlib
and because a second hasher would make a chamber's etag comparison meaningless.
"""
from __future__ import annotations

import json
from typing import Callable, Mapping, Optional, Sequence

from domain.models.reference_catalog import RevisionState
from domain.ports.output.central_reference_port import (
    CentralReferenceError,
    ReferenceProviderNotFoundError,
    ReferencePublishConflictError,
    ReferenceRevisionNotFoundError,
    ReferenceStateConflictError,
)
from domain.ports.output.platform_database_port import DbConnection


__all__ = ['PostgresCentralReferenceWriteAdapter']


_CANDIDATE_STATE = RevisionState.CANDIDATE.value
_PUBLISHED_STATE = RevisionState.PUBLISHED.value
_RETIRED_STATE = RevisionState.RETIRED.value

#: Columns the caller supplies for a new revision, in bind order. The database
#: owns id, created_at, updated_at and revision_number; listing them here would
#: reintroduce the caller-stamped drift the ingestion contract already removed.
_REVISION_INSERT = (
    'INSERT INTO "reference_revisions" ('
    '"provider_id", "family", "profile_id", "scope_kind", "scope_id", '
    '"revision_number", "state", "version", "etag", "content_sha256", '
    '"source_snapshot_id", "source_manifest_sha256", "forked_from_revision_id", '
    '"provenance_kind", "created_by", "updated_by"'
    ') SELECT "p"."id", %s, %s, %s, %s, '
    'COALESCE((SELECT MAX("r2"."revision_number") + 1 FROM "reference_revisions" r2 '
    'WHERE "r2"."provider_id" = "p"."id" AND "r2"."family" = %s '
    'AND "r2"."profile_id" = %s AND "r2"."scope_id" = %s), 1), '
    '%s, %s, %s, %s, %s, %s, %s, %s, %s, %s '
    'FROM "providers" p WHERE "p"."provider_id" = %s '
    'RETURNING "id", "revision_number"'
)

_ENTRY_INSERT = (
    'INSERT INTO "reference_entries" ('
    '"revision_id", "entry_order", "reference_id", "identity_key", '
    '"payload_json", "test_condition_ids_json", "effective_from", '
    '"effective_to", "source_sheet_name", "source_row_number", "content_sha256"'
    ') VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)'
)

#: NOTE: ``version`` is deliberately NOT incremented here. The concurrency
#: control is the conditional WHERE plus the partial unique index; adding a
#: version compare-and-swap would be a SECOND mechanism for the same job, and
#: ADR-0019 declines version CAS precisely because no central entity has a
#: lost-update surface that would justify one. ``version`` exists so a chamber
#: replica can be reconstructed faithfully, not as a concurrency token.
#:
#: Publishing is ONE CONDITIONAL STATEMENT. Both preconditions — this revision is
#: still a candidate, and no sibling already holds the published slot — live in
#: the WHERE clause, so there is no window between checking and acting. A prior
#: SELECT would let a revision be retired, or a sibling published, in between.
#:
#: This is also why the adapter never inspects a driver error message. Parsing
#: constraint text to tell a lost race from an outage is forbidden: it binds
#: behaviour to a DDL naming convention, and this table carries several unique
#: indexes so the error class alone cannot say which one lost. The conditional
#: statement answers the question without asking the driver.
_PUBLISH_UPDATE = (
    'UPDATE "reference_revisions" SET "state" = %s, "published_by" = %s, '
    '"published_at" = %s, "publish_reason" = %s, "updated_by" = %s, '
    '"updated_at" = now() '
    'WHERE "id" = %s AND "state" = %s '
    'AND NOT EXISTS (SELECT 1 FROM "reference_revisions" x '
    'WHERE "x"."provider_id" = "reference_revisions"."provider_id" '
    'AND "x"."family" = "reference_revisions"."family" '
    'AND "x"."profile_id" = "reference_revisions"."profile_id" '
    'AND "x"."scope_id" = "reference_revisions"."scope_id" '
    'AND "x"."state" = %s) '
    'RETURNING "id"'
)

#: Retire whatever currently holds the published slot for the SAME identity as
#: the revision about to be published. Scoped by a correlated subquery on the
#: incoming revision's own identity columns rather than by values the caller
#: passes, so a caller cannot aim this at another room's published revision.
#:
#: ``"x"."id" <> %s`` keeps a re-publish of the already-published row from
#: retiring itself, which would turn an idempotent no-op into data loss.
_SUPERSEDE_UPDATE = (
    'UPDATE "reference_revisions" SET "state" = %s, "retired_by" = %s, '
    '"retired_at" = %s, "retirement_reason" = %s, "updated_by" = %s, '
    '"updated_at" = now() '
    'WHERE "id" IN (SELECT "x"."id" FROM "reference_revisions" x '
    'JOIN "reference_revisions" r ON "r"."id" = %s '
    'WHERE "x"."provider_id" = "r"."provider_id" '
    'AND "x"."family" = "r"."family" '
    'AND "x"."profile_id" = "r"."profile_id" '
    'AND "x"."scope_id" = "r"."scope_id" '
    'AND "x"."state" = %s AND "x"."id" <> "r"."id")'
)

#: Why nothing moved: the row's own state, and whether a sibling already holds
#: the slot. Two facts from one statement, so the answer cannot be assembled from
#: two different moments.
_PUBLISH_DIAGNOSIS = (
    'SELECT "r"."state", EXISTS (SELECT 1 FROM "reference_revisions" x '
    'WHERE "x"."provider_id" = "r"."provider_id" AND "x"."family" = "r"."family" '
    'AND "x"."profile_id" = "r"."profile_id" AND "x"."scope_id" = "r"."scope_id" '
    'AND "x"."state" = %s) AS "slot_taken" '
    'FROM "reference_revisions" r WHERE "r"."id" = %s'
)


#: Editing is also ONE CONDITIONAL STATEMENT, for the same reason publishing is:
#: both preconditions — still a candidate, and nobody has changed it since the
#: caller read it — live in the WHERE clause, so nothing can slip in between.
#:
#: The compare-and-swap is on ``etag``, NOT on ``version``. That is the whole
#: distinction ADR-0019 turns on. A version column is a counter somebody has to
#: remember to bump on every write, and the ADR declined it precisely because
#: maintaining one buys nothing when no central entity has a lost-update
#: surface. ``etag`` is DERIVED from content, so it cannot be forgotten: any
#: write that changes a value changes the tag by construction, and two identical
#: concurrent edits converge on the same tag instead of fighting over a counter.
#:
#: ``version`` is therefore left alone here, exactly as publish leaves it alone.
#: It exists so a chamber replica can be reconstructed faithfully — a schema
#: obligation, not a concurrency token — and incrementing it would introduce
#: half of the mechanism the ADR declined while adding nothing this statement
#: does not already get from the tag. Sealed by
#: ``tests/test_central_write_concurrency_model.py::TestVersionCasDriftGuard``.
_ENTRY_EDIT_REVISION_UPDATE = (
    'UPDATE "reference_revisions" SET "etag" = %s, "content_sha256" = %s, '
    '"provenance_kind" = %s, "updated_by" = %s, '
    '"updated_at" = now() '
    'WHERE "id" = %s AND "state" = %s AND "etag" = %s '
    'RETURNING "id", "version"'
)

#: Why nothing moved: the row's own state and its current tag. A caller holding
#: a stale tag and a caller pointing at a published revision are different
#: mistakes, and only one of them is fixed by reloading.
#: 이 어댑터의 **첫 DELETE** 다. 여기까지는 참조 엔트리가 후보 생성 시 한 번 쓰이고
#: 값만 바뀌었으므로 지울 일이 없었다. 행 추가·삭제 축이 열리면서 그 사실이 바뀐다.
#: 대상은 항상 (revision_id, reference_id) — UNIQUE 인덱스를 가진 유일한 키이고,
#: identity_key 로 지우면 두 답이 가능한 키로 쓰기 대상을 지목하게 된다.
_ENTRY_DELETE = (
    'DELETE FROM "reference_entries" '
    'WHERE "revision_id" = %s AND "reference_id" = %s'
)

_ENTRY_EDIT_DIAGNOSIS = (
    'SELECT "state", "etag" FROM "reference_revisions" WHERE "id" = %s'
)

#: Only the named rows move. ``identity_key`` and ``entry_order`` are untouched
#: because the edit policy has already refused any change that would move them,
#: so this statement cannot renumber a curve or orphan a diff join key.
_ENTRY_PAYLOAD_UPDATE = (
    'UPDATE "reference_entries" SET "payload_json" = %s, "content_sha256" = %s '
    'WHERE "revision_id" = %s AND "reference_id" = %s'
)


class PostgresCentralReferenceWriteAdapter:
    """``CentralReferenceWritePort`` over a central PostgreSQL connection factory."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def create_candidate(
        self,
        provider_id: str,
        *,
        revision: Mapping,
        entries: Sequence[Mapping],
    ) -> dict:
        """Insert a candidate and its entries in ONE transaction.

        A revision without its entries would be a published-able shell that
        projects zero rows, and a chamber applying it would empty its lookup
        tables while reporting success.
        """
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(_REVISION_INSERT, (
                    revision['family'],
                    revision['profile_id'],
                    revision['scope_kind'],
                    revision['scope_id'],
                    revision['family'],
                    revision['profile_id'],
                    revision['scope_id'],
                    _CANDIDATE_STATE,
                    revision.get('version', 1),
                    revision['etag'],
                    revision['content_sha256'],
                    revision['source_snapshot_id'],
                    revision['source_manifest_sha256'],
                    revision.get('forked_from_revision_id'),
                    # Required, never defaulted here: the service decides it from
                    # WHICH operation ran (import / fork / edit), and a silent
                    # fallback in the adapter would let a future caller record a
                    # provenance nobody chose.
                    revision['provenance_kind'],
                    revision['created_by'],
                    revision.get('updated_by', revision['created_by']),
                    provider_id,
                ))
                inserted = cursor.fetchone()
                if inserted is None:
                    # The SELECT found no provider, so nothing was inserted. That
                    # is a client error, not an outage: providers are registered
                    # by an operator, never ingested.
                    raise ReferenceProviderNotFoundError(
                        f'unknown provider_id {provider_id!r}; providers are '
                        'operator-registered reference data'
                    )
                revision_id, revision_number = inserted[0], inserted[1]
                for order, entry in enumerate(entries):
                    cursor.execute(_ENTRY_INSERT, (
                        revision_id,
                        order,
                        entry['reference_id'],
                        entry['identity_key'],
                        json.dumps(entry['payload'], sort_keys=True),
                        json.dumps(list(entry.get('test_condition_ids', []))),
                        entry.get('effective_from'),
                        entry.get('effective_to'),
                        entry.get('source_sheet_name'),
                        entry.get('source_row_number'),
                        entry['content_sha256'],
                    ))
            finally:
                cursor.close()
            connection.commit()
        except (ReferenceProviderNotFoundError,):
            self._rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rollback(connection)
            raise CentralReferenceError(
                f'central reference candidate write failed: {exc}'
            ) from exc
        finally:
            self._close(connection)
        return {'revision_id': str(revision_id), 'revision_number': revision_number}

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
        """Rewrite named payloads and the revision header in one transaction.

        The revision row moves FIRST. Doing it in that order means the
        conditional WHERE has already decided whether this caller is allowed to
        write at all, so a rejected edit never touches an entry row — and the
        rejection costs one statement rather than a rollback of many.
        """
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(_ENTRY_EDIT_REVISION_UPDATE, (
                    etag,
                    content_sha256,
                    provenance_kind,
                    updated_by,
                    revision_id,
                    _CANDIDATE_STATE,
                    expected_etag,
                ))
                updated = cursor.fetchone()
                if updated is None:
                    self._raise_entry_edit_reason(
                        cursor, revision_id, expected_etag,
                    )
                version = updated[1]
                for reference_id, payload_json in payloads.items():
                    cursor.execute(_ENTRY_PAYLOAD_UPDATE, (
                        payload_json,
                        entry_hashes[reference_id],
                        revision_id,
                        reference_id,
                    ))
                    if cursor.rowcount == 0:
                        # The edit policy resolved every reference_id against
                        # the rows it read, so a miss here means the row went
                        # away between that read and this write. Refusing the
                        # whole transaction is the only outcome that does not
                        # leave a half-applied edit — and a half-applied
                        # correction curve is the silently-wrong-number case
                        # this surface exists to prevent.
                        raise ReferencePublishConflictError(
                            f'row {reference_id!r} is no longer part of '
                            f'revision {revision_id!r}; nothing was written'
                        )
                connection.commit()
            finally:
                cursor.close()
        except (
            ReferenceRevisionNotFoundError,
            ReferencePublishConflictError,
            ReferenceStateConflictError,
        ):
            self._rollback(connection)
            raise
        except Exception as exc:  # pragma: no cover - driver-specific
            self._rollback(connection)
            raise CentralReferenceError(
                f'central reference entry edit failed: {exc}'
            ) from exc
        finally:
            self._close(connection)
        return {'revision_id': str(revision_id), 'version': version}

    @staticmethod
    def _raise_entry_edit_reason(
        cursor, revision_id: str, expected_etag: str,
    ) -> None:
        """Say WHY nothing moved, without parsing a driver message.

        Three outcomes are indistinguishable from a zero-row UPDATE alone, and
        they call for three different actions: the revision is gone (404), it
        is not a candidate (409, and reloading will not help), or somebody else
        wrote first (409, and reloading is exactly what helps).
        """
        cursor.execute(_ENTRY_EDIT_DIAGNOSIS, (revision_id,))
        row = cursor.fetchone()
        if row is None:
            raise ReferenceRevisionNotFoundError(
                f'unknown revision_id {revision_id!r}'
            )
        state, current_etag = row[0], row[1]
        if state != _CANDIDATE_STATE:
            raise ReferenceStateConflictError(
                f'revision {revision_id!r} is {state}, not {_CANDIDATE_STATE}; '
                'a published revision is immutable, so this edit belongs on a '
                'fork of it'
            )
        raise ReferencePublishConflictError(
            f'revision {revision_id!r} changed since it was loaded (expected '
            f'etag {expected_etag!r}, found {current_etag!r}); reload and '
            're-apply so the other edit is not discarded'
        )

    def update_candidate_rows(
        self,
        revision_id: str,
        *,
        expected_etag: str,
        etag: str,
        content_sha256: str,
        provenance_kind: str,
        updated_by: str,
        additions,
        removals,
    ) -> dict:
        """Insert and delete rows, and move the revision header, in one transaction.

        Same ordering as :meth:`update_candidate_entries` and for the same
        reason: the revision row moves FIRST, so the conditional WHERE has
        already decided whether this caller may write at all and a rejected
        request never touches an entry row.

        Removals run before additions. A request that removes a row and adds a
        different row with the SAME identity is legitimate (that is what
        "changing an identity field" actually is), and doing it in the other
        order would hit the unique index on a row that is about to disappear.
        """
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(_ENTRY_EDIT_REVISION_UPDATE, (
                    etag,
                    content_sha256,
                    provenance_kind,
                    updated_by,
                    revision_id,
                    _CANDIDATE_STATE,
                    expected_etag,
                ))
                updated = cursor.fetchone()
                if updated is None:
                    self._raise_entry_edit_reason(
                        cursor, revision_id, expected_etag,
                    )
                version = updated[1]
                for reference_id in removals:
                    cursor.execute(_ENTRY_DELETE, (revision_id, reference_id))
                    if cursor.rowcount == 0:
                        # The policy resolved every reference_id against the rows
                        # it read, so a miss means the row went away between that
                        # read and this write. Refusing the whole transaction is
                        # the only outcome that does not leave a half-applied
                        # change to which rows exist.
                        raise ReferencePublishConflictError(
                            f'row {reference_id!r} is no longer part of '
                            f'revision {revision_id!r}; nothing was written'
                        )
                for entry in additions:
                    cursor.execute(_ENTRY_INSERT, (
                        revision_id,
                        entry['entry_order'],
                        entry['reference_id'],
                        entry['identity_key'],
                        json.dumps(entry['payload'], sort_keys=True),
                        json.dumps(list(entry.get('test_condition_ids') or [])),
                        entry.get('effective_from'),
                        entry.get('effective_to'),
                        entry.get('source_sheet_name'),
                        entry.get('source_row_number'),
                        entry['content_sha256'],
                    ))
                connection.commit()
            finally:
                cursor.close()
        except (
            ReferenceRevisionNotFoundError,
            ReferencePublishConflictError,
            ReferenceStateConflictError,
        ):
            self._rollback(connection)
            raise
        except Exception as exc:  # pragma: no cover - driver-specific
            self._rollback(connection)
            raise CentralReferenceError(
                f'central reference row edit failed: {exc}'
            ) from exc
        finally:
            self._close(connection)
        return {'revision_id': str(revision_id), 'version': version}

    def publish(
        self,
        revision_id: str,
        *,
        published_by: str,
        published_at: str,
        publish_reason: Optional[str] = None,
    ) -> dict:
        return self._publish_all(
            [revision_id],
            published_by=published_by,
            published_at=published_at,
            publish_reason=publish_reason,
        )[0]

    def publish_coupled(
        self,
        revision_id: str,
        coupled_revision_id: str,
        *,
        published_by: str,
        published_at: str,
        publish_reason: Optional[str] = None,
    ) -> list[dict]:
        """Both halves of a coupled family group, or neither.

        One connection, one transaction, two conditional UPDATEs. The second
        failing rolls the first back, so the origin never reaches the state where
        a chamber would pair one revision's signal path with another's path loss.
        """
        return self._publish_all(
            [revision_id, coupled_revision_id],
            published_by=published_by,
            published_at=published_at,
            publish_reason=publish_reason,
        )

    def _publish_all(
        self,
        revision_ids: Sequence[str],
        *,
        published_by: str,
        published_at: str,
        publish_reason: Optional[str],
    ) -> list[dict]:
        """Publish every named revision inside ONE transaction.

        Spelled once for both the single and the coupled case: two code paths
        would be two chances for the coupled one to acquire its own commit
        boundary, which is exactly the failure it exists to prevent.
        """
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                for revision_id in revision_ids:
                    # Supersede first, in THIS transaction. Publishing is how a
                    # re-measured value replaces the one in front of the
                    # instruments, and the partial unique index permits exactly
                    # one PUBLISHED row per identity — so without this the
                    # second publish for a room was refused forever and the
                    # refusal said "retire it first" about an operation that
                    # does not exist. Web authoring would dead-end at its last
                    # step: fork, edit, then nothing.
                    #
                    # It cannot be a separate retire call. Between the two
                    # requests the identity would have NO published revision,
                    # and resolve_lookup_ownership reads exactly that emptiness
                    # as "this family is workbook-owned" — so a chamber booting
                    # in the gap would measure with the workbook's numbers while
                    # the tester believed the new ones were live.
                    cursor.execute(_SUPERSEDE_UPDATE, (
                        _RETIRED_STATE, published_by, published_at,
                        f'superseded by revision {revision_id}',
                        published_by, revision_id, _PUBLISHED_STATE,
                    ))
                    cursor.execute(_PUBLISH_UPDATE, (
                        _PUBLISHED_STATE, published_by, published_at, publish_reason,
                        published_by, revision_id, _CANDIDATE_STATE, _PUBLISHED_STATE,
                    ))
                    updated = cursor.fetchone()
                    if updated is None:
                        # Nothing moved. One statement says why, so the route answers
                        # 404 or 409 rather than a shrug — and no driver message is
                        # parsed to get there.
                        cursor.execute(
                            _PUBLISH_DIAGNOSIS, (_PUBLISHED_STATE, revision_id),
                        )
                        existing = cursor.fetchone()
                        if existing is None:
                            raise ReferenceRevisionNotFoundError(
                                f'unknown revision_id {revision_id!r}'
                            )
                        state, slot_taken = existing[0], existing[1]
                        if slot_taken:
                            # After the supersede above, a slot that is STILL
                            # taken means a genuinely simultaneous publisher won
                            # the race. The advice is to reload, not to retire:
                            # the value now in front of the instruments is
                            # somebody else's, and retiring it blind would put
                            # this room back on the workbook.
                            raise ReferencePublishConflictError(
                                f'another revision took the published slot for '
                                f'the identity of {revision_id!r} while this '
                                f'publish was running; reload and re-check '
                                f'before publishing over it'
                            )
                        raise ReferencePublishConflictError(
                            f'revision {revision_id!r} is {state}, not '
                            f'{_CANDIDATE_STATE}; only a candidate can be published'
                        )
            finally:
                cursor.close()
            connection.commit()
        except (ReferenceRevisionNotFoundError, ReferencePublishConflictError):
            self._rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rollback(connection)
            # Reaching here means the partial unique index refused a genuinely
            # SIMULTANEOUS second writer — the conditional statement above already
            # answers every non-concurrent case. It surfaces as a backend error
            # rather than a conflict because distinguishing the two would require
            # parsing the driver's constraint text, which is forbidden, and
            # because a retry immediately gets the deterministic 409.
            raise CentralReferenceError(
                f'central reference publish failed: {exc}'
            ) from exc
        finally:
            self._close(connection)
        return [
            {'revision_id': revision_id, 'state': _PUBLISHED_STATE}
            for revision_id in revision_ids
        ]

    # -------------------------------------------------------------- helpers

    def _connect(self) -> DbConnection:
        try:
            return self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise CentralReferenceError(
                f'central reference write connection failed: {exc}'
            ) from exc

    @staticmethod
    def _rollback(connection) -> None:
        rollback = getattr(connection, 'rollback', None)
        if callable(rollback):
            try:
                rollback()
            except Exception:  # noqa: BLE001 — the original error is the story
                pass

    @staticmethod
    def _close(connection) -> None:
        close: Optional[Callable] = getattr(connection, 'close', None)
        if callable(close):
            close()
