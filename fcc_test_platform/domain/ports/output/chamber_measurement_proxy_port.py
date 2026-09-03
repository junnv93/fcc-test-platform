"""Output port — 챔버 노드 Session API forward 프록시 (멀티챔버 P11, 2026-06-16).

웹은 중앙 1곳만 인증한다(단일 인증 표면). ``ChamberMeasurementService`` 가 그 인증
경계 안에서 챔버 가용성 IDLE 게이트 + ``base_url`` lookup 을 수행한 뒤, **실제 노드
forward**(outbound HTTP)는 이 포트의 구현체에 위임한다. P5 에서 시간 제약상 명시
``Protocol`` 없는 구조적(duck-typed) 협력자로 승인됐던 계약을 P11 에서 narrow output
Port 로 승격해 헥사고날 정합을 회복한다(:class:`CentralChamberReadPort` 와 동형 —
포트와 실패 모드 에러를 co-locate, 측정/forward 동작 byte-compatible).

생산 구현은 ``infrastructure/adapters/driven/chamber_proxy_adapter`` 의
``HttpChamberProxyAdapter`` (stdlib urllib + ``build_outbound_traceparent_headers`` SSOT
+ 노드 경로 ``SESSION_API_ROUTES`` SSOT 파생). forward 실패(네트워크/타임아웃/노드 5xx/
JSON 오류)는 조용한 빈 결과로 "측정 시작됨" 오인을 막기 위해 :class:`ChamberProxyError`
로 loud 하게 올린다(중앙 API 가 503 UPSTREAM_UNAVAILABLE 표면화).

dependency-free: stdlib typing only (infrastructure / pyvisa / openpyxl / pandas /
PySide6 / fastapi / sqlalchemy / psycopg import 0).
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


__all__ = [
    'ChamberProxyError',
    'ChamberBusyError',
    'ChamberMeasurementProxyPort',
]


class ChamberProxyError(RuntimeError):
    """챔버 노드 Session API 로의 forward 가 실패 — loud(→ 503), 조용한 no-op 금지.

    노드 미응답/네트워크 오류/노드 5xx 를 빈 결과로 가리면 웹이 측정이 시작된 것처럼
    오인한다. 항상 loud 로 올려 중앙 API 가 503(UPSTREAM_UNAVAILABLE)을 표면화한다.
    (포트 계약의 실패 모드 SSOT — :class:`CentralChamberReadError` 와 동형 co-location.)
    """


class ChamberBusyError(ChamberProxyError):
    """노드가 ``/session/start`` 를 409 로 거부 — 이미 측정 중(재진입 가드).

    중앙 IDLE 게이트는 heartbeat 파생 가용성으로 막지만, heartbeat 지연 race 로 중앙이
    IDLE 로 본 사이 노드가 이미 측정 중일 수 있다. 그 경우 노드(권위)가 409 로 거부하고,
    중앙은 이를 5xx upstream 오류가 아니라 가용성 충돌(:class:`ChamberNotAvailableError`,
    409)로 전파한다 — IDLE 게이트와 같은 의미. ``ChamberProxyError`` 하위라 기존
    ``except ChamberProxyError`` 호출자는 영향 없음(더 구체 catch 가 선행).
    """


@runtime_checkable
class ChamberMeasurementProxyPort(Protocol):
    """챔버 노드 Session API 로 측정 시작/진행조회를 forward 하는 outbound 협력자 계약.

    두 메서드 모두 노드 progress 의 raw dict(``{is_running, completed, total, ratio}``)
    를 반환하고 호출 측 서비스가 타입을 정규화한다. forward 실패는
    :class:`ChamberProxyError` 로 올린다(빈 dict 반환 금지).
    """

    def start_measurement(
        self, base_url: str, *, published_plan_id: Optional[str] = None,
        project_id: Optional[str] = None, sample_id: Optional[str] = None,
        sample_snapshot: Optional[dict] = None,
        sample_snapshot_schema_version: Optional[str] = None,
        project_result_reference_snapshot_json: Optional[str] = None,
        project_result_reference_snapshot_schema_version: Optional[str] = None,
    ) -> dict:
        """``base_url`` 의 노드 ``/session/start`` 로 측정 시작을 forward 한다.

        The platform-owned project/sample/reference snapshot fields are forwarded
        verbatim; ``published_plan_id`` remains optional for workbook-source
        compatibility.
        """
        ...

    def get_progress(self, base_url: str) -> dict:
        """``base_url`` 의 노드 ``/session/progress`` 로 진행조회를 forward 한다."""
        ...
