"""중앙 플랫폼 계약 — 표면 레지스트리와 병합.

``api_contracts`` facade 가 노출하는 ``PLATFORM_API_*`` 표는 표면 모듈의 같은 이름
표들을 합친 것이다. 병합은 **중복 키를 조용히 덮어쓰지 않는다** — 두 표면이 같은
operationId 나 스키마 이름을 선언하면 import 시점에 loud 하게 실패한다. 조용한
덮어쓰기는 계약 하나를 통째로 사라지게 하면서 아무 게이트도 붉히지 않는다.
"""
from __future__ import annotations

from types import ModuleType

from application.central_contract import (
    surface_auth,
    surface_samples,
    surface_reports,
    surface_artifact_custody,
    surface_reference_catalog,
    surface_project_results,
    surface_chambers,
    surface_providers,
    surface_projects,
)


class DuplicateContractKeyError(KeyError):
    """두 표면이 같은 키를 선언했다 — 병합이 하나를 조용히 잃는 형상."""


#: 선언 순서는 병합 순서일 뿐 의미가 없다 — 생성 아티팩트는 ``sort_keys=True`` 로
#: 덤프되고, 라우터는 이름으로 조회하며, 순서 민감한 route 쌍은 0개다(실측).
SURFACE_MODULES: tuple[ModuleType, ...] = (
    surface_auth,
    surface_samples,
    surface_reports,
    surface_artifact_custody,
    surface_reference_catalog,
    surface_project_results,
    surface_chambers,
    surface_providers,
    surface_projects,
)


def merge_surface_table(
    attribute: str,
    modules: tuple[ModuleType, ...] = SURFACE_MODULES,
) -> dict:
    """표면 모듈의 ``attribute`` 표를 합친다. 키가 겹치면 예외."""
    merged: dict = {}
    owner: dict = {}
    for module in modules:
        table = getattr(module, attribute, None)
        if table is None:
            continue
        for key, value in table.items():
            if key in merged:
                raise DuplicateContractKeyError(
                    f"{attribute}[{key!r}] declared by both "
                    f"{owner[key]} and {module.__name__}"
                )
            merged[key] = value
            owner[key] = module.__name__
    return merged


def surface_prefixes() -> dict[str, tuple[str, ...]]:
    """모듈 이름 → 그 모듈이 소유하는 route prefix 집합."""
    return {m.__name__.rsplit(".", 1)[-1]: m.SURFACE_PREFIXES for m in SURFACE_MODULES}
