"""Read-only PostgreSQL adapter for the web sample inventory.

All historical reads use a windowed latest-revision query.  The adapter never
uses OFFSET and applies project/team/status predicates to the selected
revision snapshot, not to the current projection.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from fcc_test_platform.domain.ports.output.central_sample_inventory_read_port import (
    CentralSampleInventoryReadError,
)
from fcc_test_kernel.domain.models.sample_inventory import custody_state
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


SAMPLE_COLUMNS: tuple[str, ...] = (
    'sample_id', 'project_id', 'model_id', 'sample_number', 'sample_code',
    'sample_kind', 'sample_description', 'test_category',
    'label_number', 'smsn', 'serial_number', 'intake_cert', 'assigned_team',
    'sender', 'receiver', 'received_date', 'released_date', 'note', 'status',
    'row_version', 'deleted_at', 'deleted_by', 'created_at', 'updated_at',
    'latest_intake_id', 'intake_date', 'bl', 'ap', 'cp', 'csc', 'rf_cal',
    'hw_rev', 'intake_note', 'tech_group', 'intake_count',
    'latest_custody_event_type', 'latest_custody_occurred_on', 'custody_event_count',
)

CURRENT_SAMPLE_SQL = (
    'SELECT s."id" AS "sample_id", s."project_id", s."model_id", s."sample_number", '
    's."sample_code", s."sample_kind", s."sample_description", '
    's."test_category", s."label_number", s."smsn", '
    's."serial_number", s."intake_cert", s."assigned_team", s."sender", '
    's."receiver", s."received_date", s."released_date", s."note", '
    's."status", s."row_version", s."deleted_at", s."deleted_by", '
    's."created_at", s."updated_at", i."id" AS "latest_intake_id", '
    'i."intake_date", i."bl", i."ap", i."cp", i."csc", i."rf_cal", '
    'i."hw_rev", i."note" AS "intake_note", i."tech_group", '
    'COALESCE(c."intake_count", 0) AS "intake_count", '
    # PM 축 요약: 가장 최근 custody 사건과 총 건수. 목록에서 '지금 보유 중인가'를
    # 보이려면 이 두 값이면 충분하다 — 사건 전체는 상세에서 따로 읽는다.
    # ⚠️ SQL 은 '가장 최근 사건'을 고르기만 한다. 그 event_type 이 보유 상태로
    # 번역되는 규칙은 커널의 custody_state() 한 자리에만 산다.
    'ce."event_type" AS "latest_custody_event_type", '
    'ce."occurred_on" AS "latest_custody_occurred_on", '
    'COALESCE(cc."custody_event_count", 0) AS "custody_event_count" '
    'FROM "samples" s '
    'LEFT JOIN (SELECT ranked.* FROM (SELECT i.*, ROW_NUMBER() OVER '
    '(PARTITION BY i."sample_id" ORDER BY i."created_at" DESC, i."id" DESC) AS "rn" '
    'FROM "sample_intakes" i) ranked WHERE ranked."rn" = 1) i '
    'ON i."sample_id" = s."id" '
    'LEFT JOIN (SELECT "sample_id", COUNT(*) AS "intake_count" '
    'FROM "sample_intakes" GROUP BY "sample_id") c '
    'ON c."sample_id" = s."id" '
    'LEFT JOIN (SELECT ranked.* FROM (SELECT ce.*, ROW_NUMBER() OVER '
    '(PARTITION BY ce."sample_id" ORDER BY ce."created_at" DESC, ce."id" DESC) AS "rn" '
    'FROM "sample_custody_events" ce) ranked WHERE ranked."rn" = 1) ce '
    'ON ce."sample_id" = s."id" '
    'LEFT JOIN (SELECT "sample_id", COUNT(*) AS "custody_event_count" '
    'FROM "sample_custody_events" GROUP BY "sample_id") cc '
    'ON cc."sample_id" = s."id" '
)

CUSTODY_HISTORY_SQL = (
    'SELECT e."id" AS "custody_event_id", e."sample_id", e."project_id", '
    'e."event_type", e."occurred_on", e."counterparty", e."intake_cert_number", '
    'e."reason", e."note", e."actor_subject", e."created_at", e."updated_at" '
    'FROM "sample_custody_events" e '
    'WHERE e."project_id" = %s AND e."sample_id" = ANY(%s) '
)
CUSTODY_COLUMNS = (
    'custody_event_id', 'sample_id', 'project_id', 'event_type', 'occurred_on',
    'counterparty', 'intake_cert_number', 'reason', 'note', 'actor_subject',
    'created_at', 'updated_at',
)

HISTORY_SAMPLE_SQL = (
    'SELECT "id" AS "revision_id", "sample_id", "project_id", '
    '"revision_number", "event_type", "snapshot_json", '
    '"changed_fields_json", "actor_subject", "occurred_at", "created_at" '
    'FROM "sample_inventory_revisions" '
    'WHERE "project_id" = %s AND "sample_id" = %s '
)

INTAKE_HISTORY_SQL = (
    'SELECT i."id" AS "intake_id", i."sample_id", s."project_id", '
    's."sample_number", s."test_category", i."intake_date", i."bl", '
    'i."ap", i."cp", i."csc", i."rf_cal", i."hw_rev", i."note", '
    'i."tech_group", i."created_at", i."updated_at" '
    'FROM "sample_intakes" i JOIN "samples" s ON s."id" = i."sample_id" '
    'WHERE s."project_id" = %s AND i."sample_id" = ANY(%s) '
)

AS_OF_SAMPLE_SQL = (
    'SELECT "revision_id", "sample_id", "project_id", "revision_number", '
    '"event_type", "snapshot_json", "changed_fields_json", "actor_subject", '
    '"occurred_at", "created_at" FROM (SELECT r."id" AS "revision_id", '
    'r."sample_id", r."project_id", r."revision_number", r."event_type", '
    'r."snapshot_json", r."changed_fields_json", r."actor_subject", '
    'r."occurred_at", r."created_at", ROW_NUMBER() OVER (PARTITION BY r."sample_id" '
    'ORDER BY r."occurred_at" DESC, r."revision_number" DESC, r."id" DESC) AS "rn" '
    'FROM "sample_inventory_revisions" r WHERE r."occurred_at" <= %s) selected '
    'WHERE selected."rn" = 1 '
)

PROJECT_SQL = (
    'SELECT p."id" AS "project_id", p."project_code", dm."model_name", '
    'p."management_number", p."status" AS "project_status" '
    'FROM "projects" p LEFT JOIN "device_models" dm ON dm."project_id" = p."id" '
    'WHERE p."id" = %s LIMIT 1'
)
PROJECT_COLUMNS = ('project_id', 'project_code', 'model_name', 'management_number', 'project_status')
PUBLISHED_PLAN_PROJECT_SQL = (
    'SELECT DISTINCT "project_id" FROM "published_plan_expectation" '
    'WHERE "plan_id" = %s LIMIT 2'
)
REVISION_COLUMNS = (
    'revision_id', 'sample_id', 'project_id', 'revision_number', 'event_type',
    'snapshot_json', 'changed_fields_json', 'actor_subject', 'occurred_at', 'created_at',
)
INTAKE_COLUMNS = (
    'intake_id', 'sample_id', 'project_id', 'sample_number', 'test_category',
    'intake_date', 'bl', 'ap', 'cp', 'csc', 'rf_cal', 'hw_rev', 'note',
    'tech_group', 'created_at', 'updated_at',
)

MEASUREMENT_SNAPSHOT_SQL = (
    'SELECT current_sample.*, '
    'p."id" AS "snapshot_project_id", p."project_code", '
    '(SELECT dm0."model_name" FROM "device_models" dm0 '
    'WHERE dm0."project_id" = p."id" '
    'ORDER BY dm0."created_at" DESC, dm0."id" DESC LIMIT 1) AS "model_name", '
    'p."management_number", p."status" AS "project_status", '
    '(SELECT r."revision_number" FROM "sample_inventory_revisions" r '
    'WHERE r."project_id" = current_sample."project_id" '
    'AND r."sample_id" = current_sample."sample_id" '
    'ORDER BY r."occurred_at" DESC, r."revision_number" DESC, r."id" DESC LIMIT 1) '
    'AS "sample_revision" '
    'FROM (' + CURRENT_SAMPLE_SQL + ') current_sample '
    'JOIN "projects" p ON p."id" = current_sample."project_id" '
    'WHERE current_sample."project_id" = %s '
    'AND current_sample."sample_id" = %s '
    'LIMIT 1'
)


class PostgresCentralSampleInventoryReadAdapter:
    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def list_samples(
        self, *, project_id: Optional[str] = None, team: Optional[str] = None,
        status: Optional[str] = None, as_of: Optional[str] = None,
        after: Optional[tuple] = None, limit: int = 100,
        include_deleted: bool = False,
    ) -> dict:
        if as_of:
            rows = self._list_as_of(
                project_id=project_id, team=team, status=status,
                as_of=as_of, after=after, limit=limit,
                include_deleted=include_deleted,
            )
        else:
            rows = self._list_current(
                project_id=project_id, team=team, status=status,
                after=after, limit=limit, include_deleted=include_deleted,
            )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            if as_of:
                next_cursor = [
                    _cursor_value(last.get('occurred_at')), last.get('revision_number'),
                    _cursor_value(last.get('sample_id')), _cursor_value(last.get('revision_id')),
                ]
            else:
                next_cursor = [
                    _cursor_value(last.get('created_at')),
                    _cursor_value(last.get('sample_id')),
                ]
        envelope = _revision_envelope if as_of else _sample_envelope
        return {'items': [envelope(row) for row in page], 'next_cursor': next_cursor}

    def get_sample(self, project_id: str, sample_id: str, *, as_of: Optional[str] = None) -> Optional[dict]:
        if as_of:
            rows = self._query(
                AS_OF_SAMPLE_SQL + ' AND "project_id" = %s AND "sample_id" = %s',
                (as_of, project_id, sample_id),
                columns=REVISION_COLUMNS,
            )
            return _revision_envelope(rows[0]) if rows else None
        rows = self._query(
            CURRENT_SAMPLE_SQL + ' WHERE s."project_id" = %s AND s."id" = %s LIMIT 1',
            (project_id, sample_id), columns=SAMPLE_COLUMNS,
        )
        return _sample_envelope(rows[0]) if rows else None

    def list_history(self, project_id: str, sample_id: str, *, after: Optional[tuple] = None,
                     limit: int = 100) -> dict:
        predicates = ['"project_id" = %s', '"sample_id" = %s']
        params: list = [project_id, sample_id]
        if after:
            predicates.append('("occurred_at", "revision_number", "id") < (%s, %s, %s)')
            params.extend(after)
        # HISTORY_SAMPLE_SQL already includes a WHERE. Add predicates beyond the
        # identity pair without changing the keyset order or introducing OFFSET.
        statement = (
            'SELECT "id" AS "revision_id", "sample_id", "project_id", '
            '"revision_number", "event_type", "snapshot_json", '
            '"changed_fields_json", "actor_subject", "occurred_at", "created_at" '
            'FROM "sample_inventory_revisions" WHERE ' + ' AND '.join(predicates) +
            ' ORDER BY "occurred_at" DESC, "revision_number" DESC, "id" DESC LIMIT %s'
        )
        params.append(limit + 1)
        rows = self._query(statement, tuple(params), columns=REVISION_COLUMNS)
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            row = page[-1]
            next_cursor = [
                _cursor_value(row['occurred_at']), row['revision_number'],
                _cursor_value(row['revision_id']),
            ]
        return {'items': [_revision_envelope(row) for row in page], 'next_cursor': next_cursor}

    def list_intakes(self, project_id: str, sample_ids: list[str], *,
                     as_of: Optional[str] = None) -> list[dict]:
        if not sample_ids:
            return []
        # Use scalar placeholders rather than PostgreSQL's array ``ANY`` here.
        # The production driver and the repository SQLite shim then exercise the
        # same predicate, while the list remains fully parameterized.
        placeholders = ', '.join('%s' for _ in sample_ids)
        statement = INTAKE_HISTORY_SQL.replace('= ANY(%s)', f'IN ({placeholders})')
        params: list = [project_id, *sample_ids]
        if as_of:
            statement += ' AND i."created_at" <= %s'
            params.append(as_of)
        statement += ' ORDER BY i."sample_id" ASC, i."created_at" ASC, i."id" ASC'
        return self._query(statement, tuple(params), columns=INTAKE_COLUMNS)

    def list_custody_events(self, project_id: str, sample_ids: list[str], *,
                            as_of: Optional[str] = None) -> list[dict]:
        """Read 반입/반출 사건 for the given samples, oldest first.

        `list_intakes` 와 같은 형태다 — PostgreSQL 의 배열 ``ANY`` 대신 스칼라
        placeholder 로 펴서, 운영 드라이버와 테스트의 SQLite shim 이 같은 술어를
        지나가게 한다.
        """
        if not sample_ids:
            return []
        placeholders = ', '.join('%s' for _ in sample_ids)
        statement = CUSTODY_HISTORY_SQL.replace('= ANY(%s)', f'IN ({placeholders})')
        params: list = [project_id, *sample_ids]
        if as_of:
            statement += ' AND e."created_at" <= %s'
            params.append(as_of)
        statement += ' ORDER BY e."sample_id" ASC, e."created_at" ASC, e."id" ASC'
        return self._query(statement, tuple(params), columns=CUSTODY_COLUMNS)

    def get_project(self, project_id: str) -> Optional[dict]:
        rows = self._query(PROJECT_SQL, (project_id,), columns=PROJECT_COLUMNS)
        return dict(rows[0]) if rows else None

    def get_published_plan_project_id(self, plan_id: str) -> Optional[str]:
        rows = self._query(PUBLISHED_PLAN_PROJECT_SQL, (plan_id,))
        project_ids = {str(row.get('project_id') or '').strip() for row in rows}
        project_ids.discard('')
        if len(project_ids) > 1:
            raise CentralSampleInventoryReadError(
                f'published plan {plan_id!r} maps to multiple projects'
            )
        return next(iter(project_ids), None)

    def get_measurement_snapshot_inputs(
        self, project_id: str, sample_id: str, *,
        published_plan_id: Optional[str] = None,
    ) -> dict:
        """Read project, sample, latest intake, revision, and plan identity together.

        This is intentionally a single connection/transaction boundary. The
        chamber start path must not read a project, sample, and history from
        independent snapshots that can disagree between calls.
        """
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise CentralSampleInventoryReadError(
                f'central measurement snapshot connection failed: {exc}'
            ) from exc
        cursor = None
        try:
            cursor = connection.cursor()
            plan_project_id = None
            plan_project_count = 0
            if published_plan_id:
                cursor.execute(PUBLISHED_PLAN_PROJECT_SQL, (published_plan_id,))
                plan_rows = list(cursor.fetchall())
                plan_project_ids = {
                    str(row[0] or '').strip() for row in plan_rows
                }
                plan_project_ids.discard('')
                plan_project_count = len(plan_project_ids)
                if plan_project_count > 1:
                    raise CentralSampleInventoryReadError(
                        f'published plan {published_plan_id!r} maps to multiple projects'
                    )
                plan_project_id = next(iter(plan_project_ids), None)

            cursor.execute(MEASUREMENT_SNAPSHOT_SQL, (project_id, sample_id))
            row = cursor.fetchone()
            connection.commit()
        except CentralSampleInventoryReadError:
            rollback = getattr(connection, 'rollback', None)
            if callable(rollback):
                rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            rollback = getattr(connection, 'rollback', None)
            if callable(rollback):
                rollback()
            raise CentralSampleInventoryReadError(
                f'central measurement snapshot query failed: {exc}'
            ) from exc
        finally:
            if cursor is not None:
                cursor.close()
            close = getattr(connection, 'close', None)
            if callable(close):
                close()

        if row is None:
            return {
                'sample': None,
                'project': None,
                'sample_revision': None,
                'plan_project_id': plan_project_id,
                'plan_project_count': plan_project_count,
            }
        sample_end = len(SAMPLE_COLUMNS)
        project_end = sample_end + len(PROJECT_COLUMNS)
        sample = _sample_envelope(dict(zip(SAMPLE_COLUMNS, row[:sample_end])))
        project_values = row[sample_end:project_end]
        project_values = (project_values[0], *project_values[1:])
        project = dict(zip(PROJECT_COLUMNS, project_values))
        return {
            'sample': sample,
            'project': project,
            'sample_revision': row[project_end],
            'plan_project_id': plan_project_id,
            'plan_project_count': plan_project_count,
        }

    def _list_current(self, *, project_id, team, status, after, limit, include_deleted):
        predicates: list[str] = []
        params: list = []
        if project_id:
            predicates.append('s."project_id" = %s')
            params.append(project_id)
        if team:
            predicates.append('LOWER(COALESCE(s."assigned_team", \'\')) = LOWER(%s)')
            params.append(team)
        if status:
            predicates.append('s."status" = %s')
            params.append(status)
        elif not include_deleted:
            predicates.append('s."status" = \'active\'')
        if after:
            predicates.append('(s."created_at", s."id") > (%s, %s)')
            params.extend(after)
        statement = CURRENT_SAMPLE_SQL
        if predicates:
            statement += ' WHERE ' + ' AND '.join(predicates)
        statement += ' ORDER BY s."created_at" ASC, s."id" ASC LIMIT %s'
        params.append(limit + 1)
        return self._query(statement, tuple(params), columns=SAMPLE_COLUMNS)

    def _list_as_of(self, *, project_id, team, status, as_of, after, limit,
                    include_deleted=False):
        predicates: list[str] = []
        params: list = [as_of]
        if project_id:
            predicates.append('selected."project_id" = %s')
            params.append(project_id)
        if team:
            predicates.append(
                "LOWER(COALESCE(selected.\"snapshot_json\"->'sample'->>'assigned_team', '')) = LOWER(%s)"
            )
            params.append(team)
        if status:
            predicates.append("selected.\"snapshot_json\"->'sample'->>'status' = %s")
            params.append(status)
        elif not include_deleted:
            predicates.append("selected.\"snapshot_json\"->'sample'->>'status' = 'active'")
        if after:
            predicates.append(
                '(selected."occurred_at", selected."revision_number", '
                'selected."sample_id", selected."revision_id") > (%s, %s, %s, %s)'
            )
            params.extend(after)
        statement = AS_OF_SAMPLE_SQL
        if predicates:
            statement += ' AND ' + ' AND '.join(predicates)
        statement += (
            ' ORDER BY selected."occurred_at" ASC, selected."revision_number" ASC, '
            'selected."sample_id" ASC, selected."revision_id" ASC LIMIT %s'
        )
        params.append(limit + 1)
        return self._query(statement, tuple(params), columns=REVISION_COLUMNS)

    def _query(self, statement: str, params: tuple, *, columns: Optional[tuple[str, ...]] = None) -> list[dict]:
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise CentralSampleInventoryReadError(f'central sample read connection failed: {exc}') from exc
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, params)
                raw = list(cursor.fetchall())
                description = getattr(cursor, 'description', None)
            finally:
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            raise CentralSampleInventoryReadError(f'central sample read query failed: {exc}') from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()
        if columns is None:
            columns = tuple(str(item[0]) for item in (description or ()))
        return [dict(zip(columns, row)) for row in raw]


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value or {}


def _cursor_value(value):
    """Convert DB driver values to JSON-safe, stable keyset tokens."""
    if hasattr(value, 'isoformat') and callable(value.isoformat):
        return value.isoformat()
    return value


def _sample_envelope(row: dict) -> dict:
    latest = None
    if row.get('latest_intake_id') is not None:
        latest = {
            'id': row.get('latest_intake_id'),
            'sample_id': row.get('sample_id'),
            'intake_date': row.get('intake_date'),
            'bl': row.get('bl'), 'ap': row.get('ap'), 'cp': row.get('cp'),
            'csc': row.get('csc'), 'rf_cal': row.get('rf_cal'),
            'hw_rev': row.get('hw_rev'), 'note': row.get('intake_note'),
            'tech_group': row.get('tech_group'),
        }
    result = dict(row)
    result['latest_intake'] = latest
    # 원시 컬럼은 내보내지 않는다 — 클라이언트가 event_type 을 직접 해석하기
    # 시작하면 보유 상태 규칙이 두 곳에 살게 된다.
    result['custody_state'] = custody_state(
        {'event_type': row['latest_custody_event_type']}
        if row.get('latest_custody_event_type') is not None else None
    )
    result['latest_custody_occurred_on'] = row.get('latest_custody_occurred_on')
    result['custody_event_count'] = int(row.get('custody_event_count') or 0)
    result.pop('latest_custody_event_type', None)
    result.pop('latest_intake_id', None)
    result.pop('intake_date', None)
    result.pop('bl', None); result.pop('ap', None); result.pop('cp', None)
    result.pop('csc', None); result.pop('rf_cal', None); result.pop('hw_rev', None)
    result.pop('intake_note', None); result.pop('tech_group', None)
    return result


def _revision_envelope(row: dict) -> dict:
    result = dict(row)
    result['snapshot'] = _json(result.get('snapshot_json'))
    result['changed_fields'] = _json(result.get('changed_fields_json'))
    result.pop('snapshot_json', None)
    result.pop('changed_fields_json', None)
    return result


__all__ = [
    'AS_OF_SAMPLE_SQL',
    'CURRENT_SAMPLE_SQL',
    'CUSTODY_HISTORY_SQL',
    'HISTORY_SAMPLE_SQL',
    'PROJECT_SQL',
    'PUBLISHED_PLAN_PROJECT_SQL',
    'MEASUREMENT_SNAPSHOT_SQL',
    'PostgresCentralSampleInventoryReadAdapter',
]
