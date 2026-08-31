"""Cross-language SSOT parity helper — single source of truth for the
"two sources name the same set of things" discipline (B3, 2026-06-13).

FCC verifies several cross-language / cross-artifact SSOTs by asserting that
two independently-authored sources agree on a *set* of names:

  * RBAC permissions      — backend universe ↔ ``apps/web`` permission consts.
  * central DB schema      — schema JSON ↔ local ORM (field-mapping bridge).
  * OIDC env anchors       — backend ``HttpAuthConfig`` env keys ↔ frontend
                             runtime-config keys (shared IdP coordination).

Each of those used to (or would) re-implement the same three primitives:

  1. a *loud, side-attributed* set-equality assertion (so a drift failure says
     exactly what each side has that the other lacks), and
  2. a docstring/comment-aware TypeScript parser (so a token mentioned inside a
     doc comment never counts as a real declaration).

The equipment reference repo (`verify-lint-ruleset-parity.mjs` /
`check-env-sync.mjs` / `check-role-config-sync.mjs`) shares one generalized
set-equality discipline across all its parity checks. FCC mirrors that *intent*
— not its structure — as this dependency-free pytest support module (the
Python↔TS language barrier rules out a shared import package; see
``docs/adr/0012-cross-language-ssot-parity-framework.md``).

dependency-free: standard library only (re), so it imports cleanly in every
parity test without dragging in src/ or third-party packages.
"""
from __future__ import annotations

import re
import unittest
from functools import lru_cache
from types import MappingProxyType
from typing import Iterable, Mapping, NamedTuple


__all__ = [
    'diff_report',
    'assert_set_equality',
    'strip_ts_comments',
    'mask_ts_noncode',
    'match_brackets',
    'split_top_level',
    'parse_ts_literal_members',
    'iter_ts_object_literals',
    'TsEntry',
    'TsObjectLiteral',
    'TsUnbalancedRegionError',
    'parse_ts_export_const_strings',
    'parse_ts_object_keys',
    'TsAnchorNotFoundError',
]


class TsAnchorNotFoundError(LookupError):
    """A TypeScript object anchor named by a parity test no longer exists.

    Raised instead of returning an empty key set so that renaming the TS
    declaration fails the parity gate loudly rather than making it vacuous.
    """


# --------------------------------------------------------------------------- #
# Set-equality with a loud, side-attributed diff (MUST #1/#2).
# --------------------------------------------------------------------------- #
def diff_report(
    *,
    name: str,
    left_label: str,
    left: Iterable[str],
    right_label: str,
    right: Iterable[str],
) -> str:
    """Build a human-readable set-difference report (pure — testable alone).

    The report names *which side* has the extra/missing element, so a parity
    failure points the reader straight at the source that drifted.
    """
    left_set, right_set = set(left), set(right)
    missing_from_right = left_set - right_set
    extra_in_right = right_set - left_set
    return (
        f"\n{name} parity drift between {left_label} and {right_label}:\n"
        f"  in {left_label} but MISSING from {right_label}: {sorted(missing_from_right)}\n"
        f"  in {right_label} but MISSING from {left_label}: {sorted(extra_in_right)}\n"
        f"Add/remove the matching declaration so both sides name the same set."
    )


def assert_set_equality(
    test_case: unittest.TestCase,
    *,
    name: str,
    left_label: str,
    left: Iterable[str],
    right_label: str,
    right: Iterable[str],
) -> None:
    """Assert ``set(left) == set(right)`` with a ``diff_report`` failure message."""
    left_set, right_set = set(left), set(right)
    test_case.assertEqual(
        left_set,
        right_set,
        diff_report(
            name=name,
            left_label=left_label,
            left=left_set,
            right_label=right_label,
            right=right_set,
        ),
    )


# --------------------------------------------------------------------------- #
# Docstring/comment-aware TypeScript parsing.
# --------------------------------------------------------------------------- #
# A ``/`` may legally begin a regular expression only where an *operand* is
# expected. These are the punctuation characters after which that is true.
#
# Three characters are deliberately absent, and each absence was measured:
#   ``)`` ``]`` close an operand, so what follows is division
#          (``(n) / 2``, ``arr[i] / 2``).
#   ``}``  is ambiguous in JavaScript and unambiguous in *this* codebase: these
#          sources are TSX, where ``<Foo bar={1} />`` puts ``}`` immediately
#          before a slash that is markup, not a regex. Treating it as an operand
#          position made the lexer swallow the rest of the line — including a
#          real trailing comment. A regex directly after a block close has no
#          occurrence in ``apps/web/src``; the JSX shape has many.
_REGEX_PRECEDING_PUNCT = frozenset("(,=:[!&|?{;+-~^")

# ...and the keywords after which the same is true. ``return`` is not one
# example among many here: the defect this lexer was blind to is literally
# ``return /[",\n\r]/u.test(text)`` in
# ``apps/web/src/routes/test-plans/BulkRowsEditor.tsx``.
_REGEX_PRECEDING_KEYWORDS = frozenset(
    {
        "return",
        "typeof",
        "instanceof",
        "new",
        "delete",
        "void",
        "throw",
        "case",
        "do",
        "else",
        "yield",
        "await",
        "in",
        "of",
    }
)

_IDENT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")

#: Line terminators kept verbatim by :func:`_blanked` so a masked copy reports
#: the same line numbers as its source. Everything else becomes a space, which
#: is what makes the copy the SAME LENGTH as the source — the property every
#: offset-based consumer depends on.
_MASK_PRESERVED = frozenset("\n\r")


def _blanked(text: str) -> str:
    """``text`` with every character replaced by a space except line breaks."""
    return "".join(ch if ch in _MASK_PRESERVED else " " for ch in text)


class _TsCommentStripper:
    """Single-pass lexer that drops ``//`` line + ``/* */`` block comments
    while preserving string/template literal and **regular-expression literal**
    content verbatim, so a token mentioned inside a doc comment never counts as
    a declaration.

    The regex-literal arm is load-bearing, not defensive. Without it a quote
    inside a character class — ``/[",\\n\\r]/u`` — opens a string the lexer
    never closes on that line, and the lexer stays in string state for the rest
    of the file. Measured on ``BulkRowsEditor.tsx``: **37 line comments survived
    the strip**, so their contents were read as code by every caller. Two
    frontend assertions flipped to false positives on that alone.
    """

    def __init__(self, text: str, *, mask: bool = False) -> None:
        self._s = text
        self._i = 0
        self._n = len(text)
        self._out: list[str] = []
        # ``mask`` swaps the OUTPUT rule only, never the state machine: comment,
        # string and regex *content* becomes length-preserving blanks instead of
        # being dropped (comments) or copied (literals). The significance trail
        # below still sees the REAL characters, so regex-vs-division is decided
        # identically in both modes — one lexer, two renderings. See
        # ``mask_ts_noncode``.
        self._mask = mask
        # The last two significant (non-whitespace) characters emitted, and the
        # identifier currently being accumulated. Together they answer the only
        # question this lexer needs: is a ``/`` here an operator or an operand?
        self._last = ""
        self._prev = ""
        self._word = ""

    def strip(self) -> str:
        while self._i < self._n:
            ch = self._s[self._i]
            nxt = self._s[self._i + 1] if self._i + 1 < self._n else ""
            if ch == "/" and nxt == "/":
                start = self._i
                self._i += 2
                while self._i < self._n and self._s[self._i] != "\n":
                    self._i += 1
                self._blank(start, self._i)
            elif ch == "/" and nxt == "*":
                start = self._i
                self._i += 2
                while self._i < self._n and not (
                    self._s[self._i] == "*"
                    and self._i + 1 < self._n
                    and self._s[self._i + 1] == "/"
                ):
                    self._i += 1
                self._i += 2
                self._blank(start, min(self._i, self._n))
            elif ch == "`":
                self._consume_template()
            elif ch in ("'", '"'):
                self._consume_string(ch)
            elif ch == "/" and self._regex_can_start():
                self._consume_regex()
            else:
                self._emit(ch)
                self._i += 1
        return "".join(self._out)

    # -- significance tracking ---------------------------------------------- #

    def _blank(self, start: int, end: int) -> None:
        """Mask mode: stand in for ``self._s[start:end]`` with equal-length
        whitespace, keeping newlines so line numbers survive. Emits nothing at
        all in strip mode — that is what makes ``strip_ts_comments`` output
        byte-identical to the version before masking existed. The significance
        trail is deliberately NOT updated: a comment never influenced whether the
        next ``/`` opens a regex, and it must not start to.
        """
        if not self._mask:
            return
        self._out.append(_blanked(self._s[start:end]))

    def _emit(self, text: str, *, blank: bool = False) -> None:
        """Append ``text`` (blanked when asked) and update the significance trail.

        The trail always advances on the REAL characters — masking is a rendering
        decision, not a lexing one.
        """
        self._out.append(_blanked(text) if (blank and self._mask) else text)
        for ch in text:
            # Whitespace does not end the trailing identifier. `return /re/` has
            # a space between the keyword and the slash, so a lexer that resets
            # the word on whitespace never sees `return` and reads the regex as
            # division — which is exactly the defect being repaired here.
            if ch.isspace():
                continue
            was_ident = self._last in _IDENT_CHARS
            self._prev = self._last
            self._last = ch
            if ch in _IDENT_CHARS:
                self._word = self._word + ch if was_ident else ch
            else:
                self._word = ""

    def _regex_can_start(self) -> bool:
        """Whether a ``/`` at the cursor opens a regex literal rather than
        dividing. Errs toward *division* when unsure — mis-reading division as a
        regex would swallow real code, while mis-reading a regex as division only
        risks the pre-existing string desync on that one line."""
        last = self._last
        if last == "":
            return True
        if last == "<":
            # `</div>` — a JSX closing tag, never a regex. Reading it as one
            # swallows markup up to the next `/`.
            return False
        if last == ">":
            # `=>` introduces an operand position (`x => /re/.test(x)`), a bare
            # `>` closes a JSX tag and does not.
            return self._prev == "="
        if last in _REGEX_PRECEDING_PUNCT:
            return True
        if last in _IDENT_CHARS:
            return self._word in _REGEX_PRECEDING_KEYWORDS
        return False

    # -- literal consumption ------------------------------------------------ #

    def _consume_string(self, quote: str) -> None:
        # The delimiters stay visible in mask mode so a consumer can still tell
        # "a string literal is here" — only its CONTENT is blanked, which is what
        # makes bracket depth counting exact.
        self._emit(self._s[self._i])
        self._i += 1
        while self._i < self._n:
            ch = self._s[self._i]
            if ch == "\\":
                self._emit(self._s[self._i : self._i + 2], blank=True)
                self._i += 2
                continue
            if ch == quote:
                self._emit(ch)
                self._i += 1
                return
            self._emit(ch, blank=True)
            self._i += 1

    def _consume_template(self) -> None:
        """A backtick literal — text blanked, ``${…}`` substitutions kept as CODE.

        ⚠️ **The substitution arm is mask-mode only, and that is what keeps
        ``strip_ts_comments`` byte-identical.** In strip mode this walks the
        template exactly as the old string consumer did (everything verbatim), so
        a comment inside a substitution is still preserved there — changing that
        would move the judgement input of ~400 assertions this wave never touched.

        Why the mask must differ: a substitution holds real code, and blanking it
        wholesale erased it from every offset-based consumer. Measured — the
        vocabulary census found **zero** sites in
        ``const s = `a ${x['DRAFT_EMPTY']} b`;``. ⚠️ Neither existing invariant
        could see that: *"every non-blank output char equals its input"* is
        vacuously true of a blank, and the brace-balance seal passes because BOTH
        braces were blanked together.
        """
        self._emit(self._s[self._i])  # opening backtick
        self._i += 1
        while self._i < self._n:
            ch = self._s[self._i]
            if ch == "\\":
                self._emit(self._s[self._i : self._i + 2], blank=True)
                self._i += 2
                continue
            if ch == "`":
                self._emit(ch)
                self._i += 1
                return
            if self._mask and ch == "$" and self._s[self._i + 1 : self._i + 2] == "{":
                self._emit(self._s[self._i : self._i + 2])  # `${` is code
                self._i += 2
                self._consume_substitution()
                continue
            self._emit(ch, blank=True)
            self._i += 1

    def _consume_substitution(self) -> None:
        """Mask mode: the inside of a ``${…}``, rendered as the code it is.

        Full dispatch, because a substitution can hold anything a statement can —
        nested templates, strings, regexes and comments all appear in this tree.
        """
        depth = 1
        while self._i < self._n and depth:
            ch = self._s[self._i]
            nxt = self._s[self._i + 1] if self._i + 1 < self._n else ""
            if ch == "/" and nxt == "/":
                start = self._i
                while self._i < self._n and self._s[self._i] != "\n":
                    self._i += 1
                self._blank(start, self._i)
                continue
            if ch == "/" and nxt == "*":
                start = self._i
                self._i += 2
                while self._i < self._n and not (
                    self._s[self._i] == "*" and self._s[self._i + 1 : self._i + 2] == "/"
                ):
                    self._i += 1
                self._i += 2
                self._blank(start, min(self._i, self._n))
                continue
            if ch == "`":
                self._consume_template()
                continue
            if ch in ("'", '"'):
                self._consume_string(ch)
                continue
            if ch == "/" and self._regex_can_start():
                self._consume_regex()
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    self._emit(ch)
                    self._i += 1
                    return
            self._emit(ch)
            self._i += 1

    def _consume_regex(self) -> None:
        self._emit(self._s[self._i])  # opening '/'
        self._i += 1
        in_class = False
        while self._i < self._n:
            ch = self._s[self._i]
            if ch == "\\":
                self._emit(self._s[self._i : self._i + 2], blank=True)
                self._i += 2
                continue
            if ch == "\n":
                # A regex literal cannot span lines, so this was a division we
                # mis-read. Everything up to here is already emitted verbatim;
                # bail rather than consuming further, which keeps the damage on
                # one line instead of desynchronising the rest of the file.
                return
            if ch == "/" and not in_class:
                self._emit(ch)
                self._i += 1
                break
            self._emit(ch, blank=True)
            self._i += 1
            if ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
        while self._i < self._n and self._s[self._i].isalpha():
            self._emit(self._s[self._i], blank=True)
            self._i += 1


@lru_cache(maxsize=1024)
def strip_ts_comments(ts_source: str) -> str:
    """Return ``ts_source`` with ``//`` and ``/* */`` comments removed, string,
    template-literal and regex-literal content preserved verbatim.

    Memoised because it is now the single judgement input for 400+ assertions
    that re-read the same ~150 sources: the frontend conformance seal alone calls
    it 112 times. The lexer is a per-character loop where the copies it replaced
    were single regex passes, so without the cache consolidating the SSOT would
    have made that lane ~4x slower (measured 3.7s -> 15.9s, and back to 4.4s with
    the cache). Sound because the function is pure — same source in, same string
    out, no I/O and no dependence on anything but its argument.
    """
    return _TsCommentStripper(ts_source).strip()


@lru_cache(maxsize=256)
def mask_ts_noncode(ts_source: str) -> str:
    """``ts_source`` with comment / string / regex **content** blanked out,
    **same length, same line breaks, same code characters**.

    Why a second rendering instead of reusing :func:`strip_ts_comments`
    ----------------------------------------------------------------------
    ``strip_ts_comments`` keeps literal content verbatim — correct for the
    "does this token appear as code" question that its ~400 callers ask, and
    wrong for the two questions this module grew next: *where does this object
    literal end* and *what are its entries*. A brace inside a string
    (``'{'``) or a quantifier inside a regex (``/\\d{2}/``) desynchronises any
    depth counter that reads the stripped text, and the previous generation of
    that counter — a balanced-brace regex window — was defeated in review by
    exactly a regex literal.

    Length preservation is the load-bearing property, not a convenience: the
    masked copy is used to FIND spans and the ORIGINAL is used to READ them, so
    offsets must mean the same thing in both. A mask that collapsed comments
    would silently shift every span after the first doc block.

    See :data:`MASK_LIMITATION` for the one class of input where the *content*
    rule does not hold.
    """
    return _TsCommentStripper(ts_source, mask=True).strip()


#: ⚠️ The one input class where masking blanks a CODE character.
#:
#: This lexer reads JSX **text** as ordinary JavaScript, because it has no JSX
#: mode — an inherited property of ``strip_ts_comments``, not something masking
#: introduced. So a bare apostrophe or a bare ``//`` in rendered text
#: (``<p>it's fine</p>``, ``<p>a // b</p>``) opens a string or a comment that is
#: not there, and the mask blanks real code after it.
#:
#: It is named here rather than fixed for two reasons. Fixing it means teaching
#: the shared lexer JSX, which changes ``strip_ts_comments`` for its ~400 other
#: callers — a different wave. And it is **measurably absent today**: this
#: repository writes user-visible text through ``t()``, never as JSX text, and
#: ``tests/test_frontend_route_registry_support.py`` asserts that every source in
#: ``apps/web/src`` still masks to a brace-balanced result. The day that stops
#: being true, that assertion is red — which is the difference between a known
#: limitation and a blind spot.
MASK_LIMITATION = (
    "mask_ts_noncode has no JSX mode: a bare ' or // inside JSX TEXT is read as a "
    "string/comment opener and the code after it is blanked. Zero occurrences in "
    "apps/web/src today; sealed by a brace-balance assertion over the real tree."
)


#: Bracket pairs tracked when splitting a literal into its top-level parts.
_BRACKET_PAIRS = {"{": "}", "[": "]", "(": ")"}
_CLOSERS = {close: open_ for open_, close in _BRACKET_PAIRS.items()}

#: The four spellings an object-literal key can take. ``computed`` covers
#: ``[SOME_CONST]:``; ``string`` covers ``'404':``. All four are recognised so a
#: consumer's judgement never depends on which spelling an author happened to
#: use — that dependency is the defect class this module's newer callers exist
#: to remove.
_ENTRY_KEY_RE = re.compile(
    r"""(?P<ident>[A-Za-z_$][A-Za-z0-9_$]*)\s*:
      | (?P<number>[0-9]+)\s*:
      | (?P<string>'(?P<sq>[^'\n]*)'|"(?P<dq>[^"\n]*)")\s*:
      | \[(?P<computed>[^\]\n]*)\]\s*:
    """,
    re.X,
)

_SPREAD_RE = re.compile(r"\.\.\.\s*(?P<expr>[A-Za-z_$][A-Za-z0-9_$.]*)")


class TsEntry(NamedTuple):
    """One top-level member of an object or array literal.

    ``key`` is ``None`` for array elements and for spreads; ``kind`` says which
    of those it is, so a consumer never has to infer it from ``key is None``.
    ``value`` is sliced from the ORIGINAL source (comments and literal content
    intact) — the mask exists to find the boundary, not to be read.
    """

    kind: str  # 'entry' | 'spread' | 'element'
    key: str | None
    value: str
    start: int
    end: int
    #: HOW the key was spelled: 'ident' | 'number' | 'string' | 'computed', or ''
    #: when there is no key. ⚠️ Load-bearing, not bookkeeping — a consumer deciding
    #: whether it can READ a key needs to know whether the text it got is the key
    #: itself or an *expression that produces* one. Flattening the four spellings
    #: to one let ``{ [conflictCode]: … }`` read as a plain identifier key and slip
    #: past a census whose whole job was to report what it could not decide.
    key_kind: str = ""
    #: Offset of the member's VALUE in the source (``start`` when there is no key).
    #: A consumer that re-cut the value out of ``[start, end)`` by searching for a
    #: bracket read the key as part of the value — and a structural walk that has
    #: to guess where a value begins is not structural.
    value_start: int = -1


class TsObjectLiteral(NamedTuple):
    """A ``{...}`` span and its top-level entries. ``start`` indexes the ``{``
    and ``end`` the position just past the matching ``}``."""

    start: int
    end: int
    entries: tuple[TsEntry, ...]

    def get(self, key: str) -> TsEntry | None:
        for entry in self.entries:
            if entry.kind == "entry" and entry.key == key:
                return entry
        return None

    def has(self, key: str) -> bool:
        return self.get(key) is not None


@lru_cache(maxsize=256)
def match_brackets(masked: str) -> "Mapping[int, int]":
    """``{open_index: index_just_past_close}`` for every bracket in ``masked``.

    Takes the MASKED text (see :func:`mask_ts_noncode`) so quotes and regexes
    cannot unbalance the count. Unclosed brackets are simply absent from the
    map — the caller decides whether that is a parse failure, because "this file
    does not parse" and "this anchor is missing" are different diagnoses.

    Memoised, and therefore returned READ-ONLY: a cached function that hands back
    a mutable dict hands every caller a way to corrupt the next caller's answer.
    """
    stack: list[tuple[str, int]] = []
    pairs: dict[int, int] = {}
    for index, char in enumerate(masked):
        if char in _BRACKET_PAIRS:
            stack.append((char, index))
        elif char in _CLOSERS:
            while stack and stack[-1][0] != _CLOSERS[char]:
                # A stray closer: drop the mismatched frames rather than
                # abandoning the whole file. TSX (`<a>{x}</a>`) is balanced for
                # our purposes, but being total here means one odd construct
                # costs one span instead of every span after it.
                stack.pop()
            if stack:
                pairs[stack.pop()[1]] = index + 1
    return MappingProxyType(pairs)


class TsUnbalancedRegionError(ValueError):
    """A bracketed region whose interior brackets do not pair up.

    ⚠️ Raised rather than tolerated because the *symptom* of tolerating it is a
    SHORTER answer, not an error: an unclosed ``(`` keeps the depth counter above
    zero for the rest of the region, so no further comma splits a member and the
    literal reads as having fewer members than it has. An independent adversarial
    review found the input that produces it in the tree already — a bare ``(`` in
    JSX text, which is valid TSX and occurs today in ``routes/reports.tsx``. The
    mirror cases (``)`` and ``]``) were already loud; only the opener was silent,
    and that asymmetry is what made it invisible.
    """


def split_top_level(masked: str, start: int, end: int) -> list[tuple[int, int]]:
    """Comma-separated top-level slices of the literal spanning ``[start, end)``.

    ``start`` indexes the opening bracket, ``end`` the position past its match.
    Empty slices (a trailing comma, an elision) are dropped. An interior bracket
    that never closes raises :class:`TsUnbalancedRegionError` — see there.
    """
    parts: list[tuple[int, int]] = []
    depth = 0
    piece = start + 1
    for index in range(start + 1, end - 1):
        char = masked[index]
        if char in _BRACKET_PAIRS:
            depth += 1
        elif char in _CLOSERS:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append((piece, index))
            piece = index + 1
    if depth != 0:
        raise TsUnbalancedRegionError(
            f"brackets do not balance inside [{start}, {end}) (depth {depth} at the end) — "
            "member splitting would return a SHORTER list rather than fail, so the caller "
            "would silently see fewer members than the literal has"
        )
    parts.append((piece, end - 1))
    return [
        (begin + len(masked[begin:stop]) - len(masked[begin:stop].lstrip()), stop)
        for begin, stop in parts
        if masked[begin:stop].strip()
    ]


def parse_ts_literal_members(source: str, start: int, end: int) -> tuple[TsEntry, ...]:
    """Top-level members of the ``{...}`` or ``[...]`` literal at ``[start, end)``.

    ⚠️ Keys are recognised **only at a member's first token**. Scanning for
    ``ident:`` anywhere would read the ``:`` of a conditional expression
    (``cond ? a : b``) and the ``:`` of a ``case`` label as keys — and a parser
    that invents members cannot be used to assert that a set of members is
    complete.
    """
    masked = mask_ts_noncode(source)
    members: list[TsEntry] = []
    for begin, stop in split_top_level(masked, start, end):
        text = source[begin:stop]
        spread = _SPREAD_RE.match(masked, begin, stop)
        if spread is not None:
            members.append(
                TsEntry(
                    "spread",
                    source[spread.start("expr") : spread.end("expr")],
                    text,
                    begin,
                    stop,
                    "",
                    begin,
                )
            )
            continue
        key_match = _ENTRY_KEY_RE.match(masked, begin, stop)
        if key_match is None:
            members.append(TsEntry("element", None, text, begin, stop, "", begin))
            continue
        if key_match.lastgroup is None:  # pragma: no cover - defensive
            members.append(TsEntry("element", None, text, begin, stop, "", begin))
            continue
        # The mask blanks string CONTENT, so a quoted key has to be read back
        # from the original at the same offsets — which is exactly the property
        # `mask_ts_noncode` promises.
        if key_match.group("string") is not None:
            key = source[key_match.start("string") + 1 : key_match.end("string") - 1]
            key_kind = "string"
        elif key_match.group("computed") is not None:
            key = source[key_match.start("computed") : key_match.end("computed")].strip()
            key_kind = "computed"
        elif key_match.group("number") is not None:
            key = key_match.group("number")
            key_kind = "number"
        else:
            key = key_match.group("ident")
            key_kind = "ident"
        value_text = source[key_match.end() : stop]
        members.append(
            TsEntry(
                "entry",
                key,
                value_text.strip(),
                begin,
                stop,
                key_kind,
                key_match.end() + (len(value_text) - len(value_text.lstrip())),
            )
        )
    return tuple(members)


def iter_ts_object_literals(source: str) -> "list[TsObjectLiteral]":
    """Every ``{...}`` literal in ``source``, each with its top-level entries.

    Blocks of statements come back too — this deliberately does not try to tell
    an object literal from a function body, because every consumer so far
    identifies its targets by the SHAPE of the entries (which keys, which
    values), and a classifier here would be a second place for that judgement to
    live. A statement block simply parses to zero (or nonsense-keyed) entries and
    fails every shape predicate.
    """
    masked = mask_ts_noncode(source)
    pairs = match_brackets(masked)
    return [
        TsObjectLiteral(open_at, close_at, parse_ts_literal_members(source, open_at, close_at))
        for open_at, close_at in sorted(pairs.items())
        if masked[open_at] == "{"
    ]


def parse_ts_export_const_strings(
    ts_source: str,
    *,
    name_pattern: str = r"[A-Za-z_][A-Za-z0-9_]*",
) -> dict[str, str]:
    """Return ``{const_name: token}`` for every
    ``export const <name> = '<token>';`` declaration, ignoring any inside
    comments. ``name_pattern`` restricts which const names are collected
    (e.g. ``r"PERMISSION_[A-Z0-9_]+"`` for the RBAC permission SSOT)."""
    stripped = strip_ts_comments(ts_source)
    regex = re.compile(
        r"export\s+const\s+(" + name_pattern + r")\s*=\s*'([^']+)'\s*;",
    )
    return {m.group(1): m.group(2) for m in regex.finditer(stripped)}


def parse_ts_object_keys(ts_source: str, *, object_expr: str) -> set[str]:
    """Return the top-level (brace-depth 1) identifier keys declared inside the
    object literal that follows ``object_expr`` in ``ts_source``.

    Comment-aware. Used to read the runtime-config Zod schema keys without a
    TS toolchain — e.g. ``object_expr='runtimeConfigSchema = z.object('``
    returns ``{'apiBaseUrl', 'oidcIssuer', ...}``. Only depth-1 keys are
    returned so nested objects (e.g. ``featureFlags: z.object({...})``)
    contribute their own name, not their inner keys.
    """
    stripped = strip_ts_comments(ts_source)
    start = stripped.find(object_expr)
    if start == -1:
        # An absent anchor is a DRIFTED anchor, not an empty object. Returning
        # set() here made every downstream parity assertion vacuously true
        # (∅ ∩ anchors == ∅ == ∅), so a TypeScript rename silently disarmed the
        # cross-language gate instead of failing it. Fail loudly at the parse
        # boundary — the caller cannot distinguish "no keys" from "wrong anchor".
        raise TsAnchorNotFoundError(
            f"object anchor {object_expr!r} not found in the TypeScript source; "
            "the declaration was renamed or moved — re-point object_expr at the "
            "object literal (not at a wrapper such as .superRefine(...))."
        )
    # Advance to the opening brace of the object literal.
    brace = stripped.find("{", start + len(object_expr) - 1)
    if brace == -1:
        raise TsAnchorNotFoundError(
            f"object anchor {object_expr!r} is not followed by an object literal."
        )
    keys: set[str] = set()
    depth = 0
    i = brace
    n = len(stripped)
    # A key is an identifier at depth 1 immediately followed (after optional
    # whitespace) by ':'.
    key_re = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:")
    while i < n:
        ch = stripped[i]
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                break
            i += 1
            continue
        if depth == 1:
            m = key_re.match(stripped, i)
            if m:
                keys.add(m.group(1))
                i = m.end()
                continue
        i += 1
    return keys
