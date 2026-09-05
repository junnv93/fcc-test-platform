"""이 상자가 **자기가 필요로 하는 것을 선언하는가** (공급 폐포, 2026-09-04 → 09-05).

판정 로직은 이 파일에 없다. `fcc_test_contracts.common.supply_closure` 에 있고, 계약
레인의 같은 이름 시험이 **같은 부품**을 부른다.

⭐ **왜 옮겼는가.** 이 축은 2026-09-04 에 이 상자에서 먼저 섰고, 그때 계약 레인에도 같은
계급의 결함이 있었다(`psycopg` 를 가드 없이 부르는데 어느 extra 에도 없었다). 그것은
**사람이 손으로 훑어** 찾았다 — 게이트가 없는 상자에서는 그것이 유일한 발견 경로이고,
사람은 매번 훑지 않는다.

그렇다고 300줄을 저쪽에 복사하면 사본 둘이 생긴다. 같은 사실이 두 곳에 있고 하나가 먼저
낡는 형태이고, 이 계열이 `benchmark_harness` 에서 이미 값을 치렀다. 계약 레인의
`scripts/`·`tests/` 는 휠이 나르지 못하므로, 공유되려면 판정기가 **배포되는 패키지 안**에
살아야 한다 — 그것이 `fcc_test_contracts/common/` 인 이유이자 이 상자의 pin 이
`v0.1.19` 로 움직인 이유다.

이 파일에 남는 것은 **이 상자만의 사실** 둘뿐이고 둘 다 양방향 원장이다:

  ① 이 상자가 내는 배포판이 무엇인가 (`_EXPECTED_DISTRIBUTIONS`)
  ② 오늘 이 상자가 해소하지 못하는 이름은 무엇인가 (`_KNOWN_UNRESOLVABLE`)

⚠️ 판정기 자체의 봉인은 여기 없다. 그것은 계약 레인
(`tests/test_supply_closure_axis.py::TestTheClosureJudgementItself`)에 있다 — 판정기가
거기 살기 때문이다. 여기서 다시 봉인하면 그것이 두 번째 사본이다.
"""
from __future__ import annotations

from pathlib import Path
import unittest

from fcc_test_contracts.common.supply_closure import (
    LEDGER_REMEDY,
    MISSING_RESOURCE_REMEDY,
    UNDECLARED_REMEDY,
    ResourceClosure,
    SupplyClosure,
    WheelBuildUnavailable,
    missing_resource_report,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 이 상자가 내는 **파이썬 배포판**의 원장.
#:
#: ⚠️ 개수가 아니라 이름의 집합이고 정확한 일치를 요구한다. 이 상자는 오늘 하나뿐이지만
#: 그것을 **재는** 것이 요점이다 — 계약 레인은 둘이고(`packages/fcc-test-kernel/` 가
#: 자체 버전 축을 가진 두 번째 배포판이다), 판정기는 그 차이를 인자가 아니라 **파생**으로
#: 다룬다. 이 상자에 두 번째 `pyproject.toml` 이 생기는 날 그 트리도 자기 선언에 대고
#: 재게 되고, 그 사실이 여기서 red 로 먼저 말해진다.
_EXPECTED_DISTRIBUTIONS = {'fcc-test-platform'}

#: 계급 B 의 **원장** — 오늘 이 상자가 해소하지 못하는 이름과 그 사유.
#:
#: ⚠️ 이것은 예외 목록이 아니다. `lane_check` 이 쓰는 것과 같은 형태의 원장이고 아래
#: 시험이 **정확한 일치**를 요구한다: 늘면 red, 해소돼도 red(*"그것도 소식이다"*).
#: 예외 목록은 한 방향으로만 자라 조용히 낡지만 원장은 낡을 수 없다.
#:
#: ── 오늘의 항목 (2026-09-05 판정) ─────────────────────────────────────────────
#: **비어 있다.** 그리고 그것이 이 상자에 대한 사실이지 검사를 끈 것이 아니다.
#:
#: 직전까지 여기 두 이름이 있었다 — `check_extraction_import_boundaries` 와
#: `prepare_headless_extraction_package`. `scripts/platform_extraction_runner.py` 가
#: 그 둘을 **형제 스크립트 이름**으로 불렀고, 그 둘은 계약 레인의 `scripts/` 에 살며
#: `scripts/` 는 휠이 나르지 못한다. 그래서 그 러너는 이 상자에서 import 조차 되지
#: 않았고, 그런데 `fcc_test_platform/cutover_workflow_hints.py` 는 운영자에게 그 명령을
#: 안내했다(그 힌트 바로 위 주석이 이미 그 위험을 이름 붙여 놨다).
#:
#: 운영자 판정 2026-09-05 로 해소한 방식은 **소유권 이동이 아니다** — 모노레포
#: 매니페스트가 이미 러너를 이 레인에 넘겼고(`ownership: relinquished`) 협력자 둘을
#: 계약 레인 소유로 명시한다. 남은 결함은 도달성뿐이었고, 계약 레인이 알맹이를
#: `fcc_test_contracts.extraction_import_boundaries` / `.extraction_package` 로 올려
#: 휠이 나르게 했다. 이 상자의 러너는 이제 그 배포 경로로 부른다.
#:
#: ⚠️ 이 원장이 비었다는 것이 「스캐너가 아무것도 못 찾았다」와 같은 값이 되지 않도록
#: `test_the_scan_is_not_vacuous` 가 먼저 단언한다.
_KNOWN_UNRESOLVABLE: frozenset[str] = frozenset()


class TestEveryUnguardedImportIsDeclared(unittest.TestCase):
    """① 의존성 폐포 — 코드가 요구하는 것이 선언에 있는가.

    ⚠️ **이 시험은 두 가지 형태로 빨개지고 둘 다 옳다.**

      * 개발 머신(전부 깔려 있음): 이름은 import 되는데 그것을 제공하는 배포판이
        **선언에 없다** → 「로컬만 초록」이 되기 전에 여기서 멈춘다.
      * 갓 클론한 러너(선언한 것만 깔림): 이름이 **아예 import 되지 않는다** →
        24건이 흩어져 나는 대신 이 시험 하나가 이름을 대고 멈춘다.

    ⚠️ AST 스캔은 이 트리에서 393 파일 · 약 2초다. 시험마다 다시 돌리면 그 값을 시험
    수만큼 낸다 — 게이트가 느리면 사람이 게이트를 건너뛰기 시작한다. 클래스당 한 번만 돈다.
    """

    @classmethod
    def setUpClass(cls):
        cls.closure = SupplyClosure(PROJECT_ROOT)

    def test_the_repository_ships_exactly_the_recorded_distributions(self):
        """배포판 원장 — 이 축이 **무엇을 보는지**가 조용히 바뀌지 않게 한다."""
        observed = {distribution.name for distribution in self.closure.distributions}
        self.assertEqual(
            observed, _EXPECTED_DISTRIBUTIONS,
            '이 상자가 내는 파이썬 배포판 집합이 원장과 다르다. 새로 생겼으면 그 배포판도 '
            '이 축이 자기 선언에 대고 재게 되고, 사라졌으면 그 트리가 어느 선언에도 매이지 '
            '않는다 — 어느 쪽이든 원장을 고치면서 왜인지 적어라.',
        )

    def test_the_scan_is_not_vacuous(self):
        """빈 스캔이 통과로 읽히지 않게 한다 — 이 게이트가 스스로 꺼지는 것을 막는다."""
        self.assertGreater(
            len(self.closure.python_files), 100,
            f'스캔한 파이썬 파일이 {len(self.closure.python_files)}개뿐이다',
        )
        self.assertGreater(
            len(self.closure.sites), 0,
            '서드파티 import 를 하나도 못 찾았다 — 스캐너가 고장났다는 뜻이다',
        )

    def test_every_unguarded_third_party_import_resolves_to_a_declared_distribution(self):
        """계급 A — 이름은 해소되는데 **선언에 없다.**

        개발 머신에서만 초록인 상태다. 우연히 깔려 있는 배포판에 기대고 있고, 갓 클론한
        러너에서 무너진다. 실측: jsonschema · PyJWT(2026-08-31) · PyYAML(2026-09-04).
        """
        report = self.closure.undeclared_report()
        self.assertEqual(report, [], UNDECLARED_REMEDY.format(report='\n'.join(report)))

    def test_unresolvable_imports_match_the_recorded_ledger_exactly(self):
        """계급 B — 이름이 **어디에서도 해소되지 않는다.**

        선언 문제가 아니다. 이 상자가 자기 안에 없는 코드를 부른다는 뜻이고, 대개 모노레포
        분리 때 협력자만 다른 레인으로 가고 호출자가 남은 자리다. 계급 A 와 섞어 보고하면
        「의존성 하나 더 선언하면 되겠지」로 오독된다 — 그래서 시험을 나눠 둔다.
        """
        report = self.closure.ledger_report(_KNOWN_UNRESOLVABLE)
        self.assertEqual(report, [], LEDGER_REMEDY.format(report='\n'.join(report)))


class TestEveryPackageResourceShipsInTheWheel(unittest.TestCase):
    """② 패키지 자원 폐포 — 코드 옆에 있는 비-.py 가 **휠에도** 있는가.

    ⚠️ **실제로 휠을 빌드해서 잰다.** 선언(`package-data` 글롭)을 읽어서 재면 그 선언이
    틀렸을 때 검사도 같이 틀린다 — `decision_catalogue.json` 과 PM/RF 엑셀 서식이 정확히
    그렇게 빠졌고, 후자는 컨테이너에서 내보내기를 **구조적으로** 불가능하게 만들었다.
    재는 대상은 선언이 아니라 **산출물**이어야 한다.

    ⚠️ 빌드는 트리 밖 사본에서 한다. non-editable 설치는 `build/` 를 남기고 `lane_check`
    은 그 트리에서 **판정을 거부한다**. 검사가 자기가 재는 트리를 오염시키면 그 측정은
    자기 자신의 부작용을 잰다.
    """

    @classmethod
    def setUpClass(cls):
        cls.closures: dict[str, ResourceClosure] = {}
        for distribution in SupplyClosure(PROJECT_ROOT).distributions:
            closure = ResourceClosure(distribution, repo_root=PROJECT_ROOT)
            try:
                closure.build()
            except WheelBuildUnavailable as exc:  # pragma: no cover — 빌드 불가 환경
                raise unittest.SkipTest(str(exc)) from exc
            cls.closures[distribution.name] = closure

    @classmethod
    def tearDownClass(cls):
        for closure in getattr(cls, 'closures', {}).values():
            closure.close()

    def test_every_distribution_was_actually_built(self):
        self.assertEqual(set(self.closures), _EXPECTED_DISTRIBUTIONS)

    def test_the_scan_is_not_vacuous(self):
        total = sum(len(c.source_resources()) for c in self.closures.values())
        self.assertGreater(
            total, 0, '패키지 안에서 비-.py 자원을 하나도 못 찾았다 — 스캐너가 고장났다',
        )

    def test_every_non_python_file_beside_the_code_is_in_the_wheel(self):
        missing: list[str] = []
        for name, closure in sorted(self.closures.items()):
            missing.extend(f'{name}: {item}' for item in closure.missing_resources())
        self.assertEqual(
            missing, [],
            MISSING_RESOURCE_REMEDY.format(report='\n'.join(missing_resource_report(missing))),
        )

    def test_each_wheel_carries_the_python_it_claims(self):
        """휠이 비었는데 자원만 맞는 상태를 통과로 읽지 않는다."""
        for name, closure in sorted(self.closures.items()):
            with self.subTest(distribution=name):
                self.assertGreater(
                    closure.shipped_module_count(), 50,
                    f'{name} 휠에 .py 가 {closure.shipped_module_count()}개뿐이다',
                )


if __name__ == '__main__':
    unittest.main()
