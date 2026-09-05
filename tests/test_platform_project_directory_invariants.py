"""W3 백엔드 — 프로젝트 메타 편집 + 디렉터리 스케일 봉인.

계약: ``.claude/contracts/w3-be-project-directory.md`` (M1~M10 / S1~S16).

**M-1 (메타 편집 write 경로)** 분:

- S1 — PATCH 로 성적서 메타 8필드가 갱신되고, 요청에 **없는 키는 무변경**이며
  명시적 ``null`` 은 **삭제**된다. 두 경우가 ``None`` 하나로 뭉개지지 않는지가
  이 봉인의 핵심 — 어느 층(스키마/파서/SQL)에서든 뭉개지면 계약이 깨진다.
- S2 — PATCH 에 ``status`` / ``model_name`` 을 실으면 **loud 400** 이고 상태·정체성이
  변하지 않는다(조용한 반영 0, 조용한 무시 0).
- S3 — 상태 전이 SSOT 가 여전히 ``complete``/``reopen`` 단일 경로다 (PATCH 경유 전이 0).
- S4 — PATCH 인가가 ``platform:admin`` 으로 ``complete_project`` 와 동일하고, 신규
  grantable 토큰이 0이라 RBAC bijection 이 무변경이다.
- D-6 (계획) — 8필드가 두 테이블에 걸쳐 있으므로(``manufacturer`` 만
  ``device_models``) 두 UPDATE 가 **하나의 트랜잭션**에서 돈다(부분 성공 0).
- S16 — 도메인 정책이 순수(stdlib only)하고 어댑터가 ``%s`` paramstyle 을 지킨다.

M-2/M-3/M-4 (409 · 검색/keyset · 인덱스) 봉인은 후속 마일스톤에서 같은 파일에 붙는다.

Owned by ``/verify-platform-project-entry``.
"""
from __future__ import annotations
from tests._moved_module_source import moved_module_source
from tests._layer_of_import import imported_layers  # noqa: E402

import ast
import json
import re
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fcc_test_contracts.common.tree_artifacts import (
    resolve_dependency_artifact,
    resolve_repo_artifact,
)  # noqa: E402
from fcc_test_contracts.common.sqlite_connection_factory import SqliteConnectionFactory  # noqa: E402
from support.central_pg_sqlite_shim import (  # noqa: E402
    AdoptedQmarkConnection,
    QmarkCursor,
)

from fcc_test_kernel.application.central_contract.api_contracts import (  # noqa: E402
    PLATFORM_API_OPERATION_QUERY,
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_PERMISSIONS,
    PLATFORM_API_RESPONSE_HEADERS,
    PLATFORM_API_ROUTES,
    PLATFORM_API_SCHEMAS,
    PLATFORM_NEXT_CURSOR_HEADER,
)
from fcc_test_platform.application.central_project_service import (  # noqa: E402
    CentralProjectService,
    ProjectNotFoundError,
)
from fcc_test_platform.application.central_project_write_adapter import (  # noqa: E402
    PostgresCentralProjectWriteAdapter,
    build_device_model_metadata_update_sql,
    build_project_metadata_update_sql,
)
from fcc_test_contracts.common.api_error_codes import (  # noqa: E402
    ERROR_CODE_STATUS,
    ERROR_CODE_SURFACE_SCOPE,
    ERROR_CODE_TITLES,
    PROBLEM_PARAM_ALLOWLIST,
    SHARED_ERROR_CODES,
    ApiSurface,
    ErrorCode,
    surface_error_codes,
)
from fcc_test_platform.application.central_project_read_adapter import (  # noqa: E402
    PROJECT_LIST_COLUMNS,
    PROJECT_LIST_SQL,
    PROJECT_LIST_SQL_BY_STATUS,
    PROJECT_LIST_SQL_VARIANTS,
)
from fcc_test_kernel.application.central_contract.pagination import (  # noqa: E402
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    CursorError,
    encode_cursor,
)
from fcc_test_platform.domain.ports.output.central_project_port import (  # noqa: E402
    CentralProjectError,
    CentralProjectReadPort,
    CentralProjectWritePort,
    ProjectIdentifierConflictError,
)
from fcc_test_platform.domain.services.project_identifier_conflict import (  # noqa: E402
    PROJECT_CONFLICT_RESOURCE,
    PROJECT_UNIQUE_CONSTRAINTS,
    UNIQUE_VIOLATION_SQLSTATE,
    classify_project_unique_violation,
)
from fcc_test_platform.domain.services.project_directory_query import (  # noqa: E402
    PROJECT_DIRECTORY_CURSOR_FIELDS,
    PROJECT_DIRECTORY_LEGACY_ORDER_COLUMNS,
    PROJECT_DIRECTORY_ORDER_COLUMNS,
    PROJECT_SEARCH_COLUMNS,
    SEARCH_LIKE_ESCAPE_CHAR,
    directory_order_columns,
    normalize_search_term,
    search_like_pattern,
)
from fcc_test_kernel.domain.services.project_metadata_edit import (  # noqa: E402
    APPLICANT_IDENTITY_FIELD,
    APPLICANT_SUGGESTION_FIELDS,
    CREATE_PROJECT_IDENTITY_FIELD,
    CREATE_PROJECT_REQUIRED_FIELDS,
    DEVICE_MODEL_META_FIELDS,
    EDITABLE_PROJECT_META_FIELDS,
    IMMUTABLE_PROJECT_FIELDS,
    PROJECT_META_FIELD_TABLES,
    PROJECT_TABLE_META_FIELDS,
    UNIQUE_PROJECT_META_FIELDS,
    device_model_table_updates,
    parse_project_metadata_update,
    project_table_updates,
)

_DOMAIN_POLICY_MODULE = (
    moved_module_source('fcc_test_kernel.domain.services.project_metadata_edit')
)
_WRITE_ADAPTER_MODULE = moved_module_source(
    'fcc_test_platform.application.central_project_write_adapter')
# ⚠️ **경로가 아니라 모듈에게 묻는다** (2026-09-03, 커널 3단계).
# 이 둘은 중앙 전용이라 `fcc_test_platform.domain.services.*` 로 갔다.
# 경로를 적으면 다음 이관에서 또 낡는다 — `tests/_moved_module_source.py`.
_DIRECTORY_POLICY_MODULE = moved_module_source(
    'fcc_test_platform.domain.services.project_directory_query')
_CONFLICT_POLICY_MODULE = moved_module_source(
    'fcc_test_platform.domain.services.project_identifier_conflict')
_CENTRAL_SCHEMA = PROJECT_ROOT / 'docs' / 'platform' / 'central_db_schema.v1.json'
_MIGRATION_010 = (
    resolve_repo_artifact(__file__, 'docs/platform/migrations/010_project_directory_indexes.sql')
)
_MIGRATION_031 = (
    resolve_repo_artifact(
        __file__, 'docs/platform/migrations/031_project_applicant_search_axis.sql')
)
_MIGRATION_032 = (
    resolve_repo_artifact(
        __file__, 'docs/platform/migrations/032_retire_project_customer_column.sql')
)

#: The three published OpenAPI artifacts, per surface.
#:
#: ``resolve_dependency_artifact`` rather than ``resolve_repo_artifact``: all three
#: are owned by the contracts lane, and only ``platform-api.openapi.json`` is
#: delivered into the platform box at all. Asking this box where they are gets an
#: honest "not here" for two of them; asking the tree that ships them gets the
#: answer (``artifacts/`` at that box's root).
#:
#: ⚠️ **Spelled out one literal path at a time, deliberately.** The obvious form is
#: a loop over basenames with ``f'docs/api/{name}'``, and it costs exactly what
#: ``delivered_artifact_path_dynamic_baseline`` exists to measure: a computed
#: argument is one the artifact-path axis cannot read, so all three sites would
#: leave judgement and the axis would report a smaller, healthier-looking number
#: for having gone blind. Measured 2026-08-27 while writing this — the loop form
#: moved the platform lane's dynamic count from 4 to 5 and its readable count from
#: 124 to 123. Three literals keep three sites judged.
_SURFACE_OPENAPI_ARTIFACTS = (
    (ApiSurface.HEADLESS, resolve_dependency_artifact('docs/api/headless-api.openapi.json')),
    (ApiSurface.PLATFORM, resolve_dependency_artifact('docs/api/platform-api.openapi.json')),
    (ApiSurface.SESSION, resolve_dependency_artifact('docs/api/session-api.openapi.json')),
)


def _cursor_fields(*, paginated: bool) -> tuple[str, ...]:
    """Read-row keys for the order axis in force (positional mirror of
    ``directory_order_columns``). The legacy axis is a **prefix** of the total
    order, which is what lets one slice cover both — sealed by
    ``TestProjectDirectoryOrderAxisSsot``."""
    return PROJECT_DIRECTORY_CURSOR_FIELDS[
        :len(directory_order_columns(paginated=paginated))
    ]


def _sort_key(row, fields: tuple[str, ...] = PROJECT_DIRECTORY_CURSOR_FIELDS) -> tuple:
    """Newest-first sort key over the given cursor fields (string-compared, like
    the opaque cursor's own encoding)."""
    return tuple(str(row.get(field) or '') for field in fields)


# ── in-memory fakes (mirror of test_platform_project_entry.py idioms) ───────


class _InMemoryCentral:
    """projects/device_models 를 흉내내되 **소속 테이블 분리를 보존**한다 —
    한 dict 에 8필드를 뭉뚱그리면 D-6(두 테이블 단일 트랜잭션)을 검증할 수 없다."""

    def __init__(self) -> None:
        self.projects: dict[str, dict] = {}     # project_id -> projects row
        self.models: dict[str, dict] = {}       # project_id -> device_models row
        self.code_index: dict[str, str] = {}
        self.memberships: list[dict] = []
        self.audit_events: list[dict] = []
        self.metadata_writes: list[tuple] = []  # (project_id, updates, updated_at)


# ``check_same_thread=False``: the fake is exercised from the TestClient's
# worker thread as well as the test thread.
_LIKE_CONN = SqliteConnectionFactory(
    ':memory:', check_same_thread=False,
).create()


def _like(value, pattern: str) -> bool:
    """Evaluate ``value LIKE pattern ESCAPE '\\'`` with SQLite's real LIKE engine.

    The fake deliberately does NOT re-implement LIKE in Python: a hand-rolled
    matcher would be a second (and subtly different) semantics, so a bug in the
    escaping policy could cancel out against a matching bug in the fake.
    """
    if value is None:
        return False
    row = _LIKE_CONN.execute(
        "SELECT LOWER(?) LIKE ? ESCAPE '\\'", (str(value), pattern),
    ).fetchone()
    return bool(row[0])


class _FakeProjectReadPort:
    def __init__(self, central: _InMemoryCentral) -> None:
        self._c = central
        #: 서비스가 어댑터에 실제로 넘긴 인자 기록 (SSOT 위임 검증용).
        self.calls: list[dict] = []
        self.applicant_calls: list[dict] = []

    def list_projects(self, *, status=None, q=None, limit=None, after=None):
        """Faithful in-memory mirror of the read adapter's directory contract.

        Honours status / search / keyset / limit for real — a fake that accepted
        the arguments and ignored them would let a broken pagination service pass.
        """
        self.calls.append(
            {'status': status, 'q': q, 'limit': limit, 'after': after}
        )
        rows = [self._row(pid) for pid in self._c.projects]
        if status and status != 'all':
            rows = [row for row in rows if row.get('status') == status]
        if q is not None:
            rows = [
                row for row in rows
                if any(_like(row.get(column), q) for column in PROJECT_SEARCH_COLUMNS)
            ]
        # Order axis mirrors the adapter: total order only when this call
        # returns a PAGE (limit/cursor). Without a page boundary the fake must
        # not impose the tie-breaker either, or it would hide an S11 break.
        order_fields = _cursor_fields(
            paginated=limit is not None or after is not None,
        )
        rows.sort(key=lambda row: _sort_key(row, order_fields), reverse=True)
        if after is not None:
            boundary = tuple(str(value) for value in after)
            rows = [row for row in rows if _sort_key(row) < boundary]
        if limit is not None:
            rows = rows[:limit]
        return rows

    def read_project_detail(self, project_id):
        if project_id not in self._c.projects:
            return None
        row = self._row(project_id)
        row['samples'] = []
        return row

    def list_applicant_suggestions(self, *, q=None, limit):
        """신청자당 최신 한 행 — 어댑터 윈도우 함수와 같은 판정의 in-memory 거울.

        실제 SQL 의 그룹 키는 정규화된 이름(``lower``)이고 최신이 이긴다. 여기서도
        같은 규칙을 지킨다 — 인자를 받고 무시하는 fake 는 깨진 서비스를 통과시킨다.
        """
        self.applicant_calls.append({'q': q, 'limit': limit})
        newest: dict[str, dict] = {}
        counts: dict[str, int] = {}
        for pid in self._c.projects:
            row = self._row(pid)
            name = (row.get(APPLICANT_IDENTITY_FIELD) or '').strip()
            if not name:
                continue
            if q is not None and not _like(name, q):
                continue
            key = name.lower()
            counts[key] = counts.get(key, 0) + 1
            previous = newest.get(key)
            if previous is None or _sort_key(row) > _sort_key(previous):
                newest[key] = row
        ranked = sorted(newest.items(), key=lambda kv: _sort_key(kv[1]), reverse=True)
        return [
            {
                **{field: row.get(field) for field in APPLICANT_SUGGESTION_FIELDS},
                'project_count': counts[key],
            }
            for key, row in ranked[:limit]
        ]

    def _row(self, project_id: str) -> dict:
        project = self._c.projects[project_id]
        model = self._c.models.get(project_id, {})
        row = {
            'project_id': project_id,
            'project_code': project.get('project_code'),
            'model_name': model.get('model_name'),
            'status': project.get('status'),
            'created_at': project.get('created_at'),
            'sample_count': 0,
        }
        # 8필드는 각자의 소속 테이블에서 읽는다(합쳐진 사본 없음).
        for field in EDITABLE_PROJECT_META_FIELDS:
            source = model if field in DEVICE_MODEL_META_FIELDS else project
            row[field] = source.get(field)
        return row


class _FakeProjectWritePort:
    def __init__(self, central: _InMemoryCentral) -> None:
        self._c = central

    def find_project_by_code(self, project_code):
        pid = self._c.code_index.get(project_code)
        return None if pid is None else {'project_id': pid, 'project_code': project_code}

    def create_project_with_model(self, project_record, device_model_record):
        pid = project_record['id']
        # 실제 어댑터에서는 ux_projects_management_number 가 이 검사를 한다. Fake 가
        # UNIQUE 를 무시하면 "중복 관리번호가 409 로 나온다"는 봉인이 성립하지 않는다.
        management_number = project_record.get('management_number')
        if management_number is not None and any(
            row.get('management_number') == management_number
            for row in self._c.projects.values()
        ):
            raise ProjectIdentifierConflictError(
                'management_number', PROJECT_CONFLICT_RESOURCE,
            )
        self._c.projects[pid] = {
            key: project_record.get(key)
            # 메타 칸은 소속 테이블 SSOT 파생 — 페이크가 목록을 손으로 들면 필드가
            # 늘어난 날 이 페이크만 옛 모양을 저장하고, 테스트는 "서비스가 값을
            # 잃어버린다"고 잘못 말한다. manufacturer 가 여기 없는 것도 파생 결과다
            # (그 칸은 device_models 소속이고, 아래 models 딕트가 받는다).
            for key in ('project_code', 'name', 'status')
            + PROJECT_TABLE_META_FIELDS
            + ('created_at', 'updated_at')
        }
        self._c.models[pid] = {
            'model_name': device_model_record.get('model_name'),
            'manufacturer': device_model_record.get('manufacturer'),
            'updated_at': device_model_record.get('updated_at'),
        }
        self._c.code_index[project_record['project_code']] = pid
        return {'project_id': pid, 'project_code': project_record['project_code']}

    def update_project_status(self, project_id, status, updated_at):
        project = self._c.projects.get(project_id)
        if project is None:
            return None
        project['status'] = status
        return {'project_id': project_id, 'status': status}

    def update_project_metadata(self, project_id, updates, updated_at):
        self._c.metadata_writes.append((project_id, dict(updates), updated_at))
        project = self._c.projects.get(project_id)
        if project is None:
            return None
        # ux_projects_management_number — 편집으로 남의 관리번호를 밟는 경우도
        # create 와 같은 409 여야 한다(한쪽만 고치면 결함이 다른 표면에 남는다).
        management_number = updates.get('management_number')
        if management_number is not None and any(
            pid != project_id and row.get('management_number') == management_number
            for pid, row in self._c.projects.items()
        ):
            raise ProjectIdentifierConflictError(
                'management_number', PROJECT_CONFLICT_RESOURCE,
            )
        project.update(project_table_updates(updates))
        project['updated_at'] = updated_at
        model_updates = device_model_table_updates(updates)
        if model_updates:
            self._c.models[project_id].update(model_updates)
            self._c.models[project_id]['updated_at'] = updated_at
        return {'project_id': project_id}

    def create_project_with_model_and_admin_grant(
        self, project_record, device_model_record, user_record,
        membership_record, audit_record,
    ):
        result = self.create_project_with_model(project_record, device_model_record)
        materialized = dict(membership_record)
        materialized['user_id'] = user_record['id']
        self._c.memberships.append(materialized)
        self._c.audit_events.append(dict(audit_record))
        return result


class _FakeMembershipService:
    def __init__(self) -> None:
        self.assigns: list[dict] = []

    def assign(self, project_id, *, user_subject, role_key, actor_subject,
               user_issuer='', expires_at=None):
        self.assigns.append({'project_id': project_id, 'role_key': role_key})
        return {'project_id': project_id, 'role_key': role_key}


_PROJECT_ID = '11111111-1111-4111-8111-111111111111'


def _make_service():
    central = _InMemoryCentral()
    # 결정적이면서 **고갈되지 않는** id 열. 유한 리스트였을 때는 프로젝트를 셋 이상
    # 만드는 테스트가 StopIteration 으로 죽었는데, 그 실패는 "id 가 모자랐다"가 아니라
    # 무관한 예외로 보여서 원인을 가린다. 첫 값은 _PROJECT_ID 로 고정한다 — 기존
    # 테스트들이 그 id 로 상세/PATCH 를 부른다.
    #
    # 한 프로젝트 생성이 id 를 넷 소비한다(project · device_model · membership · audit).
    def _id_sequence():
        yield _PROJECT_ID
        for index in range(2, 1000):
            digit = index % 10 or 1
            block = str(digit) * 4
            yield f'{block}{block[:4]}-{block}-4{block[:3]}-8{block[:3]}-{block * 3}'

    ids = _id_sequence()
    # 생성 순서가 곧 최신 순서여야 신청자 제안("마지막에 쓴 값이 기본값")을 시험할 수
    # 있다. 초 단위로 전진시키되, 기존 테스트가 기대하는 첫 시각은 그대로 둔다.
    def _clock_sequence():
        for second in range(0, 60):
            yield f'2026-07-28T00:00:{second:02d}+00:00'
        while True:
            yield '2026-07-28T00:01:00+00:00'

    clock = _clock_sequence()
    service = CentralProjectService(
        _FakeProjectReadPort(central), _FakeProjectWritePort(central),
        _FakeMembershipService(),
        clock=lambda: next(clock, '2026-07-28T09:99:99+00:00'),
        id_factory=lambda: next(ids),
    )
    return service, central


def _create_body(model_name: str, **overrides) -> dict:
    """생성 요청 본문 — 필수 칸이 채워진 최소 형태(필수 집합은 도메인 SSOT)."""
    body = {
        'model_name': model_name,
        'management_number': f'MGMT-{model_name}',
        'applicant_name': 'ACME Corp',
    }
    body.update(overrides)
    return {key: value for key, value in body.items() if value is not None}


assert set(CREATE_PROJECT_REQUIRED_FIELDS) <= set(_create_body('X'))


def _seed_project(service, **meta):
    """편집 가능한 **모든** 메타 칸이 채워진 프로젝트를 하나 만든다.

    아래 PATCH 테스트들이 "보내지 않은 칸은 그대로"를 검사하려면 사전값이 전부
    non-null 이어야 한다 — 그래서 이 시드는 도메인 편집 필드 집합을 순회해 채운다.
    값이 필드마다 달라야 "다른 칸이 섞여 들어왔다"를 구분할 수 있으므로, 필드명을
    값에 넣는다.
    """
    body = {
        field: f'seed-{field}'
        for field in EDITABLE_PROJECT_META_FIELDS
    }
    body['model_name'] = meta.pop('model_name', 'SM-X100')
    # 유일 제약이 걸린 칸은 **모델명에서 파생**한다. 상수로 두면 두 번째 프로젝트를
    # 시드하는 순간 의도치 않은 409 가 나고, 그 실패는 테스트가 시험하려던 것과
    # 아무 상관이 없다.
    for field in UNIQUE_PROJECT_META_FIELDS:
        body[field] = f'{field}-{body["model_name"]}'
    body.update(meta)
    actor = body.pop('actor_subject', 'tester@example.com')
    return service.create_project(body, actor_subject=actor)


# ── S1 — 부분 갱신 semantics ────────────────────────────────────────────────


class TestProjectMetadataPatchRoundTrip(unittest.TestCase):
    """S1 — 8필드 왕복 / 키 부재 무변경 / 명시적 null 삭제."""

    def test_metadata_patch_round_trips_every_editable_field(self):
        service, _ = _make_service()
        _seed_project(service)
        # 8필드를 한 번에 전부 새 값으로 — 어느 하나라도 SET 절에서 누락되면 실패.
        new_values = {
            field: f'{field}-updated' for field in EDITABLE_PROJECT_META_FIELDS
        }
        detail = service.update_project_metadata(_PROJECT_ID, dict(new_values))
        for field, value in new_values.items():
            self.assertEqual(detail[field], value, field)
        # 재조회해도 같아야 한다(응답이 write 를 대변하는 게 아니라 실제 저장분).
        reread = service.get_project(_PROJECT_ID)
        for field, value in new_values.items():
            self.assertEqual(reread[field], value, field)

    def test_metadata_patch_absent_key_is_unchanged(self):
        service, _ = _make_service()
        before = _seed_project(service)
        detail = service.update_project_metadata(
            _PROJECT_ID, {'applicant_address': 'NEW'},
        )
        self.assertEqual(detail['applicant_address'], 'NEW')
        # 보내지 않은 나머지 칸은 전부 사전값 그대로.
        for field in EDITABLE_PROJECT_META_FIELDS:
            if field == 'applicant_address':
                continue
            self.assertEqual(detail[field], before[field], field)

    def test_metadata_patch_explicit_null_clears_the_field(self):
        service, _ = _make_service()
        _seed_project(service)
        detail = service.update_project_metadata(
            _PROJECT_ID, {'management_number': None},
        )
        self.assertIsNone(detail['management_number'])
        # 삭제는 다른 필드를 건드리지 않는다.
        self.assertEqual(detail['applicant_address'], 'seed-applicant_address')

    def test_absent_key_and_explicit_null_are_distinguishable_at_the_parser(self):
        # 계약의 핵심 — 파서가 두 경우를 구분하지 못하면 위 두 테스트가 통과해도
        # 상위 층에서 뭉개진다. 부재 키는 결과에 아예 나타나지 않아야 한다.
        self.assertNotIn(
            'applicant_address',
            parse_project_metadata_update({'manufacturer': 'X'}),
        )
        self.assertEqual(
            parse_project_metadata_update({'applicant_address': None}),
            {'applicant_address': None},
        )

    def test_blank_string_clears_like_the_create_path(self):
        # create 경로(_opt_text)와 같은 규약 — 공백 문자열은 None(삭제).
        self.assertEqual(
            parse_project_metadata_update({'applicant_address': '   '}),
            {'applicant_address': None},
        )
        self.assertEqual(
            parse_project_metadata_update({'applicant_address': ' 1 Road '}),
            {'applicant_address': '1 Road'},
        )

    def test_manufacturer_edit_reaches_the_device_models_row(self):
        # D-6 — manufacturer 만 다른 테이블. projects 만 갱신하면 조용히 유실된다.
        service, central = _make_service()
        _seed_project(service)
        detail = service.update_project_metadata(_PROJECT_ID, {'manufacturer': 'LG'})
        self.assertEqual(detail['manufacturer'], 'LG')
        self.assertEqual(central.models[_PROJECT_ID]['manufacturer'], 'LG')
        self.assertNotIn('manufacturer', central.projects[_PROJECT_ID])

    def test_unknown_project_raises_not_found(self):
        service, _ = _make_service()
        with self.assertRaises(ProjectNotFoundError):
            service.update_project_metadata(
                '99999999-9999-4999-8999-999999999999', {'applicant_address': 'X'},
            )

    def test_malformed_project_id_raises_value_error(self):
        service, _ = _make_service()
        with self.assertRaises(ValueError):
            service.update_project_metadata('not-a-uuid', {'applicant_address': 'X'})

    def test_empty_update_is_rejected_loudly(self):
        # 조용한 no-op 금지 — 빈 PATCH 는 클라이언트 오류로 드러난다.
        service, _ = _make_service()
        _seed_project(service)
        for body in ({}, None):
            with self.assertRaises(ValueError):
                service.update_project_metadata(_PROJECT_ID, body)


# ── S2 · S3 — 범위 밖 필드 loud 거부 ────────────────────────────────────────


class TestProjectMetadataPatchRejectsOutOfScope(unittest.TestCase):
    def test_status_and_model_name_are_rejected_and_state_is_unchanged(self):
        service, _ = _make_service()
        before = _seed_project(service)
        for body in (
            {'status': 'completed'},
            {'model_name': 'OTHER'},
            {'project_code': 'OTHER'},
            {'applicant_address': 'NEW', 'status': 'completed'},  # 유효 필드와 섞어도 거부
        ):
            with self.assertRaises(ValueError, msg=body):
                service.update_project_metadata(_PROJECT_ID, body)
        after = service.get_project(_PROJECT_ID)
        # 상태·정체성·유효 필드까지 전부 사전값 (부분 반영 0).
        self.assertEqual(after['status'], before['status'])
        self.assertEqual(after['model_name'], before['model_name'])
        self.assertEqual(after['project_code'], before['project_code'])
        self.assertEqual(after['applicant_address'], before['applicant_address'])

    def test_unknown_field_is_rejected(self):
        service, _ = _make_service()
        _seed_project(service)
        with self.assertRaises(ValueError):
            service.update_project_metadata(_PROJECT_ID, {'nope': 'x'})

    def test_status_transition_ssot_is_still_the_action_sub_resources(self):
        # S3 — PATCH 라우트가 상태를 전이시키지 않고, complete/reopen 만이 전이시킨다.
        service, _ = _make_service()
        _seed_project(service)
        self.assertEqual(service.complete_project(_PROJECT_ID)['status'], 'completed')
        self.assertEqual(service.reopen_project(_PROJECT_ID)['status'], 'active')
        self.assertNotIn('status', EDITABLE_PROJECT_META_FIELDS)
        self.assertIn('status', IMMUTABLE_PROJECT_FIELDS)
        self.assertEqual(
            PLATFORM_API_ROUTES['complete_project'],
            ('POST', '/platform/projects/{project_id}/complete'),
        )


# ── S4 — 인가 미러링 (신규 grantable 토큰 0) ────────────────────────────────


class TestProjectMetadataPatchAuthorization(unittest.TestCase):
    def test_update_project_permission_mirrors_complete_project(self):
        self.assertEqual(
            PLATFORM_API_PERMISSIONS['update_project'],
            PLATFORM_API_PERMISSIONS['complete_project'],
        )

    def test_no_new_grantable_permission_token_is_introduced(self):
        # bijection 무변경의 필요조건 — update_project 가 기존 토큰 집합 안에 있다.
        others = {
            token for name, token in PLATFORM_API_PERMISSIONS.items()
            if name != 'update_project'
        }
        self.assertIn(PLATFORM_API_PERMISSIONS['update_project'], others)

    def test_route_is_patch_on_the_detail_path(self):
        self.assertEqual(
            PLATFORM_API_ROUTES['update_project'],
            ('PATCH', PLATFORM_API_ROUTES['get_project'][1]),
        )

    def test_operation_declares_request_schema_and_404(self):
        operation = PLATFORM_API_OPERATIONS['update_project']
        self.assertEqual(operation['request'], 'UpdateProjectRequest')
        self.assertEqual(operation['response'], 'ProjectDetailEnvelope')
        self.assertIn('404', operation.get('error_responses', {}))


# ── SSOT 파생 (하드코딩 금지) ───────────────────────────────────────────────


class TestProjectMetadataFieldSsot(unittest.TestCase):
    def test_request_schema_properties_derive_from_the_policy_ssot(self):
        schema = PLATFORM_API_SCHEMAS['UpdateProjectRequest']
        self.assertEqual(
            tuple(schema['properties']), EDITABLE_PROJECT_META_FIELDS,
        )
        self.assertFalse(schema['additionalProperties'])
        self.assertNotIn('required', schema)  # 보낸 키만 갱신 — 필수 필드 없음
        for spec in schema['properties'].values():
            self.assertTrue(spec['nullable'])  # null = 삭제를 표현해야 한다

    def test_editable_fields_match_the_create_request_meta_fields(self):
        """생성 시 쓸 수 있는 것은 수정도 할 수 있어야 한다(비대칭이 곧 D1 결함).

        2026-09-04 — 대칭의 기준이 "optional 집합"에서 "**메타 칸 집합**"으로 바뀌었다.
        관리번호·신청자가 생성 필수가 되면서 그 둘이 create 의 required 로 옮겨갔는데,
        옛 형태의 이 검사는 그것을 "편집 필드에서 빠졌다"고 잘못 읽는다. 필수 여부는
        *생성 시점의 요구*이고 편집 가능 여부는 *언제든 고칠 수 있는가*라, 두 축은
        애초에 다른 것을 말한다 — 실제로 관리번호는 필수이면서 동시에 수정 가능해야
        한다(오타를 되돌릴 길이 없으면 그게 더 큰 결함이다).
        """
        create_meta = set(PLATFORM_API_SCHEMAS['CreateProjectRequest']['properties']) - {
            CREATE_PROJECT_IDENTITY_FIELD,
        }
        self.assertEqual(create_meta, set(EDITABLE_PROJECT_META_FIELDS))

    def test_every_required_create_field_is_still_editable_afterwards(self):
        """필수 칸이 **편집 불가**가 되면 오타를 되돌릴 길이 사라진다.

        정체성 필드(``model_name``)만 예외다 — 그것은 재키잉 문제이지 편집이 아니다.
        """
        for field in CREATE_PROJECT_REQUIRED_FIELDS:
            if field == CREATE_PROJECT_IDENTITY_FIELD:
                continue
            with self.subTest(field=field):
                self.assertIn(field, EDITABLE_PROJECT_META_FIELDS)

    def test_required_create_fields_are_not_nullable_in_the_contract(self):
        """필수 칸을 nullable 로 선언하면 계약이 서버와 다른 말을 한다.

        도메인 파서는 공백 문자열을 ``None`` 으로 정규화한 뒤 필수 위반으로 거절하므로,
        스키마도 ``minLength: 1`` 로 같은 것을 말해야 한다.
        """
        schema = PLATFORM_API_SCHEMAS['CreateProjectRequest']
        self.assertEqual(
            set(schema['required']), set(CREATE_PROJECT_REQUIRED_FIELDS),
        )
        for field in CREATE_PROJECT_REQUIRED_FIELDS:
            with self.subTest(field=field):
                spec = schema['properties'][field]
                self.assertNotIn('null', str(spec.get('type', '')))
                self.assertEqual(spec.get('minLength'), 1)

    def test_table_split_partitions_every_editable_field(self):
        self.assertEqual(
            set(PROJECT_TABLE_META_FIELDS) | set(DEVICE_MODEL_META_FIELDS),
            set(EDITABLE_PROJECT_META_FIELDS),
        )
        self.assertEqual(
            set(PROJECT_TABLE_META_FIELDS) & set(DEVICE_MODEL_META_FIELDS), set(),
        )
        self.assertEqual(DEVICE_MODEL_META_FIELDS, ('manufacturer',))

    def test_table_split_matches_the_central_schema_ssot(self):
        # 스키마 JSON 이 권위 — 컬럼이 옮겨가면 정책 맵이 그 자리에서 깨진다.
        import json

        schema = json.loads(
            (PROJECT_ROOT / 'docs' / 'platform' / 'central_db_schema.v1.json')
            .read_text(encoding='utf-8')
        )
        for field, table in PROJECT_META_FIELD_TABLES.items():
            self.assertIn(
                field, schema['tables'][table]['columns'],
                f'{field} is declared on {table} by the policy but not by the schema',
            )


# ── S16 — 계층 순수성 + paramstyle ──────────────────────────────────────────


class TestProjectMetadataEditPurity(unittest.TestCase):
    def test_domain_policy_imports_stdlib_only(self):
        tree = ast.parse(_DOMAIN_POLICY_MODULE.read_text(encoding='utf-8'))
        forbidden = (
            'infrastructure', 'application', 'psycopg', 'sqlalchemy', 'openpyxl',
            'pandas', 'PySide6', 'fastapi',
        )
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        for module in modules:
            root = module.split('.')[0]
            self.assertNotIn(root, forbidden, module)

    def test_fakes_satisfy_the_runtime_checkable_ports(self):
        central = _InMemoryCentral()
        self.assertIsInstance(_FakeProjectReadPort(central), CentralProjectReadPort)
        self.assertIsInstance(_FakeProjectWritePort(central), CentralProjectWritePort)

    def test_real_adapter_satisfies_the_write_port(self):
        adapter = PostgresCentralProjectWriteAdapter(lambda: None)
        self.assertIsInstance(adapter, CentralProjectWritePort)

    def test_metadata_update_sql_binds_every_value(self):
        sql = build_project_metadata_update_sql(PROJECT_TABLE_META_FIELDS)
        # 컬럼당 %s 1개 + updated_at + WHERE id — 값 보간(f-string 값) 0.
        self.assertEqual(sql.count('%s'), len(PROJECT_TABLE_META_FIELDS) + 2)
        self.assertTrue(sql.startswith('UPDATE "projects" SET '))
        self.assertTrue(sql.endswith('WHERE "id" = %s RETURNING "id"'))
        model_sql = build_device_model_metadata_update_sql(DEVICE_MODEL_META_FIELDS)
        self.assertEqual(model_sql.count('%s'), len(DEVICE_MODEL_META_FIELDS) + 2)
        self.assertTrue(model_sql.endswith('WHERE "project_id" = %s RETURNING "id"'))


# ── D-6 — 두 테이블 단일 트랜잭션 (실 SQL 을 DDL 형상 SQLite 에 실행) ────────


class _RecordingCursor(QmarkCursor):
    """공유 shim 의 커서 + 실행된 문장 기록. 번역은 상속한다 — 사본이 아니라
    확장이어야 "우리가 시험한 정체"라는 논거가 이 자리에도 그대로 적용된다."""

    def __init__(self, raw, log) -> None:
        super().__init__(raw)
        self._log = log

    def execute(self, statement, params=()):
        self._log.append(statement)  # 번역 **이전**의 문장이 기록 대상이다
        super().execute(statement, params)


class _RecordingConnection(AdoptedQmarkConnection):
    """단일 SQLite 연결을 감싸 commit/rollback 횟수를 기록한다 — 두 UPDATE 가
    같은 트랜잭션 안에 있는지(중간 commit 0)를 구조적으로 판정하기 위함.

    연결은 테스트가 소유하므로 ``close`` 가 no-op 인 것은 :class:`AdoptedQmarkConnection`
    에서 상속한다 — 그 결정은 수명 문제이지 paramstyle 문제가 아니다."""

    def __init__(self, conn, log) -> None:
        super().__init__(conn)
        self._log = log
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _RecordingCursor(self._conn.cursor(), self._log)

    def commit(self):
        self.commits += 1
        super().commit()

    def rollback(self):
        self.rollbacks += 1
        super().rollback()


class TestProjectMetadataSqlAgainstDdl(unittest.TestCase):
    """실 어댑터 SQL 을 DDL 형상 테이블에 실행. Fake 로는 SQL 오류·트랜잭션
    경계·RETURNING 존재판정을 잡을 수 없다."""

    def setUp(self):
        self.conn = SqliteConnectionFactory(':memory:').create()
        self.conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY, project_code TEXT,
                management_number TEXT UNIQUE, status TEXT, fcc_grantee_code TEXT,
                applicant_name TEXT, applicant_address TEXT, eut_description TEXT,
                test_standard TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE device_models (
                id TEXT PRIMARY KEY, project_id TEXT, model_name TEXT,
                manufacturer TEXT, created_at TEXT,
                updated_at TEXT
            );
            INSERT INTO projects (id, project_code, applicant_address, management_number,
                                  status, created_at, updated_at)
                VALUES ('p-a','A','ACME','MGMT-1','active','t0','t0');
            INSERT INTO device_models (id, project_id, model_name, manufacturer,
                                       created_at, updated_at)
                VALUES ('m-a','p-a','A','Samsung','t0','t0');
            """
        )
        self.statements: list[str] = []
        self.connection = _RecordingConnection(self.conn, self.statements)
        self.adapter = PostgresCentralProjectWriteAdapter(lambda: self.connection)
        self.addCleanup(self.conn.close)

    def _read(self, table, column, key='p-a'):
        key_column = 'id' if table == 'projects' else 'project_id'
        return self.conn.execute(
            f'SELECT {column} FROM {table} WHERE {key_column}=?', (key,)
        ).fetchone()[0]

    def test_cross_table_update_commits_once(self):
        result = self.adapter.update_project_metadata(
            'p-a', {'applicant_address': 'NEW', 'manufacturer': 'LG'}, 't1',
        )
        self.assertEqual(result, {'project_id': 'p-a'})
        self.assertEqual(self._read('projects', 'applicant_address'), 'NEW')
        self.assertEqual(self._read('device_models', 'manufacturer'), 'LG')
        # 두 UPDATE, commit 1회 → 하나의 트랜잭션 (부분 성공 불가).
        updates = [s for s in self.statements if s.startswith('UPDATE')]
        self.assertEqual(len(updates), 2)
        self.assertEqual(self.connection.commits, 1)
        self.assertEqual(self.connection.rollbacks, 0)

    def test_updated_at_is_bumped_on_both_tables(self):
        self.adapter.update_project_metadata(
            'p-a', {'applicant_address': 'NEW', 'manufacturer': 'LG'}, 't1',
        )
        self.assertEqual(self._read('projects', 'updated_at'), 't1')
        self.assertEqual(self._read('device_models', 'updated_at'), 't1')

    def test_manufacturer_only_edit_still_touches_projects_updated_at(self):
        self.adapter.update_project_metadata('p-a', {'manufacturer': 'LG'}, 't1')
        self.assertEqual(self._read('device_models', 'manufacturer'), 'LG')
        self.assertEqual(self._read('projects', 'updated_at'), 't1')
        self.assertEqual(self._read('projects', 'applicant_address'), 'ACME')  # 무변경

    def test_projects_only_edit_does_not_touch_device_models(self):
        self.adapter.update_project_metadata('p-a', {'applicant_address': 'NEW'}, 't1')
        updates = [s for s in self.statements if s.startswith('UPDATE')]
        self.assertEqual(len(updates), 1)
        self.assertEqual(self._read('device_models', 'updated_at'), 't0')

    def test_explicit_null_clears_the_column(self):
        self.adapter.update_project_metadata('p-a', {'management_number': None}, 't1')
        self.assertIsNone(self._read('projects', 'management_number'))

    def test_orphan_project_without_model_row_fails_loudly(self):
        # ADR-0017 D1 위반(모델 행 없는 프로젝트)에 manufacturer 를 쓰면 0행 갱신인데
        # 200 을 답하면 **쓰지 않은 write 를 보고**하는 것 — loud 실패 + 전량 롤백.
        from fcc_test_platform.domain.ports.output.central_project_port import CentralProjectError

        self.conn.execute('DELETE FROM device_models WHERE project_id=?', ('p-a',))
        self.conn.commit()
        with self.assertRaises(CentralProjectError):
            self.adapter.update_project_metadata(
                'p-a', {'applicant_address': 'NEW', 'manufacturer': 'LG'}, 't1',
            )
        self.assertEqual(self.connection.rollbacks, 1)
        self.assertEqual(self.connection.commits, 0)
        self.assertEqual(self._read('projects', 'applicant_address'), 'ACME')  # 롤백됨

    def test_unknown_project_returns_none_and_writes_nothing(self):
        self.assertIsNone(
            self.adapter.update_project_metadata(
                'p-zzz', {'applicant_address': 'NEW', 'manufacturer': 'LG'}, 't1',
            )
        )
        # RETURNING 이 빈 결과 → device_models UPDATE 는 아예 실행되지 않는다.
        updates = [s for s in self.statements if s.startswith('UPDATE')]
        self.assertEqual(len(updates), 1)
        self.assertEqual(self._read('device_models', 'manufacturer'), 'Samsung')


# ── 라우트 배선 (PATCH 가 실제로 도달하는가) ────────────────────────────────


class TestProjectMetadataPatchRouteWiring(unittest.TestCase):
    """계약 SSOT 에 라우트를 선언해도 ``route_handlers`` 등록이 빠지면 배선이
    없다 — OpenAPI 만 보고 "구현했다"고 적지 않기 위한 wire-level 봉인."""

    def setUp(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:  # pragma: no cover — shard without fastapi
            self.skipTest('fastapi not installed in this shard')
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
            create_platform_app,
        )

        self.service, _ = _make_service()
        _seed_project(self.service)
        adapter = PlatformApiAdapter(None, project_service=self.service)
        self.client = TestClient(create_platform_app(adapter), raise_server_exceptions=False)

    def test_patch_updates_metadata_over_http(self):
        resp = self.client.patch(
            f'/platform/projects/{_PROJECT_ID}', json={'applicant_name': 'NEW Inc.'},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body['applicant_name'], 'NEW Inc.')
        self.assertEqual(body['applicant_address'], 'seed-applicant_address')  # 무변경

    def test_patch_with_status_is_a_problem_json_400(self):
        resp = self.client.patch(
            f'/platform/projects/{_PROJECT_ID}', json={'status': 'completed'},
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn('problem+json', resp.headers['content-type'])
        self.assertEqual(
            self.client.get(f'/platform/projects/{_PROJECT_ID}').json()['status'],
            'active',
        )

    def test_patch_unknown_project_is_404(self):
        resp = self.client.patch(
            '/platform/projects/99999999-9999-4999-8999-999999999999',
            json={'applicant_address': 'X'},
        )
        self.assertEqual(resp.status_code, 404, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# M-2 / M-3 / M-4 — 409 충돌 · 서버측 검색 · keyset · 인덱스
# ══════════════════════════════════════════════════════════════════════════════


def _make_directory_service(count: int, *, tied_timestamps: bool = False):
    """``count`` 개 프로젝트가 든 디렉터리 서비스.

    ``tied_timestamps=True`` 는 **모든 프로젝트의 created_at 을 같게** 만든다 —
    전순서 tie-breaker(계약 S8)가 실제로 필요한 상황을 재현하기 위한 것이다.
    동률이 없으면 tie-breaker 를 지워도 테스트가 통과해 봉인이 무의미해진다.
    """
    central = _InMemoryCentral()
    counter = iter(range(1, 4 * count + 10))
    stamps = iter(range(1, count + 10))

    def _next_id() -> str:
        return f'{next(counter):08d}-0000-4000-8000-000000000000'

    def _next_clock() -> str:
        if tied_timestamps:
            return '2026-07-28T00:00:00+00:00'
        return f'2026-07-28T00:00:{next(stamps):02d}+00:00'

    read_port = _FakeProjectReadPort(central)
    service = CentralProjectService(
        read_port, _FakeProjectWritePort(central), _FakeMembershipService(),
        clock=_next_clock, id_factory=_next_id,
    )
    for index in range(count):
        service.create_project(_create_body(f'SM-X{index:03d}', applicant_name='ACME' if index % 2 == 0 else 'Contoso', management_number=f'MGMT-{index:04d}'), actor_subject='tester@example.com')
    return service, central, read_port


# ── S5 / S6 — UNIQUE 충돌의 기계 판독 표면 ──────────────────────────────────


class TestProjectUniqueViolationClassification(unittest.TestCase):
    """드라이버 예외 → 어느 정체성 키가 충돌했는가 (순수 판정)."""

    def test_postgres_sqlstate_and_constraint_name_resolve_the_field(self):
        for constraint, field in PROJECT_UNIQUE_CONSTRAINTS.items():
            self.assertEqual(
                classify_project_unique_violation(
                    sqlstate=UNIQUE_VIOLATION_SQLSTATE,
                    message=f'duplicate key value violates unique constraint "{constraint}"',
                ),
                field,
            )

    def test_sqlite_message_without_sqlstate_resolves_the_field(self):
        # sqlite3 는 SQLSTATE 가 없다 — 메시지 지문으로 폴백해야 봉인 shim 에서
        # 같은 경로가 검증된다.
        for field in PROJECT_UNIQUE_CONSTRAINTS.values():
            self.assertEqual(
                classify_project_unique_violation(
                    sqlstate=None,
                    message=f'UNIQUE constraint failed: projects.{field}',
                ),
                field,
            )

    def test_other_sqlstate_is_not_a_conflict_even_if_message_says_unique(self):
        # SQLSTATE 를 노출하는 드라이버에서는 그것이 권위 — 메시지에 'unique' 가
        # 섞여 있다고 무결성 위반으로 둔갑시키지 않는다.
        self.assertIsNone(
            classify_project_unique_violation(
                sqlstate='08006',
                message='connection failed while checking unique constraint',
            )
        )

    def test_unknown_constraint_stays_none_so_the_503_path_is_kept(self):
        self.assertIsNone(
            classify_project_unique_violation(
                sqlstate=UNIQUE_VIOLATION_SQLSTATE,
                message='duplicate key value violates unique constraint "ux_samples_x"',
            )
        )

    def test_constraint_names_match_the_central_schema_ssot(self):
        schema = json.loads(_CENTRAL_SCHEMA.read_text(encoding='utf-8'))
        unique_indexes = {
            index['name']: tuple(index.get('columns') or ())
            for index in schema['tables']['projects']['indexes']
            if index.get('unique')
        }
        self.assertEqual(
            set(PROJECT_UNIQUE_CONSTRAINTS), set(unique_indexes),
            'policy ↔ schema drift: a UNIQUE index the policy does not know about '
            'silently degrades its conflict back to a 503',
        )
        for constraint, field in PROJECT_UNIQUE_CONSTRAINTS.items():
            self.assertEqual(unique_indexes[constraint], (field,))


class TestProjectIdentifierConflictErrorContract(unittest.TestCase):
    def test_conflict_is_a_central_project_error_subclass(self):
        # 기존 ``except CentralProjectError`` 사이트(어댑터 롤백/서비스 race/라우트
        # 경계)가 하나도 안 바뀌어도 되는 이유.
        self.assertTrue(
            issubclass(ProjectIdentifierConflictError, CentralProjectError)
        )

    def test_conflict_carries_field_and_resource_within_the_pii_allowlist(self):
        exc = ProjectIdentifierConflictError('management_number', 'project')
        self.assertEqual(exc.field, 'management_number')
        self.assertEqual(exc.resource, 'project')
        self.assertLessEqual({'field', 'resource'}, PROBLEM_PARAM_ALLOWLIST)

    def test_error_table_orders_the_conflict_before_its_superclass(self):
        from fcc_test_platform.api.platform_routes import (
            _PLATFORM_ERROR_CODE_TABLE,
        )

        types = [entry[0] for entry in _PLATFORM_ERROR_CODE_TABLE]
        self.assertLess(
            types.index(ProjectIdentifierConflictError),
            types.index(CentralProjectError),
            'most-specific-first is the whole mechanism: behind its superclass the '
            'conflict resolves to UPSTREAM_UNAVAILABLE (503) again',
        )

    def test_status_is_409_and_derives_from_the_error_code_status_ssot(self):
        from fcc_test_platform.api.platform_routes import (
            _PLATFORM_ERROR_CODE_TABLE,
            api_error_status,
        )

        code = dict(_PLATFORM_ERROR_CODE_TABLE)[ProjectIdentifierConflictError]
        # 전용 코드 — generic CONFLICT 로 되돌아가면 클라이언트는 이 표면의 다른
        # 409 와 구분할 방법이 detail 문자열 매칭밖에 없다.
        self.assertEqual(code, ErrorCode.PROJECT_IDENTIFIER_CONFLICT)
        self.assertEqual(ERROR_CODE_STATUS[code], 409)
        self.assertEqual(
            api_error_status(
                ProjectIdentifierConflictError('management_number', 'project')
            ),
            409,
        )
        # 라우트가 409 를 직접 쓰지 않는다: 상태는 단일 SSOT 파생.
        self.assertNotEqual(
            api_error_status(ProjectIdentifierConflictError('x', 'project')),
            ERROR_CODE_STATUS[ErrorCode.UPSTREAM_UNAVAILABLE],
        )

    def test_route_boundary_declares_the_conflict_params(self):
        """이 409 는 어느 칸이 충돌했는지 `params` 로 말한다 — 중복 관리번호와
        중복 모델명이 둘 다 같은 코드의 409 라 그것 없이는 구분이 불가능하다.

        ⚠️ **단언 대상이 2026-08-31 에 바뀌었고 의도는 그대로다.** 예전에는 이 파일이
        `platform_routes._problem_params` 라는 **비공개 isinstance 표**를 이름으로
        import 했다. 그 표는 저장소에서 `params` 를 채우는 유일한 자리였고, 그래서
        headless 400 은 어느 칸인지 말할 수 없었다. 지금은 **예외가 자기 구조화 맥락을
        선언**하고(`PROBLEM_PARAM_FIELDS`) `build_problem_details` 가 그 선언을 읽는다.

        그래서 판정은 **비공개 헬퍼 호출이 아니라 실제 경계 경로**를 지난다 — 그 표면이
        쓰는 진짜 에러표와 기본값으로 problem 본문을 조립해 wire 에 실릴 dict 를 본다.
        완화가 아니라 강화다: 옛 단언은 헬퍼가 옳은 답을 내면 통과했고 그 답이 응답에
        실리는지는 묻지 않았다.
        """
        from fcc_test_contracts.common.api_error_codes import build_problem_details
        from fcc_test_platform.api.platform_routes import (
            _PLATFORM_DEFAULT_ERROR_CODE,
            _PLATFORM_ERROR_CODE_TABLE,
        )

        def problem_body(exc):
            return build_problem_details(
                exc,
                _PLATFORM_ERROR_CODE_TABLE,
                default=_PLATFORM_DEFAULT_ERROR_CODE,
            ).as_dict()

        body = problem_body(
            ProjectIdentifierConflictError('management_number', 'project')
        )
        self.assertEqual(
            body['params'], {'field': 'management_number', 'resource': 'project'},
        )
        # 옛 wire 동작 그대로 — 이 절반은 리팩터링이었다.
        self.assertEqual(body['status'], 409)
        self.assertEqual(body['code'], ErrorCode.PROJECT_IDENTIFIER_CONFLICT.value)
        # 다른 예외는 params 를 만들지 않는다(임의 컨텍스트 유출 0). 키가 아예 없어야
        # 한다 — 빈 매핑을 실으면 "맥락 없음" 이 두 가지 관측 상태를 갖는다.
        self.assertNotIn('params', problem_body(ValueError('x')))


class TestErrorCodeSurfaceScope(unittest.TestCase):
    """전용 코드를 추가해도 **발행 대상 표면만** 흔들리는지 봉인.

    이전 결함: ``problem_details_component_schemas`` 가 ``ErrorCode`` 전 union 을
    모든 표면 OpenAPI 에 그대로 발행 → 한 표면만 낼 수 있는 코드를 추가해도
    headless/session 아티팩트가 재작성됐다. 이제 코드가 자기 scope 를 선언하고
    표면이 자기 subset 을 주입한다.
    """

    def test_the_conflict_code_is_platform_scoped(self):
        self.assertEqual(
            ERROR_CODE_SURFACE_SCOPE[ErrorCode.PROJECT_IDENTIFIER_CONFLICT],
            frozenset({ApiSurface.PLATFORM}),
        )
        self.assertIn(
            ErrorCode.PROJECT_IDENTIFIER_CONFLICT,
            surface_error_codes(ApiSurface.PLATFORM),
        )
        self.assertNotIn(
            ErrorCode.PROJECT_IDENTIFIER_CONFLICT,
            surface_error_codes(ApiSurface.HEADLESS),
        )
        # 미선언 코드는 공유 union 에 남는다(기존 발행 계약 무변경 = ratchet).
        self.assertNotIn(ErrorCode.PROJECT_IDENTIFIER_CONFLICT, SHARED_ERROR_CODES)
        self.assertEqual(
            set(SHARED_ERROR_CODES),
            set(ErrorCode) - set(ERROR_CODE_SURFACE_SCOPE),
        )

    def test_every_code_this_surface_can_emit_is_published(self):
        from fcc_test_platform.api.platform_routes import (
            _PLATFORM_DEFAULT_ERROR_CODE,
            _PLATFORM_ERROR_CODE_TABLE,
        )

        emitted = {code for _, code in _PLATFORM_ERROR_CODE_TABLE}
        emitted.add(_PLATFORM_DEFAULT_ERROR_CODE)
        published = set(surface_error_codes(ApiSurface.PLATFORM))
        self.assertLessEqual(
            emitted, published,
            'the platform surface can raise a code its OpenAPI never advertises',
        )

    def test_the_published_enum_is_derived_not_hand_listed(self):
        from fcc_test_contracts.common.openapi_schema_builder import (
            ERROR_CODE_SCHEMA_NAME,
            problem_details_component_schemas,
        )

        platform = problem_details_component_schemas(
            surface_error_codes(ApiSurface.PLATFORM)
        )[ERROR_CODE_SCHEMA_NAME]['enum']
        self.assertEqual(
            platform, [c.value for c in surface_error_codes(ApiSurface.PLATFORM)],
        )
        # scoping 이 실제로 좁힌다: 명시적 SHARED_ERROR_CODES 호출은 platform 의
        # scoped subset 과 다르다(``codes`` 가 필수가 된 뒤에도 이 축은 유지).
        shared = problem_details_component_schemas(SHARED_ERROR_CODES)[
            ERROR_CODE_SCHEMA_NAME
        ]['enum']
        self.assertEqual(shared, [c.value for c in SHARED_ERROR_CODES])
        self.assertNotIn(ErrorCode.PROJECT_IDENTIFIER_CONFLICT.value, shared)
        self.assertNotEqual(platform, shared)

    def test_the_platform_artifact_publishes_exactly_this_surfaces_codes(self):
        artifact = json.loads(
            (resolve_dependency_artifact('docs/api/platform-api.openapi.json')).read_text(
                encoding='utf-8'
            )
        )
        self.assertEqual(
            artifact['components']['schemas']['ErrorCode']['enum'],
            [c.value for c in surface_error_codes(ApiSurface.PLATFORM)],
        )

    def test_no_artifact_carries_a_code_scoped_away_from_its_own_surface(self):
        """어떤 아티팩트도 자기 surface 가 낼 수 없는 코드를 광고하지 않는다.

        옛 판정은 제외 집합을 **HEADLESS 기준으로 한 번** 만들어 headless 와 session
        **양쪽**에 적용했다. session 이 ErrorCode 를 아예 발행하지 않던 동안에는
        공집합 교차라 통과했지만, 2026-08-23 에 session 이 자기 코드 여섯을 발행하자
        그것들이 *HEADLESS 스코프가 아니라는 이유로* 위반으로 잡혔다 — 검사가 틀렸지
        아티팩트가 틀린 것이 아니다.

        판정을 **아티팩트마다 자기 surface 로** 파생한다. 그러면 surface 가 늘어도
        기준 surface 를 손으로 고를 일이 없고, 세 방향 전부를 동시에 지킨다.

        ⚠️ **아티팩트는 파일 단위로, 그리고 소유 레인에 물어서 찾는다** (2026-08-27).
        옛 형태는 ``resolve_repo_artifact(__file__, 'docs/api') / name`` 이었고 두
        가지가 틀렸다. 하나는 **디렉터리를 물었다는 것** — 납품 상자에서 ``docs/api``
        는 쪼개진다(``headless_provider_registry.json`` 만 ``config/`` 로 가고 나머지는
        제자리) → 기록이 답을 둘 내놓고 resolver 가 ``RelocationAmbiguity`` 로 정직하게
        거부한다. 다른 하나는 **자기 상자에 물었다는 것** — 세 아티팩트는 contracts
        레인 소유이고 platform 상자에는 ``platform-api.openapi.json`` 하나만 실린다.
        27줄 위 ``test_the_platform_artifact_publishes_exactly_this_surfaces_codes``
        가 이미 ``resolve_dependency_artifact`` 로 그 질문을 옳게 하고 있었다 — 한
        파일 안에서 같은 질문에 두 답을 두면 다음 독자가 틀린 쪽을 고른다.

        해소된 경로는 ``_SURFACE_OPENAPI_ARTIFACTS`` 가 갖는다. 왜 루프 안에서
        조립하지 않는지는 그 선언이 실측과 함께 적고 있다.
        """
        for surface, path in _SURFACE_OPENAPI_ARTIFACTS:
            with self.subTest(surface=surface.value):
                artifact = json.loads(path.read_text(encoding='utf-8'))
                enum = set(
                    artifact.get('components', {})
                    .get('schemas', {})
                    .get('ErrorCode', {})
                    .get('enum', [])
                )
                self.assertTrue(enum, f'{path.name} publishes no ErrorCode enum')
                foreign = {
                    code.value
                    for code, surfaces in ERROR_CODE_SURFACE_SCOPE.items()
                    if surface not in surfaces
                }
                self.assertFalse(
                    foreign & enum,
                    f'{path.name} advertises a code {surface.value} cannot emit: '
                    f'{sorted(foreign & enum)}',
                )

    def test_both_conflicting_operations_declare_the_409(self):
        # 전용 코드는 발행돼야 쓸모가 있다 — create/update 둘 다 409 를 계약에
        # 선언해야 생성 클라이언트가 그 분기를 볼 수 있다(M3 "create 경로에도").
        for operation in ('create_project', 'update_project'):
            declared = PLATFORM_API_OPERATIONS[operation].get('error_responses') or {}
            self.assertIn('409', declared, f'{operation} emits 409 but never declares it')
            self.assertIn(
                ErrorCode.PROJECT_IDENTIFIER_CONFLICT.value, declared['409'],
                'the 409 description must name the machine-readable code',
            )

    def test_the_new_code_has_status_and_title_from_the_ssot(self):
        self.assertEqual(
            ERROR_CODE_STATUS[ErrorCode.PROJECT_IDENTIFIER_CONFLICT], 409,
        )
        self.assertTrue(ERROR_CODE_TITLES[ErrorCode.PROJECT_IDENTIFIER_CONFLICT])


class TestProjectIdentifierConflictFromRealSqlite(unittest.TestCase):
    """드라이버 예외 → 409 승격이 **실제 DB 무결성 위반**에서 동작하는지."""

    def setUp(self):
        self.conn = SqliteConnectionFactory(':memory:').create()
        self.conn.execute(
            'CREATE TABLE projects ('
            ' id TEXT PRIMARY KEY, project_code TEXT NOT NULL,'
            ' management_number TEXT, status TEXT,'
            ' fcc_grantee_code TEXT, applicant_name TEXT, applicant_address TEXT,'
            ' eut_description TEXT, test_standard TEXT,'
            ' created_at TEXT NOT NULL, updated_at TEXT NOT NULL)'
        )
        self.conn.execute(
            'CREATE UNIQUE INDEX ux_projects_management_number '
            'ON projects (management_number)'
        )
        self.conn.execute(
            "INSERT INTO projects (id, project_code, management_number,"
            " status, created_at, updated_at) VALUES"
            " ('p1','SM-A','MGMT-1','active','t0','t0')"
        )
        self.conn.execute(
            "INSERT INTO projects (id, project_code, management_number,"
            " status, created_at, updated_at) VALUES"
            " ('p2','SM-B','MGMT-2','active','t0','t0')"
        )
        self.conn.commit()
        self.adapter = PostgresCentralProjectWriteAdapter(lambda: AdoptedQmarkConnection(self.conn))

    def tearDown(self):
        self.conn.close()

    def test_metadata_edit_onto_a_taken_management_number_is_a_conflict(self):
        with self.assertRaises(ProjectIdentifierConflictError) as ctx:
            self.adapter.update_project_metadata(
                'p2', {'management_number': 'MGMT-1'}, 't1',
            )
        self.assertEqual(ctx.exception.field, 'management_number')
        self.assertEqual(ctx.exception.resource, PROJECT_CONFLICT_RESOURCE)

    def test_the_failed_edit_rolled_back(self):
        with self.assertRaises(ProjectIdentifierConflictError):
            self.adapter.update_project_metadata(
                'p2', {'management_number': 'MGMT-1'}, 't1',
            )
        row = self.conn.execute(
            "SELECT management_number FROM projects WHERE id='p2'"
        ).fetchone()
        self.assertEqual(row[0], 'MGMT-2')

    def test_an_unrelated_failure_still_raises_the_503_class(self):
        broken = PostgresCentralProjectWriteAdapter(
            lambda: (_ for _ in ()).throw(RuntimeError('boom'))
        )
        with self.assertRaises(CentralProjectError) as ctx:
            broken.update_project_metadata('p2', {'applicant_address': 'X'}, 't1')
        self.assertNotIsInstance(ctx.exception, ProjectIdentifierConflictError)




class TestCreateProjectConflictIsNotSwallowed(unittest.TestCase):
    """create 경로에도 같은 코드가 적용된다 (계약 M3 마지막 문장)."""

    def test_duplicate_management_number_on_create_raises_the_conflict(self):
        service, _central, _read = _make_directory_service(2)
        with self.assertRaises(ProjectIdentifierConflictError) as ctx:
            service.create_project(_create_body('SM-BRAND-NEW', management_number='MGMT-0000'), actor_subject='tester@example.com')
        self.assertEqual(ctx.exception.field, 'management_number')

    def test_same_model_name_still_reuses_idempotently(self):
        # D1 재사용(같은 project_code)은 충돌이 아니다 — 409 승격이 이 경로를
        # 망가뜨리지 않았는지 확인한다.
        service, _central, _read = _make_directory_service(1)
        again = service.create_project(_create_body('SM-X000'), actor_subject='tester@example.com')
        self.assertEqual(again['model_name'], 'SM-X000')


# ── S7 — 서버측 검색 ────────────────────────────────────────────────────────


class TestProjectSearchAxisSsot(unittest.TestCase):
    def test_management_number_is_in_the_search_axis(self):
        # 계약 D3 — 관리번호가 현업 1차 조회 키.
        self.assertIn('management_number', PROJECT_SEARCH_COLUMNS)

    def test_search_sql_derives_every_column_from_the_ssot(self):
        sql = PROJECT_LIST_SQL_VARIANTS[(False, True, False, False)]
        for column in PROJECT_SEARCH_COLUMNS:
            self.assertEqual(
                sql.count(f'LOWER("p"."{column}") LIKE %s'), 1,
                f'{column} must appear exactly once in the search predicate',
            )

    def test_no_column_outside_the_ssot_is_searched(self):
        sql = PROJECT_LIST_SQL_VARIANTS[(False, True, False, False)]
        searched = set(re.findall(r'LOWER\("p"\."([a-z_]+)"\) LIKE', sql))
        self.assertEqual(searched, set(PROJECT_SEARCH_COLUMNS))

    def test_search_binds_values_and_never_interpolates(self):
        for sql in PROJECT_LIST_SQL_VARIANTS.values():
            self.assertTrue(sql.startswith('SELECT '))
            # 값이 SQL 텍스트에 박히는 경로 0 — 리터럴은 ESCAPE 문자 하나뿐.
            literals = re.findall(r"'([^']*)'", sql)
            self.assertTrue(
                all(literal == SEARCH_LIKE_ESCAPE_CHAR for literal in literals),
                f'unexpected SQL string literal(s) {literals}',
            )

    def test_like_pattern_escapes_user_typed_metacharacters(self):
        # '50%' 검색이 전체 매칭이 되거나 'A_1' 이 'A11' 을 잡으면 안 된다.
        self.assertEqual(search_like_pattern('50%'), r'%50\%%')
        self.assertEqual(search_like_pattern('A_1'), r'%a\_1%')
        self.assertEqual(search_like_pattern(r'a\b'), r'%a\\b%')
        self.assertEqual(search_like_pattern('  MGMT-1  '), '%mgmt-1%')

    def test_blank_search_term_is_no_filter_not_an_error(self):
        for blank in (None, '', '   '):
            self.assertIsNone(normalize_search_term(blank))


class TestProjectSearchSqlAgainstDdl(unittest.TestCase):
    """검색 SQL 의 의미(대소문자 무관 · 부분일치 · 메타문자 이스케이프)를 실제
    DDL 형상 테이블에서 end-to-end 로 확인한다 — 파이썬 재구현이 아니라 SQL."""

    def setUp(self):
        self.conn = SqliteConnectionFactory(':memory:').create()
        self.conn.execute(
            'CREATE TABLE projects ('
            ' id TEXT PRIMARY KEY, project_code TEXT NOT NULL,'
            ' management_number TEXT, status TEXT,'
            ' fcc_grantee_code TEXT, applicant_name TEXT, applicant_address TEXT,'
            ' eut_description TEXT, test_standard TEXT,'
            ' created_at TEXT NOT NULL, updated_at TEXT NOT NULL)'
        )
        self.conn.execute(
            'CREATE TABLE device_models ('
            ' id TEXT PRIMARY KEY, project_id TEXT NOT NULL, model_name TEXT NOT NULL,'
            ' manufacturer TEXT,'
            ' created_at TEXT NOT NULL, updated_at TEXT NOT NULL)'
        )
        self.conn.execute(
            'CREATE TABLE samples ('
            ' id TEXT PRIMARY KEY, project_id TEXT NOT NULL, sample_code TEXT NOT NULL,'
            ' created_at TEXT NOT NULL)'
        )
        rows = [
            ('p1', 'SM-S921U', 'ACME Corp', '2026-RF-0001', '2026-07-28T00:00:01Z'),
            ('p2', 'SM-X940', 'Contoso', '2026-RF-0002', '2026-07-28T00:00:02Z'),
            ('p3', 'LM-G900', 'ACME Corp', '2025-SAR-50%', '2026-07-28T00:00:03Z'),
        ]
        for pid, code, applicant, mgmt, created in rows:
            self.conn.execute(
                'INSERT INTO projects (id, project_code, applicant_name,'
                ' management_number, status, created_at, updated_at)'
                " VALUES (?,?,?,?, 'active', ?, ?)",
                (pid, code, applicant, mgmt, created, created),
            )
            self.conn.execute(
                'INSERT INTO device_models (id, project_id, model_name, manufacturer,'
                ' created_at, updated_at) VALUES (?,?,?,?,?,?)',
                (f'm-{pid}', pid, code, 'Samsung', created, created),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _search(self, term, *, status=None):
        by_status = status is not None
        sql = PROJECT_LIST_SQL_VARIANTS[(by_status, True, False, False)].replace('%s', '?')
        pattern = search_like_pattern(term)
        params = ((status,) if by_status else ()) + (pattern,) * len(PROJECT_SEARCH_COLUMNS)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(zip(PROJECT_LIST_COLUMNS, row))['project_id'] for row in rows]

    def test_management_number_substring_is_found(self):
        self.assertEqual(self._search('RF-0002'), ['p2'])

    def test_search_is_case_insensitive_both_ways(self):
        self.assertEqual(self._search('sm-x940'), ['p2'])
        self.assertEqual(self._search('acme'), ['p3', 'p1'])  # newest first

    def test_search_spans_project_code_and_applicant_name(self):
        self.assertEqual(sorted(self._search('ACME')), ['p1', 'p3'])
        self.assertEqual(self._search('LM-G'), ['p3'])

    def test_percent_in_the_term_is_a_literal_not_a_wildcard(self):
        # '%' 를 이스케이프하지 않으면 이 검색이 전 행을 반환한다.
        self.assertEqual(self._search('50%'), ['p3'])

    def test_underscore_in_the_term_is_a_literal(self):
        self.assertEqual(self._search('SM_S921U'), [])

    def test_no_match_returns_empty_not_everything(self):
        self.assertEqual(self._search('zzzz-nothing'), [])

    def test_search_composes_with_the_status_filter(self):
        self.conn.execute("UPDATE projects SET status='completed' WHERE id='p1'")
        self.conn.commit()
        self.assertEqual(self._search('ACME', status='active'), ['p3'])
        self.assertEqual(self._search('ACME', status='completed'), ['p1'])


# ── S8 / S10 / S11 — keyset 페이지네이션 ────────────────────────────────────


class TestProjectDirectoryKeyset(unittest.TestCase):
    def test_order_columns_are_a_total_order_with_a_unique_tiebreaker(self):
        self.assertEqual(PROJECT_DIRECTORY_ORDER_COLUMNS[-1], 'id')
        self.assertEqual(
            len(PROJECT_DIRECTORY_ORDER_COLUMNS),
            len(PROJECT_DIRECTORY_CURSOR_FIELDS),
            'cursor arity must equal the SQL order-by arity',
        )
        self.assertEqual(PROJECT_DIRECTORY_CURSOR_FIELDS[-1], 'project_id')

    def test_cursor_fields_are_all_present_on_the_read_row(self):
        for field in PROJECT_DIRECTORY_CURSOR_FIELDS:
            self.assertIn(
                field, PROJECT_LIST_COLUMNS,
                'the service builds the cursor from the read ROW — a missing '
                'column would make next_cursor a KeyError at runtime',
            )

    def test_paging_covers_every_project_exactly_once_with_tied_timestamps(self):
        service, _central, _read = _make_directory_service(7, tied_timestamps=True)
        seen: list[str] = []
        cursor = None
        for _ in range(20):  # 무한루프 방지 상한
            page = service.list_projects(status='all', limit=2, cursor=cursor)
            seen.extend(item['project_id'] for item in page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        self.assertIsNone(cursor, 'paging did not terminate')
        self.assertEqual(len(seen), 7, 'page boundary duplicated or skipped rows')
        self.assertEqual(len(set(seen)), 7)

    def test_pages_are_newest_first_and_strictly_descending(self):
        service, _central, _read = _make_directory_service(5)
        page = service.list_projects(status='all', limit=5)
        ids = [item['project_id'] for item in page['items']]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_last_page_has_no_next_cursor(self):
        service, _central, _read = _make_directory_service(3)
        page = service.list_projects(status='all', limit=3)
        self.assertEqual(len(page['items']), 3)
        self.assertIsNone(page['next_cursor'])

    def test_offset_is_never_used(self):
        for sql in PROJECT_LIST_SQL_VARIANTS.values():
            self.assertNotIn('OFFSET', sql.upper())

    def test_malformed_cursor_is_loud_not_an_empty_page(self):
        service, _central, _read = _make_directory_service(2)
        with self.assertRaises(CursorError):
            service.list_projects(status='all', limit=2, cursor='not-a-cursor')
        # CursorError 는 ValueError 라 플랫폼 에러 테이블이 400 으로 매핑한다.
        self.assertTrue(issubclass(CursorError, ValueError))

    def test_wrong_arity_cursor_is_rejected(self):
        service, _central, _read = _make_directory_service(2)
        with self.assertRaises(CursorError):
            service.list_projects(
                status='all', limit=2, cursor=encode_cursor(['only-one-key']),
            )

    def test_page_size_is_clamped_to_the_pagination_ssot(self):
        service, _central, read_port = _make_directory_service(2)
        service.list_projects(status='all', limit=MAX_PAGE_SIZE * 10)
        # +1 은 "다음 페이지가 있는가"를 한 쿼리로 알아내는 probe row.
        self.assertEqual(read_port.calls[-1]['limit'], MAX_PAGE_SIZE + 1)
        service.list_projects(status='all', limit=0)
        self.assertEqual(read_port.calls[-1]['limit'], 2)

    def test_cursor_without_limit_uses_the_default_page_size(self):
        service, _central, read_port = _make_directory_service(3)
        first = service.list_projects(status='all', limit=1)
        service.list_projects(status='all', cursor=first['next_cursor'])
        self.assertEqual(read_port.calls[-1]['limit'], DEFAULT_PAGE_SIZE + 1)

    def test_page_size_literals_are_not_hardcoded_in_the_service(self):
        source = (
            resolve_repo_artifact(__file__, 'src/application/platform/central_project_service.py')
        ).read_text(encoding='utf-8')
        tree = ast.parse(source)
        service_fn = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == 'list_projects'
        )
        numbers = {
            node.value for node in ast.walk(service_fn)
            if isinstance(node, ast.Constant) and isinstance(node.value, int)
            and not isinstance(node.value, bool)
        }
        self.assertLessEqual(
            numbers, {1},
            'page-size policy must come from the pagination SSOT '
            '(1 is the probe-row +1, not a page size)',
        )


#: pre-W3 ``PROJECT_LIST_SQL`` 의 꼬리를 **글자 그대로** 얼려 둔 것(git 이력에서 전사).
#: 무제한 읽기의 정렬은 이 문자열이어야 한다 — SSOT 에서 파생한 값과 비교하면
#: "SSOT 가 바뀌면 기대값도 같이 바뀌는" 자기충족 테스트가 되어 S11 을 못 지킨다.
_PRE_W3_ORDER_BY = 'ORDER BY "p"."created_at" DESC'

_UNPAGED_VARIANT_KEYS = tuple(
    key for key in sorted(PROJECT_LIST_SQL_VARIANTS) if not (key[2] or key[3])
)
_PAGED_VARIANT_KEYS = tuple(
    key for key in sorted(PROJECT_LIST_SQL_VARIANTS) if key[2] or key[3]
)


class TestProjectDirectoryOrderAxisSsot(unittest.TestCase):
    """정렬 축이 두 개(legacy / 전순서)가 된 이상, 그 선택은 단일 소유점에서
    나와야 하고 두 축의 관계(접두)가 깨지면 안 된다."""

    def test_legacy_axis_is_a_strict_prefix_of_the_total_order(self):
        # 접두가 아니면 두 축은 서로 **다른 정렬**이 되어, created_at 이 다른 두 행의
        # 상대 순서마저 페이지네이션 유무에 따라 달라질 수 있다.
        self.assertEqual(
            PROJECT_DIRECTORY_ORDER_COLUMNS[:len(PROJECT_DIRECTORY_LEGACY_ORDER_COLUMNS)],
            PROJECT_DIRECTORY_LEGACY_ORDER_COLUMNS,
        )
        self.assertLess(
            len(PROJECT_DIRECTORY_LEGACY_ORDER_COLUMNS),
            len(PROJECT_DIRECTORY_ORDER_COLUMNS),
            'legacy 축은 tie-breaker 가 없는 진부분집합이어야 한다',
        )

    def test_selector_returns_the_total_order_only_for_a_paged_query(self):
        self.assertEqual(
            directory_order_columns(paginated=True), PROJECT_DIRECTORY_ORDER_COLUMNS,
        )
        self.assertEqual(
            directory_order_columns(paginated=False),
            PROJECT_DIRECTORY_LEGACY_ORDER_COLUMNS,
        )

    def test_adapter_never_hardcodes_an_order_by_column(self):
        source = (
            resolve_repo_artifact(__file__, 'src/application/platform/central_project_read_adapter.py')
        ).read_text(encoding='utf-8')
        code = '\n'.join(
            line for line in source.splitlines() if not line.lstrip().startswith('#')
        )
        for column in PROJECT_DIRECTORY_ORDER_COLUMNS:
            self.assertNotIn(
                f'"p"."{column}" DESC', code,
                'ORDER BY 절은 directory_order_columns SSOT 에서만 파생해야 한다',
            )


class TestProjectDirectoryBackwardCompatibility(unittest.TestCase):
    """S11 — q/limit/cursor 미전달 시 기존 응답과 동일."""

    def test_unbounded_read_passes_no_search_or_keyset_arguments(self):
        service, _central, read_port = _make_directory_service(3)
        page = service.list_projects(status='all')
        self.assertEqual(
            read_port.calls[-1],
            {'status': 'all', 'q': None, 'limit': None, 'after': None},
        )
        self.assertEqual(len(page['items']), 3)
        self.assertIsNone(page['next_cursor'])

    def test_envelope_gains_no_new_field(self):
        service, _central, _read = _make_directory_service(1)
        item = service.list_projects(status='all')['items'][0]
        self.assertNotIn(
            'created_at', item,
            'created_at is the cursor source, NOT part of the list envelope — '
            'adding it would change the unbounded response',
        )
        schema_properties = set(
            PLATFORM_API_SCHEMAS['ProjectEnvelope']['properties']
        )
        self.assertEqual(set(item), schema_properties)

    def test_unbounded_sql_is_the_no_filter_variant(self):
        self.assertIs(PROJECT_LIST_SQL, PROJECT_LIST_SQL_VARIANTS[(False, False, False, False)])
        self.assertIs(
            PROJECT_LIST_SQL_BY_STATUS,
            PROJECT_LIST_SQL_VARIANTS[(True, False, False, False)],
        )
        self.assertNotIn('LIKE', PROJECT_LIST_SQL)
        self.assertNotIn('LIMIT', PROJECT_LIST_SQL)

    def test_unbounded_sql_keeps_the_pre_w3_order_by_verbatim(self):
        # 두 pre-W3 read 는 정렬까지 옛 문장과 같아야 한다. keyset tie-breaker 를
        # 여기에 붙이면 created_at 동률 행들의 응답 순서가 바뀐다(= S11 위반).
        for sql in (PROJECT_LIST_SQL, PROJECT_LIST_SQL_BY_STATUS):
            self.assertTrue(
                sql.endswith(_PRE_W3_ORDER_BY),
                f'unbounded 정렬이 pre-W3 와 다르다: ...{sql[-70:]!r}',
            )

    def test_no_unpaged_variant_carries_the_keyset_tiebreaker(self):
        # 검색만 걸린 무제한 읽기(q, limit/cursor 없음)도 페이지 경계가 없으므로
        # 같은 축을 쓴다 — 정렬 축은 "페이지인가"만 보고 갈린다.
        tie_breaker = PROJECT_DIRECTORY_ORDER_COLUMNS[-1]
        for key in _UNPAGED_VARIANT_KEYS:
            sql = PROJECT_LIST_SQL_VARIANTS[key]
            self.assertTrue(sql.endswith(_PRE_W3_ORDER_BY), f'variant {key}')
            self.assertNotIn(f'"p"."{tie_breaker}" DESC', sql, f'variant {key}')

    def test_every_paged_variant_still_uses_the_total_order(self):
        # 반대 방향 봉인 — S11 을 지키려다 keyset 정확성(S8)을 깨면 안 된다.
        total_order = 'ORDER BY ' + ', '.join(
            f'"p"."{column}" DESC' for column in PROJECT_DIRECTORY_ORDER_COLUMNS
        )
        for key in _PAGED_VARIANT_KEYS:
            self.assertIn(total_order, PROJECT_LIST_SQL_VARIANTS[key], f'variant {key}')

    def test_unbounded_response_does_not_reorder_tied_rows_by_id(self):
        """created_at 동률 데이터에서 무제한 응답이 tie-breaker 순서를 강요하지 않는다.

        전순서가 새어 들어오면 응답은 정확히 id 내림차순이 된다 — 그 형태를 음성
        단언해 회귀를 잡는다(동률 구간의 순서 자체는 엔진 소관이라 양성 단언하지 않음).
        """
        service, _central, read_port = _make_directory_service(5, tied_timestamps=True)
        ids = [
            item['project_id'] for item in service.list_projects(status='all')['items']
        ]
        self.assertEqual(len(ids), 5)
        self.assertEqual(read_port.calls[-1]['limit'], None)
        self.assertNotEqual(
            ids, sorted(ids, reverse=True),
            'unbounded read applied the keyset tie-breaker (S11 회귀)',
        )
        # 대조군 — 같은 데이터의 페이지 읽기는 전순서를 그대로 쓴다.
        paged = [
            item['project_id']
            for item in service.list_projects(status='all', limit=5)['items']
        ]
        self.assertEqual(paged, sorted(paged, reverse=True))
        self.assertEqual(sorted(paged), sorted(ids), '두 축의 행 집합은 같아야 한다')


class TestProjectDirectoryDistinctRemoval(unittest.TestCase):
    """DISTINCT 는 증명 가능한 no-op 이면서 keyset 인덱스 스캔을 무력화했다 —
    되돌아오면 50k 행에서 289ms/0.54ms 의 차이가 되살아난다."""

    def test_no_variant_uses_distinct(self):
        for key, sql in PROJECT_LIST_SQL_VARIANTS.items():
            self.assertNotIn('DISTINCT', sql.upper(), f'variant {key}')

    def test_the_only_join_is_the_one_to_one_device_model(self):
        # DISTINCT 제거의 정당성은 "membership 조인이 없다"에 걸려 있다.
        for sql in PROJECT_LIST_SQL_VARIANTS.values():
            self.assertEqual(sql.upper().count(' JOIN '), 1)
            self.assertIn('LEFT JOIN "device_models"', sql)
            self.assertNotIn('project_membership', sql)


class TestProjectDirectoryKeysetSqlAgainstDdl(unittest.TestCase):
    """keyset SQL 이 실제 테이블에서 페이지를 겹치거나 건너뛰지 않는지 —
    created_at 이 전부 동률인 최악 조건으로."""

    def setUp(self):
        self.conn = SqliteConnectionFactory(':memory:').create()
        self.conn.execute(
            'CREATE TABLE projects ('
            ' id TEXT PRIMARY KEY, project_code TEXT NOT NULL,'
            ' management_number TEXT, status TEXT,'
            ' fcc_grantee_code TEXT, applicant_name TEXT, applicant_address TEXT,'
            ' eut_description TEXT, test_standard TEXT,'
            ' created_at TEXT NOT NULL, updated_at TEXT NOT NULL)'
        )
        self.conn.execute(
            'CREATE TABLE device_models ('
            ' id TEXT PRIMARY KEY, project_id TEXT NOT NULL, model_name TEXT NOT NULL,'
            ' manufacturer TEXT,'
            ' created_at TEXT NOT NULL, updated_at TEXT NOT NULL)'
        )
        self.conn.execute(
            'CREATE TABLE samples ('
            ' id TEXT PRIMARY KEY, project_id TEXT NOT NULL, sample_code TEXT NOT NULL,'
            ' created_at TEXT NOT NULL)'
        )
        self.total = 9
        for index in range(self.total):
            pid = f'p{index:02d}'
            self.conn.execute(
                'INSERT INTO projects (id, project_code, status,'
                ' created_at, updated_at)'
                " VALUES (?,?, 'active', ?, ?)",
                (pid, f'SM-{index:03d}',
                 '2026-07-28T00:00:00Z', '2026-07-28T00:00:00Z'),
            )
            self.conn.execute(
                'INSERT INTO device_models (id, project_id, model_name,'
                ' created_at, updated_at) VALUES (?,?,?,?,?)',
                (f'm{index}', pid, f'SM-{index:03d}',
                 '2026-07-28T00:00:00Z', '2026-07-28T00:00:00Z'),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _page(self, after, size):
        sql = PROJECT_LIST_SQL_VARIANTS[(True, False, after is not None, True)]
        params = ('active',) + (tuple(after) if after else ()) + (size,)
        rows = self.conn.execute(sql.replace('%s', '?'), params).fetchall()
        return [dict(zip(PROJECT_LIST_COLUMNS, row)) for row in rows]

    def test_walking_pages_covers_every_row_exactly_once(self):
        seen: list[str] = []
        after = None
        for _ in range(20):
            rows = self._page(after, 2)
            if not rows:
                break
            seen.extend(row['project_id'] for row in rows)
            after = tuple(
                rows[-1][field] for field in PROJECT_DIRECTORY_CURSOR_FIELDS
            )
        self.assertEqual(len(seen), self.total)
        self.assertEqual(len(set(seen)), self.total)

    def test_rows_come_back_newest_first(self):
        rows = self._page(None, self.total)
        ids = [row['project_id'] for row in rows]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def _unpaged(self, sql):
        rows = self.conn.execute(sql.replace('%s', '?'), ('active',)).fetchall()
        return [dict(zip(PROJECT_LIST_COLUMNS, row)) for row in rows]

    def test_unbounded_statement_is_the_pre_w3_statement_and_still_reads(self):
        # 정렬만 따로 얼려 비교한다 — 나머지 절은 현재 문장에서 그대로 가져오므로
        # 이 단언이 검사하는 것은 오직 "정렬 축이 옛 문장인가"이다.
        control = (
            PROJECT_LIST_SQL_BY_STATUS[:PROJECT_LIST_SQL_BY_STATUS.index('ORDER BY')]
            + _PRE_W3_ORDER_BY
        )
        self.assertEqual(PROJECT_LIST_SQL_BY_STATUS, control)
        rows = self._unpaged(PROJECT_LIST_SQL_BY_STATUS)
        self.assertEqual(len(rows), self.total, '무제한 읽기가 전량을 돌려줘야 한다')

    def test_the_two_axes_read_the_same_rows_but_only_one_forces_an_order(self):
        """봉인이 공허하지 않다는 근거 — 이 픽스처는 ``created_at`` 이 **전부 동률**이라
        legacy 축만으로는 순서가 결정되지 않는다(엔진 소관). 전순서 축을 붙이면 그
        구간이 id 내림차순으로 확정된다. 즉 무제한 경로에 tie-breaker 가 새면 응답
        순서가 관측 가능하게 달라진다(= Codex 가 지적한 S11 회귀).

        동률 구간의 실제 순서는 SQLite sorter 구현에 달려 있으므로 여기서 양성 단언을
        하지 않는다 — 그 단언은 결정적인 서비스+Fake 레이어가 맡는다
        (``test_unbounded_response_does_not_reorder_tied_rows_by_id``)."""
        unpaged = self._unpaged(PROJECT_LIST_SQL_BY_STATUS)
        paged_ids = [row['project_id'] for row in self._page(None, self.total)]
        self.assertEqual(
            len({row['created_at'] for row in unpaged}), 1,
            '픽스처가 동률이 아니면 두 축이 같은 순서를 내어 봉인이 공허해진다',
        )
        self.assertEqual(paged_ids, sorted(paged_ids, reverse=True))
        self.assertEqual(
            sorted(row['project_id'] for row in unpaged), sorted(paged_ids),
            '두 축은 정렬만 다르고 행 집합은 같아야 한다',
        )


# ── S12 / S13 — 인덱스 SSOT + 증분 마이그레이션 ─────────────────────────────


class TestProjectDirectoryIndexSsot(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(_CENTRAL_SCHEMA.read_text(encoding='utf-8'))
        self.indexes = {
            index['name']: index
            for index in self.schema['tables']['projects']['indexes']
        }
        self.ddl = (
            resolve_repo_artifact(__file__, 'docs/platform/migrations/001_initial_central_db.sql')
        ).read_text(encoding='utf-8')

    def test_keyset_order_has_a_backing_index_matching_the_order_columns(self):
        index = self.indexes['idx_projects_directory']
        self.assertEqual(tuple(index['columns']), PROJECT_DIRECTORY_ORDER_COLUMNS)

    def test_status_filtered_keyset_has_its_own_backing_index(self):
        index = self.indexes['idx_projects_status_directory']
        self.assertEqual(
            tuple(index['columns']), ('status',) + PROJECT_DIRECTORY_ORDER_COLUMNS,
        )

    def test_every_search_column_has_a_trigram_expression_index(self):
        for column in PROJECT_SEARCH_COLUMNS:
            index = self.indexes[f'idx_projects_search_{column}']
            self.assertEqual(index.get('using'), 'gin')
            self.assertEqual(
                index['expressions'], [f'lower({column}) gin_trgm_ops'],
                "the expression is stored in PostgreSQL's OWN canonical rendering "
                '(lower(col), unquoted) so an introspected migration-evidence '
                'manifest round-trips byte-identically; PostgreSQL normalises the '
                'adapter\'s LOWER("p"."col") predicate to the same expression, '
                'which is what makes the index usable',
            )

    def test_pg_trgm_extension_is_declared_and_rendered(self):
        self.assertIn('pg_trgm', self.schema['required_extensions'])
        self.assertIn('CREATE EXTENSION IF NOT EXISTS pg_trgm;', self.ddl)
        # 기존 확장은 그대로 남는다(추가만, 교체 아님).
        self.assertIn('CREATE EXTENSION IF NOT EXISTS pgcrypto;', self.ddl)

    def test_the_exporter_renders_every_new_index_into_001(self):
        self.assertIn(
            'CREATE INDEX IF NOT EXISTS "idx_projects_directory" '
            'ON "projects" ("created_at", "id");',
            self.ddl,
        )
        self.assertIn(
            'CREATE INDEX IF NOT EXISTS "idx_projects_status_directory" '
            'ON "projects" ("status", "created_at", "id");',
            self.ddl,
        )
        for column in PROJECT_SEARCH_COLUMNS:
            self.assertIn(
                f'CREATE INDEX IF NOT EXISTS "idx_projects_search_{column}" '
                f'ON "projects" USING gin (lower({column}) gin_trgm_ops);',
                self.ddl,
            )

    def test_001_is_in_sync_with_the_schema_ssot(self):
        result = subprocess.run(
            [sys.executable, 'scripts/export_platform_central_db_ddl.py', '--check'],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class TestProjectDirectoryMigration010(unittest.TestCase):
    def setUp(self):
        self.sql = _MIGRATION_010.read_text(encoding='utf-8')
        sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))
        import platform_db_migrate

        self.runner = platform_db_migrate
        # **적용 완료된 마이그레이션은 과거의 사실이다.** 러너가 파일 checksum 을
        # 원장과 대조하므로 010 을 손대면 이미 적용한 DB 에서 drift 오류가 난다 —
        # 즉 이 목록은 오늘의 검색 축(SSOT)이 아니라 010 이 만든 인덱스여야 한다.
        # 축이 applicant_name 으로 옮겨간 것은 031 이 담당하고,
        # TestApplicantSearchAxisMigration 이 "오늘의 SSOT ↔ 마이그레이션" 대응을
        # 지킨다. 여기서 SSOT 를 참조하면 축이 바뀔 때마다 과거 파일이 red 가 된다.
        self.expected_indexes = (
            'idx_projects_directory',
            'idx_projects_status_directory',
            'idx_projects_search_management_number',
            'idx_projects_search_project_code',
            'idx_projects_search_customer',
        )

    def test_the_runner_discovers_it_after_009(self):
        """010 must sort after 009 and appear exactly once.

        This deliberately does NOT assert that 010 is the LAST migration —
        that would freeze the ledger and turn every future migration into a
        false failure. What must hold is the ordering relative to its
        predecessor and the absence of duplicate versions.
        """
        discovered = self.runner.discover_migrations(_MIGRATION_010.parent)
        versions = [version for version, _path in discovered]

        self.assertIn(_MIGRATION_010.stem, versions)
        predecessors = [version for version in versions if version.startswith('009')]
        self.assertEqual(len(predecessors), 1, 'expected exactly one 009 migration')
        self.assertLess(
            versions.index(predecessors[0]),
            versions.index(_MIGRATION_010.stem),
            '010 must be discovered after 009',
        )
        self.assertEqual(len(versions), len(set(versions)), 'duplicate version')

    def test_it_is_the_next_version_after_009(self):
        """No migration may claim a number between 009 and 010, or reuse one."""
        versions = sorted(
            path.name.split('_')[0]
            for path in _MIGRATION_010.parent.glob('*.sql')
        )
        self.assertEqual(len(versions), len(set(versions)), 'duplicate version number')
        self.assertIn('010', versions)
        self.assertEqual(
            versions[versions.index('010') - 1],
            '009',
            '010 must directly follow 009',
        )

    def test_every_index_is_created_idempotently(self):
        for name in self.expected_indexes:
            self.assertIn(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}"', self.sql,
            )
            # 재시도 self-heal: 실패한 CONCURRENTLY 가 남긴 INVALID 인덱스를 먼저 치운다.
            self.assertIn(
                f'DROP INDEX CONCURRENTLY IF EXISTS "{name}";', self.sql,
            )

    def test_no_destructive_ddl(self):
        body = self.runner._strip_sql_line_comments(self.sql).upper()
        for forbidden in ('DROP TABLE', 'DROP COLUMN', 'TRUNCATE', 'DELETE FROM',
                          'DROP EXTENSION', 'ALTER TABLE'):
            self.assertNotIn(forbidden, body, f'{forbidden} is destructive')

    def test_runner_classifies_it_as_non_transactional(self):
        # CONCURRENTLY 는 트랜잭션 안에서 못 돈다 — 러너가 이를 감지하지 못하면
        # 마이그레이션이 운영에서 통째로 실패한다.
        self.assertTrue(self.runner.migration_requires_non_transactional(self.sql))

    def test_rollback_annotations_are_inert_comments_covering_every_index(self):
        self.assertTrue(
            self.runner.rollback_annotation_lines_are_inert_comments(self.sql)
        )
        rollback = self.runner.parse_rollback_statements(self.sql)
        self.assertEqual(len(rollback), len(self.expected_indexes))
        for name in self.expected_indexes:
            self.assertTrue(
                any(f'"{name}"' in statement for statement in rollback),
                f'{name} has no DOWN statement',
            )
        # 롤백은 인덱스만 되돌린다 — pg_trgm 확장은 남긴다(다른 객체가 의존 가능).
        self.assertFalse(
            any('EXTENSION' in statement.upper() for statement in rollback)
        )

    def test_forward_body_splits_into_executable_statements(self):
        statements = self.runner.split_sql_statements(self.sql)
        # 확장 1 + (DROP+CREATE) × 5 + ANALYZE 1
        self.assertEqual(len(statements), 2 + 2 * len(self.expected_indexes))
        self.assertTrue(all(statement.endswith(';') for statement in statements))

    def test_it_analyzes_after_creating_the_expression_indexes(self):
        # 표현식 인덱스는 자체 통계를 갖는다 — ANALYZE 가 빠지면 플래너가 기본
        # 선택도로 값을 매겨 인덱스가 있는데도 Seq Scan 을 고른다(실측 37.1ms vs
        # 0.248ms). "인덱스를 만들었으니 빨라졌다"는 추측을 막는 한 줄.
        statements = self.runner.split_sql_statements(self.sql)
        self.assertTrue(
            any(statement.upper().startswith('ANALYZE') for statement in statements),
            'a migration that adds expression indexes must refresh statistics',
        )
        self.assertTrue(statements[-1].upper().startswith('ANALYZE'))


# ── S16 — 계층 순수성 ───────────────────────────────────────────────────────


class TestDirectoryDomainPurity(unittest.TestCase):
    _FORBIDDEN = (
        'infrastructure', 'psycopg', 'psycopg2', 'sqlalchemy', 'sqlite3',
        'openpyxl', 'pandas', 'PySide6', 'fastapi', 'application',
    )

    def _imported_roots(self, path):
        """⚠️ **최상위가 아니라 계층으로 판정한다** (2026-09-03).

        여기 있던 것은 `node.module.split('.')[0]` — **최상위 이름**이었다.
        커널 이관이 모듈에 `fcc_test_kernel.` 접두사를 붙이면 최상위가
        `fcc_test_kernel` 이 되어, `'infrastructure'` 금지가 **이관 당일에
        조용히 통과**한다.

        「AST 로 하면 된다」가 답이 아니었다 — AST 로 뽑은 최상위도 같은
        맹점을 갖는다. 판정 단위가 **계층 절**이어야 한다:
        `tests/_layer_of_import.py`.

        이름은 그대로 둔다 — 호출부 셋이 이 이름을 쓰고, 이름을 바꾸는 것은
        이 정정의 축이 아니다.
        """
        return imported_layers(path)

    def test_directory_query_policy_is_pure(self):
        self.assertFalse(
            self._imported_roots(_DIRECTORY_POLICY_MODULE) & set(self._FORBIDDEN)
        )

    def test_conflict_policy_is_pure(self):
        self.assertFalse(
            self._imported_roots(_CONFLICT_POLICY_MODULE) & set(self._FORBIDDEN)
        )

    def test_read_adapter_keeps_the_psycopg_paramstyle_and_imports_no_driver(self):
        read_adapter = (
            resolve_repo_artifact(__file__, 'src/application/platform/central_project_read_adapter.py')
        )
        self.assertFalse(
            self._imported_roots(read_adapter) & {'psycopg', 'psycopg2', 'sqlite3'}
        )
        for sql in PROJECT_LIST_SQL_VARIANTS.values():
            self.assertNotIn('?', sql, 'psycopg paramstyle is %s, never ?')


# ── 라우트 배선 — 검색/페이지네이션/409 가 HTTP 로 도달하는가 ────────────────


class TestProjectDirectoryRouteWiring(unittest.TestCase):
    def setUp(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:  # pragma: no cover — shard without fastapi
            self.skipTest('fastapi not installed in this shard')
        from fcc_test_platform.api.platform_routes import (
            PlatformApiAdapter,
            create_platform_app,
        )

        self.service, _central, _read = _make_directory_service(5)
        adapter = PlatformApiAdapter(None, project_service=self.service)
        self.client = TestClient(create_platform_app(adapter), raise_server_exceptions=False)

    def test_directory_body_is_still_a_plain_array(self):
        resp = self.client.get('/platform/projects?status=all')
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsInstance(resp.json(), list)
        self.assertEqual(len(resp.json()), 5)
        self.assertNotIn(PLATFORM_NEXT_CURSOR_HEADER, resp.headers)

    def test_limit_paginates_and_emits_the_cursor_header(self):
        resp = self.client.get('/platform/projects?status=all&limit=2')
        self.assertEqual(len(resp.json()), 2)
        cursor = resp.headers[PLATFORM_NEXT_CURSOR_HEADER]
        self.assertTrue(cursor)
        following = self.client.get(
            f'/platform/projects?status=all&limit=2&cursor={cursor}'
        )
        self.assertEqual(len(following.json()), 2)
        self.assertEqual(
            set(item['project_id'] for item in resp.json())
            & set(item['project_id'] for item in following.json()),
            set(), 'consecutive pages overlapped',
        )

    def test_search_narrows_the_directory_over_http(self):
        resp = self.client.get('/platform/projects?status=all&q=MGMT-0003')
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            [item['management_number'] for item in resp.json()], ['MGMT-0003'],
        )

    def test_blank_search_returns_the_whole_directory(self):
        resp = self.client.get('/platform/projects?status=all&q=')
        self.assertEqual(len(resp.json()), 5)

    def test_corrupt_cursor_is_a_problem_json_400(self):
        resp = self.client.get('/platform/projects?status=all&cursor=@@bogus@@')
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn('problem+json', resp.headers['content-type'])
        self.assertEqual(resp.json()['code'], ErrorCode.VALIDATION_ERROR.value)

    def test_duplicate_management_number_is_a_problem_json_409_with_the_field(self):
        # 편집으로 남의 관리번호를 밟는 경로. (create 쪽 같은 승격은
        # TestCreateProjectConflictIsNotSwallowed 가 서비스 경계에서 봉인한다 —
        # create 라우트는 인증된 actor 를 요구해 이 auth-disabled 앱에서 403 이다.)
        target = self.client.get('/platform/projects?status=all').json()[0]
        resp = self.client.patch(
            f"/platform/projects/{target['project_id']}",
            json={'management_number': 'MGMT-0000'},
        )
        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertIn('problem+json', resp.headers['content-type'])
        body = resp.json()
        # 전용 코드 — 이 표면의 다른 409(claim/report edition …)와 문자열 매칭 없이
        # 구분된다. params.field 가 어느 식별자인지까지 좁힌다.
        self.assertEqual(body['code'], ErrorCode.PROJECT_IDENTIFIER_CONFLICT.value)
        self.assertNotEqual(body['code'], ErrorCode.CONFLICT.value)
        self.assertEqual(body['params'], {
            'field': 'management_number', 'resource': 'project',
        })

    def test_the_contract_declares_the_new_query_params_and_header(self):
        self.assertEqual(
            PLATFORM_API_OPERATION_QUERY['list_projects'],
            ('status', 'q', 'limit', 'cursor'),
        )
        self.assertIn(
            PLATFORM_NEXT_CURSOR_HEADER,
            PLATFORM_API_RESPONSE_HEADERS['list_projects'],
        )


# ── 신청자 축 이전 (2026-09-04) — 031 확장 / 032 수축 ────────────────────────


class TestApplicantSearchAxisMigration(unittest.TestCase):
    """검색 축이 ``customer`` → ``applicant_name`` 으로 옮겨간 두 마이그레이션.

    **expand-and-contract** 다: 031 이 새 인덱스를 먼저 만들고(확장), 032 가 값을
    합친 뒤 컬럼을 지운다(수축). 순서가 뒤집히면 축 인덱스가 없는 창이 생기고, 그
    창에서 디렉터리 검색이 Seq Scan 으로 떨어진다.
    """

    def setUp(self):
        sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))
        import platform_db_migrate

        self.runner = platform_db_migrate
        self.expand = _MIGRATION_031.read_text(encoding='utf-8')
        self.contract = _MIGRATION_032.read_text(encoding='utf-8')

    def test_todays_search_axis_is_covered_by_a_migration(self):
        """오늘의 검색 축 SSOT ↔ 마이그레이션 대응 — 이것이 드리프트 게이트다.

        010 은 자기 시점의 축을 담은 **과거의 사실**이라(체크섬이 원장에 잠겨 있어
        수정 불가) 축이 바뀌면 새 마이그레이션이 그 차이를 메워야 한다. 축이 또
        옮겨가는 날 이 테스트가 red 가 되고, 인덱스 없는 축이 배포되지 않는다.
        """
        applied = self.expand + self.contract + _MIGRATION_010.read_text(encoding='utf-8')
        for column in PROJECT_SEARCH_COLUMNS:
            with self.subTest(column=column):
                self.assertIn(f'idx_projects_search_{column}', applied)

    def test_the_expand_half_creates_both_indexes_idempotently(self):
        for name in ('idx_projects_search_applicant_name',
                     'idx_projects_applicant_directory'):
            with self.subTest(index=name):
                self.assertIn(
                    f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}"', self.expand,
                )
                # 실패한 CONCURRENTLY 가 남긴 INVALID 인덱스를 먼저 치운다(재시도 self-heal).
                self.assertIn(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}";', self.expand)

    def test_the_expand_half_is_non_transactional_and_non_destructive(self):
        self.assertTrue(self.runner.migration_requires_non_transactional(self.expand))
        body = self.runner._strip_sql_line_comments(self.expand).upper()
        for forbidden in ('DROP TABLE', 'DROP COLUMN', 'TRUNCATE', 'DELETE FROM',
                          'DROP EXTENSION', 'ALTER TABLE'):
            self.assertNotIn(forbidden, body, f'{forbidden} is destructive')

    def test_the_contract_half_runs_in_one_transaction(self):
        """백필·가드·DROP 이 한 트랜잭션이어야 부분 적용이 없다.

        CONCURRENTLY 가 없어야 러너가 트랜잭션 경로를 고르고, 그 경로만이 파일을
        통째로 실행해 ``DO $$ ... $$`` 가드를 성립시킨다.
        """
        self.assertFalse(self.runner.migration_requires_non_transactional(self.contract))
        self.assertIn('BEGIN;', self.contract)
        self.assertIn('COMMIT;', self.contract)

    def test_the_contract_half_refuses_to_destroy_an_adjudicable_value(self):
        """두 칸에 **서로 다른** 주체가 있으면 사람이 판단해야 한다 — 조용히 이기지 않는다."""
        self.assertIn('RAISE EXCEPTION', self.contract)
        # 가드는 DROP **앞**에 있어야 한다(뒤에 있으면 이미 지운 뒤다).
        self.assertLess(
            self.contract.index('RAISE EXCEPTION'),
            self.contract.index('DROP COLUMN'),
        )
        # 병합은 잃을 것이 없는 행에만 적용된다.
        self.assertIn('btrim("applicant_name") = \'\'', self.contract)

    def test_the_contract_half_declares_its_partial_reversibility(self):
        """컬럼 DROP 은 값을 되돌리지 못한다 — 그 사실이 주석에 있어야 한다.

        형상(컬럼+인덱스)은 복구하되 값은 복구하지 못한다고 말하지 않으면, 운영자는
        rollback 한 줄로 원상복구된다고 믿는다.
        """
        self.assertTrue(
            self.runner.rollback_annotation_lines_are_inert_comments(self.contract)
        )
        rollback = self.runner.parse_rollback_statements(self.contract)
        self.assertTrue(any('ADD COLUMN' in stmt for stmt in rollback))
        self.assertIn('PARTIAL', self.contract)

    def test_the_two_halves_are_ordered_and_unique(self):
        discovered = self.runner.discover_migrations(_MIGRATION_031.parent)
        versions = [version for version, _path in discovered]
        self.assertEqual(len(versions), len(set(versions)), 'duplicate version')
        self.assertLess(
            versions.index(_MIGRATION_031.stem),
            versions.index(_MIGRATION_032.stem),
            'the expand half must run before the contract half',
        )

    def test_the_retired_column_is_gone_from_the_schema_ssot(self):
        import json

        schema = json.loads(_CENTRAL_SCHEMA.read_text(encoding='utf-8'))
        columns = schema['tables']['projects']['columns']
        self.assertNotIn('customer', columns)
        # 후임 칸은 남아 있어야 한다 — 폐기는 이동이지 삭제가 아니다.
        self.assertIn(APPLICANT_IDENTITY_FIELD, columns)


class TestApplicantDirectoryRead(unittest.TestCase):
    """신청자 제안 조회 — 자동 채움의 원천이 실제로 '최신 한 행'인가."""

    def _service(self):
        service, _central = _make_service()
        return service

    def test_one_row_per_applicant_with_the_newest_values(self):
        service = self._service()
        _seed_project(
            service, model_name='OLD-1',
            applicant_name='ACME Inc.', applicant_address='1 Old Road',
            manufacturer='OldCo',
        )
        _seed_project(
            service, model_name='NEW-1',
            applicant_name='ACME Inc.', applicant_address='2 New Road',
            manufacturer='NewCo',
        )
        suggestions = service.list_applicant_suggestions()
        self.assertEqual(len(suggestions), 1, suggestions)
        entry = suggestions[0]
        # 같은 신청자로 두 번 만들었으면 **마지막에 쓴** 주소/제조사가 기본값이다.
        self.assertEqual(entry['applicant_address'], '2 New Road')
        self.assertEqual(entry['manufacturer'], 'NewCo')
        self.assertEqual(entry['project_count'], 2)

    def test_case_only_variants_are_the_same_applicant(self):
        service = self._service()
        _seed_project(service, model_name='A-1', applicant_name='ACME Inc.')
        _seed_project(service, model_name='A-2', applicant_name='acme inc.')
        self.assertEqual(len(service.list_applicant_suggestions()), 1)

    def test_projects_without_an_applicant_are_not_suggestions(self):
        service = self._service()
        # applicant_name 은 생성 필수라 빈 값으로는 만들 수 없다 — 그러니 '이름 없는
        # 후보'는 레거시 행에서만 온다. 그 경우에도 제안이 되어서는 안 된다.
        _seed_project(service, model_name='A-1', applicant_name='ACME Inc.')
        service.update_project_metadata(
            service.list_projects()['items'][0]['project_id'],
            {'applicant_name': None},
        )
        self.assertEqual(service.list_applicant_suggestions(), [])

    def test_the_suggestion_never_carries_a_unique_field(self):
        """관리번호는 제안에 실리면 **안 된다** — 물려받는 순간 409 다."""
        service = self._service()
        _seed_project(service, model_name='A-1', applicant_name='ACME Inc.')
        entry = service.list_applicant_suggestions()[0]
        for field in UNIQUE_PROJECT_META_FIELDS:
            with self.subTest(field=field):
                self.assertNotIn(field, entry)

    def test_search_narrows_by_applicant_name(self):
        service = self._service()
        _seed_project(service, model_name='A-1', applicant_name='ACME Inc.')
        _seed_project(service, model_name='B-1', applicant_name='Contoso Ltd.')
        names = [
            entry[APPLICANT_IDENTITY_FIELD]
            for entry in service.list_applicant_suggestions(q='conto')
        ]
        self.assertEqual(names, ['Contoso Ltd.'])

    def test_a_blank_search_term_is_no_filter_not_an_empty_result(self):
        service = self._service()
        _seed_project(service, model_name='A-1', applicant_name='ACME Inc.')
        self.assertEqual(len(service.list_applicant_suggestions(q='   ')), 1)

    def test_the_read_is_always_bounded(self):
        """자동완성은 타이핑마다 호출된다 — 상한 없는 읽기를 만들지 않는다."""
        service, _central = _make_service()
        _seed_project(service, model_name='A-1', applicant_name='ACME Inc.')
        service.list_applicant_suggestions()
        for call in service._read.applicant_calls:
            self.assertIsNotNone(call['limit'])
            self.assertLessEqual(call['limit'], MAX_PAGE_SIZE)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
