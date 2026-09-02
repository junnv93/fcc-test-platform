"""챔버 노드 OIDC client_credentials 토큰 provider (멀티챔버, 2026-06-20).

노드(Session API 프로세스)가 중앙 허브로 보내는 outbound 호출에 쓸 Bearer 토큰을
**만료 전 자동 갱신**한다. 기존 노드는 정적 ``FCC_CENTRAL_AUTH_TOKEN`` 을 썼는데,
OIDC access-token 은 기본 600초 만에 만료 → 장시간 노드의 heartbeat 가 401 로 끊기고
중앙이 TTL 후 OFFLINE 으로 오판하던 갭을 정공한다.

설계:
- client_credentials grant 로 토큰 발급 → ``expires_in`` 기반 만료 시각 캐시 →
  ``refresh_margin_seconds`` 이내로 임박하면 재발급(silent refresh).
- thread-safe single-flight: ``get_token()`` 이 동시 호출돼도 만료 시 재발급은 1회만
  (heartbeat 송신 스레드 + 등록 등 다중 caller 안전).
- outbound traceparent SSOT(``build_outbound_traceparent_headers``) 재사용 — 자체
  trace 생성 금지(노드→IdP 분산추적 연결).
- dependency-free: stdlib ``urllib`` + ``application.common`` 만(pyvisa/openpyxl/
  PySide6/httpx/aiohttp 미사용 — 새 HTTP 라이브러리 도입 금지).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable

from fcc_test_contracts.common.outbound_http import build_outbound_traceparent_headers


__all__ = [
    'ClientCredentialsTokenProvider',
    'DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS',
    'maybe_client_credentials_token_provider',
]


#: 만료 임박 마진(초) — 토큰 ``exp`` 까지 이 시간보다 적게 남으면 미리 재발급해
#: heartbeat 가 만료 직전 토큰으로 401 되는 경계를 피한다. OIDC 기본 access-token
#: 수명(600s)에 비해 충분히 작다. ``oidc_principal_resolver.OIDC_CLOCK_TOLERANCE``
#: (검증측 skew)와 직교 — 이건 발급측 갱신 스케줄.
DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS = 30.0


class ClientCredentialsTokenProvider:
    """OIDC client_credentials 토큰 발급·캐시·만료 전 갱신 provider."""

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 5.0,
        refresh_margin_seconds: float = DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        urlopen_fn: Callable = urllib.request.urlopen,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._refresh_margin = refresh_margin_seconds
        self._clock = clock
        self._urlopen = urlopen_fn
        self._token: str = ''
        self._expires_at: float | None = None
        self._lock = threading.Lock()

    def get_token(self) -> str:
        """유효 토큰 반환 — 캐시가 만료 임박이면 단일-비행으로 재발급."""
        # fast path — 충분히 유효하면 무락 반환.
        if self._token and not self._is_expiring(self._clock()):
            return self._token
        with self._lock:
            # double-check — 다른 스레드가 방금 갱신했을 수 있음.
            if self._token and not self._is_expiring(self._clock()):
                return self._token
            self._fetch()
            return self._token

    def _is_expiring(self, now: float) -> bool:
        if self._expires_at is None:
            return True
        return now >= (self._expires_at - self._refresh_margin)

    def _fetch(self) -> None:
        data = urllib.parse.urlencode({
            'grant_type': 'client_credentials',
            'client_id': self._client_id,
            'client_secret': self._client_secret,
        }).encode('utf-8')
        headers = build_outbound_traceparent_headers(existing={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
        })
        request = urllib.request.Request(self._token_url, data=data, headers=headers)
        with self._urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode('utf-8'))
        token = str(payload.get('access_token') or '')
        if not token:
            raise PermissionError('client_credentials response missing access_token')
        # expires_in 누락 시 보수적으로 마진만큼만 유효 처리(다음 호출에서 재발급).
        try:
            expires_in = float(payload.get('expires_in'))
        except (TypeError, ValueError):
            expires_in = self._refresh_margin
        self._token = token
        self._expires_at = self._clock() + expires_in


def maybe_client_credentials_token_provider(config):
    """중앙 outbound 호출에 실을 토큰 provider — **provider 를 만드는 유일한 자리**.

    ``config.uses_client_credentials`` (token_url+client_id+secret 셋 다 설정) 일 때만
    provider 를 만든다. 미설정이면 ``None`` → 호출자는 정적 ``config.auth_token`` 경로
    유지(byte-identical).

    ⚠️ **이 함수가 존재하는 이유는 편의가 아니라 진단 가능성이다.** ``token_provider=``
    는 모든 아웃바운드 클라이언트에서 *선택적 키워드*라, 주지 않는 것이 문법적으로
    완전히 정상으로 보인다. 2026-09-02 에 실제로 그렇게 됐다 — 형제 셋(heartbeat·자가
    등록·참조 당김)은 주고 ``ChamberSettingsClient`` 두 사이트만 빠졌는데, OIDC 모드의
    ``config.auth_token`` 은 빈 문자열이라 ``build_central_request_headers`` 가
    Authorization 헤더를 **아예 붙이지 않았고**, 실패는 401(자격이 틀렸다)이 아니라
    403(권한이 없다)으로 나타났다. 그 증상은 "권한 설계가 잘못됐다"로 읽히므로
    실측 없이는 진단이 반대로 간다.

    빌드하는 자리가 하나면 "이 호출자는 부르지 않는다"가 **셀 수 있는 사실**이 된다.
    """
    if config is None or not getattr(config, 'uses_client_credentials', False):
        return None
    return ClientCredentialsTokenProvider(
        config.oidc_token_url,
        config.client_id,
        config.client_secret,
        timeout=config.heartbeat_timeout_seconds,
    )
