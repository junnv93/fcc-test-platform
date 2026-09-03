"""Report header citation assembly (pure domain, stdlib only).

A test report (성적서) header cites identity that lives across the central
``projects`` / ``samples`` / ``sample_intakes`` rows: the derived report number +
FCC ID, the applicant / EUT / standard meta, and per-sample the Serial Number
(PM 칸) plus the **latest** firmware set (시험원 입고 칸: BL/AP/CP/CSC + RF CAL +
HW Rev). This module assembles that citation from a project-detail mapping
(the shape the central project read adapter returns) WITHOUT re-implementing the
two derivation SSOTs — it reuses :mod:`report_number_policy` and
:mod:`fcc_id_policy`. Phase G (measurement·report connection, data boundary).

The link to the local measurement DB (MeasurementTargetIdentity = Model Number +
Sample No, ``{model}__{sample}.fcc.db``) is the sample's ``sample_number``: the
measurement "Sample No" equals the inventory sample number, so each
``SampleCitation`` carries ``sample_number`` as the join key. The actual local
``.fcc.db`` join (pulling measured channels into the report) is a hardware-session
follow-up; this assembly stops at the central-data citation.

Latest-intake convention (SSOT): ``sample_intakes`` are append-only and
``intake_date`` is free text (unsortable), so recency is decided by the read
layer (``created_at DESC``). The read adapter materializes that decision as the
sample's ``latest_intake`` mapping (or ``None``); this module consumes it
verbatim and never re-derives "latest = index 0", so the recency rule has a
single owner even though the full history is no longer shipped on the wire. A
missing ``latest_intake`` ⇒ ``latest_firmware is None`` (no firmware recorded yet).

dependency-free: stdlib + the two derivation policies (no infrastructure /
pandas / PySide6 / psycopg imports).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from fcc_test_platform.domain.services import fcc_id_policy, report_number_policy

__all__ = [
    'FirmwareCitation',
    'SampleCitation',
    'ReportCitation',
    'build_report_citation',
    'build_report_citation_from_session_snapshot',
]


@dataclass(frozen=True)
class FirmwareCitation:
    """The firmware/cal/hw set cited from a sample's latest intake."""

    bl: Optional[str] = None
    ap: Optional[str] = None
    cp: Optional[str] = None
    csc: Optional[str] = None
    rf_cal: Optional[str] = None
    hw_rev: Optional[str] = None


@dataclass(frozen=True)
class SampleCitation:
    """One EUT sample's citation: SN (PM 칸) + latest firmware (시험원 칸).

    ``sample_number`` is the join key to the local measurement DB (= measurement
    'Sample No'). ``latest_firmware`` is ``None`` when the sample has no recorded
    intake history yet.
    """

    sample_number: Optional[str] = None
    serial_number: Optional[str] = None
    latest_firmware: Optional[FirmwareCitation] = None


@dataclass(frozen=True)
class ReportCitation:
    """Everything a report header quotes, assembled from central data."""

    report_number: Optional[str] = None
    fcc_id: Optional[str] = None
    management_number: Optional[str] = None
    applicant_name: Optional[str] = None
    applicant_address: Optional[str] = None
    eut_description: Optional[str] = None
    test_standard: Optional[str] = None
    samples: tuple[SampleCitation, ...] = ()


def _opt(value: object) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _latest_firmware(latest: Optional[Mapping]) -> Optional[FirmwareCitation]:
    if not latest:
        return None
    return FirmwareCitation(
        bl=_opt(latest.get('bl')),
        ap=_opt(latest.get('ap')),
        cp=_opt(latest.get('cp')),
        csc=_opt(latest.get('csc')),
        rf_cal=_opt(latest.get('rf_cal')),
        hw_rev=_opt(latest.get('hw_rev')),
    )


def _sample_citation(sample: Mapping) -> SampleCitation:
    # ``latest_intake`` is the read layer's pre-selected most-recent intake (or
    # None) — recency selection is NOT re-implemented here.
    return SampleCitation(
        sample_number=_opt(sample.get('sample_number')),
        serial_number=_opt(sample.get('serial_number')),
        latest_firmware=_latest_firmware(sample.get('latest_intake')),
    )


def build_report_citation(
    project: Mapping, *, edition: Optional[str],
) -> ReportCitation:
    """Assemble the report header citation for ``project`` at ``edition``.

    ``project`` is the central project-detail mapping (``model_name`` /
    ``management_number`` / ``fcc_grantee_code`` / ``applicant_name`` /
    ``applicant_address`` / ``eut_description`` / ``test_standard`` / ``samples``).
    ``report_number`` is derived from ``management_number`` + ``edition`` and
    ``fcc_id`` from ``fcc_grantee_code`` + ``model_name`` (both via their domain
    SSOTs; ``None`` when their basis is absent).
    """
    management_number = _opt(project.get('management_number'))
    samples = project.get('samples') or ()
    return ReportCitation(
        report_number=report_number_policy.report_number(management_number, edition),
        fcc_id=fcc_id_policy.fcc_id(
            project.get('fcc_grantee_code'), str(project.get('model_name') or ''),
        ),
        management_number=management_number,
        applicant_name=_opt(project.get('applicant_name')),
        applicant_address=_opt(project.get('applicant_address')),
        eut_description=_opt(project.get('eut_description')),
        test_standard=_opt(project.get('test_standard')),
        samples=tuple(_sample_citation(s) for s in samples),
    )


def build_report_citation_from_session_snapshot(
    project: Mapping, snapshot: Mapping, *, edition: Optional[str],
) -> ReportCitation:
    """Build a citation whose sample fields come from an immutable session cut.

    Project-level citation keeps the existing current-project semantics. A
    report tied to a measurement session is different: later sample edits or a
    hard delete must not change its SN/firmware header. The canonical snapshot
    contains one sample and its selected intake, so it is projected into the
    same citation input shape without re-selecting current inventory rows.
    """
    if not isinstance(snapshot, Mapping):
        raise ValueError('session sample snapshot must be an object')
    sample = snapshot.get('sample')
    if not isinstance(sample, Mapping):
        raise ValueError('session sample snapshot has no sample object')
    detail = dict(project)
    detail['samples'] = [
        dict(sample, latest_intake=snapshot.get('latest_intake')),
    ]
    return build_report_citation(detail, edition=edition)
