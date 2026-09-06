"""계약표(`PLATFORM_API_OPERATIONS`)의 필드 읽기는 **전부 검사되는 형태**여야 한다.

설계서 §8「contract-as-data 는 유지한다」가 남긴 결론을 기계가 지킨다. 그 절은
dict 계약을 유지하기로 하면서 타입 안전성을 커널의 ``OperationSpec``(TypedDict)에
맡겼는데, **TypedDict 는 소비 형태를 고르지 않으면 아무것도 검사하지 않는다.**

실측 2026-09-06 (커널 ``kernel-v0.5.1`` · mypy 2.3.1, 주입 대조 — 이 파일이 지키는
사실의 근거다)::

    형태                                    mypy               런타임
    ─────────────────────────────────────── ────────────────── ──────────────
    row.get('permission')                   ⚠️ 아무 말 없음     안전
    row.get('오타')                          ⚠️ 아무 말 없음     조용히 None
    row['permission']                       ✅ 키 이름 검사      Required 만 안전
    row['오타']                              ✅ [typeddict-item]  KeyError
    row['response_media_type'] (NotRequired) ⚠️ 아무 말 없음     ★ KeyError 79/80
    'k' in row 뒤 row['k']                   ✅ 좁혀서 검사       안전

즉 ``.get`` 은 키 «이름»을 **전혀** 안 본다. 오타는 영원히 조용하고, 그 조용함은
초록과 같은 모양이다. 그래서 이 봉인이 지키는 명제는 둘이다:

    ① 계약표의 필드를 읽는 모든 자리는 **첨자** 형태다.
    ② ``NotRequired`` 키를 읽는 첨자는 모두 ``in`` **가드 안**에 있다.

⚠️ ②가 없으면 ①은 «고장을 옮길» 뿐이다. 표의 다섯째 줄이 그 자리다 — 첨자 단독은
mypy 가 침묵하고 런타임이 79/80 에서 죽는다. 실측으로 확인했다: ``in`` 가드를 지우면
mypy 도 ①도 아무 말을 하지 않는다. ②는 그 침묵을 메우려고 있다.

■ 이 봉인이 «혼자» 지키는 것은 ②뿐이다 — 축별 담당 (실측 2026-09-06, 주입 대조)

    회귀                                  mypy 게이트          이 봉인
    ───────────────────────────────────── ──────────────────── ─────────
    `.get` 로 되돌린다                     ⚠️ 조용             🔴 ①
    NotRequired 를 가드 없이 첨자로 읽는다  ⚠️ 조용             🔴 ②
    `Final` 을 뗀다                        🔴 [literal-required] ⚠️ 조용
    `OPS.get(n) or {}` 로 되돌린다          🔴 [var-annotated]   ⚠️ 조용

⚠️ **둘은 겹치지 않는다.** 이 파일만 보고 「계약표 소비는 지켜진다」고 읽으면 틀린다 —
아래 두 줄은 `mypy.ini` 의 `application.*` strict 가 지키고, 그 게이트가 꺼지는 날
이 봉인은 그것을 **모른다**. 반대도 참이다.

■ ⚠️ 「어디」를 적지 않는다 — 소비자 집합을 **선언에서 파생한다**

소비자 목록을 여기 손으로 적으면 새 소비자가 조용히 밖에 선다. 그래서 패키지
전체를 훑어 계약표를 **실제로 읽는** 모듈을 찾아낸다. 대신 「필드 읽기가 있는
모듈」의 집합은 원장으로 못 박는다(:data:`MODULES_THAT_READ_FIELDS`) — 그 집합이
움직이는 날은 설계서 §8 의 표도 함께 움직여야 하는 날이기 때문이다.

■ ⚠️ 수(7)를 못 박지 않는 이유

같은 파일에 검사되는 읽기를 «더» 하는 것은 개선이다. 수를 박으면 그 개선이
회귀처럼 빨개지고, 다음 사람은 봉인을 고치는 대신 개선을 포기한다. 박는 것은
**비율이 아니라 여집합**이다 — 검사 안 되는 읽기의 집합이 공집합인가.

■ 🔴 설계서 §8 의 「상한 6/7」은 **무효다** (2026-09-06)

그 절은 런타임 인가 자리(``platform_routes.py``)를 두고 *"이 자리는 타입 검사를
포기하는 것이 옳다"* 고 결론지었다. 근거는 첨자로 바꾸면 모르는 operation 이름에
``KeyError`` 가 나서 fail-closed 가 fail-loud 로 바뀐다는 것이었고, 그 관측 자체는
맞다. 틀린 것은 **선택지가 둘뿐이라고 본 것**이다. 셋째 형태가 있다::

    spec = PLATFORM_API_OPERATIONS.get(operation)          # 조회 — 부재는 None (차단 유지)
    required = (spec['permission'] if spec is not None else '').strip()   # 필드 — 첨자 (검사됨)

⚠️ 그 절은 이 분리를 **자기가 이미 적어 놓고** 결론에서 다시 묶었다: 분모의 단위를
「TypedDict 필드 읽기」로 정의하며 *"spec 선택(`OPS.get(op)`)은 TypedDict 접근이 아니라
세지 않는다"* 고 못 박는다. 조회와 필드 읽기가 다른 것이라면 **다른 형태를 쓸 수
있다.** 상한은 6/7 이 아니라 7/7 이고, 이 봉인이 그 7/7 을 N/N 으로 일반화한다.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / 'fcc_test_platform'

#: 계약표의 이름. 커널이 공표하는 그 이름 하나가 이 스캔의 진입점이다.
TABLE = 'PLATFORM_API_OPERATIONS'

#: 필드 읽기가 **있는** 모듈의 원장 (저장소 상대 경로).
#:
#: ⚠️ 이것은 「소비자 목록」이 아니다 — 소비자는 파생한다. 이것은 설계서 §8 의 표와
#: 짝이 되는 집합이고, 여기가 움직이면 그 표도 움직여야 한다. 실측 2026-09-06:
#: `api_composition.py` 도 계약표를 임포트하지만 **필드 읽기가 0건**이다(표를 통째로
#: `ApiAccessPolicy` 생성자에 넘긴다) — 그래서 여기 없다. dict 를 통째로 넘기는
#: 자리는 TypedDict 가 검사할 것을 갖지 않는다.
MODULES_THAT_READ_FIELDS = frozenset({
    'fcc_test_platform/api/platform_routes.py',
    'fcc_test_platform/application/api_schema.py',
})


def _optional_keys() -> frozenset[str]:
    """커널 ``OperationSpec`` 의 ``NotRequired`` 키 — **선언문에서 파생한다.**

    🔴 ⚠️ **런타임 introspection 은 여기서 거짓말한다.** 커널 모듈이
    ``from __future__ import annotations`` 를 쓰므로 어노테이션이 ``ForwardRef``
    문자열이 되고, CPython 3.12 의 ``TypedDict`` 는 그 문자열 안의 ``NotRequired``
    를 못 본다. 실측 2026-09-06 (커널 ``kernel-v0.5.1`` · CPython 3.12.3)::

        선언문   NotRequired 셋 (error_responses · allowed_during_password_change
                                 · response_media_type)
        런타임   __required_keys__ = 여섯 «전부» · __optional_keys__ = «빈 집합»
        데이터   error_responses 는 16/80 에서 실제로 «없다»

    그래서 ``__optional_keys__`` 로 파생하면 이 봉인의 ②가 **조용히 공허해진다** —
    검사할 키가 하나도 없는 채로 초록이 된다. 텍스트를 읽는 것이 우회로 보이지만,
    여기서는 그것이 **선언에 더 가까운 쪽**이다.

    ⚠️ 오늘 이 레인·계약 레인 어디에도 ``__required_keys__`` 소비자가 없어 위 어긋남은
    잠복이다. 생기는 날 그것은 80건 중 최대 79건을 「필수 키 누락」으로 거절한다.
    """
    import fcc_test_kernel.application.central_contract.api_contracts as contracts
    tree = ast.parse(Path(contracts.__file__).read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'OperationSpec':
            keys = {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and isinstance(stmt.annotation, ast.Subscript)
                and _annotation_label(stmt.annotation.value) == 'NotRequired'
            }
            if keys:
                return frozenset(keys)
    raise AssertionError(
        f'커널 {contracts.__file__} 에서 OperationSpec 의 NotRequired 키를 하나도 '
        f'못 찾았다. 선언이 이사했거나 형태가 바뀌었다 — 이 봉인의 ②가 공허해지므로 '
        f'초록으로 넘어가지 않는다.')


def _annotation_label(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ''


def _final_strings(tree: ast.Module) -> dict[str, str]:
    """모듈 최상위의 ``NAME: Final = '문자열'`` — 이름에서 값으로.

    ⚠️ ``Final`` 이어야 한다. 그냥 ``NAME = '...'`` 이면 mypy 가 타입을 ``str`` 로
    넓혀 TypedDict 첨자에 못 쓴다 — 실측하면 그 자리가
    ``error: TypedDict key must be a string literal  [literal-required]`` 로
    빨개진다. 즉 여기서 ``Final`` 을 요구하는 것은 형식이 아니라 **검사되는가**를
    그대로 옮긴 것이고, 그 축은 mypy 게이트가 이미 지킨다(이 봉인은 겹쳐 선다).
    """
    values: dict[str, str] = {}
    for node in tree.body:
        if (isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and _annotation_label(node.annotation) == 'Final'):
            values[node.target.id] = node.value.value
    return values


def _scopes(tree: ast.Module) -> list[list[ast.AST]]:
    """스코프별 노드 묶음. 중첩 함수는 **자기 스코프**로 갈라진다.

    ⚠️ 가드(``'k' in row``)와 읽기(``row['k']``)를 같은 스코프 안에서만 짝지으려고
    가른다. 모듈 전체에서 가드를 찾으면 «다른 함수의» 가드가 이 함수의 첨자를
    가려 주는 것으로 읽힌다 — 그것은 검사를 끄는 방향의 오분류다.
    """
    scopes: list[list[ast.AST]] = []

    def walk(node: ast.AST, own: list[ast.AST]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                inner: list[ast.AST] = [child]
                walk(child, inner)
                scopes.append(inner)
            else:
                own.append(child)
                walk(child, own)

    module_scope: list[ast.AST] = [tree]
    walk(tree, module_scope)
    scopes.append(module_scope)
    return scopes


class _Analysis:
    """계약표에서 «행»을 얻는 식과, 그 행에서 «필드»를 읽는 자리를 가른다."""

    def __init__(self, tree: ast.Module, optional_keys: frozenset[str]) -> None:
        self.finals = _final_strings(tree)
        self.optional_keys = optional_keys
        self.row_names = self._row_names(tree)
        #: (줄, 사유) — 검사되지 않는 필드 읽기.
        self.unchecked: list[tuple[int, str]] = []
        #: (줄, 키) — ``in`` 가드 없이 NotRequired 키를 읽는 첨자.
        self.unguarded: list[tuple[int, str]] = []
        #: 검사되는 필드 읽기의 줄.
        self.checked: list[int] = []
        for scope in _scopes(tree):
            self._classify(scope)

    # ── 「행을 내는 식」인가 ────────────────────────────────────────────────
    def _is_row(self, node: ast.expr, names: frozenset[str] | set[str]) -> bool:
        # ⚠️ ``PLATFORM_API_OPERATIONS`` 자체는 행이 아니라 **표**다. 여기서 참을
        # 돌려주면 행 선택(`OPS['이름']`)이 필드 읽기로 오분류된다.
        if isinstance(node, ast.Name):
            return node.id in names
        if isinstance(node, ast.Subscript):
            return isinstance(node.value, ast.Name) and node.value.id == TABLE
        if isinstance(node, ast.Call):
            func = node.func
            return (isinstance(func, ast.Attribute) and func.attr == 'get'
                    and isinstance(func.value, ast.Name) and func.value.id == TABLE)
        if isinstance(node, ast.BoolOp):
            # `OPS.get(op) or {}` — 부재를 빈 dict 로 접는 형태. 행으로 «본다»:
            # 그래야 그 뒤의 `.get` 이 검사 밖이라고 이 봉인이 말할 수 있다.
            return any(self._is_row(v, names) for v in node.values)
        return False

    def _row_names(self, tree: ast.Module) -> frozenset[str]:
        """행에 결속된 이름. 모듈 전체에서 «두 바퀴» 모은다.

        ⚠️ 한 바퀴로 하면 결속보다 앞에 나온 읽기를 놓치고, 놓친 것은
        「검사 안 되는 읽기가 없다」와 **같은 모양**이 된다.
        """
        names: set[str] = set()
        for _ in range(2):
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and self._is_row(node.value, names):
                    names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        return frozenset(names)

    def _key_of(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.finals.get(node.id)
        return None

    # ── 스코프 하나를 분류한다 ─────────────────────────────────────────────
    def _classify(self, scope: list[ast.AST]) -> None:
        guarded = {
            key
            for node in scope
            if isinstance(node, ast.Compare)
            and len(node.ops) == 1 and isinstance(node.ops[0], ast.In)
            and self._is_row(node.comparators[0], self.row_names)
            for key in [self._key_of(node.left)] if key is not None
        }
        for node in scope:
            if isinstance(node, ast.Subscript) and self._is_row(node.value, self.row_names):
                key = self._key_of(node.slice)
                if key is None:
                    continue
                self.checked.append(node.lineno)
                if key in self.optional_keys and key not in guarded:
                    self.unguarded.append((node.lineno, key))
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == 'get'
                  and self._is_row(node.func.value, self.row_names)):
                owner = (node.func.value.id
                         if isinstance(node.func.value, ast.Name) else '<식>')
                self.unchecked.append((node.lineno, f'{owner}.get(...)'))


def _modules() -> list[Path]:
    return sorted(
        p for p in PACKAGE_ROOT.rglob('*.py')
        if TABLE in p.read_text(encoding='utf-8')
    )


def _analyse(path: Path, optional_keys: frozenset[str]) -> _Analysis:
    return _Analysis(ast.parse(path.read_text(encoding='utf-8')), optional_keys)


class TestEveryFieldReadIsChecked(unittest.TestCase):

    def setUp(self) -> None:
        self.optional_keys = _optional_keys()
        self.analyses = {
            path.relative_to(PROJECT_ROOT).as_posix(): _analyse(path, self.optional_keys)
            for path in _modules()
        }

    def test_no_field_read_uses_the_unchecked_form(self):
        """① 여집합이 공집합인가."""
        offenders = [
            f'{rel}:{line}  {form}'
            for rel, analysis in sorted(self.analyses.items())
            for line, form in analysis.unchecked
        ]
        self.assertEqual(
            [], offenders,
            '계약표의 필드를 `.get` 으로 읽는 자리가 있다 — mypy 는 그 자리의 키 '
            '«이름»을 전혀 검사하지 않는다(오타는 영원히 조용하다):\n  '
            + '\n  '.join(offenders)
            + '\n\n첨자로 바꿔라. 조회(`OPS.get(op)`)는 그대로 두고 «필드»만 첨자로 '
              '읽으면 fail-closed 도 유지된다 — 설계서 §8 「상한 6/7」 정정 참조.')

    def test_optional_keys_are_read_behind_a_guard(self):
        """② NotRequired 키의 첨자는 ``in`` 가드 안에 있는가.

        ⚠️ 이 축은 **mypy 가 안 잡는다**(실측). 첨자 단독은 조용히 통과하고
        런타임이 죽는다 — 그래서 여기가 그 자리를 지키는 유일한 게이트다.
        """
        offenders = [
            f'{rel}:{line}  op[{key!r}]  ({key} 는 NotRequired)'
            for rel, analysis in sorted(self.analyses.items())
            for line, key in analysis.unguarded
        ]
        self.assertEqual(
            [], offenders,
            'NotRequired 키를 `in` 가드 없이 첨자로 읽는다 — mypy 는 «아무 말도 '
            '안 하고» 그 키가 없는 operation 에서 런타임이 KeyError 로 죽는다:\n  '
            + '\n  '.join(offenders)
            + f'\n\n(NotRequired 키: {sorted(self.optional_keys)})\n'
              "형태: `media = op['k'] if 'k' in op else 기본값`. 그러면 검사되면서 "
              '안전하다.')

    def test_the_scan_is_not_vacuous(self):
        """⚠️ 「위반 0」과 「아무것도 안 봤다」는 출력이 같다.

        말뭉치가 사라지면 공집합형 봉인은 조용해진다. 그래서 읽기가 **있는** 모듈의
        집합을 등호로 못 박는다 — 소비자가 이사하거나 새로 생기면 여기가 먼저
        빨개지고, 그날 설계서 §8 의 표도 함께 움직여야 한다.

        ⚠️ 수(7)는 못 박지 않는다. 검사되는 읽기를 «더» 하는 것은 개선인데, 수를
        박으면 그 개선이 회귀처럼 빨개진다.
        """
        found = {rel for rel, analysis in self.analyses.items() if analysis.checked}
        self.assertEqual(
            MODULES_THAT_READ_FIELDS, frozenset(found),
            '계약표의 «필드»를 읽는 모듈 집합이 원장과 다르다. 새 소비자가 생겼거나 '
            '기존 소비자가 읽기를 잃었다 — 어느 쪽이든 설계서 §8 의 소비자 표가 '
            f'낡았다.\n  원장: {sorted(MODULES_THAT_READ_FIELDS)}\n  관측: {sorted(found)}')
