"""PublishedTestPlan 도메인 value object (publish/materialization Phase 1, ADR-0005/0009).

저장된 `TestPlanDraft`(DRAFT)를 publish 하면 만들어지는 **immutable materialized plan**.
draft 가 generation identity(`generation_key`, condition_hash=None)만 보유하던 것과 달리,
published plan 의 모든 row 는 measurement condition identity(`condition_hash`, ADR-0005
stable hash)가 **확정**되어 있다(materialize 는 publish 시점, 도메인 서비스
`unlicensed.test_plan_publish.materialize_published_rows` 가 `materialize_condition_hash`
SSOT 로 채운다 — 본 모델은 그 결과의 불변 컨테이너).

**scope/generation snapshot 재사용(Codex)**: `scope_profile_json`/`generation_metadata_json`
은 publish 시 draft 에서 **그대로 복사**한 불변 스냅샷이다(재직렬화/재해석 없음) — published
plan 의 감사 안정성(publish 당시 scope + 생성 선택 조건 보존).

**두 identity 의 분리(혼동 금지)**: `generation_key`(capability 좌표 SHA256, preview/diff/
regeneration tracking)는 row 구조 필드로 계속 보존되지만, plan 의 정체성 키는
`condition_hash`(measurement/claim/coverage identity)다. 본 모델은 generation_key 로
condition_hash 를 만들지 않는다(materialization 은 표시 셀값에서만 — 서비스 책임).

**purity**: stdlib + domain.models only. domain.services / infrastructure / pandas /
PySide6 import 0 (model 은 service 에 의존하지 않는다 — 의존 방향: service → model).
condition_hash 생산 SSOT(`materialize_condition_hash`)는 **서비스**가 호출하고, 본 모델은
그 결과의 구조 invariant(모든 row non-None + sha256 hexdigest 형식(64자 소문자 hex) +
중복 0)만 봉인한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple

from domain.models.test_plan_authoring import TestPlanRow

# condition_hash 형식 SSOT: condition_hash 생산 SSOT(`measurement_history.
# compute_stable_hash_for_field_set`)는 `hashlib.sha256(...).hexdigest()` 를 반환하므로
# 정확히 64자 소문자 hex 다. 본 패턴은 그 producer 출력 형식을 모델 레벨에서 봉인해
# 손상/절단/대문자/위조된 hash 가 immutable plan(claim/coverage identity, ADR-0005)에
# 새는 것을 차단한다. magic number 금지 — 길이는 상수.
SHA256_HEXDIGEST_LENGTH = 64
_CONDITION_HASH_PATTERN = re.compile(r'\A[0-9a-f]{%d}\Z' % SHA256_HEXDIGEST_LENGTH)


@dataclass(frozen=True)
class PublishedTestPlan:
    """publish 된 immutable materialized plan — condition_hash 확정 row 집합 + 스냅샷.

    `rows` 의 모든 row 는 `condition_hash` non-None(materialized) + sha256 hexdigest
    형식(64자 소문자 hex). `draft_id` 는 출처
    draft 이자 멱등 키(draft 당 publication 1개 — adapter UNIQUE 봉인). `plan_id` 는
    서버 생성 publication 식별자. `scope_profile_json`/`generation_metadata_json` 은
    draft 에서 복사한 불변 스냅샷.
    """
    # 'Test' prefix 아님이라 pytest 오수집 위험 없지만, 코드베이스 관례상 dunder 유지.
    __test__ = False

    plan_id: str
    draft_id: str
    project_id: str
    scope_revision: Optional[int]
    scope_profile_json: str
    rows: Tuple[TestPlanRow, ...] = field(default=())
    generation_metadata_json: Optional[str] = None
    published_by: Optional[str] = None
    published_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.plan_id:
            raise ValueError('PublishedTestPlan.plan_id 는 비어 있을 수 없습니다.')
        if not self.draft_id:
            raise ValueError('PublishedTestPlan.draft_id 는 비어 있을 수 없습니다.')
        if not self.scope_profile_json:
            raise ValueError(
                'PublishedTestPlan.scope_profile_json 은 비어 있을 수 없습니다 — publish '
                '는 draft 의 scope snapshot 을 그대로 보존합니다 (재현성/감사).',
            )
        if not self.rows:
            raise ValueError(
                'PublishedTestPlan.rows 가 비어 있습니다 — 행 없는 plan 은 publish 할 수 '
                '없습니다 (빈 materialized plan 은 의미가 없습니다).',
            )
        # M2 구조 invariant: published row 는 measurement condition identity 가 확정되어
        # 있어야 한다 (materialize 누락 row 가 immutable plan 에 새는 것을 차단).
        seen: set[str] = set()
        for row in self.rows:
            if row.condition_hash is None:
                raise ValueError(
                    'PublishedTestPlan.rows 의 condition_hash 가 None 입니다 — publish '
                    '전에 materialize_published_rows 로 stable condition hash 를 채워야 '
                    '합니다 (generation 단계 candidate 는 publish 불가).',
                )
            # M2-format 형식 봉인: condition_hash 생산 SSOT 는 sha256 hexdigest(정확히
            # 64자 소문자 hex)를 생산한다. 잘못된 길이/대문자/non-hex 는 손상/절단/위조된
            # hash 이므로 immutable plan 에 받지 않는다(materialize 우회로 candidate 가
            # non-None 이지만 형식이 깨진 값으로 새는 것을 차단).
            if not _CONDITION_HASH_PATTERN.match(row.condition_hash):
                raise ValueError(
                    'PublishedTestPlan.rows 의 condition_hash 가 stable hash 형식이 '
                    f'아닙니다 ({row.condition_hash!r}) — materialize_condition_hash(SSOT)'
                    f' 는 sha256 hexdigest(정확히 {SHA256_HEXDIGEST_LENGTH}자 소문자 hex)'
                    '를 생산합니다. 잘못된 길이/대문자/non-hex 는 손상되었거나 외부에서 '
                    '위조된 hash 입니다.',
                )
            # M3 중복 거부 (ADR-0005 Decision A collision policy = reject): 같은 plan 안에
            # 동일 condition_hash 두 row 는 같은 측정 조건이므로 데이터 오류다 (도메인
            # 서비스가 1차 거부하고, 본 구조 invariant 가 모델 레벨에서 재봉인).
            if row.condition_hash in seen:
                raise ValueError(
                    'PublishedTestPlan.rows 에 중복 condition_hash 가 있습니다 '
                    f"({row.condition_hash!r}) — ADR-0005 Decision A 는 동일 측정 조건 "
                    '중복을 reject 합니다.',
                )
            seen.add(row.condition_hash)
