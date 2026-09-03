"""ADR-0005 E4 central ingest **execution evidence** (rekey-e4-e5-evidence-cli iter-3, 2026-06-05).

E4(central expand+ingest) evidence 의 권위는 운영자의 *선언* 이 아니라 **실제 adapter
실행 결과** 다. provider-produced mapping envelope 를
``PostgresCentralRekeyIngestAdapter.ingest_mapping_envelope`` 로 실행한
``RekeyIngestAudit`` 에서 E4 evidence 필드를 **파생** 한다 — 운영자가 "ingest 돌렸고
conflict 0 이었다" 고 손으로 적는 경로(이전 iter 의 ``--central-conflict-count`` typed
arg)를 제거한다 (Codex 반려 P0 closure).

E4 evidence 4 필드는 두 실행 출처의 **분할(partition)** 이다:

- **ingest 실행** (``INGEST_AUDIT_EVIDENCE_FIELDS``):
  ``envelope_mapping_checksum`` (audit 의 declared mapping_checksum verbatim) +
  ``central_conflict_count`` (conflict pre-scan 결과 — 성공 시 0, conflict abort 시
  충돌 pair 수). 운영자 선언 아님.
- **coverage SELECT** (``central_rekey_coverage.COVERAGE_EVIDENCE_FIELDS``):
  ``central_total_attempts`` + ``central_v2_null_count``.

두 출처 키의 합집합 == ``E4_CENTRAL_INGEST_REQUIRED_FIELDS`` (서로소 + 전수 — partition
drift 가드가 봉인). E4 CLI 의 ``assemble`` 이 두 산출 JSON 을 합쳐 E4 artifact 를 만든다.

conflict 정책: ``ingest_mapping_envelope`` 가 conflict 를 만나면 **어떤 write 도 남기지
않고** (``RekeyIngestConflictError``, write 0) 중단한다 — adapter 는 UPDATE 전 pre-scan
(cheap early abort)과 UPDATE 후 commit 전 post-write 재검증 양쪽으로 conflict 를 잡아(후자가
pre-scan 통과 후 동시 writer 의 TOCTOU 경합을 rollback) write 0 을 보존한다. 본 모듈은 그것을
잡아 ``central_conflict_count = 충돌 pair 수`` 인 **truthful failure evidence** 로
표현한다 — 숨기지 않고 gate 가 ``central_conflict_count > 0`` blocker 로 닫게 한다(연결/
쿼리 실패 ``RekeyIngestError`` 는 호출자에게 전파).

central never-recompute: envelope 는 declared 값 attribute 복사로 **verbatim** 로드한다
(hash/매핑 재계산·무결성 재검증 0 — 그건 provider-side 전용). frozen-exe safe: 드라이버
import 0 (connection_factory 주입). purity: stdlib + domain ports + 형제 application.platform 만.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Tuple

from fcc_test_platform.application.central_rekey_ingest_adapter import (
    PostgresCentralRekeyIngestAdapter,
)
from fcc_test_platform.domain.ports.output.central_audit_write_port import CentralAuditWritePort
from fcc_test_platform.domain.ports.output.central_rekey_ingest_port import (
    RekeyIngestAudit,
    RekeyIngestConflictError,
    RekeyMappingEnvelopeLike,
)
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'ENVELOPE_FIELD_NAMES',
    'INGEST_AUDIT_EVIDENCE_FIELDS',
    'IngestMappingEnvelope',
    'IngestExecutionResult',
    'load_mapping_envelope',
    'ingest_audit_to_evidence',
    'execute_central_ingest',
]


#: provider mapping envelope JSON 키 == ``RekeyMappingEnvelopeLike`` Protocol 속성명 SSOT
#: (필드명 하드코딩 0 — 구조 계약에서 파생). 짜깁기(다른 재키잉 실행) 차단은 상위
#: cross-stage checksum 가드(``rekey_evidence_gate``) 책임.
ENVELOPE_FIELD_NAMES: Tuple[str, ...] = tuple(RekeyMappingEnvelopeLike.__annotations__)

#: E4 evidence 중 **ingest 실행** 이 채우는 필드 (coverage 가 아닌 나머지). coverage 키
#: (``COVERAGE_EVIDENCE_FIELDS``)와 서로소 분할을 이뤄 E4 SSOT 를 빠짐없이 덮는다 — 그
#: 분할 항등은 tests 의 drift 가드가 봉인한다. 순서: (checksum, conflict).
INGEST_AUDIT_EVIDENCE_FIELDS: Tuple[str, ...] = (
    'envelope_mapping_checksum',
    'central_conflict_count',
)


@dataclass(frozen=True)
class IngestMappingEnvelope:
    """central-side verbatim envelope view (``RekeyMappingEnvelopeLike`` 구현).

    provider-side 구체 타입(``rekey_dry_run.RekeyMappingEnvelope`` — hash 계산 포함)을
    import 하지 않고 declared 값만 담는다 — central never-recompute 경계. Protocol 이
    형태 SSOT 이므로 central 측 별도 구체 타입은 SSOT 위반이 아니라 의도된 분리다.
    """

    provider: str
    pairs: Tuple[Tuple[str, str], ...]
    old_hash_checksum: str
    new_hash_checksum: str
    mapping_checksum: str
    record_count: int


def load_mapping_envelope(data: Mapping) -> IngestMappingEnvelope:
    """provider mapping envelope dict → verbatim envelope (declared 값 복사, 재계산 0).

    키는 ``ENVELOPE_FIELD_NAMES`` (Protocol SSOT). 누락/비-dict/형식 오류는
    ``ValueError``/``TypeError`` (CLI 가 exit 2 로 surface).
    """
    if not isinstance(data, Mapping):
        raise TypeError('mapping envelope 가 object(dict) 아님')
    missing = [key for key in ENVELOPE_FIELD_NAMES if key not in data]
    if missing:
        raise ValueError(f'mapping envelope 필드 누락: {missing}')
    raw_pairs = data['pairs']
    if not isinstance(raw_pairs, (list, tuple)):
        raise ValueError('mapping envelope pairs 가 배열(list) 아님')
    pairs = tuple(_coerce_hash_pair(pair, index) for index, pair in enumerate(raw_pairs))
    return IngestMappingEnvelope(
        provider=str(data['provider']),
        pairs=pairs,
        old_hash_checksum=str(data['old_hash_checksum']),
        new_hash_checksum=str(data['new_hash_checksum']),
        mapping_checksum=str(data['mapping_checksum']),
        record_count=int(data['record_count']),
    )


def _coerce_hash_pair(pair, index: int) -> Tuple[str, str]:
    """Validate one provider mapping pair before it can reach central write code."""
    if not isinstance(pair, (list, tuple)):
        raise ValueError(f'mapping envelope pairs[{index}] 가 2원소 배열(list/tuple) 아님')
    if len(pair) != 2:
        raise ValueError(f'mapping envelope pairs[{index}] 는 정확히 2원소여야 함')
    old_hash, new_hash = pair
    if not isinstance(old_hash, str) or not isinstance(new_hash, str):
        raise ValueError(f'mapping envelope pairs[{index}] old/new hash 는 문자열이어야 함')
    if not old_hash or not new_hash:
        raise ValueError(f'mapping envelope pairs[{index}] old/new hash 는 비어 있을 수 없음')
    return old_hash, new_hash


def ingest_audit_to_evidence(*, mapping_checksum: str, conflict_count: int) -> Mapping:
    """ingest 실행값 → E4 ingest-절반 evidence dict (키 SSOT = ``INGEST_AUDIT_EVIDENCE_FIELDS``).

    ``coverage_counts_to_evidence`` 동형 — 키를 SSOT 튜플에서 순서 파생(필드명 하드코딩 0).
    """
    checksum_field, conflict_field = INGEST_AUDIT_EVIDENCE_FIELDS
    return {checksum_field: mapping_checksum, conflict_field: conflict_count}


@dataclass(frozen=True)
class IngestExecutionResult:
    """central ingest 실행 결과 — audit 파생 evidence(분할의 ingest 절반) + 진단 카운트.

    ``evidence`` 는 ``INGEST_AUDIT_EVIDENCE_FIELDS`` 키만 담는다(coverage 와 합쳐 E4 완성).
    ``has_conflict`` True = conflict pre-scan abort(write 0) → truthful failure.
    """

    evidence: Mapping
    conflict_count: int
    updated_count: int
    skipped_count: int

    @property
    def has_conflict(self) -> bool:
        return self.conflict_count > 0


def execute_central_ingest(
    connection_factory: Callable[[], DbConnection],
    envelope: RekeyMappingEnvelopeLike,
    *,
    audit_writer: Optional[CentralAuditWritePort] = None,
    audit_meta: Optional[Mapping] = None,
) -> IngestExecutionResult:
    """envelope 를 central 에 verbatim ingest 실행하고 audit 파생 evidence 를 산출.

    성공: ``RekeyIngestAudit`` 에서 mapping_checksum(declared verbatim) + conflict 0 +
    updated/skipped 카운트를 얻는다. conflict: ``RekeyIngestConflictError`` 를 잡아
    write 0 을 보존하고 ``conflict_count = 충돌 pair 수`` 인 truthful failure 로 표현한다
    (숨기지 않음 — gate 가 닫는다). 연결/쿼리 실패(``RekeyIngestError``)는 전파한다.

    ``connection_factory`` 주입 — 프로덕션은 composition root 의 lazy psycopg factory,
    테스트는 SQLite/fake factory(드라이버 의존 0).
    """
    adapter = PostgresCentralRekeyIngestAdapter(
        connection_factory, audit_writer=audit_writer,
    )
    try:
        audit: RekeyIngestAudit = adapter.ingest_mapping_envelope(
            envelope, audit_meta=audit_meta,
        )
    except RekeyIngestConflictError as exc:
        conflict_count = len(exc.conflicts)
        return IngestExecutionResult(
            evidence=ingest_audit_to_evidence(
                mapping_checksum=envelope.mapping_checksum,
                conflict_count=conflict_count,
            ),
            conflict_count=conflict_count,
            updated_count=0,
            skipped_count=0,
        )
    return IngestExecutionResult(
        evidence=ingest_audit_to_evidence(
            mapping_checksum=audit.mapping_checksum, conflict_count=0,
        ),
        conflict_count=0,
        updated_count=audit.updated_count,
        skipped_count=audit.skipped_count,
    )
