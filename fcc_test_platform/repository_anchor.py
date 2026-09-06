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

■ ⚠️ 저장소 «밖»에서는 이 헬퍼의 fallback 이 대부분 **도달하지 않는다**

아래 fallback 은 "저장소 밖이면 옛 동작으로 돌아간다" 고 적지만, 그 말이 참인 자리는
생각보다 좁다. 형제 헬퍼 ``_repository_root`` 가 **같은 질문에 반대로 답하기** 때문이다 —
이쪽은 조용히 물러서고, 저쪽은 ``RuntimeError`` 로 거부한다. 그리고 저쪽이 모듈 «수준»에서
먼저 돈다.

실측 (platform ``84f2955`` · 최상위 모듈 77개 · **선언된 핀만으로 만든 venv**
— ``fcc-test-contracts@v0.1.22`` · ``fcc-test-kernel@kernel-v0.5.0``):

    cwd = 저장소 안    import 성공 77 / 77
    cwd = 저장소 밖    import 성공 69 / 77   (RuntimeError **8**)

⚠️ **「핀만으로 만든 venv」여야 한다.** 처음에 이 값을 소비 레인의 공용 venv 에서 쟀더니
안이 36/38, 밖이 30/38 이었고 세 모듈이 ``ImportError`` 로 죽었다. 그 venv 에는
``fcc-test-kernel 0.3.0`` · ``fcc-test-contracts 0.1.12`` 가 들어 있었다 — 선언보다
한참 뒤다. 그 셋을 「이 배포판이 자기 핀보다 앞선 형제를 요구한다」로 읽을 뻔했는데,
태그를 직접 열어 보니 ``CUSTODY_EVENT_FIELDS`` 도 ``extraction_import_boundaries`` 도
**핀 안에 있다**. 낡은 설치본이 만든 그림이었다. 로컬 red 는 «트리 × 설치본»이고,
설치본을 안 고정하면 트리에 없는 결함을 트리 탓으로 읽는다.

⚠️ **그 7 은 결함이 아니다.** ``_repository_root`` 가 *"이 도구는 저장소 안에서 실행해야
한다"* 는 메시지와 함께 «의도적으로» 거부한 것이고, 그 판단은 옳다 — 틀린 뿌리 위에서
파일을 세면 「대상이 없다」와 「경로가 맞다」가 구별되지 않는다.

⚠️ 그래서 이 헬퍼를 고칠 것이 아니라 **둘의 관계를 알고 써야 한다.** ``repository_anchor``
를 쓰는 다섯 모듈 중 셋(``cross_session_result_selection_evidence_cli`` ·
``db_migration_runner_cli`` · ``export_central_db_ddl_cli``)은 큰 거부가 먼저 나므로
아래 fallback 이 **저장소 밖에서 실행되지 않는다**. 실제로 도달하는 것은 둘
(``cutover_bundle_cli`` · ``db_migrate_cli``)뿐이다.

⚠️ ``_repository_root`` 는 **사본이 넷**이다 (``central_db_live_proof_cli:87`` ·
``cross_session_result_selection_evidence_cli:66`` · ``cutover_live_workflow_cli:40`` ·
``export_central_db_ddl_cli:36``); 나머지 세 모듈은 그중 하나를 import 한다. 즉 "저장소
밖이면 어떻게 하는가" 에 대한 답이 이 패키지 안에 **다섯 벌**(넷 + 이 헬퍼) 있고 정책이
갈린다. 하나로 모으는 것은 이 파일의 범위가 아니지만, 그 사실을 모르면 여기만 고치고
「닫혔다」고 읽게 된다.

⚠️ **그리고 「두 자리」로도 부족하다 — rig 자체가 답을 바꾸는 자리가 있다.** 형제 세션
``fcc-delivery-final-91`` 이 다른 rig(비-editable 설치 + 트리 «밖» venv)로 재서 찾았다:
``api_composition`` 은 저장소 밖에서 ``FileNotFoundError`` 로 죽는데, **내 rig 에서는
성공한다**. ``rbac_role_catalog._discover_schema_path`` 가 SSOT 를 «모듈의 조상» 다음
«cwd 의 조상» 순으로 찾으므로, 모듈이 소스 트리에 있으면(editable) 찾고 site-packages 에
있으면 못 찾는다. 배포 이미지에서는 ``FCC_PLATFORM_SCHEMA_PATH`` 가 답한다.

    editable (CI: ``pip install -e '.[test]'``)   밖 68+1 실패 중 api_composition 성공
    비-editable + 트리 밖 venv                    api_composition 실패

⚠️ 그 자리는 두 집합(거부 · 못 잼) **사이로 빠진다** — 안에서 import 되고 밖에서 거부가
아닌 예외로 죽으므로 어느 쪽도 아니다. 봉인은 두 rig 모두에서 초록이었고, 그 초록이
차이를 가렸다. 그래서 세 번째 집합(``DECLARED_OUTSIDE_OTHER_FAILURES``)을 두었다.

⚠️ **검증은 두 자리에서 해야 한다.** 이 헬퍼를 겨누는 봉인
(``tests/test_repository_artifacts_resolve_for_the_caller.py``)은 pytest 가 저장소 «안»에서
돌기 때문에 위 표의 아랫줄을 **구조적으로 못 본다** — 관측자가 관측 대상 안에 있다.
한 자리에서만 초록이면 그 초록은 절반이다. 저장소 밖 축은 형제 세션
``fcc-delivery-final-91`` 이 소비 레인에서 독립으로 먼저 재현했고(35 모듈 기준 7), 위 표는
핀 venv 에서 다시 잰 것이다 — 두 축이 같은 결론에 왔다.
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
