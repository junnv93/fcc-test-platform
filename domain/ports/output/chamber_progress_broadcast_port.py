"""Chamber progress broadcast outbound port (멀티챔버 P7/B4, 2026-06-18).

중앙 진행 릴레이(ADR-0015 Option B — 노드 push 릴레이)의 fan-out 경계 Port. 중앙
Platform API 의 heartbeat ingest 경로가 in_use progress 를 받으면 이 Port 로
:class:`ChamberProgressEvent` 를 publish 하고, 중앙 WebSocket fan-out 엔드포인트가
구독자(웹 브라우저)에게 재발행한다.

Port 는 **publish 표면만** 정의한다 — async subscribe(WS 핸들러가 직접 소비)는
인프라 구현(:class:`ChamberProgressBroadcaster`)의 구체 표면이라 Port 계약 밖이다
(노드 Session API 의 ``SessionEventPort`` 가 publish/subscribe 를 함께 정의하는 것과
달리, 여기서는 도메인 어댑터(``PlatformApiAdapter``)가 publish 만 의존하고 subscribe 는
WS driving adapter 가 인프라 broadcaster 에 직접 접근). 이 좁은 의존이 도메인 경계를
publish-only 로 유지한다.

dependency-free — ``infrastructure``/``pyvisa``/``openpyxl``/``pandas``/``PySide6``
import 0 (``TestDomainPurity``).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from domain.models.chamber_node import ChamberProgressEvent


__all__ = ['ChamberProgressBroadcastPort']


@runtime_checkable
class ChamberProgressBroadcastPort(Protocol):
    """진행 이벤트를 구독자에게 fan-out 하는 outbound port (publish-only)."""

    def publish(self, event: ChamberProgressEvent) -> None:
        """진행 이벤트를 모든 활성 구독자에게 fan-out.

        구독자가 없으면 조용히 drop(합법적 — 진행 이벤트는 best-effort 릴레이이며
        권위 SSOT 는 C1 heartbeat ledger 다). 구현은 thread-safe 해야 한다(측정
        스레드/요청 스레드 어디서든 호출 가능).
        """
        ...
