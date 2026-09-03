"""Central PostgreSQL connection policy SSOT (FE-P0c WIRE, 2026-05-26).

The central Platform process needs a single, hardcoding-free source of truth
for where its database lives and which provider identity stamps ingested rows.
This module owns the central-only DSN contract. Chamber HTTP sync settings live
in ``application.session.central_sync_config`` so a Session Node cannot acquire
a database URL merely by importing its transport composition.

Design constraints honoured here:

1. **No hardcoded DSN** — the connection string comes from
   ``FCC_CENTRAL_DB_URL`` only. There is no fallback literal
   (``postgresql://localhost/...``); a missing URL surfaces as
   ``enabled = False`` so the composition root can loudly refuse to wire a
   dead sync runner.
2. **frozen-exe safe** — this module never imports ``psycopg``. DSN *parsing*
   is done with the stdlib ``urllib.parse`` only, so a PyInstaller / Nuitka
   desktop build that never composes the central Platform path pulls in zero
   PostgreSQL driver code. The concrete ``psycopg.connect`` remains behind the
   central Platform composition boundary.
3. **Env-var SSOT** — the ``FCC_CENTRAL_*`` namespace mirrors the existing
   ``FCC_HEADLESS_*`` / ``FCC_SESSION_*`` runtime-config convention and the env
   loaders are reused (``application.common.env_loaders``) so coercion rules
   stay consistent across web surfaces.

dependency-free: no FastAPI / PySide6 / openpyxl / pandas / sqlalchemy /
psycopg / sqlite3 imports — enforced by ``tests/test_central_db_config.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping, Optional
from urllib.parse import urlsplit

from fcc_test_contracts.common.env_loaders import read_int, read_text
from fcc_test_contracts.common.central_sync_config import (
    CENTRAL_RESULT_SYNC_ENV,
    CENTRAL_SYNC_DEFAULT_BATCH_LIMIT,
    DEFAULT_POLL_INTERVAL_SECONDS,
    CentralHttpSyncConfig,
    coerce_poll_interval,
)
from fcc_test_kernel.domain.services.outbox_retry_policy import OutboxRetryPolicy


__all__ = [
    'CENTRAL_DB_ENV',
    'CENTRAL_SYNC_DEFAULT_BATCH_LIMIT',
    'DEFAULT_POLL_INTERVAL_SECONDS',
    'CentralDbConfig',
    'CentralHttpSyncConfig',
    'build_central_db_connection_factory',
    'ParsedCentralDsn',
    'parse_central_dsn',
]


# Accepted URL schemes for the central database. PostgreSQL libpq URIs use
# both spellings interchangeably; psycopg accepts either as a conninfo URL.
_ACCEPTED_DSN_SCHEMES = frozenset({'postgresql', 'postgres'})

#: Public SSOT: env-var names for the central-sync runtime. One dict so callers
#: and tests reference names symbolically (no scattered string literals).
CENTRAL_DB_ENV: dict[str, str] = {
    'database_url': 'FCC_CENTRAL_DB_URL',
    # The chamber-side mapping now carries the outbox retry knobs too (moved
    # 2026-08-29): their env names were always ``FCC_CENTRAL_SYNC_*`` and a node
    # had to import THIS module — the central DSN namespace — merely to read
    # them. This spread keeps CENTRAL_DB_ENV byte-identical across that move.
    **CENTRAL_RESULT_SYNC_ENV,
}


@dataclass(frozen=True)
class ParsedCentralDsn:
    """Validated, password-redacted view of the central DSN.

    The full conninfo URL is preserved verbatim in ``CentralDbConfig.database_url``
    for the driver; this parsed view exists for validation + observability
    (logs / diagnostics MUST never leak the password, so ``__repr__`` omits it).
    """

    scheme: str
    host: str
    port: Optional[int]
    dbname: str
    user: str

    def safe_descriptor(self) -> str:
        """Human-readable target with no secret material (for logs)."""
        host = self.host or '?'
        port = f':{self.port}' if self.port else ''
        dbname = f'/{self.dbname}' if self.dbname else ''
        user = f'{self.user}@' if self.user else ''
        return f'{self.scheme}://{user}{host}{port}{dbname}'


def parse_central_dsn(url: str) -> ParsedCentralDsn:
    """Parse + validate a central DSN. Loud-fail on a non-PostgreSQL scheme.

    Raises ``ValueError`` for an empty URL or an unsupported scheme so a
    misconfigured deployment fails at composition time rather than at the first
    ingestion (fail-fast over silent dead sync).
    """
    text = str(url or '').strip()
    if not text:
        raise ValueError('central DSN is empty')
    split = urlsplit(text)
    scheme = (split.scheme or '').lower()
    if scheme not in _ACCEPTED_DSN_SCHEMES:
        raise ValueError(
            f'unsupported central DSN scheme {scheme!r} — expected one of '
            f'{sorted(_ACCEPTED_DSN_SCHEMES)} (e.g. postgresql://host/dbname)'
        )
    dbname = split.path.lstrip('/') if split.path else ''
    return ParsedCentralDsn(
        scheme=scheme,
        host=split.hostname or '',
        port=split.port,
        dbname=dbname,
        user=split.username or '',
    )


def build_central_db_connection_factory(database_url: str):
    """Build the central-only lazy PostgreSQL connection factory.

    The chamber result transport never imports this helper. Central Platform
    and the separate progress-expectation sync use this one boundary so a
    private factory cannot drift or disappear when chamber transport changes.
    """
    if not database_url:
        raise ValueError('database_url is required to build a connection factory')
    import psycopg

    def _connect():
        return psycopg.connect(database_url)

    return _connect


@dataclass(frozen=True)
class CentralDbConfig:
    """Runtime settings for the central PostgreSQL ingestion sync path.

    ``database_url`` empty ⇒ ``enabled = False`` ⇒ the composition root refuses
    to build a sync runner (no silent no-op runner that swallows outbox events).
    """

    database_url: str = ''
    provider_id: str = ''
    batch_limit: int = CENTRAL_SYNC_DEFAULT_BATCH_LIMIT
    #: Periodic drainer interval (seconds). 0 = disabled (cycle-end trigger only).
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    #: Outbox retry policy knobs (raw — empty string = use the domain SSOT
    #: default for that field). Resolved to a typed ``OutboxRetryPolicy`` by the
    #: ``outbox_retry_policy`` property below (env → typed lives here, not in the
    #: pure domain policy).
    retry_max_retries: str = ''
    retry_base_delay_seconds: str = ''
    retry_max_delay_seconds: str = ''

    @property
    def enabled(self) -> bool:
        """True when a central DSN is configured (sync is wired)."""
        return bool(self.database_url)

    @property
    def scheduler_enabled(self) -> bool:
        """True when the periodic drainer should run (interval > 0)."""
        return self.poll_interval_seconds > 0

    @property
    def outbox_retry_policy(self) -> OutboxRetryPolicy:
        """Typed retry policy resolved from the raw env knobs.

        Unset / garbage / non-positive each field → the domain SSOT default
        (``OutboxRetryPolicy.from_raw``), so the default config yields the
        default policy (byte-identical to the store's built-in default).
        """
        return OutboxRetryPolicy.from_raw(**self._sync_config.outbox_retry_knobs)

    @property
    def _sync_config(self) -> CentralHttpSyncConfig:
        """This config's chamber-side view of the shared sync settings.

        The knob→field mapping is named **once**, in
        :attr:`CentralHttpSyncConfig.outbox_retry_knobs`. Two readers of one SSOT
        that each spell the mapping out would drift on which env value feeds which
        field, and nothing would fail until the retry behaviour was already wrong.
        """
        return CentralHttpSyncConfig(
            provider_id=self.provider_id,
            batch_limit=self.batch_limit,
            poll_interval_seconds=self.poll_interval_seconds,
            retry_max_retries=self.retry_max_retries,
            retry_base_delay_seconds=self.retry_base_delay_seconds,
            retry_max_delay_seconds=self.retry_max_delay_seconds,
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> 'CentralDbConfig':
        env = os.environ if environ is None else environ
        return cls(
            database_url=read_text(env, CENTRAL_DB_ENV['database_url']),
            provider_id=read_text(env, CENTRAL_DB_ENV['provider_id']),
            batch_limit=read_int(
                env,
                CENTRAL_DB_ENV['batch_limit'],
                default=CENTRAL_SYNC_DEFAULT_BATCH_LIMIT,
            ),
            # NB: 0 (and negative) are *valid* here — they disable the periodic
            # drainer (cycle-end only). The shared ``read_int`` rejects <= 0, so
            # this field is coerced locally: unset / garbage → default, an
            # explicit non-positive value → 0 (disabled).
            poll_interval_seconds=coerce_poll_interval(
                read_text(env, CENTRAL_DB_ENV['poll_interval_seconds']),
            ),
            retry_max_retries=read_text(env, CENTRAL_DB_ENV['retry_max_retries']),
            retry_base_delay_seconds=read_text(
                env, CENTRAL_DB_ENV['retry_base_delay_seconds']
            ),
            retry_max_delay_seconds=read_text(
                env, CENTRAL_DB_ENV['retry_max_delay_seconds']
            ),
        )

    def parsed_dsn(self) -> ParsedCentralDsn:
        """Validated DSN view. Raises ``ValueError`` when ``database_url`` is
        empty/invalid — call only when ``enabled`` is True."""
        return parse_central_dsn(self.database_url)
