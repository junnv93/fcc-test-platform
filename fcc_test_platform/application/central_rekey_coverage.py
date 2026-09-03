"""Central PostgreSQL re-key coverage reader (ADR-0005 E4 evidence, 2026-06-05).

E4 (central expand+ingest) evidence 는 두 출처를 합친다: (1) ingest 결과
(`RekeyIngestAudit` — envelope mapping_checksum + conflict 0) + (2) **central
coverage 쿼리** (`measurement_attempts` 행 수 + `condition_hash_v2 IS NULL` 잔존
행 수). 본 모듈은 (2)의 **SELECT-only SSOT** 다 — 운영자/B-1 owner 가 expand
migration 적용 후 live central PG 에서 실행하는 권위 쿼리를 단일 곳에 둬,
운영 runbook 의 ad-hoc 하드코딩 SQL 을 제거한다.

설계 (`PostgresCentralRekeyIngestAdapter` 동형):

- **injected ``connection``** (DB-API ``DbConnection``). 구체 PostgreSQL 연결은
  composition root(ingest adapter 와 동일 factory)가 lazily 만든다 — 본 모듈은
  드라이버를 import 하지 않는다 (frozen-exe safe + platform 가드 통과).
- **SELECT COUNT(*) only**: ``measurement_attempts`` 를 읽기만 한다. write / DDL /
  hash 재계산 0 (central never-recompute 불변).
- 결과는 ``CentralCoverageCounts`` (non-negative scalar). 도메인 순수
  ``CentralCoverageSnapshot`` (rekey_cutover_evidence) 로 그대로 채워 cutover/gate
  evidence 조립에 흘려보낸다 (conflict_count 는 ingest 결과에서 옴 — 여기 미산출).

purity: stdlib + domain ports only. infrastructure / sqlite3 / hashlib / psycopg 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple

# 같은 central 대상 테이블 — ingest adapter 가 정의한 단일 SSOT 를 import (중복 정의 0).
from fcc_test_platform.application.central_rekey_ingest_adapter import MEASUREMENT_ATTEMPTS_TABLE
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'MEASUREMENT_ATTEMPTS_TABLE',
    'CENTRAL_TOTAL_ATTEMPTS_SQL',
    'CENTRAL_V2_NULL_COUNT_SQL',
    'COVERAGE_EVIDENCE_FIELDS',
    'CentralCoverageCounts',
    'collect_central_coverage',
    'coverage_counts_to_evidence',
]

#: central measurement_attempts 전체 행 수 (coverage 분모).
CENTRAL_TOTAL_ATTEMPTS_SQL = (
    f'SELECT COUNT(*) FROM "{MEASUREMENT_ATTEMPTS_TABLE}"'
)
#: condition_hash_v2 가 아직 NULL 인 행 수 (0 이어야 coverage 완료).
CENTRAL_V2_NULL_COUNT_SQL = (
    f'SELECT COUNT(*) FROM "{MEASUREMENT_ATTEMPTS_TABLE}" '
    'WHERE "condition_hash_v2" IS NULL'
)


@dataclass(frozen=True)
class CentralCoverageCounts:
    """central coverage 쿼리 결과 — non-negative scalar (불변).

    `total_attempts`: central `measurement_attempts` 행 수.
    `v2_null_count`: `condition_hash_v2 IS NULL` 잔존 행 수 (0 = coverage 완료).
    conflict_count 는 ingest 결과(`RekeyIngestAudit` / conflict pre-scan)에서 오므로
    coverage 쿼리 책임이 아니다 (여기 미포함).
    """

    total_attempts: int
    v2_null_count: int


#: coverage 쿼리가 채우는 E4 evidence 필드명 ↔ ``CentralCoverageCounts`` 속성 매핑 SSOT.
#: E4 evidence 의 나머지(envelope_mapping_checksum / central_conflict_count)는 coverage 가
#: 아니라 envelope / ingest 결과에서 온다 — 그 분담을 단일 곳에 둬 collect→assemble pipe 가
#: 같은 키를 쓰게 한다. `rekey_evidence_gate.E4_CENTRAL_INGEST_REQUIRED_FIELDS` 와의 정합은
#: tests/test_rekey_evidence_cli_e4_e5.py 의 drift 가드가 봉인(키 ⊆ E4 필드).
COVERAGE_EVIDENCE_FIELDS: Tuple[Tuple[str, str], ...] = (
    ('central_total_attempts', 'total_attempts'),
    ('central_v2_null_count', 'v2_null_count'),
)


def coverage_counts_to_evidence(counts: CentralCoverageCounts) -> Mapping[str, int]:
    """`CentralCoverageCounts` → E4 evidence coverage 필드 dict (키 하드코딩 0 — SSOT 파생).

    collect 명령이 산출하고 assemble 명령이 소비하는 무전사(transcription-free) 형식.
    """
    return {
        evidence_key: getattr(counts, attr_name)
        for evidence_key, attr_name in COVERAGE_EVIDENCE_FIELDS
    }


def collect_central_coverage(connection: DbConnection) -> CentralCoverageCounts:
    """central PG 연결에서 coverage 2-count 를 read-only 로 수집.

    expand migration 적용 후 호출해야 한다 (`condition_hash_v2` 컬럼 존재 전제 —
    미적용 시 DB 가 loud 하게 실패). SELECT 만 수행, write 0.
    """
    cursor = connection.cursor()
    try:
        total = _scalar(cursor, CENTRAL_TOTAL_ATTEMPTS_SQL)
        v2_null = _scalar(cursor, CENTRAL_V2_NULL_COUNT_SQL)
    finally:
        cursor.close()
    return CentralCoverageCounts(total_attempts=total, v2_null_count=v2_null)


def _scalar(cursor, statement: str) -> int:
    """단일 COUNT scalar 추출 (`PostgresCentralRekeyIngestAdapter._scalar` 동형)."""
    cursor.execute(statement, ())
    rows = list(cursor.fetchall())
    if not rows or rows[0] is None:
        return 0
    value = rows[0][0]
    return int(value) if value is not None else 0
