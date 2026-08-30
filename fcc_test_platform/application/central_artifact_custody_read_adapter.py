"""Central PostgreSQL 보관 스냅샷 read 어댑터 (plot-dual-custody ①, 2026-08-09).

**조인이 이 모듈의 설계 결정이다.** 스냅샷은 ``session_id`` FK 를 들지 않고
``test_sessions`` 와 **같은 자연키**(provider_id, chamber_id, provider_session_id)를
든다. 프로젝트 귀속은 읽는 순간 그 키로 조인해서 정해진다.

왜 FK 가 아닌가 — 두 가지가 이 선택을 강제한다:

1. **스냅샷이 세션 부모행보다 먼저 도착할 수 있다.** FK 면 그때 거절되고, 거절된
   보관 판정은 다시 만들어지지 않는다(관측은 시점의 사실이다).
2. **``project_id`` 는 나중에 채워진다.** 중앙은 세션의 ``model_name`` 으로
   프로젝트를 해소하는데 그 등록이 측정보다 늦을 수 있다. 조인이면 등록되는 순간
   과거 스냅샷들이 **백필 없이** 그 프로젝트에 나타난다. FK 였다면 스냅샷마다
   재작성이 필요했을 것이고, 그 재작성은 아무도 트리거하지 않는다.

``LEFT JOIN`` 이 아니라 ``JOIN`` 인 이유: 이 조회는 "이 프로젝트의 보관 현황"이다.
귀속되지 않은 스냅샷은 여기 들어오면 안 되고, 대신 ``count_unresolved_snapshots``
가 **개수로** 보고한다 — 조용히 빼면 "이상 없음"과 "볼 수 없음"이 같아 보인다.

설계(``PostgresCentralReportReadAdapter`` 미러): 주입 ``connection_factory`` /
``%s`` paramstyle / loud-fail / 읽기 전용.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from domain.ports.output.central_artifact_custody_port import (
    ArtifactCustodyNotFoundError,
    CentralArtifactCustodyError,
)
from domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'SNAPSHOT_SELECT_COLUMNS',
    'LIST_PROJECT_SNAPSHOTS_SQL',
    'COUNT_UNRESOLVED_SQL',
    'COUNT_SESSIONS_WITHOUT_SNAPSHOT_SQL',
    'COUNT_MISSING_SNAPSHOT_SESSIONS_SQL',
    'GET_SNAPSHOT_SQL',
    'LIST_FINDINGS_SQL',
    'PostgresCentralArtifactCustodyReadAdapter',
]


SNAPSHOT_SELECT_COLUMNS: tuple[str, ...] = (
    'snapshot_id',
    'provider_session_id',
    'chamber_id',
    'session_label',
    'status',
    'verified_count',
    'missing_count',
    'diverged_count',
    'unknown_count',
    'roots_json',
    'observed_at',
    'reported_at',
)

_SNAPSHOT_PROJECTION = (
    's."id" AS "snapshot_id", s."provider_session_id", s."chamber_id", '
    's."session_label", s."status", s."verified_count", s."missing_count", '
    's."diverged_count", s."unknown_count", s."roots_json", '
    's."observed_at", s."reported_at"'
)

#: 자연키 3열 전부로 조인한다 — chamber_id 를 빼면 두 챔버가 같은 로컬 세션 번호를
#: 쓸 때 한 챔버의 보관 판정이 다른 챔버의 세션에 붙는다(로컬 id 는 노드마다 1부터다).
_NATURAL_KEY_JOIN = (
    'JOIN "test_sessions" t ON t."provider_id" = s."provider_id" '
    'AND t."chamber_id" = s."chamber_id" '
    'AND t."provider_session_id" = s."provider_session_id"'
)

LIST_PROJECT_SNAPSHOTS_SQL = (
    f'SELECT {_SNAPSHOT_PROJECTION} FROM "artifact_custody_snapshots" s '
    f'{_NATURAL_KEY_JOIN} '
    'WHERE t."project_id" = %s '
    # 차단 상태를 먼저 — 화면 상단이 조치가 필요한 세션이다. 그다음 최신 관측 순.
    "ORDER BY (s.\"status\" IN ('missing', 'diverged')) DESC, s.\"observed_at\" DESC"
)

#: 어느 프로젝트에도 귀속되지 않은 스냅샷 — 세션 행이 없거나 project_id 가 NULL.
#: 두 원인을 합치는 이유: 시험원이 할 일이 같다(프로젝트에 모델을 등록한다).
COUNT_UNRESOLVED_SQL = (
    'SELECT COUNT(*) FROM "artifact_custody_snapshots" s '
    'LEFT JOIN "test_sessions" t ON t."provider_id" = s."provider_id" '
    'AND t."chamber_id" = s."chamber_id" '
    'AND t."provider_session_id" = s."provider_session_id" '
    'WHERE (t."id" IS NULL OR t."project_id" IS NULL) '
    # ⚠️ provider 범위가 없으면 이것은 **전역** 개수이고, 프로젝트 화면이 그것을
    # 프로젝트 숫자로 렌더한다(독립 리뷰 2026-08-09). 이 프로젝트에 세션을 보낸
    # provider 들만이 이 프로젝트에 귀속될 수 있는 스냅샷을 만든다.
    'AND s."provider_id" IN ('
    'SELECT DISTINCT "provider_id" FROM "test_sessions" WHERE "project_id" = %s)'
)

# Project sessions are the denominator for custody coverage. This anti-join
# counts sessions with no snapshot without manufacturing a finding row.
COUNT_SESSIONS_WITHOUT_SNAPSHOT_SQL = (
    'SELECT COUNT(*) FROM "test_sessions" t '
    'WHERE t."project_id" = %s '
    'AND NOT EXISTS ('
    'SELECT 1 FROM "artifact_custody_snapshots" s '
    'WHERE s."provider_id" = t."provider_id" '
    'AND s."chamber_id" = t."chamber_id" '
    'AND s."provider_session_id" = t."provider_session_id"'
    ')'
)
# Descriptive alias retained for callers/tests that name the metric by its
# result rather than by the anti-join predicate.
COUNT_MISSING_SNAPSHOT_SESSIONS_SQL = COUNT_SESSIONS_WITHOUT_SNAPSHOT_SQL

#: 상세 조회는 **프로젝트 귀속을 조건에 넣는다.** 넣지 않으면 한 프로젝트의 뷰어가
#: snapshot_id 하나로 남의 프로젝트 보관 상세를 읽는다.
GET_SNAPSHOT_SQL = (
    f'SELECT {_SNAPSHOT_PROJECTION} FROM "artifact_custody_snapshots" s '
    f'{_NATURAL_KEY_JOIN} '
    'WHERE t."project_id" = %s AND s."id" = %s'
)

LIST_FINDINGS_SQL = (
    'SELECT "relative_path", "status", "artifact_type", "expected_sha256", '
    '"observed_sha256", "reason" FROM "artifact_custody_findings" '
    'WHERE "snapshot_id" = %s ORDER BY "relative_path"'
)

_FINDING_COLUMNS = (
    'relative_path', 'status', 'artifact_type',
    'expected_sha256', 'observed_sha256', 'reason',
)


class PostgresCentralArtifactCustodyReadAdapter:
    """``CentralArtifactCustodyReadPort`` — 프로젝트 축 조인 읽기."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def list_project_snapshots(self, project_id: str) -> list[dict]:
        def _query(cursor) -> list[dict]:
            cursor.execute(LIST_PROJECT_SNAPSHOTS_SQL, (project_id,))
            return [_snapshot_row(row) for row in cursor.fetchall()]

        return self._read(_query)

    def count_unresolved_snapshots(self, project_id: str) -> int:
        def _query(cursor) -> int:
            cursor.execute(COUNT_UNRESOLVED_SQL, (project_id,))
            rows = list(cursor.fetchall())
            return int(rows[0][0]) if rows else 0

        return self._read(_query)

    def count_sessions_without_snapshot(self, project_id: str) -> int:
        def _query(cursor) -> int:
            cursor.execute(COUNT_SESSIONS_WITHOUT_SNAPSHOT_SQL, (project_id,))
            rows = list(cursor.fetchall())
            return int(rows[0][0]) if rows else 0

        return self._read(_query)

    def get_snapshot(self, project_id: str, snapshot_id: str) -> dict:
        def _query(cursor) -> dict:
            cursor.execute(GET_SNAPSHOT_SQL, (project_id, snapshot_id))
            rows = list(cursor.fetchall())
            if not rows:
                raise ArtifactCustodyNotFoundError(
                    f'custody snapshot {snapshot_id!r} not found in project {project_id!r}'
                )
            snapshot = _snapshot_row(rows[0])
            cursor.execute(LIST_FINDINGS_SQL, (snapshot['snapshot_id'],))
            snapshot['findings'] = [
                {key: _text(value) for key, value in zip(_FINDING_COLUMNS, row)}
                for row in cursor.fetchall()
            ]
            return snapshot

        return self._read(_query)

    def _read(self, body: Callable[[object], object]):
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise CentralArtifactCustodyError(
                f'central artifact custody read connection failed: {exc}'
            ) from exc
        try:
            cursor = connection.cursor()
            try:
                return body(cursor)
            finally:
                cursor.close()
        except CentralArtifactCustodyError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CentralArtifactCustodyError(
                f'central artifact custody read failed: {exc}'
            ) from exc
        finally:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass


def _snapshot_row(row) -> dict:
    record = dict(zip(SNAPSHOT_SELECT_COLUMNS, row))
    record['roots'] = _decode_roots(record.pop('roots_json', None))
    for key in ('snapshot_id', 'provider_session_id', 'chamber_id',
                'session_label', 'status', 'observed_at', 'reported_at'):
        record[key] = _text(record.get(key))
    for key in ('verified_count', 'missing_count', 'diverged_count', 'unknown_count'):
        record[key] = int(record.get(key) or 0)
    return record


def _decode_roots(raw) -> list:
    """``roots_json`` → 목록. 깨진 JSON 은 **빈 목록**이지 예외가 아니다.

    이 필드는 진단 보조(어디를 봤는가)이고 판정에 쓰이지 않는다. 노드가 이상한 값을
    보냈다고 프로젝트 전체 보관 조회가 500 이 되면, 관측 가능성을 높이려고 넣은 필드가
    관측 자체를 막는다.
    """
    if isinstance(raw, list):
        return [str(item) for item in raw]
    text = str(raw or '').strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _text(value) -> Optional[str]:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)
