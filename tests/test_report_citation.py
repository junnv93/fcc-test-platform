"""report_citation assembly — SN + latest firmware + derived report#/FCC ID (Phase G).

Pure-domain assembly of the report header citation from a central project-detail
mapping. Seals: derivation reuse (report_number/fcc_id SSOTs, not reimplemented),
the compact ``latest_intake`` consumption (the read adapter is the latest-first
SSOT; this module never re-derives "latest = index 0"), and the sample_number
join key to the local measurement DB. Edge cases: no samples / no
management_number / no intake history.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


from fcc_test_platform.domain.services import report_citation as rc  # noqa: E402


def _project(**overrides) -> dict:
    base = {
        "model_name": "SM-X940",
        "management_number": "4792232056",
        "fcc_grantee_code": "A3L",
        "applicant_name": "Samsung Electronics",
        "applicant_address": "Suwon",
        "eut_description": "BT/BLE Tablet",
        "test_standard": "FCC 47 CFR PART 15 SUBPART C",
        "samples": [],
    }
    base.update(overrides)
    return base


class TestProjectMetaCitation(unittest.TestCase):
    def test_report_number_derived(self):
        cit = rc.build_report_citation(_project(), edition="E2V1")
        self.assertEqual(cit.report_number, "S-4792232056-E2V1")

    def test_fcc_id_derived(self):
        cit = rc.build_report_citation(_project(), edition="E2V1")
        self.assertEqual(cit.fcc_id, "A3LSMX940")

    def test_report_number_none_without_management_number(self):
        cit = rc.build_report_citation(_project(management_number=None), edition="E2V1")
        self.assertIsNone(cit.report_number)

    def test_report_number_none_without_edition(self):
        cit = rc.build_report_citation(_project(), edition=None)
        self.assertIsNone(cit.report_number)

    def test_fcc_id_none_without_grantee(self):
        cit = rc.build_report_citation(_project(fcc_grantee_code=None), edition="E2V1")
        self.assertIsNone(cit.fcc_id)

    def test_meta_passthrough(self):
        cit = rc.build_report_citation(_project(), edition="E2V1")
        self.assertEqual(cit.applicant_name, "Samsung Electronics")
        self.assertEqual(cit.eut_description, "BT/BLE Tablet")
        self.assertEqual(cit.test_standard, "FCC 47 CFR PART 15 SUBPART C")

    def test_blank_meta_becomes_none(self):
        cit = rc.build_report_citation(_project(applicant_address="   "), edition="E2V1")
        self.assertIsNone(cit.applicant_address)


class TestSampleCitation(unittest.TestCase):
    def test_empty_samples(self):
        cit = rc.build_report_citation(_project(samples=[]), edition="E2V1")
        self.assertEqual(cit.samples, ())

    def test_serial_number_and_join_key(self):
        proj = _project(samples=[{"sample_number": "1", "serial_number": "R3GL123", "latest_intake": None}])
        cit = rc.build_report_citation(proj, edition="E2V1")
        self.assertEqual(len(cit.samples), 1)
        self.assertEqual(cit.samples[0].sample_number, "1")  # join key to {model}__{sample}.fcc.db
        self.assertEqual(cit.samples[0].serial_number, "R3GL123")

    def test_no_intake_history_yields_none_firmware(self):
        # Absent / null latest_intake ⇒ no firmware recorded yet.
        proj = _project(samples=[{"sample_number": "1", "serial_number": "R3GL123", "latest_intake": None}])
        cit = rc.build_report_citation(proj, edition="E2V1")
        self.assertIsNone(cit.samples[0].latest_firmware)

    def test_latest_firmware_from_pre_selected_latest_intake(self):
        # The read layer pre-selects the latest intake (created_at DESC) into
        # ``latest_intake``; this module consumes it without re-selecting.
        proj = _project(samples=[{
            "sample_number": "1",
            "serial_number": "R3GL123",
            "latest_intake": {
                "bl": "BL_NEW", "ap": "AP_NEW", "cp": "CP_NEW", "csc": "CSC_NEW",
                "rf_cal": "OK", "hw_rev": "REV2",
            },
        }])
        cit = rc.build_report_citation(proj, edition="E2V1")
        fw = cit.samples[0].latest_firmware
        self.assertIsNotNone(fw)
        self.assertEqual(fw.bl, "BL_NEW")
        self.assertEqual(fw.hw_rev, "REV2")

    def test_full_history_array_is_not_consumed(self):
        # Regression guard: a sample carrying a legacy full ``intakes`` array but no
        # ``latest_intake`` must NOT resurrect index-0 selection here. The latest
        # selection lives only in the read adapter, so this module yields no
        # firmware when the compact field is absent.
        proj = _project(samples=[{
            "sample_number": "1",
            "intakes": [{"bl": "BL_OLD", "hw_rev": "REV1"}],
        }])
        cit = rc.build_report_citation(proj, edition="E2V1")
        self.assertIsNone(cit.samples[0].latest_firmware)

    def test_multiple_samples_preserved(self):
        proj = _project(samples=[
            {"sample_number": "1", "serial_number": "R3GL1", "latest_intake": None},
            {"sample_number": "2", "serial_number": "R3GL2", "latest_intake": None},
        ])
        cit = rc.build_report_citation(proj, edition="E2V1")
        self.assertEqual([s.sample_number for s in cit.samples], ["1", "2"])


if __name__ == "__main__":
    unittest.main()
