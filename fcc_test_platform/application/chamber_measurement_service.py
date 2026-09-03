"""중앙 측정 프록시 서비스 (멀티챔버 Phase 5, 2026-06-16).

웹은 중앙 1곳만 인증한다(단일 인증 표면). ``ChamberMeasurementService`` 가 그 인증
경계 안에서 (1) 대상 챔버의 **가용성**을 P2 ``CentralChamberReadService`` 로 조회해
IDLE 게이트를 걸고(offline/in_use 차단), (2) 레지스트리의 ``base_url`` 을 찾아
(3) 챔버 노드의 Session API 로 측정 시작/진행조회를 forward 한다.

forward 자체(outbound HTTP)는 domain output Port
:class:`domain.ports.output.chamber_measurement_proxy_port.ChamberMeasurementProxyPort`
의 구현체(생산: ``infrastructure/adapters/driven/chamber_proxy_adapter``)가 수행한다.
이 서비스는 게이트/조회/정규화만 책임지는 application 경계이며 stdlib +
``application.common`` + ``domain`` 만 의존한다(infrastructure / FastAPI / SQL import 0 —
P2 챔버 서비스와 동형).

P5 에서 시간 제약상 명시 ``Protocol`` 없는 구조적(duck-typed) 협력자로 승인됐던 계약을
P11(2026-06-16)에서 narrow output Port 로 승격했다(헥사고날 정합 회복, 측정/forward
동작 byte-compatible). :class:`ChamberProxyError` 도 포트와 co-locate 되며 본 모듈은
하위호환을 위해 re-export 한다(기존 ``except ChamberProxyError`` 동일성 보존).

IDLE 게이트의 "idle" 은 :class:`domain.models.chamber_node.ChamberNodeStatus` SSOT 에서
파생하며 가용성 status 는 read service 가 **주입 clock + per-chamber TTL** 로 이미 파생한
(idle/in_use/offline) 값을 verbatim 소비한다(DB-side now() 없음 — P1/P2 불변식 연속).
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

from fcc_test_platform.application.central_chamber_read_service import CentralChamberReadService
from fcc_test_kernel.domain.models.chamber_node import ChamberNodeStatus, ChamberProgress
from fcc_test_platform.domain.ports.output.central_chamber_write_port import ChamberNotFoundError
from fcc_test_platform.domain.ports.output.chamber_measurement_proxy_port import (
    ChamberBusyError,
    ChamberMeasurementProxyPort,
    ChamberProxyError,
)


__all__ = [
    'ChamberMeasurementService',
    # ``ChamberNotFoundError`` 는 도메인 포트(``central_chamber_write_port``)가 SSOT
    # 정의 — heartbeat write 경로도 같은 사실을 올리므로 포트가 소유한다(부채 청산 M2).
    # 본 모듈은 기존 소비자(platform_routes / 테스트) 하위호환 위해 re-export.
    'ChamberNotFoundError',
    'ChamberNotAvailableError',
    # ``ChamberProxyError`` 는 도메인 포트(``chamber_measurement_proxy_port``)가 SSOT
    # 정의 — 본 모듈은 기존 소비자(platform_routes / 테스트) 하위호환 위해 re-export.
    'ChamberProxyError',
]


class ChamberNotAvailableError(RuntimeError):
    """챔버가 IDLE 이 아니어서 측정을 시작할 수 없음(in_use **또는** offline) — 409.

    OFFLINE 은 heartbeat 부재/경과에서 파생된 상태이고 IN_USE 는 이미 측정 중이므로,
    둘 다 새 측정 시작을 거부한다. 가용성 판정은 P2 read service 의 주입-clock 파생을
    재사용한다(중앙이 DB now() 로 계산하지 않음).
    """


class ChamberMeasurementService:
    """챔버 가용성 게이트 + base_url lookup + 노드 forward 의 application 경계.

    ``proxy`` 는 domain output Port :class:`ChamberMeasurementProxyPort` 구현체
    (생산: ``HttpChamberProxyAdapter``, 테스트: in-memory fake). 명시 ``Protocol``
    타입으로 duck-typed ``object`` 를 대체(P11) — 구조적 충족이면 명시 상속 불필요.
    """

    def __init__(
        self,
        read_service: CentralChamberReadService,
        proxy: ChamberMeasurementProxyPort,
        sample_inventory_service=None,
        project_reference_service=None,
    ) -> None:
        self._read = read_service
        self._proxy = proxy
        self._sample_inventory = sample_inventory_service
        self._project_reference_service = project_reference_service

    @property
    def requires_sample_snapshot(self) -> bool:
        """Whether this composition owns the web sample/session invariant."""
        return self._sample_inventory is not None

    def start_measurement(
        self, chamber_id: str, *, project_id: Optional[str] = None,
        sample_id: Optional[str] = None,
        published_plan_id: Optional[str] = None,
        project_result_reference_requests: Optional[Sequence[Mapping]] = None,
        reference_consumer_provider_id: Optional[str] = None,
    ) -> dict:
        """Validate/capture sample → IDLE 게이트 → node ``/session/start``.

        Production composition injects the web inventory service, making
        ``project_id`` and ``sample_id`` mandatory and ensuring the node receives
        one platform-owned immutable snapshot. The no-service/no-identity branch
        remains only for legacy isolated callers and cannot be reached through the
        production platform route.

        Returns ``{'chamber_id': str, 'progress': {is_running, completed, total, ratio}}``.
        Raises ``ChamberNotFoundError``(404) / ``ChamberNotAvailableError``(409) /
        ``ChamberProxyError``(503).
        """
        project = str(project_id or '').strip()
        sample = str(sample_id or '').strip()
        snapshot = None
        if self._sample_inventory is not None:
            if not project or not sample:
                raise ValueError('project_id and sample_id are required')
            snapshot = self._sample_inventory.build_measurement_snapshot(
                project, sample, published_plan_id=published_plan_id,
            )
        elif project or sample:
            raise RuntimeError('sample inventory service is not wired')
        reference_snapshot_json = None
        reference_snapshot_schema_version = None
        if project_result_reference_requests:
            if self._project_reference_service is None:
                raise RuntimeError('project reference service is not wired')
            reference_snapshot_json, reference_snapshot_schema_version = (
                self._project_reference_service.build_session_reference_snapshot(
                    project_id=project,
                    consumer_provider_id=str(reference_consumer_provider_id or '').strip(),
                    requests=project_result_reference_requests,
                )
            )
        item = self._resolve_idle_chamber(chamber_id)
        try:
            kwargs = {'published_plan_id': published_plan_id}
            if snapshot is not None:
                kwargs.update({
                    'project_id': project,
                    'sample_id': sample,
                    'sample_snapshot': snapshot,
                    'sample_snapshot_schema_version': snapshot.get('schema_version'),
                })
            if reference_snapshot_json is not None:
                kwargs.update({
                    'project_result_reference_snapshot_json': reference_snapshot_json,
                    'project_result_reference_snapshot_schema_version': (
                        reference_snapshot_schema_version
                    ),
                })
            raw = self._proxy.start_measurement(item['base_url'], **kwargs)
        except ChamberBusyError as exc:
            # heartbeat 지연 race: 중앙은 IDLE 로 봤으나 노드(권위)가 이미 측정 중이라
            # 409 거부 → 5xx upstream 이 아니라 가용성 충돌로 전파(IDLE 게이트와 동의).
            raise ChamberNotAvailableError(
                f"chamber {item['chamber_id']!r} is already measuring "
                '(node rejected a concurrent start) — cannot start a measurement'
            ) from exc
        return _snapshot(item['chamber_id'], raw)

    def measurement_progress(self, chamber_id: str) -> dict:
        """base_url lookup → 노드 ``/session/progress`` forward(IDLE 게이트 없음).

        진행조회는 측정 중(IN_USE) 챔버를 폴링하므로 IDLE 을 요구하지 않는다 —
        챔버가 존재하기만 하면 forward 한다(노드 미응답 시 ``ChamberProxyError`` 503).
        """
        item = self._resolve_chamber(chamber_id)
        raw = self._proxy.get_progress(item['base_url'])
        return _snapshot(item['chamber_id'], raw)

    # ── 내부 — 게이트 + lookup ────────────────────────────────────────────────

    def _resolve_chamber(self, chamber_id: str) -> dict:
        cid = (chamber_id or '').strip()
        if not cid:
            raise ValueError('chamber_id is required')
        page = self._read.chamber_availability()
        for item in page.get('items', ()):
            if item.get('chamber_id') == cid:
                return item
        raise ChamberNotFoundError(f'unknown chamber_id: {cid!r}')

    def _resolve_idle_chamber(self, chamber_id: str) -> dict:
        item = self._resolve_chamber(chamber_id)
        if not item.get('enabled', False):
            raise ChamberNotAvailableError(
                f"chamber {item['chamber_id']!r} is disabled — cannot start a measurement"
            )
        status = item.get('status')
        if status != ChamberNodeStatus.IDLE.value:
            raise ChamberNotAvailableError(
                f"chamber {item['chamber_id']!r} is not idle (status={status!r}) — "
                'cannot start a measurement'
            )
        return item


def _snapshot(chamber_id: str, raw: dict) -> dict:
    """중앙 측정 스냅샷 envelope — 노드 progress 를 타입 정규화해 감싼다."""
    return {'chamber_id': chamber_id, 'progress': _progress_view(raw)}


def _progress_view(raw: Optional[dict]) -> dict:
    """노드 progress raw dict → 안정 wire shape.

    정규화는 도메인 :class:`ChamberProgress` SSOT 위임(heartbeat-carried progress 와
    동일 shape). 빈/None raw 는 zero 스냅샷(``is_running=False``)으로 표현한다."""
    progress = ChamberProgress.from_raw(raw) or ChamberProgress.zero()
    return progress.as_dict()
