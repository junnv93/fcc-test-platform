"""ASGI factory entrypoint for the platform read API runtime.

Intended runtime usage:
    uvicorn --factory platform_api_app:create_app

Uses the modern FastAPI ``lifespan`` constructor exclusively. Legacy
``add_event_handler`` / ``@app.on_event`` hooks are forbidden — see
``TestNoDeprecatedFastApiInWebSurface``.
"""
from __future__ import annotations

from contextlib import asynccontextmanager


def create_app(environ=None):
    """Create the configured platform read FastAPI app.

    1. ``PlatformApiConfig.from_env(environ)`` — central DSN + auth from env.
    2. ``create_platform_runtime_from_config(config)`` — assemble the central
       read runtime (loud-fail if ``FCC_CENTRAL_DB_URL`` is unset).
    3. Build the FastAPI app with a ``lifespan`` that disposes the runtime on
       shutdown; dispose immediately if app construction fails first.
    """
    from fcc_test_contracts.common.proxy_trust import enforce_trusted_proxy_config
    from fcc_test_contracts.common.trace_sampler import install_sampler_from_env
    from fcc_test_contracts.common.logging_channel import install_server_stream_handler
    from fcc_test_platform.application.runtime_config import PlatformApiConfig
    from fcc_test_platform.api_composition import (
        create_platform_app_from_config,
        create_platform_runtime_from_config,
    )

    # This is the server-only logging boundary.  It must run before the first
    # boot notice (trusted-proxy validation) and before runtime composition.
    # The dependency-free shared capability owns installation and is
    # idempotent, so repeated factory calls reuse the server handler without
    # importing the provider-owned logger facade.
    install_server_stream_handler()

    install_sampler_from_env(environ)

    # peer 축 신뢰 hop (2026-08-22) — 이 프로세스가 어떤 출처를 "우리 리버스 프록시"
    # 로 믿는지 확정한다. install_sampler_from_env 와 같은 자리인 이유도 같다:
    # 프로세스 단위 · env 주도 · 첫 요청 이전에 결정돼야 한다. 위험한 값은 여기서
    # 부팅을 거부하고(그 실패 모드는 조용하다), 미설정은 시끄럽게 고지만 한다.
    enforce_trusted_proxy_config(environ)

    config = PlatformApiConfig.from_env(environ)
    runtime = create_platform_runtime_from_config(config)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            runtime.dispose()

    try:
        app = create_platform_app_from_config(runtime, config, lifespan=lifespan)
    except Exception:
        runtime.dispose()
        raise
    _attach_runtime(app, runtime)
    return app


def _attach_runtime(app, runtime) -> None:
    state = getattr(app, 'state', None)
    if state is not None:
        setattr(state, 'platform_runtime', runtime)
    else:
        setattr(app, 'platform_runtime', runtime)
