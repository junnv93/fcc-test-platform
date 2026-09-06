"""central read 어댑터 11개가 «자기 포트가 약속한 것»을 전부 갖는가 — 이름과 **반환 타입**.

── write 봉인과 «한 파일로 합치지 않은» 이유 (2026-09-06) ───────────────────────
`tests/test_write_port_conformance.py` 와 규칙이 같다면 합치는 쪽이 옳다. 실측해 보니
**같지 않다.** 갈리는 축이 둘이고, 둘 다 read 쪽에만 있다:

  ① **포트가 두 패키지에 흩어져 산다.** 실측 2026-09-06:

         ReadPort   platform 10 · kernel 1   (`CentralRbacReadPort`)
         WritePort  platform 12 · kernel 0

     write 봉인은 이 레포의 포트 패키지 «하나»만 훑고 그래서 옳다. 같은 파일에서
     read 를 처리하려면 그 훑기를 두 패키지로 넓혀야 하고, 그러면 **write 봉인의
     대상도 조용히 넓어진다** — 커널에 `…WritePort` 가 생기는 날 write 쪽 판정이
     아무도 결정하지 않은 채로 바뀐다. 대상이 다른 두 판정은 파일도 나눈다.

  ② **read 는 반환 타입이 계약의 본체다.** write 어댑터는 대개 ``None`` 이나
     식별자를 돌려주지만 read 는 «돌려주는 모양»이 곧 약속이다. 포트가
     ``Optional[dict]`` 을 약속하고 어댑터가 ``dict`` 를 적으면 메서드 «집합»은
     완전히 일치하면서 소비자가 ``None`` 을 못 받는다. 아래
     ``test_each_method_returns_what_its_port_promises`` 가 그 축이고, write 에는
     걸 이유가 없다.

⚠️ 그래도 **메서드 집합 대조 규칙 자체는 한 곳**이다 — `_promised` 를 write 봉인에서
   import 한다. 두 파일이 각자 「약속이란 무엇인가」를 정의하면 그중 하나가 먼저 낡는다.

── 짝은 «클래스 이름»에서 파생한다 ─────────────────────────────────────────────
`Postgres<X>ReadAdapter` ↔ `<X>ReadPort`. 모듈 이름으로 지으면 **조용히 빠진다** —
실측 2026-09-06: read 포트 10개 중 **다섯**이 `central_<X>_read_port.py` 가 아닌 모듈에
산다(artifact_custody · project · reference · report · test_equipment_list 는
`central_<X>_port.py`). write 에서 5/12 를 놓칠 뻔한 것과 같은 형태다.

── 포트 색인은 «디렉터리 글롭»이 아니라 `pkgutil` 이다 ──────────────────────────
커널은 이 레포의 형제 «트리»일 수도 있고 CI 처럼 pip 설치본일 수도 있다(그리고 실측상
namespace 패키지라 `__path__` 에 둘이 동시에 들어온다). `REPO_ROOT.parent` 로 경로를
지어 훑으면 CI 에서 조용히 0개가 되고, 그러면 rbac 짝이 사라진 채로 이 파일이
초록이 된다 — `_contracts_are_reachable()` 이 같은 값을 이미 치렀다.
`pkgutil.iter_modules(pkg.__path__)` 는 «임포트가 답한 자리»를 훑으므로 둘 다 답한다.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import unittest
from pathlib import Path

#: ⚠️ **규칙은 한 곳이다.** 「포트가 약속한 것」의 정의를 여기 다시 적으면 write 와
#:    read 가 갈린다. 그 파일이 옮겨지면 이 import 가 이름을 대며 멈추고, 그때
#:    옮긴 사람이 두 봉인을 같이 본다 — 그것이 여기서 원하는 동작이다.
from tests.test_write_port_conformance import _promised

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_DIR = REPO_ROOT / 'fcc_test_platform' / 'application'

#: 포트가 사는 «모든» 패키지. 파일 docstring ① 의 실측이 이 튜플의 근거다.
PORT_PACKAGES = (
    'fcc_test_platform.domain.ports.output',
    'fcc_test_kernel.domain.ports.output',
)

#: 2026-09-06 실측 — read 어댑터는 11개이고 **열하나 모두** 짝이 성립한다.
#: 줄면 어댑터가 사라졌거나 이름 규약이 깨진 것이고, 그때 아래 판정은
#: «아무것도 요구하지 않으면서» 초록이 된다.
_EXPECTED_PAIRS = 11

#: 어댑터가 «자기 포트의 이름을 코드에서 한 번도 대지 않는» 자리 — **선언된 갈라짐**.
#:
#: ⚠️ 등호(`assertEqual`)로 잰다. 「N개 이하」로 두면 내일 하나가 더 생겨도 조용하다.
#:    write 쪽에도 정확히 하나 있었다(`CentralSampleInventoryWritePort`) — 같은 도메인이
#:    read 에서도 같은 형태를 낸다. 오늘은 여덟 메서드가 구조적으로 맞아 돌지만
#:    **둘을 잇는 것이 코드에 없다.** 이 파일이 그 자리를 대신 지킨다.
#:    이 집합을 «줄이는» 것은 좋은 변경이다(어댑터가 포트를 명시적으로 상속·언급하게
#:    하면 된다). 줄였으면 여기서도 지워라.
PORTS_NOT_NAMED_BY_THEIR_ADAPTER = frozenset({
    'central_sample_inventory_read_adapter',
})


def _read_ports() -> dict[str, type]:
    """``*ReadPort`` Protocol 을 **이름으로** 색인한다 — 두 패키지 전부에서."""
    index: dict[str, type] = {}
    for package in PORT_PACKAGES:
        pkg = importlib.import_module(package)
        for found in pkgutil.iter_modules(list(pkg.__path__)):
            module = importlib.import_module(f'{package}.{found.name}')
            for obj in vars(module).values():
                if (inspect.isclass(obj) and getattr(obj, '_is_protocol', False)
                        and obj.__module__ == module.__name__
                        and obj.__name__.endswith('ReadPort')):
                    index[obj.__name__] = obj
    return index


def _adapters() -> list[tuple[str, str, type]]:
    """``(모듈 이름, 기대하는 포트 이름, 어댑터 클래스)`` — 짝이 «성립하지 않아도» 싣는다.

    ⚠️ 여기서 포트를 못 찾은 어댑터를 **버리지 않는다.** 버리면 포트가 사라진 날
    그 어댑터가 목록에서 조용히 빠지고, 아래 판정은 남은 것들에 대해 참이 된다.
    ``test_every_adapter_is_matched_to_a_port`` 가 그 부재를 이름으로 세운다.
    """
    found: list[tuple[str, str, type]] = []
    for path in sorted(ADAPTER_DIR.glob('central_*read_adapter.py')):
        module = importlib.import_module(f'fcc_test_platform.application.{path.stem}')
        for obj in vars(module).values():
            if not (inspect.isclass(obj) and obj.__module__ == module.__name__
                    and obj.__name__.startswith('Postgres')
                    and obj.__name__.endswith('ReadAdapter')):
                continue
            want = obj.__name__[len('Postgres'):-len('Adapter')] + 'Port'
            found.append((path.stem, want, obj))
    return found


def _pairs() -> list[tuple[str, type, type]]:
    """``(어댑터 모듈 이름, 포트 Protocol, 어댑터 클래스)`` — 짝이 성립한 것만."""
    ports = _read_ports()
    return [(name, ports[want], adapter)
            for name, want, adapter in _adapters() if want in ports]


def _returns(owner: type, method: str) -> str:
    """선언된 반환 타입의 **표기**. 없으면 그렇다고 말한다.

    ⚠️ 타입 «객체»가 아니라 표기를 비교한다. 두 파일 모두
    ``from __future__ import annotations`` 라 런타임에는 문자열이고, 그 문자열을
    해소하려면 두 모듈의 네임스페이스를 합쳐야 하는데 그 합침 자체가 새 가정이다.
    표기 대조는 ``Optional[dict]`` 과 ``dict | None`` 을 «다르다»고 말한다 — 그것은
    거짓 양성이 아니라 **같은 계약을 두 가지로 적고 있다**는 보고다.
    """
    attr = getattr(owner, method, None)
    if attr is None:
        return '«메서드 없음»'
    try:
        annotation = inspect.signature(attr).return_annotation
    except (TypeError, ValueError):  # pragma: no cover — 시그니처를 못 읽는 객체
        return '«시그니처 없음»'
    if annotation is inspect.Signature.empty:
        return '«선언 없음»'
    return getattr(annotation, '__name__', None) or str(annotation)


class TestEveryReadAdapterKeepsItsPortPromise(unittest.TestCase):

    def test_the_pairs_are_all_found(self):
        """비공허성 — 짝을 못 찾으면 아래 판정은 아무것도 요구하지 않는다."""
        pairs = _pairs()
        self.assertGreaterEqual(
            len(pairs), _EXPECTED_PAIRS,
            f'어댑터↔포트 짝이 {len(pairs)}개다 — 2026-09-06 실측은 {_EXPECTED_PAIRS}개였다. '
            '어댑터가 사라졌거나 ``Postgres<X>ReadAdapter`` ↔ ``<X>ReadPort`` '
            '클래스 이름 규약이 깨졌다.',
        )

    def test_the_index_spans_every_package_that_holds_ports(self):
        """⚠️ 커널 쪽 색인이 죽으면 rbac 짝이 «조용히» 사라진다.

        그때 `test_every_adapter_is_matched_to_a_port` 가 잡기는 하지만, 사유가
        「포트가 없다」로 보여 다음 사람이 포트를 찾으러 간다. 여기서 먼저
        「색인이 그 패키지를 못 봤다」고 말하면 진짜 원인을 가리킨다.
        """
        for package in PORT_PACKAGES:
            with self.subTest(package=package):
                pkg = importlib.import_module(package)
                names = {
                    obj.__name__
                    for found in pkgutil.iter_modules(list(pkg.__path__))
                    for obj in vars(
                        importlib.import_module(f'{package}.{found.name}')).values()
                    if inspect.isclass(obj) and getattr(obj, '_is_protocol', False)
                    and obj.__name__.endswith('ReadPort')
                }
                self.assertTrue(
                    names,
                    f'{package} 에서 ReadPort 를 하나도 못 찾았다 — 이 패키지가 '
                    f'옮겨졌거나 도달할 수 없다. PORT_PACKAGES 를 고쳐라.')

    def test_every_adapter_is_matched_to_a_port(self):
        """포트 «없는» 어댑터 — 이름은 포트를 구현한다고 말하는데 그 포트가 없다."""
        ports = _read_ports()
        orphans = sorted(
            f'{name}: {want} 를 못 찾았다'
            for name, want, _ in _adapters() if want not in ports)
        self.assertEqual(
            orphans, [],
            '어댑터가 자기 이름으로 가리키는 포트를 찾을 수 없다. 이름 규약이 '
            '깨졌거나, 포트가 PORT_PACKAGES 밖으로 옮겨졌다:\n  ' + '\n  '.join(orphans),
        )

    def test_each_adapter_has_every_method_its_port_promises(self):
        """이름 축 — write 봉인과 **같은 규칙**(`_promised`)이다."""
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

    def test_each_method_returns_what_its_port_promises(self):
        """⚠️ **read 고유의 축이다** — 파일 docstring ② 참조.

        메서드 집합이 완전히 일치해도 반환 모양이 갈리면 소비자가 깨진다.
        ``Optional[dict]`` 을 약속하고 ``dict`` 를 적으면 ``None`` 을 받는 자리가
        타입상 존재하지 않게 된다.
        """
        drift: list[str] = []
        for name, port, adapter in _pairs():
            for method in sorted(_promised(port)):
                promised, delivered = _returns(port, method), _returns(adapter, method)
                if promised != delivered:
                    drift.append(
                        f'{name}.{method}: 포트={promised} 어댑터={delivered}')
        self.assertEqual(
            drift, [],
            '어댑터가 «포트와 다른 모양»을 돌려준다고 선언했다. 같은 계약을 두 가지로 '
            '적은 것이라면 표기를 맞춰라 — 다른 계약이라면 어느 쪽이 진실인지 '
            '정하고 둘 다 고쳐라:\n  ' + '\n  '.join(drift),
        )

    def test_no_port_promises_nothing(self):
        """약속이 0개인 포트는 「만족한다」가 공허하다 — 그런 포트가 생기면 알아야 한다."""
        empty = [name for name, port, _ in _pairs() if not _promised(port)]
        self.assertEqual(
            empty, [],
            f'메서드를 하나도 약속하지 않는 read 포트가 있다 — 그 포트에 대한 '
            f'「만족한다」는 아무 뜻이 없다: {empty}',
        )

    def test_the_adapters_that_never_name_their_port_are_exactly_the_declared_ones(self):
        """선언된 갈라짐 — `PORTS_NOT_NAMED_BY_THEIR_ADAPTER` 주석 참조."""
        silent = {
            name for name, want, _ in _adapters()
            if want not in (ADAPTER_DIR / f'{name}.py').read_text(encoding='utf-8')
        }
        self.assertEqual(
            set(PORTS_NOT_NAMED_BY_THEIR_ADAPTER), silent,
            '자기 포트 이름을 한 번도 대지 않는 어댑터의 집합이 선언과 다르다.\n'
            f'  선언: {sorted(PORTS_NOT_NAMED_BY_THEIR_ADAPTER)}\n'
            f'  실측: {sorted(silent)}\n'
            '늘었다면 새 어댑터가 포트와 «코드상 아무 연결 없이» 살고 있다는 뜻이고, '
            '줄었다면 좋은 변경이니 선언에서도 지워라.',
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
