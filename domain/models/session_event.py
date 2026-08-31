# domain/models/session_event.py
"""SessionEvent — driving-port domain event for session progress streaming.

순수 도메인 값 객체. ``infrastructure``, ``PySide6``, ``fastapi``,
``asyncio``, ``anyio``, ``pyvisa``, ``openpyxl``, ``pandas`` import 금지.

Producer: 어댑터(예: ``QtSignalEventBridge``)가 ``TestRunner``의 Qt 시그널을
이 객체로 변환하여 ``SessionEventPort.publish`` 호출.

Consumer: 다른 어댑터(예: ``InMemoryEventBus``의 구독자) 또는 WS 라우터.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SessionEventKind(str, Enum):
    """세션 진행 중 발생할 수 있는 이벤트 종류.

    문자열 Enum — JSON 직렬화 시 ``.value`` 그대로 사용.
    """

    PROGRESS = 'progress'
    CELL = 'cell'
    TOTAL = 'total'
    COMPLETION = 'completion'
    ERROR = 'error'
    LOG = 'log'
    NOTIFICATION = 'notification'


@dataclass(frozen=True)
class SessionEvent:
    """Frozen domain event payload."""

    kind: SessionEventKind
    payload: tuple[Any, ...] = field(default_factory=tuple)

    @classmethod
    def of(cls, kind: SessionEventKind, *payload: Any) -> 'SessionEvent':
        return cls(kind=kind, payload=tuple(payload))
