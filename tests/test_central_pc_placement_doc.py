"""중앙 PC 배치표 — 런북이 «무엇을 어디에 두는가» 를 적고, 그 주장이 파생되는가.

**왜 이 검사가 있는가 (운영자 질문 2026-09-02).** 런북의 전제는 한 줄이었다 —
*"중앙 PC 에 WSL Ubuntu + Docker 가 설치돼 있고 **repo 가 배치돼 있다**"*. 어느 repo
인지, 공유 레인 둘도 클론해야 하는지, 웹 이미지는 어디서 오는지가 **어디에도 없다**.
운영자가 정확히 그 셋을 물었고, 답은 저장소 안에 흩어져 있었다(매니페스트 · compose
주석 · `requirements-central.txt` 주석). **흩어진 답은 없는 답과 같다** — 런북만 읽는
사람에게는 존재하지 않는다.

**세 주장을 각각 다른 소스에서 파생해 대조한다.** 산문을 산문으로 검사하면 문서가
자기를 증명하게 되므로, 각 주장이 참인 근거를 **문서 밖**에서 읽는다:

⚠️ **2026-09-03 이관 — 파생 셋 중 둘이 뒤집혔다.** 이 파일은 provider 저장소에서 왔고,
거기서 참이던 두 문장이 여기서는 거짓이다. 옮기면서 «이 저장소» 가 가리키는 대상이
바뀌었기 때문이다 — 같은 웨이브에서 compose 주석과 재배포 런북 §5 도 같은 형태로
뒤집혀 있었다.

===========================  ==========================================
주장                          파생 소스
===========================  ==========================================
공유 레인은 클론 안 한다      `requirements-central.txt` 의 ``git+https`` 핀
웹 이미지는 **여기서 만든다**  compose 의 ``web`` 서비스에 ``build:`` **존재**
이 저장소는 **간 쪽이다**      매니페스트 ``extraction_target == True``
===========================  ==========================================

⚠️ **비-공허성이 이 파일의 절반이다.** 파생 소스를 못 읽으면 세 검사가 *"비교할 것이
없어서"* 통과한다 — 그것은 «문서가 맞다» 와 «검사가 아무것도 안 봤다» 가 같은 값이
되는 자리다. 그래서 각 파생을 **먼저 단언**한다.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNBOOK = (
    _REPO_ROOT / 'docs' / 'operations'
    / 'central-pc-operational-validation-runbook.md'
)
_REQUIREMENTS = _REPO_ROOT / 'requirements-central.txt'
_COMPOSE = _REPO_ROOT / 'infra' / 'docker-compose.central.yml'
_MANIFEST = (
    _REPO_ROOT / 'docs' / 'api'
    / 'headless_contract_extraction_manifest.v1.json'
)

#: 배치표가 사는 절. 이름을 상수로 두는 이유 — 검사 메시지가 운영자에게 *어디를*
#: 고치라고 말할 수 있어야 한다.
_SECTION_HEADING = '## ⚠️ 저장소 배치 (2026-09-03 변경)'


def _runbook_text() -> str:
    return _RUNBOOK.read_text(encoding='utf-8')


def _pinned_lane_repos() -> list[str]:
    """`requirements-central.txt` 가 ``git+https`` 로 고정한 레인 이름들."""
    text = _REQUIREMENTS.read_text(encoding='utf-8')
    return re.findall(
        r'^([A-Za-z0-9_.-]+)\s*@\s*git\+https://\S+', text, flags=re.MULTILINE,
    )


def _web_service_block() -> str:
    """compose 의 ``web:`` 서비스 블록(다음 최상위 서비스 전까지)."""
    text = _COMPOSE.read_text(encoding='utf-8')
    start = text.index('\n  web:\n')
    rest = text[start + 1:]
    nxt = re.search(r'\n  [a-z][a-z0-9-]*:\n', rest)
    return rest[: nxt.start()] if nxt else rest


def _non_extracted_lanes() -> list[str]:
    manifest = json.loads(_MANIFEST.read_text(encoding='utf-8'))
    return sorted(
        name for name, spec in manifest['repositories'].items()
        if not spec.get('extraction_target')
    )


class TestTheDerivationSourcesAreReadable(unittest.TestCase):
    """⚠️ 먼저 파생이 실제로 성립하는지 본다.

    이것이 없으면 아래 세 검사가 *"비교할 것이 없어서"* 통과할 수 있고, 그때
    «문서가 맞다» 와 «아무것도 안 봤다» 가 같은 값이 된다.
    """

    def test_requirements_pins_at_least_one_shared_lane(self):
        """⚠️ **개수를 못 박지 않는다** (2026-09-03 정정).

        옛 판은 ``['fcc-test-contracts', 'fcc-test-platform']`` 를 상등으로 단언했고,
        같은 날 `fcc-test-kernel` 이 생기자 red 가 됐다 — **결함이 아니라 레인이 는
        것**인데 게이트가 그것을 결함으로 말했다. 손 목록은 낡는다.

        여기서 필요한 것은 목록이 아니라 **비-공허성**이다: 핀이 0건이면 아래
        「배치 절이 모든 핀을 언급한다」가 공허하게 통과한다. 실제 대조는 그쪽이 한다.
        """
        pinned = _pinned_lane_repos()
        self.assertTrue(
            pinned,
            'requirements-central.txt 에 git+https 핀이 0건이다 — 그러면 배치 절 검사가 '
            '비교할 것이 없어 공허하게 통과한다.',
        )

    def test_the_web_service_has_a_build_stanza(self):
        """판정 축은 **YAML 키**이지 낱말이 아니다.

        ⚠️ 첫 판은 블록 전체에서 ``'build:'`` 문자열을 찾았고, 그 블록의 **주석**이
        *"`build:` 가 없는 것은 누락이 아니다"* 라고 적고 있어서 red 가 됐다(실측
        2026-09-02). 산문은 같은 낱말로 반대를 말한다 — 오늘 이 저장소에서 세 번째로
        같은 형태를 밟았다(런북 UUID 검사 · 정지 술어 · 여기).
        """
        block = _web_service_block()
        keys = [
            line.strip().split(':', 1)[0]
            for line in block.splitlines()
            if re.match(r'^    [a-z_]+:', line)  # 서비스 하위 키만, 주석 제외
        ]
        # ⚠️ **2026-09-03 뒤집힘.** provider 저장소에서는 이 키가 **없어야** 했다.
        # 여기서는 `apps/web` 이 이 저장소에 있고 web 을 **빌드한다** — 그 사실이
        # 배치표의 «중앙 PC 가 만드는 이미지» 열을 참으로 만든다.
        self.assertIn(
            'build', keys,
            'compose 의 web 서비스에 build 키가 없다 — 그렇다면 이 저장소가 웹 '
            '이미지를 만들지 못한다는 뜻이고, 배치표의 «중앙 PC 가 fcc-central-web 을 '
            f'빌드한다» 가 더는 참이 아니다. 발견된 키: {keys!r}',
        )
        self.assertIn(
            'image', keys,
            f'web 서비스가 image 키를 갖지 않는다 — 발견된 키: {keys!r}',
        )

    def test_this_repo_is_an_extracted_lane(self):
        """⚠️ **2026-09-03 뒤집힘.** provider 저장소는 «남는 쪽» 이었고 이 저장소는
        «간 쪽» 이다. 같은 매니페스트를 읽는데 답이 반대다."""
        self.assertNotIn(
            'fcc-test-platform', _non_extracted_lanes(),
            '매니페스트가 fcc-test-platform 을 «남는 쪽» 으로 바꿨다 — 그렇다면 이 '
            '저장소가 독립 레인이라는 배치표의 전제가 무너진다.',
        )


class TestTheRunbookCarriesThePlacementTable(unittest.TestCase):
    """런북 한 곳에서 세 질문에 답이 나와야 한다."""

    def test_the_section_exists(self):
        self.assertIn(
            _SECTION_HEADING, _runbook_text(),
            f'런북에 배치 절이 없다. 제목: {_SECTION_HEADING!r}',
        )

    def test_it_names_the_repo_the_operator_must_place(self):
        # 중앙 PC 에 두는 것은 이 저장소다. provider 저장소는 «두지 않는다» 쪽으로
        # 표에 나오므로 두 이름이 모두 있어야 한다.
        self.assertIn('fcc-test-platform', _runbook_text())
        self.assertIn('FCC_mobile_test_automation', _runbook_text())

    def test_it_says_the_pinned_lanes_are_not_cloned(self):
        """레인 이름이 «클론하지 않는다» 는 말과 **같은 절 안에** 있어야 한다.

        문서 어딘가에 그 이름이 있는 것만으로는 부족하다 — 운영자는 이 절을 읽고
        결정한다.
        """
        text = _runbook_text()
        start = text.index(_SECTION_HEADING)
        nxt = text.find('\n## ', start + 1)
        section = text[start: nxt if nxt != -1 else len(text)]
        for lane in _pinned_lane_repos():
            with self.subTest(lane=lane):
                self.assertIn(lane, section, f'배치 절이 {lane} 을 언급하지 않는다')
        self.assertIn('클론하지 않는다', section)

    def test_it_says_the_web_image_comes_from_the_platform_repo(self):
        text = _runbook_text()
        start = text.index(_SECTION_HEADING)
        nxt = text.find('\n## ', start + 1)
        section = text[start: nxt if nxt != -1 else len(text)]
        self.assertIn('fcc-central-web', section)
        self.assertIn('fcc-test-platform', section)

    def test_it_tells_the_operator_what_the_chamber_pc_needs(self):
        text = _runbook_text()
        start = text.index(_SECTION_HEADING)
        nxt = text.find('\n## ', start + 1)
        section = text[start: nxt if nxt != -1 else len(text)]
        self.assertIn('챔버 PC', section)


if __name__ == '__main__':
    unittest.main()
