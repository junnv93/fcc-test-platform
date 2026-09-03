"""Typed runtime configuration for the headless API entrypoint.

F-2-D4 (2026-05-24): the 9 auth fields are embedded via composition with
``HttpAuthConfig`` (``application/common/auth_config.py``) so Session/Headless
share a single auth dataclass shape. Env-var prefixes still differ.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Mapping

from fcc_test_contracts.common.auth_config import HttpAuthConfig
from fcc_test_contracts.common.env_loaders import read_paths, read_text
from fcc_test_contracts.common.rate_limit_config import (
    load_rate_limit_policy,
    rate_limit_env_map,
)
from fcc_test_contracts.common.rate_limit_policy import RateLimitPolicy


__all__ = [
    'FCC_HEADLESS_PLATFORM_API_BASE_URL_ENV',
    'FCC_HEADLESS_AUTH_ENV_PREFIX',
    'HEADLESS_API_ENV',
    'HeadlessApiConfig',
]


FCC_HEADLESS_AUTH_ENV_PREFIX = 'FCC_HEADLESS_'
FCC_HEADLESS_PLATFORM_API_BASE_URL_ENV = 'FCC_HEADLESS_PLATFORM_API_BASE_URL'

_NON_AUTH_HEADLESS_API_ENV: dict[str, str] = {
    'db_path': 'FCC_HEADLESS_DB_PATH',
    'screenshot_root': 'FCC_HEADLESS_SCREENSHOT_ROOT',
    'artifact_roots': 'FCC_HEADLESS_ARTIFACT_ROOTS',
    'template_dir': 'FCC_HEADLESS_TEMPLATE_DIR',
    'report_output_dir': 'FCC_HEADLESS_REPORT_OUTPUT_DIR',
    'app_title': 'FCC_HEADLESS_APP_TITLE',
    'app_version': 'FCC_HEADLESS_APP_VERSION',
    'download_signing_secret': 'FCC_HEADLESS_DOWNLOAD_SIGNING_SECRET',
    'platform_api_base_url': FCC_HEADLESS_PLATFORM_API_BASE_URL_ENV,
}

# Public SSOT: non-auth keys (including the platform gateway) + auth keys
# derived from HttpAuthConfig + rate-limit keys derived from the shared
# rate_limit_config SSOT (2026-07-19).
HEADLESS_API_ENV: dict[str, str] = {
    **_NON_AUTH_HEADLESS_API_ENV,
    **HttpAuthConfig.env_keys(FCC_HEADLESS_AUTH_ENV_PREFIX),
    **rate_limit_env_map(FCC_HEADLESS_AUTH_ENV_PREFIX),
}


@dataclass(frozen=True)
class HeadlessApiConfig:
    """Runtime settings for the headless web/API composition path."""

    db_path: str
    screenshot_root: str = ''
    artifact_roots: tuple[str, ...] = ()
    template_dir: str = ''
    report_output_dir: str = ''
    app_title: str = 'FCC Headless API'
    app_version: str = ''
    # FE-P6-DL — HMAC secret for self-authorizing report download tokens.
    # Injected via env (never hardcoded); empty disables the download grant
    # route (the route fails loud rather than issuing unsigned tokens).
    download_signing_secret: str = ''
    auth: HttpAuthConfig = field(default_factory=HttpAuthConfig)
    #: Inbound throttle (2026-07-19). ``from_env`` builds an ENABLED policy when
    #: the env vars are unset — secure by default; the kill-switch is explicit
    #: (``FCC_HEADLESS_RATE_LIMIT_ENABLED=0``). A directly-constructed config
    #: (unit tests / embedders) gets the same SSOT defaults.
    rate_limit: RateLimitPolicy = field(default_factory=RateLimitPolicy)
    #: Authenticated platform gateway used for project-scoped RBAC reads. The
    #: headless process is deliberately not on the central PostgreSQL network.
    platform_api_base_url: str = ''

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> 'HeadlessApiConfig':
        env = os.environ if environ is None else environ
        db_path = read_text(env, _NON_AUTH_HEADLESS_API_ENV['db_path'])
        if not db_path:
            raise ValueError(f"{_NON_AUTH_HEADLESS_API_ENV['db_path']} is required")
        defaults = cls.__dataclass_fields__
        return cls(
            db_path=db_path,
            screenshot_root=read_text(env, _NON_AUTH_HEADLESS_API_ENV['screenshot_root']),
            artifact_roots=read_paths(env, _NON_AUTH_HEADLESS_API_ENV['artifact_roots']),
            template_dir=read_text(env, _NON_AUTH_HEADLESS_API_ENV['template_dir']),
            report_output_dir=read_text(env, _NON_AUTH_HEADLESS_API_ENV['report_output_dir']),
            app_title=(
                read_text(env, _NON_AUTH_HEADLESS_API_ENV['app_title'])
                or defaults['app_title'].default
            ),
            app_version=read_text(env, _NON_AUTH_HEADLESS_API_ENV['app_version']),
            download_signing_secret=read_text(
                env, _NON_AUTH_HEADLESS_API_ENV['download_signing_secret']
            ),
            auth=HttpAuthConfig.from_env(env, prefix=FCC_HEADLESS_AUTH_ENV_PREFIX),
            rate_limit=load_rate_limit_policy(env, prefix=FCC_HEADLESS_AUTH_ENV_PREFIX),
            platform_api_base_url=read_text(
                env, _NON_AUTH_HEADLESS_API_ENV['platform_api_base_url']
            ),
        )

    def app_options(self) -> dict:
        options = {'title': self.app_title}
        if self.app_version:
            options['version'] = self.app_version
        return options

    def auth_options(self) -> dict:
        return self.auth.as_options()
