"""central write 어댑터 11개가 «자기 포트가 약속한 것»을 전부 갖는가.

── 왜 이 파일이 생겼나 (2026-09-06 포트 감사) ──────────────────────────────────
PR #112 가 `CentralChamberWritePort` 에서 실증했다: 그 Protocol 은 메서드 **둘**을
약속하면서 서비스가 **일곱**을 부르고 있었다. 구체 어댑터가 일곱을 다 갖고 있어 오늘
돌았을 뿐, **그 포트를 만족하는 다른 구현체는 타입 검사를 통과하고 런타임에 죽는다.**

그 축을 write 어댑터군 전체로 넓혀 재 봤다(실측):

    적합성 단언(assertIsInstance)이 있는 포트   6 / 11
    @runtime_checkable 인 포트                  9 / 11
    단언 «없는» 5개: ArtifactCustody · Membership · Progress · SampleInventory · User

⚠️ 그리고 둘(`Progress` · `SampleInventory`)은 ``@runtime_checkable`` 이 아니라
   ``isinstance`` 로 **잴 수조차 없다.** 그래서 이 파일은 인스턴스가 아니라 **메서드
   집합**을 대조한다 — 어댑터를 만들 필요도, 포트에 데코레이터를 더할 필요도 없이
   11쌍 전부를 한 규칙으로 덮는다.

⚠️ ``CentralSampleInventoryWritePort`` 는 그 어댑터 모듈이 **이름조차 언급하지 않는다**
   (실측: 그 파일에 ``Central…Port`` 문자열이 0건). 오늘은 여섯 메서드가 정확히 맞아
   구조적으로 만족하지만, **둘을 잇는 것이 코드에 없다** — 포트가 일곱 번째를 얻는 날
   (챔버 포트가 방금 그랬듯) 아무 데서도 소리가 나지 않는다. 이 파일이 그 자리다.

── 짝은 «파생»한다, 손으로 적지 않는다 ─────────────────────────────────────────
``central_<X>_write_adapter`` ↔ ``central_<X>_write_port``. 표를 손으로 적으면 새
어댑터가 표에 없는 채로 조용히 빠진다 — 그때 이 검사는 초록인 채로 그것을 안 본다.
"""
from __future__ import annotations

import importlib
import inspect
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_DIR = REPO_ROOT / 'fcc_test_platform' / 'application'
PORT_PACKAGE = 'fcc_test_platform.domain.ports.output'

#: 2026-09-06 실측 — write 어댑터는 12개다(그중 11개가 이번 웨이브의 strict 대상이고,
#: `central_audit_write_adapter` 는 이미 오류 0건이었다). 줄면 어댑터가 사라졌거나
#: 이름 규약이 바뀐 것이고, 그때 아래
#: 판정은 «아무것도 요구하지 않으면서» 초록이 된다.
_EXPECTED_PAIRS = 12


def _write_ports() -> dict[str, type]:
    """포트 «패키지 전체»에서 ``*WritePort`` Protocol 을 이름으로 색인한다.

    ⚠️ 모듈 이름으로 짝을 짓지 «않는다». 실측 2026-09-06: 포트 모듈 이름이 균일하지
    않다 — ``central_project_write_adapter`` 의 포트는
    ``central_project_write_port`` 가 아니라 다른 모듈에 산다(5/12 가 그렇다).
    모듈명 규약을 전제하면 그 다섯은 «조용히 빠지고», 이 검사는 초록인 채로 그것을
    안 본다. 클래스 이름은 규약이 지켜지는 축이므로 그것으로 잇는다.
    """
    index: dict[str, type] = {}
    for path in sorted((REPO_ROOT / PORT_PACKAGE.replace('.', '/')).glob('*.py')):
        if path.stem == '__init__':
            continue
        module = importlib.import_module(f'{PORT_PACKAGE}.{path.stem}')
        for obj in vars(module).values():
            if (inspect.isclass(obj) and getattr(obj, '_is_protocol', False)
                    and obj.__module__ == module.__name__
                    and obj.__name__.endswith('WritePort')):
                index[obj.__name__] = obj
    return index


def _pairs() -> list[tuple[str, type, type]]:
    """``(어댑터 이름, 포트 Protocol, 어댑터 클래스)``.

    ``PostgresCentral<X>WriteAdapter`` ↔ ``Central<X>WritePort`` — 클래스 이름에서
    파생한다. 손으로 표를 적으면 새 어댑터가 표에 없는 채로 빠진다.
    """
    ports = _write_ports()
    found: list[tuple[str, type, type]] = []
    for path in sorted(ADAPTER_DIR.glob('central_*_write_adapter.py')):
        module = importlib.import_module(f'fcc_test_platform.application.{path.stem}')
        for obj in vars(module).values():
            if not (inspect.isclass(obj) and obj.__module__ == module.__name__
                    and obj.__name__.startswith('Postgres')
                    and obj.__name__.endswith('WriteAdapter')):
                continue
            port = ports.get(obj.__name__[len('Postgres'):-len('Adapter')] + 'Port')
            if port is not None:
                found.append((path.stem, port, obj))
    return found


def _promised(port: type) -> set[str]:
    return {
        name for name, value in vars(port).items()
        if callable(value) and not name.startswith('_')
    }


class TestEveryWriteAdapterKeepsItsPortPromise(unittest.TestCase):

    def test_the_pairs_are_all_found(self):
        """비공허성 — 짝을 못 찾으면 아래 판정은 아무것도 요구하지 않는다."""
        pairs = _pairs()
        self.assertGreaterEqual(
            len(pairs), _EXPECTED_PAIRS,
            f'어댑터↔포트 짝이 {len(pairs)}개다 — 2026-09-06 실측은 {_EXPECTED_PAIRS}개였다. '
            '어댑터가 사라졌거나 ``central_<X>_write_adapter`` ↔ '
            '``central_<X>_write_port`` 이름 규약이 깨졌다.',
        )

    def test_each_adapter_has_every_method_its_port_promises(self):
        """⚠️ 이것이 이 파일의 본체다.

        포트가 약속한 이름 중 하나라도 어댑터에 없으면, 그 포트를 타입으로 받는
        소비자는 **타입 검사를 통과하고 런타임에 죽는다.**
        """
        missing: list[str] = []
        for name, port, adapter in _pairs():
            gaps = sorted(_promised(port) - set(dir(adapter)))
            if gaps:
                missing.append(f'{name}: {adapter.__name__} 에 없는 것 {gaps}')
        self.assertEqual(
            missing, [],
            '포트가 약속한 메서드를 어댑터가 갖고 있지 않다. 그 포트를 타입으로 받는 '
            '소비자는 타입 검사를 통과하고 «런타임에» 죽는다:\n  ' + '\n  '.join(missing),
        )

    def test_no_port_promises_nothing(self):
        """약속이 0개인 포트는 「만족한다」가 공허하다 — 그런 포트가 생기면 알아야 한다."""
        empty = [name for name, port, _ in _pairs() if not _promised(port)]
        self.assertEqual(
            empty, [],
            f'메서드를 하나도 약속하지 않는 write 포트가 있다 — 그 포트에 대한 '
            f'「만족한다」는 아무 뜻이 없다: {empty}',
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
