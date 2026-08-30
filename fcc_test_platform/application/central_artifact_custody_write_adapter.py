"""Central PostgreSQL 보관 스냅샷 write 어댑터 (plot-dual-custody ①, 2026-08-09).

``PostgresCentralArtifactCustodyWriteAdapter`` 가 ``CentralArtifactCustodyWritePort``
를 구현한다.

**이 모듈에서 가장 중요한 사실 — latest-wins 는 ``WHERE`` 절에 있다.**

``ON CONFLICT … DO UPDATE`` 만 쓰면 **마지막에 도착한 것**이 이긴다. 그런데 이 축의
전송은 재시도되고(중앙 불통 · 노드 재부팅), 재시도는 순서를 뒤집는다. 그러면 3일 전
관측이 오늘 관측을 덮어써서 **화면이 과거로 되돌아가고**, 시험원은 이미 옮긴 파일을
다시 찾으러 간다. 그래서 ``DO UPDATE`` 에 조건을 건다::

    WHERE EXCLUDED."observed_at" > "artifact_custody_snapshots"."observed_at"

같은 이유로 ``RETURNING`` 이 필수다 — 조건부 DO UPDATE 는 조건이 거짓이면 **아무 행도
반환하지 않으므로**, 반환 여부가 곧 "저장됐는가"의 답이다. 서비스는 그 답으로
``accepted`` / ``superseded`` 를 가른다. 이것을 읽지 않으면 밀려남이 조용한 성공이 된다.

**findings 는 스냅샷이 실제로 갱신됐을 때만 교체한다.** 갱신되지 않은(밀려난) 스냅샷의
findings 를 지우면 중앙이 **더 새로운 판정의 상세를 잃는다** — 카운터는 최신인데 목록은
비어 있는 상태가 되고, 화면은 "문제가 몇 건 있는데 어느 파일인지는 모른다"고 말한다.

**교체는 전량이다(부분 병합 아님).** 시험원이 파일을 옮기면 그 항목은 목록에서 사라져야
하는데, 부분 병합이면 이미 해결된 항목이 남아 **없는 일을 하게 만든다.**

설계(``PostgresCentralTestEquipmentListWriteAdapter`` 미러): 주입
``connection_factory`` / ``%s`` paramstyle / loud-fail / 단일 트랜잭션.
"""
from __future__ import annotations

from typing import Callable, Mapping, Sequence

from domain.ports.output.central_artifact_custody_port import (
    CentralArtifactCustodyError,
)
from domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'SNAPSHOT_INSERT_COLUMNS',
    'FINDING_INSERT_COLUMNS',
    'UPSERT_SNAPSHOT_SQL',
    'DELETE_FINDINGS_SQL',
    'INSERT_FINDING_SQL',
    'PostgresCentralArtifactCustodyWriteAdapter',
]


SNAPSHOT_INSERT_COLUMNS: tuple[str, ...] = (
    'provider_id',
    'chamber_id',
    'provider_session_id',
    'status',
    'verified_count',
    'missing_count',
    'diverged_count',
    'unknown_count',
    'roots_json',
    'session_label',
    'observed_at',
)

FINDING_INSERT_COLUMNS: tuple[str, ...] = (
    'snapshot_id',
    'relative_path',
    'status',
    'artifact_type',
    'expected_sha256',
    'observed_sha256',
    'reason',
)

#: 갱신 대상 열 — 자연키 3열과 ``id``/``reported_at`` 을 뺀 나머지.
#: **파생이다.** 손으로 적으면 열이 늘 때 하나가 조용히 갱신되지 않는다.
_SNAPSHOT_UPDATE_COLUMNS: tuple[str, ...] = tuple(
    column for column in SNAPSHOT_INSERT_COLUMNS
    if column not in ('provider_id', 'chamber_id', 'provider_session_id')
)


def _quoted(columns: Sequence[str]) -> str:
    return ', '.join(f'"{column}"' for column in columns)


#: latest-wins upsert. 조건이 거짓이면 RETURNING 이 **빈 결과**이고, 그것이 곧
#: "중앙이 이미 더 새로운 관측을 갖고 있다"는 답이다.
UPSERT_SNAPSHOT_SQL = (
    f'INSERT INTO "artifact_custody_snapshots" ({_quoted(SNAPSHOT_INSERT_COLUMNS)}) '
    f'VALUES ({", ".join(["%s"] * len(SNAPSHOT_INSERT_COLUMNS))}) '
    'ON CONFLICT ("provider_id", "chamber_id", "provider_session_id") DO UPDATE SET '
    + ', '.join(f'"{c}" = EXCLUDED."{c}"' for c in _SNAPSHOT_UPDATE_COLUMNS)
    + ', "reported_at" = now() '
    'WHERE EXCLUDED."observed_at" > "artifact_custody_snapshots"."observed_at" '
    'RETURNING "id"'
)

DELETE_FINDINGS_SQL = 'DELETE FROM "artifact_custody_findings" WHERE "snapshot_id" = %s'

INSERT_FINDING_SQL = (
    f'INSERT INTO "artifact_custody_findings" ({_quoted(FINDING_INSERT_COLUMNS)}) '
    f'VALUES ({", ".join(["%s"] * len(FINDING_INSERT_COLUMNS))})'
)


class PostgresCentralArtifactCustodyWriteAdapter:
    """``CentralArtifactCustodyWritePort`` — latest-wins 스냅샷 수신."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def store_report(
        self,
        *,
        provider_id: str,
        chamber_id: str,
        sessions: Sequence[Mapping],
    ) -> dict:
        accepted: list[str] = []
        superseded: list[str] = []

        def _txn(cursor) -> dict:
            for session in sessions:
                session_key = str(session.get('provider_session_id') or '')
                counts = session.get('counts') or {}
                cursor.execute(UPSERT_SNAPSHOT_SQL, (
                    provider_id,
                    chamber_id,
                    session_key,
                    session.get('status'),
                    int(counts.get('verified', 0) or 0),
                    int(counts.get('missing', 0) or 0),
                    int(counts.get('diverged', 0) or 0),
                    int(counts.get('unknown', 0) or 0),
                    session.get('roots_json'),
                    session.get('session_label'),
                    session.get('observed_at'),
                ))
                rows = list(cursor.fetchall())
                if not rows:
                    # 중앙이 더 새로운 관측을 갖고 있다. findings 를 **건드리지
                    # 않는다** — 지우면 최신 판정의 상세를 잃는다.
                    superseded.append(session_key)
                    continue
                snapshot_id = rows[0][0]
                cursor.execute(DELETE_FINDINGS_SQL, (snapshot_id,))
                for finding in (session.get('findings') or []):
                    cursor.execute(INSERT_FINDING_SQL, (
                        snapshot_id,
                        finding.get('relative_path'),
                        finding.get('status'),
                        finding.get('artifact_type'),
                        finding.get('expected_sha256'),
                        finding.get('observed_sha256'),
                        finding.get('reason'),
                    ))
                accepted.append(session_key)
            return {'accepted': accepted, 'superseded': superseded}

        return self._in_transaction(_txn)

    def _in_transaction(self, body: Callable[[object], object]):
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001
            raise CentralArtifactCustodyError(
                f'central artifact custody write connection failed: {exc}'
            ) from exc
        try:
            cursor = connection.cursor()
            try:
                result = body(cursor)
            finally:
                cursor.close()
            connection.commit()
            return result
        except CentralArtifactCustodyError:
            _rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            _rollback(connection)
            raise CentralArtifactCustodyError(
                f'central artifact custody write failed: {exc}'
            ) from exc
        finally:
            _close(connection)


def _rollback(connection) -> None:
    try:
        connection.rollback()
    except Exception:  # noqa: BLE001 — 롤백 실패가 원인 예외를 가리지 않게 한다
        pass


def _close(connection) -> None:
    try:
        connection.close()
    except Exception:  # noqa: BLE001
        pass
