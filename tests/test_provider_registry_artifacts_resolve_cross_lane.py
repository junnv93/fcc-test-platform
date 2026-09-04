"""등재된 provider 의 계약 아티팩트가 **발행 레인에 실재하는가**.

⚠️ **이 축이 없던 동안 두 상자가 동시에 초록이었다.** 그 침묵의 형태는
``scripts/check_headless_provider_registry.py``(계약 레인) 자신의 docstring 이
이미 이름으로 적어 두었다 — 옛 판이 *"this file spans two repositories"* 라고
**산문으로** 경고했고, **아무도 그것을 red 로 바꾸지 않았다**. 그래서 그 파일은
망가진 채로 있었고 두 레인 모두 초록을 보고했다.

실측 2026-09-04: platform 작업트리의 레지스트리가 어느 트리에도 없는 아티팩트
(``kc_unlicensed_headless_api_contract.example.json``)를 가리키는데, 계약 레인의
체커를 **손으로** 물려야만 보였다(exit 2 · ``providers: []`` — 새 항목 하나가
아니라 **레지스트리 전체가 로드 실패**한다). 두 레인의 pytest 는 전부 초록이었다.

## 왜 이 질문을 여기서 물을 수 있나 — ``phase38`` 의 유보를 뒤집지 않는다

``test_provider_registry_phase38.py`` 는 *"그 아티팩트들은 이 트리에 없으므로,
여기서 «이 아티팩트가 존재하나» 를 묻는 것은 정직한 답이 없다"* 고 적었다. 그
문장은 :func:`resolve_repo_artifact` 에 대해 **참이다** — 그것은 «**내** 트리가
이것을 어디 뒀나» 를 묻고, platform 트리는 계약 아티팩트를 담지 않는다.

:func:`resolve_dependency_artifact` 는 **묻는 트리를 바꾼다** — «나를 배송한
의존 레인이 이것을 어디 뒀나». 그 함수의 docstring 이 정확히 이 용도로 자신을
설명한다. 그러므로 이 검사는 phase38 의 경계를 넘지 않는다: *어느 provider 가
등재됐나*(내용)는 여전히 이 레인의 것이고, *그 이름이 가리키는 아티팩트가
실재하나*는 **발행 레인에게 그 레인의 해소기로** 묻는다.

## ⚠️ 결과가 없을 때 red 가 되는 것이 이 축의 전부다

``fcc-test-contracts/docs/OPEN-QUESTIONS.md`` §1 이 남은 설계 셋 중 **셋째가
핵심**이라고 못박는다 — *"결과가 없거나 낡았을 때 무엇이 red 가 되는가."* 없으면
*"결과가 오지 않는 상태와 결과가 통과한 상태가 같은 초록"* 이다.

그래서 이 파일에는 **skip 이 하나도 없다**. 어느 설치 형상에서도 「못 물어봤다」가
초록이 되지 않는다:

* ``fcc_test_contracts`` 자체를 import 못 하면 이 모듈은 **수집 에러**로 넘어지고,
  ``scripts/lane_check_plugin.py`` 의 ``pytest_collectreport`` 가 그것을 실패
  집합에 적는다(요약줄 ``grep '^FAILED '`` 은 수집 에러를 못 본다 — 그 플러그인이
  존재하는 이유다).
* :class:`DependencyTreeUnavailable` 은 잡되 **skip 이 아니라 실패로** 적는다.

## 두 설치 형상에서 실제로 무엇이 일어나는가 — 실측 2026-09-04

⚠️ **처음 이 파일은 *「휠로 설치되면 트리가 없어 축이 못 돈다」* 고 적었다. 재보니
틀렸다** — 계약 레인의 휠은 ``artifacts/`` 를 **패키지 안에** 실어 보내고
(``fcc_test_contracts/artifacts/…``), :func:`resolve_dependency_artifact` 는
:data:`PACKAGE_LAYOUT_RECORD_NAME` 기록을 통해 그것을 찾아낸다. 축은 **두 형상 모두에서
돈다**. 그 규칙(``.claude/rules/check-axis-blindness.md`` §*「그 차이는 X 때문이다」*)
그대로, 설명이 요구하는 전제를 적고 확인해서 나온 정정이다.

===================  ==========================  ==================================
설치 형상            실재하는 아티팩트            **없는** 아티팩트
===================  ==========================  ==================================
트리(sibling)        해소 OK                     경로를 돌려주고 ``exists()`` False
휠(site-packages)    해소 OK (패키지 안 사본)     :class:`DependencyTreeUnavailable`
===================  ==========================  ==================================

즉 **없는 아티팩트가 형상에 따라 다른 모양으로 나타난다.** 둘 다 red 여야 하고, 둘 다
*같은 결함*이므로 아래 존재 검사는 두 모양을 **한 목록으로 모아** 보고한다. 형상이
바뀌었다고 결함이 사라진 것처럼 보이면 그것이야말로 이 축이 막으려는 침묵이다.

⚠️ **눈에 보이는 수리가 함정이다.** 이 검사가 red 일 때 아티팩트 파일을 계약
트리에 만들어 넣지 마라. ``provider_registry._resolve_artifact_path`` 는
``registry_path.parent/path`` 폴백을 갖고 있어 **레지스트리 옆에 사본을 두면 코드
변경 0으로 초록이 된다** — 그리고 그것은 운영자가 2026-08-31 에 기각한 안 「다」
(provider 아티팩트를 이 트리로 복사)를 게이트만 초록으로 만들어 되살리는 것이다.
판정은 안 「나」: **provider 가 자기 레포에서 자기 계약을 검사하고, 중앙은 그
결과만 받는다.** 이 검사가 red 라면 답은 *등재를 되돌리거나* 안 「나」를 구현하는
것이지, 사본을 두는 것이 아니다.
"""
import json
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / 'src'))

# ⚠️ 모듈 최상위에서 import 한다. 계약 레인이 없으면 **수집 에러**로 red 가 되어야
# 하고, 함수 안에서 잡아 skip 하면 그 순간 이 축이 사라진다.
from fcc_test_contracts.common.tree_artifacts import (  # noqa: E402
    DependencyTreeUnavailable,
    resolve_dependency_artifact,
    resolve_repo_artifact,
)

# 레포 어휘 그대로 부른다. 배송 트리에서는 레이아웃 기록이 `config/` 로 답한다
# (실측 2026-09-04) — phase38 과 같은 철자를 쓰는 이유는 두 검사가 **같은 문서**를
# 본다는 것이 읽는 사람에게 보여야 하기 때문이다.
REGISTRY_PATH = resolve_repo_artifact(__file__, 'docs/api/headless_provider_registry.json')


#: 계약 레인이 **자기 자신을 위해** 발행하는 아티팩트. OPEN-QUESTIONS §1 의 표에서
#: 발행자가 *"우리 (SSOT 자신)"* 인 유일한 항목이고, 예시(`.example.json`)가 아니다.
#: 그래서 "이 축이 돌 수 있나" 를 묻는 데 쓸 수 있는 유일한 이름이다 — 레지스트리
#: 내용이 어떻게 바뀌든 이것은 계약 레인이 존재하는 한 존재한다.
SSOT_ARTIFACT = 'fcc_test_contracts/artifacts/headless_api_contract.v1.json'


class TestRegistryArtifactsResolveInPublishingLane(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
        self.providers = self.registry.get('providers') or []

    def test_publishing_lane_is_reachable_at_all(self):
        """**답이 이미 알려진 대상**으로 이 축의 눈금을 맞춘다.

        ``.claude/rules/check-axis-blindness.md`` §*「새 측정 도구는 답이 이미 알려진
        대상에서 먼저 재라」*. 아래 존재 검사는 답이 **미지**인 대상(등재된 provider
        들)에 돌린다. 그 전에 답이 **알려진** 대상 하나로 도구가 옳은지 본다 —
        계약 레인이 자기 SSOT 로 발행하는 아티팩트는 그 레인이 존재하는 한 존재한다.

        ⚠️ 이것이 없으면 「전부 해소됐다」와 **「해소기가 무엇도 못 찾는다」**가
        존재 검사의 출력 축에서 같은 값이다(레지스트리의 모든 항목이 동시에
        사라지는 형상 — 예컨대 아티팩트 디렉터리가 통째로 안 실린 휠). 그때 존재
        검사는 *실패 목록이 비었으므로* 초록이다.

        red 일 때의 처방은 **provider 등재가 아니라 설치 형상**이다.
        """
        prescription = (
            '⚠️ 이것은 provider 등재의 문제가 **아니라** 설치 형상의 문제다. '
            '아티팩트를 만들어 넣지 마라 — 계약 레인이 자기 SSOT 아티팩트조차 '
            '내놓지 못하는 상태다.'
        )
        try:
            resolved = resolve_dependency_artifact(SSOT_ARTIFACT)
        except DependencyTreeUnavailable as exc:
            self.fail(
                f'계약 레인이 자기 SSOT 아티팩트를 내놓지 못한다 — '
                f'이 축은 지금 아무것도 판정하지 못한다.\n  {exc}\n{prescription}'
            )
        self.assertTrue(
            resolved.exists(),
            f'계약 레인의 SSOT 아티팩트에 닿지 못한다 — 이 축은 지금 돌지 않는다.\n'
            f'  등재  {SSOT_ARTIFACT}\n'
            f'  해소  {resolved}\n{prescription}',
        )

    def test_registry_names_at_least_one_provider(self):
        """비-공허성 팔.

        ⚠️ 이 팔을 쓸 때 물어야 하는 것(`.claude/rules/check-axis-blindness.md`
        §비-공허성 팔이 성공을 금지하는 경우): *이 검사가 성공하면 이 팔이 red 가
        되는가?* 되지 않는다 — provider 가 **몇이든** 이 팔은 초록이고, 0일 때만
        red 다. 그러므로 아래 검사의 성공을 금지하지 않는다.

        이 팔이 없으면 ``providers`` 가 빈 목록일 때 아래 루프가 0회 돌고
        **아무것도 안 본 것이 전부 통과와 같은 초록**이 된다.
        """
        self.assertGreater(
            len(self.providers), 0,
            f'{REGISTRY_PATH} 가 provider 를 하나도 등재하지 않았다 — '
            '아티팩트 검사가 0회 돌면 그것은 통과가 아니다',
        )

    def test_every_registered_artifact_exists_in_the_publishing_lane(self):
        """등재된 모든 ``contract_artifact`` 가 발행 레인에 실재해야 한다.

        ⚠️ 첫 실패에서 멈추지 않는다 — 계약 레인 체커는 ``load_provider_registry``
        가 첫 결함에서 raise 하므로 ``providers[3]`` 하나만 이름을 얻고 나머지는
        보이지 않았다(실측 2026-09-04). 여기서는 **전부** 모아 보고한다. 등재가
        여럿 깨졌을 때 한 번에 보이는 편이 고치는 사람에게 정직하다.
        """
        missing = []
        for index, provider in enumerate(self.providers):
            rel = provider.get('contract_artifact')
            self.assertTrue(
                rel, f'providers[{index}] 에 contract_artifact 가 없다',
            )
            # ⚠️ 잡되 **실패로 적는다**. 휠 형상에서 없는 아티팩트는 경로가 아니라
            # 이 예외로 나타난다(위 표) — 잡지 않으면 첫 결함에서 루프가 끊겨
            # 나머지 provider 가 안 보이고, skip 하면 축이 사라진다.
            try:
                resolved = resolve_dependency_artifact(rel)
                if resolved.exists():
                    continue
                reason = f'해소  {resolved}  ← 없다'
            except DependencyTreeUnavailable:
                reason = '해소  실패 — 발행 레인이 이 이름을 트리로도 패키지로도 내놓지 않는다'
            missing.append(
                f"  providers[{index}] {provider.get('provider_id')!r}\n"
                f"    등재  {rel}\n"
                f"    {reason}"
            )

        self.assertEqual(
            missing, [],
            '등재된 계약 아티팩트가 발행 레인에 없다:\n'
            + '\n'.join(missing)
            + '\n\n⚠️ 아티팩트 파일을 만들어 넣어 초록으로 만들지 마라 — '
              'OPEN-QUESTIONS §1 의 기각된 안 「다」다. '
              '등재를 되돌리거나 안 「나」(발행처가 검사하고 결과만 수신)를 구현하라.',
        )


if __name__ == '__main__':
    unittest.main()
