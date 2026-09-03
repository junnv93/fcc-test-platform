"""Central identity lookups the published-plan ingest needs (plan-delivery, 2026-09-02).

Two questions, both answered by one short read: *does this provider exist and is
it enabled* (the natural key ``providers.provider_id`` → the UUID FK
``providers.id``) and *does this project exist*.

The provider question has a precedent this module copies rather than reinvents —
``PostgresCentralResultSelectionAdapter._resolve_provider_id`` resolves the same
public natural key before it reaches a UUID FK query, with the same
``enabled = TRUE`` clause. The clause is load-bearing: a disabled provider must
not acquire a denominator, and ``published_plan_expectation.provider_id`` is part
of the join axis that keeps two headless providers from overwriting each other.

⚠️ The project id is **already** the central UUID here. Unlike the outbox path
(``PostgresCentralIdResolver.resolve_project_uuid``, which maps a provider-local
``project_code``), the plan was authored in a browser against the central project
id, so a code lookup would resolve nothing. This checks existence only.
"""
from __future__ import annotations

import uuid as _uuid
from typing import Callable, Optional

from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'PROJECT_EXISTS_SQL',
    'PROVIDER_UUID_SQL',
    'PublishedPlanIdentityError',
    'PostgresPublishedPlanIdentityAdapter',
]


#: PostgreSQL ``%s`` paramstyle, quoted identifiers — same shape as the sibling
#: central adapters so the SQLite shim used in tests accepts it too.
PROVIDER_UUID_SQL = (
    'SELECT "id" FROM "providers" WHERE "provider_id" = %s AND "enabled" = TRUE'
)
PROJECT_EXISTS_SQL = 'SELECT 1 FROM "projects" WHERE "id" = %s LIMIT 1'


class PublishedPlanIdentityError(Exception):
    """Connection/query failure while resolving central identity (loud-fail)."""


class PostgresPublishedPlanIdentityAdapter:
    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        self._connect = connection_factory

    def resolve_provider_uuid(self, provider_id: str) -> Optional[str]:
        row = self._fetch_one(PROVIDER_UUID_SQL, (str(provider_id),))
        return None if row is None else str(row[0])

    def project_exists(self, project_id: str) -> bool:
        # ⚠️ The shape check happens **before** the query, and that is not
        # defensive noise. ``projects.id`` is a PostgreSQL ``uuid``; handing it a
        # non-uuid string raises *invalid input syntax for type uuid*, which this
        # adapter would report as a backend failure (500). "That is not a project
        # id" is a not-found, and answering 500 sends an operator to look at the
        # database for a malformed request.
        candidate = str(project_id)
        try:
            _uuid.UUID(candidate)
        except (ValueError, AttributeError, TypeError):
            return False
        return self._fetch_one(PROJECT_EXISTS_SQL, (candidate,)) is not None

    def _fetch_one(self, sql: str, params: tuple):
        try:
            conn = self._connect()
        except Exception as exc:  # noqa: BLE001 — loud-fail boundary
            raise PublishedPlanIdentityError(str(exc)) from exc
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise PublishedPlanIdentityError(str(exc)) from exc
        finally:
            if cursor is not None:
                close_cursor = getattr(cursor, 'close', None)
                if callable(close_cursor):
                    close_cursor()
            close = getattr(conn, 'close', None)
            if callable(close):
                close()
        return rows[0] if rows else None
