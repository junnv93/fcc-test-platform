"""report_number_policy SSOT — derived S-{management_number}-{edition} (Phase G).

Mirror of test_fcc_id_policy: the report number is derived, never stored, and is
``None`` when its basis (management_number or edition) is absent. The 'S-' / '-'
format lives only in the policy module (no hardcoded literal at call sites — see
TestNoHardcodedReportNumberFormat).
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


from fcc_test_platform.domain.services import report_number_policy as rnp  # noqa: E402
from fcc_test_platform.domain.services import report_number_policy as report_number_policy_module  # noqa: E402


class TestReportNumber(unittest.TestCase):
    def test_basic_derivation(self):
        self.assertEqual(rnp.report_number("4792232056", "E2V1"), "S-4792232056-E2V1")

    def test_trims_inputs(self):
        self.assertEqual(rnp.report_number("  4792232056 ", " E2V1 "), "S-4792232056-E2V1")

    def test_none_management_number_yields_none(self):
        self.assertIsNone(rnp.report_number(None, "E2V1"))

    def test_blank_management_number_yields_none(self):
        self.assertIsNone(rnp.report_number("   ", "E2V1"))

    def test_none_edition_yields_none(self):
        self.assertIsNone(rnp.report_number("4792232056", None))

    def test_blank_edition_yields_none(self):
        self.assertIsNone(rnp.report_number("4792232056", ""))

    def test_no_partial_emitted(self):
        # Never an 'S--E2V1' or 'S-4792232056-' partial.
        self.assertNotIn("--", rnp.report_number("4792232056", "E2V1"))

    def test_format_constants_are_ssot(self):
        self.assertEqual(rnp.REPORT_NUMBER_PREFIX, "S-")
        self.assertEqual(rnp.REPORT_NUMBER_SEPARATOR, "-")
        out = rnp.report_number("X", "Y")
        self.assertTrue(out.startswith(rnp.REPORT_NUMBER_PREFIX))


class TestNoHardcodedReportNumberFormat(unittest.TestCase):
    """No src/ module outside the policy hardcodes the 'S-' report-number prefix.

    A literal "S-" string constant elsewhere would be a format-drift bug (the
    fcc_id precedent: derivation format lives in exactly one SSOT). We scan src/
    string literals for the 'S-{...}' report-number shape, allowing the policy
    module itself.
    """

    POLICY_FILE = "report_number_policy.py"

    def test_no_report_number_prefix_literal_in_src(self):
        """⚠️ 이 팔은 «코드»가 아니라 «트리»를 단언한다 — 그래서 트리가 바뀌면 재조준해야 한다.

        모노레포에서는 `src/` 를 훑었다. 이 레인에서 같은 명제를 참으로 유지하려면
        훑을 트리가 패키지 디렉터리다. 경로를 손으로 적지 않고 검사 대상 모듈의
        위치에서 파생시켜, 다음 이전 때 같은 자리에서 다시 깨지지 않게 한다.
        """
        package_root = Path(report_number_policy_module.__file__).resolve().parents[2]
        offenders = []
        for path in package_root.rglob("*.py"):
            if path.name == self.POLICY_FILE:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    # The report-number shape is 'S-' immediately followed by an
                    # f-string brace or another 'S-{mgmt}-' join — flag the prefix
                    # literal used to build a report number.
                    if node.value.startswith("S-{") or node.value == "S-{management_number}-":
                        offenders.append(f"{path.relative_to(package_root)}: {node.value!r}")
        self.assertEqual(offenders, [], f"hardcoded report-number format: {offenders}")


if __name__ == "__main__":
    unittest.main()
