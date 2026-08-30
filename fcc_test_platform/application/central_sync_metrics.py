"""Bounded platform metrics for central result-sync readiness."""
from __future__ import annotations

from fcc_test_contracts.common.metrics_registry import ApiMetricsRegistry, CounterFamily
from application.central_contract.central_sync_readiness import (
    CentralSyncReadiness,
    CentralSyncReadinessCode,
)


CENTRAL_SYNC_READINESS_METRIC = 'central_sync_readiness_total'


def _readiness_label_values() -> tuple[tuple[str, str], ...]:
    """Derive every bounded ``(code, retryable)`` series from the enum."""
    return tuple(
        (code.value, str(retryable).lower())
        for code in CentralSyncReadinessCode
        for retryable in (False, True)
    )


CENTRAL_SYNC_READINESS_COUNTER_FAMILY = CounterFamily(
    name=CENTRAL_SYNC_READINESS_METRIC,
    help='Central result-sync readiness outcomes by code and retryability.',
    label_names=('code', 'retryable'),
    label_values=_readiness_label_values(),
)

# Compatibility aliases for the existing platform composition naming; both
# names point to the same enum-derived declaration.
CENTRAL_SYNC_READINESS_COUNTER = CENTRAL_SYNC_READINESS_COUNTER_FAMILY


class CentralSyncReadinessMetrics:
    """Observe readiness outcomes without provider/chamber/event labels."""

    def __init__(self, registry: ApiMetricsRegistry) -> None:
        self._registry = registry

    def observe(self, readiness: CentralSyncReadiness) -> None:
        if not isinstance(readiness, CentralSyncReadiness):
            raise TypeError('readiness observer requires CentralSyncReadiness')
        code = CentralSyncReadinessCode(readiness.code)
        if not isinstance(readiness.retryable, bool):
            raise TypeError('readiness retryable must be a bool')
        self._registry.inc_counter(
            CENTRAL_SYNC_READINESS_METRIC,
            labels={
                'code': code.value,
                'retryable': str(readiness.retryable).lower(),
            },
        )

    __call__ = observe


CentralSyncMetrics = CentralSyncReadinessMetrics


__all__ = [
    'CENTRAL_SYNC_READINESS_COUNTER_FAMILY',
    'CENTRAL_SYNC_READINESS_COUNTER',
    'CENTRAL_SYNC_READINESS_METRIC',
    'CentralSyncReadinessMetrics',
    'CentralSyncMetrics',
]
