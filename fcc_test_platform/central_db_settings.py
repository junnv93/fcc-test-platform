"""Central PostgreSQL **설정** SSOT — 드라이버를 모르는 쪽 (FE-P0c WIRE, 2026-05-26).

중앙 Platform 프로세스가 「DB 가 어디 있고 어느 provider 신원이 행을 찍는가」를
하드코딩 없이 읽는 단일 출처다. 챔버 HTTP 동기화 설정은
``application.session.central_sync_config`` 에 있다 — Session Node 가 전송 조립을
import 했다는 이유만으로 데이터베이스 URL 을 얻지 못하게.

여기서 지키는 설계 제약:

1. **하드코딩된 DSN 없음** — 연결 문자열은 ``FCC_CENTRAL_DB_URL`` 에서만 온다.
   폴백 리터럴(``postgresql://localhost/...``)이 없다. URL 부재는 ``enabled = False``
   로 드러나 조립 루트가 죽은 sync 러너를 크게 거부할 수 있다.
2. **드라이버를 모른다** — DSN *파싱*은 stdlib ``urllib.parse`` 만 쓴다. 이 모듈은
   ``psycopg`` 를 함수 안에서조차 import 하지 않는다. 연결 «생성»은
   ``infrastructure.adapters.driven.central_db_connection`` 이 갖는다.
3. **Env-var SSOT** — ``FCC_CENTRAL_*`` 네임스페이스는 기존
   ``FCC_HEADLESS_*`` / ``FCC_SESSION_*`` 관례를 따르고 env 로더를 재사용한다.

■ 왜 ``central_db_config`` 에서 갈라져 나왔나 (설계서 S3, 2026-09-05)

한 모듈이 설정과 드라이버를 함께 들고 있어서, ``application.runtime_config`` 가
``CentralDbConfig`` 하나를 읽는 것만으로 ``psycopg`` 에 정적으로 닿았다
(``app-no-db`` 계약 위반 ②). 그 사슬을 끊는 방법은 **분리**뿐이다 —
설정을 읽는 쪽은 여기, 연결을 만드는 쪽은 driven 어댑터.

⚠️ **이 모듈에 드라이버 import 를 다시 넣지 마라.** 넣는 순간
``application.runtime_config -> central_db_settings -> psycopg`` 로 같은 위반이
되살아난다. import-linter 의 ``app-no-db`` 계약이 이름을 대고 멈춘다.

공표된 이름은 ``central_db_config`` 이 그대로 다시 내보낸다 — 외부 소비자
(모노레포 ``src/progress_expectation_sync_composition.py``)는 이 분리를 보지 못한다.
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
