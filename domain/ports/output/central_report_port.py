"""Output ports — central test-report (성적서) read + write (Phase G, 2026-06-23).

The platform report surface (``GET/POST /platform/projects/{id}/reports`` +
``GET …/report-citation``) reads + creates the central ``test_reports`` rows that
back a project's certificate instances. A project (= one model, ADR-0017 D1) has
N reports (editions/revisions); ``report_number`` is NOT stored — it is derived
(``S-{management_number}-{edition}``, report_number_policy SSOT, mirror of the
derived fcc_id).

Two narrow ports (mirror of the project read/write split):

- ``CentralReportReadPort`` — list one project's report rows (read-only).
- ``CentralReportWritePort`` — create one report row, race-safe on the
  ``(project_id, edition)`` natural key (a duplicate edition is reported as a
  conflict, never a silent overwrite).

The header citation (SN / firmware / FCC ID / applicant / standard) is assembled
in the application service from the project detail + report_citation domain SSOT;
it is NOT a port method here (no extra DB round trip — the project read port
already returns samples + intakes).

dependency-free: stdlib typing only (no infrastructure / pyvisa / openpyxl /
pandas / PySide6 / fastapi / sqlalchemy / psycopg imports).
"""
from __future__ import annotations

from typing import Mapping, Optional, Protocol, runtime_checkable


__all__ = [
    'CentralReportError',
    'ReportEditionConflictError',
    'ReportSessionNotFoundError',
    'CentralReportReadPort',
    'CentralReportWritePort',
]


class CentralReportError(RuntimeError):
    """Raised when a central report read/write fails at the infrastructure level
    — loud (→ 503), never a silently-empty list (which would render a project as
    "no reports" and mask a backend outage)."""


class ReportEditionConflictError(RuntimeError):
    """A report with the same ``(project_id, edition)`` already exists (→ 409).

    Distinct from ``CentralReportError`` (503 backend failure) and from
    ``ValueError`` (400 malformed input) so the platform error table maps it to a
    conflict status (mirror of ``ClaimConflictError``)."""


class ReportSessionNotFoundError(LookupError):
    """The requested session is missing or has no valid project snapshot."""


@runtime_checkable
class CentralReportReadPort(Protocol):
    """Read one project's test-report rows from the central DB."""

    def list_reports(self, project_id: str) -> list[dict]:
        """Return the project's report rows, newest first. Columns:
        ``{report_id, project_id, edition, date_of_issue, date_tested_start,
        date_tested_end, prepared_by, prepared_site, rev_history_json,
        created_at}``. ``report_number`` is NOT a column — the service derives it
        (``S-{management_number}-{edition}``). Empty list ⇒ no reports yet (a
        backend failure raises :class:`CentralReportError` instead, never a blank
        list)."""
        ...

    def get_session_snapshot(self, session_id: str) -> Optional[dict]:
        """Return immutable central session identity/snapshot columns, if present."""
        ...


@runtime_checkable
class CentralReportWritePort(Protocol):
    """Create one ``test_reports`` row, race-safe on (project_id, edition)."""

    def create_report(self, report_record: Mapping) -> Optional[dict]:
        """Insert one ``test_reports`` row and return
        ``{report_id, edition}``, or ``None`` when a row with the same
        ``(project_id, edition)`` already exists (the unique natural key short-
        circuits the insert — the service maps ``None`` to
        :class:`ReportEditionConflictError`). A connection/query failure raises
        :class:`CentralReportError`."""
        ...
