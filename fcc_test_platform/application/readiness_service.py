"""Readiness evaluation — TTL-cached, single-flight (2026-07-20).

Holds the two things :mod:`domain.services.health_probe_policy` deliberately does
not: the clock and the dependency I/O. The *decisions* (status vocabulary,
freshness rule, body shape) all live in the domain policy; this service only
executes probes and remembers the verdict.

Two properties matter operationally:

**TTL cache** — the verdict is reused while it is fresh, so the dependency touch
rate is bounded by ``1 / ttl`` per process no matter how hard the auth-exempt
endpoint is polled (see the policy module for why that matters).

**Single flight, non-blocking** — the refresh runs while holding the lock, so N
concurrent requests arriving on a cold cache produce **one** dependency probe,
not N. This is the case that matters most: a cache miss is exactly when the
dependency is already under stress (startup, or a recovery after an outage), and
a thundering herd of probes is the worst possible thing to send it.

Crucially, the callers that *lose* the race do not queue on the lock — they are
answered immediately from the last known verdict (stale-while-revalidate), or
with a fail-closed "unavailable" when there is no verdict yet. Queueing was the
original shape and it is unsafe, because the connection this probe makes is not
time-bounded: ``psycopg.connect`` inherits the OS TCP timeout (~130 s of SYN
retries against a blackholed host), and the DSN carries no ``connect_timeout``
(adding one belongs to the shared connection factory, which every business read
adapter also uses — out of this module's blast radius). With queueing, one hung
dependency pins one server thread **per concurrent probe request** until it
gives up; the endpoint is auth-exempt and polled by every orchestrator, so the
FastAPI sync threadpool is exhausted long before the connect fails and the
*business* API stops serving. A readiness probe must never be able to take down
the process whose readiness it reports. Non-blocking acquisition bounds the
damage of a hung dependency to exactly one thread — the one doing the probe.

The orchestrator still learns the truth: the losers are served ``unavailable``
(cold) or the previous verdict (warm), and the in-flight probe eventually
records the real one.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Mapping, Optional

from fcc_test_contracts.common.health_probe_policy import (
    READINESS_CACHE_TTL_SECONDS,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    ReadinessSnapshot,
)
from fcc_test_contracts.common.logging_channel import get_logger


__all__ = ['ReadinessService']


logger = get_logger('platform_api')


class ReadinessService:
    """Evaluate declared dependency probes and cache the verdict for ``ttl``."""

    def __init__(
        self,
        dependency_probes: Mapping[str, Callable[[], None]],
        *,
        clock: Optional[Callable[[], float]] = None,
        ttl: float = READINESS_CACHE_TTL_SECONDS,
    ) -> None:
        """
        Args:
            dependency_probes: dependency name → callable that returns normally
                when reachable and raises otherwise. A *callable* rather than a
                port object because that is the entire contract — the adapters
                that satisfy it (``PostgresCentralHealthAdapter.ping``) stay
                free to live wherever their connection factory does.
            clock: monotonic seconds source (injected for tests). Monotonic, not
                wall clock, so an NTP step cannot freeze or expire the cache.
            ttl: how long a verdict stays authoritative. Defaults to the domain
                SSOT; a ``ttl`` of 0 disables caching (every call re-probes).
        """
        self._probes = dict(dependency_probes)
        self._clock = clock or time.monotonic
        self._ttl = float(ttl)
        # CLAUDE.md: every shared cache is lock-guarded.
        self._lock = threading.Lock()
        self._snapshot: Optional[ReadinessSnapshot] = None

    @property
    def dependency_names(self) -> tuple[str, ...]:
        """Declared dependency names, in declaration order."""
        return tuple(self._probes)

    def check(self) -> ReadinessSnapshot:
        """Return a fresh-enough readiness verdict, probing only when stale.

        Never blocks on another caller's in-flight probe — see the module
        docstring for why queueing here can take the whole API process down.
        """
        if not self._lock.acquire(blocking=False):
            # Another thread is probing. Reading ``_snapshot`` outside the lock
            # is safe: it is a single attribute load of an immutable, fully
            # constructed frozen dataclass, so a racing writer can only make us
            # read the previous verdict or the new one — never a torn value.
            return self._contended_verdict()
        try:
            now = float(self._clock())
            cached = self._snapshot
            if cached is not None and cached.is_fresh(now=now, ttl=self._ttl):
                return cached
            snapshot = ReadinessSnapshot(
                dependencies=self._probe_all(), checked_at=now,
            )
            self._snapshot = snapshot
            return snapshot
        finally:
            self._lock.release()

    def _contended_verdict(self) -> ReadinessSnapshot:
        """The answer for a caller that lost the single-flight race.

        Warm cache → the last known verdict (stale-while-revalidate: a verdict
        from a few seconds ago is far better than a stalled request). Cold cache
        → fail closed, every declared dependency ``unavailable``. Fail-closed is
        the correct default for readiness: "nothing has confirmed I can serve
        yet" must mean *stop routing to me*, which is exactly the state of a
        container still starting up.

        The provisional verdict is deliberately NOT cached — it is an answer
        about our own ignorance, not an observation of the dependency, and
        storing it would let it be served as fresh after the real probe lands.
        """
        cached = self._snapshot
        if cached is not None:
            return cached
        return ReadinessSnapshot(
            dependencies={name: STATUS_UNAVAILABLE for name in self._probes},
            checked_at=float(self._clock()),
        )

    def invalidate(self) -> None:
        """Drop the cached verdict (used by tests and by runtime disposal)."""
        with self._lock:
            self._snapshot = None

    def _probe_all(self) -> dict:
        statuses = {}
        for name, probe in self._probes.items():
            try:
                probe()
            except Exception as exc:  # noqa: BLE001 — any failure = unavailable
                # The reason is logged (operators need it) but never returned:
                # the exception text of a connection failure embeds host/user,
                # and the readiness body is unauthenticated.
                logger.warning(
                    'platform readiness dependency unavailable: %s (%s)', name, exc,
                )
                statuses[name] = STATUS_UNAVAILABLE
            else:
                statuses[name] = STATUS_OK
        return statuses
