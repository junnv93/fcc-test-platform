"""Unit tests for the FCC ID derivation policy (Phase B, project-report-meta).

``fcc_id`` is DERIVED (grantee code + product_code(model_name)), never stored.
The rule lives in a single pure domain SSOT ``domain/services/fcc_id_policy``.
"""
import os
import sys
import unittest


from fcc_test_platform.domain.services import fcc_id_policy  # noqa: E402


class TestProductCode(unittest.TestCase):
    def test_strips_hyphen_and_uppercases(self):
        self.assertEqual(fcc_id_policy.product_code('SM-X940'), 'SMX940')

    def test_strips_spaces_and_uppercases_lowercase(self):
        self.assertEqual(fcc_id_policy.product_code('sm x940'), 'SMX940')

    def test_mixed_punctuation_normalizes_to_alnum_only(self):
        self.assertEqual(fcc_id_policy.product_code('sm-x_940.v2'), 'SMX940V2')

    def test_already_clean_code_is_upper_passthrough(self):
        self.assertEqual(fcc_id_policy.product_code('SMX940'), 'SMX940')

    def test_empty_string(self):
        self.assertEqual(fcc_id_policy.product_code(''), '')


class TestFccId(unittest.TestCase):
    def test_grantee_plus_product_code(self):
        self.assertEqual(fcc_id_policy.fcc_id('A3L', 'SM-X940'), 'A3LSMX940')

    def test_grantee_is_trimmed_verbatim(self):
        # Grantee used verbatim (only outer whitespace trimmed); product code
        # normalized. ' A3L ' → 'A3L'.
        self.assertEqual(fcc_id_policy.fcc_id('  A3L  ', 'sm x940'), 'A3LSMX940')

    def test_none_grantee_yields_none(self):
        self.assertIsNone(fcc_id_policy.fcc_id(None, 'SM-X940'))

    def test_blank_grantee_yields_none(self):
        self.assertIsNone(fcc_id_policy.fcc_id('   ', 'SM-X940'))


if __name__ == '__main__':
    unittest.main()
