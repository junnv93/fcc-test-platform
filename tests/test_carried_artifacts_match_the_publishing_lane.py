"""경계를 넘어온 산출물을 «받은 그대로» 믿지 않는다 (2026-09-06).

이 상자의 `docs/api/*.openapi.json` 은 두 부류다:

* **여기서 쓴 것** — `scripts/export_platform_openapi.py` 가 커널의 선언에서 조립한다.
  이쪽은 이 상자가 SSOT 이므로 밖과 대조할 것이 없다.
* **받아 나르는 것** — 발행 레인(`fcc-test-contracts`)이 쓰고 이 상자가 사본을 싣는다.
  이쪽은 **설치된 발행본과 같아야 한다.**

⚠️ **실측 2026-09-06: 나르는 문서 중 하나만 대조되고 있었다.**
`sync_published_openapi.py` 는 `headless-api.openapi.json` **하나**를 본다
(`carried_paths()` → 2건). `session-api.openapi.json` 도 두 벌(`docs/api/` ·
`packages/api-artifacts/artifacts/`)을 나르는데 **아무 검사도 그것을 발행본과 대조하지
않았다.** 오늘은 우연히 같다.

⚠️ **왜 목록을 손으로 적지 않았나.** 「headless 와 session 을 본다」고 적으면 세 번째
문서가 들어오는 날 이 검사가 조용히 그것을 안 본다. 그래서 부류를 **파생**한다 —
*이 상자의 발행 스크립트가 그 파일을 쓰는가*. 쓰면 저자, 안 쓰면 나르는 것.

## 이 검사가 서 있는 자리 — 겹치지 않는다

* `test_published_openapi_is_carried_not_authored` 는 `headless-api` **한 문서**를
  깊게 본다(그 문서의 사본 둘 ↔ 발행본).
* 이 검사는 **어느 문서가 대조 대상인지**를 판정한다. 앞의 것은 대상이 고정이고
  이쪽은 대상이 파생이다. 둘은 서로를 대신하지 못한다 — 실제로 앞의 것이 초록인 동안
  `session-api` 는 아무에게도 보이지 않았다.

## 이 결함 계열의 값 — 어제 실측

`chambers` 시각 골든 8장이 **배송될 때부터 코드와 안 맞았다.** 골든을 실은 커밋 시점에
그 화면 코드에는 이미 해당 절이 있었고 그 파일은 그 뒤 바이트 동일인데 골든에는 없었다.
**경계를 넘어온 산출물은 「도착했다」가 「맞다」를 뜻하지 않는다.**
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

_ROOT = Path(__file__).resolve().parent.parent
DOCS_API = _ROOT / 'docs' / 'api'
SCRIPTS = _ROOT / 'scripts'
MIRROR = _ROOT / 'packages' / 'api-artifacts' / 'artifacts'


def _installed_artifacts_dir() -> Path | None:
    """설치된 발행 레인이 나르는 산출물 디렉터리.

    ⚠️ `fcc_test_contracts` 는 네임스페이스 패키지라 `__file__` 이 `None` 이다 —
    `__path__` 로 잡는다. 이것을 틀리면 검사가 «패키지를 못 찾음»으로 조용히 건너뛴다.
    """
    try:
        import fcc_test_contracts
    except ImportError:  # pragma: no cover - 설치되지 않은 환경
        return None
    for root in fcc_test_contracts.__path__:
        candidate = Path(root) / 'artifacts'
        if candidate.is_dir():
            return candidate
    return None


#: 이 상자의 «발행기» 를 알아보는 규칙. 발행 스크립트는 `export_` 로 시작한다
#: (`export_platform_openapi.py`). 나르기만 하는 스크립트(`sync_published_openapi.py`)는
#: 같은 경로를 문자열로 담지만 저자가 아니므로 이 접두로 갈린다.
#:
#: ⚠️ 규칙을 이름에 두는 것은 약한 결합이다. 그래서 `test_both_classes_are_non_empty`
#: 가 «두 부류가 모두 비지 않았는지» 를 본다 — 규칙이 낡아 한쪽이 통째로 비면 그때
#: 멈춘다. 조용히 전부 한 부류가 되는 것을 막는 자리다.
_PUBLISHER_PREFIX = 'export_'


def _authored_here() -> set[str]:
    """이 상자의 발행기가 **출력으로 적는** 문서 이름.

    목록이 아니라 파생이다 — 발행기 소스에 그 경로가 적혀 있는가로 판정한다.
    """
    publishers = [
        path.read_text(encoding='utf-8')
        for path in sorted(SCRIPTS.glob(f'{_PUBLISHER_PREFIX}*.py'))
    ]
    return {
        doc.name
        for doc in DOCS_API.glob('*.openapi.json')
        if any(f'docs/api/{doc.name}' in text for text in publishers)
    }


def _carried() -> list[Path]:
    """나르는 문서 — 저자가 아닌 것 전부. 두 벌(정본 · 미러)을 모두 낸다."""
    authored = _authored_here()
    out: list[Path] = []
    for doc in sorted(DOCS_API.glob('*.openapi.json')):
        if doc.name in authored:
            continue
        out.append(doc)
        mirror = MIRROR / doc.name
        if mirror.is_file():
            out.append(mirror)
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestThereIsSomethingToCheck(unittest.TestCase):
    """⚠️ 대상이 0개면 아래 단언은 아무것도 요구하지 않으면서 통과한다."""

    def test_the_publishing_lane_is_installed(self) -> None:
        self.assertIsNotNone(
            _installed_artifacts_dir(),
            '발행 레인의 산출물 디렉터리를 찾지 못했다 — `fcc-test-contracts` 가 '
            '설치되지 않았거나 네임스페이스 경로 해소가 깨졌다. 이 검사가 공허하다',
        )

    def test_both_classes_are_non_empty(self) -> None:
        """저자도 있고 나르는 것도 있어야 파생이 실제로 갈랐다는 뜻이다."""
        authored, carried = _authored_here(), _carried()
        self.assertTrue(
            authored,
            '이 상자가 쓰는 문서를 하나도 못 찾았다 — 발행 스크립트 인식이 깨졌고, '
            '그러면 모든 문서가 「나르는 것」으로 분류돼 거짓 빨강이 난다',
        )
        self.assertTrue(
            carried,
            '나르는 문서를 하나도 못 찾았다 — 이 검사가 공허하다',
        )


class TestEveryCarriedCopyEqualsThePublishedArtifact(unittest.TestCase):
    """이 파일이 존재하는 이유."""

    def test_carried_documents_match_the_installed_lane(self) -> None:
        published = _installed_artifacts_dir()
        assert published is not None  # 위 검사가 먼저 잡는다
        drifted: list[str] = []
        missing: list[str] = []
        for copy in _carried():
            source = published / copy.name
            if not source.is_file():
                missing.append(f'{copy.relative_to(_ROOT)} — 발행 레인에 같은 이름이 없다')
                continue
            if _sha(copy) != _sha(source):
                drifted.append(f'{copy.relative_to(_ROOT)}')
        self.assertEqual(
            [], drifted,
            '나르는 사본이 설치된 발행본과 다르다 — 이 상자는 그 문서의 저자가 아니므로 '
            '「어느 쪽이 옳은가」의 답은 발행 레인이다. 핀을 올렸다면 사본도 함께 '
            f'갱신하라:\n  ' + '\n  '.join(drifted),
        )
        self.assertEqual(
            [], missing,
            '나르는 것으로 분류됐는데 발행 레인에 원본이 없다 — 분류가 틀렸거나(여기서 '
            f'쓰는 문서인데 스크립트 인식이 실패), 발행 레인이 그것을 뺐다:\n  '
            + '\n  '.join(missing),
        )


class TestTheDerivationActuallySeparates(unittest.TestCase):
    """⚠️ 파생이 실제로 두 부류를 가르는지 — 이름으로 확인한다.

    가르지 못하면 두 방향 모두 틀린다: 저자를 나르는 것으로 보면 거짓 빨강,
    나르는 것을 저자로 보면 **조용한 초록**이다.
    """

    def test_the_locally_authored_document_is_not_compared(self) -> None:
        """`platform-api` 는 이 상자가 커널 선언에서 조립한다 — 밖과 대조하지 않는다."""
        self.assertIn('platform-api.openapi.json', _authored_here())
        self.assertNotIn(
            'platform-api.openapi.json', [p.name for p in _carried()],
        )

    def test_a_carried_document_is_compared(self) -> None:
        """`session-api` 는 이 상자에 발행기가 없다 — 실측 2026-09-06 로 대조 대상이 됐다."""
        self.assertIn('session-api.openapi.json', [p.name for p in _carried()])

    def test_both_copies_of_a_carried_document_are_covered(self) -> None:
        """정본 하나만 보면 미러가 조용히 갈라진다."""
        names = [str(p.relative_to(_ROOT)) for p in _carried()]
        self.assertIn('docs/api/session-api.openapi.json', names)
        self.assertIn(
            'packages/api-artifacts/artifacts/session-api.openapi.json', names,
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
