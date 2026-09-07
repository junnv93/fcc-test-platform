"""PostgreSQL adapter for the generic project-result reference ledger."""
from __future__ import annotations

from typing import Callable, Mapping, Optional

from fcc_test_kernel.application.central_contract.pagination import (
    CursorValueDomain,
    decode_cursor,
    encode_cursor,
)
from fcc_test_kernel.domain.models.project_result_reference import canonical_payload_hash
from fcc_test_platform.domain.ports.output.central_project_reference_port import (
    CentralProjectReferencePort,
    CentralProjectReferenceError,
    ReferenceNotFoundError,
    ReferenceRetiredError,
    ReferenceHashMismatchError,
    ReferenceSourceMismatchError,
)
from fcc_test_kernel.domain.ports.output.platform_database_port import DbCursor
# ⚠️ 이 어댑터가 요구하는 «행을 읽는» 커서/연결 표면은 여기서 **가져온다** — 한때
#    이 파일 안에 `_RowCursor`·`_RowConnection` 사본이 있었다. `central_db_surfaces`
#    의 모듈 docstring 이 *「모듈마다 사본을 두지 않는다. 같은 표면을 11번 선언하면
#    그중 하나가 조용히 갈리는 날이 온다」* 고 적어 둔 그 예언이 실제로 일어났다:
#
#        SSOT   fetchall() -> Sequence   ·   fetchone() 있음
#        사본   fetchall() -> list       ·   fetchone() 없음
#
#    반환 타입은 공변이라 `Sequence` 는 `list` 의 부분형이 **아니다.** 그래서
#    `RowConnection` 은 `_RowConnection` 이 아니었고, 공용 연결 팩토리를 이 어댑터에
#    넘기는 `api_composition` 의 한 줄이 타입상 «틀린» 상태로 있었다.
#
#    ⚠️ 그런데 그 갈라짐은 **`kernel-v0.5.1` 이 `py.typed` 를 싣기 전까지 보이지
#    않았다.** 커널 포트가 `Any` 로 보이는 동안에는 두 사본이 무엇을 약속하든
#    같은 값(=Any)이었기 때문이다. 즉 사본은 「언젠가」가 아니라 「표식이 켜지는
#    날」에 소리를 냈다. 사본을 남기는 비용은 그때까지 «침묵»으로 지불된다.
from fcc_test_platform.application.central_db_surfaces import RowConnection, RowCursor


__all__ = ['PostgresCentralProjectReferenceAdapter']


REFERENCE_CURSOR_DOMAINS: tuple[CursorValueDomain, ...] = (
    CursorValueDomain.UUID,
)

_REFERENCE_IDENTITY_LOCK_SQL = (
    'SELECT pg_advisory_xact_lock(hashtextextended('
    'concat_ws(chr(31), %s::text, %s::text, %s::text, %s::text), 0))'
)


_REFERENCE_COLUMNS = (
    'id', 'project_id', 'producer_provider_id', 'reference_type',
    'schema_version', 'source_selection_event_id', 'source_attempt_id',
    'source_session_id', 'source_sample_id', 'source_chamber_id', 'payload_json',
    'content_sha256', 'state', 'revision_number', 'created_by', 'created_at',
    'retired_by', 'retired_at', 'retirement_reason',
)

_REFERENCE_TARGET_COLUMNS = (
    'id', 'project_id', 'producer_provider_uuid', 'producer_provider_id',
    'reference_type', 'schema_version', 'source_selection_event_id',
    'source_attempt_id', 'source_session_id', 'source_sample_id',
    'source_chamber_id', 'payload_json', 'content_sha256', 'state',
    'revision_number', 'created_by', 'created_at', 'retired_by', 'retired_at',
    'retirement_reason',
)


def _jsonb_payload(value: Mapping) -> object:
    """Bind an opaque provider object through psycopg's JSONB adapter.

    The provider mapping and its canonical hash stay owned by the application
    boundary; this wrapper only supplies psycopg's PostgreSQL JSONB type
    adaptation for the placeholder.  Passing the mapping directly is rejected
    by psycopg, while serialising it to text would change the JSONB value's
    boundary semantics and duplicate the canonical payload serializer.
    """
    from psycopg.types.json import Jsonb  # type: ignore

    return Jsonb(dict(value))




class PostgresCentralProjectReferenceAdapter(CentralProjectReferencePort):
    """Keep source validation and lifecycle writes in one central transaction."""

    def __init__(self, connection_factory: Callable[[], RowConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def list_references(
        self, project_id: str, *, producer_provider_id: Optional[str] = None,
        state: Optional[str] = None, limit: int, cursor: Optional[str] = None,
    ) -> Mapping:
        sql = (
            'SELECT r.id, r.project_id, p.provider_id AS producer_provider_id, '
            'r.reference_type, r.schema_version, r.source_selection_event_id, '
            'r.source_attempt_id, r.source_session_id, r.source_sample_id, '
            'r.source_chamber_id, r.payload_json, r.content_sha256, r.state, '
            'r.revision_number, r.created_by, r.created_at, r.retired_by, '
            'r.retired_at, r.retirement_reason '
            'FROM project_result_reference_revisions r '
            'JOIN providers p ON p.id = r.producer_provider_id '
            'WHERE r.project_id IS NOT DISTINCT FROM %s'
        )
        params: list[object] = [project_id]
        if producer_provider_id:
            sql += ' AND p.provider_id = %s'
            params.append(producer_provider_id)
        if state:
            sql += ' AND r.state = %s'
            params.append(state)
        if cursor:
            (last_id,) = decode_cursor(
                cursor, arity=1, domains=REFERENCE_CURSOR_DOMAINS,
            )
            sql += ' AND r.id > %s'
            params.append(last_id)
        sql += ' ORDER BY r.id ASC LIMIT %s'
        params.append(limit + 1)
        rows = self._query(sql, tuple(params))
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            'items': [self._public(row) for row in rows],
            'next_cursor': encode_cursor([str(rows[-1]['id'])])
            if has_more and rows else None,
        }

    def publish_reference(self, record: Mapping) -> Mapping:
        connection = self._open()
        try:
            cursor = connection.cursor()
            try:
                self._serializable(cursor)
                provider = self._resolve_provider_id(
                    cursor, str(record['producer_provider_id'])
                )
                producer_provider_uuid = provider['id']
                source = self._fetch_one(
                    cursor,
                    'SELECT e.id AS selection_event_id, e.action, e.attempt_id, '
                    'a.project_id, p.provider_id AS producer_provider_id, '
                    'a.condition_hash, a.session_id, '
                    's.sample_id, s.chamber_id, a.status '
                    'FROM project_result_selection_events e '
                    'JOIN measurement_attempts a ON a.id = e.attempt_id '
                    'JOIN test_sessions s ON s.id = a.session_id AND s.provider_id = a.provider_id '
                    'JOIN providers p ON p.id = a.provider_id AND p.enabled = TRUE '
                    'WHERE e.id = %s AND e.project_id IS NOT DISTINCT FROM %s '
                    'AND e.provider_id = %s AND e.condition_hash = %s '
                    'AND e.action = \'selected\' AND a.status = \'completed\' '
                    'AND NOT EXISTS (SELECT 1 FROM project_result_selection_events later '
                    'WHERE later.project_id IS NOT DISTINCT FROM e.project_id '
                    'AND later.provider_id = e.provider_id AND later.condition_hash = e.condition_hash '
                    'AND later.revision > e.revision)',
                    (
                        record['source_selection_event_id'], record['project_id'],
                        # The event ledger stores the provider foreign key.  The
                        # public natural key is only valid at the provider lookup
                        # and response boundaries above/below this SQL seam.
                        producer_provider_uuid, record['condition_hash'],
                    ),
                    (
                        'selection_event_id', 'action', 'attempt_id', 'project_id',
                        'producer_provider_id', 'condition_hash', 'session_id',
                        'sample_id', 'chamber_id', 'status',
                    ),
                )
                if source is None:
                    raise ReferenceSourceMismatchError('reference source is not the selected scoped attempt')
                if str(source['producer_provider_id']) != str(record['producer_provider_id']):
                    raise ReferenceSourceMismatchError('reference source provider mismatch')
                self._lock_reference_identity(
                    cursor,
                    record['project_id'], producer_provider_uuid,
                    record['reference_type'], record['schema_version'],
                )
                revision = self._fetch_exactly_one(
                    cursor,
                    'SELECT COALESCE(MAX(revision_number), 0) + 1 AS revision_number '
                    'FROM project_result_reference_revisions '
                    'WHERE project_id IS NOT DISTINCT FROM %s AND producer_provider_id = %s '
                    'AND reference_type = %s AND schema_version = %s',
                    (
                        record['project_id'], producer_provider_uuid,
                        record['reference_type'], record['schema_version'],
                    ),
                    ('revision_number',),
                )
                values = (
                    record['id'], record['project_id'], producer_provider_uuid,
                    record['reference_type'], record['schema_version'],
                    source['selection_event_id'], source['attempt_id'],
                    source['session_id'], source.get('sample_id'),
                    source.get('chamber_id'), _jsonb_payload(record['payload_json']),
                    record['content_sha256'], record['state'],
                    int(revision['revision_number']), record['created_by'],
                    record['created_at'],
                )
                cursor.execute(
                    'INSERT INTO project_result_reference_revisions '
                    '(id, project_id, producer_provider_id, reference_type, schema_version, '
                    'source_selection_event_id, source_attempt_id, source_session_id, '
                    'source_sample_id, source_chamber_id, payload_json, content_sha256, '
                    'state, revision_number, created_by, created_at) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                    values,
                )
                connection.commit()
                return self._public({
                    'id': record['id'], 'project_id': record['project_id'],
                    'producer_provider_id': record['producer_provider_id'],
                    'reference_type': record['reference_type'],
                    'schema_version': record['schema_version'],
                    'source_selection_event_id': source['selection_event_id'],
                    'source_attempt_id': source['attempt_id'],
                    'source_session_id': source['session_id'],
                    'source_sample_id': source.get('sample_id'),
                    'source_chamber_id': source.get('chamber_id'),
                    'payload_json': record['payload_json'],
                    'content_sha256': record['content_sha256'],
                    'state': record['state'], 'revision_number': int(revision['revision_number']),
                    'created_by': record['created_by'], 'created_at': record['created_at'],
                    'retired_by': None, 'retired_at': None, 'retirement_reason': None,
                })
            finally:
                cursor.close()
        except (ReferenceSourceMismatchError, ReferenceNotFoundError,
                CentralProjectReferenceError):
            self._rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rollback(connection)
            raise CentralProjectReferenceError(f'central reference write failed: {exc}') from exc
        finally:
            self._close(connection)

    def retire_reference(self, *, revision_id: str, actor_subject: str,
                         occurred_at: str, reason: str,
                         project_id: Optional[str] = None) -> Mapping:
        connection = self._open()
        try:
            cursor = connection.cursor()
            try:
                self._serializable(cursor)
                target_sql = (
                    'SELECT r.id, r.project_id, '
                    'r.producer_provider_id AS producer_provider_uuid, '
                    'p.provider_id AS producer_provider_id, r.reference_type, '
                    'r.schema_version, r.source_selection_event_id, r.source_attempt_id, '
                    'r.source_session_id, r.source_sample_id, r.source_chamber_id, '
                    'r.payload_json, r.content_sha256, r.state, r.revision_number, '
                    'r.created_by, r.created_at, r.retired_by, r.retired_at, '
                    'r.retirement_reason '
                    'FROM project_result_reference_revisions r '
                    'JOIN providers p ON p.id = r.producer_provider_id '
                    'WHERE r.id = %s AND r.state = \'published\''
                )
                target_params: list[object] = [revision_id]
                if project_id is not None:
                    target_sql += ' AND r.project_id IS NOT DISTINCT FROM %s'
                    target_params.append(project_id)
                target_sql += ' FOR UPDATE'
                target = self._fetch_one(
                    cursor, target_sql, tuple(target_params),
                    _REFERENCE_TARGET_COLUMNS,
                )
                if target is None:
                    raise ReferenceNotFoundError('published reference revision not found')
                producer_provider_uuid = target['producer_provider_uuid']
                self._lock_reference_identity(
                    cursor,
                    target['project_id'], producer_provider_uuid,
                    target['reference_type'], target['schema_version'],
                )
                next_revision = self._fetch_exactly_one(
                    cursor,
                    'SELECT COALESCE(MAX(revision_number), 0) + 1 AS revision_number '
                    'FROM project_result_reference_revisions '
                    'WHERE project_id IS NOT DISTINCT FROM %s '
                    'AND producer_provider_id = %s AND reference_type = %s '
                    'AND schema_version = %s',
                    (
                        target['project_id'], producer_provider_uuid,
                        target['reference_type'], target['schema_version'],
                    ),
                    ('revision_number',),
                )
                cursor.execute(
                    'INSERT INTO project_result_reference_revisions '
                    '(project_id, producer_provider_id, reference_type, schema_version, '
                    'source_selection_event_id, source_attempt_id, source_session_id, '
                    'source_sample_id, source_chamber_id, payload_json, content_sha256, '
                    'state, revision_number, created_by, created_at, retired_by, '
                    'retired_at, retirement_reason) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '
                    '\'retired\', %s, %s, %s, %s, %s, %s) '
                    'RETURNING id, project_id, producer_provider_id, reference_type, '
                    'schema_version, source_selection_event_id, source_attempt_id, '
                    'source_session_id, source_sample_id, source_chamber_id, payload_json, '
                    'content_sha256, state, revision_number, created_by, created_at, '
                    'retired_by, retired_at, retirement_reason',
                    (
                        target['project_id'], producer_provider_uuid,
                        target['reference_type'], target['schema_version'],
                        target['source_selection_event_id'], target['source_attempt_id'],
                        target['source_session_id'], target['source_sample_id'],
                        target['source_chamber_id'], _jsonb_payload(target['payload_json']),
                        target['content_sha256'], int(next_revision['revision_number']),
                        actor_subject, occurred_at, actor_subject, occurred_at, reason,
                    ),
                )
                row = self._fetch_cursor_row(cursor)
                if row is None:
                    raise CentralProjectReferenceError(
                        'reference retirement insert returned no revision'
                    )
                row['producer_provider_id'] = target['producer_provider_id']
                connection.commit()
                return self._public(row)
            finally:
                cursor.close()
        except (ReferenceNotFoundError, CentralProjectReferenceError):
            self._rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rollback(connection)
            raise CentralProjectReferenceError(f'central reference retirement failed: {exc}') from exc
        finally:
            self._close(connection)

    def resolve_reference(self, *, project_id: str, consumer_provider_id: str,
                          revision_id: str, reference_type: str,
                          schema_version: str) -> Mapping:
        rows = self._query(
            'SELECT r.id, r.project_id, producer.provider_id AS producer_provider_id, '
            'r.reference_type, r.schema_version, r.source_selection_event_id, '
            'r.source_attempt_id, r.source_session_id, r.source_sample_id, '
            'r.source_chamber_id, r.payload_json, r.content_sha256, r.state, '
            'r.revision_number, r.created_by, r.created_at, r.retired_by, '
            'r.retired_at, r.retirement_reason, '
            'EXISTS (SELECT 1 FROM project_result_reference_revisions later '
            'WHERE later.project_id IS NOT DISTINCT FROM r.project_id '
            'AND later.producer_provider_id = r.producer_provider_id '
            'AND later.reference_type = r.reference_type '
            'AND later.schema_version = r.schema_version '
            'AND later.revision_number > r.revision_number '
            'AND later.state = \'retired\') AS has_later_retirement '
            'FROM project_result_reference_revisions r '
            'JOIN providers producer ON producer.id = r.producer_provider_id '
            'WHERE r.id = %s '
            'AND r.project_id IS NOT DISTINCT FROM %s AND r.reference_type = %s '
            'AND r.schema_version = %s',
            (revision_id, project_id, reference_type, schema_version),
        )
        if not rows:
            raise ReferenceNotFoundError('reference revision not found')
        row = dict(rows[0])
        has_later_retirement = row.pop('has_later_retirement', False)
        if row.get('state') != 'published' or has_later_retirement:
            raise ReferenceRetiredError('reference revision is retired')
        payload = row.get('payload_json')
        if not isinstance(payload, Mapping) or canonical_payload_hash(payload) != str(
            row.get('content_sha256') or ''
        ).lower():
            raise ReferenceHashMismatchError(
                'reference payload hash does not match the stored payload'
            )
        # consumer_provider_id is intentionally an input to the adapter boundary:
        # provider compatibility is checked by the consumer-owned adapter before
        # calling resolve. The central store does not invent compatibility rules.
        if not str(consumer_provider_id or '').strip():
            raise ReferenceSourceMismatchError('consumer provider is required')
        return self._public(row)

    def _query(self, sql: str, params: tuple) -> list[dict]:
        connection = self._open()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(sql, params)
                return self._rows(cursor)
            finally:
                cursor.close()
        except CentralProjectReferenceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CentralProjectReferenceError(f'central reference read failed: {exc}') from exc
        finally:
            self._close(connection)

    @staticmethod
    def _rows(cursor: RowCursor) -> list[dict]:
        descriptions = getattr(cursor, 'description', None) or ()
        columns = tuple(getattr(item, 'name', item[0]) for item in descriptions)
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    @classmethod
    def _fetch_one(cls, cursor: RowCursor, sql: str, params: tuple,
                   columns: Optional[tuple[str, ...]] = None) -> Optional[dict]:
        cursor.execute(sql, params)
        rows = cls._fetch_cursor_rows(cursor, columns)
        return rows[0] if rows else None

    @classmethod
    def _fetch_exactly_one(cls, cursor: RowCursor, sql: str, params: tuple,
                           columns: Optional[tuple[str, ...]] = None) -> dict:
        """**반드시 한 행**인 질의 — 집계 전용.

        ⚠️ 이것은 ``Optional`` 을 벗기려는 우회로가 **아니다.** 위 ``_fetch_one`` 을
        쓰는 자리 중 ``source`` · ``target`` · ``row`` 는 조회가 빈손일 수 있어
        ``is None`` 가드를 갖는다. 반면 ``SELECT COALESCE(MAX(...), 0) + 1`` 처럼
        GROUP BY 가 없는 집계는 **대상 행이 0개여도 언제나 정확히 한 행**을 돌려준다.
        저자가 그 두 자리에만 가드를 두지 않은 이유가 그것이고, 이 헬퍼는 그
        사실을 산문이 아니라 **타입과 검사**로 적는다.

        ⚠️ 그리고 한 행이 아니면 조용히 넘어가지 않는다. 그런 결과가 왔다는 것은
        **질의가 우리가 믿는 것과 다르다**는 뜻이다. 그 자리에서 이름을 가진 오류로
        죽는 것이 옳다 — 호출부까지 ``None`` 을 흘려보내면 진단이
        ``'NoneType' object is not subscriptable`` 이 되고 **어느 질의였는지 남지 않는다.**
        """
        cursor.execute(sql, params)
        rows = cls._fetch_cursor_rows(cursor, columns)
        if len(rows) != 1:
            raise CentralProjectReferenceError(
                f'aggregate query must return exactly one row, got {len(rows)}: '
                f'{" ".join(sql.split())[:120]}'
            )
        return rows[0]

    @classmethod
    def _fetch_cursor_row(cls, cursor: RowCursor) -> Optional[dict]:
        rows = cls._fetch_cursor_rows(cursor)
        return rows[0] if rows else None

    @staticmethod
    def _fetch_cursor_rows(
        cursor: RowCursor, columns: Optional[tuple[str, ...]] = None,
    ) -> list[dict]:
        rows = list(cursor.fetchall())
        if not rows:
            return []
        descriptions = getattr(cursor, 'description', None) or ()
        names = tuple(getattr(item, 'name', item[0]) for item in descriptions)
        names = names or columns or _REFERENCE_COLUMNS
        return [dict(zip(names, row, strict=True)) for row in rows]

    @staticmethod
    def _public(row: Mapping) -> dict:
        result = dict(row)
        result['revision_id'] = result.pop('id', result.get('revision_id'))
        result['payload'] = result.pop('payload_json', result.get('payload'))
        return result

    def _open(self) -> RowConnection:
        try:
            return self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise CentralProjectReferenceError(f'central reference connection failed: {exc}') from exc

    @staticmethod
    def _resolve_provider_id(cursor: RowCursor, provider_id: str) -> Mapping:
        row = PostgresCentralProjectReferenceAdapter._fetch_one(
            cursor,
            'SELECT id, provider_id FROM providers '
            'WHERE provider_id = %s AND enabled = TRUE',
            (provider_id,),
            ('id', 'provider_id'),
        )
        if row is None:
            raise ReferenceNotFoundError('producer provider is not registered')
        return row

    @staticmethod
    # ⚠️ 여기는 ``RowCursor`` 가 아니라 ``DbCursor`` 다 — 이 함수는 행을 읽지
    #    않는다. 필요보다 넓은 타입을 적으면 「이 함수가 결과를 본다」는 거짓을 남긴다.
    def _serializable(cursor: DbCursor) -> None:
        try:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE', ())
        except Exception as exc:  # noqa: BLE001
            raise CentralProjectReferenceError(
                'central reference requires PostgreSQL SERIALIZABLE isolation'
            ) from exc

    @staticmethod
    def _lock_reference_identity(
        cursor: DbCursor, project_id: str, provider_id: str,
        reference_type: str, schema_version: str,
    ) -> None:
        cursor.execute(
            _REFERENCE_IDENTITY_LOCK_SQL,
            (project_id, provider_id, reference_type, schema_version),
        )

    @staticmethod
    def _rollback(connection: RowConnection) -> None:
        rollback = getattr(connection, 'rollback', None)
        if callable(rollback):
            try:
                rollback()
            except Exception:
                pass

    @staticmethod
    def _close(connection: RowConnection) -> None:
        close = getattr(connection, 'close', None)
        if callable(close):
            close()
