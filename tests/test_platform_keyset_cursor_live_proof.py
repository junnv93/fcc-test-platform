"""Focused contract seal for the live PostgreSQL keyset proof runner.

The implementation guard belongs to the row-lock proof function, not to a
whole-file substring search.  The optional live test exercises the runner's
actual result contract when the disposable proof DSN is supplied.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import fcc_test_platform.keyset_cursor_live_proof_cli as live_proof  # noqa: E402

from tests._moved_module_source import moved_module_source  # noqa: E402

#: ⚠️ **경로가 아니라 모듈에게 묻는다** (2026-09-05). 이 두 시험은
#: `_prove_equipment_config_row_lock` 의 «본문»을 AST 로 파헤친다. 알맹이가
#: `fcc_test_platform.keyset_cursor_live_proof_cli` 로 간 뒤 `scripts/` 쪽에는
#: 22줄 진입점만 남아 그 함수가 아예 없다 — 경로로 읽으면 이 축이 사라진다.
_GUTS_SOURCE = moved_module_source('fcc_test_platform.keyset_cursor_live_proof_cli')


def _function_node(source: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"missing function under test: {name}")


class TestLiveProofCounterfactualContract(unittest.TestCase):
    """Keep the counterfactual tied to the production SQL constant."""

    _FUNCTION = "_prove_equipment_config_row_lock"

    def test_counterfactual_derivation_is_scoped_to_the_proof_function(self) -> None:
        source = _GUTS_SOURCE.read_text(encoding="utf-8")
        function = _function_node(source, self._FUNCTION)

        derived_calls = []
        production_constant_uses = []
        literal_selects = []
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                receiver = node.func.value
                if (
                    node.func.attr == "sub"
                    and isinstance(receiver, ast.Name)
                    and receiver.id == "_FOR_UPDATE_SUFFIX"
                ):
                    derived_calls.append(node)
            if isinstance(node, ast.Name) and node.id == (
                "SELECT_CHAMBER_EQUIPMENT_CONFIG_FOR_UPDATE_SQL"
            ):
                production_constant_uses.append(node)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.lstrip().upper().startswith("SELECT "):
                    literal_selects.append(node.value)

        self.assertEqual(len(derived_calls), 1)
        derived = derived_calls[0]
        self.assertEqual(len(derived.args), 2)
        self.assertIsInstance(derived.args[0], ast.Constant)
        self.assertEqual(derived.args[0].value, "")
        self.assertIsInstance(derived.args[1], ast.Name)
        self.assertEqual(
            derived.args[1].id,
            "SELECT_CHAMBER_EQUIPMENT_CONFIG_FOR_UPDATE_SQL",
        )
        self.assertGreaterEqual(len(production_constant_uses), 2)
        self.assertEqual(literal_selects, [])

        counterfactual = live_proof._FOR_UPDATE_SUFFIX.sub(
            "", live_proof.SELECT_CHAMBER_EQUIPMENT_CONFIG_FOR_UPDATE_SQL,
        )
        self.assertNotIn("FOR UPDATE", counterfactual.upper())
        self.assertIn("equipment_config_json", counterfactual)
        self.assertIn("WHERE", counterfactual.upper())

    def test_counterfactual_both_key_guard_is_function_scoped(self) -> None:
        source = _GUTS_SOURCE.read_text(encoding="utf-8")
        function = _function_node(source, self._FUNCTION)
        matching_guards = []
        for node in ast.walk(function):
            if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
                continue
            comparison = node.test
            if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.Eq):
                continue
            if not isinstance(comparison.left, ast.Call):
                continue
            if not isinstance(comparison.left.func, ast.Name):
                continue
            if comparison.left.func.id != "sorted":
                continue
            if len(comparison.left.args) != 1:
                continue
            if not isinstance(comparison.left.args[0], ast.Name):
                continue
            if comparison.left.args[0].id != "unlocked":
                continue
            matching_guards.append(node)

        self.assertEqual(len(matching_guards), 1)
        guard = matching_guards[0]
        self.assertEqual(
            [element.value for element in guard.test.comparators[0].elts],
            ["analyzer", "switchbox"],
        )
        self.assertTrue(
            any(
                isinstance(node, ast.Raise)
                and isinstance(node.exc, ast.Call)
                and isinstance(node.exc.func, ast.Name)
                and node.exc.func.id == "LiveProofError"
                for node in ast.walk(guard)
            ),
        )


@unittest.skipUnless(
    os.environ.get("FCC_KEYSET_PROOF_DB_URL"),
    "FCC_KEYSET_PROOF_DB_URL not set — live PostgreSQL proof skipped",
)
class TestLiveProofResultContract(unittest.TestCase):
    """Assert the two-key production/counterfactual result on real PostgreSQL."""

    def test_equipment_config_row_lock_result(self) -> None:
        evidence = live_proof.run_live_proof(
            os.environ["FCC_KEYSET_PROOF_DB_URL"],
            proof_seed="focused-row-lock-test",
        )["equipment_config_row_lock"]
        self.assertEqual(evidence["locked_keys_kept"], ["analyzer", "switchbox"])
        self.assertTrue(evidence["counterfactual_lost_an_edit"])
        self.assertNotEqual(
            evidence["counterfactual_keys_kept"],
            ["analyzer", "switchbox"],
        )


if __name__ == "__main__":
    unittest.main()
