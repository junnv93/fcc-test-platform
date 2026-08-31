"""Seals for the two derivations that replaced five hand-rolled route regexes
and one identifier-spelling rule (`conformance-gate-proposition-axis`, 2026-08-21).

The consumers live in ``tests/test_frontend_architecture_conformance.py``; what
is sealed HERE is the machinery they rest on, because a derivation that is wrong
makes every assertion built on it wrong in the same direction — silently, and in
the direction of green.

Three axes:

* :class:`TestTheMaskIsLengthPreserving` — ``mask_ts_noncode`` promises the
  masked copy has the SAME offsets as its source. Every span this wave finds is
  found in the mask and read from the original, so a mask that shifted by one
  character would read the wrong text with no visible failure.
* :class:`TestTheObjectParserReadsStructureNotText` — the parser must not INVENT
  members (a parser that does cannot be used to assert completeness) and must not
  MISS them (one missed member is one unchecked screen).
* :class:`TestTheRouteWalkIsTotalOrLoud` — the walk either follows an element to
  its declaration or raises. There is no third answer, and the absence of a third
  answer is the entire repair: the seal this replaces had one, spelled
  ``the regex matched nothing``.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "tests") not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from support.frontend_error_copy import (  # noqa: E402
    VOCABULARY_AXIS_LIMITATION,
    code_token_sites,
    census_copy_tables,
    enclosing_container,
    error_code_vocabulary,
    status_keys,
)
from support.frontend_route_registry import (  # noqa: E402
    UnresolvedRouteElementError,
    collect_route_entries,
)
from support.parity import (  # noqa: E402
    MASK_LIMITATION,
    TsUnbalancedRegionError,
    iter_ts_object_literals,
    mask_ts_noncode,
    match_brackets,
    parse_ts_literal_members,
    split_top_level,
    strip_ts_comments,
)


WEB_SRC = PROJECT_ROOT / "apps" / "web" / "src"

#: Sources whose shape the mask has to survive. Chosen because each carries the
#: construct that defeated an earlier generation of this repository's TS scanning:
#: a regex literal with a brace quantifier, a template literal, and JSX.
_MASK_CORPUS = (
    "const re = /\\d{2}[,'\"]/u; const a = { k: 1 };",
    "const s = 'a { b } c'; const t = `x ${y} z`; const o = { k: 2 };",
    "const el = <div className=\"a{b}\">{value}</div>;",
    "// a comment with { and ' and /re/\nconst o = { k: 3 };",
    "/* block { with ' quote */ const o = { k: 4 };",
)


class TestTheMaskIsLengthPreserving(unittest.TestCase):
    """Offsets in the mask must mean what they mean in the source."""

    def test_the_mask_has_the_same_length_and_line_breaks(self) -> None:
        for source in _MASK_CORPUS:
            with self.subTest(source[:32]):
                masked = mask_ts_noncode(source)
                self.assertEqual(len(masked), len(source), "마스크가 오프셋을 옮긴다")
                self.assertEqual(
                    masked.count("\n"), source.count("\n"), "마스크가 줄 번호를 옮긴다"
                )

    def test_code_characters_are_untouched(self) -> None:
        for source in _MASK_CORPUS:
            with self.subTest(source[:32]):
                masked = mask_ts_noncode(source)
                for index, (original, rendered) in enumerate(zip(source, masked)):
                    if rendered not in (" ", "\n", "\r"):
                        self.assertEqual(
                            rendered,
                            original,
                            f"{index}: 마스크가 코드 문자를 바꿨다",
                        )

    def test_literal_content_is_gone_and_delimiters_remain(self) -> None:
        masked = mask_ts_noncode("const s = 'brace { here';")
        self.assertNotIn("brace", masked, "문자열 내용이 마스크에 남아 있다")
        self.assertNotIn("{", masked, "문자열 안의 중괄호가 깊이 계산을 오염시킨다")
        self.assertEqual(masked.count("'"), 2, "문자열 경계가 사라졌다")

    def test_the_real_tree_survives_the_mask(self) -> None:
        """**세 성질 전부** 를 실제 소스 전량에 대해 다시 묻는다.

        합성 코퍼스는 *내가 생각해 낸* 구문만 담는다. 트리는 그렇지 않다.
        ⚠️ 이 검사는 한때 길이와 줄 수만 물었고, 그래서 *내용* 축(코드 문자가 그대로인가)
        은 합성 문자열 다섯 개에만 걸려 있었다 — 독립 적대 평가가 그 비대칭을 지적했다.
        """
        sources = [path for path in sorted(WEB_SRC.rglob("*.ts*")) if path.is_file()]
        self.assertGreater(len(sources), 0, "스캔 대상이 없다 — 이 검사가 공허하다")
        for path in sources:
            text = path.read_text(encoding="utf-8")
            masked = mask_ts_noncode(text)
            self.assertEqual(
                (len(masked), masked.count("\n")),
                (len(text), text.count("\n")),
                f"{path}: 마스크가 오프셋 또는 줄 번호를 옮긴다",
            )
            for index, (original, rendered) in enumerate(zip(text, masked)):
                if rendered not in (" ", "\n", "\r"):
                    self.assertEqual(
                        rendered, original, f"{path}:{index}: 마스크가 코드 문자를 바꿨다"
                    )

    def test_a_template_substitution_keeps_its_code(self) -> None:
        """`${…}` 안은 **텍스트가 아니라 코드**다 — 지우면 그 자리가 통째로 사라진다.

        ⚠️ 이것을 **두 기존 불변식 다 볼 수 없었다.** *"비-공백 출력 문자는 입력과
        같다"* 는 블랭크에 대해 공허하게 참이고, 괄호 균형 봉인은 여는/닫는 괄호가
        **함께** 블랭크되므로 균형이 유지된다. 그래서 이 축은 **양성 단언**이어야 한다.
        """
        source = "const s = `a ${x['DRAFT_EMPTY']} b`;"
        masked = mask_ts_noncode(source)
        self.assertIn("${x[", masked, "치환 안의 코드가 지워졌다")
        self.assertNotIn("DRAFT_EMPTY", masked, "치환 안 *문자열*의 내용은 여전히 블랭크여야 한다")
        self.assertNotIn("a ", masked.split("`")[1][:3], "템플릿 텍스트가 코드로 남았다")
        self.assertEqual(
            [code for code, _ in code_token_sites(source, frozenset({"DRAFT_EMPTY"}))],
            ["DRAFT_EMPTY"],
            "치환 안에서 이름 불린 코드를 어휘 축이 보지 못한다",
        )

    def test_a_nested_template_and_a_comment_inside_a_substitution(self) -> None:
        """치환은 문장이 담을 수 있는 것을 전부 담는다 — 전체 디스패치여야 한다."""
        masked = mask_ts_noncode("const s = `a ${ f(`in ${y} ner`) /* c */ } b`;")
        self.assertIn("${ f(`", masked, "중첩 템플릿에서 desync 했다")
        self.assertIn("${y}", masked, "중첩 치환의 코드가 지워졌다")
        self.assertNotIn("/* c */", masked, "치환 안 주석이 코드로 남았다")

    def test_the_real_tree_keeps_the_code_in_every_substitution(self) -> None:
        """실 트리 전량 — 치환이 있는 자리마다 코드가 살아 있는가.

        합성 프로브는 *내가 생각해 낸* 한 형태만 시험한다. 이 검사는
        **비어 있지 않은 치환이 통째로 블랭크된 자리가 하나도 없다**를 단언한다.
        """
        emptied: list[str] = []
        seen = 0
        for path in sorted(WEB_SRC.rglob("*.ts*")):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            masked = mask_ts_noncode(text)
            index = 0
            while True:
                index = masked.find("${", index)
                if index < 0:
                    break
                close = masked.find("}", index)
                if close < 0:
                    break
                seen += 1
                if text[index + 2 : close].strip() and not masked[index + 2 : close].strip():
                    emptied.append(f"{path.relative_to(WEB_SRC).as_posix()}:{index}")
                index = close + 1
        self.assertGreater(seen, 0, "치환을 하나도 찾지 못했다 — 이 검사가 공허하다")
        self.assertEqual(emptied, [], f"내용이 있는 치환이 통째로 블랭크됐다: {emptied[:5]}")

    def test_the_named_mask_limitation_is_absent_from_the_real_tree(self) -> None:
        """선언된 한계가 **오늘의 트리에 실재하지 않는다**는 것을 실행으로 확인한다.

        ``MASK_LIMITATION`` 은 JSX 텍스트의 맨 ``'`` 나 ``//`` 가 문자열/주석을 여는
        것으로 읽혀 뒤의 코드가 지워질 수 있다고 적는다. 산문으로만 두면 그것은
        사각지대다. 마스크 결과의 괄호가 균형을 이루는가 — 그 한계가 발화하면 거의
        확실히 깨지는 성질 — 를 전 소스에 대해 묻는다. 깨지는 날 red 로 알려 준다.
        """
        self.assertIn("JSX", MASK_LIMITATION, "한계 고지가 무엇에 대한 것인지 말하지 않는다")
        sources = [path for path in sorted(WEB_SRC.rglob("*.ts*")) if path.is_file()]
        unbalanced: list[str] = []
        for path in sources:
            masked = mask_ts_noncode(path.read_text(encoding="utf-8"))
            depth = 0
            for char in masked:
                if char in "([{":
                    depth += 1
                elif char in ")]}":
                    depth -= 1
                if depth < 0:
                    break
            if depth != 0:
                unbalanced.append(path.relative_to(WEB_SRC).as_posix())
        self.assertEqual(
            unbalanced,
            [],
            "마스크 결과의 괄호가 균형을 잃었다 — 선언된 JSX 한계가 실재하기 시작했거나 "
            f"렉서가 다른 곳에서 desync 한다: {unbalanced}",
        )

    def test_the_strip_rendering_is_unaffected(self) -> None:
        """마스킹 축이 형제 렌더링을 건드리지 않았다.

        ``strip_ts_comments`` 는 400+ 단언의 단일 판정 입력이다. 여기서 한 글자라도
        달라지면 이 웨이브가 만지지 않은 게이트가 움직인다 — 그래서 그 성질은
        *희망* 이 아니라 단언이어야 한다.
        """
        for source in _MASK_CORPUS:
            with self.subTest(source[:32]):
                stripped = strip_ts_comments(source)
                self.assertNotIn("//", stripped)
                # 문자열 내용은 strip 에서 **보존** 된다 — 그것이 두 렌더링의 차이다.
                if "'a { b } c'" in source:
                    self.assertIn("'a { b } c'", stripped)


class TestTheObjectParserReadsStructureNotText(unittest.TestCase):
    """A parser used for a completeness claim may neither invent nor miss."""

    def test_members_are_read_at_their_first_token_only(self) -> None:
        """``cond ? a : b`` 의 ``:`` 는 키가 아니다.

        어디서나 ``ident:`` 를 찾는 스캐너는 삼항과 ``case`` 라벨을 멤버로 읽는다.
        멤버를 지어내는 파서는 *"멤버 집합이 완전하다"* 를 말할 수 없다.
        """
        source = "const o = { a: flag ? x : y, b: 2 };"
        literal = self._only_object(source)
        self.assertEqual(
            [entry.key for entry in literal.entries if entry.kind == "entry"],
            ["a", "b"],
            "삼항의 콜론을 키로 읽었다",
        )

    def test_every_key_spelling_is_read(self) -> None:
        source = "const o = { plain: 1, 404: 2, 'quoted': 3, [COMPUTED]: 4, ...spread };"
        literal = self._only_object(source)
        self.assertEqual(
            [(entry.kind, entry.key) for entry in literal.entries],
            [
                ("entry", "plain"),
                ("entry", "404"),
                ("entry", "quoted"),
                ("entry", "COMPUTED"),
                ("spread", "spread"),
            ],
            "키 철자 중 하나를 못 읽는다 — 저자가 고른 철자에 판정이 의존한다",
        )

    def test_a_brace_inside_a_string_does_not_close_the_literal(self) -> None:
        source = "const o = { a: '}', b: 2 };"
        literal = self._only_object(source)
        self.assertEqual(
            [entry.key for entry in literal.entries if entry.kind == "entry"],
            ["a", "b"],
            "문자열 안의 중괄호가 리터럴을 조기 종료시킨다",
        )

    def test_a_brace_inside_a_regex_does_not_close_the_literal(self) -> None:
        source = "const o = { a: /x{2}/u, b: 2 };"
        literal = self._only_object(source)
        self.assertEqual(
            [entry.key for entry in literal.entries if entry.kind == "entry"],
            ["a", "b"],
            "정규식 리터럴의 중괄호가 리터럴을 조기 종료시킨다 — 앞 세대의 균형 "
            "괄호 창이 정확히 이것에 깨졌다",
        )

    def test_a_key_is_read_only_at_a_members_first_token(self) -> None:
        """`.match` → `.search` 변이가 살아남았다 — 그 변이는 키를 **지어낸다**.

        형제 검사(`test_members_are_read_at_their_first_token_only`)의 픽스처는 첫
        토큰이 진짜 키라 두 술어가 같은 답을 준다. 배열 원소가 삼항일 때 비로소
        갈라진다: `search` 는 `x :` 를 보고 키 `x` 를 만들어 낸다.
        """
        source = "const a = [ flag ? x : y ];"
        members = parse_ts_literal_members(source, source.index("["), source.rindex("]") + 1)
        self.assertEqual(
            [(entry.kind, entry.key) for entry in members],
            [("element", None)],
            "삼항 원소에서 키를 지어냈다 — 멤버를 지어내는 파서는 완전성을 말할 수 없다",
        )

    def test_the_bracket_map_is_read_only(self) -> None:
        """캐시된 함수가 가변 dict 를 돌려주면 한 호출자가 다음 호출자의 답을 오염시킨다.

        ⚠️ 이 성질은 라운드 1 에서 고쳐졌지만 **봉인이 없었고**, 라운드 2 의 변이가
        그것을 정확히 지적했다(가변 dict 로 되돌려도 전량 green).
        """
        mapping = match_brackets("{}")
        with self.assertRaises(TypeError):
            mapping[99] = 100  # type: ignore[index]

    def test_an_unbalanced_region_is_loud_not_shorter(self) -> None:
        """짝 없는 여는 괄호는 멤버를 **줄인다** — 실패가 아니라 더 짧은 답이 된다.

        JSX 텍스트의 맨 `(` 는 유효한 TSX 이고 오늘 트리에 실재한다(`routes/reports.tsx`).
        닫는 쪽(`)`/`]`)은 이미 시끄러웠고 여는 쪽만 조용했다 — 그 비대칭이 이것을
        보이지 않게 했다.
        """
        source = "[ { a: 1 }, ( , { b: 2 } ]"
        with self.assertRaises(TsUnbalancedRegionError):
            split_top_level(mask_ts_noncode(source), 0, len(source))

    def test_nested_literals_are_reported_separately(self) -> None:
        literals = iter_ts_object_literals("const o = { a: { b: 1 } };")
        self.assertEqual(len(literals), 2, "중첩 리터럴이 하나로 접혔다")

    def _only_object(self, source: str):
        literals = [
            literal
            for literal in iter_ts_object_literals(source)
            if any(entry.kind in ("entry", "spread") for entry in literal.entries)
        ]
        self.assertEqual(len(literals), 1, f"객체 리터럴을 정확히 하나 찾지 못했다: {literals}")
        return literals[0]

    def test_members_can_be_parsed_from_a_bare_fragment(self) -> None:
        """``handle: { titleKey: … }`` 처럼 잘려 나온 조각도 같은 규칙으로 읽힌다."""
        fragment = "{ titleKey: 'routes.x.title' }"
        members = parse_ts_literal_members(fragment, 0, len(fragment))
        self.assertEqual(
            [(entry.key, entry.value) for entry in members],
            [("titleKey", "'routes.x.title'")],
        )


class TestTheCopyTableCensusAsksAProposition(unittest.TestCase):
    """(a) — the census must key on SHAPE, and must not exempt what it cannot read."""

    ERRORS_TS = WEB_SRC / "ui" / "errors.ts"

    def test_an_unreadable_key_is_reported_not_exempted(self) -> None:
        source = "const M = { [codes.x]: 'errors.a' };"
        census = census_copy_tables(source)
        self.assertEqual(len(census.unreadable), 1, "읽을 수 없는 키를 조용히 넘겼다")
        self.assertEqual(census.code_keyed, ())

    def test_a_computed_constant_key_still_reads_as_a_code(self) -> None:
        source = "const M = { [SOME_CODE]: 'errors.a' };"
        self.assertEqual(len(census_copy_tables(source).code_keyed), 1)

    def test_a_computed_variable_key_is_unreadable_not_absent(self) -> None:
        """⚠️ 독립 적대 평가가 정확히 이 자리를 뚫었다.

        `{ [conflictCode]: 'errors.…' }` 는 **어느 버킷에도** 들어가지 않았다 —
        SCREAMING_SNAKE 가 아니라 `code_keyed` 도 아니고, `isidentifier()` 가 참이라
        `unreadable` 도 아니었다. 양옆의 두 검사(`[codes.x]` · `[SOME_CODE]`)가 그
        구멍을 사이에 두고 서 있었다.
        """
        census = census_copy_tables("const M = { [conflictCode]: 'errors.a' };")
        self.assertEqual(len(census.unreadable), 1, "변수 계산 키가 조용히 넘어갔다")
        self.assertEqual(census.code_keyed, ())

    def test_a_concatenated_copy_key_still_counts(self) -> None:
        """`'errors.' + x` — 키를 한 토큰에 다 적지 않았을 뿐 운영 문구다."""
        self.assertEqual(
            len(census_copy_tables("const M = { A_CODE: 'errors.' + suffix };").code_keyed), 1
        )

    def test_a_published_code_is_found_however_it_is_spelled(self) -> None:
        """어휘 발생 축 — 구문을 열거하지 않는다.

        `new Map` · `switch` · if-chain 은 객체 리터럴이 아니라 구조 축이 묻지 않는다.
        묻는 것은 *발행된 code 를 이름으로 불렀는가* 이고, 그것은 철자와 무관하다.
        """
        vocabulary = frozenset({"DRAFT_EMPTY"})
        for label, source in (
            ("bare identifier", "if (c === DRAFT_EMPTY) return 'errors.draftEmpty';"),
            ("single-quoted", "case 'DRAFT_EMPTY': return 'errors.draftEmpty';"),
            ("double-quoted", 'const m = new Map([["DRAFT_EMPTY", "errors.draftEmpty"]]);'),
        ):
            with self.subTest(label):
                self.assertEqual(
                    [code for code, _ in code_token_sites(source, vocabulary)],
                    ["DRAFT_EMPTY"],
                    "발행된 code 의 출현을 놓쳤다",
                )

    def test_the_vocabulary_axis_announces_its_own_edge(self) -> None:
        """오탐 가능성을 **선언된 한계**로 둔다 — 숨은 것과 이름 붙은 것은 다르다.

        ⚠️ 발행 코드에는 평범한 영어 단어가 있다(`CONFLICT` · `NOT_FOUND` · `FORBIDDEN`).
        축은 토큰을 묻지 의미를 묻지 않으므로 그것을 구분할 수 없고, **바로 그 이유로**
        `switch`/`Map` 안에서 이름 불린 코드를 볼 수 있다. 대신 폭발 반경이 구조로
        제한된다 — 사이트는 *운영 문구를 내는 모듈* 안에서만 보고된다.
        """
        for word in ("CONFLICT", "NOT_FOUND", "FORBIDDEN"):
            self.assertIn(word, VOCABULARY_AXIS_LIMITATION, "한계 고지가 충돌 단어를 이름으로 대지 않는다")
        vocabulary = error_code_vocabulary(PROJECT_ROOT / "docs" / "api")
        self.assertTrue(
            {"CONFLICT", "NOT_FOUND", "FORBIDDEN"} & vocabulary,
            "고지가 말하는 충돌이 실제 어휘에 없다 — 고지가 낡았다",
        )
        # 그 단어가 실제로 사이트로 잡힌다는 것 자체를 고정한다(고지가 사실인지 확인).
        self.assertEqual(
            [code for code, _ in code_token_sites(
                "export type Verdict = 'CONFLICT' | 'RESOLVED';", frozenset({"CONFLICT"})
            )],
            ["CONFLICT"],
            "고지가 설명하는 오탐이 재현되지 않는다 — 고지가 틀렸다",
        )

    def test_a_code_named_only_in_a_comment_is_not_a_site(self) -> None:
        """규칙을 설명하는 문장이 위반이면 사람들이 설명을 지운다."""
        self.assertEqual(
            code_token_sites("// DRAFT_EMPTY is refined above\n", frozenset({"DRAFT_EMPTY"})),
            (),
        )

    def test_a_backtick_copy_key_is_still_a_copy_key(self) -> None:
        """``t(`errors.x`)`` 는 ``t('errors.x')`` 와 똑같이 해소된다 — 한 글자로 규칙을
        지나갈 수 있으면 그것은 다시 철자를 묻는 규칙이다."""
        self.assertEqual(len(census_copy_tables("const M = { A_CODE: `errors.a` };").code_keyed), 1)

    def test_a_value_that_merely_contains_a_copy_key_still_counts(self) -> None:
        """값이 리터럴 *이어야* 한다고 요구하면 삼항 하나로 면제된다."""
        source = "const M = { A_CODE: cond ? 'errors.a' : 'errors.b' };"
        self.assertEqual(len(census_copy_tables(source).code_keyed), 1)

    def test_a_map_whose_values_are_not_copy_keys_is_ignored(self) -> None:
        source = "const M = { SOME_CODE: 42, OTHER: someFn() };"
        self.assertEqual(census_copy_tables(source), ((), ()))

    def test_two_sibling_tables_have_no_common_container(self) -> None:
        source = (
            "const A = { X_CODE: 'errors.x' };\nconst B = { Y_CODE: 'errors.y' };\n"
        )
        census = census_copy_tables(source)
        self.assertEqual(len(census.code_keyed), 2)
        self.assertIsNone(enclosing_container(source, census.code_keyed))

    def test_nested_tables_share_their_container(self) -> None:
        source = "const T = { 404: { X_CODE: 'errors.x' }, 503: { Y_CODE: 'errors.y' } };"
        census = census_copy_tables(source)
        container = enclosing_container(source, census.code_keyed)
        self.assertIsNotNone(container)
        assert container is not None
        self.assertEqual(source[container.start], "{")

    def test_the_container_is_the_smallest_one_not_the_largest(self) -> None:
        """`min` → `max` 변이가 살아남았다 — "가장 작은"이 하중을 진다고 적어 놓고서.

        가장 바깥을 고르면 파일 전체를 감싸는 블록이 컨테이너가 되고, 그러면 status
        키 단언이 공허해진다(블록에는 키가 없다).
        """
        source = (
            "function wrap() {\n"
            "  const T = { 404: { X_CODE: 'errors.x' }, 503: { Y_CODE: 'errors.y' } };\n"
            "  return T;\n"
            "}\n"
        )
        census = census_copy_tables(source)
        container = enclosing_container(source, census.code_keyed)
        self.assertIsNotNone(container)
        assert container is not None
        self.assertEqual(
            sorted(status_keys(container)),
            ["404", "503"],
            "가장 작은 컨테이너가 아니라 함수 본문을 골랐다 — status 단언이 공허해진다",
        )

    def test_the_container_is_never_one_of_the_tables(self) -> None:
        """표 하나뿐일 때 그 표 자신을 컨테이너로 고르면 규칙이 자기 자신을 만족시킨다."""
        source = "const M = { A_CODE: 'errors.a' };"
        census = census_copy_tables(source)
        self.assertEqual(len(census.code_keyed), 1)
        self.assertIsNone(
            enclosing_container(source, census.code_keyed),
            "표 하나가 자기 자신을 감싸는 것으로 읽혔다",
        )

    def test_every_key_spelling_is_recorded_not_only_the_computed_one(self) -> None:
        """`key_kind` 는 네 값이고 넷 다 하중을 진다 — 봉인은 하나뿐이었다."""
        source = "const o = { plain: 1, 404: 2, 'quoted': 3, [COMPUTED]: 4 };"
        literal = next(
            literal
            for literal in iter_ts_object_literals(source)
            if any(entry.kind == "entry" for entry in literal.entries)
        )
        self.assertEqual(
            [(entry.key, entry.key_kind) for entry in literal.entries],
            [("plain", "ident"), ("404", "number"), ("quoted", "string"), ("COMPUTED", "computed")],
            "키 철자 기록이 소비자의 판정을 바꿀 수 있는데 넷 중 일부만 맞다",
        )

    def test_the_vocabulary_comes_from_the_contract_artifacts(self) -> None:
        vocabulary = error_code_vocabulary(PROJECT_ROOT / "docs" / "api")
        self.assertGreater(len(vocabulary), 0)
        self.assertIn("NOT_FOUND", vocabulary, "발행된 코드가 어휘에 없다 — 소스가 틀렸다")

    def test_an_empty_artifact_directory_is_loud(self) -> None:
        """빈 어휘는 모든 소속 단언을 공허하게 참으로 만든다."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                error_code_vocabulary(Path(tmp))


class TestTheRouteWalkIsTotalOrLoud(unittest.TestCase):
    """(b) — every array member is followed, or the walk raises."""

    ENTRY = """
import {{ extra }} from '@/shared/extra';
export const appRoutes = [
  {{ path: '/', element: <L/>, errorElement: <E/>, handle: {{ titleKey: 'shell' }},
    children: [
      {{ index: true, element: <H/>, errorElement: <E/>, handle: {{ titleKey: 'home' }} }},
      ...{spread},
    ] }},
];
"""
    EXTRA = "export const extra = [{member}];\n"
    GOOD_MEMBER = "{ path: 'ok', element: <X/>, errorElement: <E/>, handle: { titleKey: 'ok' } }"

    def _tree(self, *, spread: str = "extra", member: str | None = None, entry: str | None = None):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "app.tsx").write_text(
            entry if entry is not None else self.ENTRY.format(spread=spread), encoding="utf-8"
        )
        (root / "shared").mkdir()
        (root / "shared" / "extra.tsx").write_text(
            self.EXTRA.format(member=member if member is not None else self.GOOD_MEMBER),
            encoding="utf-8",
        )
        return tmp, root

    def test_a_cross_module_route_is_collected_with_its_address(self) -> None:
        tmp, root = self._tree()
        with tmp:
            entries = collect_route_entries(root)
        self.assertIn("/ok", {entry.address for entry in entries})
        self.assertEqual(
            {entry.module for entry in entries},
            {"app.tsx", "shared/extra.tsx"},
            "전개된 모듈이 파생에 기여하지 않았다",
        )

    def test_a_relative_specifier_resolves_too(self) -> None:
        entry = self.ENTRY.format(spread="extra").replace("'@/shared/extra'", "'./shared/extra'")
        tmp, root = self._tree(entry=entry)
        with tmp:
            self.assertIn("/ok", {e.address for e in collect_route_entries(root)})

    def test_an_aliased_named_import_resolves_too(self) -> None:
        entry = self.ENTRY.format(spread="aliased").replace(
            "import { extra }", "import { extra as aliased }"
        )
        tmp, root = self._tree(entry=entry)
        with tmp:
            self.assertIn("/ok", {e.address for e in collect_route_entries(root)})

    def test_both_arms_of_a_gated_ternary_are_collected(self) -> None:
        """빌드 플래그가 세우는 라우트도 누군가 제목을 줘야 하는 라우트다.

        ⚠️ **두 팔 모두에 라우트를 둔다.** 앞 판은 첫 팔에만 라우트를 두고 둘째를
        `[]` 로 두었고, 그래서 `arrays[:1]` 변이 — 둘째 팔을 통째로 버리는 것 — 가
        살아남았다. 픽스처가 시험하지 않는 자리는 시험되지 않는다.
        """
        extra = (
            "export const extra = flag\n"
            "  ? [{ path: 'gated', element: <X/>, errorElement: <E/>, handle: { titleKey: 'g' } }]\n"
            "  : [{ path: 'ungated', element: <Y/>, errorElement: <E/>, handle: { titleKey: 'u' } }];\n"
        )
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "app.tsx").write_text(self.ENTRY.format(spread="extra"), encoding="utf-8")
        (root / "shared").mkdir()
        (root / "shared" / "extra.tsx").write_text(extra, encoding="utf-8")
        with tmp:
            addresses = {e.address for e in collect_route_entries(root)}
        self.assertEqual(
            sorted(addresses),
            ["/", "/gated", "/ungated"],
            "게이트된 배열의 한 팔이 조용히 사라졌다",
        )

    def test_an_array_consumed_by_an_expression_raises(self) -> None:
        """``[…].concat(hidden)`` 은 배열 리터럴 스캔에 완벽하게 읽히고 조용히 절반을 잃는다.

        메서드 호출 **하나**로 이 걷기를 무력화할 수 있었다. 완전성을 주장하는
        파생이 못 읽은 것을 없는 것으로 세는 그 형태 그대로다.
        """
        member = self.GOOD_MEMBER
        for label, tail in (("concat", ".concat(hidden)"), ("index", "[0]")):
            with self.subTest(label):
                tmp, root = self._tree()
                with tmp:
                    (root / "shared" / "extra.tsx").write_text(
                        f"export const extra = [{member}]{tail};\n", encoding="utf-8"
                    )
                    with self.assertRaises(UnresolvedRouteElementError):
                        collect_route_entries(root)

    def test_an_initialiser_that_is_not_an_array_raises(self) -> None:
        tmp, root = self._tree()
        with tmp:
            (root / "shared" / "extra.tsx").write_text(
                "export const extra = makeRoutes();\n", encoding="utf-8"
            )
            with self.assertRaises(UnresolvedRouteElementError):
                collect_route_entries(root)

    def test_both_arms_of_a_gated_children_array_are_collected(self) -> None:
        """중첩 배열도 같은 헬퍼로 찾는다 — `.index("[")` 지름길은 한 팔을 조용히 버렸다."""
        entry = """
export const appRoutes = [
  { path: '/', element: <L/>, errorElement: <E/>, handle: { titleKey: 'shell' },
    children: f
      ? [{ path: 'x', element: <X/>, errorElement: <E/>, handle: { titleKey: 'x' } }]
      : [{ path: 'y', element: <Y/>, handle: { titleKey: 'y' } }] },
];
"""
        tmp, root = self._tree(entry=entry)
        with tmp:
            entries = collect_route_entries(root)
        self.assertEqual(
            sorted(e.address for e in entries),
            ["/", "/x", "/y"],
            "게이트된 children 의 한 팔이 사라졌다",
        )
        self.assertEqual(
            [e.address for e in entries if not e.has_error_element],
            ["/y"],
            "버려졌던 팔의 결함이 보이지 않는다",
        )

    def test_children_that_is_not_an_array_literal_raises(self) -> None:
        entry = (
            "export const appRoutes = [\n"
            "  { path: '/', element: <L/>, errorElement: <E/>, handle: { titleKey: 's' },\n"
            "    children: someRoutes },\n"
            "];\n"
        )
        tmp, root = self._tree(entry=entry)
        with tmp:
            with self.assertRaises(UnresolvedRouteElementError):
                collect_route_entries(root)

    def test_a_declaration_without_a_semicolon_stops_at_the_next_statement(self) -> None:
        """ASI 로 끝나는 선언이 파일 나머지를 삼키지 않는다.

        삼키면 다음 선언의 배열이 이 선언의 것으로 읽히고, 그 과잉은 *조용한* 실패가
        아니라 엉뚱한 곳에서 나는 실패라 진단이 더 비싸다.
        """
        entry = (
            "import { extra } from '@/shared/extra';\n"
            "export const appRoutes = [\n"
            "  { path: '/', element: <L/>, errorElement: <E/>, handle: { titleKey: 's' },\n"
            "    children: [...extra] },\n"
            "]\n"
            "export const other = [1, 2, 3];\n"
        )
        tmp, root = self._tree(entry=entry)
        with tmp:
            self.assertEqual(
                sorted(e.address for e in collect_route_entries(root)),
                ["/", "/ok"],
            )

    def test_a_handle_with_a_type_assertion_still_yields_its_title(self) -> None:
        """`{…} as AppRouteHandle` 은 `{` 로 시작하지만 `}` 로 끝나지 않는다."""
        entry = (
            "export const appRoutes = [{ path: 'z', element: <Z/>, errorElement: <E/>, "
            "handle: { titleKey: 'z' } as AppRouteHandle }];\n"
        )
        tmp, root = self._tree(entry=entry)
        with tmp:
            self.assertEqual(
                [(e.address, e.title_key) for e in collect_route_entries(root)],
                [("/z", "z")],
            )

    def test_one_array_spread_under_two_parents_is_collected_twice(self) -> None:
        """부모 사슬을 담은 `seen` 키 — **봉인 없던 수정 ①**.

        ⚠️ 독립 평가 C 가 이 수정을 `(module, name)` 으로 되돌리는 변이를 심었고
        **살아남았다**. 등가 변이가 아니다: 공유 배열을 두 부모에 전개하는 것은
        평범한 react-router 형태이고, 되돌리면 둘째 부모의 사본이 — 경계가 없더라도 —
        예외 없이 사라진다.
        """
        entry = (
            "import { shared } from '@/shared/s';\nexport const appRoutes = [\n"
            "  { path: 'a', element: <A/>, errorElement: <E/>, handle: { titleKey: 'a' },"
            " children: [...shared] },\n"
            "  { path: 'b', element: <B/>, errorElement: <E/>, handle: { titleKey: 'b' },"
            " children: [...shared] },\n];\n"
        )
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "app.tsx").write_text(entry, encoding="utf-8")
        (root / "shared").mkdir()
        (root / "shared" / "s.tsx").write_text(
            "export const shared = [{ path: 'x', element: <S/>, handle: { titleKey: 'x' } }];\n",
            encoding="utf-8",
        )
        with tmp:
            entries = collect_route_entries(root)
        self.assertEqual(
            sorted(e.address for e in entries),
            ["/a", "/a/x", "/b", "/b/x"],
            "공유 배열이 둘째 부모 아래에서 사라졌다",
        )
        self.assertEqual(
            sorted(e.address for e in entries if not e.has_error_element),
            ["/a/x", "/b/x"],
            "사라진 사본의 결함이 보이지 않는다",
        )

    def test_the_array_may_not_be_mutated_after_its_declaration(self) -> None:
        """변이 가드 — **봉인 없던 수정 ②**. 네 철자 전부.

        ⚠️ 넷 다 독립 평가가 **실 `app.tsx` 에** 얹어 전량 green 을 받아낸 형태다
        (경계 없는 라우트 + 어느 로케일에도 없는 이름표). 그리고 `_ARRAY_MUTATORS = ()`
        변이가 살아남았다 — 가드는 있는데 그것을 잡는 검사가 없었다.
        """
        target = "{ path: 'z', element: <Z/> }"
        for label, tail in (
            ("bare", f"extra.push({target});"),
            ("type-asserted", f"(extra as AppRoute[]).push({target});"),
            ("alias", f"const r = extra;\nr.push({target});"),
            ("index write", f"extra[extra.length] = {target};"),
        ):
            with self.subTest(label):
                tmp, root = self._tree(member=self.GOOD_MEMBER)
                with tmp:
                    (root / "shared" / "extra.tsx").write_text(
                        f"export const extra = [{self.GOOD_MEMBER}];\n{tail}\n", encoding="utf-8"
                    )
                    with self.assertRaises(UnresolvedRouteElementError):
                        collect_route_entries(root)

    def test_the_mutation_guard_does_not_judge_an_unrelated_binding(self) -> None:
        """오탐 축 — 게이트가 남의 코드를 거부하면 사람들이 게이트를 끈다.

        ⚠️ 독립 평가가 `registry.appRoutes.push('x')` — **완전히 무관한 객체의 멤버** —
        가 raise 하는 것을 찾아냈다. 이름이 같은 것과 같은 바인딩인 것은 다르다.
        """
        for label, tail in (
            ("member of another object", "registry.extra.push('x');"),
            ("different name", "otherExtra.push({ path: 'z', element: <Z/> });"),
            ("reads one element", "const first = extra[0];"),
        ):
            with self.subTest(label):
                tmp, root = self._tree(member=self.GOOD_MEMBER)
                with tmp:
                    (root / "shared" / "extra.tsx").write_text(
                        f"export const extra = [{self.GOOD_MEMBER}];\n{tail}\n", encoding="utf-8"
                    )
                    self.assertIn("/ok", {e.address for e in collect_route_entries(root)})

    def test_an_object_spread_inside_a_route_object_is_resolved(self) -> None:
        """세 입장 중 셋째만 옳다 — 버리지도, 거부하지도 않고 **해소**한다.

        ⚠️ 조용히 버리면 *더 나쁜* 오답이다(`{ ...routeCommon, path: 'jobs' }` 를
        "경계 없음"으로 **고발**한다). 거부하면 `errorElement` 를 공유하는 가장 흔한
        형태를 막고, 그러면 게이트가 꺼진다. 그래서 전개를 따라가 키를 병합한다 —
        자기 키가 이기는 것은 JavaScript 의 규칙 그대로다.
        """
        common = "const routeCommon = { errorElement: <E/>, handle: { titleKey: 'shared' } };\n"
        cases = (
            ("공유가 경계를 준다", "{ ...routeCommon, path: 'ok', element: <X/> }", True, "shared"),
            (
                "자기 키가 이긴다",
                "{ ...routeCommon, path: 'ok', element: <X/>, handle: { titleKey: 'own' } }",
                True,
                "own",
            ),
        )
        for label, member, boundary, title in cases:
            with self.subTest(label):
                tmp, root = self._tree()
                with tmp:
                    (root / "shared" / "extra.tsx").write_text(
                        common + f"export const extra = [{member}];\n", encoding="utf-8"
                    )
                    entry = next(e for e in collect_route_entries(root) if e.address == "/ok")
                self.assertEqual((entry.has_error_element, entry.title_key), (boundary, title))

    def test_a_spread_that_supplies_no_boundary_is_still_reported(self) -> None:
        """해소가 관대함이 아니라는 것 — 공유가 경계를 안 주면 그대로 결함이다."""
        tmp, root = self._tree()
        with tmp:
            (root / "shared" / "extra.tsx").write_text(
                "const bare = { handle: { titleKey: 'x' } };\n"
                "export const extra = [{ ...bare, path: 'ok', element: <X/> }];\n",
                encoding="utf-8",
            )
            entry = next(e for e in collect_route_entries(root) if e.address == "/ok")
        self.assertFalse(entry.has_error_element, "경계를 주지 않는 공유가 통과했다")

    def test_an_unfollowable_object_spread_still_raises(self) -> None:
        """**봉인 없던 수정 ③** — 못 따라가는 전개는 여전히 loud 다."""
        for label, extra in (
            ("미선언 식별자", "export const extra = [{ ...unknownThing, path: 'ok', element: <X/> }];\n"),
            ("호출식", "export const extra = [{ ...make(), path: 'ok', element: <X/> }];\n"),
        ):
            with self.subTest(label):
                tmp, root = self._tree()
                with tmp:
                    (root / "shared" / "extra.tsx").write_text(extra, encoding="utf-8")
                    with self.assertRaises(UnresolvedRouteElementError):
                        collect_route_entries(root)

    def test_a_two_level_shared_default_is_followed(self) -> None:
        """전개 안의 전개 — **라운드 3 수정이 만든 회귀**.

        ⚠️ 첫 판은 해소된 객체의 멤버를 `kind == "entry"` 로 걸렀다. 한 층 위에서 방금
        없앤 바로 그 필터다. 두 단계 공유 기본값(`{ ...CHILD }` 이 `...BASE` 를 전개)에서
        안쪽이 주는 것을 전부 잃고, **멀쩡한 라우트를 경계 없다고 고발**했다 — 그 수정이
        스스로 이름 붙인 두 실패 모드를 한 층 아래에서 그대로 재생산한 것이다.
        """
        tmp, root = self._tree()
        with tmp:
            (root / "shared" / "extra.tsx").write_text(
                "const BASE_D = { errorElement: <E/>, handle: { titleKey: 'shared' } };\n"
                "const CHILD_D = { ...BASE_D };\n"
                "export const extra = [{ ...CHILD_D, path: 'ok', element: <X/> }];\n",
                encoding="utf-8",
            )
            entry = next(e for e in collect_route_entries(root) if e.address == "/ok")
        self.assertEqual((entry.has_error_element, entry.title_key), (True, "shared"))

    def test_children_from_a_spread_is_read_from_its_own_module(self) -> None:
        """멤버는 **자기가 온 파일**을 기억한다 — 라운드 3 수정이 만든 회귀 둘째.

        전개는 다른 모듈의 멤버를 이 객체에 병합하고 그 오프셋은 **그 모듈**의 것이다.
        `TsEntry` 만 들고 오면 `children` 을 로컬 소스에서 남의 오프셋으로 잘라내
        운 좋으면 `IndexError`, 운 나쁘면 **다른 파일의 바이트를 이 라우트의 자식으로**
        읽는다. 둘 다 이 모듈이 세운 계약("못 따라가면 시끄럽게")을 어긴다.
        """
        tmp, root = self._tree()
        with tmp:
            (root / "shared" / "extra.tsx").write_text(
                "const PRESET = { errorElement: <E/>, handle: { titleKey: 'p' },\n"
                "  children: [{ path: 'kid', element: <K/>, errorElement: <E/>,"
                " handle: { titleKey: 'k' } }] };\n"
                "export const extra = [{ ...PRESET, path: 'ok', element: <X/> }];\n",
                encoding="utf-8",
            )
            addresses = sorted(e.address for e in collect_route_entries(root))
        # 셸과 그 index 자식이 둘 다 `/` 를 답한다 — 주소는 정체성이 아니다(위 참조).
        self.assertEqual(
            addresses, ["/", "/", "/ok", "/ok/kid"], "전개가 실어 온 children 을 잃었다"
        )

    def test_a_spread_that_follows_a_key_wins(self) -> None:
        """병합 순서는 JavaScript 의 것이다 — 뒤에 오는 전개가 이긴다."""
        tmp, root = self._tree()
        with tmp:
            (root / "shared" / "extra.tsx").write_text(
                "const over = { path: 'spreadwins' };\n"
                "export const extra = [{ path: 'own', element: <X/>, errorElement: <E/>,"
                " handle: { titleKey: 'k' }, ...over }];\n",
                encoding="utf-8",
            )
            addresses = {e.address for e in collect_route_entries(root)}
        self.assertIn("/spreadwins", addresses, "자기 키가 뒤따르는 전개를 이겼다 — 런타임과 다르다")

    def test_a_function_local_binding_is_not_the_module_one(self) -> None:
        """변이 가드도 **스코프를 본다** — 선언 해소는 라운드 1부터 그랬다.

        ⚠️ 그 비대칭이 오탐이었다: 자기 이름의 지역 배열을 만들어 `push` 하는 평범한
        헬퍼가 모듈 배열을 만졌다고 고발당했다.
        """
        for label, tail in (
            (
                "지역 그림자",
                "export function build() { const extra: AppRoute[] = [];"
                " extra.push({ path: 'z', element: <Z/> }); return extra; }",
            ),
            (
                "무관한 지역 별칭",
                "const r = extra;\nexport function f() { const r: string[] = []; r.push('x'); }",
            ),
            ("함수 안의 읽기 전용 사용", "export function b() { return toRouteObjects(extra); }"),
        ):
            with self.subTest(label):
                tmp, root = self._tree()
                with tmp:
                    (root / "shared" / "extra.tsx").write_text(
                        f"export const extra = [{self.GOOD_MEMBER}];\n{tail}\n", encoding="utf-8"
                    )
                    self.assertIn("/ok", {e.address for e in collect_route_entries(root)})

    def test_the_array_may_not_escape_into_a_module_scope_call(self) -> None:
        """**라운드 3 의 헤드라인.** 변이의 *이름* 을 세는 것으로는 절대 닿을 수 없다.

        `registerExtraRoutes(appRoutes as AppRoute[])` 는 변이자 이름을 하나도 적지
        않는다. 여기 적혀 있는 것은 **바인딩이 빠져나갔다**는 사실이고, 그것이 물을 수
        있는 유일한 질문이다. 독립 평가가 이 형태로 어느 로케일에도 없는 titleKey 를
        등록했다 — 571 passed, `tsc` clean.
        """
        target = "{ path: 'z', element: <Z/> }"
        for label, tail in (
            ("헬퍼 호출이 배열을 받는다", "registerExtra(extra as AppRoute[]);"),
            ("옵셔널 호출", f"extra.push?.({target});"),
            ("계산 멤버", f"extra['push']({target});"),
            ("length 대입", "extra.length = 0;"),
        ):
            with self.subTest(label):
                tmp, root = self._tree()
                with tmp:
                    (root / "shared" / "extra.tsx").write_text(
                        f"export const extra = [{self.GOOD_MEMBER}];\n{tail}\n", encoding="utf-8"
                    )
                    with self.assertRaises(UnresolvedRouteElementError):
                        collect_route_entries(root)

    def test_a_declared_but_valueless_boundary_is_not_a_boundary(self) -> None:
        """`errorElement: undefined` 는 키를 선언하고 아무것도 렌더하지 않는다."""
        tmp, root = self._tree(
            member="{ path: 'ok', element: <X/>, errorElement: undefined, handle: { titleKey: 'k' } }"
        )
        with tmp:
            entry = next(e for e in collect_route_entries(root) if e.address == "/ok")
        self.assertFalse(entry.has_error_element, "값 없는 선언이 경계로 셈됐다")

    def test_a_type_argument_comma_is_not_a_declarator(self) -> None:
        """`, NAME` 은 타입 인자 목록에도 나온다 — 실 트리에서 **유령 선언 8건**이었다."""
        tmp, root = self._tree()
        with tmp:
            (root / "shared" / "extra.tsx").write_text(
                "type Pair = Map<string, extra>;\n"
                f"export const extra = [{self.GOOD_MEMBER}];\n",
                encoding="utf-8",
            )
            self.assertIn("/ok", {e.address for e in collect_route_entries(root)})

    def test_a_multi_declarator_statement_is_accepted(self) -> None:
        """오탐 축 — `export const a = 1, extra = [...]` 는 정당한 TypeScript 다."""
        tmp, root = self._tree()
        with tmp:
            (root / "shared" / "extra.tsx").write_text(
                f"export const version = 1, extra = [{self.GOOD_MEMBER}];\n", encoding="utf-8"
            )
            self.assertIn("/ok", {e.address for e in collect_route_entries(root)})

    def test_an_array_member_that_is_neither_object_nor_spread_raises(self) -> None:
        """**봉인 없던 수정 ④** — 이 raise 를 `continue` 로 바꾼 변이가 살아남았다.

        그 분기가 곧 이 모듈의 명제다: 못 읽은 것을 없는 것으로 세지 않는다.
        """
        tmp, root = self._tree()
        with tmp:
            (root / "shared" / "extra.tsx").write_text(
                f"export const extra = [makeRoute(), {self.GOOD_MEMBER}];\n", encoding="utf-8"
            )
            with self.assertRaises(UnresolvedRouteElementError):
                collect_route_entries(root)

    def test_a_missing_declaration_raises(self) -> None:
        tmp, root = self._tree()
        with tmp:
            (root / "shared" / "extra.tsx").write_text("export const other = [];\n", encoding="utf-8")
            with self.assertRaises(UnresolvedRouteElementError):
                collect_route_entries(root)

    def test_a_missing_module_raises(self) -> None:
        tmp, root = self._tree()
        with tmp:
            (root / "shared" / "extra.tsx").unlink()
            with self.assertRaises(UnresolvedRouteElementError):
                collect_route_entries(root)

    def test_a_package_specifier_raises(self) -> None:
        entry = self.ENTRY.format(spread="extra").replace("'@/shared/extra'", "'some-package'")
        tmp, root = self._tree(entry=entry)
        with tmp:
            with self.assertRaises(UnresolvedRouteElementError):
                collect_route_entries(root)

    def test_a_non_literal_path_raises(self) -> None:
        tmp, root = self._tree(
            member="{ path: SOME_CONST, element: <X/>, errorElement: <E/>, handle: { titleKey: 'x' } }"
        )
        with tmp:
            with self.assertRaises(UnresolvedRouteElementError):
                collect_route_entries(root)

    def test_a_non_literal_handle_raises(self) -> None:
        tmp, root = self._tree(
            member="{ path: 'x', element: <X/>, errorElement: <E/>, handle: SHARED_HANDLE }"
        )
        with tmp:
            with self.assertRaises(UnresolvedRouteElementError):
                collect_route_entries(root)

    def test_a_route_object_with_neither_path_nor_index_raises(self) -> None:
        tmp, root = self._tree(member="{ element: <X/>, errorElement: <E/>, handle: { titleKey: 'x' } }")
        with tmp:
            with self.assertRaises(UnresolvedRouteElementError):
                collect_route_entries(root)

    def test_an_index_route_has_its_own_identity(self) -> None:
        """주소는 정체성이 아니다 — 서로 다른 부모 아래의 index 라우트 둘.

        ⚠️ 주소로 만든 집합은 그 둘을 **조용히 하나로 합치고**, 주소로 이름을 대는
        진단은 자식의 결함에 **부모의 주소**를 댄다.
        """
        entry = (
            "export const appRoutes = [\n"
            "  { path: 'a', element: <A/>, errorElement: <E/>, handle: { titleKey: 'a' },"
            " children: [{ index: true, element: <X/>, handle: { titleKey: 'x' } }] },\n"
            "  { path: 'b', element: <B/>, errorElement: <E/>, handle: { titleKey: 'b' },"
            " children: [{ index: true, element: <Y/>, errorElement: <E/>, handle: { titleKey: 'y' } }] },\n"
            "];\n"
        )
        tmp, root = self._tree(entry=entry)
        with tmp:
            entries = collect_route_entries(root)
        self.assertEqual(
            sorted(e.address for e in entries),
            ["/a", "/a", "/b", "/b"],
            "주소가 정체성이라는 전제가 실제로 깨진다는 사실 자체를 고정한다",
        )
        self.assertEqual(
            sorted(e.identity for e in entries),
            ["/a", "/a (index)", "/b", "/b (index)"],
            "정체성이 index 라우트를 구분하지 못한다",
        )
        self.assertEqual(
            [e.identity for e in entries if not e.has_error_element],
            ["/a (index)"],
            "자식의 결함에 부모의 이름이 붙는다",
        )

    def test_a_cycle_terminates(self) -> None:
        """순환은 트리의 결함이지 무한 루프의 사유가 아니다."""
        tmp, root = self._tree()
        with tmp:
            (root / "shared" / "extra.tsx").write_text(
                "export const extra = [...extra];\n", encoding="utf-8"
            )
            self.assertEqual(
                {e.address for e in collect_route_entries(root)},
                {"/"},
                "순환 전개가 라우트를 만들어 냈다",
            )

    def test_the_walk_is_read_only(self) -> None:
        """합성 트리를 읽는 것 말고 아무것도 하지 않는다 — 파일이 그대로다."""
        tmp, root = self._tree()
        with tmp:
            before = {p.name: p.read_bytes() for p in root.rglob("*.tsx")}
            collect_route_entries(root)
            after = {p.name: p.read_bytes() for p in root.rglob("*.tsx")}
        self.assertEqual(before, after)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
