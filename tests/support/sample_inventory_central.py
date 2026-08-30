"""SQLite stand-in for the central sample-inventory tables — test SSOT (W3 백엔드).

The sample-inventory route boundary now needs MORE than the write tables: it
resolves the import's **attribution axis** (the project's model name) through
``CentralProjectService`` → ``PostgresCentralProjectReadAdapter``, which joins
``projects ⋈ device_models``. Two test files need that shape, so the DDL and the
fixture builders live here once instead of drifting in two hand-written copies.

Companion to ``central_pg_sqlite_shim`` (which owns the ``%s``→``?`` connection
shim); this module owns only the sample-inventory *shape*.
"""
from __future__ import annotations

import tempfile
from typing import Optional

from fcc_test_platform.application.central_project_read_adapter import (
    PostgresCentralProjectReadAdapter,
)
from fcc_test_platform.application.central_project_service import CentralProjectService
from fcc_test_contracts.common.sqlite_connection_factory import SqliteConnectionFactory
from tests.support.central_pg_sqlite_shim import create_tables_from_schema
from tests.support.central_pg_sqlite_shim import QmarkConnection


__all__ = [
    'CENTRAL_SAMPLE_DDL',
    'make_central_db',
    'seed_project',
    'make_project_service',
]


# The fixture derives its table/column set from the central schema JSON.  The
# inventory adapter is intentionally tested against the same current projection
# and revision tables as production; a hand-copied DDL would let a migration
# drift past the CRUD tests.
CENTRAL_SAMPLE_TABLES = (
    'projects', 'device_models', 'samples', 'sample_intakes',
    'sample_inventory_revisions', 'audit_events',
)
CENTRAL_SAMPLE_DDL = 'derived from docs/platform/central_db_schema.v1.json'


def make_central_db() -> str:
    """Create a temp SQLite file carrying ``CENTRAL_SAMPLE_DDL``; return its path."""
    handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    handle.close()
    connection = SqliteConnectionFactory(handle.name).create()
    try:
        # The project directory's production schema contains PostgreSQL GIN
        # expression indexes; the small SQLite shim intentionally handles only
        # column indexes. Keep the table columns schema-derived and declare this
        # one table without its database-specific index family.
        connection.execute(
            'CREATE TABLE projects ('
            'id TEXT, project_code TEXT, name TEXT, customer TEXT, '
            'management_number TEXT, status TEXT, fcc_grantee_code TEXT, '
            'applicant_name TEXT, applicant_address TEXT, eut_description TEXT, '
            'test_standard TEXT, created_at TEXT, updated_at TEXT)'
        )
        create_tables_from_schema(
            connection,
            tuple(name for name in CENTRAL_SAMPLE_TABLES if name != 'projects'),
        )
    finally:
        connection.close()
    return handle.name


def seed_project(
    db_path: str,
    project_id: str,
    *,
    model_name: Optional[str],
    project_code: Optional[str] = None,
) -> None:
    """Insert one project + its 1:1 device_models row (ADR-0017 D1 overlay).

    ``model_name=None`` seeds the abnormal "project without a model" state the
    fail-closed branch refuses — the one case where no attribution axis exists.
    """
    connection = SqliteConnectionFactory(db_path).create()
    try:
        connection.execute(
            'INSERT INTO projects (id, project_code, name, status, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (project_id, project_code or model_name or project_id,
             project_code or model_name or project_id, 'active',
             '2026-07-28T00:00:00+00:00', '2026-07-28T00:00:00+00:00'),
        )
        if model_name is not None:
            connection.execute(
                'INSERT INTO device_models '
                '(id, project_id, model_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
                (f'dm-{project_id}', project_id, model_name,
                 '2026-07-28T00:00:00+00:00', '2026-07-28T00:00:00+00:00'),
            )
        connection.commit()
    finally:
        connection.close()


class _UnusedCollaborator:
    """A collaborator the exercised path must never touch (loud if it does)."""

    def __getattr__(self, name: str):  # pragma: no cover — failure path only
        raise AssertionError(
            f'the attribution-axis read path touched {name!r} on a collaborator '
            'it is not supposed to use'
        )


def make_project_service(db_path: str) -> CentralProjectService:
    """A real ``CentralProjectService`` over the SQLite shim (read path only).

    The write port + membership service are deliberately fail-loud stubs: model
    resolution is a pure read, and a stub that raises proves it stayed one.
    """
    return CentralProjectService(
        PostgresCentralProjectReadAdapter(lambda: QmarkConnection(db_path)),
        _UnusedCollaborator(),
        _UnusedCollaborator(),
    )
