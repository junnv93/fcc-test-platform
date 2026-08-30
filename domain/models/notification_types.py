"""
domain/models/notification_types.py

알림 도메인 타입 — 순수 값 객체 (I/O 없음).

외부 의존: 표준 라이브러리만 (datetime, dataclasses, enum)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Set


class NotificationLevel(str, Enum):
    """알림 중요도 레벨 (str Enum — JSON/log 직렬화 호환)."""
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EventTypes:
    """이벤트 타입 상수 — 문자열 리터럴 SSOT."""
    SESSION_STARTED = "session.started"
    SESSION_COMPLETED = "session.completed"
    SESSION_FAILED = "session.failed"
    TEST_FAILED = "test.failed"
    BATTERY_WARNING = "battery.warning"
    BATTERY_CRITICAL = "battery.critical"
    PROGRAM_ERROR = "system.error"
    HARDWARE_ERROR = "hardware.error"
    PROGRESS_MILESTONE = "progress.milestone"   # Sprint 70: 25%/50%/75% 진행률 알람

    # cooldown 면제 이벤트 — 이 이벤트는 중복 억제 없이 즉시 전달
    NO_COOLDOWN: Set[str] = {
        # 세션/시스템 레벨 이벤트는 항상 정확히 1회씩 발생 → cooldown 면제
        SESSION_STARTED,
        SESSION_COMPLETED,
        SESSION_FAILED,
        HARDWARE_ERROR,
        PROGRESS_MILESTONE,   # Sprint 70: 동일 세션 내 3회(25/50/75%) — 중복 없음
        # 의도적으로 제외: TEST_FAILED
        # → 동일 세션 내 연속 다수 발생 가능, spam 방지를 위해 300초 cooldown 적용
        # → 첫 실패 알람 후 5분마다 1회 발송이 운영 모니터링 관점에서 적정
    }


@dataclass
class NotificationEvent:
    """
    단일 알림 이벤트 값 객체.

    불변 계약: 생성 후 필드 변경 금지 (frozen=False지만 관행상 읽기 전용으로 취급).
    """
    event_type: str
    level: NotificationLevel
    title: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    details: Dict[str, Any] = field(default_factory=dict)
