"""Typed runtime configuration for the Session API entrypoint.

Mirrors the ``HeadlessApiConfig`` pattern (``application/headless/runtime_config.py``)
but exposes a separate ``FCC_SESSION_*`` env namespace because the Session API
composes a measurement ``TestRunner`` instead of a DB-only API surface.

F-2-D4 (2026-05-24): the 9 auth fields are now embedded via composition with
``HttpAuthConfig`` (``application/common/auth_config.py``) so Session/Headless
share a single auth dataclass shape. Env-var prefixes still differ.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Mapping

from fcc_test_contracts.common.auth_config import HttpAuthConfig
from fcc_test_contracts.common.env_loaders import (
    read_bool,
    read_csv,
    read_float,
    read_int,
    read_text,
)
from fcc_test_contracts.common.operator_subject import LOCAL_GUI_OPERATOR_SUBJECT
from fcc_test_contracts.common.rate_limit_config import (
    load_rate_limit_policy,
    rate_limit_env_map,
)
from fcc_test_contracts.common.rate_limit_policy import RateLimitPolicy
from fcc_test_kernel.domain.services.workbook_upload_policy import (
    DEFAULT_MAX_WORKBOOK_UPLOAD_BYTES,
    DEFAULT_WORKBOOK_RETENTION_SECONDS,
    ENV_MAX_WORKBOOK_UPLOAD_BYTES,
    ENV_WORKBOOK_RETENTION_SECONDS,
    resolve_max_upload_bytes,
    resolve_workbook_retention_seconds,
    workbook_upload_root_parts,
)


__all__ = [
    'DEFAULT_SESSION_NODE_HOST',
    'DEFAULT_SESSION_NODE_PORT',
    'DEFAULT_SESSION_NODE_READINESS_TIMEOUT_SECONDS',
    'DEFAULT_SESSION_NODE_READINESS_INTERVAL_SECONDS',
    'FCC_SESSION_AUTH_ENV_PREFIX',
    'FCC_SESSION_ENV',
    'SessionApiConfig',
]


FCC_SESSION_AUTH_ENV_PREFIX = 'FCC_SESSION_'

# Session Node listener defaults. The approved chamber listener port is declared
# exactly once here; launchers/build scripts consume the typed config instead of
# copying an operational port literal.
DEFAULT_SESSION_NODE_HOST = '0.0.0.0'
DEFAULT_SESSION_NODE_PORT = 9000
DEFAULT_SESSION_NODE_READINESS_TIMEOUT_SECONDS = 60.0
DEFAULT_SESSION_NODE_READINESS_INTERVAL_SECONDS = 0.5

_NON_AUTH_FCC_SESSION_ENV: dict[str, str] = {
    'excel_path': 'FCC_SESSION_EXCEL_PATH',
    'switchbox_enabled': 'FCC_SESSION_SWITCHBOX_ENABLED',
    'manual_bt_call': 'FCC_SESSION_MANUAL_BT_CALL',
    'device_less': 'FCC_SESSION_DEVICE_LESS',
    'ble_minimal_reconfig': 'FCC_SESSION_BLE_MINIMAL_RECONFIG',
    'app_title': 'FCC_SESSION_APP_TITLE',
    'app_version': 'FCC_SESSION_APP_VERSION',
    'event_buffer_size': 'FCC_SESSION_EVENT_BUFFER_SIZE',
    # Fix #5 (2026-05-24): historical FCC_SESSION_ATTACH_QT_BRIDGE 영구 폐기.
    # 기본 bridge 는 F-2-D2 이후 PySide6-free ``CallbackEventBridge`` — 이름이
    # 동작과 다른 부정확성을 해소. backwards-compat alias 0 (사용자 명시 "옛 API 금지").
    'attach_event_bridge': 'FCC_SESSION_ATTACH_EVENT_BRIDGE',
    'cors_origins': 'FCC_SESSION_CORS_ORIGINS',
    'cors_allow_credentials': 'FCC_SESSION_CORS_ALLOW_CREDENTIALS',
    'ws_heartbeat_seconds': 'FCC_SESSION_WS_HEARTBEAT_SECONDS',
    # FE-P0b (2026-05-25): deployment 별 station operator (measurement_attempts.
    # recorded_by provenance). 미설정 시 LOCAL_GUI_OPERATOR_SUBJECT 폴백.
    'operator': 'FCC_SESSION_OPERATOR',
    # Writable state/log root. The operator package stays immutable; the
    # entrypoint changes into this directory before composing the runtime so
    # legacy relative SQLite/log paths resolve outside the package.
    'runtime_dir': 'FCC_SESSION_RUNTIME_DIR',
    # plot-dual-custody ① — 측정 시작 요청이 지정할 수 있는 저장 루트 허용 목록(csv).
    # 미설정 = override 거부. 이 값은 곧바로 파일 쓰기의 루트가 되므로 기본 허용은
    # 임의 경로 쓰기 능력이 된다.
    'allowed_storage_roots': 'FCC_SESSION_ALLOWED_STORAGE_ROOTS',
    # Ceiling for an uploaded workbook, in bytes. Unset/blank/garbage/non-positive
    # all resolve to the policy default — there is deliberately no spelling for
    # "unlimited" (see ``workbook_upload_policy.resolve_max_upload_bytes``).
    'max_workbook_upload_bytes': ENV_MAX_WORKBOOK_UPLOAD_BYTES,
    # How long an uploaded workbook nothing references is kept, in seconds.
    # Same coercion rules as the ceiling above, deliberately — two knobs on one
    # config object that disagree about what a blank value means are where the
    # next defect lives. There is likewise no "never sweep" spelling.
    'workbook_retention_seconds': ENV_WORKBOOK_RETENTION_SECONDS,
    # Dedicated Session Node process settings. These are kept in the Session
    # namespace so the GUI's legacy entrypoint does not acquire a listener.
    'node_host': 'FCC_SESSION_NODE_HOST',
    'node_port': 'FCC_SESSION_NODE_PORT',
    'node_readiness_timeout_seconds': 'FCC_SESSION_NODE_READINESS_TIMEOUT_SECONDS',
    'node_readiness_interval_seconds': 'FCC_SESSION_NODE_READINESS_INTERVAL_SECONDS',
}

# Public SSOT: non-auth keys + 9 auth keys derived from HttpAuthConfig + the
# rate-limit keys derived from the shared rate_limit_config SSOT (2026-07-19).
FCC_SESSION_ENV: dict[str, str] = {
    **_NON_AUTH_FCC_SESSION_ENV,
    **HttpAuthConfig.env_keys(FCC_SESSION_AUTH_ENV_PREFIX),
    **rate_limit_env_map(FCC_SESSION_AUTH_ENV_PREFIX),
}


@dataclass(frozen=True)
class SessionApiConfig:
    """Runtime settings for the session web/API composition path."""

    #: Optional since 2026-08-18. A node has no reason to require a workbook to
    #: boot: the plan arrives by ``published_plan_id``, reference values by the
    #: central PULL, equipment endpoints and storage root by chamber settings.
    #: The old ``is required`` check was a leftover from when the workbook was the
    #: only input — not a decision anyone made, just a line nobody revisited.
    #:
    #: EMPTY IS NORMAL; A PATH THAT DOES NOT RESOLVE IS NOT. This field carries the
    #: configured value verbatim and does not probe the filesystem (a frozen config
    #: that stats on construction would fail every test that builds one with a
    #: fixture path). The absent/misconfigured split is
    #: ``domain.services.workbook_availability`` and is applied at the composition
    #: root, where something is actually done with the answer.
    excel_path: str = ''
    switchbox_enabled: bool = False
    manual_bt_call: bool = False
    device_less: bool = False
    ble_minimal_reconfig: bool = False
    app_title: str = 'FCC Session API'
    app_version: str = ''
    auth: HttpAuthConfig = field(default_factory=HttpAuthConfig)
    event_buffer_size: int = 1024
    attach_event_bridge: bool = True
    cors_origins: tuple[str, ...] = ()
    cors_allow_credentials: bool = True
    ws_heartbeat_seconds: float = 20.0
    operator: str = LOCAL_GUI_OPERATOR_SUBJECT
    #: Inbound throttle (2026-07-19) — see HeadlessApiConfig.rate_limit. Secure
    #: by default from env; explicit FCC_SESSION_RATE_LIMIT_ENABLED=0 disables.
    rate_limit: RateLimitPolicy = field(default_factory=RateLimitPolicy)
    # Dedicated Session Node bind/readiness settings. They are appended to keep
    # existing positional construction of SessionApiConfig compatible.
    node_host: str = DEFAULT_SESSION_NODE_HOST
    node_port: int = DEFAULT_SESSION_NODE_PORT
    node_readiness_timeout_seconds: float = DEFAULT_SESSION_NODE_READINESS_TIMEOUT_SECONDS
    node_readiness_interval_seconds: float = DEFAULT_SESSION_NODE_READINESS_INTERVAL_SECONDS
    runtime_dir: str = ''
    #: plot-dual-custody ① — ``POST /session/start`` 의 ``storage_root`` 가 들어갈 수
    #: 있는 루트들. **비면 override 를 거부한다**(임의 경로 쓰기 방지).
    allowed_storage_roots: tuple[str, ...] = ()
    #: Ceiling for an uploaded workbook. Typed here, defaulted by the policy
    #: SSOT, so the number has one definition and the env parser is pure.
    max_workbook_upload_bytes: int = DEFAULT_MAX_WORKBOOK_UPLOAD_BYTES
    #: Session lifetime axis (2026-08-31) — how long an unreferenced upload is
    #: kept. A workbook a live session is bound to is never removed, whatever
    #: this says; the retention window governs only the ones nobody is using
    #: (see ``domain.services.workbook_upload_gc_policy``).
    workbook_retention_seconds: float = float(DEFAULT_WORKBOOK_RETENTION_SECONDS)

    def __post_init__(self) -> None:
        host = str(self.node_host or '').strip()
        if not host:
            raise ValueError('FCC_SESSION_NODE_HOST must not be empty')
        if not 1 <= int(self.node_port) <= 65535:
            raise ValueError('FCC_SESSION_NODE_PORT must be between 1 and 65535')
        if self.node_readiness_timeout_seconds <= 0:
            raise ValueError(
                'FCC_SESSION_NODE_READINESS_TIMEOUT_SECONDS must be positive'
            )
        if self.node_readiness_interval_seconds <= 0:
            raise ValueError(
                'FCC_SESSION_NODE_READINESS_INTERVAL_SECONDS must be positive'
            )
        try:
            self.resolved_runtime_dir
        except (OSError, ValueError) as exc:
            raise ValueError('FCC_SESSION_RUNTIME_DIR is invalid') from exc

    @property
    def node_base_url(self) -> str:
        """Return the bind-derived node URL used for local diagnostics.

        The central advertised address remains ``FCC_CENTRAL_NODE_BASE_URL`` in
        ``CentralHeartbeatConfig``; this property is deliberately only the
        local listener projection and never substitutes a deployment address.
        """
        host = self.node_host
        if host == '0.0.0.0':
            host = '127.0.0.1'
        elif host == '::':
            host = '[::1]'
        return f'http://{host}:{self.node_port}'

    @property
    def readiness_url(self) -> str:
        """Stable local readiness URL derived from the typed bind settings."""
        return f'{self.node_base_url}/session/health'

    @property
    def resolved_runtime_dir(self) -> Path:
        """Return the absolute writable root for logs and local state.

        An empty value intentionally means the current working directory for
        library/test compatibility. The operator env template supplies an
        explicit machine-local directory outside the immutable package.
        """
        raw = str(self.runtime_dir or '').strip()
        return (Path(raw).expanduser() if raw else Path.cwd()).resolve()

    @property
    def workbook_upload_root(self) -> Path:
        """Directory this node stores uploaded workbooks in.

        Derived, never configured separately: it hangs off the same writable
        root the node already changes into at boot, using the parts the policy
        SSOT owns. A second environment variable would let an operator point
        uploads somewhere the node does not otherwise own, which is the
        arbitrary-destination problem the handle design exists to remove.
        """
        return self.resolved_runtime_dir.joinpath(*workbook_upload_root_parts())

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> 'SessionApiConfig':
        env = os.environ if environ is None else environ
        # ABSENCE AND MISCONFIGURATION ARE DIFFERENT EVENTS (2026-08-18).
        #
        # Unset is a normal state — a web-operated node gets its plan from
        # `published_plan_id`, its reference values from the central PULL, and its
        # equipment endpoints and storage root from chamber settings. The old
        # `is required` check was a leftover from when the workbook was the only
        # input, not a decision anyone made.
        #
        # A path that WAS given and does not resolve is a different matter, and
        # folding it into "absent" is how one typo silently becomes workbook-less
        # operation with the reference fallback gone and nobody told.
        #
        # ⚠️ 그 구분은 여기서 raise 하지 않는다. 이 함수는 **파싱**이고, 경로가
        # 지금 존재하는가는 사용 시점의 사실이다(네트워크 공유가 잠시 안 보일 수도
        # 있다). 구분은 ``_compose_driven_adapters`` 가 하고 — 경로를 대는 ERROR —
        # 위험한 결과 자체는 참조 게이트가 hard-refuse 한다. 즉 오타는 조용하지
        # 않고, 참조값을 어디서도 못 구하면 부팅이 거부된다.
        excel_path = read_text(env, _NON_AUTH_FCC_SESSION_ENV['excel_path'])
        defaults = cls.__dataclass_fields__
        return cls(
            excel_path=excel_path,
            switchbox_enabled=read_bool(
                env,
                _NON_AUTH_FCC_SESSION_ENV['switchbox_enabled'],
                default=defaults['switchbox_enabled'].default,
            ),
            device_less=read_bool(
                env,
                _NON_AUTH_FCC_SESSION_ENV['device_less'],
                default=defaults['device_less'].default,
            ),
            manual_bt_call=read_bool(
                env,
                _NON_AUTH_FCC_SESSION_ENV['manual_bt_call'],
                default=defaults['manual_bt_call'].default,
            ),
            ble_minimal_reconfig=read_bool(
                env,
                _NON_AUTH_FCC_SESSION_ENV['ble_minimal_reconfig'],
                default=defaults['ble_minimal_reconfig'].default,
            ),
            app_title=(
                read_text(env, _NON_AUTH_FCC_SESSION_ENV['app_title'])
                or defaults['app_title'].default
            ),
            app_version=read_text(env, _NON_AUTH_FCC_SESSION_ENV['app_version']),
            auth=HttpAuthConfig.from_env(env, prefix=FCC_SESSION_AUTH_ENV_PREFIX),
            event_buffer_size=read_int(
                env,
                _NON_AUTH_FCC_SESSION_ENV['event_buffer_size'],
                default=defaults['event_buffer_size'].default,
            ),
            attach_event_bridge=read_bool(
                env,
                _NON_AUTH_FCC_SESSION_ENV['attach_event_bridge'],
                default=defaults['attach_event_bridge'].default,
            ),
            cors_origins=read_csv(env, _NON_AUTH_FCC_SESSION_ENV['cors_origins']),
            cors_allow_credentials=read_bool(
                env,
                _NON_AUTH_FCC_SESSION_ENV['cors_allow_credentials'],
                default=defaults['cors_allow_credentials'].default,
            ),
            ws_heartbeat_seconds=read_float(
                env,
                _NON_AUTH_FCC_SESSION_ENV['ws_heartbeat_seconds'],
                default=defaults['ws_heartbeat_seconds'].default,
            ),
            operator=(
                read_text(env, _NON_AUTH_FCC_SESSION_ENV['operator'])
                or defaults['operator'].default
            ),
            runtime_dir=(
                read_text(env, _NON_AUTH_FCC_SESSION_ENV['runtime_dir'])
                or defaults['runtime_dir'].default
            ),
            allowed_storage_roots=read_csv(
                env, _NON_AUTH_FCC_SESSION_ENV['allowed_storage_roots'],
            ),
            # Parsed by the policy SSOT, not by ``read_int``: the coercion rules
            # (blank / garbage / non-positive all mean "the default") are part of
            # the ceiling's definition, and a second parser here would be free to
            # disagree — most dangerously by accepting 0 as "no limit".
            max_workbook_upload_bytes=resolve_max_upload_bytes(
                read_text(env, _NON_AUTH_FCC_SESSION_ENV['max_workbook_upload_bytes']),
            ),
            # Same reasoning as the ceiling directly above — the policy owns the
            # coercion so the two knobs cannot drift into disagreeing about what
            # an unset value means.
            workbook_retention_seconds=resolve_workbook_retention_seconds(
                read_text(env, _NON_AUTH_FCC_SESSION_ENV['workbook_retention_seconds']),
            ),
            rate_limit=load_rate_limit_policy(env, prefix=FCC_SESSION_AUTH_ENV_PREFIX),
            node_host=(
                read_text(env, _NON_AUTH_FCC_SESSION_ENV['node_host'])
                or defaults['node_host'].default
            ),
            node_port=read_int(
                env,
                _NON_AUTH_FCC_SESSION_ENV['node_port'],
                default=defaults['node_port'].default,
            ),
            node_readiness_timeout_seconds=read_float(
                env,
                _NON_AUTH_FCC_SESSION_ENV['node_readiness_timeout_seconds'],
                default=defaults['node_readiness_timeout_seconds'].default,
            ),
            node_readiness_interval_seconds=read_float(
                env,
                _NON_AUTH_FCC_SESSION_ENV['node_readiness_interval_seconds'],
                default=defaults['node_readiness_interval_seconds'].default,
            ),
        )

    def app_options(self) -> dict:
        options = {'title': self.app_title}
        if self.app_version:
            options['version'] = self.app_version
        return options
