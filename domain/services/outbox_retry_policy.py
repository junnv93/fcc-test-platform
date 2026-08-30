"""Result outbox retry policy — pure domain SSOT (2026-06-23).

The central-sync result outbox (``infrastructure/database/result_outbox_store``)
is a textbook *transactional outbox*: a measurement attempt is recorded into the
local DB and an outbox event in the SAME transaction, then a background drainer
ships pending events to the central platform DB. The first three phases of that
pattern (durable enqueue, at-least-once delivery, idempotency key) were already
in place — but the **retry policy** (the outbox pattern's last mile) was not:

- a failed event only incremented ``retry_count`` and went straight back to
  ``pending`` → **busy-spin retry with no cap** (a permanently-failing "poison"
  event retried forever, every cycle);
- there was **no exponential backoff** (a transient central outage hammered the
  backend instead of stepping back);
- there was **no dead-letter terminal state** → a poison event sat at the head
  of the ``created_at ASC`` queue and starved newer events.

This module owns the *decision* half of that policy as a **pure** value object:
given an event's ``retry_count`` it answers (a) is this event exhausted (→
dead-letter) and (b) how long to back off before the next attempt. The
*persistence* half (writing ``next_attempt_at`` / ``dead_lettered_at`` / status)
lives in the infrastructure store, which is the single writer and is handed this
policy by injection (default = the SSOT singleton below).

Design mirrors ``analyzer_reset_policy.py``: pure numeric predicates + named
constant SSOT (no magic numbers) + ``from_raw`` parser that never touches
``os`` (env → typed coercion is the application layer's job). stdlib only;
``domain/`` purity (no infrastructure / pandas / PySide6 / pyvisa) is AST-sealed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


__all__ = [
    'DEFAULT_MAX_RETRIES',
    'DEFAULT_BASE_DELAY_SECONDS',
    'DEFAULT_MAX_DELAY_SECONDS',
    'DEFAULT_OUTBOX_RETRY_POLICY',
    'OutboxRetryPolicy',
]


#: Retry attempts before an event is moved to the dead-letter terminal state.
#: 8 attempts with the backoff below spans ~2s → ~10min (capped), i.e. roughly
#: 25 minutes of total back-off before giving up — long enough to ride out a
#: transient central outage, bounded enough that a genuine poison event stops
#: consuming drainer cycles instead of retrying forever.
DEFAULT_MAX_RETRIES = 8

#: First back-off step (seconds). Each subsequent attempt doubles this until the
#: cap. Kept as a named constant so the single source lives here, not as a magic
#: literal scattered across the store / config.
DEFAULT_BASE_DELAY_SECONDS = 2.0

#: Upper bound on a single back-off interval (seconds). Without a cap the
#: exponential term grows unbounded; 10 minutes balances retry latency against
#: not flooding the backend right after recovery.
DEFAULT_MAX_DELAY_SECONDS = 600.0


def _coerce_positive_int(raw: object, default: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _coerce_positive_float(raw: object, default: float) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class OutboxRetryPolicy:
    """Pure retry/back-off decision for a single outbox event.

    Frozen + stdlib-only so it is trivially shareable as a session-scoped
    singleton and safe to inject into the persistence store. All decisions are
    derived from ``retry_count`` (the attempt count *after* the failure is
    recorded — 1 for the first failure), so the store stays free of any
    back-off arithmetic.
    """

    max_retries: int = DEFAULT_MAX_RETRIES
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS

    def __post_init__(self) -> None:
        # Fail-loud on a nonsensical policy rather than silently producing
        # zero-length back-offs (which would re-introduce the busy-spin this
        # module exists to remove).
        if self.max_retries <= 0:
            raise ValueError('max_retries must be positive')
        if self.base_delay_seconds <= 0:
            raise ValueError('base_delay_seconds must be positive')
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError('max_delay_seconds must be >= base_delay_seconds')

    def is_exhausted(self, retry_count: int) -> bool:
        """True when ``retry_count`` reached the cap → move to dead-letter.

        Called with the post-increment attempt count, so the event is
        dead-lettered on the attempt that *reaches* ``max_retries``.
        """
        return int(retry_count) >= self.max_retries

    def next_retry_delay_seconds(
        self,
        retry_count: int,
        *,
        rng: Optional[Callable[[], float]] = None,
    ) -> float:
        """Exponential back-off for the given (post-increment) attempt count.

        ``delay = min(base * 2**(retry_count-1), max)``. ``retry_count <= 1``
        yields the base delay (defensive against 0 / negative input).

        ``rng`` is an optional jitter seam: when supplied it must return a
        fraction in ``[0, 1)`` (e.g. a decorrelated-jitter source); the delay is
        then scaled by ``(1 - fraction)`` so concurrent drainers do not thunder.
        Default ``None`` keeps the result deterministic (and the module pure) —
        the single-node desktop drainer has no herd to avoid, and determinism
        keeps the policy unit-testable.
        """
        steps = max(int(retry_count) - 1, 0)
        # Guard the exponent so a misconfigured huge retry_count cannot overflow
        # the float before the cap clamps it.
        if steps > 32:
            delay = self.max_delay_seconds
        else:
            delay = min(self.base_delay_seconds * (2 ** steps), self.max_delay_seconds)
        if rng is not None:
            fraction = max(0.0, min(float(rng()), 1.0))
            delay = delay * (1.0 - fraction)
        return float(delay)

    @classmethod
    def from_raw(
        cls,
        *,
        max_retries: object = None,
        base_delay_seconds: object = None,
        max_delay_seconds: object = None,
    ) -> 'OutboxRetryPolicy':
        """Build a policy from raw (e.g. env-string / None) values.

        Pure — never reads ``os``; the application layer passes already-read raw
        values. ``None`` / garbage / non-positive falls back to the SSOT
        default for that field (so a partially-set config never yields a
        zero-delay policy).
        """
        return cls(
            max_retries=_coerce_positive_int(max_retries, DEFAULT_MAX_RETRIES),
            base_delay_seconds=_coerce_positive_float(
                base_delay_seconds, DEFAULT_BASE_DELAY_SECONDS
            ),
            max_delay_seconds=_coerce_positive_float(
                max_delay_seconds, DEFAULT_MAX_DELAY_SECONDS
            ),
        )


#: SSOT singleton — the default policy the persistence store uses unless a
#: caller injects an override. One object so the store / fake / future config
#: all reference the same defaults instead of re-deriving them.
DEFAULT_OUTBOX_RETRY_POLICY = OutboxRetryPolicy()
