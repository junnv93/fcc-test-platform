"""Central PostgreSQL reachability probe (2026-07-20).

The readiness probe needs to answer one question — "can this process still reach
the central DB?" — and it must answer it **cheaply**, because an auth-exempt
endpoint that runs a real query is a load source pointed straight at the
dependency it is reporting on.

``PostgresCentralHealthAdapter.ping`` therefore does the minimum that actually
proves reachability end to end: open a connection through the injected factory,
execute ``SELECT 1``, close. That touches the TCP path, the authentication
handshake and the query path (a connection-only check would pass against a
Postgres that has run out of backends mid-startup), while reading no table, no
view and no catalog — so it cannot be slowed down by data volume, a lock, or a
materialized-view refresh.

Design mirrors :class:`~application.platform.central_read_adapter.PostgresCentralReadAdapter`:

- **injected ``connection_factory``** (``() -> DbConnection``) — this module
  never imports a PostgreSQL driver (frozen-exe safety), and tests inject a
  SQLite/fake factory.
- **loud-fail** — any failure raises ``CentralReadError``. The readiness service
  is the single place that converts that into an ``unavailable`` status, so a
  connection error message (which embeds host and user) never reaches a caller.
"""
from __future__ import annotations

from typing import Callable

from fcc_test_platform.domain.ports.output.central_read_port import CentralReadError
from fcc_test_kernel.domain.ports.output.platform_database_port import DbConnection


__all__ = ['CENTRAL_PING_SQL', 'PostgresCentralHealthAdapter']


#: The cheapest statement that proves the full connect→auth→execute path. No
#: table, no catalog, no planner work — constant cost regardless of DB size.
CENTRAL_PING_SQL = 'SELECT 1'


class PostgresCentralHealthAdapter:
    """Reachability probe for the central DB behind ``connection_factory``."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        self._connection_factory = connection_factory

    def ping(self) -> None:
        """Return normally when the central DB answered; raise otherwise.

        Raises:
            CentralReadError: connection or query failure (wrapped so callers
                never have to know which driver raised what).
        """
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 — wrap as loud CentralReadError
            raise CentralReadError(f'central health connection failed: {exc}') from exc
        try:
            cursor = connection.cursor()
            try:
                # ``()`` keeps the DB-API 2-arg form the DbCursor port declares,
                # matching how every other central adapter calls execute.
                cursor.execute(CENTRAL_PING_SQL, ())
            finally:
                cursor.close()
        except Exception as exc:  # noqa: BLE001 — wrap as loud CentralReadError
            raise CentralReadError(f'central health query failed: {exc}') from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()
