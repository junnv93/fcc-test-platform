"""중앙 챔버 가용성 Prometheus 메트릭 (멀티챔버 M4/관측성, 2026-06-20).

운영자가 분산 측정 장애를 빨리 판단할 수 있게, 챔버 레지스트리의 *파생* 신호를
``/platform/metrics`` 에 노출한다. 메트릭은 scrape 시점에 ``ChamberMetricsCollector``
가 ``CentralChamberReadService.chamber_availability()`` 에서 계산해 registry 의 선언된
gauge family 에 set 한다(pull/collector 패턴).

SSOT 준수:
- 챔버 상태(idle/in_use/offline) 는 availability envelope 의 *파생 status* 를 그대로
  재사용한다 — staleness 규칙(``derive_chamber_status``, last_heartbeat+ttl)을 여기서
  재구현하지 않는다.
- gauge 메트릭 *이름* 은 :data:`CHAMBER_GAUGE_FAMILIES` 단일 SSOT — composition 이
  registry 에 선언하고, alert-parity 테스트도 같은 SSOT 로 registry 를 구성한다(드리프트 0).
- 카디널리티 제한(성능): per-chamber_id 라벨 대신 status 별 집계 + 최대 heartbeat age
  (고-카디널리티 chamber_id 라벨 폭발 회피).

dependency-free of infrastructure(application 계층) — domain enum + application.common 만.
"""
from __future__ import annotations

from typing import Callable

from fcc_test_contracts.common.metrics_registry import GaugeFamily
from application.central_contract.envelope_helpers import parse_timestamp
from domain.models.chamber_node import ChamberNodeStatus


__all__ = [
    'CHAMBER_GAUGE_FAMILIES',
    'CHAMBER_COUNT_GAUGE',
    'CHAMBER_HEARTBEAT_AGE_MAX_GAUGE',
    'ChamberMetricsCollector',
]


#: gauge 메트릭 base 이름(namespace 미포함 — registry 가 ``fcc_platform_`` prepend).
CHAMBER_COUNT_GAUGE = 'chamber_count'
CHAMBER_HEARTBEAT_AGE_MAX_GAUGE = 'chamber_heartbeat_age_max_seconds'

#: status 라벨 값 SSOT — 도메인 enum 에서 파생(리터럴 드리프트 금지).
_STATUS_VALUES: tuple[str, ...] = tuple(
    s.value for s in (
        ChamberNodeStatus.IDLE, ChamberNodeStatus.IN_USE, ChamberNodeStatus.OFFLINE,
    )
)

#: 선언 gauge family SSOT — composition + alert-parity 테스트 공유.
CHAMBER_GAUGE_FAMILIES: tuple[GaugeFamily, ...] = (
    GaugeFamily(
        name=CHAMBER_COUNT_GAUGE,
        help='Registered chambers by derived availability status.',
        # label key 'availability' (NOT 'status') — 'status' 는 request-status
        # taxonomy(ok/denied/error) 전용이라 의미 분리 + alert-parity 라벨 검사 충돌 회피.
        label_key='availability',
        label_values=_STATUS_VALUES,
    ),
    GaugeFamily(
        name=CHAMBER_HEARTBEAT_AGE_MAX_GAUGE,
        help='Max seconds since last heartbeat across registered chambers '
             '(0 when none). Crossing the TTL indicates a stalling fleet.',
    ),
)


class ChamberMetricsCollector:
    """Scrape-시점 챔버 gauge 갱신기.

    ``registry`` 는 :data:`CHAMBER_GAUGE_FAMILIES` 를 선언한 ``ApiMetricsRegistry``.
    ``refresh()`` 는 best-effort — 조회/계산 실패가 metrics 응답을 깨지 않게 흡수한다
    (관측성 보조 경로가 운영을 막지 않음)."""

    def __init__(self, chamber_read_service, registry) -> None:
        self._chamber_read_service = chamber_read_service
        self._registry = registry

    def refresh(self) -> None:
        try:
            self._collect()
        except Exception:  # pragma: no cover — 방어적: 관측성 실패는 흡수
            pass

    def _collect(self) -> None:
        snapshot = self._chamber_read_service.chamber_availability()
        items = snapshot.get('items', []) if isinstance(snapshot, dict) else []
        now = parse_timestamp(snapshot.get('server_time')) if isinstance(snapshot, dict) else None

        counts: dict[str, int] = {v: 0 for v in _STATUS_VALUES}
        max_age = 0.0
        for item in items:
            status = str(item.get('status') or '')
            if status in counts:
                counts[status] += 1
            last_hb = parse_timestamp(item.get('last_heartbeat_at'))
            if now is not None and last_hb is not None:
                age = (now - last_hb).total_seconds()
                if age > max_age:
                    max_age = age

        for status_value, count in counts.items():
            self._registry.set_gauge(
                CHAMBER_COUNT_GAUGE, count, label_value=status_value,
            )
        self._registry.set_gauge(CHAMBER_HEARTBEAT_AGE_MAX_GAUGE, max_age)
