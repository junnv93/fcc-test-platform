"""Output port — central re-key mapping ingestion (ADR-0005 B-1, 2026-06-01).

local (provider) 이 산출한 `(old_hash → new_hash)` 매핑을 central PostgreSQL 의
`measurement_attempts.condition_hash_v2` 병렬 컬럼에 **verbatim 적용**하는 계약.
central 은 hash 도 매핑도 만들거나 검증(재계산)하지 않는다 — local 산출물(envelope)을
받아 그대로 적용만 한다 (ADR-0005 (c) never-recompute 불변). 구현 어댑터는
``application/platform/central_rekey_ingest_adapter.PostgresCentralRekeyIngestAdapter``.

설계 (claim/audit write port 동형):
- envelope 의 `(old, new)` pair 를 `UPDATE ... SET condition_hash_v2 = :new
  WHERE condition_hash = :old AND condition_hash_v2 IS NULL` 로 적용 (idempotent —
  이미 채운 행 skip, live `condition_hash` 불가침).
- **conflict pre-scan + post-write 재검증**: 기존 `condition_hash_v2` 가 expected new 와
  다르면 어떤 write 도 하지 않고 `RekeyIngestConflictError` (write 0 abort, local backfill 과
  동형 정책). pre-scan 은 UPDATE 전 cheap early abort 이고, 같은 스캔을 UPDATE 후 commit 전에
  **재실행** 해 pre-scan 통과 후 동시 writer 가 DIFFERENT v2 를 채운 TOCTOU 경합도 잡는다
  (READ COMMITTED 가 타 txn committed write 를 봄 → rollback 으로 write 0 보존). commit
  이후의 conflict 까지 막는 full serialization 은 connection 의 isolation 책임(adapter 미관여).
- envelope 의 declared checksum 은 **문자열 그대로** propagation audit 에 보존 (central 은
  checksum 을 재계산해 비교하지 않는다 — 문자열 동등성만이 local↔central 연결 SSOT).

dependency-free: stdlib typing only (no infrastructure / pyvisa / openpyxl / pandas /
PySide6 / fastapi / sqlalchemy / psycopg imports). hash 계산 함수 import 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Protocol, Tuple, runtime_checkable


__all__ = [
    'RekeyMappingEnvelopeLike',
    'RekeyIngestError',
    'RekeyIngestConflict',
    'RekeyIngestConflictError',
    'RekeyIngestAudit',
    'CentralRekeyIngestPort',
]


@runtime_checkable
class RekeyMappingEnvelopeLike(Protocol):
    """central 이 소비하는 envelope 의 구조적 형태 (domain.services 역의존 0).

    구체 타입은 `domain/services/rekey_dry_run.RekeyMappingEnvelope` 지만, central
    ingestion 은 그 모듈(hash 계산 포함)을 import 하지 않고 attribute 만 읽는다.
    """

    provider: str
    pairs: Tuple[Tuple[str, str], ...]
    old_hash_checksum: str
    new_hash_checksum: str
    mapping_checksum: str
    record_count: int


class RekeyIngestError(RuntimeError):
    """central re-key ingestion 이 인프라 레벨에서 실패 — loud (silent no-op 금지).

    조용히 삼킨 ingestion 실패는 일부 행만 re-key 된 부분 전파를 남겨 cutover 후 조회
    불일치를 유발한다. 항상 loud 로 올려 운영자가 재시도/조사하게 한다.
    """


@dataclass(frozen=True)
class RekeyIngestConflict:
    """이미 채워진 `condition_hash_v2` 가 envelope 의 expected new 와 다름 — 덮어쓰기 금지 신호."""

    old_hash: str
    expected_new_hash: str
    existing_v2_count: int


class RekeyIngestConflictError(RuntimeError):
    """기존 condition_hash_v2 가 expected new 와 충돌 — write 0 (덮어쓰기 금지, abort)."""

    def __init__(self, conflicts: Tuple[RekeyIngestConflict, ...]):
        self.conflicts = conflicts
        super().__init__(
            f'central re-key ingest conflict {len(conflicts)} 건 — 기존 condition_hash_v2 가 '
            'envelope expected new 와 다릅니다. 덮어쓰지 않고 중단(write 0). 원인 조사 필요.',
        )


@dataclass(frozen=True)
class RekeyIngestAudit:
    """central ingestion 결과 — same-transaction 집계 evidence (불변).

    `*_checksum` 은 envelope 의 declared 값을 **문자열 그대로** 옮긴 것(재계산 0). local
    `RekeyBackfillAudit` / envelope 의 checksum 과 문자열 동등성으로 연결된다.
    """

    record_count: int            # envelope.record_count (declared)
    updated_count: int           # 이번 실행에서 새로 채운 행 수
    skipped_count: int           # 이미 expected new 로 채워짐 (idempotent rerun)
    old_hash_checksum: str       # declared (string copy)
    new_hash_checksum: str
    mapping_checksum: str


@runtime_checkable
class CentralRekeyIngestPort(Protocol):
    """central `measurement_attempts.condition_hash_v2` 에 envelope 를 verbatim 적용."""

    def ingest_mapping_envelope(
        self,
        envelope: RekeyMappingEnvelopeLike,
        *,
        audit_meta: Optional[Mapping] = None,
    ) -> RekeyIngestAudit:
        """envelope.pairs 를 단일 트랜잭션에서 verbatim 적용한다.

        (1) 모든 pair 에 대해 conflict pre-scan — 기존 `condition_hash_v2` 가 expected new 와
        다른 행이 하나라도 있으면 어떤 UPDATE 도 commit 하지 않고 `RekeyIngestConflictError`
        (write 0). (2) `condition_hash_v2 IS NULL` 행만 expected new 로 UPDATE (idempotent,
        실제 적용 행 수는 driver rowcount). live `condition_hash` 미변경. (3) UPDATE 후 commit
        전 같은 conflict 스캔을 **재실행** — pre-scan 통과 후 동시 writer 가 채운 DIFFERENT v2
        를 잡아 `RekeyIngestConflictError` 로 rollback(write 0). (4) audit writer 가 wired 이고
        `audit_meta` 가 주어지면, envelope 의 declared checksum 을 문자열 그대로 담은 propagation
        audit 행을 같은 트랜잭션에 INSERT (audit 실패 시 primary rollback). 연결/쿼리 실패는
        `RekeyIngestError`.
        """
        ...
