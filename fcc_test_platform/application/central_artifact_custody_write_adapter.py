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

**노드가 보내는 ``provider_id`` 는 자연키이고, 이 표의 컬럼은 uuid 다 — 해소는 여기서
한다 (2026-09-03).** ``artifact_custody_snapshots.provider_id`` 는 ``providers.id`` 를
가리키는 **uuid** FK 이고, 인입 축이 주고받는 값은 ``providers.provider_id`` **자연키**
다(계약 SSOT · 중앙 env · 노드 런처가 그것으로 일치한다). 이 어댑터는 2026-09-02 에
그 자연키를 **verbatim** 으로 uuid 칸에 넣고 있었고, 그래서 그날 이후 보관 보고가
**전량 거절**됐다. 같은 순간 대조 실측(2026-09-03, 도는 중앙):

    fcc-unlicensed-conducted  →  503  invalid input syntax for type uuid, 행 델타 0
    70a985fa-…                →  200  accepted

⚠️ **env 를 UUID 로 되돌리는 것은 정공이 아니다.** 인입 축은 자연키가 맞고 형제들이
모두 해소를 갖고 있었다 — ``CentralBackendSyncAdapter`` 는 platform readiness 가 해소한
``provider_uuid`` 를 받고, ``PostgresCentralResultSelectionAdapter`` 는 스스로 해소하며,
``PostgresCentralReferenceWriteAdapter`` 는 INSERT 안에서 providers 를 조인한다.
**이 어댑터 하나만 그 해소를 건너뛰었다.**

⚠️ **해소를 INSERT 안으로 접지 않는다.** ``INSERT … SELECT p."id" … FROM providers``
로 접으면 한 줄이 줄지만, 그 순간 **빈 ``RETURNING`` 의 뜻이 둘이 된다** — "중앙이 더
새로운 관측을 갖고 있다(밀려남)" 와 "그런 provider 가 없다". 위 문단 전체가 존재하는
이유가 바로 그 반환값 하나로 저장 여부를 가르기 때문이므로, 접으면 **없는 provider 로
온 보고가 조용한 «밀려남»** 이 된다. 그래서 같은 트랜잭션 안에서 **먼저 해소하고**
loud-fail 한다.

설계(``PostgresCentralTestEquipmentListWriteAdapter`` 미러): 주입
``connection_factory`` / ``%s`` paramstyle / loud-fail / 단일 트랜잭션.
"""
from __future__ import annotations

import uuid
from typing import Callable, Mapping, Sequence

# 해소 SQL 은 **다시 적지 않고 빌려온다.** readiness 가 이 레인에서 «자연키 →
# providers.id» 를 소유하는 자리이고, 여기서 같은 SELECT 를 새로 쓰면 두 벌이 되어
# providers 의 열이 바뀔 때 한쪽만 따라간다.
from fcc_test_kernel.application.central_contract.central_sync_readiness import (
    PROVIDER_READINESS_SQL,
)
from fcc_test_platform.domain.ports.output.central_artifact_custody_port import (
    ArtifactCustodyProviderNotFoundError,
    CentralArtifactCustodyError,
)
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'SNAPSHOT_INSERT_COLUMNS',
    'FINDING_INSERT_COLUMNS',
    'UPSERT_SNAPSHOT_SQL',
    'DELETE_FINDINGS_SQL',
    'INSERT_FINDING_SQL',
    'RESOLVE_PROVIDER_UUID_SQL',
    'PostgresCentralArtifactCustodyWriteAdapter',
]


#: 자연키 → ``providers.id``. readiness 의 SSOT 를 그대로 쓴다(위 주석 참조).
RESOLVE_PROVIDER_UUID_SQL = PROVIDER_READINESS_SQL


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
            provider_uuid = self._resolve_provider_uuid(cursor, provider_id)
            for session in sessions:
                session_key = str(session.get('provider_session_id') or '')
                counts = session.get('counts') or {}
                cursor.execute(UPSERT_SNAPSHOT_SQL, (
                    provider_uuid,
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

    @staticmethod
    def _resolve_provider_uuid(cursor, provider_id: str) -> str:
        """자연키를 ``providers.id`` 로 바꾼다. **같은 트랜잭션 안에서** 한다.

        ``uuid.UUID`` 로 정규화하는 것은 모양 검사가 아니라 **경계 방어**다 —
        registry 행이 깨져 있으면 그 값은 여기서 멈춰야지, 나중에 FK 경계에서
        「중앙 장애」로 나타나면 안 된다(``CentralSyncProviderIdentity`` 가 같은
        이유로 같은 정규화를 한다).
        """
        key = str(provider_id or '').strip()
        if not key:
            raise ArtifactCustodyProviderNotFoundError(
                'provider_id is required on an artifact custody report'
            )
        cursor.execute(RESOLVE_PROVIDER_UUID_SQL, (key,))
        row = cursor.fetchone()
        if not row:
            raise ArtifactCustodyProviderNotFoundError(
                f'unknown provider_id {key!r}; providers are operator-registered '
                'reference data, never ingested'
            )
        try:
            return str(uuid.UUID(str(row[0])))
        except (TypeError, ValueError) as exc:
            raise CentralArtifactCustodyError(
                f'central providers registry returned a non-uuid id for '
                f'{key!r} — the custody FK cannot be satisfied'
            ) from exc

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
