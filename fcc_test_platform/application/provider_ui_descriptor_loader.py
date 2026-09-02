"""Provider UI descriptor 를 **런타임 아티팩트**로 읽는다 (2026-09-03).

**왜 import 가 아니라 파일인가.** 2026-09-03 실측: ``fcc_test_platform.api_app``
의 ``create_app()`` 이 ``application.headless.provider_ui_descriptor`` 를 모듈 레벨에서
import 했고, 그 모듈은 **provider 소유**(자기 docstring 이 그렇게 적는다)이며
``column_names``(279줄, provider 의 Excel SSOT)와 ``domain.services.chamber_config_policy``
를 끌어온다. 즉 중앙 스택이 provider 저장소를 **코드로** 필요로 했다 — ``/app/src`` 를
``sys.path`` 에서 빼면 ``create_app()`` 이 바로 그 줄에서 ``ModuleNotFoundError`` 로 죽었다.

그 결합을 끊는 방향은 이미 그 모듈이 적어 두었다:

    "The platform renders these descriptors schema-first via the shared contract
     — it never imports this module's builder (only the composition root wires it
     into the platform registry)."

**합성 루트가 registry 에 배선한다**가 설계이고, 그 배선을 import 로 하던 것이 결합의
전부였다. 여기서는 같은 배선을 **파일**로 한다. descriptor 는 순수 데이터이고(렌더된
크기 실측 9,988 bytes) 그 스키마는 이미 공유 계약 레인에 있다
(``fcc_test_contracts.common.provider_ui_descriptor_schema``).

⚠️ **이 저장소는 descriptor 를 담지 않는다.** 담으면 provider 소유 내용의 **두 번째
사본**이 되고, 사본은 갈라진다 — 2026-09-01 에 실측된 그대로다(게이트웨이 업로드 천장이
한쪽에만 고쳐져 있었다). 파일은 provider 배포가 놓는다. 그래서 ``config/provider-ui/``
는 git 에서 무시되고 README 만 추적된다.

⚠️ **비어 있음을 조용히 통과시키지 않는다.** provider 가 하나도 없는 것은 정당한 상태다
(아직 아무 provider 도 배포되지 않았다). 그러나 «배포했는데 경로가 틀렸다»와 모양이
같으므로, 무엇을 어디서 몇 개 읽었는지 **매 기동 로그로 말한다.** 보관 축이 «빈 결과가
이상 없음처럼 읽힌다»는 이유로 loud-fail 을 택한 것과 같은 규율이다.

dependency-free: stdlib 만 (json / os / pathlib / logging).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterable, Mapping


__all__ = [
    'PROVIDER_UI_DIR_ENV',
    'DEFAULT_PROVIDER_UI_DIR',
    'ProviderUiDescriptorSourceError',
    'load_provider_ui_descriptors',
    'workbench_areas',
]

logger = logging.getLogger(__name__)

#: 운영자/배포가 경로를 정한다. compose 가 이 값을 컨테이너로 넘긴다.
PROVIDER_UI_DIR_ENV = 'FCC_PLATFORM_PROVIDER_UI_DIR'
#: 이미지 안의 관례 경로 — compose 가 호스트의 ``config/provider-ui`` 를 여기 마운트한다.
DEFAULT_PROVIDER_UI_DIR = '/app/config/provider-ui'


class ProviderUiDescriptorSourceError(RuntimeError):
    """descriptor 파일을 읽었지만 쓸 수 없다. **빈 registry 로 강등하지 않는다.**

    없는 것과 깨진 것은 다른 사실이다. 깨진 파일을 «provider 없음»으로 접으면 화면이
    ``[]`` 을 보여주고, 운영자는 배포가 안 된 줄 알고 배포를 다시 한다.
    """


def load_provider_ui_descriptors(
    directory: 'str | os.PathLike | None' = None,
    *,
    environ: 'Mapping[str, str] | None' = None,
) -> dict[str, dict]:
    """``provider_id -> descriptor`` 를 디렉터리에서 읽는다.

    키는 **파일 안의 ``provider_id``** 다. 파일 이름을 키로 쓰면 이름과 내용이 두 번째
    SSOT 가 되어, 파일을 잘못 이름 붙인 배포가 **다른 provider 의 화면**을 그린다.
    """
    env = os.environ if environ is None else environ
    raw = directory if directory is not None else env.get(
        PROVIDER_UI_DIR_ENV, DEFAULT_PROVIDER_UI_DIR,
    )
    root = Path(raw)
    if not root.is_dir():
        logger.warning(
            'provider UI descriptor 디렉터리가 없다: %s — provider 화면이 비어 '
            '보인다. 아직 아무 provider 도 배포되지 않았다면 정상이다. '
            '(%s 로 경로를 바꾼다)', root, PROVIDER_UI_DIR_ENV,
        )
        return {}

    descriptors: dict[str, dict] = {}
    sources: dict[str, Path] = {}
    for path in sorted(root.glob('*.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ProviderUiDescriptorSourceError(
                f'provider UI descriptor 를 읽지 못했다: {path} — {exc}'
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderUiDescriptorSourceError(
                f'provider UI descriptor 는 객체여야 한다: {path}'
            )
        provider_id = str(payload.get('provider_id') or '').strip()
        if not provider_id:
            raise ProviderUiDescriptorSourceError(
                f'provider UI descriptor 에 provider_id 가 없다: {path} — '
                '이 값이 registry 키이므로 없으면 배선할 수 없다'
            )
        if provider_id in descriptors:
            # 조용히 덮으면 «둘을 놓았는데 하나만 보인다»가 되고, 어느 쪽이 이겼는지는
            # 파일 정렬 순서라는 아무도 안 보는 축이 정한다.
            raise ProviderUiDescriptorSourceError(
                f'provider_id {provider_id!r} 가 두 파일에 있다: '
                f'{sources[provider_id]} 와 {path}'
            )
        descriptors[provider_id] = payload
        sources[provider_id] = path

    logger.info(
        'provider UI descriptor %d개를 %s 에서 읽었다: %s',
        len(descriptors), root, sorted(descriptors) or '(없음)',
    )
    return descriptors


def workbench_areas(descriptors: Mapping[str, dict]) -> tuple[str, ...]:
    """등록된 descriptor 들이 선언하는 progress area 이름.

    **파생이다.** descriptor 의 ``workbench_area_technologies`` 키가 곧 area 이름이므로
    (실측: ``{"unlicensed_conducted": ["BT","BLE","DTS","UNII"]}``), 상수를 따로 두면
    provider 가 area 이름을 바꿀 때 한쪽만 따라간다.
    """
    areas: list[str] = []
    for descriptor in descriptors.values():
        mapping = descriptor.get('workbench_area_technologies')
        if isinstance(mapping, Mapping):
            areas.extend(str(area) for area in mapping)
    return tuple(sorted(set(areas)))


def sole_workbench_area(
    descriptors: Mapping[str, dict], *, fallback: str,
) -> str:
    """area 가 정확히 하나면 그것, 아니면 ``fallback``.

    ⚠️ **이 함수가 존재하는 것 자체가 미해결 축의 표시다.** 진행률 서비스는 지금
    area 를 **하나만** 받는다(``PublishedPlanExpectationService(progress_area=…)``).
    provider 가 둘 이상이 되면 그 시그니처가 틀린 것이지 이 함수가 틀린 것이 아니다 —
    그때 서비스를 provider 축으로 열어야 한다. 지금 조용히 첫 번째를 고르면 그 사실이
    **아무 데도 드러나지 않으므로**, 여럿일 때는 fallback 을 쓰고 경고한다.
    """
    areas = workbench_areas(descriptors)
    if len(areas) == 1:
        return areas[0]
    if len(areas) > 1:
        logger.warning(
            'progress area 가 %d개다 (%s). 진행률 서비스는 아직 하나만 받으므로 '
            '%r 로 간다 — provider 축으로 열어야 하는 자리다.',
            len(areas), areas, fallback,
        )
    return fallback
