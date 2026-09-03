"""P6L-3a — PublishedTestPlan → PublishedConditionRow glue seal.

``published_conditions_from_plan`` must map every published row to one ingest
condition row, in order, carrying ``condition_hash`` + ``raw_test_type``
verbatim, with (technology, band) derived by the pure-domain glue so the bucket
SSOT accepts them (incl. the bare 6 GHz display dialect from the renderer).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fcc_test_platform.application.published_plan_progress_glue import (  # noqa: E402
    published_conditions_from_plan,
)
from fcc_test_kernel.domain.models.published_test_plan import PublishedTestPlan  # noqa: E402
from fcc_test_kernel.domain.models.test_plan_authoring import RowOrigin, TestPlanRow  # noqa: E402
from fcc_test_platform.domain.services.progress_bucket import progress_bucket_id  # noqa: E402


def _row(capability_path, *, condition_hash, test_type="PSD"):
    return TestPlanRow(
        capability_path=tuple(capability_path),
        origin=RowOrigin.GENERATED,
        scope_revision=1,
        condition_hash=condition_hash,
        test_type=test_type,
    )


def _plan(rows):
    return PublishedTestPlan(
        plan_id="plan-1",
        draft_id="draft-1",
        project_id="proj-1",
        scope_revision=1,
        scope_profile_json="{}",
        rows=tuple(rows),
    )


class TestPublishedConditionsFromPlan(unittest.TestCase):
    def test_maps_rows_in_order_verbatim_hash(self):
        h_bt = "a" * 64
        h_wlan = "b" * 64
        plan = _plan([
            _row(("BT", "BR", "2.4G", "BT|BDR;EDR|1MHz|39", "1M"),
                 condition_hash=h_bt, test_type="Output Power"),
            _row(("WLAN", "802.11a_6", "6G",
                  "WLAN|UNII-5(LPI);UNII-5(SP);UNII-5(VLP)|20MHz|Lower", "20M"),
                 condition_hash=h_wlan, test_type="PSD"),
        ])

        conditions = published_conditions_from_plan(plan)

        self.assertEqual(len(conditions), 2)
        # order + verbatim condition_hash + verbatim raw_test_type
        self.assertEqual(conditions[0].condition_hash, h_bt)
        self.assertEqual(conditions[0].raw_test_type, "Output Power")
        self.assertEqual(conditions[1].condition_hash, h_wlan)
        self.assertEqual(conditions[1].raw_test_type, "PSD")

    def test_tokens_round_trip_to_bucket(self):
        # The derived tokens must be bucketable by the provider SSOT — including
        # the 6 GHz row whose real (pipe-grammar) channel_ref_id carries the
        # power-class band segment → 'UNII-5(LPI)' → unii_5.
        plan = _plan([
            _row(("BT", "BR", "2.4G", "BT|BDR;EDR|1MHz|39", "1M"),
                 condition_hash="c" * 64),
            _row(("WLAN", "802.11a_6", "6G",
                  "WLAN|UNII-5(LPI);UNII-5(SP);UNII-5(VLP)|20MHz|Lower", "20M"),
                 condition_hash="d" * 64),
        ])

        conditions = published_conditions_from_plan(plan)

        self.assertEqual(
            progress_bucket_id(
                technology=conditions[0].technology, band=conditions[0].band
            ),
            "bt",
        )
        self.assertEqual(
            progress_bucket_id(
                technology=conditions[1].technology, band=conditions[1].band
            ),
            "unii_5",
        )


if __name__ == "__main__":
    unittest.main()
