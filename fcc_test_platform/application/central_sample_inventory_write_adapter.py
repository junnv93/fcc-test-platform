"""Atomic PostgreSQL writes for web sample CRUD and revision history."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Callable, Mapping, Optional
import uuid

from fcc_test_kernel.domain.models.sample_inventory import (
    CUSTODY_EVENT_FIELDS,
    INTAKE_FIELDS,
    SAMPLE_EDITABLE_FIELDS,
    SampleRevisionEvent,
    SampleStatus,
)
from fcc_test_platform.domain.ports.output.central_sample_inventory_write_port import (
    CentralSampleInventoryNotFoundError,
    CentralSampleInventoryWriteError,
)
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection
from fcc_test_kernel.domain.services.sample_inventory_policy import (
    SampleExpectedVersionConflict,
    SampleInventoryPolicyError,
    validate_custody_event,
    apply_patch,
    assert_expected_version,
    canonical_snapshot,
    event_for_change,
    sample_projection,
    snapshot_json,
    transition_status,
    validate_patch,
)


# ⚠️ 아래 세 SQL 의 컬럼 순서는 커널의 SAMPLE_EDITABLE_FIELDS 순서와 **묶여 있다**.
# `_sample_update_values` 가 그 튜플을 그대로 훑어 값을 만들기 때문에, 한쪽만 고치면
# 오류 없이 값이 옆 칸으로 밀린다(라벨넘버가 시료종류 칸에 들어가는 식). 함께 고쳐라.
SAMPLE_SELECT_SQL = (
    'SELECT "id", "project_id", "sample_number", "sample_code", '
    '"sample_kind", "sample_description", "test_category", '
    '"label_number", "smsn", "serial_number", "intake_cert", "assigned_team", '
    '"sender", "receiver", "received_date", "released_date", "note", "status", '
    '"row_version", "deleted_at", "deleted_by", "created_at", "updated_at" FROM "samples" '
    'WHERE "project_id" = %s AND "id" = %s FOR UPDATE'
)
PROJECT_SELECT_SQL = (
    'SELECT p."id" AS "project_id", p."project_code", dm."model_name", '
    'p."management_number", p."status" AS "project_status" '
    'FROM "projects" p LEFT JOIN "device_models" dm ON dm."project_id" = p."id" '
    'WHERE p."id" = %s'
)
SAMPLE_INSERT_SQL = (
    'INSERT INTO "samples" ("id", "project_id", "sample_number", "sample_code", '
    '"sample_kind", "sample_description", '
    '"test_category", "label_number", "smsn", "serial_number", "intake_cert", '
    '"assigned_team", "sender", "receiver", "received_date", "released_date", '
    '"note", "status", "row_version", "created_at", "updated_at") '
    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)'
)
SAMPLE_UPDATE_SQL = (
    'UPDATE "samples" SET "sample_number" = %s, "sample_code" = %s, '
    '"sample_kind" = %s, "sample_description" = %s, '
    '"test_category" = %s, "label_number" = %s, "smsn" = %s, '
    '"serial_number" = %s, "intake_cert" = %s, "assigned_team" = %s, '
    '"sender" = %s, "receiver" = %s, "received_date" = %s, '
    '"released_date" = %s, "note" = %s, "status" = %s, "row_version" = %s, '
    '"deleted_at" = %s, "deleted_by" = %s, "updated_at" = %s '
    'WHERE "project_id" = %s AND "id" = %s'
)
STATUS_UPDATE_SQL = (
    'UPDATE "samples" SET "status" = %s, "row_version" = "row_version" + 1, '
    '"deleted_at" = %s, "deleted_by" = %s, "updated_at" = %s '
    'WHERE "project_id" = %s AND "id" = %s'
)
INTAKE_INSERT_SQL = (
    'INSERT INTO "sample_intakes" ("id", "sample_id", "intake_date", "bl", "ap", '
    '"cp", "csc", "rf_cal", "hw_rev", "note", "tech_group", "created_at", "updated_at") '
    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)'
)
REVISION_INSERT_SQL = (
    'INSERT INTO "sample_inventory_revisions" ("id", "sample_id", "project_id", '
    '"revision_number", "event_type", "snapshot_json", "changed_fields_json", '
    '"actor_subject", "occurred_at", "created_at") '
    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)'
)
NEXT_REVISION_SQL = (
    'SELECT "revision_number" FROM "sample_inventory_revisions" '
    'WHERE "sample_id" = %s ORDER BY "revision_number" DESC LIMIT 1 FOR UPDATE'
)
LATEST_INTAKE_SQL = (
    'SELECT "id", "sample_id", "intake_date", "bl", "ap", "cp", "csc", '
    '"rf_cal", "hw_rev", "note", "tech_group", "created_at" '
    'FROM "sample_intakes" WHERE "sample_id" = %s '
    'ORDER BY "created_at" DESC, "id" DESC LIMIT 1'
)
CUSTODY_INSERT_SQL = (
    'INSERT INTO "sample_custody_events" ("id", "sample_id", "project_id", '
    '"event_type", "occurred_on", "counterparty", "intake_cert_number", '
    '"reason", "note", "actor_subject", "created_at", "updated_at") '
    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)'
)
CUSTODY_DELETE_SQL = (
    'DELETE FROM "sample_custody_events" WHERE "id" = %s AND "sample_id" = %s '
    'AND "project_id" = %s'
)
# 시료가 이 프로젝트의 것인지 확인한다. custody 쓰기는 project_id 를 URL 에서 받으므로,
# 확인 없이 쓰면 다른 프로젝트의 시료에 사건을 붙일 수 있다.
SAMPLE_OWNERSHIP_SQL = (
    'SELECT "id" FROM "samples" WHERE "project_id" = %s AND "id" = %s'
)
AUDIT_HARD_DELETE_SQL = (
    'INSERT INTO "audit_events" ("id", "event_type", "project_id", "actor_subject", '
    '"detail_json", "occurred_at", "created_at") VALUES (%s, %s, %s, %s, %s, %s, %s)'
)

PROJECT_COLUMNS = ('project_id', 'project_code', 'model_name', 'management_number', 'project_status')
SAMPLE_COLUMNS = (
    'id', 'project_id', 'sample_number', 'sample_code',
    'sample_kind', 'sample_description', 'test_category',
    'label_number', 'smsn', 'serial_number', 'intake_cert', 'assigned_team',
    'sender', 'receiver', 'received_date', 'released_date', 'note', 'status',
    'row_version', 'deleted_at', 'deleted_by', 'created_at', 'updated_at',
)
INTAKE_COLUMNS = (
    'id', 'sample_id', 'intake_date', 'bl', 'ap', 'cp', 'csc', 'rf_cal',
    'hw_rev', 'note', 'tech_group', 'created_at',
)


class PostgresCentralSampleInventoryWriteAdapter:
    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def create_sample(self, project_id: str, payload: Mapping[str, Any], *,
                      actor_subject: str, occurred_at: str) -> dict:
        conn, cursor = self._open()
        try:
            project = self._fetchone(cursor, PROJECT_SELECT_SQL, (project_id,), PROJECT_COLUMNS)
            if project is None:
                raise CentralSampleInventoryNotFoundError(f'unknown project_id {project_id}')
            value = _initial_projection(payload)
            sample_id = str(payload.get('id') or uuid.uuid4())
            now = occurred_at
            cursor.execute(SAMPLE_INSERT_SQL, (
                sample_id, project_id, value['sample_number'], value['sample_code'],
                value['sample_kind'], value['sample_description'],
                value['test_category'], value['label_number'], value['smsn'],
                value['serial_number'], value['intake_cert'], value['assigned_team'],
                value['sender'], value['receiver'], value['received_date'],
                value['released_date'], value['note'], SampleStatus.ACTIVE.value,
                now, now,
            ))
            intake = payload.get('latest_intake')
            if intake:
                self._insert_intake(cursor, sample_id, intake, now)
            snapshot = _snapshot_for_projection(
                project, sample_id, value, intake, revision_number=1,
                captured_at=occurred_at,
            )
            self._insert_revision(
                cursor, sample_id, project_id, 1, SampleRevisionEvent.CREATED,
                snapshot, SAMPLE_EDITABLE_FIELDS + ('status', 'row_version', 'latest_intake'),
                actor_subject, occurred_at,
            )
            conn.commit()
            result = deepcopy(value)
            result.update({
                'id': sample_id, 'project_id': project_id,
                'status': SampleStatus.ACTIVE.value, 'row_version': 1,
                'latest_intake': dict(intake) if intake else None,
                'created_at': now, 'updated_at': now,
            })
            return result
        except CentralSampleInventoryNotFoundError:
            self._rollback(conn)
            raise
        except SampleInventoryPolicyError:
            self._rollback(conn)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rollback(conn)
            raise CentralSampleInventoryWriteError(f'create sample failed: {exc}') from exc
        finally:
            self._close(conn, cursor)

    def patch_sample(self, project_id: str, sample_id: str, payload: Mapping[str, Any], *,
                     expected_version: int, actor_subject: str, occurred_at: str) -> dict:
        conn, cursor = self._open()
        try:
            current = self._fetchone(cursor, SAMPLE_SELECT_SQL, (project_id, sample_id), SAMPLE_COLUMNS)
            if current is None:
                raise CentralSampleInventoryNotFoundError(f'unknown sample_id {sample_id}')
            latest = self._fetchone(cursor, LATEST_INTAKE_SQL, (sample_id,), INTAKE_COLUMNS)
            current_value = _row_projection(current, latest)
            assert_expected_version(current_value['row_version'], expected_version)
            after, changed = apply_patch(current_value, payload)
            after['row_version'] = int(current_value['row_version']) + 1
            now = occurred_at
            cursor.execute(SAMPLE_UPDATE_SQL, _sample_update_values(
                after, now, project_id, sample_id,
            ))
            if getattr(cursor, 'rowcount', 1) != 1:
                raise ValueError('sample version conflict')
            if 'latest_intake' in changed:
                self._insert_intake(cursor, sample_id, after['latest_intake'], now)
            project = self._fetchone(cursor, PROJECT_SELECT_SQL, (project_id,), PROJECT_COLUMNS)
            revision_number = self._next_revision(cursor, sample_id)
            snapshot = _snapshot_for_projection(
                project or {'project_id': project_id}, sample_id, after,
                after.get('latest_intake'), revision_number, occurred_at,
            )
            event = event_for_change(
                status_before=current_value['status'], status_after=after['status'],
            )
            self._insert_revision(cursor, sample_id, project_id, revision_number, event,
                                  snapshot, changed, actor_subject, occurred_at)
            conn.commit()
            after.update({'id': sample_id, 'project_id': project_id, 'updated_at': now})
            return after
        except CentralSampleInventoryNotFoundError:
            self._rollback(conn)
            raise
        except SampleExpectedVersionConflict:
            self._rollback(conn)
            raise
        except SampleInventoryPolicyError:
            self._rollback(conn)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rollback(conn)
            if 'version conflict' in str(exc).lower():
                raise ValueError('sample version conflict') from exc
            raise CentralSampleInventoryWriteError(f'patch sample failed: {exc}') from exc
        finally:
            self._close(conn, cursor)

    def change_status(self, project_id: str, sample_id: str, status: str, *,
                      expected_version: int, actor_subject: str, occurred_at: str) -> dict:
        conn, cursor = self._open()
        try:
            current = self._fetchone(cursor, SAMPLE_SELECT_SQL, (project_id, sample_id), SAMPLE_COLUMNS)
            if current is None:
                raise CentralSampleInventoryNotFoundError(f'unknown sample_id {sample_id}')
            latest = self._fetchone(cursor, LATEST_INTAKE_SQL, (sample_id,), INTAKE_COLUMNS)
            current_value = _row_projection(current, latest)
            next_status = transition_status(current_value['status'], status)
            # Even a no-op transition is a conditional write request. Accepting
            # it with a stale version would let a client mistake an old row for
            # the current projection and bypass the optimistic-concurrency
            # contract.
            assert_expected_version(current_value['row_version'], expected_version)
            if next_status.value == current_value['status']:
                conn.commit()
                return current_value
            next_version = int(current_value['row_version']) + 1
            deleted_at = occurred_at if next_status is SampleStatus.DELETED else None
            deleted_by = actor_subject if next_status is SampleStatus.DELETED else None
            cursor.execute(STATUS_UPDATE_SQL, (
                next_status.value, deleted_at, deleted_by, occurred_at,
                project_id, sample_id,
            ))
            if getattr(cursor, 'rowcount', 1) != 1:
                raise ValueError('sample version conflict')
            after = dict(current_value)
            after.update({
                'status': next_status.value,
                'row_version': next_version,
                'deleted_at': deleted_at,
                'deleted_by': deleted_by,
            })
            project = self._fetchone(cursor, PROJECT_SELECT_SQL, (project_id,), PROJECT_COLUMNS)
            revision_number = self._next_revision(cursor, sample_id)
            snapshot = _snapshot_for_projection(
                project or {'project_id': project_id}, sample_id, after,
                after.get('latest_intake'), revision_number, occurred_at,
            )
            event = event_for_change(
                status_before=current_value['status'], status_after=next_status.value,
            )
            self._insert_revision(cursor, sample_id, project_id, revision_number, event,
                                  snapshot, ('status', 'row_version'), actor_subject, occurred_at)
            conn.commit()
            return after
        except CentralSampleInventoryNotFoundError:
            self._rollback(conn)
            raise
        except SampleExpectedVersionConflict:
            self._rollback(conn)
            raise
        except SampleInventoryPolicyError:
            self._rollback(conn)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rollback(conn)
            if 'version conflict' in str(exc).lower():
                raise ValueError('sample version conflict') from exc
            raise CentralSampleInventoryWriteError(f'change sample status failed: {exc}') from exc
        finally:
            self._close(conn, cursor)

    def hard_delete(self, sample_id: str, *, actor_subject: str, occurred_at: str) -> dict:
        conn, cursor = self._open()
        try:
            row = self._fetchone(
                cursor,
                'SELECT "id", "project_id" FROM "samples" WHERE "id" = %s FOR UPDATE',
                (sample_id,),
                ('id', 'project_id'),
            )
            if row is None:
                raise CentralSampleInventoryNotFoundError(f'unknown sample_id {sample_id}')
            # Tombstone contains only stable identifiers and actor/time. No sample
            # labels, serials, notes, or intake values are copied to the audit log.
            revision_row = self._fetchone(
                cursor,
                'SELECT COUNT(*) AS "revision_count" FROM "sample_inventory_revisions" '
                'WHERE "sample_id" = %s',
                (sample_id,),
                ('revision_count',),
            )
            revision_count = int(
                revision_row.get('revision_count', 0) if isinstance(revision_row, Mapping)
                else (revision_row[0] if revision_row else 0)
            )
            project_id = row.get('project_id')
            project_id_text = str(project_id) if project_id is not None else None
            cursor.execute(AUDIT_HARD_DELETE_SQL, (
                str(uuid.uuid4()), 'sample.hard_deleted', project_id_text, actor_subject,
                json.dumps({
                    'sample_id': sample_id,
                    'project_id': project_id_text,
                    'revision_count': revision_count,
                    'reason': 'system_admin_request',
                }, sort_keys=True, separators=(',', ':')),
                occurred_at, occurred_at,
            ))
            cursor.execute('DELETE FROM "sample_custody_events" WHERE "sample_id" = %s', (sample_id,))
            cursor.execute('DELETE FROM "sample_intakes" WHERE "sample_id" = %s', (sample_id,))
            cursor.execute('DELETE FROM "sample_inventory_revisions" WHERE "sample_id" = %s', (sample_id,))
            cursor.execute('DELETE FROM "samples" WHERE "id" = %s', (sample_id,))
            conn.commit()
            return {'sample_id': sample_id, 'hard_deleted': True}
        except CentralSampleInventoryNotFoundError:
            self._rollback(conn)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rollback(conn)
            raise CentralSampleInventoryWriteError(f'hard delete sample failed: {exc}') from exc
        finally:
            self._close(conn, cursor)

    def append_custody_event(self, project_id: str, sample_id: str,
                             payload: Mapping[str, Any], *, actor_subject: str,
                             occurred_at: str) -> dict:
        """Append one 반입/반출 사건 (ADR-0002).

        ⚠️ `sample_inventory_revisions` 에는 쓰지 않고 `row_version` 도 올리지 않는다.
        그 원장은 `samples` 현재 투영의 스냅샷이고 그 모양을 측정 세션이 함께 쓴다 —
        custody 축의 추가마다 그 계약을 흔들 이유가 없다. 이 행이 스스로
        `actor_subject`/`created_at` 을 갖는다. row_version 을 올리지 않으므로 편집
        화면이 열려 있어도 헛된 409 가 나지 않는다.
        """
        conn, cursor = self._open()
        try:
            value = validate_custody_event(payload)
            owner = self._fetchone(
                cursor, SAMPLE_OWNERSHIP_SQL, (project_id, sample_id), ('id',))
            if owner is None:
                raise CentralSampleInventoryNotFoundError(f'unknown sample_id {sample_id}')
            event_id = str(uuid.uuid4())
            cursor.execute(CUSTODY_INSERT_SQL, (
                event_id, sample_id, project_id, value['event_type'],
                value['occurred_on'], value['counterparty'],
                value['intake_cert_number'], value['reason'], value['note'],
                actor_subject, occurred_at, occurred_at,
            ))
            conn.commit()
            return {
                'id': event_id, 'sample_id': sample_id, 'project_id': project_id,
                **{field: value[field] for field in CUSTODY_EVENT_FIELDS},
                'actor_subject': actor_subject,
                'created_at': occurred_at, 'updated_at': occurred_at,
            }
        except CentralSampleInventoryNotFoundError:
            self._rollback(conn)
            raise
        except SampleInventoryPolicyError:
            self._rollback(conn)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rollback(conn)
            raise CentralSampleInventoryWriteError(
                f'append custody event failed: {exc}') from exc
        finally:
            self._close(conn, cursor)

    def delete_custody_event(self, project_id: str, sample_id: str, event_id: str, *,
                             actor_subject: str, occurred_at: str) -> dict:
        """Remove one wrongly recorded custody event (ADR-0002).

        정정 수단은 수정이 아니라 삭제다 — 수정은 흔적 없이 과거를 바꾸지만 삭제는
        보이고, 다시 적으면 새 행위자와 시각이 붙는다.
        """
        conn, cursor = self._open()
        try:
            cursor.execute(CUSTODY_DELETE_SQL, (event_id, sample_id, project_id))
            if getattr(cursor, 'rowcount', 1) != 1:
                raise CentralSampleInventoryNotFoundError(
                    f'unknown custody event {event_id}')
            conn.commit()
            return {'custody_event_id': event_id, 'deleted': True}
        except CentralSampleInventoryNotFoundError:
            self._rollback(conn)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rollback(conn)
            raise CentralSampleInventoryWriteError(
                f'delete custody event failed: {exc}') from exc
        finally:
            self._close(conn, cursor)

    def _insert_intake(self, cursor, sample_id: str, value: Mapping[str, Any], now: str) -> None:
        cursor.execute(INTAKE_INSERT_SQL, (
            str(uuid.uuid4()), sample_id, value.get('intake_date'), value.get('bl'),
            value.get('ap'), value.get('cp'), value.get('csc'), value.get('rf_cal'),
            value.get('hw_rev'), value.get('note'), value.get('tech_group'), now, now,
        ))

    def _insert_revision(self, cursor, sample_id, project_id, revision_number,
                         event_type, snapshot, changed_fields, actor_subject, occurred_at):
        cursor.execute(REVISION_INSERT_SQL, (
            str(uuid.uuid4()), sample_id, project_id, revision_number,
            event_type.value if isinstance(event_type, SampleRevisionEvent) else str(event_type),
            snapshot_json(snapshot), json.dumps(list(changed_fields), separators=(',', ':')),
            actor_subject, occurred_at, occurred_at,
        ))

    def _next_revision(self, cursor, sample_id: str) -> int:
        row = self._fetchone(cursor, NEXT_REVISION_SQL, (sample_id,))
        current = int((row or [0])[0] if not isinstance(row, Mapping) else next(iter(row.values())))
        return current + 1

    @staticmethod
    def _fetchone(cursor, statement: str, params: tuple, columns: tuple[str, ...] = ()):
        cursor.execute(statement, params)
        row = cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, Mapping):
            return dict(row)
        description = getattr(cursor, 'description', None)
        if description:
            return dict(zip((str(item[0]) for item in description), row))
        if columns:
            return dict(zip(columns, row))
        return row

    def _open(self):
        try:
            conn = self._connection_factory()
            return conn, conn.cursor()
        except Exception as exc:  # noqa: BLE001
            raise CentralSampleInventoryWriteError(f'central sample write connection failed: {exc}') from exc

    @staticmethod
    def _rollback(conn):
        rollback = getattr(conn, 'rollback', None)
        if callable(rollback):
            rollback()

    @staticmethod
    def _close(conn, cursor):
        try:
            cursor.close()
        finally:
            close = getattr(conn, 'close', None)
            if callable(close):
                close()


def _initial_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_patch(payload)
    sample_number = payload.get('sample_number')
    sample_code = payload.get('sample_code') or sample_number
    if not sample_number or not sample_code:
        raise ValueError('sample_number and sample_code are required')
    result = {field: validated.get(field) for field in SAMPLE_EDITABLE_FIELDS}
    result['sample_number'] = sample_number
    result['sample_code'] = sample_code
    if 'latest_intake' in validated:
        result['latest_intake'] = dict(validated['latest_intake'])
    return result


def _row_projection(row: Mapping[str, Any], latest: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    result = {field: row.get(field) for field in SAMPLE_EDITABLE_FIELDS}
    result.update({
        'id': row.get('id'), 'project_id': row.get('project_id'),
        'status': row.get('status', SampleStatus.ACTIVE.value),
        'row_version': int(row.get('row_version', 1)),
        'deleted_at': row.get('deleted_at'),
        'deleted_by': row.get('deleted_by'),
        'latest_intake': dict(latest) if latest else None,
    })
    return result


def _sample_update_values(after, now, project_id, sample_id):
    return tuple(
        [after.get(field) for field in SAMPLE_EDITABLE_FIELDS]
        + [after['status'], after['row_version'],
           after.get('deleted_at'), after.get('deleted_by'), now,
           project_id, sample_id]
    )


def _snapshot_for_projection(project, sample_id, projection, latest_intake,
                             revision_number, captured_at):
    value = dict(projection)
    value['id'] = sample_id
    return canonical_snapshot(
        project=project,
        sample=value,
        latest_intake=latest_intake,
        sample_revision=revision_number,
        captured_at=captured_at,
    ).as_dict()


__all__ = [
    'AUDIT_HARD_DELETE_SQL',
    'CUSTODY_DELETE_SQL',
    'CUSTODY_INSERT_SQL',
    'INTAKE_INSERT_SQL',
    'NEXT_REVISION_SQL',
    'PostgresCentralSampleInventoryWriteAdapter',
    'REVISION_INSERT_SQL',
    'SAMPLE_INSERT_SQL',
    'SAMPLE_OWNERSHIP_SQL',
    'SAMPLE_UPDATE_SQL',
    'STATUS_UPDATE_SQL',
]
