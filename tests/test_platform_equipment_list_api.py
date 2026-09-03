"""성적서 §6 장비목록 플랫폼 API 계약 + 경계 봉인 (2026-08-07).

이 파일이 지키는 것:

**계약(Phase 3)**
- 5 operation 이 routes / permissions / operations / path-params 에 모두 있다
- **신규 grantable 토큰 0** — read=``platform:read`` / write=``platform:claim``.
  새 토큰은 ``rbac_role_grants`` ↔ ``permissions.ts`` ↔ Keycloak realm bijection
  3자 갱신을 강제한다
- 모든 operation 이 **명명된** response 스키마를 갖는다(인라인 폴백 미사용)
- 어휘 enum 이 도메인에서 파생했다(계약에 리터럴 재선언 0)
- ``ReplaceTestEquipmentListItemsRequest`` 에 ``sort_order`` property 0
- 생성 아티팩트에 ``nullable`` 키 0(=union 으로 정규화) + 맨몸 object 0
- 신규 ``ErrorCode`` 멤버 0

**경계(Phase 4)**
- 어댑터 메서드 5개가 모두 ``authorize`` 를 먼저 호출한다
- 3 예외가 에러표와 ``route_error_boundary`` **양쪽**에 등재된다
  (한쪽만이면 500 으로 샌다)
- ``api_error_status`` 가 404/409/503/400 으로 매핑한다
- 핸들러가 ``route_handlers`` 에 전부 등재된다
- 미배선 서비스는 loud ``RuntimeError``

Owned by ``/verify-report-equipment-list-central``.
"""
from __future__ import annotations
from tests._moved_module_source import moved_module_source  # noqa: E402

import ast
import json
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fcc_test_contracts.common.tree_artifacts import (
    resolve_dependency_artifact,
    resolve_repo_artifact,
)  # noqa: E402

from fcc_test_contracts.common.api_error_codes import ErrorCode  # noqa: E402
from fcc_test_kernel.application.central_contract.api_contracts import (  # noqa: E402
    PLATFORM_API_OPERATIONS,
    PLATFORM_API_PATH_PARAMS,
    PLATFORM_API_PERMISSIONS,
    PLATFORM_API_ROUTES,
    PLATFORM_API_SCHEMAS,
)
from fcc_test_kernel.domain.services.test_equipment_list_policy import (  # noqa: E402
    TEST_ITEM_KEYS,
    ItemType,
    ListStatus,
)

ARTIFACT = resolve_dependency_artifact('docs/api/platform-api.openapi.json')
ROUTES_MODULE = (
    resolve_repo_artifact(__file__, 'src/infrastructure/adapters/driving/api/platform_routes.py')
)
COMPOSITION_MODULE = resolve_repo_artifact(__file__, 'src/platform_api_composition.py')

_READ_OPS = ("list_test_equipment_lists", "get_test_equipment_list")
_WRITE_OPS = (
    "create_test_equipment_list",
    "replace_test_equipment_list_items",
    "attach_test_equipment_list",
    "confirm_test_equipment_list",
)
_ALL_OPS = _READ_OPS + _WRITE_OPS

_EXCEPTIONS = (
    "EquipmentListNotFoundError",
    "EquipmentListConflictError",
    "CentralTestEquipmentListError",
)


def _artifact() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


class TestContractCompleteness(unittest.TestCase):
    def test_operations_routes_permissions_present(self):
        for op in _ALL_OPS:
            self.assertIn(op, PLATFORM_API_OPERATIONS, f"{op} missing operation")
            self.assertIn(op, PLATFORM_API_ROUTES, f"{op} missing route")
            self.assertIn(op, PLATFORM_API_PERMISSIONS, f"{op} missing permission")

    def test_path_param_declared(self):
        """미선언이면 ``_path_parameters_for`` 가 loud 실패한다."""
        self.assertIn("equipment_list_id", PLATFORM_API_PATH_PARAMS)
        self.assertEqual(PLATFORM_API_PATH_PARAMS["equipment_list_id"]["format"], "uuid")

    def test_routes_are_project_scoped(self):
        base = "/platform/projects/{project_id}/equipment-lists"
        self.assertEqual(PLATFORM_API_ROUTES["list_test_equipment_lists"], ("GET", base))
        self.assertEqual(PLATFORM_API_ROUTES["create_test_equipment_list"], ("POST", base))
        self.assertEqual(
            PLATFORM_API_ROUTES["get_test_equipment_list"],
            ("GET", base + "/{equipment_list_id}"),
        )
        self.assertEqual(
            PLATFORM_API_ROUTES["replace_test_equipment_list_items"],
            ("PUT", base + "/{equipment_list_id}/items"),
        )
        self.assertEqual(
            PLATFORM_API_ROUTES["attach_test_equipment_list"],
            ("POST", base + "/{equipment_list_id}/attach"),
        )
        self.assertEqual(
            PLATFORM_API_ROUTES["confirm_test_equipment_list"],
            ("POST", base + "/{equipment_list_id}/confirm"),
        )

    def test_every_operation_names_a_declared_response_schema(self):
        """미선언 response 는 인라인 object 폴백으로 떨어져 ratchet 을 키운다."""
        for op in _ALL_OPS:
            response = PLATFORM_API_OPERATIONS[op]["response"]
            self.assertIn(response, PLATFORM_API_SCHEMAS, f"{op} response undeclared")

    def test_request_schemas_are_declared(self):
        for op in (
            "create_test_equipment_list",
            "replace_test_equipment_list_items",
            "attach_test_equipment_list",
        ):
            request = PLATFORM_API_OPERATIONS[op]["request"]
            self.assertIn(request, PLATFORM_API_SCHEMAS, f"{op} request undeclared")

    def test_read_operations_have_no_request_body(self):
        for op in _READ_OPS + ("confirm_test_equipment_list",):
            self.assertIsNone(PLATFORM_API_OPERATIONS[op]["request"])

    def test_error_responses_declared(self):
        for op in _ALL_OPS:
            errors = PLATFORM_API_OPERATIONS[op].get("error_responses", {})
            self.assertIn("404", errors, f"{op} missing 404")
        for op in _WRITE_OPS:
            self.assertIn("409", PLATFORM_API_OPERATIONS[op]["error_responses"], op)


class TestPermissionsReuseExistingTokens(unittest.TestCase):
    """신규 grantable 토큰 0 — bijection 무변경."""

    def test_reads_use_platform_read(self):
        for op in _READ_OPS:
            self.assertEqual(PLATFORM_API_PERMISSIONS[op], "platform:read")

    def test_writes_use_platform_claim(self):
        for op in _WRITE_OPS:
            self.assertEqual(PLATFORM_API_PERMISSIONS[op], "platform:claim")

    def test_no_bespoke_equipment_token(self):
        for op in _ALL_OPS:
            token = PLATFORM_API_PERMISSIONS[op]
            self.assertNotIn("equipment", token, f"{op} invented a new token: {token}")

    def test_claim_token_is_an_existing_engineer_tier_token(self):
        """선례가 실제로 있어야 재사용이라 부를 수 있다."""
        self.assertEqual(PLATFORM_API_PERMISSIONS["start_chamber_measurement"], "platform:claim")


class TestVocabularyDerivesFromTheDomain(unittest.TestCase):
    def test_item_type_enum_matches_domain(self):
        schema = PLATFORM_API_SCHEMAS["TestEquipmentListItem"]["properties"]["item_type"]
        self.assertEqual(set(schema["enum"]), {m.value for m in ItemType})

    def test_status_enum_matches_domain(self):
        schema = PLATFORM_API_SCHEMAS["TestEquipmentListSummary"]["properties"]["status"]
        self.assertEqual(set(schema["enum"]), {m.value for m in ListStatus})

    def test_table_spec_enum_matches_domain(self):
        schema = PLATFORM_API_SCHEMAS["TestEquipmentTableSpec"]["properties"]["item_type"]
        self.assertEqual(set(schema["enum"]), {m.value for m in ItemType})

    def test_item_properties_cover_the_persisted_fields(self):
        from fcc_test_kernel.domain.services.test_equipment_list_policy import ITEM_PERSISTED_FIELDS

        props = PLATFORM_API_SCHEMAS["TestEquipmentListItem"]["properties"]
        for field in ITEM_PERSISTED_FIELDS:
            self.assertIn(field, props)

    def test_create_request_test_item_key_is_a_closed_vocabulary(self):
        """자유 문자열이면 성적서 어느 편에도 대응하지 않는 목록이 만들어진다."""
        schema = PLATFORM_API_SCHEMAS["CreateTestEquipmentListRequest"]["properties"]
        self.assertEqual(schema["test_item_key"]["enum"], list(TEST_ITEM_KEYS))

    def test_summary_test_item_key_is_a_closed_vocabulary(self):
        schema = PLATFORM_API_SCHEMAS["TestEquipmentListSummary"]["properties"]
        self.assertEqual(schema["test_item_key"]["enum"], list(TEST_ITEM_KEYS))

    def test_test_item_enum_preserves_domain_declaration_order(self):
        """순서는 성적서 파일 순서(E6~E9)다 — 프론트 선택지가 이 순서로 그려진다.

        집합 비교만 하면 순서 드리프트를 놓친다.
        """
        for name in ("CreateTestEquipmentListRequest", "TestEquipmentListSummary"):
            with self.subTest(schema=name):
                self.assertEqual(
                    PLATFORM_API_SCHEMAS[name]["properties"]["test_item_key"]["enum"],
                    list(TEST_ITEM_KEYS),
                )

    def test_no_literal_test_item_vocabulary_in_the_contract_module(self):
        """계약이 어휘를 리터럴로 재선언하면 도메인과 조용히 갈라진다."""
        module = moved_module_source('fcc_test_kernel.application.central_contract.api_contracts')
        tree = ast.parse(module.read_text(encoding="utf-8"))
        vocabulary = set(TEST_ITEM_KEYS)
        offenders: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                continue
            literals = {
                elt.value
                for elt in node.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
            if vocabulary.issubset(literals):
                offenders.append(getattr(node, "lineno", 0))
        self.assertEqual(
            offenders, [], f"api_contracts.py 가 시험항목 어휘를 리터럴로 재선언했다: {offenders}"
        )

    def test_the_literal_vocabulary_guard_is_not_vacuous(self):
        planted = ast.parse(f"KEYS = {list(TEST_ITEM_KEYS)!r}\n")
        found = [
            node
            for node in ast.walk(planted)
            if isinstance(node, (ast.Tuple, ast.List, ast.Set))
            and set(TEST_ITEM_KEYS).issubset(
                {
                    elt.value
                    for elt in node.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                }
            )
        ]
        self.assertNotEqual(found, [], "합성 offender 를 탐지하지 못했다")


class TestListResponseCarriesTheVocabulary(unittest.TestCase):
    """생성 폼의 선택지는 서버 응답에서 온다.

    생성된 TS 타입은 **타입 레벨 union** 이라 런타임 배열을 주지 못한다. 어휘를
    응답에 싣지 않으면 프론트가 배열을 적는 수밖에 없고, 그 순간 같은 어휘가
    TS/Python 두 곳으로 쪼개진다 — 상세 응답이 ``tables`` 로 열 순서를 내려보내는
    것과 같은 결정이다.
    """

    def test_list_operation_returns_the_collection_envelope(self):
        operation = PLATFORM_API_OPERATIONS["list_test_equipment_lists"]
        self.assertEqual(operation["response"], "TestEquipmentListCollection")

    def test_collection_requires_both_lists_and_vocabulary(self):
        schema = PLATFORM_API_SCHEMAS["TestEquipmentListCollection"]
        self.assertEqual(sorted(schema["required"]), ["lists", "test_items"])

    def test_collection_vocabulary_is_the_domain_vocabulary(self):
        schema = PLATFORM_API_SCHEMAS["TestEquipmentListCollection"]["properties"]
        self.assertEqual(schema["test_items"]["items"]["enum"], list(TEST_ITEM_KEYS))

    def test_the_bare_array_response_is_gone(self):
        """옛 배열 스키마가 남아 있으면 다음 사람이 그것을 다시 쓴다."""
        self.assertNotIn("TestEquipmentListList", PLATFORM_API_SCHEMAS)


class TestSortOrderIsServerOwned(unittest.TestCase):
    def test_item_input_has_no_sort_order(self):
        props = PLATFORM_API_SCHEMAS["TestEquipmentListItemInput"]["properties"]
        self.assertNotIn("sort_order", props)

    def test_replace_request_body_has_no_sort_order(self):
        body = json.dumps(PLATFORM_API_SCHEMAS["ReplaceTestEquipmentListItemsRequest"])
        self.assertNotIn("sort_order", body)

    def test_item_output_has_sort_order(self):
        """응답에는 있다 — 서버가 부여한 결과를 클라이언트가 읽는다."""
        props = PLATFORM_API_SCHEMAS["TestEquipmentListItem"]["properties"]
        self.assertIn("sort_order", props)

    def test_create_request_has_no_status(self):
        props = PLATFORM_API_SCHEMAS["CreateTestEquipmentListRequest"]["properties"]
        self.assertNotIn("status", props)


class TestSchemasAreClosed(unittest.TestCase):
    def test_object_schemas_forbid_additional_properties(self):
        for name, schema in PLATFORM_API_SCHEMAS.items():
            if not name.startswith(("TestEquipment", "CreateTestEquipment", "ReplaceTestEquipment", "ConfirmTestEquipment")):
                continue
            if schema.get("type") != "object":
                continue
            self.assertIs(
                schema.get("additionalProperties"), False, f"{name} is not closed"
            )


class TestGeneratedArtifact(unittest.TestCase):
    """생성물이 계약의 두 결함 계열을 재생산하지 않는지."""

    def _equipment_schemas(self) -> dict:
        schemas = _artifact()["components"]["schemas"]
        return {
            k: v
            for k, v in schemas.items()
            if "TestEquipment" in k
        }

    def test_paths_present(self):
        paths = [p for p in _artifact()["paths"] if "equipment-lists" in p]
        self.assertEqual(len(paths), 5, paths)

    def test_nullable_is_emitted_as_a_union(self):
        """``nullable: true`` 는 union 으로 정규화되어야 한다 — 형제 키워드로 남으면
        openapi-typescript 가 ``never`` 로 붕괴시킨다."""
        blob = json.dumps(self._equipment_schemas())
        self.assertNotIn('"nullable"', blob)
        prop = self._equipment_schemas()["TestEquipmentListSummary"]["properties"]["test_report_id"]
        self.assertEqual(prop.get("type"), ["string", "null"])

    def test_no_intersection_shaped_nullable(self):
        blob = json.dumps(self._equipment_schemas())
        self.assertNotIn('"allOf"', blob)

    def test_no_bare_free_form_object(self):
        def bare(node, path=""):
            found = []
            if isinstance(node, dict):
                if (
                    node.get("type") == "object"
                    and "properties" not in node
                    and "additionalProperties" not in node
                ):
                    found.append(path)
                for key, value in node.items():
                    found += bare(value, f"{path}/{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    found += bare(value, f"{path}[{index}]")
            return found

        self.assertEqual(bare(self._equipment_schemas()), [])

    def test_bodies_and_responses_are_refs(self):
        """path parameter 는 인라인이 정상(경로 SSOT 파생) — body/response 는 $ref."""
        spec = _artifact()
        for path, item in spec["paths"].items():
            if "equipment-lists" not in path:
                continue
            for method, operation in item.items():
                body = operation.get("requestBody")
                if body:
                    schema = body["content"]["application/json"]["schema"]
                    self.assertIn("$ref", schema, f"{method} {path} inline request")
                for code, response in operation.get("responses", {}).items():
                    for content in response.get("content", {}).values():
                        schema = content.get("schema", {})
                        self.assertIn("$ref", schema, f"{method} {path} {code} inline response")

    def test_problem_json_on_errors(self):
        spec = _artifact()
        for path, item in spec["paths"].items():
            if "equipment-lists" not in path:
                continue
            for operation in item.values():
                for code, response in operation.get("responses", {}).items():
                    if not code.startswith(("4", "5")):
                        continue
                    self.assertIn("application/problem+json", response.get("content", {}), code)


class TestNoNewErrorCodes(unittest.TestCase):
    def test_reuses_existing_error_codes(self):
        for name in ("NOT_FOUND", "CONFLICT", "UPSTREAM_UNAVAILABLE", "VALIDATION_ERROR"):
            self.assertTrue(hasattr(ErrorCode, name), name)

    def test_no_equipment_specific_error_code(self):
        for member in ErrorCode:
            self.assertNotIn("EQUIPMENT", member.name.upper())


class TestRouteBoundaryWiring(unittest.TestCase):
    """Phase 4 — 에러표와 boundary 양쪽 등재 + authorize 선행."""

    def _routes_source(self) -> str:
        return ROUTES_MODULE.read_text(encoding="utf-8")

    def test_exceptions_registered_in_error_table(self):
        """AST 로 튜플 **원소**를 읽는다 — 문자열 포함 검사는 주석에 무력화된다.

        그 표 구간의 주석이 이미 예외 이름을 담고 있어서, 텍스트 검사만 하면
        해당 튜플 행을 지워도 통과한다(가드가 공허해진다).
        """
        tree = ast.parse(self._routes_source())
        registered: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign):
                continue
            target = node.target
            if not (isinstance(target, ast.Name) and target.id == "_PLATFORM_ERROR_CODE_TABLE"):
                continue
            for element in getattr(node.value, "elts", []):
                first = getattr(element, "elts", [None])[0]
                if isinstance(first, ast.Name):
                    registered.add(first.id)
        self.assertTrue(registered, "_PLATFORM_ERROR_CODE_TABLE 을 AST 로 읽지 못했다")
        for name in _EXCEPTIONS:
            self.assertIn(name, registered, f"{name} missing from _PLATFORM_ERROR_CODE_TABLE")

    def test_the_error_table_guard_is_not_vacuous(self):
        """합성 offender — 주석에만 이름이 있고 행이 없으면 반드시 red."""
        planted = ast.parse(
            "_PLATFORM_ERROR_CODE_TABLE: tuple = (\n"
            "    # EquipmentListConflictError 는 RuntimeError 라 위에 둔다\n"
            "    (SomethingElse, ErrorCode.CONFLICT),\n"
            ")\n"
        )
        registered: set[str] = set()
        for node in ast.walk(planted):
            if isinstance(node, ast.AnnAssign):
                for element in getattr(node.value, "elts", []):
                    first = getattr(element, "elts", [None])[0]
                    if isinstance(first, ast.Name):
                        registered.add(first.id)
        self.assertNotIn("EquipmentListConflictError", registered)

    def test_exceptions_registered_in_route_error_boundary(self):
        """에러표에만 있고 boundary 에 없으면 500 으로 샌다."""
        source = self._routes_source()
        boundary = source.split("def route_error_boundary", 1)[1]
        for name in _EXCEPTIONS:
            self.assertIn(name, boundary, f"{name} missing from route_error_boundary")

    def test_every_adapter_method_authorizes_first(self):
        """``PlatformApiAdapter`` 메서드가 첫 문장에서 authorize 한다.

        모듈 레벨의 동명 라우트 핸들러와 구분해야 한다 — 그 핸들러는 어댑터에
        위임할 뿐이고, 인가는 어댑터가 소유한다(토큰 ∪ 멤버십 union seam).
        """
        tree = ast.parse(self._routes_source())
        adapter = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "PlatformApiAdapter"
        )
        methods = set(_ALL_OPS)
        seen: set[str] = set()
        for node in adapter.body:
            if not isinstance(node, ast.FunctionDef) or node.name not in methods:
                continue
            seen.add(node.name)
            body = [
                n
                for n in node.body
                if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
            ]
            self.assertTrue(body, f"{node.name} has an empty body")
            self.assertIn(
                "authorize", ast.dump(body[0]), f"{node.name} does not authorize first"
            )
        self.assertEqual(seen, methods, f"missing adapter methods: {methods - seen}")

    def test_handlers_registered(self):
        source = self._routes_source()
        handlers = source.split("route_handlers", 1)[1]
        for op in _ALL_OPS:
            self.assertIn(f"'{op}'", handlers, f"{op} not registered in route_handlers")

    def test_no_direct_http_exception_in_equipment_handlers(self):
        """전부 boundary 경유 — 즉석 {detail: str} 응답 0."""
        source = self._routes_source()
        for op in _ALL_OPS:
            marker = f"def {op}("
            if marker not in source:
                continue
            body = source.split(marker, 1)[1].split("\n    def ", 1)[0]
            self.assertNotIn("HTTPException(", body, f"{op} raises HTTPException directly")


class TestCompositionWiring(unittest.TestCase):
    def test_service_is_composed(self):
        source = COMPOSITION_MODULE.read_text(encoding="utf-8")
        self.assertIn("CentralTestEquipmentListService", source)
        self.assertIn("equipment_list_service", source)

    def test_no_module_level_psycopg_import(self):
        source = COMPOSITION_MODULE.read_text(encoding="utf-8")
        self.assertFalse(re.search(r"^import psycopg", source, re.M))
        self.assertFalse(re.search(r"^from psycopg", source, re.M))


class TestErrorStatusMapping(unittest.TestCase):
    def test_status_mapping(self):
        from fcc_test_platform.api.platform_routes import api_error_status
        from fcc_test_platform.application.central_project_service import ProjectNotFoundError
        from fcc_test_platform.domain.ports.output.central_test_equipment_list_port import (
            CentralTestEquipmentListError,
            EquipmentListConflictError,
            EquipmentListNotFoundError,
        )

        self.assertEqual(api_error_status(EquipmentListNotFoundError("x")), 404)
        self.assertEqual(api_error_status(ProjectNotFoundError("x")), 404)
        self.assertEqual(api_error_status(EquipmentListConflictError("x")), 409)
        self.assertEqual(api_error_status(CentralTestEquipmentListError("x")), 503)
        self.assertEqual(api_error_status(ValueError("x")), 400)


if __name__ == "__main__":
    unittest.main()
