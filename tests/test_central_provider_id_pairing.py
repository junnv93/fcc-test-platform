"""`FCC_CENTRAL_PROVIDER_ID` 짝 검사 — 노드와 중앙이 같은 값을 쓰는가 (F-1, 2026-09-02).

**왜 이 축에 검사가 필요한가.** 이 값은 두 프로세스가 **각자** 설정하고, 둘이 다르면
`ChamberResultIngestionService.ingest` 가 ``provider_id does not match central
configuration`` 으로 거절한다. 그 거절은 **인입 시점**에 오고, 그 앞의 두 층(챔버 토큰
바인딩 · 기계 신분증)을 이미 통과한 뒤라 **인증 문제처럼 읽힌다**. 실측 2026-09-02 —
중앙 셋(계약 SSOT · `providers.provider_id` · 중앙 컨테이너 env)은 자연키로 일치하는데
노드 런처만 `providers.id` UUID 였고, 그것을 검사하는 것이 **0건**이었다.

**어쩌다 UUID 가 들어갔나 — 봉인이 사본 하나를 안 덮었다.** 형제 봉인
``TestProviderIdentityValue`` 는 `central.env.example` 과 compose 기본값의 *값*을
계약 SSOT 로 묶는다. 런북은 그 집합에 없었고, 그 사이 런북이 다섯 자리에서 UUID 를
지시하게 됐다. 운영자는 런북을 따랐다. 그러므로 이 파일은 두 가지를 봉인한다 —
(1) 짝 검사기 자체, (2) **런북이 그 SSOT 집합에 들어온다**는 사실.

⚠️ **UUID 를 「틀린 값」으로 판정하는 근거는 모양이 아니라 계약이다.** 계약 SSOT 가
언젠가 UUID 형태를 고른다면 그때는 UUID 가 맞는 값이 된다. 그래서 검사기는
*"UUID 처럼 보이면 거절"* 이 아니라 *"계약 SSOT 와 다르면 거절"* 이고, UUID 모양은
**진단 문구를 고르는 데만** 쓴다(운영자가 런북을 따라 넣은 값이라는 것을 알려주려고).
"""
from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / 'scripts' / 'check_central_provider_id_pairing.py'
_RUNBOOK = (
    _REPO_ROOT / 'docs' / 'operations'
    / 'central-pc-operational-validation-runbook.md'
)
_SRC_ROOT = _REPO_ROOT / 'src'


def _load_checker():
    """⚠️ ``sys.modules`` 등록이 규격의 일부다.

    빼면 ``from __future__ import annotations`` 아래의 ``@dataclass`` 가 필드
    annotation(문자열)을 해소하려고 ``sys.modules[cls.__module__]`` 을 찾다가
    ``None`` 을 만나 ``AttributeError`` 로 죽는다 — 스크립트 결함처럼 보이지만
    로더 쪽 결함이다(실측 2026-09-02).
    """
    spec = importlib.util.spec_from_file_location(
        'check_central_provider_id_pairing', _SCRIPT,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _contract_provider_id() -> str:
    sys.path.insert(0, str(_SRC_ROOT))
    try:
        from fcc_test_contracts.headless.api_contracts import (
            DEFAULT_PROVIDER_METADATA,
        )
    finally:
        sys.path.remove(str(_SRC_ROOT))
    return str(DEFAULT_PROVIDER_METADATA['provider_id'])


class TestTheCheckerJudgesThePair(unittest.TestCase):
    """세 값(계약 SSOT · 중앙 · 노드)이 같아야 통과한다."""

    def setUp(self):
        self.mod = _load_checker()
        self.expected = _contract_provider_id()

    def test_all_three_agreeing_is_ok(self):
        verdict = self.mod.judge(
            central=self.expected, node=self.expected, contract=self.expected,
        )
        self.assertEqual(verdict.exit_code, self.mod.EXIT_OK, verdict.message)

    def test_the_node_holding_a_uuid_is_a_mismatch(self):
        """실측된 형상 그대로 — 중앙은 자연키, 노드는 providers.id UUID."""
        verdict = self.mod.judge(
            central=self.expected,
            node='70a985fa-4724-4d71-a227-ef9ea7605808',
            contract=self.expected,
        )
        self.assertEqual(verdict.exit_code, self.mod.EXIT_MISMATCH)
        self.assertIn('node', verdict.message.lower())

    def test_a_uuid_shaped_node_value_names_the_runbook(self):
        """진단이 **왜 그 값이 들어갔는지**를 짚어야 운영자가 다음에 안 넣는다."""
        verdict = self.mod.judge(
            central=self.expected,
            node='70a985fa-4724-4d71-a227-ef9ea7605808',
            contract=self.expected,
        )
        self.assertIn('runbook', verdict.message.lower())

    def test_central_drifting_from_the_contract_is_a_mismatch(self):
        verdict = self.mod.judge(
            central='unlicensed', node='unlicensed', contract=self.expected,
        )
        self.assertEqual(verdict.exit_code, self.mod.EXIT_MISMATCH)
        self.assertIn('contract', verdict.message.lower())

    def test_a_missing_value_is_undetermined_not_a_pass(self):
        """읽을 수 없는 것과 틀린 것은 다르다 — 접으면 판정이 거짓말이 된다."""
        for central, node in ((None, self.expected), (self.expected, None)):
            with self.subTest(central=central, node=node):
                verdict = self.mod.judge(
                    central=central, node=node, contract=self.expected,
                )
                self.assertEqual(
                    verdict.exit_code, self.mod.EXIT_UNDETERMINED, verdict.message,
                )

    def test_the_uuid_shape_is_not_itself_the_verdict(self):
        """계약이 UUID 를 고르면 UUID 가 맞는 값이다 — 모양으로 판정하지 않는다."""
        uuid_contract = '70a985fa-4724-4d71-a227-ef9ea7605808'
        verdict = self.mod.judge(
            central=uuid_contract, node=uuid_contract, contract=uuid_contract,
        )
        self.assertEqual(verdict.exit_code, self.mod.EXIT_OK, verdict.message)


class TestTheCheckerReadsRealEnvShapes(unittest.TestCase):
    """중앙은 ``KEY=v`` 파일, 노드는 ``export KEY=v`` 셸 스크립트다."""

    def setUp(self):
        self.mod = _load_checker()

    def test_it_reads_an_export_prefixed_shell_launcher(self):
        values = self.mod.read_env_text(
            '#!/usr/bin/env bash\n'
            'export FCC_CENTRAL_PROVIDER_ID=fcc-unlicensed-conducted\n'
            'export OTHER=1\n'
        )
        self.assertEqual(
            values.get('FCC_CENTRAL_PROVIDER_ID'), 'fcc-unlicensed-conducted',
        )

    def test_it_reuses_the_sibling_parser_rather_than_copying_it(self):
        """파서가 둘이면 그중 하나가 먼저 낡는다 — 형제 스크립트에 위임한다.

        ⚠️ **판정 축을 이름에서 위임으로 바꿨다 (2026-09-03).** 옛 판은
        ``'def read_env_text' not in source`` 였다. 그런데 이 스크립트가 형제 로드를
        **지연**시키면서(모듈 레벨에서 죽어 exit 1 을 내던 결함의 수리) 그 이름의
        **위임 래퍼**가 생겼고, 그 축에서는 「파서를 베꼈다」와 「형제를 감쌌다」가
        같은 값이 된다 — 옛 단언은 수리를 결함으로 말했다.

        진짜 불변식은 *«파싱 규칙이 여기 두 벌로 있지 않다»* 이고, 그것은 이름이
        아니라 **위임의 존재**로 판정한다.
        """
        source = _SCRIPT.read_text(encoding='utf-8')
        self.assertIn('check_auth_mode_pairing', source)
        self.assertIn(
            '_load_sibling().read_env_text', source,
            '형제 파서에 위임하는 호출이 없다 — 파싱 규칙을 여기서 다시 구현하면 '
            '두 벌이 되고 그중 하나가 먼저 낡는다.',
        )
        # ⚠️ **여기서 멈춘다.** 첫 판은 형제가 맞춰 놓은 네 가지(인라인 주석 · BOM ·
        # `KEY=` · 중복 키)의 낱말이 이 파일에 나타나면 사본이라고 단언했는데,
        # `encoding='utf-8-sig'` 에서 곧바로 오탐이 났다 — 그것은 **파일을 어떻게
        # 여는가**이지 파싱 규칙의 재구현이 아니다. 표현할 수 없는 술어를 가진
        # 게이트는 없는 게이트보다 나쁘다. 위임의 존재가 이 불변식의 판정항이다.


class TestTheRunbookJoinsTheValueSsot(unittest.TestCase):
    """**이 클래스가 이 웨이브의 요점이다.**

    형제 봉인 ``TestProviderIdentityValue`` 는 `central.env.example` 과 compose
    기본값을 계약 SSOT 로 묶었지만 **런북을 빼놓았다**. 그 빈자리에서 런북이 다섯
    자리에 `providers.id` UUID 를 적게 됐고, 운영자가 그것을 따라 노드에 넣었다.
    사본을 하나 빼놓은 봉인은 그 사본이 드리프트하는 것을 막지 못한다.
    """

    def test_every_runbook_assignment_is_the_contract_provider_code(self):
        """판정 축은 **대입문**이지 산문이 아니다.

        ⚠️ 첫 판은 *"`FCC_CENTRAL_PROVIDER_ID` 와 'UUID' 가 같은 줄에 있으면 위반"*
        이었고, 그 술어는 **UUID 를 지시하는 줄과 UUID 를 경고하는 줄을 구분하지
        못했다** — 정정문을 넣자마자 자기가 red 가 됐다(실측 2026-09-02). 산문은
        같은 낱말로 반대를 말할 수 있으므로 그 축에서는 두 상태가 같은 값이다.

        형제 봉인 ``TestProviderIdentityValue`` 가 `central.env.example` 에 대해
        쓰는 축과 **같은 것**을 쓴다 — 이 문서가 그 SSOT 집합에 합류한다는 것이
        이 웨이브의 요점이므로, 판정 방식도 같아야 한다.
        """
        expected = _contract_provider_id()
        text = _RUNBOOK.read_text(encoding='utf-8')
        assignments = re.findall(
            r'FCC_CENTRAL_PROVIDER_ID=([^\s`|]*)', text,
        )
        self.assertTrue(
            assignments,
            '런북에 FCC_CENTRAL_PROVIDER_ID 대입문이 하나도 없다 — '
            '이 검사가 아무것도 재지 않는다(비-공허성).',
        )
        offenders = [value for value in assignments if value != expected]
        self.assertEqual(
            offenders, [],
            f'런북의 모든 FCC_CENTRAL_PROVIDER_ID 대입은 계약 SSOT {expected!r} '
            f'여야 한다 — 중앙 env·compose 기본값과 같은 값이다. 위반: {offenders!r}',
        )

    def test_the_runbook_keeps_no_uuid_placeholder_for_that_env(self):
        """``<PROVIDER_UUID>`` 자리표시자가 남아 있으면 운영자가 UUID 를 채운다."""
        text = _RUNBOOK.read_text(encoding='utf-8')
        self.assertNotIn(
            'FCC_CENTRAL_PROVIDER_ID=<PROVIDER_UUID>', text,
            '런북이 아직 UUID 자리표시자를 남겨 두고 있다.',
        )

    def test_the_runbook_states_the_contract_provider_code(self):
        """*무엇이 틀렸는지*만 적고 *무엇이 맞는지*를 안 적으면 다음 사람이 또 고른다."""
        text = _RUNBOOK.read_text(encoding='utf-8')
        self.assertIn(_contract_provider_id(), text)

    def test_the_runbook_points_at_the_pairing_checker(self):
        text = _RUNBOOK.read_text(encoding='utf-8')
        self.assertIn('check_central_provider_id_pairing.py', text)


class TestTheCopyCensusIsDerivedNotHandListed(unittest.TestCase):
    """**이 웨이브의 근본 처방.**

    형제 봉인은 사본 **둘**(`central.env.example` · compose 기본값)을 손으로 열거했다.
    셋째 사본(런북)이 그 목록에 없어서 드리프트했고, 아무도 그것을 몰랐다. 목록을
    하나 늘리면 넷째 사본이 생기는 날 같은 일이 반복된다.

    그러므로 **목록을 늘리지 않고 축을 바꾼다** — 저장소에서 그 env 를 대입하는 자리를
    **전수 발견**해서 전부 계약 SSOT 와 같은지 묻는다. 새 사본이 생기면 자동으로
    이 검사의 대상이 된다.
    """

    #: **운영자가 따라 하는 자리**만 본다 — 배포 설정과 절차서.
    #:
    #: ⚠️ 범위를 좁히는 것은 한계이고, 그 한계를 이름으로 적는다. `.claude/**` 의
    #: 평가서·장부는 *과거에 어떤 값이 틀렸는지*를 **기록**하므로 그 안의 옛 값은
    #: 위반이 아니라 증거다. `tests/**` 는 fixture 로 다른 값을 일부러 쓴다.
    #: 그러나 **운영자가 복사하는 파일이 새로 생기면 이 셋 아래 놓이므로** 자동으로
    #: 대상이 된다 — 그것이 손 열거와의 차이다.
    _OPERATOR_FACING_ROOTS = ('infra/', 'docs/operations/', 'scripts/')

    #: 검사기 자신은 진단 문구에 틀린 값을 예시로 담는다.
    _FIXTURE_OWNERS = {'scripts/check_central_provider_id_pairing.py'}

    def test_every_assignment_in_the_repo_is_the_contract_provider_code(self):
        expected = _contract_provider_id()
        pattern = re.compile(r'FCC_CENTRAL_PROVIDER_ID=([^\s`|\'"]*)')
        offenders: list[str] = []
        found_any = False

        for path in _REPO_ROOT.rglob('*'):
            if not path.is_file():
                continue
            rel = path.relative_to(_REPO_ROOT).as_posix()
            if not rel.startswith(self._OPERATOR_FACING_ROOTS):
                continue
            if rel in self._FIXTURE_OWNERS:
                continue
            try:
                text = path.read_text(encoding='utf-8-sig')
            except (OSError, UnicodeDecodeError):
                continue
            for value in pattern.findall(text):
                # compose 의 ``${VAR:-default}`` 는 참조이지 대입이 아니다 —
                # 그 기본값은 형제 봉인이 이미 계약 SSOT 로 묶는다.
                if value.startswith('${'):
                    continue
                found_any = True
                if value and value != expected:
                    offenders.append(f'{rel}: {value!r}')

        self.assertTrue(
            found_any,
            '저장소에서 FCC_CENTRAL_PROVIDER_ID 대입을 하나도 못 찾았다 — '
            '술어가 깨졌다(비-공허성). 이 검사가 아무것도 재지 않는다.',
        )
        self.assertEqual(
            offenders, [],
            f'FCC_CENTRAL_PROVIDER_ID 를 대입하는 모든 자리는 계약 SSOT '
            f'{expected!r} 여야 한다. 위반:\n  ' + '\n  '.join(offenders),
        )


class TestTheOperatorCanActuallyRunIt(unittest.TestCase):
    """⚠️ pytest 환경은 운영자 환경이 아니다.

    이 클래스가 없을 때 봉인은 **전부 green** 이었는데 스크립트를 그냥 실행하면
    `ModuleNotFoundError` 로 죽었다(실측 2026-09-02) — conftest 가 경로를 깔아 주고
    있었기 때문이다. 검사기의 존재 이유가 *운영자가 돌리는 것*이므로, 그 축을 재지
    않는 봉인은 요점을 비켜간다.
    """

    def test_running_it_as_a_subprocess_produces_a_verdict(self):
        import subprocess
        completed = subprocess.run(
            [sys.executable, str(_SCRIPT),
             '--central-env', str(_REPO_ROOT / 'infra' / 'central' / 'central.env.example'),
             '--node-value', _contract_provider_id()],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        self.assertEqual(
            completed.returncode, 0,
            f'stdout={completed.stdout!r} stderr={completed.stderr!r}',
        )
        self.assertIn('agree', completed.stdout)

    def test_a_disagreeing_pair_exits_one_as_a_subprocess(self):
        import subprocess
        completed = subprocess.run(
            [sys.executable, str(_SCRIPT),
             '--central-env', str(_REPO_ROOT / 'infra' / 'central' / 'central.env.example'),
             '--node-value', '70a985fa-4724-4d71-a227-ef9ea7605808'],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn('runbook', completed.stderr.lower())


if __name__ == '__main__':
    unittest.main()
