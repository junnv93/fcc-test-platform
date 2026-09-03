"""`scripts/make_central_env.py` 의 봉인 (2026-09-03).

**이 봉인이 지키는 성질은 「생성기가 값을 나르기만 하지 않는다」다.**

⚠️ 실측 2026-09-03 (형제 레인): 챔버 env 생성기가
`FCC_CENTRAL_PROVIDER_ID=unlicensed` 를 **값이 있으면 옳은지 안 보고 그대로 날랐다.**
계약 SSOT 는 `fcc-unlicensed-conducted` 이고, 그대로 붙었으면 heartbeat 가 404 로
막히면서 증상은 「노드가 안 뜬다」였을 것이다. 같은 날 두 번째로 `FCC_CENTRAL_CLIENT_ID`
가 같은 형태로 틀린 것이 발견됐다.

> **생성기가 값을 나르기만 하면 그것은 두 번째 SSOT 다.**

그러므로 이 봉인이 묻는 것은 *「파일을 만드나」* 가 아니라 **「틀린 입력을 거부하나」** 다.
"""
from __future__ import annotations

import contextlib
import io
import pathlib
import re
import sys
import tempfile
import unittest
import unittest.mock

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / 'scripts'
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import make_central_env as maker  # noqa: E402


class _Target:
    """`TARGET` 을 임시 경로로 바꾸고 출력을 삼킨다."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = maker.TARGET
        maker.TARGET = pathlib.Path(self._tmp.name) / 'central.env'
        return self

    def __exit__(self, *a):
        maker.TARGET = self._saved
        self._tmp.cleanup()

    def run(self, *argv: str) -> tuple[int, bool, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = maker.main(list(argv))
        return code, maker.TARGET.exists(), buf.getvalue()

    def text(self) -> str:
        return maker.TARGET.read_text(encoding='utf-8')


class TestTheExampleIsPresent(unittest.TestCase):
    """⚠️ 비-공허성 — example 이 없으면 아래 전부가 「비교할 것이 없어서」 통과한다."""

    def test_the_example_exists_and_holds_the_keys_this_script_edits(self):
        self.assertTrue(maker.EXAMPLE.is_file(), f'{maker.EXAMPLE} 가 없다')
        text = maker.EXAMPLE.read_text(encoding='utf-8')
        keys = (*maker.SECRETS, *maker.OPERATOR_KEYS, *maker.CONTRACT_KEYS)
        self.assertTrue(keys, '편집 대상 키가 0개다 — 이 검사가 아무것도 판정하지 않는다')
        for key in keys:
            with self.subTest(key=key):
                self.assertRegex(text, rf'(?m)^{re.escape(key)}=')


class TestItRefusesRatherThanGuessing(unittest.TestCase):
    """⚠️ **거부 경로가 이 파일의 존재 이유다.** 절반만 쓴 env 는
    「설정이 틀렸다」와 「생성이 중단됐다」를 같은 모양으로 만든다."""

    def test_a_hostname_public_host_is_refused_and_nothing_is_written(self):
        with _Target() as t:
            code, wrote, out = t.run('--public-host', 'central-pc.local')
        self.assertEqual(1, code)
        self.assertFalse(wrote, '거부했는데 파일을 썼다')
        self.assertIn('IP 가 아니다', out)

    def test_a_range_without_a_prefix_length_is_refused(self):
        """⚠️ `ip_network('10.206.0.0', strict=False)` 는 **거부하지 않고 `/32`** 를 준다.

        즉 `/16` 을 빠뜨리면 **주소 하나만 신뢰**하게 되는데, 그 실패는
        「대역을 적었다」와 출력에서 같은 모양이고 운영 중에는 「어떤 챔버는 되고
        어떤 챔버는 안 된다」로 나타난다. 실측 2026-09-03: 첫 판이 이것을 통과시켰다.
        """
        with _Target() as t:
            code, wrote, out = t.run(
                '--public-host', '10.206.34.233', '--client-ranges', '10.206.0.0')
        self.assertEqual(1, code)
        self.assertFalse(wrote)
        self.assertIn('접두 길이', out)

    def test_an_existing_file_is_not_overwritten_without_force(self):
        with _Target() as t:
            maker.TARGET.write_text('EXISTING=1\n', encoding='utf-8')
            code, _, out = t.run('--public-host', '10.206.34.233')
            self.assertEqual(2, code)
            self.assertIn('이미 있다', out)
            self.assertEqual('EXISTING=1\n', t.text(), '거부했는데 내용이 바뀌었다')


class TestItAsksTheContractRatherThanCarryingAValue(unittest.TestCase):
    """계약 SSOT 대조 — 이 웨이브가 등재한 성질."""

    def test_the_provider_id_matches_the_contract_source_of_truth(self):
        from fcc_test_contracts.headless.api_contracts import DEFAULT_PROVIDER_METADATA

        with _Target() as t:
            code, wrote, _ = t.run('--public-host', '10.206.34.233')
            self.assertEqual(0, code)
            self.assertTrue(wrote)
            written = maker._read_value(t.text(), 'FCC_CENTRAL_PROVIDER_ID')
        self.assertEqual(str(DEFAULT_PROVIDER_METADATA['provider_id']), written)

    def test_a_drifted_provider_id_is_refused(self):
        """⚠️ **판별력** — 대조가 실제로 붙잡는가.

        이 팔이 없으면 위 검사는 *「example 이 우연히 맞다」* 만 증명한다.
        """
        with _Target() as t:
            # ⚠️ `Path` 의 메서드는 패치할 수 없다(C 레벨 슬롯). **example 자체를
            # 드리프트시킨 임시 파일로 바꾼다** — 그것이 실제 조건에 더 가깝기도 하다:
            # 이 결함의 실제 형태는 「example 이 낡았다」이지 「read_text 가 거짓말한다」가
            # 아니다.
            drifted = re.sub(
                r'(?m)^FCC_CENTRAL_PROVIDER_ID=.*$',
                'FCC_CENTRAL_PROVIDER_ID=unlicensed',
                maker.EXAMPLE.read_text(encoding='utf-8'))
            fake = maker.TARGET.parent / 'drifted.env.example'
            fake.write_text(drifted, encoding='utf-8')
            saved_example, maker.EXAMPLE = maker.EXAMPLE, fake
            try:
                code, wrote, out = t.run('--public-host', '10.206.34.233')
            finally:
                maker.EXAMPLE = saved_example
        self.assertEqual(1, code)
        self.assertFalse(wrote, '드리프트를 감지하고도 파일을 썼다')
        self.assertIn('계약 SSOT 와 다르다', out)


class TestSecretsAreGeneratedAndNeverPrinted(unittest.TestCase):
    def test_every_demo_secret_is_replaced(self):
        with _Target() as t:
            code, _, _ = t.run('--public-host', '10.206.34.233')
            self.assertEqual(0, code)
            text = t.text()
        for key, demo in maker.SECRETS.items():
            with self.subTest(key=key):
                value = maker._read_value(text, key)
                self.assertNotEqual(demo, value, f'{key} 가 데모 값 그대로다')
                self.assertGreaterEqual(len(value or ''), 32)

    def test_the_generated_secrets_are_all_different(self):
        """같은 값을 다섯 곳에 쓰면 하나가 새면 전부 샌다."""
        with _Target() as t:
            t.run('--public-host', '10.206.34.233')
            text = t.text()
        values = [maker._read_value(text, key) for key in maker.SECRETS]
        self.assertEqual(len(values), len(set(values)))

    def test_no_secret_value_reaches_stdout(self):
        """⚠️ 운영 런북이 *「시크릿을 채팅·이슈·문서에 붙여 넣지 않는다」* 를 명시한다.
        이 스크립트의 출력이 그 경로가 되면 안 된다."""
        with _Target() as t:
            _, _, out = t.run('--public-host', '10.206.34.233')
            text = t.text()
        for key in maker.SECRETS:
            with self.subTest(key=key):
                self.assertNotIn(maker._read_value(text, key), out)

    def test_the_file_is_not_world_readable(self):
        with _Target() as t:
            t.run('--public-host', '10.206.34.233')
            mode = maker.TARGET.stat().st_mode & 0o777
        self.assertEqual(0o600, mode, f'권한이 {oct(mode)} 다 — 시크릿 파일이다')


if __name__ == '__main__':  # pragma: no cover
    import unittest.mock  # noqa: F401
    unittest.main(verbosity=2)
