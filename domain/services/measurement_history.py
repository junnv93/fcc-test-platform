"""
Measurement history condition fingerprinting.

Pure domain helper used by DB adapters and tests. It intentionally stores the
condition contract in a deterministic JSON payload so repeated measurements can
be grouped without depending on dict insertion order or pandas-specific NaN.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any


def normalize_condition_value(value: Any) -> str | None:
    """Normalize a condition value for stable hashing."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    if not text or text.lower() == 'nan':
        return None
    return text


def serialize_condition_payload(payload: dict) -> str:
    """Serialize the condition payload deterministically."""
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


# --- WEB-PROVIDER-UI-0.5 (ADR-0005) stable row identity — forward migration target ---
#
# Decision A (value-based identity): the condition fingerprint derives ONLY from
# sheet_name + provider field-set values, with row_order REMOVED. This makes the
# fingerprint stable across test-plan insert/delete/reorder and preserves cross-engineer
# coverage (the same condition measured by different sessions maps to the same hash —
# central keys on pure (project_id, condition_hash)).
#
# These are the implementation-ready forward SSOT. They are NOT yet wired: the live
# path (build_condition_payload_for_field_set / compute_live_hash_for_field_set
# below) still includes row_order until the re-keying migration (WEB-PROVIDER-UI-0.6/2A)
# recomputes existing local SQLite + central PG hashes. Swapping before the migration
# would orphan every central claim/coverage row keyed on the old (row_order-baked) hash.
# See docs/adr/0005-stable-row-identity-and-test-plan-store.md.

# ─── Provider field-set injection (ADR-0005 re-keying prerequisite, 2026-05-31) ───
#
# 설계 원칙 — **hash algorithm 은 generic (provider-agnostic), identity field-set 은
# provider-owned**. canonical-json + sha256 직렬화 알고리즘은 어느 provider 든
# 동일하지만, "어떤 컬럼이 condition identity 를 구성하는가" 는 provider 가 소유한다.
# 이 분리로 2nd provider (mmWave/licensed) 가 자기 field-set 만 선언하면 같은
# algorithm 을 재사용한다 — 단일 provider 가정을 코드에 더 굳히지 않는다.
#
# **본 모듈은 algorithm 만 소유한다 (2026-08-14, repository-split delivery closure)**
# — field-set 자체(어떤 Excel 컬럼이 identity 를 구성하는가)는 provider 어휘라 이
# 모듈이 `column_names` 를 import 하는 결함(shared-kernel 납품 폐포가 provider
# 모듈 `column_names` 를 끌고 들어옴)을 만든다. Unlicensed field-set
# (`UNLICENSED_CONDITION_FIELD_SET`) + 그 field-set 으로 고정된 wrapper
# (`compute_condition_hash`/`build_condition_payload`/`compute_stable_condition_hash`/
# `build_stable_condition_payload`)는 `domain/services/unlicensed/condition_identity.py`
# 로 이전됐다 — 이름/시그니처 byte-identical, 정의 위치만 이동. 아래 generic 함수
# (field_set 인자 필수) 가 유일한 hash 계산 로직이고, provider wrapper 는 거기에 위임.


@dataclass(frozen=True)
class ConditionFieldSet:
    """provider-owned condition identity field-set 선언 (ADR-0005).

    `columns`: condition_hash 를 구성하는 ordered 컬럼 (provider 가 소유). generic
    algorithm (`compute_stable_hash_for_field_set`) 이 본 field-set 을 주입받아
    hash 를 계산하므로, algorithm 과 field-set 이 분리된다.

    invariant (`__post_init__`): name 비어있지 않음 / columns 비어있지 않음 /
    duplicate column 금지 (중복 컬럼은 hash payload 키 충돌·의미 모호 — loud 차단).
    """

    name: str
    columns: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError('ConditionFieldSet.name 은 비어있을 수 없습니다.')
        if not self.columns:
            raise ValueError(
                f"ConditionFieldSet({self.name!r}).columns 가 비어있습니다 — "
                'identity field-set 은 최소 1개 컬럼이 필요합니다.',
            )
        if len(self.columns) != len(set(self.columns)):
            dups = sorted({c for c in self.columns if list(self.columns).count(c) > 1})
            raise ValueError(
                f"ConditionFieldSet({self.name!r}).columns 에 중복 컬럼 {dups} — "
                'hash payload 키 충돌/의미 모호를 차단합니다.',
            )


class DuplicateConditionError(ValueError):
    """Domain signal: an edit would create a row whose STABLE_CONDITION_COLUMNS values
    duplicate an existing condition (ADR-0005 Decision A collision policy = reject).

    Pure-domain — names no HTTP status code. The web/editing-API adapter maps it to an
    HTTP conflict (409 / 422), exactly as application/platform's ``ClaimConflictError``
    is mapped to 409 by the route layer. Keeping status codes out of the domain
    preserves the hexagonal boundary (domain purity).
    """


class DuplicateConditionPolicy:
    """ADR-0005 collision policy for value-based identity (Decision A).

    Two test-plan rows with identical STABLE_CONDITION_COLUMNS values collapse to a
    single condition_hash. Genuine exact duplicates are REJECTed at edit time (raising
    DuplicateConditionError) rather than silently merged or disambiguated by row
    position — a duplicate condition row is a data error (the Power columns in
    HISTORY_CONDITION already disambiguate sweep variants). HTTP status mapping
    (409/422) belongs to the web/editing-API adapter, not this domain module.
    """

    REJECT = 'reject'


def build_stable_condition_payload_for_field_set(
    field_set: ConditionFieldSet, *, sheet_name: str, test_params: dict,
) -> dict:
    """Generic (provider-agnostic) stable condition payload — row_order 부재.

    algorithm 은 generic 이고 `field_set.columns` 가 주입된 provider identity field-set.
    canonical-json + sha256 직렬화 자체는 어느 provider 든 동일하다 (ADR-0005).
    """
    return {
        'sheet_name': normalize_condition_value(sheet_name) or '',
        'params': {
            col: normalize_condition_value(test_params.get(col))
            for col in field_set.columns
        },
    }


def compute_stable_hash_for_field_set(
    field_set: ConditionFieldSet, *, sheet_name: str, test_params: dict,
) -> str:
    """Generic (provider-agnostic) stable fingerprint — field-set 주입 algorithm.

    algorithm/field-set 분리 SSOT: 본 함수가 유일한 stable-hash algorithm 이고,
    provider 별 차이는 `field_set` 인자뿐이다. provider-specific
    `compute_stable_condition_hash` 는 본 함수에 Unlicensed field-set 으로 위임.
    """
    payload = build_stable_condition_payload_for_field_set(
        field_set, sheet_name=sheet_name, test_params=test_params,
    )
    return hashlib.sha256(serialize_condition_payload(payload).encode('utf-8')).hexdigest()


def build_condition_payload_for_field_set(
    field_set: ConditionFieldSet, *, sheet_name: str, row_order: int, test_params: dict,
) -> dict:
    """Generic (provider-agnostic) LIVE condition payload — row_order included.

    Symmetric to `build_stable_condition_payload_for_field_set`, minus the
    row_order removal. This is the pre-migration identity formula (ADR-0005 §(d) —
    unwired until the re-keying migration runs). algorithm 은 generic 이고
    `field_set.columns` 가 주입된 provider identity field-set.
    """
    return {
        'sheet_name': normalize_condition_value(sheet_name) or '',
        'row_order': int(row_order),
        'params': {
            col: normalize_condition_value(test_params.get(col))
            for col in field_set.columns
        },
    }


def compute_live_hash_for_field_set(
    field_set: ConditionFieldSet, *, sheet_name: str, row_order: int, test_params: dict,
) -> str:
    """Generic (provider-agnostic) LIVE fingerprint — field-set 주입 algorithm.

    algorithm/field-set 분리 SSOT: 본 함수가 유일한 live-hash algorithm 이고,
    provider 별 차이는 `field_set` 인자뿐이다. provider-specific
    `compute_condition_hash` 는 본 함수에 Unlicensed field-set 으로 위임.
    """
    payload = build_condition_payload_for_field_set(
        field_set, sheet_name=sheet_name, row_order=row_order, test_params=test_params,
    )
    return hashlib.sha256(serialize_condition_payload(payload).encode('utf-8')).hexdigest()


_CONTEXT_KEYS = (
    'project_id',
    'model_id',
    'model_number',
    'sample_id',
    'sample_number',
    'sample_code',
    'test_pc_id',
    'test_pc_code',
    'run_id',
    'technology_code',
)


def build_result_context(
    *,
    session_id: int,
    sheet_name: str,
    row_order: int,
    test_params: dict,
    technology_column: str,
    context: dict | None = None,
) -> dict:
    """Build canonical result context for central sync and audit metadata.

    `technology_column` is the provider column name that carries the technology
    value in `test_params` (Unlicensed = `Col.TECHNOLOGY`) — injected by the
    caller so this generic module does not import provider vocabulary
    (`column_names`).
    """
    source = context or {}
    technology = (
        source.get('technology_code')
        if source.get('technology_code') is not None
        else test_params.get(technology_column)
    )
    result = {
        'session_id': int(session_id),
        'sheet_name': normalize_condition_value(sheet_name) or '',
        'row_order': int(row_order),
        'technology_code': normalize_condition_value(technology),
    }
    for key in _CONTEXT_KEYS:
        if key == 'technology_code':
            continue
        if key == 'sample_number':
            result[key] = _normalize_int(source.get(key))
        else:
            result[key] = normalize_condition_value(source.get(key))
    return result


def compute_attempt_idempotency_key(
    *,
    session_id: int,
    condition_hash: str,
    attempt_number: int,
    context: dict | None = None,
) -> str:
    """Return a stable idempotency key for one measurement attempt."""
    ctx = context or {}
    run_id = normalize_condition_value(ctx.get('run_id'))
    test_pc_id = normalize_condition_value(ctx.get('test_pc_id') or ctx.get('test_pc_code'))
    scope = f"run:{run_id}" if run_id else f"local-session:{int(session_id)}"
    if test_pc_id:
        scope = f"{scope}:pc:{test_pc_id}"
    raw = f"{scope}:condition:{condition_hash}:attempt:{int(attempt_number)}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _normalize_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        text = normalize_condition_value(value)
        if text is None:
            return None
        try:
            return int(text)
        except ValueError:
            return None
