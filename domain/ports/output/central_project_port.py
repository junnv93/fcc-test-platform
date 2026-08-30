"""Output ports — central project registry read + write (Phase 1, 2026-06-22).

The platform project-entry surface (``GET/POST /platform/projects`` + detail)
reads + creates the central ``projects`` / ``device_models`` / ``samples`` rows
that back the "내 프로젝트" entry screen. ADR-0017 D1 pins a **1:1 project↔model
overlay** on the campaign-grouping schema: creating a project creates one
``projects`` row + one ``device_models`` row, and a same-``project_code`` request
reuses the existing project (idempotent).

Two narrow ports (mirror of the chamber read/write split):

- ``CentralProjectReadPort`` — list the caller's projects + read one project's
  detail (model + samples). Read-only by construction.
- ``CentralProjectWritePort`` — find a project by its unique ``project_code`` and
  create the (project + device_model) pair atomically.

condition_hash / claim / coverage are NOT touched here (ADR-0017 D2 —
sample-agnostic; sample propagation is Phase 3 on the measurement record path).

dependency-free: stdlib typing only (no infrastructure / pyvisa / openpyxl /
pandas / PySide6 / fastapi / sqlalchemy / psycopg imports).
"""
from __future__ import annotations

from typing import Mapping, Optional, Protocol, runtime_checkable


__all__ = [
    'CentralProjectError',
    'CentralProjectReadPort',
    'CentralProjectWritePort',
    'ProjectIdentifierConflictError',
]


class CentralProjectError(RuntimeError):
    """Raised when a central project read/write fails at the infrastructure
    level — loud (→ 503), never a silently-empty list (which would render the
    entry screen as "no projects" and mask a backend outage) or a silent
    no-op create (which would lose a just-created project)."""


class ProjectIdentifierConflictError(CentralProjectError):
    """A human-assigned UNIQUE project key (``project_code`` /
    ``management_number``) is already taken → **409**, not 503 (W3 백엔드).

    Subclasses :class:`CentralProjectError` on purpose: every existing
    ``except CentralProjectError`` site (adapter rollback, service race
    self-heal, route error boundary) keeps working unchanged. The platform error
    table lists this class BEFORE its superclass so the more specific 409 wins —
    that ordering is the whole mechanism, and it is sealed by a test.

    ``field`` is the conflicting COLUMN NAME (never the user-supplied value) so
    it can ride in the RFC 9457 ``params`` under the PII allow-list.

    ``PROBLEM_PARAM_FIELDS`` is how that ride is now declared. The route boundary
    used to name this class in a private isinstance chain; the declaration moves
    that knowledge onto the exception, which is the thing that actually knows what
    its structured context is. Wire output is unchanged — the same two keys.
    """

    #: RFC 9457 ``params`` this refusal publishes (⊆ ``PROBLEM_PARAM_ALLOWLIST``).
    PROBLEM_PARAM_FIELDS = ('field', 'resource')

    def __init__(self, field: str, resource: str, message: str = '') -> None:
        super().__init__(
            message or f'{resource} {field} is already taken'
        )
        self.field = field
        self.resource = resource


@runtime_checkable
class CentralProjectReadPort(Protocol):
    """Read the caller's projects + one project's detail from the central DB."""

    def list_projects(
        self,
        *,
        status: Optional[str] = None,
        q: Optional[str] = None,
        limit: Optional[int] = None,
        after: Optional[tuple] = None,
    ) -> list[dict]:
        """Return projects filtered by ``status`` (read-open — not membership-scoped).

        ``status`` is one of the sealed status domain values (``active`` /
        ``completed``); ``None`` or ``all`` returns every project.

        W3 백엔드 — directory scale. All three extra arguments are OPTIONAL and
        omitting all of them reproduces the pre-W3 unbounded read byte-identically:

        - ``q`` — case-insensitive substring match over the
          ``project_directory_query.PROJECT_SEARCH_COLUMNS`` SSOT (which always
          includes ``management_number``, the operator's primary lookup key).
          ``None`` ⇒ no search filter.
        - ``limit`` — page size. ``None`` ⇒ unbounded (no ``LIMIT``).
        - ``after`` — keyset cursor as a positional tuple aligned with
          ``PROJECT_DIRECTORY_ORDER_COLUMNS`` (``(created_at, id)``); rows strictly
          BEFORE it in the newest-first total order are returned. ``None`` ⇒ first
          page. OFFSET is never used (unstable + O(skip)).

        One row per project, joined to its 1:1 ``device_models`` model + a
        ``sample_count`` aggregate. Columns: ``project_id`` / ``project_code`` /
        ``model_name`` / ``customer`` / ``manufacturer`` / ``management_number``
        (PM-assigned, UNIQUE/nullable) / ``status`` (active|completed) /
        ``fcc_grantee_code`` / ``applicant_name`` / ``applicant_address`` /
        ``eut_description`` / ``test_standard`` (성적서 표지 메타, all nullable) /
        ``sample_count`` / ``created_at`` (keyset cursor 원천 — 응답 envelope 에는
        싣지 않는다: 실으면 ``limit``/``cursor`` 미전달 응답이 기존과 달라져
        계약 S11(byte-identical)이 깨진다).
        ``fcc_id`` is NOT a column — the service derives it
        (``fcc_grantee_code`` + product_code(``model_name``)).
        ``subject`` None ⇒ unscoped (all projects) — only reachable on the
        auth-disabled (allow-insecure) dev composition; production always binds
        a subject so a tester sees only their member projects.
        """
        ...

    def read_project_detail(self, project_id: str) -> Optional[dict]:
        """Return one project's detail, or ``None`` when no such project.

        ``{project_id, project_code, model_name, customer, manufacturer,
        management_number, status, fcc_grantee_code, applicant_name,
        applicant_address, eut_description, test_standard, created_at,
        samples: [{sample_id, sample_code, serial_number, model_id,
        sample_number, test_category, label_number, smsn, intake_cert,
        assigned_team, sender, receiver, received_date, released_date}, ...]}``.
        ``management_number`` is PM-assigned (UNIQUE/nullable); ``status`` is
        active|completed. The 성적서 표지 메타 (fcc_grantee_code/applicant_name/
        applicant_address/eut_description/test_standard) are all nullable text.
        Each sample carries PM 칸 인벤토리 메타 (sample_number/test_category/
        label_number/smsn/intake_cert/assigned_team/sender/receiver/
        received_date/released_date) — all nullable text (Phase C).
        ``fcc_id`` is derived by the service (not a stored column).
        ``None`` ⇒ unknown project (the service maps it to a
        404, distinct from a 503 backend failure which raises
        :class:`CentralProjectError`).
        """
        ...


@runtime_checkable
class CentralProjectWritePort(Protocol):
    """Find a project by code + create the (project + device_model) pair."""

    def find_project_by_code(self, project_code: str) -> Optional[dict]:
        """Return ``{project_id, project_code}`` for an existing project whose
        ``project_code`` matches, else ``None`` (ADR-0017 D1 same-model reuse
        gate — a match short-circuits creation)."""
        ...

    def create_project_with_model(
        self, project_record: Mapping, device_model_record: Mapping,
    ) -> dict:
        """Insert one ``projects`` row + one ``device_models`` row in a SINGLE
        transaction and return the created project row
        (``{project_id, project_code, ...}``).

        Atomic: a failure inserting the device_model rolls the project insert
        back (no orphan project without its 1:1 model). ``device_model_record``
        carries the ``project_id`` foreign key (= the created project's id).
        """
        ...

    def update_project_status(
        self, project_id: str, status: str, updated_at: str,
    ) -> Optional[dict]:
        """Set a project's lifecycle status (the sealed ``active``/``completed``
        domain). Return ``{project_id, status}`` when the row exists, else
        ``None`` (unknown ``project_id`` → the service raises a 404)."""
        ...

    def update_project_metadata(
        self,
        project_id: str,
        updates: Mapping[str, Optional[str]],
        updated_at: str,
    ) -> Optional[dict]:
        """Partially update the 성적서 표지 메타 of one project (W3 백엔드).

        ``updates`` carries **only the fields the client actually sent**
        (``project_metadata_edit.parse_project_metadata_update`` SSOT): a key that
        is absent stays unchanged, a key mapped to ``None`` is cleared. The 8
        editable fields straddle two tables (``manufacturer`` lives on
        ``device_models``, the other 7 on ``projects``), so an implementation MUST
        write both in a SINGLE transaction — a partially applied edit would leave
        the model's manufacturer disagreeing with the project's cover metadata.

        ``status`` / ``model_name`` / ``project_code`` are never reachable here:
        status transitions own their action sub-resources and the model name is
        the project identity (re-keying, ADR-0005).

        Return ``{project_id}`` when the row exists, else ``None`` (unknown
        ``project_id`` → the service raises a 404, distinct from a 503 backend
        failure which raises :class:`CentralProjectError`).
        """
        ...
