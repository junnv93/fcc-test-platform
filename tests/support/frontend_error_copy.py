"""Census of ``code``-refined operator copy tables in the frontend error SSOT.

Why this exists (ledger ``[2026-08-21 conformance-gate-premise-shift-audit]`` P2)
---------------------------------------------------------------------------------
``describeApiError`` refines its copy by the RFC 9457 ``code``. That lookup
started as three module-scope maps whose arms each re-spelled the same three
lines, and folding them into one table was the repair. The seal that keeps them
folded asked the wrong question:

    ``\\b(?:const|let|var)\\s+([A-Z0-9_]*KEY_BY_CODE)\\b``

That is a question about **spelling**. An independent adversarial review planted

    ``const CONFLICT_REFINEMENTS = { SOME_CODE: 'errors.someCode' };``

in the 409 arm and took the whole lane green — the sixth copy the seal's own
docstring promised to stop, wearing a different name. A rule that asks for a
name is defeated by a rename, and a rename is the single most natural thing a
reviewer waves through.

So this module asks the **proposition** instead:

    A *code-refinement table* is an object literal that maps at least one
    SCREAMING_SNAKE key to an operator copy key (``'errors.…'``). Every one of
    them in ``ui/errors.ts`` must live inside ONE enclosing literal, and that
    enclosing literal must be keyed by HTTP status.

No identifier is named anywhere in that sentence, so no rename satisfies it.

⚠️ **Unreadable is reported, never exempted.** A literal that maps to operator
copy through a key this parser cannot classify (a computed key) is returned in
its own bucket rather than dropped. *"I cannot read this key"* must not become
*"this is not a copy table"* — that substitution is precisely how the previous
generation of this repository's threshold census was defeated twice.

dependency-free: standard library plus the shared TS lexer in ``support.parity``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import NamedTuple

from support.parity import (
    TsObjectLiteral,
    match_brackets,
    TsUnbalancedRegionError,
    iter_ts_object_literals,
    mask_ts_noncode,
    strip_ts_comments,
)


__all__ = [
    "COPY_KEY_PREFIX",
    "CopyTableCensus",
    "GENERATED_SUBTREE",
    "VOCABULARY_AXIS_LIMITATION",
    "tables_not_directly_under",
    "copy_assignment_sites",
    "CodeCopyTable",
    "tree_code_copy_tables",
    "census_copy_tables",
    "code_token_sites",
    "copy_keys_in_value",
    "modules_with_copy_assignments",
    "refined_copy_keys",
    "canonical_copy_module",
    "modules_naming_codes_beside_copy",
    "scan_tree_for_copy_tables",
    "enclosing_container",
    "error_code_vocabulary",
    "screaming_snake_keys",
    "status_keys",
]


#: The i18n namespace `describeApiError` resolves through. A value outside it is
#: not operator copy, so a table of such values is not this module's business.
COPY_KEY_PREFIX = "errors."

#: A key spelled the way the backend spells an ``ErrorCode``. This is a SHAPE,
#: not a vocabulary: a table keyed by a code the backend never published is
#: still a second copy of the lookup, and requiring vocabulary membership would
#: hand every author an exemption spelled ``MADE_UP_CODE``.
_SCREAMING_SNAKE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

#: A copy key appearing as a string literal in a member's value.
#:
#: ⚠️ Two deliberate loosenings, each closing an evasion that costs one character
#: or one keystroke:
#:
#: * **All three quote spellings**, backtick included. ``t(`errors.x`)`` resolves
#:   exactly like ``t('errors.x')``, so a rule that only knew about ``'`` and
#:   ``"`` could be walked past by changing one character — the same class of
#:   defeat as the identifier-spelling rule this module replaces.
#: * **Searched, not anchored.** ``{ SOME_CODE: cond ? 'errors.a' : 'errors.b' }``
#:   is a code-keyed copy lookup whose value is not itself a literal. Requiring
#:   the value to BE a literal would exempt it.
#:
#: * **The closing quote is not required.** ``{ A_CODE: 'errors.' + suffix }`` is a
#:   code→copy lookup assembled by concatenation; demanding a complete key inside
#:   one literal exempted it (measured). What identifies operator copy is the
#:   namespace, not whether the author finished spelling the key in one token.
#:
#: The value text comes from the ORIGINAL source, so the quotes are still there.
_COPY_KEY_VALUE_RE = re.compile(r"""['"`]errors\.""")


#: Every operator copy key that appears anywhere in a member's value.
#:
#: ⚠️ **One value, one parser.** The locale seal grew a THIRD reading of the same
#: bytes (``value.strip().strip("'\"`")`` then ``startswith``) and it disagreed
#: with the two beside it: ``DRAFT_EMPTY: DEV ? 'errors.draftEmpty' : 'errors.nope'``
#: contributed **zero** keys to the locale check *and* removed the good one from
#: the checked set, so a key resolving in neither locale shipped with the lane
#: green. ``_COPY_KEY_VALUE_RE`` is deliberately *searched* — re-anchoring it in a
#: sibling is how two rules about the same text end up answering differently.
_COPY_KEY_LITERAL_RE = re.compile(r"""(['"`])(errors\.[A-Za-z0-9_.]*)\1""")


def copy_keys_in_value(value: str) -> "tuple[str, ...]":
    """Copy keys spelled as complete literals in ``value`` — all of them.

    A ternary contributes both arms; a concatenation contributes the fragment it
    completed (or nothing, which the assignment axis is there to catch).
    """
    return tuple(match.group(2) for match in _COPY_KEY_LITERAL_RE.finditer(value))


def refined_copy_keys(literal: TsObjectLiteral) -> "tuple[str, ...]":
    """Every copy key the table's members name, in source order."""
    return tuple(
        key
        for entry in literal.entries
        if entry.kind == "entry"
        for key in copy_keys_in_value(entry.value)
    )


class CopyTableCensus(NamedTuple):
    """What ``ui/errors.ts`` contains, split by what this parser could decide.

    ``code_keyed`` — literals that map a SCREAMING_SNAKE key to operator copy.
    ``unreadable`` — literals that map SOMETHING to operator copy through a key
    this parser cannot classify. A consumer must treat a non-empty bucket as a
    defect, not as an empty one.
    """

    code_keyed: tuple[TsObjectLiteral, ...]
    unreadable: tuple[TsObjectLiteral, ...]


def screaming_snake_keys(literal: TsObjectLiteral) -> tuple[str, ...]:
    """The literal's top-level keys spelled like an ``ErrorCode``."""
    return tuple(
        entry.key
        for entry in literal.entries
        if entry.kind == "entry" and entry.key is not None and _SCREAMING_SNAKE_RE.match(entry.key)
    )


def _maps_to_operator_copy(literal: TsObjectLiteral) -> bool:
    return any(
        entry.kind == "entry" and _COPY_KEY_VALUE_RE.search(entry.value) is not None
        for entry in literal.entries
    )


def _has_unclassifiable_key(literal: TsObjectLiteral) -> bool:
    """Does the literal key something by an expression this parser cannot decide?

    ⚠️ This exempted anything satisfying ``key.isidentifier()``, and an independent
    adversarial review walked straight through the hole: ``{ [conflictCode]: 'errors.…' }``
    landed in **neither** bucket — not ``code_keyed`` (``conflictCode`` is not
    SCREAMING_SNAKE) and not ``unreadable`` (it *is* an identifier). The module's own
    ⚠️ paragraph promised the opposite. The two seals around it straddled the hole:
    ``[codes.x]`` (dotted) and ``[SOME_CODE]`` (screaming) were both covered, and the
    plain lower-case identifier between them was not.

    A computed key is decidable here only when it reads as a code constant. A computed
    key spelled as a *variable* holds whatever that variable holds — which is exactly
    the case where "I cannot read this" must not become "this is not a code table".
    """
    return any(
        entry.kind == "entry"
        and entry.key is not None
        and entry.key_kind == "computed"
        and not _SCREAMING_SNAKE_RE.match(entry.key)
        for entry in literal.entries
    )


def census_copy_tables(source: str) -> CopyTableCensus:
    """Split every object literal in ``source`` that maps to operator copy."""
    code_keyed: list[TsObjectLiteral] = []
    unreadable: list[TsObjectLiteral] = []
    for literal in iter_ts_object_literals(source):
        if not _maps_to_operator_copy(literal):
            continue
        if _has_unclassifiable_key(literal):
            unreadable.append(literal)
        elif screaming_snake_keys(literal):
            code_keyed.append(literal)
    return CopyTableCensus(tuple(code_keyed), tuple(unreadable))


#: An identifier or quoted token that could name a published ``ErrorCode``.
#: Scanned over the MASKED source with string content restored from the original,
#: because a code can be named as a bare identifier (``case DRAFT_EMPTY``) or as a
#: string (``case 'DRAFT_EMPTY'``) and both are the same fact.
_CODE_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]*")

#: ⚠️ The vocabulary is not private to error handling.
#:
#: ``CONFLICT``, ``NOT_FOUND``, ``FORBIDDEN``, ``VALIDATION_ERROR``,
#: ``INTERNAL_ERROR`` and ``RATE_LIMITED`` are published codes AND ordinary
#: uppercase words, so ``export type Verdict = 'CONFLICT' | 'RESOLVED'`` reads as
#: a site. The axis asks about TOKENS, not about meaning, and it cannot tell the
#: two apart — which is also the only reason it can see a code named inside a
#: ``switch`` or a ``Map``.
#:
#: The blast radius is bounded by construction: a site only matters in a module
#: that also emits an ``'errors.…'`` key (:func:`modules_naming_codes_beside_copy`),
#: so an unrelated union type elsewhere is never reported. Measured today: zero
#: collisions. The day one appears, the fix is to move the colliding declaration
#: out of the copy module, not to weaken the axis — narrowing it to "codes that
#: look like codes" is how the previous generation of this rule was defeated.
VOCABULARY_AXIS_LIMITATION = (
    "code_token_sites matches uppercase TOKENS, and some published ErrorCodes are "
    "ordinary words (CONFLICT, NOT_FOUND, FORBIDDEN). A false positive is only "
    "reachable inside a module that also emits operator copy; zero collisions today."
)


def code_token_sites(source: str, vocabulary: "frozenset[str]") -> "tuple[tuple[str, int], ...]":
    """``(code, offset)`` for every occurrence of a PUBLISHED error code in ``source``.

    ⚠️ **This axis exists because the object-literal axis is a question about
    notation.** An independent adversarial review put three second code→copy
    lookups on the real ``ui/errors.ts`` with the whole lane green — a
    ``new Map([['WORKBOOK_HANDLE_NOT_FOUND', 'errors.workbookHandleNotFound']])``,
    a ``switch (code) { case 'DRAFT_EMPTY': return 'errors.draftEmpty'; }``, and an
    if-chain. None of them is an object literal, so none of them was ever asked
    about. Enumerating constructs would be the same mistake one level up: the next
    reviewer writes the fourth construct.

    The total question does not mention constructs at all. **To refine copy by a
    code you must NAME that code**, and the set of nameable codes is published by
    the backend. So: every occurrence of a published code, wherever and however it
    is spelled, must fall inside the one sanctioned container.

    Comments are excluded (the mask blanks them) — a rule whose violation is a
    sentence explaining the rule gets its explanation deleted.

    ⚠️ See :data:`VOCABULARY_AXIS_LIMITATION` — the vocabulary contains ordinary
    English words, and this asks about tokens rather than about their meaning.
    """
    masked = mask_ts_noncode(source)
    sites: list[tuple[str, int]] = []
    for match in _CODE_TOKEN_RE.finditer(masked):
        token = match.group(0)
        if token in vocabulary:
            sites.append((token, match.start()))
    # String CONTENT is blanked in the mask, so a code named as `'DRAFT_EMPTY'`
    # has to be recovered from the original at the string's own offsets.
    for quote in ("'", '"', "`"):
        start = 0
        while True:
            opened = masked.find(quote, start)
            if opened < 0:
                break
            closed = masked.find(quote, opened + 1)
            if closed < 0:
                break
            body = source[opened + 1 : closed]
            if body in vocabulary:
                sites.append((body, opened + 1))
            start = closed + 1
    return tuple(sorted(set(sites), key=lambda site: site[1]))


def enclosing_container(
    source: str, tables: "tuple[TsObjectLiteral, ...]"
) -> TsObjectLiteral | None:
    """The smallest literal in ``source`` that strictly contains every table.

    ``None`` means the tables are SIBLINGS — there is more than one lookup, which
    is the whole defect. Note what this does NOT do: it never looks for a name.
    Two tables at module scope have no common enclosing literal no matter what
    either of them is called, and one table nested under an HTTP status has one
    no matter what the container is called.
    """
    if not tables:
        return None
    lo = min(table.start for table in tables)
    hi = max(table.end for table in tables)
    candidates = [
        literal
        for literal in iter_ts_object_literals(source)
        if literal.start <= lo
        and literal.end >= hi
        and not any(literal.start == t.start and literal.end == t.end for t in tables)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda literal: literal.end - literal.start)


def tables_not_directly_under(
    source: str, container: TsObjectLiteral, tables: "tuple[TsObjectLiteral, ...]"
) -> "tuple[TsObjectLiteral, ...]":
    """Tables whose IMMEDIATE enclosing literal is not ``container``.

    ⚠️ *"Inside the container"* is not the proposition — *"a direct value of a
    status key"* is. An independent adversarial review put a second table one
    nesting level lower, inside an existing arm:

        ``422: { legacy: { CLAIM_CONFLICT: 'errors.…' }, DRAFT_EMPTY: … }``

    The container is unchanged, its keys are still all digits, and the arm↔table
    correspondence still holds — the whole lane stayed green. It is the same shape
    as the round-1 ``'418': {`` escape, one level down: a permanently unreachable
    copy arm, because ``CODE_REFINED_KEY_BY_STATUS[422][code]`` yields the nested
    OBJECT, never a key. Depth is the fix, and it has to be exact.
    """
    # ⚠️ Parents are counted over ALL bracketed literals, not only ``{}``. Counting
    # braces alone let an interposed ARRAY sit between a table and the container,
    # so the table was two literals down while the immediate-object-parent test
    # still saw the container.
    masked = mask_ts_noncode(source)
    pairs = match_brackets(masked)
    literals = [
        TsObjectLiteral(open_at, close_at, ())
        for open_at, close_at in sorted(pairs.items())
    ]
    stray: list[TsObjectLiteral] = []
    for table in tables:
        parents = [
            literal
            for literal in literals
            if literal.start < table.start
            and literal.end >= table.end
            and not (literal.start == table.start and literal.end == table.end)
        ]
        if not parents:
            stray.append(table)
            continue
        immediate = min(parents, key=lambda literal: literal.end - literal.start)
        if (immediate.start, immediate.end) != (container.start, container.end):
            stray.append(table)
    return tuple(stray)


#: ``X[…] = 'errors.…'`` — a copy lookup **written by assignment** rather than
#: declared as a literal.
#:
#: ⚠️ This closes an evasion the vocabulary axis structurally cannot see:
#: ``S['DRAFT' + '_EMPTY'] = 'errors.draftEmpty'`` names a published code without
#: ever spelling it as one token. Keying on the ASSIGNMENT shape rather than on
#: the key expression makes how the key is spelled irrelevant — which is the same
#: move that got this module out of asking about identifiers in the first place.
#: ⚠️ ``=`` alone was one character short. ``EXTRA['DRAFT' + '_EMPTY'] ??= '…'``
#: assigns exactly the same lookup and did not match — an independent review
#: reopened the whole axis with that one extra character. Compound assignment is
#: assignment.
_COPY_ASSIGNMENT_RE = re.compile(
    r"""[A-Za-z_$][\w$]*\s*\[[^\]\n]*\]\s*(?:\?\?|\|\||&&|\+)?=\s*(?!=)"""
)


def copy_assignment_sites(source: str) -> "tuple[int, ...]":
    """Offsets of computed-index assignments whose value is an operator copy key."""
    masked = mask_ts_noncode(source)
    sites: list[int] = []
    for match in _COPY_ASSIGNMENT_RE.finditer(masked):
        # The value is a string literal, so its content lives in the ORIGINAL at
        # the same offsets — the property `mask_ts_noncode` exists to give.
        tail = source[match.end() : match.end() + 12]
        if _COPY_KEY_VALUE_RE.match(tail.lstrip()) is not None:
            sites.append(match.start())
    return tuple(sites)


def status_keys(literal: TsObjectLiteral) -> tuple[str, ...]:
    """The literal's top-level keys, as written. Used to assert that the one
    surviving container is keyed by HTTP status — an author who nests a second
    lookup under a non-numeric key has produced a second lookup again."""
    return tuple(
        entry.key for entry in literal.entries if entry.kind == "entry" and entry.key is not None
    )


#: Codegen output. It DECLARES the `ErrorCode` unions, so every published code
#: appears there by construction — scanning it would report the contract itself
#: as a second lookup. It is also gitignored, so it is not source under audit.
GENERATED_SUBTREE = "api/generated"


def scan_tree_for_copy_tables(
    src_root: Path, *, exclude: str = GENERATED_SUBTREE
) -> "dict[str, CopyTableCensus]":
    """``module -> census`` for every frontend source that maps a code to copy.

    ⚠️ **The single-file scope was itself the defect.** Both propositions above
    read ``ui/errors.ts`` and nothing else, so an independent adversarial review
    put the second lookup in a NEW module (``ui/error-refinements.ts``), wired it
    into the 409 arm, and took the whole lane green — the identical manoeuvre this
    repository's route seals were defeated by, one axis over. A rule about "the"
    lookup has to be able to see a lookup that moved.

    Nothing here names a file. The canonical module is DERIVED as the unique one
    holding a table (:func:`canonical_copy_module`); two of them is the defect,
    and which two is the diagnosis.
    """
    censuses: dict[str, CopyTableCensus] = {}
    for path in sorted(src_root.rglob("*.ts*")):
        if not path.is_file():
            continue
        relative = path.relative_to(src_root).as_posix()
        if relative.startswith(exclude):
            continue
        census = census_copy_tables(path.read_text(encoding="utf-8"))
        if census.code_keyed or census.unreadable:
            censuses[relative] = census
    return censuses


def modules_naming_codes_beside_copy(
    src_root: Path, vocabulary: "frozenset[str]", *, exclude: str = GENERATED_SUBTREE
) -> "dict[str, tuple[str, ...]]":
    """``module -> published codes it names``, for modules that also emit copy keys.

    The vocabulary axis, widened from one file to the tree. A module that merely
    COMPARES a code (``err.code === 'DRAFT_EMPTY'`` in a route, to decide whether
    to offer a retry) is not a copy lookup and is not reported — the offence is
    naming a code **in the same module that produces operator copy**, which is what
    a second refinement table is.
    """
    found: dict[str, tuple[str, ...]] = {}
    for path in sorted(src_root.rglob("*.ts*")):
        if not path.is_file():
            continue
        relative = path.relative_to(src_root).as_posix()
        if relative.startswith(exclude):
            continue
        source = path.read_text(encoding="utf-8")
        # Comments stripped, string CONTENT kept — the one rendering that answers
        # "does this module emit an operator copy key" without reading a sentence
        # that merely mentions one.
        if _COPY_KEY_VALUE_RE.search(strip_ts_comments(source)) is None:
            continue
        codes = tuple(sorted({code for code, _ in code_token_sites(source, vocabulary)}))
        if codes:
            found[relative] = codes
    return found


def error_code_vocabulary(openapi_dir: Path) -> frozenset[str]:
    """Union of the published ``ErrorCode`` enums across the API surfaces.

    ⚠️ The vocabulary is derived from the CONTRACT ARTIFACTS, not from the file
    under audit and not from a list in a test. A derivation is only as good as
    its source: a set the audited author controls is a set the audited author
    can edit, and this repository has been defeated that way before. These files
    are regenerated from the backend ``ErrorCode`` enum, so an entry naming a
    code the backend retired shows up here as dead copy.
    """
    codes: set[str] = set()
    surfaces = sorted(openapi_dir.glob("*.openapi.json"))
    if not surfaces:
        raise FileNotFoundError(
            f"no OpenAPI artifacts under {openapi_dir} — the ErrorCode vocabulary "
            "would be empty and every membership assertion vacuously true"
        )
    for surface in surfaces:
        schema = json.loads(surface.read_text(encoding="utf-8"))
        enum = schema.get("components", {}).get("schemas", {}).get("ErrorCode", {}).get("enum")
        if enum:
            codes.update(enum)
    return frozenset(codes)


def canonical_copy_module(censuses: "dict[str, CopyTableCensus]") -> str | None:
    """The one module holding code-refinement tables, or ``None`` when it is not one.

    ⚠️ **Derived, never named.** Hard-coding ``ui/errors.ts`` would put the rule's
    subject in the hands of whoever moves the file, and this whole wave exists
    because a rule that names things is satisfied by renaming them. ``None`` means
    zero (the seal has nothing to check — vacuous) or more than one (the defect);
    the caller distinguishes those and reports which modules.
    """
    return next(iter(censuses)) if len(censuses) == 1 else None


def modules_with_copy_assignments(
    src_root: Path, *, exclude: str = GENERATED_SUBTREE
) -> "dict[str, tuple[int, ...]]":
    """``module -> offsets`` for every frontend source writing copy by assignment.

    ⚠️ **Tree-wide, like its two siblings.** This axis was the only single-file one
    — it read ``ui/errors.ts`` and nothing else — while the structural and
    vocabulary axes had already been widened for exactly this reason. A review
    composed the three gaps into one counterexample: a NEW module, keys assembled
    by concatenation, written with ``??=``, wired live into the 409 arm, lane green.
    A rule about "the" lookup has to see a lookup that moved.
    """
    found: dict[str, tuple[int, ...]] = {}
    for path in sorted(src_root.rglob("*.ts*")):
        if not path.is_file():
            continue
        relative = path.relative_to(src_root).as_posix()
        if relative.startswith(exclude):
            continue
        sites = copy_assignment_sites(path.read_text(encoding="utf-8"))
        if sites:
            found[relative] = sites
    return found


# --------------------------------------------------------------------------- #
# The tree-wide axis, keyed on the CONTRACT rather than on a namespace name.
# --------------------------------------------------------------------------- #
#: An i18n message key as it appears in source: a dotted literal.
_I18N_KEY_RE = re.compile(r"""(['"`])([a-zA-Z][\w]*(?:\.[\w]+)+)\1""")


#: 스캔 대상 확장자. ⚠️ ``rglob("*.[tj]s*")`` 는 ``.mts``/``.cts``/``.mjs``/``.cjs`` 에
#: 눈이 멀었다 — 확장자 하나로 축 밖에 나가는 것은 이 웨이브가 없애는 형태다.
#: 값이 **읽히지만 문구가 아닌** 경우 — 점 없는 문자열 리터럴, 숫자, 불리언.
#: ⚠️ *"읽었고 문구가 아니다"* 와 *"읽을 수 없다"* 는 같은 답을 가져서는 안 된다.
_PLAIN_LITERAL_RE = re.compile(r"""^(?:(['"`])[^'"`$]*\1|\d+|true|false|null|undefined)$""")

_SCANNED_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"})


class CodeCopyTable(NamedTuple):
    """A table that maps published error codes to operator copy, wherever it lives.

    ``codes`` is **every** SCREAMING_SNAKE key, published or not — the caller asks
    the membership question, because a collector that pre-filters by membership
    makes that question unanswerable. ``unreadable`` names the members whose value
    this parser could not resolve to a copy key.
    """

    module: str
    codes: tuple[str, ...]
    copy_keys: tuple[str, ...]
    unreadable: tuple[str, ...]
    start: int
    end: int


def tree_code_copy_tables(
    src_root: Path,
    vocabulary: "frozenset[str]",
    *,
    exclude: str = GENERATED_SUBTREE,
) -> "tuple[CodeCopyTable, ...]":
    """Every object literal in the frontend that maps a PUBLISHED code to copy.

    ⚠️ **The namespace was a name.** Both tree-wide axes recognised operator copy by
    the ``'errors.'`` prefix, so a code→copy table under a route's own i18n
    namespace was invisible to all of them — the identical defeat this wave exists
    to remove, one level down. An independent review planted
    ``CREATE_HINT_BY_CODE`` in a route with three published codes and both censuses
    still answered ``['ui/errors.ts']``; the whole exemption was
    ``grep -c "'errors\\." → 0``.

    So the predicate names nothing: a key that the **backend published** mapped to a
    key that **resolves in the locale files**. Both sides come from artifacts this
    module does not own.

    The locale side is asked by the CALLER, not filtered here — see the note on
    ``copy_keys`` below.

    ⚠️ **And that measurement corrected the wave's own claim.** *"Exactly one
    code→copy lookup in the whole frontend"* was **false on the real tree**:
    ``routes/reports.tsx`` has carried ``REPORT_MESSAGE_KEY_BY_CODE`` since
    2026-07-28, and its own comment argues the case — a code-first rung that then
    delegates to ``describeApiError`` is *"one ladder with a sharper first rung,
    not a second ladder"*. The namespace restriction was hiding it rather than
    permitting it. What every such table must satisfy is health (published codes,
    resolvable copy); what only the TAXONOMY's tables must satisfy is the fold.
    """
    found: list[CodeCopyTable] = []
    for path in sorted(src_root.rglob("*")):
        if not path.is_file() or path.suffix not in _SCANNED_SUFFIXES:
            continue
        relative = path.relative_to(src_root).as_posix()
        if relative.startswith(exclude):
            continue
        source = path.read_text(encoding="utf-8")
        for literal in iter_ts_object_literals(source):
            entries = [e for e in literal.entries if e.kind == "entry" and e.key is not None]
            # ⚠️ **Membership is decided by the KEY, never by the value.**
            #
            # The first version required a published code AND an inline dotted
            # literal value, so three ordinary refactors — a constant reference, a
            # template substitution, a concatenation — made the whole table VANISH
            # from the census instead of failing it. Measured by an independent
            # review: the textbook "extract the copy keys to a constants file"
            # refactor shipped a published `CLAIM_CONFLICT` mapped to a key
            # resolving in NEITHER locale, and the census silently went from six
            # tables in two modules to five in one while `assertGreater(len, 0)`
            # stayed happy. A collector that drops what its checker looks for is a
            # vacuous axis — this wave has now shipped that shape twice.
            if not any(entry.key in vocabulary for entry in entries):
                continue
            keyed = [entry for entry in entries if _SCREAMING_SNAKE_RE.match(entry.key)]
            codes = tuple(entry.key for entry in keyed)
            copy_keys: list[str] = []
            unreadable: list[str] = []
            for entry in keyed:
                names = [match.group(2) for match in _I18N_KEY_RE.finditer(entry.value)]
                if names:
                    copy_keys.extend(names)
                elif _PLAIN_LITERAL_RE.match(entry.value.strip().rstrip(",")):
                    # A readable value that is simply NOT copy — an internal reason
                    # token (`'forbidden'`), a number, a boolean. Decidable, so it is
                    # neither a copy key nor a complaint. ⚠️ Keeping this distinct
                    # from "unreadable" is the whole point: *"I read it and it is not
                    # copy"* and *"I could not read it"* must not share an answer.
                    continue
                else:
                    unreadable.append(f"{entry.key}: {entry.value.strip()[:40]}")
            found.append(
                CodeCopyTable(
                    relative,
                    codes,
                    tuple(copy_keys),
                    tuple(unreadable),
                    literal.start,
                    literal.end,
                )
            )
    return tuple(found)
