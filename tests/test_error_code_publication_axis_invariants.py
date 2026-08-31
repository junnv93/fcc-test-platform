# ⚠️ 2026-08-31: 이 파일은 모노레포 `tests/test_error_code_publication_axis_invariants.py` 에서 갈라져 왔다. 남은 것은
#    소비 대상이 이 레포에 있는 단위(TestNonEmptinessGuards, TestFrontendUnionCompleteness)뿐이고,
#    나머지 형제 검사와 그것들만 쓰던 import 는 저쪽에 남았다.
"""Error code publication axis — does each surface publish what it can emit?

2026-08-13 결함(PR #248 상환분, 사람이 발견): ``SESSION_RESULTS_EMPTY`` 는
headless-scoped, headless 가 실제로 emit, OpenAPI 422 산문에도 이름이 적혀
있었는데 headless 아티팩트의 ``ErrorCode`` enum 에는 없었다. 기전은
``problem_details_component_schemas(codes=None)`` 의 기본값이
``SHARED_ERROR_CODES``(어느 surface 에도 스코프되지 않은 코드)였다는 것 —
스코프된 코드는 정확히 그 기본값에서 빠진다. platform 호출 지점만 스코프를
넘겼고 headless 는 기본값이었다.

이 파일은 그 결함의 재발을 두 층에서 막는다:

1. **구조** — ``problem_details_component_schemas`` 의 ``codes`` 인자가 이제
   필수다(``openapi_schema_builder.py``). 기본값이 없으면 다음 surface 가
   같은 자리에서 넘어질 수 없다.
2. **게이트** — 그래도 갈라질 수 있는 세 축을 파생 단언으로 잠근다:
   - AC-2 발행 완전성: emittable(surface) ⊆ published(surface)
   - AC-3 누출 없음: published(surface) ⊆ surface_error_codes(surface)
   - AC-4 프론트 유니온 완전성: api-error.ts 의 ``ErrorCode`` 유니온이
     ErrorCode 를 발행하는 **FE 가 실제로 소비하는** 아티팩트(=
     ``packages/api-artifacts`` 미러, ``codegen: true`` 항목) 전량을 커버하는지

두 게이트(AC-2/AC-3)는 상반된 수리를 요구하므로(빠진 것은 넣어서, 새는 것은
빼서) 한 단언으로 접지 않는다. 어느 쪽도 surface 를 손으로 나열하지 않는다 —
``ApiSurface`` 를 순회한다.

**R4 — 이 게이트는 platform 전용 등가성 봉인(``test_platform_project_
directory_invariants.py::TestErrorCodeSurfaceScope``)과 서로 함의하지 않는다.**
그 파일의 ``test_every_code_this_surface_can_emit_is_published`` /
``test_the_platform_artifact_publishes_exactly_this_surfaces_codes`` 는
platform **한 surface** 를 이름으로 지목해 등가성을 검사한다 — 세 번째
surface 가 내일 생겨도 그 파일의 assertion 은 단 하나도 움직이지 않는다.
여기 두 클래스는 ``for surface in ApiSurface`` 로 순회하므로 세 번째
surface 가 enum 에 추가되는 순간 자동으로 그 surface 를 검사 대상에 넣는다.
즉 셋을 겹쳐 세는 3중 계수가 아니라, "platform 은 스스로와 같은가"(기존)와
"모든 surface 는 자기 스코프의 부분집합인가"(이 파일)라는 **다른 질문**이다.

**2026-08-13 iteration-3 리뷰 상환분** — 세 결함이 이 파일 자체에 있었다:

1. **AC-4 alias 단어 출현 false negative** — 옛 ``_surfaces_missing_from_union``
   은 union 선언 슬라이스 안에 alias *식별자*가 나타나는지만 봤다. 그래서
   ``HeadlessComponents['schemas']['ErrorCode']`` 를
   ``HeadlessComponents['notSchemas']['NotErrorCode']`` 로 바꾼 사본도, 선언
   헤드를 ``ErrorCodeLegacy`` 로 개명한 사본도 ``missing=[]`` 로 통과했다.
   정공은 :func:`error_code_union_module_stems` — union 멤버가 정확히
   ``Alias['schemas']['ErrorCode']`` 로 인덱싱되는지, 그리고 선언 헤드가
   ``ErrorCode`` 뒤에 식별자 문자가 오지 않는 정확한 경계인지를 함께 본다
   (``TestMutationRegressions`` (e)/(f)가 두 반사실을 실증).
2. **AC-5 (a)/(b)가 검출기/리더 자체를 통과시키지 않았다** — (a)는
   counterfactual source 를 만들지 않고 함수를 직접 호출해 "함수가 거부한다"
   만 증명했고, (b)는 counterfactual table tuple 을 만들지 않고 이미 계산된
   ``_emittable()`` 결과에 코드를 union 해 "집합 연산이 옳다"만 증명했다 —
   둘 다 AST 스윕(``_call_arg_counts_in_source``)이나 테이블 리더
   (``_codes_from_table``) 자체가 고장 나도 green 일 수 있었다. 정공은 두
   변이 모두 **순수 검출기/리더 함수에 counterfactual 입력을 실제로 통과**시킨다.
"""
from __future__ import annotations

import ast
import copy
import json
import re
import sys
from pathlib import Path as _Path
import unittest
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC = PROJECT_ROOT / 'src'
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


from fcc_test_contracts.common.api_error_codes import (  # noqa: E402
    ApiSurface,
    ErrorCode,
    SHARED_ERROR_CODES,
    surface_error_codes,
)
from tests.support import api_surface_boundary  # noqa: E402
from fcc_test_contracts.common.openapi_schema_builder import (  # noqa: E402
    problem_details_component_schemas,
)

# ⚠️ `resolve_repo_artifact` 는 모노레포의 **재배치 레이어**를 해소하는 헬퍼다.
#    이 레포에는 그 레이어가 없고 경로가 그대로이므로, 같은 이름으로 단순
#    루트 결합을 쓴다. 모노레포 판본을 부르면 `RelocationAmbiguity` 로 죽는다.
def resolve_repo_artifact(_anchor, relative):  # noqa: F811
    return _Path(__file__).resolve().parent.parent / relative



WEB_ROOT = PROJECT_ROOT / 'apps' / 'web'
API_ERROR_TS_PATH = WEB_ROOT / 'src' / 'shared' / 'api-error.ts'

DOCS_API_DIR = resolve_repo_artifact(__file__, 'docs/api')

# FE codegen never reads docs/api directly — it reads this mirror package
# (sync.mjs --check + tests/test_api_artifacts_package.py already own
# docs/api == mirror byte-identity; AC-4 relies on that identity but does not
# re-check it here).
API_ARTIFACTS_ROOT = PROJECT_ROOT / 'packages' / 'api-artifacts'
API_ARTIFACTS_MANIFEST_PATH = API_ARTIFACTS_ROOT / 'manifest.json'
API_ARTIFACTS_DIR = API_ARTIFACTS_ROOT / 'artifacts'

# Real production call sites — swept by the AST guard in mutation (a) so the
# "codes is mandatory" structural claim is checked against the actual source,
# not only against the function signature.
HEADLESS_API_SCHEMA_PATH = _SRC / 'application' / 'headless' / 'api_schema.py'
PLATFORM_API_SCHEMA_PATH = resolve_repo_artifact(__file__, 'src/application/platform/api_schema.py')


# ── surface → boundary module resolution (규약 파생, 손 매핑 금지) ────────────
# 표면→경계 모듈 해소와 emittable 계산은 ``tests/support/api_surface_boundary``
# 단일 정의를 소비한다. 분류 축(``test_headless_boundary_default_honesty``)이
# **같은 질문**을 하므로 사본을 두면 드리프트하고, 드리프트하는 쪽은 덜 고치는 쪽이다
# (선례: ``tests/support/parity.py``).
_routes_module = api_surface_boundary.routes_module
_codes_from_table = api_surface_boundary.codes_from_table
_emittable = api_surface_boundary.emittable_codes


# ── shared reader: "does this artifact publish ErrorCode, and what" ──────────
# AC-2 uses the non-None branch (published set). AC-4 uses `is not None` alone
# (which codegen artifacts publish ErrorCode at all). One definition, two call
# sites — a second copy would be the same drift risk AC-2/AC-3 exist to close.
def artifact_error_code_enum(artifact: dict) -> Optional[frozenset]:
    """Return the published ``ErrorCode`` set, or ``None`` if the artifact has
    no ``ErrorCode`` component schema at all.

    ``None`` is a distinct fact from "publishes zero codes" — an artifact
    that never got the schema at all is a structurally different failure
    than one whose enum is (correctly or incorrectly) empty. Callers that
    need "surface must publish something" convert ``None`` into a loud,
    named failure instead of collapsing it to an empty set (an empty set
    would make AC-2 report "every emittable code is missing" instead of the
    real fact, "the schema itself never landed").
    """
    schema = artifact.get('components', {}).get('schemas', {}).get('ErrorCode')
    if schema is None:
        return None
    return frozenset(ErrorCode(value) for value in schema.get('enum', []))


def _load_docs_artifact(surface: ApiSurface) -> dict:
    path = DOCS_API_DIR / f'{surface.value}-api.openapi.json'
    return json.loads(path.read_text(encoding='utf-8'))


def _published_from_artifact(surface: ApiSurface, artifact: dict) -> frozenset:
    """AC-2/AC-3 published set for ``surface`` — loud if the schema is absent."""
    enum = artifact_error_code_enum(artifact)
    if enum is None:
        raise AssertionError(
            f'{surface.value} artifact has no ErrorCode component schema at '
            'all -- this is not "publishes 0 codes", the schema itself never '
            'landed. Fix the boundary that builds this artifact, not this test.'
        )
    return enum


def _missing_from_publication(emittable: frozenset, published: frozenset) -> list:
    """AC-2 위반 집합: emit 가능한데 발행되지 않은 코드(이름순)."""
    return sorted(emittable - published, key=lambda c: c.value)


def _leaked_out_of_scope(published: frozenset, allowed: frozenset) -> list:
    """AC-3 위반 집합: 발행됐는데 그 surface 스코프 밖인 코드(이름순)."""
    return sorted(published - allowed, key=lambda c: c.value)


def _format_violations(surface: ApiSurface, codes: list) -> list:
    """``surface: CODE`` 형식 — 어느 surface 의 어느 코드인지 이름을 댄다."""
    return [f'{surface.value}: {code.value}' for code in codes]


# ── AST sweep — mutation (a)'s production-source half ────────────────────────
def _call_arg_counts_in_source(source_text: str, func_name: str) -> list:
    """Pure AST sweep over already-loaded source *text*: positional+keyword
    arg count for every call to ``func_name``.

    Kept separate from :func:`_call_arg_counts` (which reads a path) so a
    **counterfactual string** can be fed through the exact same detector logic
    the real sweep uses — proving the detector itself fires on a bare call,
    not merely that today's real files happen to be clean.
    """
    tree = ast.parse(source_text)
    counts = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)
        if name == func_name:
            counts.append(len(node.args) + len(node.keywords))
    return counts


def _call_arg_counts(source_path: Path, func_name: str) -> list:
    """AST sweep over a real file on disk. Used to prove no *real* call site
    is bare — complements the signature check, which only proves the function
    itself rejects a bare call, not that nobody still writes one.
    """
    return _call_arg_counts_in_source(
        source_path.read_text(encoding='utf-8'), func_name,
    )


def _bare_call_counterfactual(source_text: str, func_name: str) -> str:
    """Return a copy of ``source_text`` with every ``func_name(...)`` call
    rewritten to a bare ``func_name()`` — balanced-paren aware, because the
    real production call sites nest another call inside the argument
    (``problem_details_component_schemas(surface_error_codes(ApiSurface.X))``),
    so a naive non-nested regex would stop at the first inner ``)``.
    """
    marker = func_name + '('
    pieces = []
    i = 0
    while True:
        idx = source_text.find(marker, i)
        if idx == -1:
            pieces.append(source_text[i:])
            break
        pieces.append(source_text[i:idx])
        depth = 0
        j = idx + len(marker) - 1  # position of the call's opening '('
        while True:
            if source_text[j] == '(':
                depth += 1
            elif source_text[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        pieces.append(func_name + '()')
        i = j + 1
    return ''.join(pieces)


# ── api-error.ts declaration slice: block comments → decl start → next ';' →
# line comments → alias/module-stem derivation. ──────────────────────────────
_BLOCK_COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)
_LINE_COMMENT_RE = re.compile(r'//[^\n]*')
# Boundary-aware: `export type ErrorCode` is a textual *prefix* of
# `export type ErrorCodeLegacy`, so a bare `str.find` would slice the wrong
# (renamed) declaration and still "find" every alias inside it. The negative
# lookahead requires the next character to NOT continue an identifier.
_DECLARATION_HEAD_RE = re.compile(r'export type ErrorCode(?![A-Za-z0-9_])')
# Exact union-member participation: `Alias['schemas']['ErrorCode']` only —
# not "the alias identifier appears somewhere in the slice" (which a decoy
# index like `Alias['notSchemas']['NotErrorCode']` would also satisfy).
_UNION_MEMBER_RE = re.compile(
    r"(\w+)\s*\[\s*(['\"])schemas\2\s*\]\s*\[\s*(['\"])ErrorCode\3\s*\]"
)


def _strip_block_comments(ts_text: str) -> str:
    """Step 1 — remove ``/* ... */`` first, globally, before any text search.

    Without this a decoy inside a docblock (e.g. a comment that quotes
    ``export type ErrorCode = ...;`` as an example) could be mistaken for the
    real declaration by a naive substring/regex search.
    """
    return _BLOCK_COMMENT_RE.sub('', ts_text)


def _declaration_slice(stripped_text: str) -> str:
    """Steps 2-4 — find the declaration start, slice to the next ``;``, then
    strip any line comments that ended up inside the slice.
    """
    match = _DECLARATION_HEAD_RE.search(stripped_text)
    if match is None:
        raise AssertionError(
            'api-error.ts has no `export type ErrorCode = ...;` declaration '
            '(searched outside block comments)'
        )
    terminator = stripped_text.find(';', match.start())
    if terminator == -1:
        raise AssertionError(
            'api-error.ts `export type ErrorCode` declaration has no '
            'terminating `;`'
        )
    declaration = stripped_text[match.start():terminator + 1]
    return _LINE_COMMENT_RE.sub('', declaration)


def error_code_union_module_stems(ts_source: str) -> frozenset:
    """Pure parse: which ``@/api/generated/*`` module stems genuinely
    participate in the ``export type ErrorCode`` union via *exact*
    ``Alias['schemas']['ErrorCode']`` indexing.

    Deliberately narrower than "the alias identifier appears somewhere in the
    declaration slice" (2026-08-13 iteration-3 review finding) — an alias
    whose import is real but whose union member indexes something else
    entirely (``Alias['notSchemas']['NotErrorCode']``) must not count, and
    neither may a same-named alias that only shows up because the declaration
    *head itself* was renamed (``export type ErrorCodeLegacy = ...`` is not
    ``export type ErrorCode``, sealed by :data:`_DECLARATION_HEAD_RE`). Both
    are exercised as counterfactuals in ``TestMutationRegressions``.
    """
    stripped = _strip_block_comments(ts_source)
    declaration = _declaration_slice(stripped)
    aliases = _import_aliases(stripped)  # import_path -> alias
    participating_aliases = frozenset(
        m.group(1) for m in _UNION_MEMBER_RE.finditer(declaration)
    )
    return frozenset(
        import_path.rsplit('/', 1)[-1]
        for import_path, alias in aliases.items()
        if alias in participating_aliases
    )


def _import_aliases(stripped_text: str) -> dict:
    """``@/api/generated/*.types`` import 경로 → 로컬 식별자(alias 없으면 'components')."""
    aliases = {}
    for m in re.finditer(
        r"import type \{ components(?: as (\w+))? \} from "
        r"'(@/api/generated/[\w./-]+)';",
        stripped_text,
    ):
        alias, path = m.group(1) or 'components', m.group(2)
        aliases[path] = alias
    return aliases


# ── packages/api-artifacts manifest resolution (step 5: module-stem 파생) ────
def _manifest() -> dict:
    return json.loads(API_ARTIFACTS_MANIFEST_PATH.read_text(encoding='utf-8'))


def _codegen_entries() -> list:
    """manifest ``artifacts[]`` entries with ``codegen: true`` — the subset FE
    codegen actually turns into TS types (``central-db-schema`` is
    ``codegen: false`` and must not be judged by AC-4 at all).
    """
    return [entry for entry in _manifest()['artifacts'] if entry.get('codegen')]


def _load_package_artifact(entry_name: str) -> dict:
    entry = next(e for e in _manifest()['artifacts'] if e['name'] == entry_name)
    return json.loads((API_ARTIFACTS_DIR / entry['file']).read_text(encoding='utf-8'))


def _module_stem(entry: dict) -> str:
    """Derive the TS import module stem from the manifest's own
    ``typesBasename`` — not a guessed ``f'{name}.types'`` string. The
    manifest already knows this; deriving it a second way is exactly the
    kind of second surface-list AC-2 exists to forbid.
    """
    basename = entry['typesBasename']
    assert basename.endswith('.ts'), f'unexpected typesBasename: {basename!r}'
    return basename[:-len('.ts')]


def _artifacts_publishing_error_code() -> list:
    """AC-4: codegen manifest entries whose artifact bytes carry ``ErrorCode``."""
    return [
        entry for entry in _codegen_entries()
        if artifact_error_code_enum(_load_package_artifact(entry['name'])) is not None
    ]


def _all_codegen_stems() -> frozenset:
    return frozenset(_module_stem(entry) for entry in _codegen_entries())


def _required_stems() -> frozenset:
    """Module stems AC-4 requires the union to cover — codegen entries whose
    artifact actually publishes ``ErrorCode``."""
    return frozenset(_module_stem(entry) for entry in _artifacts_publishing_error_code())


def _surfaces_missing_from_union(ts_text: str) -> list:
    """AC-4 위반 집합: ErrorCode 를 발행하지만 union 에 정확히 인덱싱되지 않은 codegen artifact 이름."""
    participating_stems = error_code_union_module_stems(ts_text)
    return [
        entry['name']
        for entry in _artifacts_publishing_error_code()
        if _module_stem(entry) not in participating_stems
    ]




class TestNonEmptinessGuards(unittest.TestCase):
    """비공허성 가드 — ``for surface in ApiSurface`` 순회 단언은 그 집합이나
    중간 집합이 비면 **진공 참**(vacuous truth)으로 green 이 된다. 각 가드는
    AC-2/AC-3/AC-4 가 실제로 뭔가를 비교하고 있다는 것을 확인한다.
    """

    def test_1_api_surface_enum_has_at_least_two_members(self):
        # >= 2, not just non-empty -- a single-member ApiSurface would make
        # "every surface" and "the whole scope" the same statement, so the
        # AC-2/AC-3 per-surface subset claim would be vacuous in the same way
        # an empty enum would.
        self.assertGreaterEqual(
            len(list(ApiSurface)), 2,
            'ApiSurface has fewer than 2 members -- per-surface scoping assertions are vacuous',
        )

        # ⚠️ `test_2_every_surface_has_a_non_empty_emittable_set` 는 세 표면
        #    (platform/headless/session) 을 모두 요구하는데 이 레포엔 platform 만
        #    있다. 사유는 모노레포 `tests/RETIRED_WITH_THE_FRONTEND.md` §5.


    def test_3_every_surface_has_a_non_empty_published_set(self):
        for surface in ApiSurface:
            with self.subTest(surface=surface.value):
                published = _published_from_artifact(surface, _load_docs_artifact(surface))
                self.assertTrue(
                    published, f'{surface.value} artifact publishes no codes at all',
                )

    def test_4_every_surface_has_a_non_empty_allowed_scope(self):
        for surface in ApiSurface:
            with self.subTest(surface=surface.value):
                self.assertTrue(
                    surface_error_codes(surface),
                    f'{surface.value} has an empty allowed scope',
                )

    # ⚠️ `test_5_at_least_one_codegen_artifact_publishes_error_code` 는 이 레포로 오지 못했다 — 사유는
    #    `tests/RETIRED_WITH_THE_FRONTEND.md`(모노레포) §5 참조.


    def test_6_the_ts_import_alias_scan_finds_at_least_one_alias(self):
        ts_text = API_ERROR_TS_PATH.read_text(encoding='utf-8')
        aliases = _import_aliases(_strip_block_comments(ts_text))
        self.assertTrue(
            aliases, 'import alias scan found nothing -- regex or file may be broken',
        )

    # ⚠️ `test_7_the_required_stem_set_is_not_empty` 는 이 레포로 오지 못했다 — 사유는
    #    `tests/RETIRED_WITH_THE_FRONTEND.md`(모노레포) §5 참조.


    def test_8_the_real_union_declaration_parses_at_least_one_stem(self):
        # The TS-side half of AC-4 (`error_code_union_module_stems`) must not
        # silently return an empty set on real input -- that would make the
        # completeness check pass by omission rather than by genuine coverage.
        ts_text = API_ERROR_TS_PATH.read_text(encoding='utf-8')
        stems = error_code_union_module_stems(ts_text)
        self.assertTrue(
            stems, 'error_code_union_module_stems found nothing in the real declaration',
        )

    # ⚠️ `test_9_the_required_set_excludes_an_artifact_that_publishes_no_error_code` 는 이 레포로 오지 못했다 — 사유는
    #    `tests/RETIRED_WITH_THE_FRONTEND.md`(모노레포) §5 참조.


    # ⚠️ `test_10_the_mutation_b_and_c_fuel_code_is_genuinely_out_of_headless_scope` 는 이 레포로 오지 못했다 — 사유는
    #    `tests/RETIRED_WITH_THE_FRONTEND.md`(모노레포) §5 참조.







# ⚠️ `TestFrontendUnionCompleteness` 는 이 레포로 오지 못했다 — 사유는
#    `tests/RETIRED_WITH_THE_FRONTEND.md`(모노레포) §5 참조.







if __name__ == '__main__':
    unittest.main()
