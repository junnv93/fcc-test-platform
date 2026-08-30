"""중앙 플랫폼 계약 — operation 빌더와 표면 횡단 에러 조각.

여기 있는 에러 조각은 **둘 이상의 표면이 참조한다**는 사실로 파생된 것이지 손으로
고른 것이 아니다. 한 표면만 참조하는 조각은 그 표면 모듈이 갖는다.
"""
from __future__ import annotations

from typing import Optional

from fcc_test_contracts.common.access_policy import ALLOWED_DURING_PASSWORD_CHANGE

# Operation-specific error-response descriptions (SSOT — referenced by the write
# operations below so the same string is not duplicated, and consumed by the
# shared ``apply_operation_error_responses`` merge in ``api_schema``).
_CLAIM_CONFLICT_409 = (
    'Claim conflict — condition already held by another operator '
    '(acquire) or no open claim to release/expire (release).'
)


# Phase 1 — project detail not found.
_PROJECT_NOT_FOUND_404 = (
    'Unknown project_id — no central projects row. (Creating with an existing '
    'model name reuses that project, so create never 404s.)'
)


_CHAMBER_NOT_FOUND_404 = (
    'Unknown chamber_id — not registered in the central chamber_nodes registry.'
)


def _operation(
    *,
    request: Optional[str],
    response: str,
    permission: str,
    error_responses: Optional[dict] = None,
    response_media_type: Optional[str] = None,
    allowed_during_password_change: bool = False,
) -> dict:
    op = {'request': request, 'response': response, 'permission': permission}
    # FE-P6-unify (2026-05-29): operation-specific error responses declared in the
    # contract SSOT (data-driven), consumed by the shared
    # ``apply_operation_error_responses`` helper — the SAME mechanism the headless
    # builder uses. Replaces the old name-based ``if`` branches in _build_responses_for.
    if error_responses is not None:
        op['error_responses'] = error_responses
    if response_media_type:
        op['response_media_type'] = response_media_type
    # 신원 축 EMS 정합 (2026-08-21) — 비밀번호 강제 변경 중에도 도달 가능한 operation.
    #
    # ⚠️ True 일 때만 키를 넣는다. ``ApiAccessPolicy`` 가 ``.get(...)`` 으로 읽으므로
    # **키의 부재가 곧 차단**이고, 나중에 추가되는 operation 은 누군가 의도적으로
    # 옵트인하기 전까지 거부된다(fail closed). 모든 항목에 ``False`` 를 적어 두면
    # 읽기는 같지만 다음 사람이 "중복 키 정리"로 기본값을 뒤집게 만든다.
    if allowed_during_password_change:
        op[ALLOWED_DURING_PASSWORD_CHANGE] = True
    return op
