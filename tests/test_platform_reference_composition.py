"""합성 루트가 어댑터의 협력자를 하나도 빠뜨리지 않는다는 것을 파생으로 증명한다.

## 왜 이 파일이 존재하는가

2026-08-08 실측: 중앙 참조 카탈로그는 **도메인 포트 · 어댑터 2종 · 애플리케이션
서비스 · 라우트 핸들러 4개 · API 계약 · OpenAPI 산출물** 이 전부 존재하고 전부
테스트로 덮여 있었다. 그런데 `platform_api_composition.py` 가 `reference_service=`
를 넘기지 않아 **네 operation 전부가 런타임에서**

    RuntimeError('… called but reference_service is not wired')

로 죽었다. 표면 전체가 도달 불가였고, 그 사실을 아는 테스트가 하나도 없었다.

각 조각을 검증하는 테스트는 많았지만 **조각들이 실제로 연결됐는지** 묻는 테스트가
없었기 때문이다. 이 저장소는 같은 결함 부류에 이미 이름을 붙여 뒀다 —
`COMPOSITION_FORWARDED_KWARGS` 의 양방향 드리프트 게이트(`ADR-0001`). 그것은
8-link chain 을 덮지만 플랫폼 어댑터의 최종 조립 지점은 덮지 않는다.

## 왜 손수 만든 목록이 아니라 파생인가

기대 협력자 이름을 여기에 나열하면, 다음에 추가되는 협력자는 목록에도 합성에도
빠진 채 green 으로 남는다 — 즉 **목록 자체가 결함의 공범**이 된다. 그래서 양쪽을
AST 로 읽어 **집합 상등**을 단언한다. 새 협력자를 어댑터에 추가하면 합성에 넘기기
전까지 red 다. baseline 도 allowlist 도 없다.

## 면제 2개와 그 근거

`principal` / `provisioning_dedup` 은 **요청 스코프**다. `with_principal()` 이 요청마다
어댑터를 새로 만들면서 채우므로 프로세스 1회 합성 지점이 넘길 수 있는 값이 아니다.
그 둘을 면제하는 근거는 "합성이 안 넘기더라"가 아니라 **생명주기가 다르다**이고,
아래 `test_the_request_scoped_exemptions_are_actually_request_scoped` 가 그 근거를
소스에서 확인한다 — 면제가 편의로 확장되면 red 다.

`read_service` 는 **위치 인자**로 넘어간다. 키워드 축의 상등을 보는 이 봉인의
대상이 아니며, 위치 인자 부재는 `TypeError` 라 조용히 빠질 수 없다.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC = _PROJECT_ROOT / 'src'
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

_COMPOSITION = resolve_repo_artifact(__file__, 'src/platform_api_composition.py')
_ROUTES = resolve_repo_artifact(__file__, 'src/infrastructure/adapters/driving/api/platform_routes.py')
_SCHEMA = _PROJECT_ROOT / 'docs' / 'platform' / 'central_db_schema.v1.json'

_ADAPTER = 'PlatformApiAdapter'

#: 위치 인자로 넘어가므로 키워드 상등의 대상이 아니다. 부재는 ``TypeError``.
_POSITIONAL = frozenset({'read_service'})

#: 요청 스코프 협력자 — ``with_principal()`` 이 요청마다 채운다. 프로세스 1회
#: 합성이 넘길 수 있는 값이 아니다. 이 집합이 커지면 아래 테스트가 근거를 요구한다.
_REQUEST_SCOPED = frozenset({'principal', 'provisioning_dedup'})


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding='utf-8'), filename=str(path))


def _adapter_keyword_collaborators(tree: ast.Module) -> set[str]:
    """``PlatformApiAdapter.__init__`` 이 받는 키워드 협력자 이름 집합."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == _ADAPTER:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == '__init__':
                    args = item.args
                    names = {a.arg for a in args.args} | {a.arg for a in args.kwonlyargs}
                    return names - {'self'}
    raise AssertionError(f'{_ADAPTER}.__init__ 를 찾지 못했다')


def _composition_call_keywords(tree: ast.Module) -> set[str]:
    """합성 루트의 ``PlatformApiAdapter(...)`` 호출이 넘기는 키워드 이름 집합."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, 'attr', None)
        if name == _ADAPTER:
            return {kw.arg for kw in node.keywords if kw.arg is not None}
    raise AssertionError(f'합성 루트에서 {_ADAPTER}(...) 호출을 찾지 못했다')


class TestEveryAdapterCollaboratorIsComposed(unittest.TestCase):
    """어댑터가 받는 것 == 합성이 넘기는 것 (요청 스코프/위치 인자 제외)."""

    def setUp(self) -> None:
        self.expected = (
            _adapter_keyword_collaborators(_parse(_ROUTES))
            - _POSITIONAL
            - _REQUEST_SCOPED
        )
        self.actual = _composition_call_keywords(_parse(_COMPOSITION))

    def test_the_derivation_is_not_vacuous(self) -> None:
        """양쪽 파생이 실제로 협력자를 찾아냈다 — 빈 집합끼리의 상등이 아니다."""
        self.assertGreater(len(self.expected), 10, '어댑터 협력자 파생이 비었다')
        self.assertGreater(len(self.actual), 10, '합성 키워드 파생이 비었다')

    def test_no_collaborator_is_silently_unwired(self) -> None:
        missing = sorted(self.expected - self.actual)
        self.assertEqual(
            [], missing,
            f'{_ADAPTER} 가 받지만 합성이 넘기지 않는 협력자: {missing}. '
            f'이 협력자를 쓰는 operation 은 전부 런타임 RuntimeError 다.',
        )

    def test_no_composed_keyword_is_unknown_to_the_adapter(self) -> None:
        extra = sorted(self.actual - self.expected)
        self.assertEqual(
            [], extra,
            f'합성이 넘기지만 {_ADAPTER} 가 받지 않는 키워드: {extra}',
        )

    def test_the_reference_service_is_among_them(self) -> None:
        """이 봉인을 만들게 한 바로 그 협력자를 이름으로 고정한다."""
        self.assertIn('reference_service', self.expected)
        self.assertIn('reference_service', self.actual)

    def test_the_request_scoped_exemptions_are_actually_request_scoped(self) -> None:
        """면제 2개가 정말 요청 스코프인지 소스에서 확인한다.

        면제는 "합성이 안 넘기더라"가 아니라 "생명주기가 다르다"여야 한다.
        ``with_principal`` 이 그 둘을 채우는 유일한 지점이다.
        """
        source = _ROUTES.read_text(encoding='utf-8')
        tree = _parse(_ROUTES)
        with_principal = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'with_principal':
                with_principal = node
                break
        self.assertIsNotNone(with_principal, 'with_principal 를 찾지 못했다')
        body = ast.get_source_segment(source, with_principal) or ''
        for name in sorted(_REQUEST_SCOPED):
            self.assertIn(
                name, body,
                f"면제된 '{name}' 이 with_principal 에 등장하지 않는다 — "
                f'요청 스코프라는 면제 근거가 성립하지 않는다.',
            )


class TestTheSealFailsOnASyntheticOffender(unittest.TestCase):
    """비-공허성 통제 — 합성에서 협력자 하나를 빼면 반드시 red 여야 한다."""

    def test_removing_a_composed_kwarg_is_detected(self) -> None:
        source = _COMPOSITION.read_text(encoding='utf-8')
        offender = source.replace(
            '        reference_service=reference_service,\n', '', 1,
        )
        self.assertNotEqual(offender, source, '합성 offender 를 만들지 못했다')

        expected = (
            _adapter_keyword_collaborators(_parse(_ROUTES))
            - _POSITIONAL
            - _REQUEST_SCOPED
        )
        actual = _composition_call_keywords(
            ast.parse(offender, filename='<synthetic-offender>')
        )
        self.assertEqual(
            ['reference_service'], sorted(expected - actual),
            '합성에서 협력자를 제거했는데 봉인이 그것을 잡지 못했다 — '
            '이 봉인은 봉인처럼 읽히는 죽은 코드다.',
        )

    def test_an_unknown_composed_kwarg_is_detected(self) -> None:
        """반대 방향 — 어댑터가 모르는 키워드를 넘기면 잡힌다."""
        source = _COMPOSITION.read_text(encoding='utf-8')
        offender = source.replace(
            '        reference_service=reference_service,\n',
            '        reference_service=reference_service,\n'
            '        nonexistent_service=None,\n',
            1,
        )
        actual = _composition_call_keywords(
            ast.parse(offender, filename='<synthetic-offender>')
        )
        expected = (
            _adapter_keyword_collaborators(_parse(_ROUTES))
            - _POSITIONAL
            - _REQUEST_SCOPED
        )
        self.assertEqual(['nonexistent_service'], sorted(actual - expected))


def _sqlite_type(declared: str) -> str:
    return {
        'integer': 'INTEGER',
        'boolean': 'INTEGER',
        'json': 'TEXT',
    }.get(declared, 'TEXT')


def _create_table_sql(table: str, columns: dict) -> str:
    body = ', '.join(
        f'"{name}" {_sqlite_type(spec.get("type", "text"))}'
        for name, spec in columns.items()
    )
    return f'CREATE TABLE "{table}" ({body})'


class TestTheComposedRuntimeReachesTheReferenceService(unittest.TestCase):
    """합성된 런타임에서 참조 operation 이 배선 RuntimeError 를 내지 않는다.

    AST 상등(위 클래스)은 "넘긴다"를 증명하고, 이것은 "넘긴 것이 실제로 동작한다"를
    증명한다. 둘 다 필요하다 — 이름만 맞고 타입이 틀린 조립은 AST 가 못 본다.

    중앙 스키마는 ``docs/platform/central_db_schema.v1.json`` 에서 **파생**해
    만든다. 컬럼 목록을 여기에 적으면 그것이 두 번째 스키마가 된다.
    """

    @staticmethod
    def _offered_provider_id() -> str:
        """Which provider this deployment's picker offers, asked of the registry.

        Built through the composition root rather than by importing the provider
        builder: the root is the single place allowed to know that builder, so
        importing it here would both spell the fact a second way and pull a
        platform-lane test across into the provider package.
        """
        from fcc_test_platform.central_db_config import CentralDbConfig
        from fcc_test_contracts.common.auth_config import HttpAuthConfig
        from fcc_test_platform.application.runtime_config import PlatformApiConfig
        from fcc_test_platform.api_composition import create_platform_runtime

        def _no_connection():  # pragma: no cover — never called
            raise AssertionError('the registry must not need a database')

        runtime = create_platform_runtime(
            PlatformApiConfig(
                central=CentralDbConfig(
                    database_url='postgresql://unused/registry-only',
                    provider_id='unused-for-this-probe',
                ),
                auth=HttpAuthConfig(),
                allow_insecure=True,
            ),
            connection_factory=_no_connection,
        )
        offered = runtime.api_adapter._provider_ui_descriptor_registry.provider_ids()  # noqa: SLF001
        assert offered, 'the composed registry offers no provider at all'
        return offered[0]

    def setUp(self) -> None:
        self.offered_provider_id = self._offered_provider_id()
        schema = json.loads(_SCHEMA.read_text(encoding='utf-8'))
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / 'central.sqlite3')
        # 같은 shim 을 쓴다 — raw ``sqlite3.connect`` 사이트를 하나 더 만들지
        # 않기 위해서다(``SqliteConnectionFactory`` SSOT 의 ratchet 은 내려가기만
        # 한다). 그리고 테이블을 만드는 연결과 어댑터가 읽는 연결이 같은 종류라
        # 이 테스트가 조립을 확인하는 경로가 실제 경로와 어긋나지 않는다.
        from tests.support.central_pg_sqlite_shim import QmarkConnection

        connection = QmarkConnection(self.db_path)
        try:
            cursor = connection.cursor()
            for table in ('providers', 'reference_revisions', 'reference_entries'):
                spec = schema['tables'].get(table)
                if spec is None:
                    continue
                cursor.execute(_create_table_sql(table, spec['columns']))
            # 참조 operation 은 2026-08-25 부터 provider 를 **먼저** 해소하므로,
            # 등록 행이 없으면 이 테스트가 배선이 아니라 미등록 거부를 보게 된다.
            # id 는 descriptor 레지스트리 SSOT 에서 읽는다 — 리터럴로 적으면
            # 이 파일이 정확히 이 웨이브가 없애는 드리프트를 다시 만든다.
            cursor.execute(
                'INSERT INTO "providers" ("id", "provider_id") VALUES (?, ?)',
                ('11111111-1111-1111-1111-111111111111', self.offered_provider_id),
            )
            cursor.close()
            connection.commit()
        finally:
            connection.close()
        self.addCleanup(self._tmp.cleanup)

    def _runtime(self):
        from fcc_test_platform.central_db_config import CentralDbConfig
        from fcc_test_contracts.common.auth_config import HttpAuthConfig
        from fcc_test_platform.application.runtime_config import PlatformApiConfig
        from fcc_test_platform.api_composition import create_platform_runtime
        from tests.support.central_pg_sqlite_shim import QmarkConnection

        config = PlatformApiConfig(
            central=CentralDbConfig(
                database_url='postgresql://unused/only-the-factory-is-used',
                provider_id=self.offered_provider_id,
            ),
            auth=HttpAuthConfig(),
            allow_insecure=True,
        )
        return create_platform_runtime(
            config, connection_factory=lambda: QmarkConnection(self.db_path),
        )

    def test_listing_revisions_does_not_raise_the_wiring_error(self) -> None:
        runtime = self._runtime()
        adapter = runtime.api_adapter
        # 인가는 이 테스트의 대상이 아니다 — 배선만 본다.
        adapter.authorize = lambda *a, **k: None  # type: ignore[method-assign]
        rows = adapter.list_reference_revisions(self.offered_provider_id)
        self.assertIsInstance(rows, dict)

    def test_the_composed_runtime_knows_which_providers_the_screen_offers(self) -> None:
        """`offered_provider_ids` 가 실제로 주입됐는지 — 합성만이 증명할 수 있다.

        서비스 단위 테스트는 자기가 만든 콜러블을 넘기므로 합성 루트가 그것을
        빠뜨려도 green 이다. 여기서만 "화면이 제공하는 집합"과 "중앙 등록부"가
        같은 프로세스에서 만난다.
        """
        from domain.ports.output.central_reference_port import (
            ReferenceProviderNotFoundError,
            ReferenceProviderNotRegisteredError,
        )

        adapter = self._runtime().api_adapter
        adapter.authorize = lambda *a, **k: None  # type: ignore[method-assign]
        with self.assertRaises(ReferenceProviderNotFoundError) as caught:
            adapter.list_reference_revisions('a-provider-nobody-offers')
        self.assertNotIsInstance(
            caught.exception, ReferenceProviderNotRegisteredError,
            'the composed service could not tell an unoffered id from an '
            'unregistered one, so offered_provider_ids was never wired',
        )

    def test_the_composed_runtime_wires_the_reference_resolver_registry(self) -> None:
        runtime = self._runtime()
        service = runtime.api_adapter._project_result_reference_service  # noqa: SLF001
        resolver = service._provider_resolver  # noqa: SLF001
        self.assertEqual(resolver.provider_ids(), (self.offered_provider_id,))

    def test_the_unwired_shape_still_raises(self) -> None:
        """비-공허성 — 서비스를 떼면 옛 RuntimeError 가 그대로 난다."""
        runtime = self._runtime()
        adapter = runtime.api_adapter
        adapter.authorize = lambda *a, **k: None  # type: ignore[method-assign]
        adapter._reference_service = None  # noqa: SLF001 — 의도적 offender
        with self.assertRaises(RuntimeError) as caught:
            adapter.list_reference_revisions(self.offered_provider_id)
        self.assertIn('reference_service is not wired', str(caught.exception))


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
