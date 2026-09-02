"""챔버 노드 Session API forward 어댑터 (멀티챔버 Phase 5, 2026-06-16).

domain output Port
:class:`domain.ports.output.chamber_measurement_proxy_port.ChamberMeasurementProxyPort`
의 생산 구현(구조적 충족 — 명시 상속 불필요) — 중앙 허브가 ``chamber_nodes.base_url``
의 챔버 PC Session API(``:8000``)로 측정 시작/진행조회를 forward 한다.

outbound HTTP 정책 (SSOT):

- **W3C TraceContext** — 모든 outbound 호출은
  ``application.common.outbound_http.build_outbound_traceparent_headers()`` 단일 SSOT 로
  ``traceparent`` 헤더를 붙인다(중앙 inbound → 노드 outbound 분산추적 연결). raw 헤더
  dict 리터럴 금지.
- **stdlib urllib 만** — ``httpx`` / ``aiohttp`` 신규 도입 금지
  (``TestOutboundHttpSSot`` 봉인). OIDC JWKS 로더와 동일 ``urllib.request`` 패턴.
- **노드 경로 SSOT** — ``/session/start`` / ``/session/progress`` 를 하드코딩하지 않고
  ``application.session.api_contracts.SESSION_API_ROUTES`` 에서 파생(노드 라우트가
  바뀌면 중앙 프록시가 자동 정합).
- **timeout/재시도 정책 SSOT** — 요청 타임아웃과 transient 재시도 backoff 는
  ``domain.services.chamber_proxy_policy.ChamberProxyPolicy`` 에서 파생한다(매직 넘버
  금지). composition root 가 ``FCC_PLATFORM_CHAMBER_PROXY_*`` env 로 정책을 빌드해
  주입한다(기본 정책 = SSOT 싱글톤).

**재시도 멱등성 경계(중요)** — 재시도는 **멱등 연산에만** 적용한다:

- ``get_progress`` (진행 조회, GET·읽기) → transient 실패(연결오류/타임아웃/노드 5xx·
  408·429) 시 재시도. 같은 조회를 반복해도 부작용이 없다.
- ``start_measurement`` (측정 시작, POST·쓰기) → **재시도하지 않는다**. 노드가 첫 요청을
  받아 측정을 시작했는데 응답만 유실된 경우 재시도하면 **측정이 중복 시작**된다(멱등키
  부재). 따라서 timeout 만 적용하고 첫 실패를 그대로 ``ChamberProxyError`` 로 올린다.

forward 실패(네트워크/타임아웃/노드 5xx/JSON 오류)는 ``ChamberProxyError`` 로 loud 하게
올린다 — 조용한 빈 결과로 "측정 시작됨" 오인 금지(→ 중앙 API 503).
"""
from __future__ import annotations

import json
import time
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fcc_test_contracts.common.outbound_http import build_outbound_traceparent_headers
from application.session.api_contracts import SESSION_API_ROUTES
from domain.ports.output.chamber_measurement_proxy_port import (
    ChamberBusyError,
    ChamberProxyError,
)
from domain.services.chamber_proxy_policy import (
    DEFAULT_CHAMBER_PROXY_POLICY,
    ChamberProxyPolicy,
)


__all__ = ['HttpChamberProxyAdapter']


#: 노드 Session API 경로 SSOT 파생 — 하드코딩 '/session/start' / '/session/progress' 금지.
_START_METHOD, _START_PATH = SESSION_API_ROUTES['start_session']
_PROGRESS_METHOD, _PROGRESS_PATH = SESSION_API_ROUTES['get_session_progress']

_JSON_HEADERS = {'Content-Type': 'application/json', 'Accept': 'application/json'}


class HttpChamberProxyAdapter:
    """``ChamberMeasurementProxyPort`` 의 stdlib ``urllib`` 생산 구현 + traceparent SSOT.

    domain output Port 를 구조적으로 충족한다(``start_measurement`` / ``get_progress`` —
    명시 상속 없이 ``isinstance(adapter, ChamberMeasurementProxyPort)`` 성립, P11).
    """

    def __init__(
        self,
        *,
        policy: ChamberProxyPolicy = DEFAULT_CHAMBER_PROXY_POLICY,
        urlopen_fn: Callable = urlopen,
        sleep_fn: Callable[[float], None] = time.sleep,
        token_supplier: Optional[Callable[[], str]] = None,
    ) -> None:
        # ``urlopen_fn`` 주입 — 테스트는 outbound 를 가짜로 캡처(실 네트워크 불필요).
        # ``sleep_fn`` 주입 — 테스트는 재시도 backoff 를 no-op 으로 만들어 즉시 검증.
        #
        # ``token_supplier`` 주입 — 중앙이 노드에 제시할 **기계 신분증**(운영자 판정
        # 2026-09-01: 사용자 토큰 위임이 아니라 서비스 계정). 노드는 ``oidc_jwt`` 가
        # 아니면 기동 자체를 거부하므로 이 hop 은 자격증명 없이는 성립할 수 없다 —
        # 실측 2026-09-01: 헤더 없는 호출에 노드가 ``403 missing_permission``.
        #
        # ⚠️ env 를 여기서 읽지 않는다. 공급자는 합성 루트가 만들어 넣는다
        # (``ClientCredentialsTokenProvider`` 가 이미 노드→중앙 방향에서 같은 일을
        # 한다 — 두 방향이 한 어휘를 쓴다).
        #
        # ⚠️ 기본값 ``None`` = 헤더를 붙이지 않던 옛 동작과 byte-identical. 인증을
        # 요구하지 않는 노드(개발용 ``disabled`` 프로필)와 기존 테스트가 그대로 돈다.
        self._policy = policy
        self._urlopen = urlopen_fn
        self._sleep = sleep_fn
        self._token_supplier = token_supplier

    def start_measurement(
        self, base_url: str, *, published_plan_id: Optional[str] = None,
        project_id: Optional[str] = None, sample_id: Optional[str] = None,
        sample_snapshot: Optional[dict] = None,
        sample_snapshot_schema_version: Optional[str] = None,
        project_result_reference_snapshot_json: Optional[str] = None,
        project_result_reference_snapshot_schema_version: Optional[str] = None,
    ) -> dict:
        body: dict = {}
        if published_plan_id is not None:
            # P4 하위호환: 미지정 시 빈 body → 노드가 런타임 워크북 사용.
            body['published_plan_id'] = published_plan_id
        if project_id is not None:
            body['project_id'] = project_id
        if sample_id is not None:
            body['sample_id'] = sample_id
        if sample_snapshot is not None:
            body['sample_snapshot'] = sample_snapshot
        if sample_snapshot_schema_version is not None:
            body['sample_snapshot_schema_version'] = sample_snapshot_schema_version
        if project_result_reference_snapshot_json is not None:
            body['project_result_reference_snapshot_json'] = (
                project_result_reference_snapshot_json
            )
        if project_result_reference_snapshot_schema_version is not None:
            body['project_result_reference_snapshot_schema_version'] = (
                project_result_reference_snapshot_schema_version
            )
        # 측정 시작은 비멱등(POST·쓰기) → retryable=False(중복 측정 시작 방지).
        payload = self._request(
            _START_METHOD, self._join(base_url, _START_PATH), body, retryable=False,
        )
        # 노드 /session/start 응답은 {operation, api_version, progress:{...}}.
        # progress sub-dict 를 풀어 반환(서비스가 타입 정규화).
        if isinstance(payload, dict) and isinstance(payload.get('progress'), dict):
            return payload['progress']
        return payload if isinstance(payload, dict) else {}

    def get_progress(self, base_url: str) -> dict:
        # 진행 조회는 멱등(GET·읽기) → retryable=True(transient 실패 재시도 안전).
        payload = self._request(
            _PROGRESS_METHOD, self._join(base_url, _PROGRESS_PATH), None, retryable=True,
        )
        return payload if isinstance(payload, dict) else {}

    def _with_authorization(self, headers: dict) -> dict:
        """Return ``headers`` plus the machine bearer, when one is configured.

        ⚠️ The token is fetched PER REQUEST, not cached here. Caching (and its
        expiry arithmetic) belongs to the supplier — ``ClientCredentialsTokenProvider``
        already owns it for the node->central direction, and a second cache on this
        side would be a second expiry clock that can disagree with the first.

        A supplier that raises is not swallowed: a node call with no credential
        comes back as ``403 missing_permission``, which the surface reports as an
        UPSTREAM failure and sends the operator looking at the node. Failing here
        names the real cause (central could not obtain its own token).
        """
        if self._token_supplier is None:
            return headers
        token = str(self._token_supplier() or '').strip()
        if not token:
            return headers
        return {**headers, 'Authorization': f'Bearer {token}'}

    # ── 내부 ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _join(base_url: str, path: str) -> str:
        if not base_url or not str(base_url).strip():
            raise ChamberProxyError('chamber base_url is empty — cannot forward')
        return base_url.rstrip('/') + path

    def _request(
        self, method: str, url: str, body: Optional[dict], *, retryable: bool,
    ) -> dict:
        """Forward one request to a node, retrying transient failures iff ``retryable``.

        ``retryable`` is the per-operation idempotency gate: only idempotent reads
        (``get_progress``) pass ``True``. Non-idempotent writes (``start_measurement``)
        pass ``False`` so a lost-response retry can never start a duplicate
        measurement. Retry coverage (transient classification) lives in the
        ``ChamberProxyPolicy`` SSOT — no inline backoff arithmetic here.
        """
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
        headers = build_outbound_traceparent_headers(existing=_JSON_HEADERS)
        headers = self._with_authorization(headers)

        attempt = 0
        while True:
            request = Request(url, data=data, headers=headers, method=method)
            try:
                with self._urlopen(request, timeout=self._policy.timeout_seconds) as response:
                    raw = response.read()
                break  # 성공 — 재시도 루프 탈출.
            except HTTPError as exc:  # 노드가 4xx/5xx 반환 — upstream 실패.
                # 5xx·408·429 만 transient(정책 SSOT). 4xx 영구 오류는 재시도 금지.
                if (
                    retryable
                    and self._policy.is_retryable_http_status(exc.code)
                    and self._policy.should_retry(attempt)
                ):
                    self._sleep(self._policy.next_retry_delay_seconds(attempt))
                    attempt += 1
                    continue
                if exc.code == 409:
                    # 노드 재진입 가드: 이미 측정 중 → 가용성 충돌(503 아님). 중앙
                    # 서비스가 ChamberNotAvailableError(409)로 전파(IDLE 게이트와 동의).
                    raise ChamberBusyError(
                        f'chamber node is busy (already measuring) for {method} {url}'
                    ) from exc
                raise ChamberProxyError(
                    f'chamber node returned HTTP {exc.code} for {method} {url}'
                ) from exc
            except URLError as exc:  # 연결 거부 / DNS / 타임아웃 등 — 항상 transient.
                if retryable and self._policy.should_retry(attempt):
                    self._sleep(self._policy.next_retry_delay_seconds(attempt))
                    attempt += 1
                    continue
                raise ChamberProxyError(
                    f'chamber node unreachable for {method} {url}: {exc.reason}'
                ) from exc
            except OSError as exc:  # socket.timeout 등 잔여 OS 오류 — 항상 transient.
                if retryable and self._policy.should_retry(attempt):
                    self._sleep(self._policy.next_retry_delay_seconds(attempt))
                    attempt += 1
                    continue
                raise ChamberProxyError(
                    f'chamber node forward failed for {method} {url}: {exc}'
                ) from exc

        if not raw:
            return {}
        try:
            decoded = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError) as exc:
            # 노드가 응답은 했으나 body 가 깨짐 = 노드측 버그(영구) → 재시도 무의미.
            raise ChamberProxyError(
                f'chamber node returned non-JSON body for {method} {url}'
            ) from exc
        return decoded if isinstance(decoded, dict) else {}
