"""Chamber measurement proxy forward policy — pure domain SSOT (2026-06-23).

The central hub forwards measurement start / progress calls to a chamber node's
Session API (``infrastructure/adapters/driven/chamber_proxy_adapter``). Before
this module the forward had two hard-coded compromises:

- the request **timeout** was a module-level literal (``10.0``) with no env
  override, so a slow LAN / heavy measurement could not be tuned without an
  edit-and-rebuild;
- there was **no retry** — a single transient blip (a dropped TCP packet, a
  node mid-restart returning 503, a momentary DNS hiccup) surfaced straight to
  the web as a 503, even though an immediate re-attempt would have succeeded.

This module owns the *decision* half of the forward policy as a **pure** value
object: the request timeout plus, on a *transient* failure, whether to retry and
how long to back off. The *execution* half (issuing the HTTP request, classifying
the caught exception, sleeping) lives in the infrastructure adapter, which is the
single I/O site and is handed this policy by injection (default = the SSOT
singleton below).

Retries apply to **transient** failures only. A permanent upstream failure
(HTTP 4xx — bad request / not found / conflict) must NOT be retried: re-issuing
it just wastes the operator's wait and the node's cycles. The transient/permanent
split is expressed here as a pure numeric predicate over the HTTP status
(:meth:`ChamberProxyPolicy.is_retryable_http_status`); connection-level errors
(URLError / OSError / timeout) are always transient and the adapter treats them
as such without consulting a status code.

Design mirrors ``outbox_retry_policy.py`` / ``analyzer_reset_policy.py``: pure
numeric predicates + named constant SSOT (no magic numbers) + ``from_raw``
parser that never touches ``os`` (env → typed coercion is the application
layer's job). stdlib only; ``domain/`` purity (no infrastructure / pandas /
PySide6 / pyvisa) is AST-sealed.
"""
from __future__ import annotations

from dataclasses import dataclass


__all__ = [
    'DEFAULT_PROXY_TIMEOUT_SECONDS',
    'DEFAULT_PROXY_MAX_RETRIES',
    'DEFAULT_PROXY_BASE_DELAY_SECONDS',
    'DEFAULT_PROXY_MAX_DELAY_SECONDS',
    'DEFAULT_CHAMBER_PROXY_POLICY',
    'ChamberProxyPolicy',
]


#: Per-request forward timeout (seconds). A node that does not answer within this
#: window yields a loud ``ChamberProxyError`` (→ 503) instead of an unbounded
#: wait. Default kept at the prior hard-coded value so behaviour is unchanged
#: when no env override is set.
DEFAULT_PROXY_TIMEOUT_SECONDS = 10.0

#: Number of *re-attempts* after the first try fails transiently. The total
#: number of forward attempts is ``1 + max_retries``. Kept small because this is
#: an **inline synchronous** retry inside a web request handler (unlike the
#: outbox drainer's background retries) — 2 re-tries rides out a momentary blip
#: without holding the caller's request open for long.
DEFAULT_PROXY_MAX_RETRIES = 2

#: First back-off step (seconds); each subsequent re-attempt doubles it until the
#: cap. Short because the retry is synchronous on the request path.
DEFAULT_PROXY_BASE_DELAY_SECONDS = 0.2

#: Upper bound on a single back-off interval (seconds) — bounds the worst-case
#: added latency of the inline retry sequence.
DEFAULT_PROXY_MAX_DELAY_SECONDS = 1.0

#: HTTP statuses that are worth retrying even though the node answered: a
#: temporary overload / restart. 408 Request Timeout, 429 Too Many Requests, and
#: any 5xx are transient; every other 4xx is a permanent client error.
_RETRYABLE_EXACT_STATUSES = frozenset({408, 429})


def _coerce_positive_int(raw: object, default: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _coerce_non_negative_int(raw: object, default: int) -> int:
    # max_retries=0 (retry disabled) is a legitimate configuration, so this
    # field — unlike the positive-only ones — accepts zero.
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _coerce_positive_float(raw: object, default: float) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class ChamberProxyPolicy:
    """Pure timeout + transient-retry decision for a single proxy forward.

    Frozen + stdlib-only so it is trivially shareable as a session-scoped
    singleton and safe to inject into the forward adapter. The adapter loops
    over attempts (0-based), asking :meth:`should_retry` after each transient
    failure and sleeping :meth:`next_retry_delay_seconds` before the next try.
    """

    timeout_seconds: float = DEFAULT_PROXY_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_PROXY_MAX_RETRIES
    base_delay_seconds: float = DEFAULT_PROXY_BASE_DELAY_SECONDS
    max_delay_seconds: float = DEFAULT_PROXY_MAX_DELAY_SECONDS

    def __post_init__(self) -> None:
        # Fail-loud on a nonsensical policy rather than silently producing a
        # zero/negative timeout or an inverted back-off cap.
        if self.timeout_seconds <= 0:
            raise ValueError('timeout_seconds must be positive')
        if self.max_retries < 0:
            raise ValueError('max_retries must be >= 0')
        if self.base_delay_seconds <= 0:
            raise ValueError('base_delay_seconds must be positive')
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError('max_delay_seconds must be >= base_delay_seconds')

    def should_retry(self, attempt: int) -> bool:
        """True when another attempt remains after a transient failure.

        ``attempt`` is the 0-based index of the attempt that just failed (0 for
        the first try). With ``max_retries`` re-tries the last retry is attempt
        index ``max_retries``, so we retry while ``attempt < max_retries``.
        """
        return int(attempt) < self.max_retries

    def next_retry_delay_seconds(self, attempt: int) -> float:
        """Exponential back-off before the re-attempt following ``attempt``.

        ``delay = min(base * 2**attempt, max)``. ``attempt <= 0`` yields the
        base delay (defensive against negative input). Deterministic (no jitter)
        — a single central hub retrying one node has no thundering herd to avoid,
        and determinism keeps the policy unit-testable.
        """
        steps = max(int(attempt), 0)
        if steps > 32:  # guard the exponent before the cap clamps it
            return float(self.max_delay_seconds)
        delay = min(self.base_delay_seconds * (2 ** steps), self.max_delay_seconds)
        return float(delay)

    @staticmethod
    def is_retryable_http_status(status_code: int) -> bool:
        """True for transient HTTP statuses (408 / 429 / any 5xx).

        Every other status — notably 4xx client errors — is permanent and must
        not be retried. Pure numeric predicate so the adapter never embeds the
        transient-status set inline.
        """
        try:
            code = int(status_code)
        except (TypeError, ValueError):
            return False
        if code in _RETRYABLE_EXACT_STATUSES:
            return True
        return 500 <= code <= 599

    @classmethod
    def from_raw(
        cls,
        *,
        timeout_seconds: object = None,
        max_retries: object = None,
        base_delay_seconds: object = None,
        max_delay_seconds: object = None,
    ) -> 'ChamberProxyPolicy':
        """Build a policy from raw (e.g. env-string / None) values.

        Pure — never reads ``os``; the application layer passes already-read raw
        values. ``None`` / garbage / out-of-range falls back to the SSOT default
        for that field (so a partially-set config never yields a zero-timeout or
        zero-delay policy). ``max_retries`` accepts 0 (retry disabled).
        """
        return cls(
            timeout_seconds=_coerce_positive_float(
                timeout_seconds, DEFAULT_PROXY_TIMEOUT_SECONDS
            ),
            max_retries=_coerce_non_negative_int(max_retries, DEFAULT_PROXY_MAX_RETRIES),
            base_delay_seconds=_coerce_positive_float(
                base_delay_seconds, DEFAULT_PROXY_BASE_DELAY_SECONDS
            ),
            max_delay_seconds=_coerce_positive_float(
                max_delay_seconds, DEFAULT_PROXY_MAX_DELAY_SECONDS
            ),
        )


#: SSOT singleton — the default policy the forward adapter uses unless a caller
#: injects an override. One object so the adapter / fake / future config all
#: reference the same defaults instead of re-deriving them.
DEFAULT_CHAMBER_PROXY_POLICY = ChamberProxyPolicy()
