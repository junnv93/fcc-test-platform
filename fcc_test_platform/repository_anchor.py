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

from fcc_test_contracts.common.tree_artifacts import LAYOUT_RECORD_NAME

#: 「여기가 상자 루트다」라고 말하는 파일들. **하나로는 부족하다.**
#:
#: ⚠️ 실측 2026-09-06 — 이 헬퍼의 첫 판은 ``pyproject.toml`` 하나만 인정했고,
#: 그래서 **컨테이너 이미지 안에서 마이그레이션 러너가 죽었다**:
#:
#:     {"ok": false, "error": "마이그레이션 디렉터리가 없다: /docs/platform/migrations"}
#:
#: 원인은 이미지가 그 표식을 **의도적으로 지운다**는 데 있다
#: (``infra/central/Dockerfile.api``)::
#:
#:     RUN pip install --no-deps . && rm -rf … /app/pyproject.toml /app/README.md …
#:
#: 소스를 남기면 ``/app`` 이 cwd 이므로 휠보다 먼저 import 되어 **휠을 조용히 가린다**
#: — 그 삭제는 옳다. 그리고 같은 Dockerfile 이 바로 그 자리를 메우려고
#: ``.extraction-layout.json`` 을 싣는다: *"상자 표식을 함께 싣는다 … 그래서 부르는
#: 쪽이 경로를 몰라도 된다."*
#:
#: 즉 이미지는 상자 표식을 **가지고 있었고**, 이 헬퍼가 그것을 표식으로 세지 않았을
#: 뿐이다. ``_tree_root`` 는 이미 그 기록을 **가장 먼저** 찾으므로, 앵커가 상자 안을
#: 가리키기만 하면 나머지는 그대로 성립한다.
#:
#: ⚠️ 순서는 후보 디렉터리가 바깥, 표식이 안이다 — **가장 가까운 트리가 이긴다.**
#: 표식을 바깥 고리로 돌리면 먼 조상의 ``pyproject.toml`` 이 가까운 상자를 이긴다.
BOX_MARKERS = ('pyproject.toml', LAYOUT_RECORD_NAME)


def repository_anchor(module_file: str | Path) -> Path:
    """``resolve_repo_artifact`` 에 넘길 앵커 파일.

    cwd 나 그 조상 중 :data:`BOX_MARKERS` 중 하나를 가진 첫 트리를 «다루는 곳»으로
    본다 — 저장소 체크아웃은 ``pyproject.toml`` 로, 배송된 상자와 컨테이너 이미지는
    ``.extraction-layout.json`` 으로 답한다.
    배송된 상자 안에서 돌 때도 그 상자가 답이 되므로 상자의 배치 기록이 그대로 쓰인다.

    ⚠️ ``.git`` 을 요구하지 **않는다** — 배송된 상자는 저장소가 아니라 트리이고,
    ``.git`` 을 요구하면 상자 안에서 이 헬퍼가 자기 상자를 못 찾는다.

    저장소 밖(예: 운영자가 ``/tmp`` 에서 부름)이면 ``module_file`` 로 돌아간다 —
    그것이 이 헬퍼가 생기기 전의 동작이므로, 이 변경은 «틀렸던 자리만» 움직인다.
    """
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        for marker in BOX_MARKERS:
            if (candidate / marker).is_file():
                return candidate / marker
    return Path(module_file)
