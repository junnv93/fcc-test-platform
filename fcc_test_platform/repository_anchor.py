"""해소의 기준점 — 「모듈이 사는 곳」이 아니라 「다루는 곳」.

⚠️ **이 파일은 실측된 결함에서 나왔다** (2026-09-06). 다섯 모듈이
``resolve_repo_artifact(__file__, 'docs/platform/...')`` 로 저장소 산출물을 찾았는데,
그 함수의 첫 인자는 *어느 트리에 묻는가* 를 정한다. 배포판이 설치되면 ``__file__`` 은
``site-packages`` 이므로 그 트리에는 ``docs/`` 를 가진 조상이 없고, 걷기는 최외곽까지
올라가 ``/docs/platform/migrations`` 를 답한다. 실측 — 소비 레인(모노레포)에서:

    db_migrate_cli.DEFAULT_MIGRATIONS_DIR                       → /docs/platform/migrations
    export_central_db_ddl_cli.DEFAULT_SCHEMA                    → /docs/platform/central_db_schema.v1.json
    db_migration_runner_cli.DEFAULT_SCHEMA_PATH                 → /docs/platform/central_db_schema.v1.json
    cross_session_result_selection_evidence_cli.MIGRATIONS_DIR  → /docs/platform/migrations

넷 다 ``exists() == False`` 다. 즉 **이 저장소 없이 도구를 부를 수 있게 만든 웨이브가,
그 도구들이 «부르는 쪽의 산출물»을 찾지 못하는 상태로 끝나 있었다.** 진입점은 생겼고
import 도 되므로 어떤 게이트도 이것을 잡지 못한다 — 실제로 «소비 레인에서» 돌려 봐야
드러난다.

``cross_session_result_selection_evidence_cli._repository_root`` 가 이미 옳은 축을
문장으로 적어 두었다: *"모듈이 사는 곳이 아니라 **다루는 곳**이 기준이다."* 그런데 같은
파일이 두 축을 섞어 썼다 — ``ROOT`` 는 cwd 에서, ``MIGRATIONS_DIR`` 은 ``__file__`` 에서.
이 헬퍼는 그 축 하나를 모든 자리에 준다.
"""
from __future__ import annotations

from pathlib import Path


def repository_anchor(module_file: str | Path) -> Path:
    """``resolve_repo_artifact`` 에 넘길 앵커 파일.

    cwd 나 그 조상 중 ``pyproject.toml`` 을 가진 첫 트리를 «다루는 곳»으로 본다.
    배송된 상자 안에서 돌 때도 그 상자가 답이 되므로 상자의 배치 기록이 그대로 쓰인다.

    ⚠️ ``.git`` 을 요구하지 **않는다** — 배송된 상자는 저장소가 아니라 트리이고,
    ``.git`` 을 요구하면 상자 안에서 이 헬퍼가 자기 상자를 못 찾는다.

    저장소 밖(예: 운영자가 ``/tmp`` 에서 부름)이면 ``module_file`` 로 돌아간다 —
    그것이 이 헬퍼가 생기기 전의 동작이므로, 이 변경은 «틀렸던 자리만» 움직인다.
    """
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / 'pyproject.toml').is_file():
            return candidate / 'pyproject.toml'
    return Path(module_file)
