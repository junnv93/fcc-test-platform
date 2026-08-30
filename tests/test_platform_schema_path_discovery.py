"""The platform schema SSOT must be findable from whichever tree ships it.

``rbac_role_catalog`` reads ``docs/platform/central_db_schema.v1.json`` at
**module scope** and used to locate it with a fixed ``parents[3]``. That was
correct in exactly one tree: this module lives at
``src/application/platform/rbac_role_catalog.py`` in the monorepo and at
``fcc_test_platform/application/rbac_role_catalog.py`` in the delivered package
— one level shallower — so the same arithmetic pointed *above* the delivered
tree and the module raised ``FileNotFoundError`` on import. Measured 2026-08-12:
ten delivered test files could not be collected because of it.

Neither extraction axis can see this. The boundary gate asks what a tree may
import and the dependency-resolution gate asks whether an import resolves; a
data path is not an import, so both stayed green. That is why this file exists:
the defect class needs a seal that does not live in the extraction gates.

The monorepo cannot host that seal on its own — discovery there resolves to the
same answer the old arithmetic gave, so a regression is invisible at this depth.
These tests build trees at *other* depths, which is the only place the two
implementations disagree.
"""
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

from fcc_test_platform.application.rbac_role_catalog import (  # noqa: E402
    _SCHEMA_PATH_ENV,
    _SCHEMA_RELATIVE_PATH,
    _discover_schema_path,
    _resolve_schema_path,
)


class TestTheSchemaIsFoundFromTheTreeThatShipsIt(unittest.TestCase):
    def test_the_monorepo_answer_is_unchanged(self):
        """Behaviour here must be byte-identical to the old arithmetic.

        This is the control: the repair is only allowed to change the answer in
        trees where the old one was wrong.
        """
        # Deliberately *not* resolved through the delivered-tree layout: the
        # property under control — "the new discovery agrees with parents[3]" —
        # is true exactly where the module sits at monorepo depth, and false in
        # a delivered tree by construction. That is the defect the repair
        # exists for, so a resolver that made this pass everywhere would be
        # asserting the opposite of the axis. This control therefore does not
        # travel; it is recorded by name in
        # governance.delivered_test_run_baseline.
        here = Path(
            project_root / 'src' / 'application' / 'platform' / 'rbac_role_catalog.py'
        ).resolve()
        legacy = here.parents[3] / _SCHEMA_RELATIVE_PATH

        self.assertEqual(_discover_schema_path(), legacy)
        self.assertTrue(legacy.is_file())

    def test_a_shallower_tree_still_finds_it(self):
        """The delivered shape, reproduced.

        ``fcc_test_platform/application/…`` is one level shallower than
        ``src/application/platform/…``. A fixed depth misses by exactly that.
        """
        import importlib.util
        import tempfile

        source = (
            resolve_repo_artifact(__file__, 'src/application/platform/rbac_role_catalog.py')
        ).read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as tmpdir:
            tree = Path(tmpdir) / 'fcc-test-platform'
            module_path = tree / 'fcc_test_platform' / 'application' / 'rbac_role_catalog.py'
            module_path.parent.mkdir(parents=True)
            module_path.write_text(source, encoding='utf-8')
            schema = tree / _SCHEMA_RELATIVE_PATH
            schema.parent.mkdir(parents=True)
            # The real SSOT, copied. A thin stand-in would only prove that
            # discovery found *a* file; module import is the assertion here, and
            # it needs the document the module actually parses.
            schema.write_bytes(
                (project_root / _SCHEMA_RELATIVE_PATH).read_bytes()
            )

            spec = importlib.util.spec_from_file_location(
                'staged_rbac_role_catalog', module_path,
            )
            module = importlib.util.module_from_spec(spec)
            # Import is the assertion: the old arithmetic raised
            # FileNotFoundError right here, at module scope.
            spec.loader.exec_module(module)
            found = module._discover_schema_path()

            self.assertEqual(found, schema.resolve())
            # Stated without a depth, deliberately: a test about not encoding a
            # depth should not encode one either.
            self.assertTrue(
                found.is_relative_to(tree.resolve()),
                'discovery escaped the tree the module belongs to',
            )

    def test_the_nearest_tree_wins_when_two_ancestors_qualify(self):
        """Deterministic, and it must not reach past its own package.

        A staging root that also happens to hold ``docs/platform/`` — the shape
        that produced the original failure — must not capture a module that has
        its own copy one level down.
        """
        import importlib.util
        import tempfile

        source = (
            resolve_repo_artifact(__file__, 'src/application/platform/rbac_role_catalog.py')
        ).read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as tmpdir:
            outer = Path(tmpdir)
            (outer / _SCHEMA_RELATIVE_PATH).parent.mkdir(parents=True)
            (outer / _SCHEMA_RELATIVE_PATH).write_bytes(
                (project_root / _SCHEMA_RELATIVE_PATH).read_bytes()
            )
            tree = outer / 'fcc-test-platform'
            module_path = tree / 'fcc_test_platform' / 'application' / 'rbac_role_catalog.py'
            module_path.parent.mkdir(parents=True)
            module_path.write_text(source, encoding='utf-8')
            (tree / _SCHEMA_RELATIVE_PATH).parent.mkdir(parents=True)
            (tree / _SCHEMA_RELATIVE_PATH).write_bytes(
                (project_root / _SCHEMA_RELATIVE_PATH).read_bytes()
            )

            spec = importlib.util.spec_from_file_location(
                'nested_rbac_role_catalog', module_path,
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            self.assertEqual(
                module._discover_schema_path(), (tree / _SCHEMA_RELATIVE_PATH).resolve(),
            )

    def test_the_env_override_still_wins(self):
        """The explicit escape hatch is untouched by the discovery change."""
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {_SCHEMA_PATH_ENV: '/tmp/explicit-schema.json'}):
            self.assertEqual(_resolve_schema_path(), Path('/tmp/explicit-schema.json'))

    def test_no_repository_relative_depth_arithmetic_remains_in_this_module(self):
        """The seal that keeps the old shape from coming back by hand."""
        import ast

        source = (
            resolve_repo_artifact(__file__, 'src/application/platform/rbac_role_catalog.py')
        ).read_text(encoding='utf-8')
        offenders = [
            node.lineno
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == 'parents'
            and isinstance(node.slice, ast.Constant)
        ]

        self.assertEqual(
            offenders, [],
            'a fixed parents[N] is back; it is correct in exactly one tree',
        )


if __name__ == '__main__':
    unittest.main()
