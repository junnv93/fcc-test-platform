"""성적서 §6 장비목록 애플리케이션 서비스 봉인 (2026-08-07).

in-memory fake 포트로 서비스 경계의 판정을 단언한다. MagicMock 을 쓰지 않는다 —
fake 가 실제 포트 시그니처를 구현하므로 포트가 바뀌면 여기서 먼저 깨진다
(MagicMock 은 어떤 시그니처 변경도 조용히 통과시킨다).

봉인 대상:

- 프로젝트 미존재 → 404(``ProjectNotFoundError``)
- 타 프로젝트 목록 → 404(403 이 아니다 — 존재를 흘리지 않는다)
- 자연키 중복 → 409
- 확정본 항목 편집 → 409
- 빈 목록 확정 → 409
- 정상 확정 → ``status='confirmed'`` + ``confirmed_at``
- ``sort_order`` 는 서버가 배열 위치에서 부여하고 클라이언트 값을 무시한다
- ``tables`` 는 도메인 정책에서 파생한다(서비스가 열 이름을 재선언하지 않는다)
- 잘못된 uuid → ``ValueError``
- 어댑터가 부분 인덱스 두 술어를 **각각** 쓰는 INSERT 를 갖는다

Owned by ``/verify-report-equipment-list-central``.
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402
from support.central_pg_sqlite_shim import AdoptedQmarkConnection  # noqa: E402

from fcc_test_platform.application.central_project_service import ProjectNotFoundError  # noqa: E402
from fcc_test_platform.domain.ports.output.central_report_port import CentralReportReadPort  # noqa: E402
from fcc_test_platform.application.central_test_equipment_list_service import (  # noqa: E402
    CentralTestEquipmentListService,
)
from fcc_test_platform.application.central_test_equipment_list_write_adapter import (  # noqa: E402
    INSERT_LIST_ATTACHED_SQL,
    INSERT_LIST_UNATTACHED_SQL,
    ITEM_INSERT_COLUMNS,
)
from fcc_test_platform.domain.ports.output.central_test_equipment_list_port import (  # noqa: E402
    CentralTestEquipmentListReadPort,
    CentralTestEquipmentListWritePort,
    EquipmentListConflictError,
    EquipmentListNotFoundError,
)
from fcc_test_kernel.domain.services.test_equipment_list_policy import (  # noqa: E402
    EQUIPMENT_TABLE_COLUMNS,
    SOFTWARE_TABLE_COLUMNS,
    TEST_ITEM_KEYS,
    ListStatus,
)

_PROJECT = "11111111-1111-4111-8111-111111111111"
_OTHER_PROJECT = "22222222-2222-4222-8222-222222222222"
_LIST = "33333333-3333-4333-8333-333333333333"


class FakeProjectRead:
    def __init__(self, known=(_PROJECT,)):
        self._known = set(known)

    def read_project_detail(self, project_id: str):
        if project_id not in self._known:
            return None
        return {"project_id": project_id, "management_number": "4792232056"}

    def list_projects(self, *args, **kwargs):  # pragma: no cover — port completeness
        return []


class FakeReportRead:
    """성적서 read 포트 — create/attach 의 프로젝트 귀속 확인에 쓰인다.

    ⚠️ 이 클래스는 ``CentralReportReadPort`` **전량**을 만족해야 한다. 이 서비스가
    ``list_reports`` 만 부른다는 사실은 이유가 되지 않는다 — 대역이 드라이버보다 느슨하면
    그 대역을 받아들이는 봉인은 어떤 드라이버도 바인딩할 수 없는 객체를 통과시킨다.
    실측 2026-08-26: ``get_session_snapshot`` 이 포트에 추가된 뒤 이 대역만 옛 모양에
    남아 ``TestPortConformance`` 가 red 였다.
    """

    def __init__(self, reports=None, session_snapshots=None):
        self.reports = dict(reports or {})
        self.session_snapshots = dict(session_snapshots or {})

    def list_reports(self, project_id: str) -> list[dict]:
        return [dict(r) for r in self.reports.get(project_id, [])]

    def get_session_snapshot(self, session_id: str):
        snapshot = self.session_snapshots.get(session_id)
        return dict(snapshot) if snapshot is not None else None


class FakeEquipmentRead:
    def __init__(self, lists=None, items=None):
        self.lists = list(lists or [])
        self.items = dict(items or {})

    def list_lists(self, project_id: str) -> list[dict]:
        return [row for row in self.lists if row.get("project_id") == project_id]

    def read_list(self, equipment_list_id: str):
        for row in self.lists:
            if row.get("list_id") == equipment_list_id:
                return dict(row)
        return None

    def list_items(self, equipment_list_id: str) -> list[dict]:
        return [dict(i) for i in self.items.get(equipment_list_id, [])]


class FakeEquipmentWrite:
    def __init__(self, *, conflict_on_create=False):
        self.conflict_on_create = conflict_on_create
        self.created: list[dict] = []
        self.replaced: list[tuple] = []
        self.confirmed: list[tuple] = []
        self.attached: list[tuple] = []

    def create_list(self, list_record):
        if self.conflict_on_create:
            return None
        self.created.append(dict(list_record))
        return {"list_id": list_record.get("id")}

    def replace_items(self, equipment_list_id, items, *, updated_at):
        self.replaced.append((equipment_list_id, [dict(i) for i in items], updated_at))
        return {"item_count": len(items)}

    def attach_to_report(self, equipment_list_id, *, test_report_id, updated_at):
        self.attached.append((equipment_list_id, test_report_id, updated_at))
        return {'list_id': equipment_list_id, 'test_report_id': test_report_id}

    def confirm_list(self, equipment_list_id, *, confirmed_at):
        self.confirmed.append((equipment_list_id, confirmed_at))
        return {
            "list_id": equipment_list_id,
            "status": ListStatus.CONFIRMED.value,
            "confirmed_at": confirmed_at,
        }


def _row(status=ListStatus.DRAFT.value, project_id=_PROJECT, list_id=_LIST, **extra):
    row = {
        "list_id": list_id,
        "project_id": project_id,
        "test_report_id": None,
        "test_item_key": "BT",
        "test_item_name": "Bluetooth",
        "status": status,
        "source_profile_key": None,
        "source_revision_key": None,
        "source_pulled_at": None,
        "confirmed_at": None,
        "created_at": "2026-08-07T00:00:00+00:00",
        "updated_at": "2026-08-07T00:00:00+00:00",
        "item_count": 0,
    }
    row.update(extra)
    return row


def _service(
    read=None, write=None, projects=(_PROJECT,), clock=None, ids=None, reports=None,
):
    counter = {"n": 0}

    def _id():
        counter["n"] += 1
        return f"00000000-0000-4000-8000-{counter['n']:012d}"

    return CentralTestEquipmentListService(
        read or FakeEquipmentRead(),
        write or FakeEquipmentWrite(),
        FakeProjectRead(projects),
        FakeReportRead(reports if reports is not None else {}),
        clock=clock or (lambda: "2026-08-07T12:00:00+00:00"),
        id_factory=ids or _id,
    )


class TestPortConformance(unittest.TestCase):
    def test_fakes_satisfy_the_ports(self):
        self.assertIsInstance(FakeEquipmentRead(), CentralTestEquipmentListReadPort)
        self.assertIsInstance(FakeEquipmentWrite(), CentralTestEquipmentListWritePort)
        self.assertIsInstance(FakeReportRead(), CentralReportReadPort)


class TestProjectScoping(unittest.TestCase):
    def test_unknown_project_is_not_found(self):
        with self.assertRaises(ProjectNotFoundError):
            _service(projects=()).list_lists(_PROJECT)

    def test_unknown_project_on_create(self):
        with self.assertRaises(ProjectNotFoundError):
            _service(projects=()).create_list(_PROJECT, test_item_key="BT")

    def test_list_from_another_project_is_not_found(self):
        """403 이면 그 id 가 존재한다는 사실을 흘린다."""
        read = FakeEquipmentRead(lists=[_row(project_id=_OTHER_PROJECT)])
        with self.assertRaises(EquipmentListNotFoundError):
            _service(read=read).get_list(_PROJECT, _LIST)

    def test_missing_list_is_not_found(self):
        with self.assertRaises(EquipmentListNotFoundError):
            _service().get_list(_PROJECT, _LIST)

    def test_malformed_uuid_is_value_error(self):
        with self.assertRaises(ValueError):
            _service().list_lists("not-a-uuid")
        with self.assertRaises(ValueError):
            _service().get_list(_PROJECT, "nope")

    def test_only_own_project_lists_are_returned(self):
        read = FakeEquipmentRead(
            lists=[_row(), _row(project_id=_OTHER_PROJECT, list_id="other")]
        )
        envelope = _service(read=read).list_lists(_PROJECT)
        self.assertEqual([r["list_id"] for r in envelope["lists"]], [_LIST])


class TestVocabularyRidesOnTheListResponse(unittest.TestCase):
    """생성 폼의 선택지는 서버가 준다 — 프론트가 배열을 적으면 어휘가 두 곳이 된다."""

    def test_list_response_carries_the_domain_vocabulary_in_order(self):
        envelope = _service().list_lists(_PROJECT)
        self.assertEqual(envelope["test_items"], list(TEST_ITEM_KEYS))

    def test_vocabulary_is_present_even_when_no_list_exists(self):
        """첫 목록을 만들려면 목록이 0개인 바로 그 순간에 선택지가 필요하다."""
        envelope = _service(read=FakeEquipmentRead(lists=[])).list_lists(_PROJECT)
        self.assertEqual(envelope["lists"], [])
        self.assertEqual(envelope["test_items"], list(TEST_ITEM_KEYS))

    def test_vocabulary_is_a_copy_not_the_domain_tuple(self):
        """호출자가 응답을 변형해도 도메인 SSOT 가 오염되면 안 된다."""
        envelope = _service().list_lists(_PROJECT)
        envelope["test_items"].append("MUTATED")
        self.assertEqual(
            _service().list_lists(_PROJECT)["test_items"], list(TEST_ITEM_KEYS)
        )


class TestCreate(unittest.TestCase):
    def test_status_is_server_owned(self):
        write = FakeEquipmentWrite()
        _service(write=write).create_list(_PROJECT, test_item_key="BT")
        self.assertEqual(write.created[0]["status"], ListStatus.DRAFT.value)

    def test_duplicate_natural_key_is_conflict(self):
        write = FakeEquipmentWrite(conflict_on_create=True)
        with self.assertRaises(EquipmentListConflictError):
            _service(write=write).create_list(_PROJECT, test_item_key="BT")

    def test_blank_test_item_key_rejected(self):
        with self.assertRaises(ValueError):
            _service().create_list(_PROJECT, test_item_key="   ")

    def test_ems_wording_is_rejected(self):
        """축은 EMS 가 아니라 성적서가 정한다 — EMS 표기는 400 이다.

        ``'DFS, UNII'`` 는 EMS 의 표준 표기이고 성적서 **여러 편**에 걸친다. 그것을
        축으로 받으면 ②단계가 "이 성적서의 장비목록"을 집을 수 없다. 이 테스트는
        ①단계의 정반대 단언(같은 값이 통과함)을 대체한 것이다.
        """
        write = FakeEquipmentWrite()
        with self.assertRaises(ValueError):
            _service(write=write).create_list(_PROJECT, test_item_key="DFS, UNII")
        self.assertEqual(write.created, [], "어휘 밖 값이 write 포트까지 도달했다")

    def test_unknown_token_is_rejected(self):
        """오타는 성적서 어느 편에도 대응하지 않는 행을 남긴다."""
        with self.assertRaises(ValueError):
            _service().create_list(_PROJECT, test_item_key="BTX")

    def test_test_item_key_is_canonicalised(self):
        """대소문자를 접지 않으면 자연키가 같은 시험항목을 두 행으로 허용한다.

        unique 인덱스는 정확한 텍스트 비교이므로 ``'bt'`` 와 ``'BT'`` 가 둘 다
        통과하면 한 프로젝트에 같은 시험항목의 목록이 둘 생긴다.
        """
        write = FakeEquipmentWrite()
        created = _service(write=write).create_list(_PROJECT, test_item_key=" bt ")
        self.assertEqual(write.created[0]["test_item_key"], "BT")
        self.assertEqual(created["test_item_key"], "BT")

    def test_every_domain_vocabulary_member_is_accepted(self):
        """어휘가 늘었는데 경계가 막으면 그 시험항목은 영영 못 만든다."""
        for key in TEST_ITEM_KEYS:
            with self.subTest(key=key):
                write = FakeEquipmentWrite()
                _service(write=write).create_list(_PROJECT, test_item_key=key)
                self.assertEqual(write.created[0]["test_item_key"], key)

    def test_source_fields_are_optional_and_passed_through(self):
        """EMS pull 이 나중에 붙어도 계약이 바뀌지 않도록 자리를 비워둔다."""
        write = FakeEquipmentWrite()
        _service(write=write).create_list(
            _PROJECT,
            test_item_key="BT",
            source_profile_key="prof-1",
            source_revision_key="rev-9",
            source_pulled_at="2026-08-07T00:00:00+00:00",
        )
        created = write.created[0]
        self.assertEqual(created["source_profile_key"], "prof-1")
        self.assertEqual(created["source_revision_key"], "rev-9")
        self.assertEqual(created["source_pulled_at"], "2026-08-07T00:00:00+00:00")

    def test_created_envelope_starts_empty(self):
        envelope = _service().create_list(_PROJECT, test_item_key="BT")
        self.assertEqual(envelope["item_count"], 0)
        self.assertEqual(envelope["status"], ListStatus.DRAFT.value)


class TestReplaceItems(unittest.TestCase):
    def test_sort_order_is_server_assigned(self):
        read = FakeEquipmentRead(lists=[_row()])
        write = FakeEquipmentWrite()
        _service(read=read, write=write).replace_items(
            _PROJECT,
            _LIST,
            [
                {"item_type": "equipment", "description": "a", "sort_order": 99},
                {"item_type": "equipment", "description": "b"},
            ],
        )
        _, items, _ = write.replaced[0]
        self.assertEqual([i["sort_order"] for i in items], [0, 1])

    def test_items_get_ids(self):
        read = FakeEquipmentRead(lists=[_row()])
        write = FakeEquipmentWrite()
        _service(read=read, write=write).replace_items(
            _PROJECT, _LIST, [{"item_type": "equipment", "description": "a"}]
        )
        _, items, _ = write.replaced[0]
        self.assertTrue(items[0]["id"])

    def test_confirmed_list_cannot_be_edited(self):
        read = FakeEquipmentRead(lists=[_row(status=ListStatus.CONFIRMED.value)])
        with self.assertRaises(EquipmentListConflictError):
            _service(read=read).replace_items(_PROJECT, _LIST, [{"item_type": "equipment", "description": "a"}])

    def test_empty_replacement_is_allowed(self):
        """비우는 것 자체는 편집이다 — 막는 것은 그 상태로 '확정'하는 것뿐."""
        read = FakeEquipmentRead(lists=[_row()])
        write = FakeEquipmentWrite()
        outcome = _service(read=read, write=write).replace_items(_PROJECT, _LIST, [])
        self.assertEqual(outcome["item_count"], 0)

    def test_other_project_list_cannot_be_edited(self):
        read = FakeEquipmentRead(lists=[_row(project_id=_OTHER_PROJECT)])
        with self.assertRaises(EquipmentListNotFoundError):
            _service(read=read).replace_items(_PROJECT, _LIST, [])


class TestConfirm(unittest.TestCase):
    def test_empty_list_cannot_be_confirmed(self):
        read = FakeEquipmentRead(lists=[_row()], items={_LIST: []})
        with self.assertRaises(EquipmentListConflictError):
            _service(read=read).confirm_list(_PROJECT, _LIST)

    def test_already_confirmed_is_conflict(self):
        read = FakeEquipmentRead(
            lists=[_row(status=ListStatus.CONFIRMED.value)],
            items={_LIST: [{"item_id": "i1"}]},
        )
        with self.assertRaises(EquipmentListConflictError):
            _service(read=read).confirm_list(_PROJECT, _LIST)

    def test_draft_with_items_confirms(self):
        read = FakeEquipmentRead(lists=[_row()], items={_LIST: [{"item_id": "i1"}]})
        write = FakeEquipmentWrite()
        outcome = _service(read=read, write=write).confirm_list(_PROJECT, _LIST)
        self.assertEqual(outcome["status"], ListStatus.CONFIRMED.value)
        self.assertEqual(outcome["confirmed_at"], "2026-08-07T12:00:00+00:00")
        self.assertEqual(write.confirmed[0][0], _LIST)

    def test_confirmed_at_comes_from_the_injected_clock(self):
        read = FakeEquipmentRead(lists=[_row()], items={_LIST: [{"item_id": "i1"}]})
        service = _service(read=read, clock=lambda: "2030-01-01T00:00:00+00:00")
        self.assertEqual(
            service.confirm_list(_PROJECT, _LIST)["confirmed_at"],
            "2030-01-01T00:00:00+00:00",
        )


class TestTablesComeFromTheDomain(unittest.TestCase):
    def test_tables_mirror_the_policy(self):
        read = FakeEquipmentRead(lists=[_row()], items={_LIST: []})
        envelope = _service(read=read).get_list(_PROJECT, _LIST)
        tables = {t["item_type"]: t["columns"] for t in envelope["tables"]}
        self.assertEqual(tables["equipment"], list(EQUIPMENT_TABLE_COLUMNS))
        self.assertEqual(tables["test_software"], list(SOFTWARE_TABLE_COLUMNS))

    def test_items_are_enveloped_with_every_table_column(self):
        read = FakeEquipmentRead(
            lists=[_row()],
            items={
                _LIST: [
                    {
                        "item_id": "i1",
                        "item_type": "equipment",
                        "sort_order": 0,
                        "description": "Spectrum Analyzer, 44 GHz",
                        "manufacturer": "KEYSIGHT",
                        "model_name": "N9030B",
                        "serial_number": "MY60070693",
                        "calibration_due_date": "2026-01-02",
                    }
                ]
            },
        )
        envelope = _service(read=read).get_list(_PROJECT, _LIST)
        item = envelope["items"][0]
        for column in EQUIPMENT_TABLE_COLUMNS:
            self.assertIn(column, item)
        self.assertEqual(item["description"], "Spectrum Analyzer, 44 GHz")

    def test_na_calibration_date_survives_verbatim(self):
        """원천 값이 'N/A' 를 포함하므로 date 로 파싱하지 않는다."""
        read = FakeEquipmentRead(
            lists=[_row()],
            items={_LIST: [{"item_id": "i1", "calibration_due_date": "N/A"}]},
        )
        envelope = _service(read=read).get_list(_PROJECT, _LIST)
        self.assertEqual(envelope["items"][0]["calibration_due_date"], "N/A")


class TestWriteAdapterPartialIndexTargets(unittest.TestCase):
    """부분 unique 인덱스 두 개에는 술어를 포함한 ON CONFLICT 가 각각 필요하다.

    술어 없는 단일 ON CONFLICT 는 PostgreSQL 이 인덱스를 추론하지 못해
    런타임에 실패한다 — 스키마를 읽지 않으면 드러나지 않는 함정이라 봉인한다.
    """

    def test_unattached_insert_targets_the_project_scoped_index(self):
        self.assertIn('ON CONFLICT ("project_id", "test_item_key")', INSERT_LIST_UNATTACHED_SQL)
        self.assertIn('WHERE "test_report_id" IS NULL', INSERT_LIST_UNATTACHED_SQL)

    def test_attached_insert_targets_the_report_scoped_index(self):
        self.assertIn('ON CONFLICT ("test_report_id", "test_item_key")', INSERT_LIST_ATTACHED_SQL)
        self.assertIn('WHERE "test_report_id" IS NOT NULL', INSERT_LIST_ATTACHED_SQL)

    def test_the_two_statements_are_distinct(self):
        self.assertNotEqual(INSERT_LIST_UNATTACHED_SQL, INSERT_LIST_ATTACHED_SQL)

    def test_neither_statement_omits_the_predicate(self):
        for sql in (INSERT_LIST_UNATTACHED_SQL, INSERT_LIST_ATTACHED_SQL):
            conflict = sql.split("ON CONFLICT", 1)[1]
            self.assertIn("WHERE", conflict, "partial index requires a predicate")

    def test_item_insert_columns_derive_from_the_domain(self):
        from fcc_test_kernel.domain.services.test_equipment_list_policy import ITEM_PERSISTED_FIELDS

        for field in ITEM_PERSISTED_FIELDS:
            self.assertIn(field, ITEM_INSERT_COLUMNS)
        self.assertIn("sort_order", ITEM_INSERT_COLUMNS)


class TestAdapterIsReadOnlyWhereItClaimsToBe(unittest.TestCase):
    def test_read_adapter_has_no_mutating_sql(self):
        source = resolve_repo_artifact(
            __file__,
            'src/application/platform/central_test_equipment_list_read_adapter.py',
        ).read_text(encoding="utf-8")
        # SQL 문자열 상수만 본다 — 한국어 주석에 'UPDATE' 가 들어 있어도
        # 오탐하지 않도록(옛 판본은 항등 replace 라 의도가 소실돼 있었다).
        statements = " ".join(
            re.findall(r"'([^']*(?:SELECT|INSERT|UPDATE|DELETE)[^']*)'", source, re.I)
        ).upper()
        for banned in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP "):
            self.assertNotIn(banned, statements)

    def test_no_driver_import_in_either_adapter(self):
        for name in (
            "central_test_equipment_list_read_adapter.py",
            "central_test_equipment_list_write_adapter.py",
        ):
            source = (
                resolve_repo_artifact(__file__, 'src/application/platform') / name
            ).read_text(encoding="utf-8")
            self.assertFalse(re.search(r"^\s*import\s+psycopg", source, re.M))
            self.assertFalse(re.search(r"^\s*from\s+psycopg", source, re.M))


if __name__ == "__main__":
    unittest.main()


_REPORT = "44444444-4444-4444-8444-444444444444"
_OTHER_REPORT = "55555555-5555-4555-8555-555555555555"


class TestItemVocabularyIsValidatedAtTheBoundary(unittest.TestCase):
    """클라이언트 오류가 서버 장애(503)로 보고되면 안 된다.

    ``item_type`` 결측/오타를 통과시키면 NOT NULL 또는 CHECK 위반이 되고, 어댑터의
    generic except 가 그것을 ``CentralTestEquipmentListError`` 로 감싸 503 이 된다
    (CLAUDE.md ``Platform Boundary Honesty SSOT`` 의 두 번째 축 위반).
    """

    def test_missing_item_type_is_a_value_error(self):
        read = FakeEquipmentRead(lists=[_row()])
        with self.assertRaises(ValueError):
            _service(read=read).replace_items(_PROJECT, _LIST, [{"description": "a"}])

    def test_unknown_item_type_is_a_value_error(self):
        read = FakeEquipmentRead(lists=[_row()])
        with self.assertRaises(ValueError):
            _service(read=read).replace_items(
                _PROJECT, _LIST, [{"item_type": "instrument"}]
            )

    def test_non_object_item_is_a_value_error(self):
        read = FakeEquipmentRead(lists=[_row()])
        with self.assertRaises(ValueError):
            _service(read=read).replace_items(_PROJECT, _LIST, ["not-an-object"])

    def test_both_item_types_are_accepted(self):
        read = FakeEquipmentRead(lists=[_row()])
        write = FakeEquipmentWrite()
        _service(read=read, write=write).replace_items(
            _PROJECT,
            _LIST,
            [{"item_type": "equipment"}, {"item_type": "test_software"}],
        )
        _, items, _ = write.replaced[0]
        self.assertEqual([i["item_type"] for i in items], ["equipment", "test_software"])

    def test_the_error_never_reaches_the_write_port(self):
        """검증이 어댑터 앞에서 끝나야 DB 제약이 아니라 400 이 된다."""
        read = FakeEquipmentRead(lists=[_row()])
        write = FakeEquipmentWrite()
        with self.assertRaises(ValueError):
            _service(read=read, write=write).replace_items(
                _PROJECT, _LIST, [{"item_type": "bogus"}]
            )
        self.assertEqual(write.replaced, [])


class TestReportOwnershipIsVerified(unittest.TestCase):
    """성적서 축에도 프로젝트 귀속 확인이 필요하다.

    확인하지 않으면 프로젝트 A 가 B 의 성적서에 목록을 붙여 두 번째 부분 unique
    인덱스를 **선점**하고, B 의 정당한 생성이 영구히 409 가 된다(교차 테넌트 거부).
    """

    def test_create_with_foreign_report_is_not_found(self):
        write = FakeEquipmentWrite()
        service = _service(write=write, reports={_OTHER_PROJECT: [{"report_id": _REPORT}]})
        with self.assertRaises(EquipmentListNotFoundError):
            service.create_list(_PROJECT, test_item_key="BT", test_report_id=_REPORT)
        self.assertEqual(write.created, [], "거부된 요청이 write 포트에 도달했다")

    def test_create_with_unknown_report_is_not_found(self):
        with self.assertRaises(EquipmentListNotFoundError):
            _service().create_list(_PROJECT, test_item_key="BT", test_report_id=_REPORT)

    def test_create_with_own_report_succeeds(self):
        write = FakeEquipmentWrite()
        service = _service(write=write, reports={_PROJECT: [{"report_id": _REPORT}]})
        service.create_list(_PROJECT, test_item_key="BT", test_report_id=_REPORT)
        self.assertEqual(write.created[0]["test_report_id"], _REPORT)

    def test_create_without_report_stays_unattached(self):
        write = FakeEquipmentWrite()
        _service(write=write).create_list(_PROJECT, test_item_key="BT")
        self.assertIsNone(write.created[0]["test_report_id"])


class TestAttachToReport(unittest.TestCase):
    """초안 목록이 나중에 성적서 판에 도달할 수 있어야 한다.

    이 경로가 없으면 "성적서 행이 생기기 전부터 목록을 만든다"는 설계가 API 로
    도달 불가해지고, 두 번째 부분 unique 인덱스의 근거 전체가 쓰이지 못한다.
    """

    def test_attaches_a_draft_list(self):
        read = FakeEquipmentRead(lists=[_row()])
        write = FakeEquipmentWrite()
        service = _service(read=read, write=write, reports={_PROJECT: [{"report_id": _REPORT}]})
        outcome = service.attach_to_report(_PROJECT, _LIST, test_report_id=_REPORT)
        self.assertEqual(outcome["test_report_id"], _REPORT)
        self.assertEqual(write.attached[0][:2], (_LIST, _REPORT))

    def test_foreign_report_is_not_found(self):
        read = FakeEquipmentRead(lists=[_row()])
        write = FakeEquipmentWrite()
        service = _service(
            read=read, write=write, reports={_OTHER_PROJECT: [{"report_id": _REPORT}]}
        )
        with self.assertRaises(EquipmentListNotFoundError):
            service.attach_to_report(_PROJECT, _LIST, test_report_id=_REPORT)
        self.assertEqual(write.attached, [])

    def test_foreign_list_is_not_found(self):
        read = FakeEquipmentRead(lists=[_row(project_id=_OTHER_PROJECT)])
        service = _service(read=read, reports={_PROJECT: [{"report_id": _REPORT}]})
        with self.assertRaises(EquipmentListNotFoundError):
            service.attach_to_report(_PROJECT, _LIST, test_report_id=_REPORT)

    def test_blank_report_id_is_a_value_error(self):
        read = FakeEquipmentRead(lists=[_row()])
        with self.assertRaises(ValueError):
            _service(read=read).attach_to_report(_PROJECT, _LIST, test_report_id="")


class TestRealAdaptersSatisfyThePorts(unittest.TestCase):
    """fake 만 검사하면 실제 어댑터가 포트에서 벗어나도 green 이다.

    ``central_report_*`` 선례가 실어댑터를 대조하는 것과 같은 이유 — fake 는
    테스트가 소유하고 어댑터는 프로덕션이 소유한다.
    """

    def test_postgres_adapters_conform(self):
        from fcc_test_platform.application.central_test_equipment_list_read_adapter import (
            PostgresCentralTestEquipmentListReadAdapter,
        )
        from fcc_test_platform.application.central_test_equipment_list_write_adapter import (
            PostgresCentralTestEquipmentListWriteAdapter,
        )

        factory = lambda: None  # noqa: E731 — never called; Protocol shape only
        self.assertIsInstance(
            PostgresCentralTestEquipmentListReadAdapter(factory),
            CentralTestEquipmentListReadPort,
        )
        self.assertIsInstance(
            PostgresCentralTestEquipmentListWriteAdapter(factory),
            CentralTestEquipmentListWritePort,
        )

    def test_attach_statement_is_doubly_guarded(self):
        """확정본 재부착 금지 + 이미 붙은 목록의 판 이동 금지 — 조건 두 개."""
        from fcc_test_platform.application.central_test_equipment_list_write_adapter import (
            ATTACH_DRAFT_LIST_SQL,
        )

        self.assertIn('"status" = %s', ATTACH_DRAFT_LIST_SQL)
        self.assertIn('"test_report_id" IS NULL', ATTACH_DRAFT_LIST_SQL)


class TestEmptyListConfirmationIsAtomic(unittest.TestCase):
    """빈 목록 확정의 check-then-act 창 봉인 (2026-08-08).

    서비스는 ``len(list_items())`` 를 읽고 ``confirm_refusal_reason`` 으로 판정한 뒤
    UPDATE 한다. 그런데 옛 조건부 UPDATE 의 WHERE 에는 ``status='draft'`` 만 있어
    **항목 수 축은 원자적이지 않았다** — 판정과 UPDATE 사이에 다른 세션이 항목을
    전부 지우면 빈 목록이 확정되고, 실패는 한참 뒤 성적서 생성 단계에서 났다.

    여기서는 그 창을 **실제 SQLite 로 재현**한다: 항목이 있는 상태로 판정한 뒤,
    UPDATE 직전에 항목을 지운다.
    """

    _DDL = (
        'CREATE TABLE test_equipment_lists ('
        ' id TEXT PRIMARY KEY, status TEXT, confirmed_at TEXT, updated_at TEXT)',
        'CREATE TABLE test_equipment_list_items (id TEXT PRIMARY KEY, list_id TEXT)',
    )

    def setUp(self):
        import tempfile

        from fcc_test_contracts.common.sqlite_connection_factory import (
            SqliteConnectionFactory,
        )

        # 파일 DB + 프로젝트 표준 팩토리를 쓴다. `:memory:` + raw `sqlite3.connect`
        # 이 더 짧지만, 이 저장소는 `sqlite3.connect` 직접 호출을 ratchet 으로 막고
        # 있고(`TestApplyBusyTimeoutPragmaSsotAudit`) baseline 확대는 금지다 —
        # 그 규율을 테스트 편의로 깎을 이유가 없다. 덤으로 실제 운영과 같은
        # PRAGMA(WAL / foreign_keys / busy_timeout) 위에서 조건부 UPDATE 를 본다.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._raw = SqliteConnectionFactory(
            str(pathlib.Path(self._tmp.name) / 'equipment_lists.sqlite3')
        ).create()
        for statement in self._DDL:
            self._raw.execute(statement)
        self._raw.execute(
            "INSERT INTO test_equipment_lists (id, status) VALUES ('L1', 'draft')"
        )
        self._raw.execute(
            "INSERT INTO test_equipment_list_items (id, list_id) VALUES ('I1', 'L1')"
        )
        self._raw.commit()
        self.addCleanup(self._raw.close)

    def _adapter(self):
        from fcc_test_platform.application.central_test_equipment_list_write_adapter import (
            PostgresCentralTestEquipmentListWriteAdapter,
        )

        return PostgresCentralTestEquipmentListWriteAdapter(
            lambda: AdoptedQmarkConnection(self._raw)
        )

    def _status(self):
        return self._raw.execute(
            "SELECT status FROM test_equipment_lists WHERE id = 'L1'"
        ).fetchone()[0]

    def test_a_list_with_items_confirms(self):
        """비-공허성 — 아래 거부가 '무조건 거부'가 아님을 보인다."""
        outcome = self._adapter().confirm_list('L1', confirmed_at='2026-08-08')

        self.assertEqual(outcome['status'], 'confirmed')
        self.assertEqual(self._status(), 'confirmed')

    def test_items_deleted_after_the_check_do_not_confirm(self):
        """결함 그 자체 — 판정 시점엔 항목이 있었고 UPDATE 시점엔 없다."""
        from fcc_test_platform.domain.ports.output.central_test_equipment_list_port import (
            EquipmentListConflictError,
        )

        # 서비스의 사전 판정이 통과하는 상태를 만든 뒤(항목 1개),
        # 경쟁 세션이 항목을 지운 상황을 재현한다.
        self._raw.execute("DELETE FROM test_equipment_list_items WHERE list_id = 'L1'")
        self._raw.commit()

        with self.assertRaises(EquipmentListConflictError):
            self._adapter().confirm_list('L1', confirmed_at='2026-08-08')
        self.assertEqual(self._status(), 'draft', '빈 목록이 확정돼서는 안 된다')

    def test_the_refusal_says_it_is_empty_not_just_draft(self):
        """상태만 말하면 시험원이 '초안인데 왜 확정이 안 되나'로 읽는다."""
        from fcc_test_platform.domain.ports.output.central_test_equipment_list_port import (
            EquipmentListConflictError,
        )
        from fcc_test_kernel.domain.services.test_equipment_list_policy import (
            CONFIRM_REFUSAL_EMPTY_LIST,
        )

        self._raw.execute("DELETE FROM test_equipment_list_items WHERE list_id = 'L1'")
        self._raw.commit()

        with self.assertRaises(EquipmentListConflictError) as caught:
            self._adapter().confirm_list('L1', confirmed_at='2026-08-08')
        self.assertIn(CONFIRM_REFUSAL_EMPTY_LIST, str(caught.exception))

    def test_a_missing_list_is_still_not_found(self):
        """새 갈래가 옛 404 갈래를 삼키지 않았는지 본다."""
        from fcc_test_platform.domain.ports.output.central_test_equipment_list_port import (
            EquipmentListNotFoundError,
        )

        with self.assertRaises(EquipmentListNotFoundError):
            self._adapter().confirm_list('NOPE', confirmed_at='2026-08-08')

    def test_an_already_confirmed_list_still_says_confirmed(self):
        from fcc_test_platform.domain.ports.output.central_test_equipment_list_port import (
            EquipmentListConflictError,
        )

        self._raw.execute(
            "UPDATE test_equipment_lists SET status = 'confirmed' WHERE id = 'L1'"
        )
        self._raw.commit()

        with self.assertRaises(EquipmentListConflictError) as caught:
            self._adapter().confirm_list('L1', confirmed_at='2026-08-08')
        self.assertIn('confirmed', str(caught.exception))

    def test_the_statement_carries_the_item_predicate(self):
        """WHERE 절이 항목 존재를 실제로 포함한다 — 문장 축 파생 단언."""
        from fcc_test_platform.application.central_test_equipment_list_write_adapter import (
            CONFIRM_DRAFT_LIST_SQL,
        )

        self.assertIn('EXISTS', CONFIRM_DRAFT_LIST_SQL)
        self.assertIn('test_equipment_list_items', CONFIRM_DRAFT_LIST_SQL)

    def test_a_confirmed_empty_list_says_confirmed_not_empty(self):
        """거부 우선순위는 도메인 SSOT 가 정한다 — 어댑터가 재발명하지 않는다.

        확정본이면서 항목이 0개인 목록(레거시 데이터)을 `empty_list` 로 보고하면,
        시험원이 항목을 채워 재시도해도 여전히 확정본이라 같은 "비어 있음"이
        반복된다. `confirm_refusal_reason` 은 상태를 먼저 본다.
        """
        from fcc_test_platform.domain.ports.output.central_test_equipment_list_port import (
            EquipmentListConflictError,
        )
        from fcc_test_kernel.domain.services.test_equipment_list_policy import (
            CONFIRM_REFUSAL_ALREADY_CONFIRMED,
            CONFIRM_REFUSAL_EMPTY_LIST,
        )

        self._raw.execute(
            "UPDATE test_equipment_lists SET status = 'confirmed' WHERE id = 'L1'"
        )
        self._raw.execute("DELETE FROM test_equipment_list_items WHERE list_id = 'L1'")
        self._raw.commit()

        with self.assertRaises(EquipmentListConflictError) as caught:
            self._adapter().confirm_list('L1', confirmed_at='2026-08-08')
        message = str(caught.exception)
        self.assertIn(CONFIRM_REFUSAL_ALREADY_CONFIRMED, message)
        self.assertNotIn(CONFIRM_REFUSAL_EMPTY_LIST, message)
