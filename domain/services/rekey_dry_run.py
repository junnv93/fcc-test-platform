"""ADR-0005 re-keying dry-run / checksum tool (B-3, 2026-05-31).

**read-only, pure domain, DB 비접근**. ADR-0005 re-keying migration (`docs/architecture/
adr-0005-rekeying-migration-plan.md` §4 preflight) 의 dry-run 단계를 계약화한다 — old
(row_order-baked) → new (value-based) condition_hash 매핑 + exact-duplicate collision
집계 + row count / old·new hash set checksum 리포트.

**본 sprint 범위 (Codex)**: contract + pure report builder + in-memory/fake 검증. 실제
local SQLite / central PG **연결·실행은 하지 않는다** (Phase D.1+ 의 caller 가 read-only
로 행을 읽어 본 builder 에 주입). DB 변경 0, hash 전환 0, central recompute 0.

**입력**: `RekeyConditionRecord` (sheet_name / row_order / test_params) 시퀀스 — caller
가 이미 읽어온 read-only 스냅샷. **출력**: `RekeyDryRunReport` (불변 값 객체).

**collision policy**: 본 도구는 read-only 라 reject 하지 않고 **merge group 을 surface**
만 한다 (≥2 distinct old_hash → 1 new_hash = re-key 시 consolidation). 운영자/migration
이 ADR-0005 `DuplicateConditionPolicy.REJECT` 로 검토. value-based new_hash 가 같으면
condition values 가 동일하다는 뜻 (exact-duplicate condition = 데이터 오류 후보).

purity: domain.services.measurement_history (hash SSOT) + stdlib only.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence, Tuple

from domain.services.measurement_history import (
    ConditionFieldSet,
    compute_live_hash_for_field_set,
    compute_stable_hash_for_field_set,
)


@dataclass(frozen=True)
class RekeyConditionRecord:
    """re-key dry-run 입력 — 한 persisted condition row (read-only 스냅샷, DB 비접근).

    caller (Phase D.1) 가 local SQLite / central PG 에서 read-only 로 읽어 주입한다.
    `stored_condition_hash` 는 **DB 에 실제 저장된** old hash (P1: re-key preflight 의
    핵심 — 저장값을 live formula 로 재계산한 값과 대조해 drift/버그/부분 sync 를 잡는다).
    `test_params` 는 `Col.HISTORY_CONDITION` 값을 담은 매핑 (recomputed old + new 계산용).
    `parse_error` 가 set 되면 (예: malformed condition_json) 본 record 는 hash 계산에서
    제외되고 report 의 `parse_errors` 채널로 분리된다 (빈 params 로 silent 흡수 금지 —
    Codex, 2026-05-31). caller (reader) 가 파싱 실패 사유를 담는다.
    """

    sheet_name: str
    row_order: int
    stored_condition_hash: str
    test_params: Mapping = field(default_factory=dict)
    parse_error: Optional[str] = None


@dataclass(frozen=True)
class RekeyParseError:
    """condition_json 등 입력 파싱 실패 — hash drift 와 **별도 채널** 로 surface.

    parse 실패는 test_params 를 복원할 수 없어 old/new hash 재계산이 무의미하므로
    `hash_mismatches` (drift) 와 섞지 않는다. 운영자 dry-run 리포트에서 원인 구분
    (드리프트 조사 vs 파싱/데이터 오류 조사) 을 명확히 한다.
    """

    sheet_name: str
    row_order: int
    stored_hash: str
    detail: str


@dataclass(frozen=True)
class RekeyHashMismatch:
    """stored old hash ≠ live formula 로 재계산한 old hash — preflight FAIL 후보.

    과거 버그 / 수동 수정 / 부분 sync / central·local drift 로 저장 hash 가 현 코드
    공식과 어긋난 경우. dry-run 은 reject/raise 하지 않고 report 에 surface 만 한다
    (운영자/migration 이 검토). mapping/merge group 은 **stored** hash 기준이므로
    mismatch record 도 backfill 매핑에 포함되되, 본 목록으로 별도 가시화된다.
    """

    sheet_name: str
    row_order: int
    stored_hash: str
    recomputed_old_hash: str


@dataclass(frozen=True)
class RekeyMergeGroup:
    """new_hash 하나에 ≥2 distinct old_hash 가 매핑 = re-key 시 consolidation 그룹.

    value-based new_hash 가 같으면 condition values 동일 → exact-duplicate condition
    후보 (운영자 검토 대상; ADR-0005 REJECT 정책은 edit/migration 단계에서 적용).
    """

    new_hash: str
    old_hashes: tuple[str, ...]


@dataclass(frozen=True)
class RekeyDryRunReport:
    """re-key dry-run 결과 — 불변. migration 실행 없이 안전성 근거를 산출.

    checksum/mapping/merge 는 **stored** old hash 기준 (실 backfill 에 사용 가능).
    `recomputed_old_hash_checksum` 은 live formula 재계산 evidence (stored 와 비교용).
    `hash_mismatches` 는 stored ≠ recomputed (drift) 를 surface — preflight FAIL 후보.
    """

    total_records: int
    distinct_old_hashes: int                   # distinct STORED old hashes
    distinct_new_hashes: int
    old_hash_checksum: str                     # STORED old hash set
    recomputed_old_hash_checksum: str          # live formula 재계산 set (validation)
    new_hash_checksum: str
    mapping_checksum: str                      # (old\tnew) pair 결합 checksum (association 봉인)
    mapping: tuple[tuple[str, str], ...]       # STORED old → new (backfill), distinct, sorted
    merge_groups: tuple[RekeyMergeGroup, ...]  # STORED old 기준 ≥2→1 (sorted by new_hash)
    hash_mismatches: tuple[RekeyHashMismatch, ...]  # stored ≠ recomputed (sorted)
    parse_errors: tuple[RekeyParseError, ...] = ()  # 입력 파싱 실패 (drift 와 별도 채널)


def is_backfill_precondition_met(report: RekeyDryRunReport) -> bool:
    """backfill 착수 precondition SSOT (ADR-0005 plan §5.0).

    `hash_mismatches == 0 AND parse_errors == 0` 이어야 backfill 가능 — 저장 hash 가
    live formula 와 모두 일치하고(drift 0) 파싱 실패가 없을(데이터 신뢰) 때만. merge_groups
    (exact-duplicate 후보) 는 운영자 검토 사안이라 precondition 에 포함하지 않는다
    (REJECT 정책은 edit/migration orchestration 단계).
    """
    return not report.hash_mismatches and not report.parse_errors


def _checksum(hashes: Sequence[str]) -> str:
    """hash **집합** 의 결정적 checksum (정렬된 distinct hash 의 sha256)."""
    canonical = '\n'.join(sorted(set(hashes)))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _mapping_checksum(pairs: Sequence[Tuple[str, str]]) -> str:
    """(old→new) **pair** checksum — association 봉인 (P1, Codex 2026-06-01).

    old set / new set 따로 checksum 하면 pair swap (`old1→new2, old2→new1`) 을 못 잡는다.
    `"old\\tnew"` line 을 정렬 후 sha256 — pair 결합이 바뀌면 checksum 이 달라진다.
    """
    lines = sorted(f'{old}\t{new}' for old, new in pairs)
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class RekeyMappingEnvelope:
    """local→central verbatim re-key mapping artifact (ADR-0005 central propagation).

    local (provider) 이 dry-run report 에서 산출한 **(old_hash → new_hash)** 매핑을
    central 이 **verbatim 적용** (condition_hash 재계산 0 — central never-recompute
    불변). `*_checksum` 은 transport 무결성용 — **provider-side** 에서
    `verify_mapping_envelope_integrity` 로 검증 후 송신하고, central 은 declared
    checksum 을 **문자열로 저장/비교**만 한다 (hashlib 재계산 금지 가드 정합).
    """

    provider: str                        # field_set.name (provenance)
    pairs: tuple[tuple[str, str], ...]    # (old_hash, new_hash) — distinct old, sorted
    old_hash_checksum: str
    new_hash_checksum: str
    mapping_checksum: str                 # (old\tnew) pair checksum — association 봉인
    record_count: int


class RekeyMappingEnvelopePreconditionError(RuntimeError):
    """dirty report (hash_mismatches 또는 parse_errors) 로 central envelope 생성 시도.

    central 전파 artifact 생성은 권장이 아니라 **gate** — drift/parse 잔존 매핑을
    central 으로 보내면 안 된다 (Codex P1, 2026-06-01).
    """

    def __init__(self, report: RekeyDryRunReport):
        self.report = report
        super().__init__(
            f'mapping envelope precondition 미충족: hash_mismatches='
            f'{len(report.hash_mismatches)} parse_errors={len(report.parse_errors)}. '
            'clean dry-run report 만 central 전파 envelope 로 생성 가능.',
        )


def build_mapping_envelope(
    report: RekeyDryRunReport, *, provider: str = 'unlicensed',
) -> RekeyMappingEnvelope:
    """dry-run report → central 전파용 verbatim mapping envelope (provider-side).

    central 으로 보낼 (old→new) 매핑 + checksum 을 캡슐화. report.mapping (stored old →
    new, distinct) 을 그대로 옮긴다 (재계산 0). **precondition gate (Codex P1)**:
    `is_backfill_precondition_met` false (hash_mismatches 또는 parse_errors 존재) 면
    `RekeyMappingEnvelopePreconditionError` — dirty 매핑을 central 에 보내지 않는다.
    """
    if not is_backfill_precondition_met(report):
        raise RekeyMappingEnvelopePreconditionError(report)
    return RekeyMappingEnvelope(
        provider=provider,
        pairs=report.mapping,
        old_hash_checksum=report.old_hash_checksum,
        new_hash_checksum=report.new_hash_checksum,
        mapping_checksum=report.mapping_checksum,
        record_count=report.distinct_old_hashes,
    )


def verify_mapping_envelope_integrity(envelope: RekeyMappingEnvelope) -> bool:
    """envelope pairs 가 declared checksum 과 일치 (transport 무결성, provider-side).

    old/new **set** checksum + **(old\\tnew) pair** checksum (`mapping_checksum`) 을 모두
    재계산해 declared 와 대조 — pair association swap (`old1→new2, old2→new1`) 까지 탐지
    (Codex P1). condition_hash 재계산이 아니라 mapping string checksum 검증이다. central
    은 본 함수를 호출하지 않는다 (hashlib 금지 가드) — local 이 송신 전 검증.
    """
    olds = [old for old, _new in envelope.pairs]
    news = [new for _old, new in envelope.pairs]
    return (
        _checksum(olds) == envelope.old_hash_checksum
        and _checksum(news) == envelope.new_hash_checksum
        and _mapping_checksum(envelope.pairs) == envelope.mapping_checksum
    )


def build_rekey_dry_run_report(
    records: Sequence[RekeyConditionRecord],
    field_set: ConditionFieldSet,
) -> RekeyDryRunReport:
    """records → old→new 매핑 + collision + checksum 리포트 (pure, read-only).

    **stored** old hash (record.stored_condition_hash) 가 mapping/checksum/merge 의
    기준이다 (실 backfill 에 사용). recomputed old = LIVE
    `compute_live_hash_for_field_set` (row_order 포함) 는 stored 와 대조해
    drift(`hash_mismatches`) 를 잡는 evidence. new = generic
    `compute_stable_hash_for_field_set` (value-based, field-set 주입). 둘 다 같은
    `field_set` 을 쓴다 — provider identity field-set 은 caller 가 주입한다(이 모듈은
    generic 이라 provider 기본값을 갖지 않는다, 2026-08-14). 입력을 mutate 하지 않으며
    DB 에 접근하지 않는다. 같은 입력 → byte-identical (결정적).
    """
    stored_to_new: dict[str, str] = {}
    new_to_stored: dict[str, set[str]] = {}
    stored_hashes: list[str] = []
    recomputed_hashes: list[str] = []
    new_hashes: list[str] = []
    mismatches: list[RekeyHashMismatch] = []
    parse_errors: list[RekeyParseError] = []

    for record in records:
        # 파싱 실패 record 는 test_params 복원 불가 → hash 계산 제외, 별도 채널 surface
        # (빈 params 로 silent 흡수 → drift 로 둔갑 금지).
        if record.parse_error is not None:
            parse_errors.append(
                RekeyParseError(
                    sheet_name=record.sheet_name,
                    row_order=record.row_order,
                    stored_hash=record.stored_condition_hash,
                    detail=record.parse_error,
                ),
            )
            continue
        stored = record.stored_condition_hash
        recomputed_old = compute_live_hash_for_field_set(
            field_set,
            sheet_name=record.sheet_name,
            row_order=record.row_order,
            test_params=dict(record.test_params),
        )
        new = compute_stable_hash_for_field_set(
            field_set,
            sheet_name=record.sheet_name,
            test_params=dict(record.test_params),
        )
        stored_hashes.append(stored)
        recomputed_hashes.append(recomputed_old)
        new_hashes.append(new)
        # backfill 매핑/merge 는 stored 기준 (DB 에 실제 있는 hash 를 re-key).
        stored_to_new[stored] = new
        new_to_stored.setdefault(new, set()).add(stored)
        if stored != recomputed_old:
            mismatches.append(
                RekeyHashMismatch(
                    sheet_name=record.sheet_name,
                    row_order=record.row_order,
                    stored_hash=stored,
                    recomputed_old_hash=recomputed_old,
                ),
            )

    merge_groups = tuple(
        RekeyMergeGroup(new_hash=new, old_hashes=tuple(sorted(olds)))
        for new, olds in sorted(new_to_stored.items())
        if len(olds) >= 2
    )
    mapping = tuple(sorted((stored, new) for stored, new in stored_to_new.items()))
    mismatches_sorted = tuple(
        sorted(mismatches, key=lambda m: (m.sheet_name, m.row_order, m.stored_hash))
    )
    parse_errors_sorted = tuple(
        sorted(parse_errors, key=lambda e: (e.sheet_name, e.row_order, e.stored_hash))
    )

    return RekeyDryRunReport(
        total_records=len(records),
        distinct_old_hashes=len(stored_to_new),
        distinct_new_hashes=len(new_to_stored),
        old_hash_checksum=_checksum(stored_hashes),
        recomputed_old_hash_checksum=_checksum(recomputed_hashes),
        new_hash_checksum=_checksum(new_hashes),
        mapping_checksum=_mapping_checksum(mapping),
        mapping=mapping,
        merge_groups=merge_groups,
        hash_mismatches=mismatches_sorted,
        parse_errors=parse_errors_sorted,
    )
