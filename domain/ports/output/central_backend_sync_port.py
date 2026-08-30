"""Output port for central backend result synchronization."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class ResultSyncBatchOutcome(str, Enum):
    """What the backend did with the current batch."""

    COMPLETED = 'completed'
    RETRYABLE_DISABLED = 'retryable_disabled'


@dataclass(frozen=True)
class ResultSyncBatchResult:
    """Acknowledgement returned by a central backend sync adapter."""

    synced_event_ids: list[int] = field(default_factory=list)
    failed_events: dict[int, str] = field(default_factory=dict)
    outcome: ResultSyncBatchOutcome = ResultSyncBatchOutcome.COMPLETED
    outcome_reason: str = ''
    # Readiness is deliberately scalar here.  The domain port must not import
    # the platform enum; the platform/HTTP boundary validates its vocabulary
    # before constructing this result.
    readiness_code: str | None = None
    retryable: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ResultSyncBatchOutcome):
            raise TypeError('outcome must be a ResultSyncBatchOutcome')
        if self.readiness_code is not None:
            if not isinstance(self.readiness_code, str) or not self.readiness_code.strip():
                raise ValueError('readiness_code must be a non-empty string when supplied')
            object.__setattr__(self, 'readiness_code', self.readiness_code.strip())
        if self.retryable is not None and not isinstance(self.retryable, bool):
            raise TypeError('retryable must be a bool when supplied')
        has_readiness_metadata = (
            self.readiness_code is not None or self.retryable is not None
        )
        if self.outcome is ResultSyncBatchOutcome.RETRYABLE_DISABLED:
            if self.readiness_code is None or self.retryable is not True:
                raise ValueError(
                    'retryable_disabled results require readiness_code and retryable=True'
                )
        elif has_readiness_metadata and (
            self.readiness_code is None or self.retryable is not False
        ):
            raise ValueError(
                'completed results require readiness_code with retryable=False '
                'when readiness metadata is supplied'
            )


@runtime_checkable
class CentralBackendSyncPort(Protocol):
    """Push local result outbox events to the central backend."""

    def sync_result_events(
        self,
        events: list[dict],
        *,
        provider_uuid: str | None = None,
    ) -> ResultSyncBatchResult:
        """
        Send one batch of local result outbox events.

        Implementations must be idempotent by each event's idempotency_key.
        ``provider_uuid`` is supplied only by the central platform readiness
        boundary; a chamber HTTP implementation keeps its configured provider
        code on the wire and does not use this optional central-only hint.
        """
        ...
