"""``Field.default`` 는 「기본값」이 아니다 — 선언이 없으면 «센티널»이다.

두 런타임 설정은 환경변수가 비면 dataclass 필드의 기본값으로 되돌아간다. 그 되돌림이
``defaults['app_title'].default`` 로 쓰여 있었고, mypy 는 그 자리를 8건의
``Any | Literal[_MISSING_TYPE.MISSING]`` 로 짚었다.

⚠️ 필드에 기본값이 **선언돼 있지 않으면** 그 자리에는 값이 아니라
``dataclasses.MISSING`` 객체가 들어 있고 **예외 없이** ``str`` 필드로 흘러간다.
앱 제목이 ``<dataclasses._MISSING_TYPE object at 0x7f…>`` 가 된다.

── 오늘 왜 안 터졌나, 그리고 그 안전이 왜 «우연»인가 (실측 2026-09-06) ────────────
이 패턴으로 읽는 필드는 16개이고 **그중 기본값이 없는 필드는 0개**다. 그래서 오늘
센티널은 흐르지 않는다. 그러나 그것은 **두 목록이 우연히 포개진 것**이고 아무도 그
포개짐을 검사하지 않았다:

  * ``HeadlessApiConfig.db_path`` 는 **이미 기본값이 없는 필드**다(맨 앞이라 dataclass
    정의는 통과한다) — 즉 「기본값 없는 필드」가 이 저장소에 실재한다.
  * 기본값 있는 필드에서 그것을 지우면 파이썬이
    ``TypeError: non-default argument … follows default argument`` 로 클래스 정의를
    거절한다. **오늘의 방어는 「필드 순서」에 얹혀 있다** — 순서가 바뀌거나 새 필드가
    앞쪽에 들어오는 날 그 방어는 조용히 사라진다.

이 파일은 그 우연을 **검사**로 바꾼다.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import unittest
from pathlib import Path

from fcc_test_platform.application.declared_defaults import declared_default
import fcc_test_platform.application.session.runtime_config as _session_config
import fcc_test_platform.application.headless.runtime_config as _headless_config


REPO_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_DIR = REPO_ROOT / 'fcc_test_platform' / 'application'

#: 이 패턴을 쓰는 두 모듈 — 검사가 실제로 무엇을 보는지 이름으로 적는다.
_CONFIG_MODULES = (_session_config, _headless_config)


@dataclasses.dataclass
class _Probe:
    """맨 앞 필드에 기본값이 없다 — 파이썬이 **허용하는** 배치다."""

    no_default: str
    with_default: str = 'declared'
    wrong_kind: int = 7


class TestTheHelperRefusesTheSentinel(unittest.TestCase):

    def test_a_declared_default_comes_back(self):
        self.assertEqual(
            declared_default(_Probe.__dataclass_fields__, 'with_default', str),
            'declared',
        )

    def test_a_field_without_a_default_raises_instead_of_returning_the_sentinel(self):
        """⚠️ 이 검사가 이 파일의 존재 이유다.

        옛 형태(``fields[name].default``)는 여기서 ``dataclasses.MISSING`` 객체를
        **말없이** 돌려줬고, 그것이 ``str`` 필드에 그대로 앉았다.
        """
        raw = _Probe.__dataclass_fields__['no_default'].default
        self.assertIs(
            raw, dataclasses.MISSING,
            '전제가 깨졌다 — 이 필드에 기본값이 생겼다면 이 검사는 아무것도 묻지 않는다',
        )
        with self.assertRaises(ValueError) as refused:
            declared_default(_Probe.__dataclass_fields__, 'no_default', str)
        self.assertIn('MISSING', str(refused.exception))
        self.assertIn('no_default', str(refused.exception))

    def test_a_default_of_the_wrong_type_raises(self):
        """``Field.default`` 를 mypy 는 ``Any`` 로밖에 못 본다 — 그 검사를 런타임으로 옮긴다."""
        with self.assertRaises(TypeError) as refused:
            declared_default(_Probe.__dataclass_fields__, 'wrong_kind', str)
        self.assertIn('wrong_kind', str(refused.exception))

    def test_an_unknown_field_name_names_the_known_ones(self):
        with self.assertRaises(KeyError) as refused:
            declared_default(_Probe.__dataclass_fields__, 'nope', str)
        self.assertIn('with_default', str(refused.exception))


class TestNoConfigStillReadsFieldDefaultDirectly(unittest.TestCase):
    """드리프트 가드 — 내일 추가되는 설정 필드가 다시 센티널을 읽지 못하게 한다."""

    @staticmethod
    def _raw_reads(source: str) -> list[int]:
        """``<something>[…].default`` **속성 접근**의 줄 번호.

        ⚠️ 정규식이 아니라 AST 다. 헬퍼 모듈의 docstring 이 옛 형태를 **인용**하고
        있어서, 문자열을 세면 그 설명 자체가 위반으로 잡힌다 —
        「무엇을 설명했나」와 「무엇을 실행하나」는 다른 값이다.
        """
        return [
            node.lineno
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute)
            and node.attr == 'default'
            and isinstance(node.value, ast.Subscript)
        ]

    def test_the_runtime_configs_go_through_the_helper(self):
        offenders: list[str] = []
        for module in _CONFIG_MODULES:
            path = Path(inspect.getsourcefile(module))
            for lineno in self._raw_reads(path.read_text(encoding='utf-8')):
                offenders.append(f'{path.relative_to(REPO_ROOT).as_posix()}:{lineno}')
        self.assertEqual(
            offenders, [],
            'dataclass 필드의 기본값을 직접 읽는 자리가 생겼다. 그 값은 선언이 없으면 '
            'dataclasses.MISSING 센티널이고 «예외 없이» 필드로 흘러간다 — '
            'declared_default(defaults, 이름, 기대타입) 을 써라:\n  '
            + '\n  '.join(offenders),
        )

    def test_the_helper_is_actually_used(self):
        """비공허성 — 위 검사가 「아무도 안 쓴다」로 초록이 되는 것을 막는다."""
        used = 0
        for module in _CONFIG_MODULES:
            source = Path(inspect.getsourcefile(module)).read_text(encoding='utf-8')
            used += sum(
                1 for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'declared_default'
            )
        self.assertGreaterEqual(
            used, 16,
            f'declared_default 호출이 {used}개다 — 2026-09-06 실측은 16개였다. '
            '줄었다면 누군가 옛 형태로 되돌렸거나 필드가 사라졌다.',
        )


class TestEveryFallbackFieldStillDeclaresADefault(unittest.TestCase):
    """헬퍼가 **오늘** 죽지 않는다는 것도 재 둔다 — 설정 로딩은 프로세스 시작 지점이다."""

    #: 헬퍼가 던지는 거절의 지문. 이것으로 «헬퍼의 거절»과 «설정의 정당한 요구»를 가른다.
    _HELPER_REFUSALS = ('declares no default', 'was expected here')

    def test_no_config_falls_back_to_a_sentinel_from_an_empty_environment(self):
        """⚠️ 「빈 환경에서 로딩이 성공하는가」를 묻는 것이 **아니다.**

        ``HeadlessApiConfig`` 는 빈 환경에서 정당하게 거절한다
        (``FCC_HEADLESS_DB_PATH is required``) — ``db_path`` 는 기본값이 없는 필드이고,
        그 요구는 이 축이 아니라 그 설정의 계약이다. 여기서 묻는 것은 오직
        **되돌림이 센티널에 닿는가**이고, 그것은 헬퍼가 던지는 두 문구로만 판정한다.
        도메인 거절을 실패로 세면 이 검사는 이 축과 무관한 이유로 빨개진다.
        """
        for module in _CONFIG_MODULES:
            config_cls = next(
                value for value in vars(module).values()
                if dataclasses.is_dataclass(value) and hasattr(value, 'from_env')
                and value.__module__ == module.__name__
            )
            with self.subTest(config=config_cls.__name__):
                try:
                    config_cls.from_env({})
                except Exception as exc:  # noqa: BLE001 — 지문으로 축을 가른다
                    message = str(exc)
                    hit = [f for f in self._HELPER_REFUSALS if f in message]
                    self.assertEqual(
                        hit, [],
                        f'{config_cls.__name__}.from_env({{}}) 의 되돌림이 선언되지 않은 '
                        f'기본값에 닿았다 — {type(exc).__name__}: {message}',
                    )


class TestTheSelectedSourceColumnsMatchItsTypedDict(unittest.TestCase):
    """``cast(SelectedSource, row)`` 를 떠받치는 두 축 중 «두 번째».

    ``central_result_selection_adapter.selected_source`` 는 SELECT 결과 dict 를
    ``SelectedSource``(TypedDict)로 cast 한다. 그 cast 가 거짓말이 아니려면 둘이 필요하다:

      ① 어댑터가 **런타임에** 그 행의 키 집합이 ``SELECTED_SOURCE_COLUMNS`` 와 같은지
         확인한다 — 그 가드는 이미 있었다(아니면 도메인 오류로 거절).
      ② 그 컬럼 튜플이 ``SelectedSource`` 의 키와 같다 — **아무도 확인하지 않았다.**

    ②가 깨지면 ①은 여전히 통과하고(자기 자신과 비교하므로) cast 만 조용히 틀린다.
    실측 2026-09-06: 양쪽 25개, 차집합 0.
    """

    def test_the_column_tuple_and_the_typed_dict_declare_the_same_keys(self):
        from fcc_test_platform.application.central_result_selection_adapter import (
            SELECTED_SOURCE_COLUMNS,
        )
        from fcc_test_platform.domain.ports.output.central_result_selection_port import (
            SelectedSource,
        )
        columns = set(SELECTED_SOURCE_COLUMNS)
        keys = set(SelectedSource.__annotations__)
        self.assertTrue(columns, 'SELECT 컬럼이 비었다 — 판정이 vacuous 다')
        self.assertEqual(
            columns, keys,
            'SELECT 컬럼과 SelectedSource 의 키가 갈렸다. 어댑터는 그 행을 '
            'SelectedSource 로 cast 하므로, 갈린 순간 그 cast 는 «검사되지 않은 거짓»이 '
            f'된다 — 컬럼에만: {sorted(columns - keys)} · 키에만: {sorted(keys - columns)}',
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
