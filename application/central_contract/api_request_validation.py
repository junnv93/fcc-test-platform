"""중앙 플랫폼 계약 — 명령형 요청 검증.

선언 표가 아니라 **실행되는 검증**이라 표면 모듈과 종류가 다르다. 라우터가 스키마로는
표현되지 않는 조건을 거부할 때 여기를 부른다.
"""
from __future__ import annotations

from typing import Mapping

class ProjectResultReferenceRequestUnprocessableError(ValueError):
    """Raised when publication input crosses the server-owned provenance boundary."""


_PROJECT_RESULT_REFERENCE_REQUEST_FIELDS = frozenset({
    'provider_id',
    'condition_hash',
    'reason',
})


def validate_project_result_reference_request(body: object) -> dict[str, object]:
    """Validate and copy the closed publication request at the HTTP boundary.

    The OpenAPI mapping below is the documentation projection; this function is
    the runtime guard. In particular, JSON objects containing payload, hashes,
    attempt/session ids, or any other server-owned provenance are rejected before
    the application service is called.
    """
    if not isinstance(body, Mapping):
        raise ProjectResultReferenceRequestUnprocessableError(
            'publication request must be a JSON object'
        )

    extra = sorted(set(body) - _PROJECT_RESULT_REFERENCE_REQUEST_FIELDS)
    if extra:
        raise ProjectResultReferenceRequestUnprocessableError(
            'publication request contains unknown or server-owned fields'
        )

    missing = [
        field for field in ('provider_id', 'condition_hash')
        if field not in body
    ]
    if missing:
        raise ProjectResultReferenceRequestUnprocessableError(
            'publication request requires provider_id and condition_hash'
        )

    for field in ('provider_id', 'condition_hash'):
        value = body[field]
        if not isinstance(value, str) or not value.strip():
            raise ProjectResultReferenceRequestUnprocessableError(
                f'{field} must be a non-empty string'
            )

    if 'reason' in body:
        reason = body['reason']
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ProjectResultReferenceRequestUnprocessableError(
                'reason must be a non-empty string of at most 500 characters'
            )

    return {key: body[key] for key in body}
