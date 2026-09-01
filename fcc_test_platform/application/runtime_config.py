"""Platform read API runtime config (FE-P0d, 2026-05-27).

Composes the two env-sourced settings the platform read surface needs:

- ``central`` — where the central DB lives (``CentralDbConfig``, ``FCC_CENTRAL_*``
  namespace, reused so the read API and the write/sync path point at the same
  database from one SSOT).
- ``auth`` — HTTP auth for the platform surface (``HttpAuthConfig``,
  ``FCC_PLATFORM_*`` namespace, mirroring ``FCC_SESSION_`` / ``FCC_HEADLESS_``).

dependency-free: no FastAPI / PySide6 / sqlalchemy / psycopg imports (psycopg is
bound lazily in the composition root only).
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping, Optional

from fcc_test_contracts.common.auth_config import HttpAuthConfig
from fcc_test_contracts.common.env_loaders import read_bool
from fcc_test_contracts.common.rate_limit_config import (
    load_rate_limit_policy,
    rate_limit_env_map,
)
from fcc_test_platform.central_db_config import CentralDbConfig
from domain.services.chamber_proxy_policy import ChamberProxyPolicy
from fcc_test_contracts.common.rate_limit_policy import RateLimitPolicy


__all__ = [
    'PLATFORM_ALLOW_INSECURE_ENV',
    'PLATFORM_RATE_LIMIT_ENV',
    'PLATFORM_AUTH_ENV_PREFIX',
    'PLATFORM_CHAMBER_PROXY_ENV',
    'PLATFORM_NODE_CREDENTIAL_ENV',
    'NodeMachineCredential',
    'PlatformApiConfig',
]


#: Env-var prefix for the platform surface auth fields (mirrors FCC_SESSION_ /
#: FCC_HEADLESS_). e.g. FCC_PLATFORM_AUTH_MODE, FCC_PLATFORM_OIDC_ISSUER.
PLATFORM_AUTH_ENV_PREFIX = 'FCC_PLATFORM_'

#: Explicit, audited dev-only escape hatch to run the platform read API WITHOUT
#: auth. The platform surface serves cross-engineer central coverage/claims, so
#: it is secure-by-default: the composition root refuses to build when auth is
#: disabled unless this flag is set (loud warning when it is). Production must
#: set FCC_PLATFORM_AUTH_MODE instead.
PLATFORM_ALLOW_INSECURE_ENV = 'FCC_PLATFORM_ALLOW_INSECURE'

#: Env-var names for the central→node measurement proxy forward policy (timeout +
#: transient retry backoff). Single mapping SSOT so the env→typed read and any
#: parity audit reference the same names (mirrors central_heartbeat_config's
#: FCC_CENTRAL_ENV). Values flow verbatim into ``ChamberProxyPolicy.from_raw``,
#: which owns coercion + fallback (garbage/unset → SSOT default — no busy/zero
#: policy). Tuning the forward timeout/retry needs no edit-and-rebuild.
PLATFORM_CHAMBER_PROXY_ENV: dict = {
    'timeout_seconds': 'FCC_PLATFORM_CHAMBER_PROXY_TIMEOUT_SECONDS',
    'max_retries': 'FCC_PLATFORM_CHAMBER_PROXY_MAX_RETRIES',
    'base_delay_seconds': 'FCC_PLATFORM_CHAMBER_PROXY_BASE_DELAY_SECONDS',
    'max_delay_seconds': 'FCC_PLATFORM_CHAMBER_PROXY_MAX_DELAY_SECONDS',
}


#: Env-var names for the credential central presents to a chamber node
#: (운영자 판정 2026-09-01: **기계 신분증**, 사용자 토큰 위임이 아니다).
#:
#: ⚠️ This is the OPPOSITE direction from ``FCC_CENTRAL_*`` on a node. A node uses
#: those to talk to central; central uses THESE to talk to a node. Two directions,
#: two credentials — reusing one name for both would make "which side is
#: misconfigured" unanswerable from the env alone.
PLATFORM_NODE_CREDENTIAL_ENV: dict = {
    'token_url': 'FCC_PLATFORM_NODE_OIDC_TOKEN_URL',
    'client_id': 'FCC_PLATFORM_NODE_CLIENT_ID',
    'client_secret': 'FCC_PLATFORM_NODE_CLIENT_SECRET',
}


@dataclass(frozen=True)
class NodeMachineCredential:
    """client_credentials inputs for the central→node hop.

    ``is_configured`` is all three present. Partial configuration is NOT treated
    as configured: a token URL without a secret would make every node call fail
    at token acquisition, and the operator would read that as "the node is
    down". Unset-and-silent is the honest state — the node then answers
    ``403 missing_permission`` and the cause is named on the node side.
    """

    token_url: str = ''
    client_id: str = ''
    client_secret: str = ''

    @property
    def is_configured(self) -> bool:
        return bool(
            self.token_url.strip()
            and self.client_id.strip()
            and self.client_secret.strip()
        )


#: Rate-limit env names for this surface, derived from the shared
#: ``rate_limit_config`` suffix SSOT (2026-07-19). e.g.
#: FCC_PLATFORM_RATE_LIMIT_ENABLED / _REQUESTS / _WINDOW_SECONDS.
PLATFORM_RATE_LIMIT_ENV: dict = rate_limit_env_map(PLATFORM_AUTH_ENV_PREFIX)


@dataclass(frozen=True)
class PlatformApiConfig:
    central: CentralDbConfig
    auth: HttpAuthConfig
    allow_insecure: bool = False
    #: Central→node measurement proxy forward policy. Defaults to the SSOT
    #: singleton (prior hard-coded timeout, transient retry on idempotent reads).
    #: The composition root injects this into ``HttpChamberProxyAdapter``.
    chamber_proxy_policy: ChamberProxyPolicy = ChamberProxyPolicy()
    #: Machine credential the composition root turns into the proxy's
    #: ``token_supplier``. Empty by default so an unconfigured deployment keeps
    #: the pre-2026-09-01 behaviour (no Authorization header) byte-identical.
    node_credential: NodeMachineCredential = NodeMachineCredential()
    #: Inbound throttle (2026-07-19). ``from_env`` yields an ENABLED policy when
    #: unset — the platform surface is the internet-adjacent one (central hub),
    #: so it is secure by default; ``FCC_PLATFORM_RATE_LIMIT_ENABLED=0`` opts out.
    rate_limit: RateLimitPolicy = RateLimitPolicy()

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> 'PlatformApiConfig':
        env = os.environ if environ is None else environ
        return cls(
            central=CentralDbConfig.from_env(env),
            auth=HttpAuthConfig.from_env(env, prefix=PLATFORM_AUTH_ENV_PREFIX),
            allow_insecure=read_bool(env, PLATFORM_ALLOW_INSECURE_ENV, default=False),
            node_credential=NodeMachineCredential(
                token_url=str(env.get(PLATFORM_NODE_CREDENTIAL_ENV['token_url']) or ''),
                client_id=str(env.get(PLATFORM_NODE_CREDENTIAL_ENV['client_id']) or ''),
                client_secret=str(
                    env.get(PLATFORM_NODE_CREDENTIAL_ENV['client_secret']) or ''
                ),
            ),
            # env raw strings → policy SSOT parser (coercion + per-field fallback
            # live in ChamberProxyPolicy.from_raw — no read_float/int duplication).
            chamber_proxy_policy=ChamberProxyPolicy.from_raw(
                timeout_seconds=env.get(PLATFORM_CHAMBER_PROXY_ENV['timeout_seconds']),
                max_retries=env.get(PLATFORM_CHAMBER_PROXY_ENV['max_retries']),
                base_delay_seconds=env.get(
                    PLATFORM_CHAMBER_PROXY_ENV['base_delay_seconds']
                ),
                max_delay_seconds=env.get(
                    PLATFORM_CHAMBER_PROXY_ENV['max_delay_seconds']
                ),
            ),
            rate_limit=load_rate_limit_policy(env, prefix=PLATFORM_AUTH_ENV_PREFIX),
        )

    def app_options(self) -> dict:
        """FastAPI app metadata (title/version) for the platform surface."""
        from application.central_contract.api_contracts import PLATFORM_API_CONTRACT_VERSION
        from fcc_test_platform.application.api_schema import PLATFORM_API_TITLE

        return {'title': PLATFORM_API_TITLE, 'version': PLATFORM_API_CONTRACT_VERSION}
