"""Central PostgreSQL reference-catalog read adapter (Wave 3, 2026-08-07).

``PostgresCentralReferenceReadAdapter`` implements ``CentralReferenceReadPort``
against the central ``reference_revisions`` / ``reference_entries`` tables
(docs/platform/central_db_schema.v1.json).

Design (mirrors ``PostgresCentralReportReadAdapter``):

- **injected ``connection_factory``** (``() -> DbConnection``) — psycopg is built
  lazily by the composition root; this module imports no PostgreSQL driver.
- **read-only**: only ``SELECT``.
- **``%s`` paramstyle** (psycopg).
- **loud-fail**: any connection/query failure raises ``CentralReferenceError``
  rather than degrading to an empty list. An empty list has a meaning here —
  "this room publishes nothing" — and an outage that borrowed that meaning would
  tell a chamber it is correctly holding no correction data.

THE BUNDLE IS ONE STATEMENT, NOT A LOOP
----------------------------------------
``read_bundle`` fetches every published revision for the requested scopes in a
single statement, and the entries in a second one keyed on those revision ids.
Two statements rather than one-per-revision is not only cheaper: issuing N
queries would let a publish land between them, and the chamber would apply half
of a coupled family group — a signal path paired with another path's loss, which
measures normally and is wrong by a cable.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from fcc_test_kernel.domain.models.reference_catalog import RevisionState
from fcc_test_platform.domain.ports.output.central_reference_port import CentralReferenceError
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'PROVIDER_NATURAL_KEY_COLUMN',
    'REVISION_COLUMNS',
    'ENTRY_COLUMNS',
    'BUNDLE_IDENTITY_COLUMNS',
    'PostgresCentralReferenceReadAdapter',
]


#: Output columns of the revision SELECTs (= SELECT aliases). Order defines the
#: row→dict mapping the service consumes. ``entry_count`` is aggregated here so
#: a listing does not need a second round trip per row.
REVISION_COLUMNS: tuple[str, ...] = (
    'revision_id',
    'provider_id',
    'family',
    'profile_id',
    'scope_kind',
    'scope_id',
    'revision_number',
    'state',
    'version',
    'etag',
    'content_sha256',
    'source_snapshot_id',
    'source_manifest_sha256',
    'official_manifest_sha256',
    'forked_from_revision_id',
    'provenance_kind',
    'created_by',
    'created_at',
    'updated_by',
    'updated_at',
    'approved_by',
    'approved_at',
    'approval_reason',
    'published_by',
    'published_at',
    'publish_reason',
    'retired_by',
    'retired_at',
    'retirement_reason',
    'entry_count',
)

ENTRY_COLUMNS: tuple[str, ...] = (
    'revision_id',
    'entry_order',
    'reference_id',
    'identity_key',
    'payload_json',
    'test_condition_ids_json',
    'effective_from',
    'effective_to',
    'source_sheet_name',
    'source_row_number',
    'content_sha256',
)

#: Output columns of the bundle-identity SELECT. Two columns, because the tag is
#: derived from revision etags and nothing else needs to travel to compute it.
BUNDLE_IDENTITY_COLUMNS: tuple[str, ...] = ('revision_id', 'etag')

#: The column of ``providers`` that IS the provider identity every reference
#: statement keys on. Named once here because two adapters resolve providers —
#: this one to answer "is it registered", the write one inside its insert guard —
#: and a disagreement about which column is the natural key would make the read
#: path accept a provider the write path rejects. The write adapter keeps its
#: statement verbatim (a byte-identical SQL constant is the regression line for
#: this wave); the agreement is sealed from source instead.
PROVIDER_NATURAL_KEY_COLUMN = 'provider_id'

_PROVIDER_EXISTS_SELECT = (
    f'SELECT 1 FROM "providers" WHERE "{PROVIDER_NATURAL_KEY_COLUMN}" = %s LIMIT 1'
)

_REVISION_SELECT = (
    'SELECT "r"."id" AS "revision_id", "p"."provider_id", "r"."family", '
    '"r"."profile_id", "r"."scope_kind", "r"."scope_id", "r"."revision_number", '
    '"r"."state", "r"."version", "r"."etag", "r"."content_sha256", '
    '"r"."source_snapshot_id", "r"."source_manifest_sha256", '
    '"r"."official_manifest_sha256", "r"."forked_from_revision_id", '
    '"r"."provenance_kind", '
    '"r"."created_by", "r"."created_at", "r"."updated_by", "r"."updated_at", '
    '"r"."approved_by", "r"."approved_at", "r"."approval_reason", '
    '"r"."published_by", "r"."published_at", "r"."publish_reason", '
    '"r"."retired_by", "r"."retired_at", "r"."retirement_reason", '
    'COALESCE("e"."n", 0) AS "entry_count" '
    'FROM "reference_revisions" r '
    'JOIN "providers" p ON "p"."id" = "r"."provider_id" '
    'LEFT JOIN (SELECT "revision_id", COUNT(*) AS "n" FROM "reference_entries" '
    'GROUP BY "revision_id") e ON "e"."revision_id" = "r"."id" '
)

_ENTRY_SELECT = (
    'SELECT "revision_id", "entry_order", "reference_id", "identity_key", '
    '"payload_json", "test_condition_ids_json", "effective_from", '
    '"effective_to", "source_sheet_name", "source_row_number", "content_sha256" '
    'FROM "reference_entries" '
)

#: The keyset axis. Identity then revision number, so paging never revisits a
#: row and never skips one when a concurrent insert lands mid-walk. It matches
#: ``ux_reference_revisions_identity_number`` so the seek is index-backed.
_KEYSET_ORDER = (
    'ORDER BY "r"."family", "r"."profile_id", "r"."scope_id", '
    '"r"."revision_number", "r"."id"'
)

#: The one state a chamber may hold. DERIVED from the domain vocabulary rather
#: than spelled: the same three tokens are declared by the central CHECK
#: constraint and the OpenAPI enum, and re-typing one of them here is how the
#: three drift apart.
_PUBLISHED_STATE = RevisionState.PUBLISHED.value


class PostgresCentralReferenceReadAdapter:
    """``CentralReferenceReadPort`` over a central PostgreSQL connection factory."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def provider_exists(self, provider_id: str) -> bool:
        """One indexed probe on the provider natural key.

        ``SELECT 1 … LIMIT 1`` rather than a count: the question is existence,
        and a count would make the statement describe a quantity nobody asked
        for. The failure path stays loud through ``_query`` — an outage raises
        ``CentralReferenceError`` instead of answering ``False``, so a database
        that cannot be reached never gets read as "that provider is not
        registered".
        """
        return bool(self._query(_PROVIDER_EXISTS_SELECT, ('exists',), (provider_id,)))

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
        clauses = ['"p"."provider_id" = %s']
        params: list = [provider_id]
        for column, value in (
            ('family', family),
            ('scope_kind', scope_kind),
            ('scope_id', scope_id),
            ('state', state),
        ):
            if value is not None:
                clauses.append(f'"r"."{column}" = %s')
                params.append(value)
        if cursor_values:
            # Row-value comparison rather than an OR-chain: one index seek, and
            # no chance of the chain and the ORDER BY disagreeing about the axis.
            clauses.append(
                '("r"."family", "r"."profile_id", "r"."scope_id", '
                '"r"."revision_number", "r"."id") > (%s, %s, %s, %s, %s)'
            )
            params.extend(cursor_values)

        statement = _REVISION_SELECT + 'WHERE ' + ' AND '.join(clauses) + ' ' + _KEYSET_ORDER
        if limit is not None:
            statement += ' LIMIT %s'
            params.append(limit)
        return self._query(statement, REVISION_COLUMNS, tuple(params))

    def read_revision(self, revision_id: str) -> Optional[dict]:
        """One revision row by id, or ``None``.

        Shares ``_REVISION_SELECT`` with the listing so the two can never
        disagree about what a revision summary contains — a second projection
        would drift the moment either side gained a column.
        """
        rows = self._query(
            _REVISION_SELECT + 'WHERE "r"."id" = %s',
            REVISION_COLUMNS,
            (revision_id,),
        )
        return rows[0] if rows else None

    def read_entries(self, revision_id: str) -> list[dict]:
        return self._query(
            _ENTRY_SELECT + 'WHERE "revision_id" = %s ORDER BY "entry_order"',
            ENTRY_COLUMNS,
            (revision_id,),
        )

    def read_bundle_identity(
        self, provider_id: str, *, scope_ids: Sequence[str],
    ) -> list[dict]:
        """Identity of every published revision in the cut — unpaged, no payloads.

        Deliberately a separate, cheap statement rather than a projection of the
        paged read: the bundle tag has to describe the WHOLE cut on every page,
        and deriving it from whatever a page happened to contain would give each
        page its own tag. A chamber comparing tags across pages would then see a
        change on every walk and never finish applying anything.
        """
        scopes = [scope for scope in scope_ids if str(scope).strip()]
        if not scopes:
            return []
        placeholders = ', '.join(['%s'] * len(scopes))
        return self._query(
            'SELECT "r"."id" AS "revision_id", "r"."etag" '
            'FROM "reference_revisions" r '
            'JOIN "providers" p ON "p"."id" = "r"."provider_id" '
            'WHERE "p"."provider_id" = %s AND "r"."state" = %s '
            f'AND "r"."scope_id" IN ({placeholders}) '
            + _KEYSET_ORDER,
            BUNDLE_IDENTITY_COLUMNS,
            tuple([provider_id, _PUBLISHED_STATE, *scopes]),
        )

    def read_bundle(
        self,
        provider_id: str,
        *,
        scope_ids: Sequence[str],
        limit: Optional[int] = None,
        cursor_values: Optional[Sequence] = None,
    ) -> list[dict]:
        """One page of published revisions for the requested scopes, with entries.

        Returns revision dicts each carrying an ``entries`` list, so the caller
        receives one consistent answer rather than assembling it from several.

        The entry statement is keyed on the revision ids of THIS page, so a page
        never carries entries belonging to a revision it did not return.
        """
        scopes = [scope for scope in scope_ids if str(scope).strip()]
        if not scopes:
            return []
        placeholders = ', '.join(['%s'] * len(scopes))
        clauses = [
            '"p"."provider_id" = %s',
            '"r"."state" = %s',
            f'"r"."scope_id" IN ({placeholders})',
        ]
        params: list = [provider_id, _PUBLISHED_STATE, *scopes]
        if cursor_values:
            # Row-value comparison on the same axis as the ORDER BY — one index
            # seek, and no way for the two to disagree about where the page ends.
            clauses.append(
                '("r"."family", "r"."profile_id", "r"."scope_id", '
                '"r"."revision_number", "r"."id") > (%s, %s, %s, %s, %s)'
            )
            params.extend(cursor_values)
        statement = (
            _REVISION_SELECT + 'WHERE ' + ' AND '.join(clauses) + ' ' + _KEYSET_ORDER
        )
        if limit is not None:
            statement += ' LIMIT %s'
            params.append(limit)
        revisions = self._query(statement, REVISION_COLUMNS, tuple(params))
        if not revisions:
            return []

        revision_ids = [revision['revision_id'] for revision in revisions]
        entry_placeholders = ', '.join(['%s'] * len(revision_ids))
        entries = self._query(
            _ENTRY_SELECT
            + f'WHERE "revision_id" IN ({entry_placeholders}) '
            'ORDER BY "revision_id", "entry_order"',
            ENTRY_COLUMNS,
            tuple(revision_ids),
        )
        grouped: dict = {revision_id: [] for revision_id in revision_ids}
        for entry in entries:
            grouped.setdefault(entry['revision_id'], []).append(entry)
        return [
            {**revision, 'entries': grouped.get(revision['revision_id'], [])}
            for revision in revisions
        ]

    # -------------------------------------------------------------- helpers

    def _query(
        self, statement: str, columns: tuple[str, ...], params: tuple,
    ) -> list[dict]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 — wrap as loud CentralReferenceError
            raise CentralReferenceError(
                f'central reference read connection failed: {exc}'
            ) from exc
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, params)
                rows = list(cursor.fetchall())
            finally:
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            raise CentralReferenceError(
                f'central reference read query failed: {exc}'
            ) from exc
        finally:
            close: Optional[Callable] = getattr(connection, 'close', None)
            if callable(close):
                close()
        return [dict(zip(columns, row)) for row in rows]
