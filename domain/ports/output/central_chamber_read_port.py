"""Output port — central chamber registry / availability read model (멀티챔버 P1).

웹 작업대(Phase 6)의 "시험 챔버" 화면과 측정 프록시(Phase 5)는 어떤 챔버가
가용한지(IDLE/IN_USE/OFFLINE)를 **중앙** DB 에서 읽는다. 가용성은 append-only
``chamber_heartbeat_events`` ledger 에서 파생한 ``chamber_availability`` VIEW 가
운반하되, VIEW 는 ``reported_status``/``last_heartbeat_at``/ttl 을 **verbatim**
노출만 하고 OFFLINE 은 계산하지 않는다(DB-side ``now()`` 금지). OFFLINE 파생은
read service(Phase 2)가 주입 clock 으로 :func:`domain.models.chamber_node.derive_chamber_status`
에 위임한다 (``CentralReadPort`` 의 ``expires_at`` injected-clock 처리와 동형).

read-only by construction — ``read_*`` 메서드만 노출. 생산 어댑터는 Phase 2
(``application/platform/central_chamber_read_adapter``)가 ``DbConnection`` factory
를 통해 중앙 ``chamber_nodes``/``chamber_availability`` 를 읽는다.

dependency-free: stdlib typing only (infrastructure / pyvisa / openpyxl / pandas /
PySide6 / fastapi / sqlalchemy / psycopg import 0).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


__all__ = [
    'CentralChamberReadError',
    'CentralChamberReadPort',
]


class CentralChamberReadError(RuntimeError):
    """Raised when a central chamber read fails — loud, never a silent empty result.

    가용성 조회가 조용히 빈 결과를 돌려주면 모든 챔버가 "없음/OFFLINE" 으로 보여
    백엔드 장애를 (위험하게) 빈 화면으로 가린다. 항상 loud 로 올려 API 가 5xx 를
    표면화하고 운영자가 재시도하게 한다.
    """


@runtime_checkable
class CentralChamberReadPort(Protocol):
    """Read chamber registry + availability projection from the central database."""

    def read_chamber_nodes(self) -> list[dict]:
        """``chamber_nodes`` 레지스트리 행 전체를 반환한다.

        한 행 = 등록된 챔버 1개(정체성 + ``base_url`` + ``heartbeat_ttl_seconds``).
        측정 대상이 없으면 빈 리스트(정상 — 비-에러).
        """
        ...

    def read_chamber_availability(self) -> list[dict]:
        """``chamber_availability`` VIEW 행을 verbatim 반환한다.

        한 행 = 등록된 챔버 1개 + 최신 heartbeat 의 ``reported_status`` /
        ``last_heartbeat_at`` / ``heartbeat_ttl_seconds`` / ``session_id``
        (heartbeat 전무 챔버는 LEFT JOIN 으로 ``last_heartbeat_at`` NULL 로 포함).
        OFFLINE 은 여기서 계산하지 않는다 — read service 가 주입 clock 으로 파생.
        """
        ...
