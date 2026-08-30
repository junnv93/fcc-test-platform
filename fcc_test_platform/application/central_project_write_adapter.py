"""Central PostgreSQL project write adapter (Phase 1, 2026-06-22).

``PostgresCentralProjectWriteAdapter`` implements ``CentralProjectWritePort``
against the central ``projects`` + ``device_models`` tables. ``create_project_
with_model`` inserts the (project, device_model) pair in a SINGLE transaction so
the 1:1 overlay (ADR-0017 D1) is never half-created (a project without its
model). ``find_project_by_code`` is the same-model reuse gate (D1).

Design (mirrors ``PostgresCentralMembershipWriteAdapter``):

- **injected ``connection_factory``** (``() -> DbConnection``).
- **``%s`` paramstyle** (psycopg) for both INSERTs + the SELECT.
- **loud-fail**: a connection/query failure raises ``CentralProjectError``.
- **atomic pair**: both INSERTs run inside one ``_in_transaction`` body before
  commit; a device_model failure rolls the project insert back.

When constructed with an audit writer, ``create_project_with_model_and_admin_grant``
binds the project insert, user upsert, membership upsert, and audit insert to
one connection/cursor/commit so a new project cannot persist without its
creator admin grant.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Callable, Mapping, Optional

from fcc_test_platform.application.central_membership_write_adapter import (
    MEMBERSHIP_INSERT_COLUMNS,
    SELECT_MEMBERSHIP_WITH_SUBJECT_SQL,
    UPSERT_MEMBERSHIP_SQL,
    _set_serializable_best_effort,
)
from fcc_test_platform.application.central_user_write_adapter import (
    UPSERT_USER_SQL,
    USER_INSERT_COLUMNS,
)
from domain.ports.output.central_audit_write_port import CentralAuditWritePort
from domain.ports.output.central_project_port import (
    CentralProjectError,
    ProjectIdentifierConflictError,
)
from domain.ports.output.platform_database_port import DbConnection
from domain.services.project_identifier_conflict import (
    PROJECT_CONFLICT_RESOURCE,
    classify_project_unique_violation,
)
from domain.services.project_metadata_edit import (
    DEVICE_MODEL_META_FIELDS,
    PROJECT_TABLE_META_FIELDS,
)


__all__ = [
    'DEVICE_MODEL_INSERT_COLUMNS',
    'DEVICE_MODEL_META_SET_FRAGMENTS',
    'INSERT_DEVICE_MODEL_SQL',
    'INSERT_PROJECT_SQL',
    'PROJECT_INSERT_COLUMNS',
    'PROJECT_META_SET_FRAGMENTS',
    'SELECT_PROJECT_BY_CODE_SQL',
    'UPDATE_PROJECT_STATUS_SQL',
    'USER_UPSERT_RETURNING_COLUMNS',
    'PostgresCentralProjectWriteAdapter',
    'build_device_model_metadata_update_sql',
    'build_project_metadata_update_sql',
]


PROJECT_INSERT_COLUMNS: tuple[str, ...] = (
    'id',
    'project_code',
    'name',
    'customer',
    'management_number',
    'status',
    'fcc_grantee_code',
    'applicant_name',
    'applicant_address',
    'eut_description',
    'test_standard',
    'created_at',
    'updated_at',
)
DEVICE_MODEL_INSERT_COLUMNS: tuple[str, ...] = (
    'id',
    'project_id',
    'model_name',
    'manufacturer',
    'metadata_json',
    'created_at',
    'updated_at',
)


def _build_insert(table: str, columns: tuple[str, ...]) -> str:
    column_sql = ', '.join(f'"{column}"' for column in columns)
    placeholders = ', '.join(['%s'] * len(columns))
    return f'INSERT INTO "{table}" ({column_sql}) VALUES ({placeholders})'


INSERT_PROJECT_SQL = _build_insert('projects', PROJECT_INSERT_COLUMNS)
INSERT_DEVICE_MODEL_SQL = _build_insert('device_models', DEVICE_MODEL_INSERT_COLUMNS)
SELECT_PROJECT_BY_CODE_SQL = (
    'SELECT "id" AS "project_id", "project_code" FROM "projects" '
    'WHERE "project_code" = %s LIMIT 1'
)
_PROJECT_BY_CODE_COLUMNS: tuple[str, ...] = ('project_id', 'project_code')
# project-status-visibility — set a project's lifecycle status. RETURNING "id"
# lets the caller distinguish "updated" (one row) from "unknown project" (no
# row → service maps to ProjectNotFoundError 404). The CHECK constraint
# (ck_projects_status) rejects any value outside the sealed domain at the DB.
UPDATE_PROJECT_STATUS_SQL = (
    'UPDATE "projects" SET "status" = %s, "updated_at" = %s '
    'WHERE "id" = %s RETURNING "id"'
)
USER_UPSERT_RETURNING_COLUMNS: tuple[str, ...] = (
    'id', 'issuer', 'subject', 'display_name', 'email', 'enabled',
)

# W3 백엔드 — 성적서 메타 부분 편집. 갱신 대상 필드 집합이 요청마다 달라서 단일
# SQL 상수로는 표현할 수 없다(2^7 조합). 대신 **필드당 SET 조각을 import 시점에
# 정책 SSOT 로부터 사전 생성**해 두고, 호출 시에는 그 조각들을 고르기만 한다 —
# 컬럼명이 사용자 입력에서 SQL 로 흘러들 경로가 구조적으로 없다(값은 전부 %s 바인드).
PROJECT_META_SET_FRAGMENTS: Mapping[str, str] = MappingProxyType({
    field: f'"{field}" = %s' for field in PROJECT_TABLE_META_FIELDS
})
DEVICE_MODEL_META_SET_FRAGMENTS: Mapping[str, str] = MappingProxyType({
    field: f'"{field}" = %s' for field in DEVICE_MODEL_META_FIELDS
})


def _build_metadata_update(
    table: str,
    fragments: Mapping[str, str],
    fields: tuple[str, ...],
    *,
    key_column: str,
    returning: str = '',
) -> str:
    # ``updated_at`` always rides along, so the SET clause is never empty even for
    # a manufacturer-only edit (the projects row still gets its mtime bumped and
    # RETURNING still answers "does this project exist?").
    set_sql = ', '.join(
        [fragments[field] for field in fields] + ['"updated_at" = %s']
    )
    tail = f' RETURNING {returning}' if returning else ''
    return f'UPDATE "{table}" SET {set_sql} WHERE "{key_column}" = %s{tail}'


def build_project_metadata_update_sql(fields: tuple[str, ...]) -> str:
    """``UPDATE "projects" SET <fields>, "updated_at" = %s WHERE "id" = %s RETURNING "id"``.

    ``RETURNING "id"`` distinguishes "updated" (one row) from "unknown project"
    (no row → the service raises a 404) — same idiom as the status update.
    """
    return _build_metadata_update(
        'projects', PROJECT_META_SET_FRAGMENTS, fields,
        key_column='id', returning='"id"',
    )


def build_device_model_metadata_update_sql(fields: tuple[str, ...]) -> str:
    """``UPDATE "device_models" SET <fields>, "updated_at" = %s WHERE "project_id" = %s``.

    Keyed by ``project_id`` (ADR-0017 D1 pins one model per project). The project's
    existence is already decided by the ``projects`` update; this ``RETURNING``
    catches the *other* case — a project whose 1:1 ``device_models`` row is missing
    (a D1 integrity violation). Without it a ``manufacturer`` edit would update 0
    rows and still answer 200, silently dropping the write.
    """
    return _build_metadata_update(
        'device_models', DEVICE_MODEL_META_SET_FRAGMENTS, fields,
        key_column='project_id', returning='"id"',
    )


class PostgresCentralProjectWriteAdapter:
    """``CentralProjectWritePort`` — find-by-code + atomic (project + model) insert."""

    def __init__(
        self,
        connection_factory: Callable[[], DbConnection],
        *,
        audit_writer: Optional[CentralAuditWritePort] = None,
    ) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory
        self._audit = audit_writer

    def find_project_by_code(self, project_code: str) -> Optional[dict]:
        def _txn(cursor) -> Optional[dict]:
            cursor.execute(SELECT_PROJECT_BY_CODE_SQL, (project_code,))
            rows = list(cursor.fetchall())
            if not rows:
                return None
            return dict(zip(_PROJECT_BY_CODE_COLUMNS, rows[0]))

        return self._in_transaction(_txn)

    def create_project_with_model(
        self, project_record: Mapping, device_model_record: Mapping,
    ) -> dict:
        project_values = tuple(
            project_record.get(column) for column in PROJECT_INSERT_COLUMNS
        )
        device_values = tuple(
            device_model_record.get(column) for column in DEVICE_MODEL_INSERT_COLUMNS
        )

        def _txn(cursor) -> dict:
            cursor.execute(INSERT_PROJECT_SQL, project_values)
            cursor.execute(INSERT_DEVICE_MODEL_SQL, device_values)
            return {
                'project_id': project_record.get('id'),
                'project_code': project_record.get('project_code'),
            }

        result = self._in_transaction(_txn)
        assert result is not None  # create path always returns a row
        return result

    def update_project_status(
        self, project_id: str, status: str, updated_at: str,
    ) -> Optional[dict]:
        def _txn(cursor) -> Optional[dict]:
            cursor.execute(UPDATE_PROJECT_STATUS_SQL, (status, updated_at, project_id))
            rows = list(cursor.fetchall())
            if not rows:
                return None  # unknown project_id → service raises 404
            return {'project_id': project_id, 'status': status}

        return self._in_transaction(_txn)

    def update_project_metadata(
        self,
        project_id: str,
        updates: Mapping[str, Optional[str]],
        updated_at: str,
    ) -> Optional[dict]:
        # Re-derive the touched columns from the policy SSOT tuples (never from
        # the caller's key order) so an unexpected key cannot reach the SQL text.
        project_fields = tuple(
            field for field in PROJECT_TABLE_META_FIELDS if field in updates
        )
        model_fields = tuple(
            field for field in DEVICE_MODEL_META_FIELDS if field in updates
        )
        if not project_fields and not model_fields:
            raise CentralProjectError(
                'update_project_metadata called with no editable field — the '
                'service must reject an empty update before it reaches the adapter'
            )
        project_sql = build_project_metadata_update_sql(project_fields)
        project_values = tuple(updates[field] for field in project_fields) + (
            updated_at, project_id,
        )
        model_sql = build_device_model_metadata_update_sql(model_fields)
        model_values = tuple(updates[field] for field in model_fields) + (
            updated_at, project_id,
        )

        def _txn(cursor) -> Optional[dict]:
            # Both tables inside ONE transaction body (D-6): a device_models
            # failure rolls the projects edit back — never a half-applied edit.
            cursor.execute(project_sql, project_values)
            if not list(cursor.fetchall()):
                return None  # unknown project_id → service raises 404
            if model_fields:
                cursor.execute(model_sql, model_values)
                if not list(cursor.fetchall()):
                    # Project exists but its 1:1 device_models row does not
                    # (ADR-0017 D1 violation). Raise so the whole edit rolls back
                    # — answering 200 here would report a write that never landed.
                    raise CentralProjectError(
                        f'project {project_id!r} has no device_models row — '
                        f'cannot apply {list(model_fields)} (ADR-0017 D1 expects '
                        f'exactly one model per project)'
                    )
            return {'project_id': project_id}

        return self._in_transaction(_txn)

    def create_project_with_model_and_admin_grant(
        self,
        project_record: Mapping,
        device_model_record: Mapping,
        user_record: Mapping,
        membership_record: Mapping,
        audit_record: Mapping,
    ) -> dict:
        """Insert project/model and grant creator admin in one DB transaction."""
        if self._audit is None:
            raise CentralProjectError(
                'audit_writer is required for atomic project creator grant'
            )
        project_values = tuple(
            project_record.get(column) for column in PROJECT_INSERT_COLUMNS
        )
        device_values = tuple(
            device_model_record.get(column) for column in DEVICE_MODEL_INSERT_COLUMNS
        )
        user_values = tuple(user_record.get(column) for column in USER_INSERT_COLUMNS)

        def _txn(cursor) -> dict:
            _set_serializable_best_effort(cursor)
            cursor.execute(UPSERT_USER_SQL, user_values)
            user_row = cursor.fetchone()
            if user_row is None:
                raise CentralProjectError('central users upsert returned no row')
            user = dict(zip(USER_UPSERT_RETURNING_COLUMNS, user_row))
            if not _is_enabled(user.get('enabled')):
                raise PermissionError('actor user is disabled')
            materialized_membership = dict(membership_record)
            materialized_membership['user_id'] = user['id']
            membership_values = tuple(
                materialized_membership.get(column)
                for column in MEMBERSHIP_INSERT_COLUMNS
            )
            cursor.execute(INSERT_PROJECT_SQL, project_values)
            cursor.execute(INSERT_DEVICE_MODEL_SQL, device_values)
            cursor.execute(UPSERT_MEMBERSHIP_SQL, membership_values)
            self._audit.append_event_in_transaction(cursor, audit_record)
            cursor.execute(
                SELECT_MEMBERSHIP_WITH_SUBJECT_SQL,
                (
                    project_record.get('id'),
                    user['id'],
                    materialized_membership.get('role_key'),
                ),
            )
            if not list(cursor.fetchall()):
                raise CentralProjectError(
                    'creator admin grant did not produce a readable membership row'
                )
            return {
                'project_id': project_record.get('id'),
                'project_code': project_record.get('project_code'),
            }

        result = self._in_transaction(_txn)
        assert result is not None
        return result

    def _in_transaction(self, body: Callable[[object], Optional[dict]]) -> Optional[dict]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise CentralProjectError(
                f'central project write connection failed: {exc}'
            ) from exc
        try:
            cursor = connection.cursor()
            try:
                result = body(cursor)
            finally:
                cursor.close()
            connection.commit()
            return result
        except CentralProjectError:
            _safe_rollback(connection)
            raise
        except PermissionError:
            _safe_rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(connection)
            conflict = _as_identifier_conflict(exc)
            if conflict is not None:
                raise conflict from exc
            raise CentralProjectError(f'central project write failed: {exc}') from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()


def _as_identifier_conflict(exc: BaseException) -> Optional[ProjectIdentifierConflictError]:
    """Promote a ``projects`` UNIQUE violation to the 409 conflict class.

    Driver-agnostic on purpose: this module imports no PostgreSQL driver (the
    ``connection_factory`` is injected), so the classification runs off the
    SQLSTATE the driver exposes (psycopg3 ``.sqlstate`` / psycopg2 ``.pgcode``)
    plus the exception text — never an ``isinstance`` on a driver class. Anything
    the domain policy cannot pin to a specific key stays on the existing
    ``CentralProjectError`` → 503 path (an unrecognised integrity error must not
    be dressed up as "management number already taken").
    """
    field = classify_project_unique_violation(
        sqlstate=getattr(exc, 'sqlstate', None) or getattr(exc, 'pgcode', None),
        message=exc,
    )
    if field is None:
        return None
    return ProjectIdentifierConflictError(field, PROJECT_CONFLICT_RESOURCE)


def _safe_rollback(connection) -> None:
    rollback = getattr(connection, 'rollback', None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001 — never mask the original error
            pass


def _is_enabled(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {'0', 'false', 'f', 'no', 'off'}
