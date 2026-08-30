"""Platform test-report service (Phase G, 2026-06-23).

``CentralReportService`` is the application boundary between the platform driving
adapter (``PlatformApiAdapter``) and the central report read/write ports + the
project read port. It owns the report-surface logic:

1. **list_reports** — read a project's report rows and project meta, deriving each
   row's ``report_number`` (``S-{management_number}-{edition}``, report_number_
   policy SSOT) since it is not stored.
2. **create_report** — validate ``edition`` (non-empty) + the project exists
   (→ 404), insert one row race-safe on ``(project_id, edition)`` (duplicate →
   409 ``ReportEditionConflictError``), and return the created report envelope.
3. **get_report_citation** — assemble the report header citation (SN / firmware /
   FCC ID / applicant / standard) from the project detail via the
   ``report_citation`` domain SSOT. ``edition`` (optional) feeds the derived
   ``report_number``; the sample citations carry ``sample_number`` (the join key
   to the local measurement DB).

dependency-free of infrastructure / FastAPI / SQL — only the domain ports +
domain services + stdlib ``uuid`` / ``datetime``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from dataclasses import asdict
import json
from typing import Callable, Optional

from fcc_test_platform.application.central_project_service import ProjectNotFoundError
from application.central_contract.envelope_helpers import optional_text, require_uuid, text
from domain.models.sample_inventory import SNAPSHOT_SCHEMA_VERSION
from domain.models.session_provenance import SessionOrigin
from domain.ports.output.central_project_port import CentralProjectReadPort
from domain.ports.output.central_report_port import (
    CentralReportReadPort,
    CentralReportWritePort,
    ReportEditionConflictError,
    ReportSessionNotFoundError,
)
from domain.services import report_citation, report_number_policy


__all__ = ['CentralReportService']


class CentralReportService:
    def __init__(
        self,
        read_port: CentralReportReadPort,
        write_port: CentralReportWritePort,
        project_read_port: CentralProjectReadPort,
        *,
        clock: Optional[Callable[[], str]] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self._read = read_port
        self._write = write_port
        self._project_read = project_read_port
        self._clock = clock or _utcnow_iso
        self._id_factory = id_factory or _uuid4_str

    def list_reports(self, project_id: str) -> list[dict]:
        """Return the project's reports (newest first), each with a derived
        ``report_number``. Raises ``ValueError`` (malformed id) or
        ``ProjectNotFoundError`` (unknown project)."""
        pid = require_uuid(project_id, 'project_id')
        management_number = self._require_project_management_number(pid)
        rows = self._read.list_reports(pid)
        return [_report_envelope(row, management_number) for row in rows]

    def create_report(
        self, project_id: str, *, edition: str,
        date_of_issue: Optional[str] = None,
        date_tested_start: Optional[str] = None,
        date_tested_end: Optional[str] = None,
        prepared_by: Optional[str] = None,
        prepared_site: Optional[str] = None,
        rev_history_json: object = None,
    ) -> dict:
        """Create one report for ``project_id`` at ``edition`` and return its
        envelope. ``edition`` is required; a duplicate ``(project_id, edition)``
        raises ``ReportEditionConflictError`` (409)."""
        pid = require_uuid(project_id, 'project_id')
        edition_token = _require_text(edition, 'edition')
        management_number = self._require_project_management_number(pid)

        now = self._clock()
        record = {
            'id': self._id_factory(),
            'project_id': pid,
            'edition': edition_token,
            'date_of_issue': _opt_text(date_of_issue),
            'date_tested_start': _opt_text(date_tested_start),
            'date_tested_end': _opt_text(date_tested_end),
            'prepared_by': _opt_text(prepared_by),
            'prepared_site': _opt_text(prepared_site),
            'rev_history_json': rev_history_json,
            'created_at': now,
            'updated_at': now,
        }
        created = self._write.create_report(record)
        if created is None:
            raise ReportEditionConflictError(
                f'report edition {edition_token!r} already exists for this project'
            )
        return _report_envelope(
            {
                'report_id': record['id'],
                'project_id': pid,
                'edition': edition_token,
                'date_of_issue': record['date_of_issue'],
                'date_tested_start': record['date_tested_start'],
                'date_tested_end': record['date_tested_end'],
                'prepared_by': record['prepared_by'],
                'prepared_site': record['prepared_site'],
                'rev_history_json': rev_history_json,
                'created_at': now,
            },
            management_number,
        )

    def get_report_citation(
        self, project_id: str, *, edition: Optional[str] = None,
        session_snapshot: Optional[dict] = None, session_id: Optional[str] = None,
    ) -> dict:
        """Assemble the report header citation for the project at ``edition``.

        Raises ``ValueError`` (malformed id) or ``ProjectNotFoundError`` (unknown
        project)."""
        pid = require_uuid(project_id, 'project_id')
        detail = self._project_read.read_project_detail(pid)
        if detail is None:
            raise ProjectNotFoundError(f'unknown project_id {pid!r}')
        if session_id is not None:
            sid = require_uuid(session_id, 'session_id')
            session_snapshot = _validated_session_snapshot(
                self._read.get_session_snapshot(sid), project_id=pid,
            )
        if session_snapshot is None:
            citation = report_citation.build_report_citation(
                detail, edition=_opt_text(edition),
            )
        else:
            citation = report_citation.build_report_citation_from_session_snapshot(
                detail, session_snapshot, edition=_opt_text(edition),
            )
        return _citation_envelope(pid, citation)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _require_project_management_number(self, project_id: str) -> Optional[str]:
        """Read the project (→ 404 when unknown) and return its management number
        (the report_number basis; may be ``None`` when unassigned)."""
        detail = self._project_read.read_project_detail(project_id)
        if detail is None:
            raise ProjectNotFoundError(f'unknown project_id {project_id!r}')
        return optional_text(detail.get('management_number'))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid4_str() -> str:
    return str(uuid.uuid4())


def _require_text(value: object, label: str) -> str:
    cleaned = '' if value is None else str(value).strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _opt_text(value: object) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _report_envelope(row: dict, management_number: Optional[str]) -> dict:
    edition = optional_text(row.get('edition'))
    return {
        'report_id': text(row.get('report_id')),
        'project_id': text(row.get('project_id')),
        'edition': edition,
        # Derived (never stored): S-{management_number}-{edition}.
        'report_number': report_number_policy.report_number(management_number, edition),
        'date_of_issue': optional_text(row.get('date_of_issue')),
        'date_tested_start': optional_text(row.get('date_tested_start')),
        'date_tested_end': optional_text(row.get('date_tested_end')),
        'prepared_by': optional_text(row.get('prepared_by')),
        'prepared_site': optional_text(row.get('prepared_site')),
        'rev_history': row.get('rev_history_json'),
        'created_at': text(row.get('created_at')),
    }


def _citation_envelope(project_id: str, citation: report_citation.ReportCitation) -> dict:
    # dataclasses.asdict recurses into the nested SampleCitation/FirmwareCitation
    # frozen dataclasses → a JSON-safe nested dict. samples is a tuple → list.
    payload = asdict(citation)
    payload['project_id'] = project_id
    payload['samples'] = list(payload.get('samples') or [])
    return payload


def _validated_session_snapshot(record: Optional[dict], *, project_id: str) -> dict:
    """Resolve only a same-project, complete immutable WEB session cut."""
    if not isinstance(record, dict) or record.get('project_id') != project_id:
        raise ReportSessionNotFoundError('session is not part of this project')
    raw = record.get('sample_snapshot_json')
    if record.get('session_origin') != SessionOrigin.WEB_SESSION.value or not raw:
        raise ReportSessionNotFoundError('session has no web sample snapshot')
    if record.get('sample_snapshot_schema_version') != SNAPSHOT_SCHEMA_VERSION:
        raise ReportSessionNotFoundError('session snapshot schema is unsupported')
    try:
        snapshot = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReportSessionNotFoundError('session snapshot is invalid') from exc
    if not isinstance(snapshot, dict):
        raise ReportSessionNotFoundError('session snapshot is not an object')
    project = snapshot.get('project')
    sample = snapshot.get('sample')
    record_sample_id = record.get('sample_id')
    if (
        not isinstance(project, dict)
        or project.get('project_id') != project_id
        or not isinstance(sample, dict)
        or not sample.get('sample_id')
        # ``test_sessions.sample_id`` is intentionally nullable with
        # ``ON DELETE SET NULL``. The immutable WEB snapshot remains the
        # citation authority after the live inventory row is hard-deleted;
        # reject only a present, conflicting foreign-key value.
        or (
            record_sample_id is not None
            and sample.get('sample_id') != record_sample_id
        )
    ):
        raise ReportSessionNotFoundError('session snapshot identity is invalid')
    return snapshot
