"""The registered route set of ``apps/web``, derived from the MODULE GRAPH.

Why this exists (ledger ``[2026-08-21 conformance-gate-premise-shift-audit]`` P2)
---------------------------------------------------------------------------------
Three separate seals derived "which routes are registered" by running

    ``re.findall(r"path:\\s*'([^']+)'", app_tsx_source)``

over one file. That answers *"which path literals are written in app.tsx"*,
which is a different question, and the gap between the two is where an
independent adversarial review walked in: it added ``shared/extra-routes.tsx``,
spread it in with ``...extraRoutes``, and every one of the three seals stayed
green because the new routes were missing from **both sides** of their equality.
``app.tsx`` even carries a comment naming that exact risk (the ``...gridPocRoutes``
precedent), and the counting seal's own docstring called the array "the route
registration SSOT" while reading only its file.

⚠️ ``tsc`` covers half of it and cannot cover the other half. ``errorElement``
and ``handle`` are REQUIRED by ``AppRouteCommon``, so a route object missing
either fails type-check wherever it is declared. But whether a ``titleKey``
**resolves in ko.json and en.json** is not a type question at all, and an
unresolved one ships a raw dotted key into the screen-name slot. That is the
defect this derivation exists to see.

The three rules that make this a derivation rather than a better regex
----------------------------------------------------------------------
1. **Structure, not text.** Route objects are read as object literals through
   the shared TS lexer, so a behaviour-preserving reshuffle cannot change the
   answer. (The previous seal lost 23 routes down to 1 when a helper was hoisted
   above the array — a pure relocation.)
2. **The graph, not the file.** ``...IDENT`` is resolved to its declaration,
   following an ``import`` into another module when that is where it lives.
3. **Unresolvable is loud.** Anything this module cannot follow raises
   :class:`UnresolvedRouteElementError`. A derivation that claims completeness
   may not count what it could not read as nothing — that substitution is how
   the seal it replaces was defeated, and a silent widening fallback reads to
   this consumer as a pass.

dependency-free: standard library plus the shared TS lexer in ``support.parity``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from support.parity import (
    TsEntry,
    strip_ts_comments,
    TsUnbalancedRegionError,
    mask_ts_noncode,
    match_brackets,
    parse_ts_literal_members,
    split_top_level,
)


__all__ = [
    "ROUTE_ENTRY_MODULE",
    "ROUTE_ARRAY_NAME",
    "RouteEntry",
    "UnresolvedRouteElementError",
    "collect_route_entries",
    "registered_addresses",
    "title_key_by_address",
]


#: Where the walk starts. Both are arguments with these as defaults so a
#: synthetic tree can be fed to the same function — the non-vacuity discipline
#: this repository already uses for `_document_title_writers(root)`.
ROUTE_ENTRY_MODULE = "app.tsx"
ROUTE_ARRAY_NAME = "appRoutes"

#: Module specifier prefixes understood by the resolver. ``@/`` is the Vite
#: alias for ``apps/web/src`` (tsconfig ``paths``); the relative forms are
#: resolved against the importing module's directory.
_ALIAS_PREFIX = "@/"

#: Extension/·index candidates tried for a specifier, in resolution order.
_MODULE_CANDIDATES = (".tsx", ".ts", "/index.tsx", "/index.ts")

#: How far object spreads may nest before the walk refuses. A bound, not a
#: judgement: a cycle would otherwise recurse forever, and refusing loudly is the
#: contract everywhere else in this module.
_MAX_SPREAD_DEPTH = 8

#: ``<`` that opens a type-argument list (an identifier or `{`/`[` follows). JSX and
#: comparisons are excluded — this only has to be right often enough to stop a comma
#: inside `Foo<A, B>` from reading as a declarator list.
_TYPE_ARGUMENT_OPEN_RE = re.compile(r"<\s*[A-Za-z_$\[{]")

#: A string literal in any of the three JS spellings. ⚠️ The backtick arm is not
#: decoration: the sibling axis in ``frontend_error_copy`` deliberately accepts
#: backticks (``t(`errors.x`)`` resolves identically), and a walk that RAISED on
#: ``titleKey: `routes.x.title` `` would make the two halves of this wave disagree
#: about the same language. An interpolated template (``${``) is excluded — that is
#: not a literal and the key it produces is not knowable here.
_STRING_LITERAL_RE = re.compile(r"""^(['"])(?P<text>[^'"]*)\1$|^`(?P<tpl>[^`$]*)`$""", re.S)
_TRUE_RE = re.compile(r"^true$")

#: ``const|let|var NAME`` — the declaration of a route array in some module.
#: ``export`` is optional because a spread can also name a module-local binding.
def _declaration_re(name: str) -> "re.Pattern[str]":
    """``const NAME`` — and also ``, NAME`` inside a multi-declarator statement.

    ⚠️ ``export const appVersion = 1, appRoutes = [...]`` is legal TypeScript and
    the first version of this refused it outright, naming a reason that was not
    true (*"it moved, it is nested inside a function, or it is not module-local"*).
    A gate that refuses ordinary code with a wrong diagnosis is a gate somebody
    switches off — this repository has reached that conclusion three times.

    ⚠️ **And the comma form must be a real declarator.** ``, NAME`` alone also
    occurs inside type arguments, parameter lists and export clauses — an
    independent review measured **8 phantom declarations** on the real tree with
    that spelling, one of which resolved the WRONG binding and lost imported
    routes. A declarator in a comma list has an initializer, so requiring ``=``
    is the shape, not a patch.
    """
    escaped = re.escape(name)
    return re.compile(
        rf"\b(?:const|let|var)\s+{escaped}\b|,\s*{escaped}\s*(?::[^=;]*)?=(?!=)"
    )


#: ``import { A, B as C } from 'spec'`` / ``import D from 'spec'``.
_IMPORT_RE = re.compile(
    r"import\s+(?P<clause>[^;]*?)\s+from\s+['\"](?P<spec>[^'\"]+)['\"]",
    re.S,
)
_NAMED_BINDING_RE = re.compile(
    r"(?P<source>[A-Za-z_$][A-Za-z0-9_$]*)(?:\s+as\s+(?P<local>[A-Za-z_$][A-Za-z0-9_$]*))?"
)

#: ``export { A, B as C } from 'spec'`` — a barrel forwarding someone else's binding.
_RE_EXPORT_RE = re.compile(
    r"export\s*\{(?P<names>[^}]*)\}\s*from\s*['\"](?P<spec>[^'\"]+)['\"]",
    re.S,
)


class _OwnedMember(NamedTuple):
    """A member together with the module and source its offsets belong to.

    ⚠️ Provenance is not bookkeeping here. A spread merges ANOTHER module's members
    into a route object, and their offsets index that module's text. Keeping only
    the ``TsEntry`` meant ``children`` from a shared preset was sliced out of the
    local source with foreign offsets — an unhandled ``IndexError`` in the lucky
    case, and reading a different file's bytes as this route's children in the
    unlucky one.
    """

    module: str
    source: str
    entry: TsEntry


class UnresolvedRouteElementError(LookupError):
    """A route array member this module could not follow to a declaration.

    Raised — never swallowed — because the caller's assertion is a COMPLETENESS
    claim. "I could not read this" and "there was nothing here" must not produce
    the same answer.
    """


class RouteEntry(NamedTuple):
    """One registered route object, wherever in the graph it was declared."""

    #: ``src``-relative posix path of the module the object literal lives in.
    module: str
    #: The route's own ``path:`` literal, or ``None`` for an ``index: true`` route.
    path: str | None
    index: bool
    has_error_element: bool
    title_key: str | None
    #: Ancestor ``path`` literals, outermost first — the address is derived from
    #: these plus ``path``, so nesting is never re-spelled by a consumer.
    parents: tuple[str, ...]
    #: The route's ``element:`` expression verbatim. Kept because two seals ask
    #: about it (which lazy module renders this screen, is this top-level element
    #: wrapped in the announcer) and re-cutting the same text a third time is how
    #: this derivation came to have five copies in the first place.
    element: str

    @property
    def top_level(self) -> bool:
        """A sibling of the shell rather than one of its children."""
        return not self.parents

    @property
    def address(self) -> str:
        """The URL this route answers at, react-router nesting applied.

        ⚠️ **An address is not an identity.** Two ``index: true`` routes under
        different parents both answer at their parent's address, so a set built
        from addresses silently merges them — and a diagnostic that names the
        address blames the PARENT for the child's missing boundary. Consumers that
        need to tell routes apart use :attr:`identity`; today the tree has one
        nested index route, so this is a live shape with a latent cost.
        """
        segments = [segment for segment in (*self.parents, self.path or "") if segment]
        joined = "/".join(segment.strip("/") for segment in segments if segment.strip("/"))
        return f"/{joined}" if joined else "/"

    @property
    def identity(self) -> str:
        """A name unique per registered route object, index routes included."""
        return f"{self.address} (index)" if self.index else self.address


def collect_route_entries(
    src_root: Path,
    *,
    entry_module: str = ROUTE_ENTRY_MODULE,
    array_name: str = ROUTE_ARRAY_NAME,
) -> tuple[RouteEntry, ...]:
    """Every route object reachable from ``array_name`` in ``entry_module``.

    ``src_root`` is an argument, not a module constant, so the same function can
    be pointed at a synthetic tree. A detector that only ever runs against the
    real tree cannot tell "nothing is wrong" from "I match nothing".
    """
    entries: list[RouteEntry] = []
    _collect_declaration(src_root, entry_module, array_name, (), entries, set())
    return tuple(entries)


def registered_addresses(entries: "tuple[RouteEntry, ...]") -> set[str]:
    """The set of URLs the registered routes answer at."""
    return {entry.address for entry in entries}


def title_key_by_address(entries: "tuple[RouteEntry, ...]") -> dict[str, str]:
    """``address -> titleKey`` for every route that declares one."""
    return {
        entry.address: entry.title_key for entry in entries if entry.title_key is not None
    }


# --------------------------------------------------------------------------- #
# Walk
# --------------------------------------------------------------------------- #
def _read_module(src_root: Path, module: str) -> str:
    path = src_root / module
    if not path.is_file():
        raise UnresolvedRouteElementError(f"module {module!r} not found under {src_root}")
    return path.read_text(encoding="utf-8")


def _collect_declaration(
    src_root: Path,
    module: str,
    name: str,
    parents: "tuple[str, ...]",
    out: "list[RouteEntry]",
    seen: "set[tuple[str, str, tuple[str, ...]]]",
) -> None:
    """Collect from the route array bound to ``name`` in ``module``."""
    # ⚠️ The key carries the PARENT CHAIN. Keyed on ``(module, name)`` alone this
    # was not a cycle guard but a silent drop: spreading one shared array into two
    # different parents is an ordinary react-router pattern, and the second parent's
    # copy — boundary-less or not — vanished with no exception (measured). A real
    # cycle repeats the same (module, name, parents) triple and still terminates.
    fingerprint = (module, name, parents)
    if fingerprint in seen:
        return
    seen.add(fingerprint)
    source = _read_module(src_root, module)
    if not _module_scope_declarations(mask_ts_noncode(source), name):
        forwarded = _re_exported_from(source, name)
        if forwarded is not None:
            specifier, exported_name = forwarded
            _collect_declaration(
                src_root, _resolve_module(src_root, module, specifier),
                exported_name, parents, out, seen,
            )
            return
        imported = _imported_from(source, name)
        if imported is not None:
            specifier, exported_name = imported
            _collect_declaration(
                src_root, _resolve_module(src_root, module, specifier),
                exported_name, parents, out, seen,
            )
            return
    _assert_array_is_not_mutated_after_declaration(module, source, name)
    begin, end = _initializer_span(source, name, module)
    arrays = _outermost_arrays(source, begin, end)
    if not arrays:
        raise UnresolvedRouteElementError(
            f"{module}: `{name}` is not initialised from an array literal — "
            f"{source[begin:end].strip()[:80]!r}"
        )
    for array_start, array_end in arrays:
        _assert_array_value_is_not_transformed(module, source, array_end, end)
        _collect_array(src_root, module, source, array_start, array_end, parents, out, seen)


#: Array methods that change WHICH routes an already-declared array holds. Read-only
#: members (`map`, `filter` used to build something else, `length`) are not here —
#: the question is whether the binding this walk read still names the same set.
_ARRAY_MUTATORS = ("push", "unshift", "splice", "pop", "shift", "fill", "copyWithin")


def _module_scope_spans(masked: str) -> "list[tuple[int, int]]":
    """The stretches of ``masked`` at bracket depth 0 — module-scope statements.

    ⚠️ Declaration resolution has been depth-filtered since round 1; the mutation
    guard was a plain ``re.search`` over the whole module, and an independent
    review used exactly that asymmetry: an ordinary helper with its OWN
    ``const appRoutes: AppRoute[] = []; appRoutes.push(…)`` — a function-local
    binding that cannot touch the module one — raised, naming the route array.
    Two questions about the same binding must use the same notion of scope.

    ⚠️ **Only braces nest here, not parentheses.** Counting ``(`` too excluded the
    interior of every call — which is exactly where an argument lives — so the
    escape check below could never see ``registerExtraRoutes(appRoutes)``. Braces
    are what create a body; parentheses are what a module-scope statement is made
    of. (An object/array literal's braces do nest, and that is harmless: nobody
    mutates the array from inside its own initializer.)
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    for index, char in enumerate(masked):
        if char == "{":
            if depth == 0:
                spans.append((start, index))
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                start = index
    spans.append((start, len(masked)))
    return spans


def _search_module_scope(masked: str, pattern: "re.Pattern[str]") -> "re.Match[str] | None":
    for begin, stop in _module_scope_spans(masked):
        found = pattern.search(masked, begin, stop)
        if found is not None:
            return found
    return None


def _binding_aliases(masked: str, name: str) -> "set[str]":
    """Names that hold the same array — ``const r = appRoutes;`` and friends.

    Only a declaration whose initializer IS the binding (optionally parenthesised
    or type-asserted) creates an alias. ``const first = appRoutes[0]`` does not:
    it holds a route, not the array.
    """
    aliases = {name}
    pattern = re.compile(
        rf"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]*)?=\s*\(?\s*"
        rf"{re.escape(name)}\s*(?:as\s+[^;)]*)?\)?\s*;"
    )
    for begin, stop in _module_scope_spans(masked):
        for match in pattern.finditer(masked, begin, stop):
            aliases.add(match.group(1))
    return aliases


def _assert_array_is_not_mutated_after_declaration(module: str, source: str, name: str) -> None:
    """Raise when a later statement adds to or removes from the route array.

    ⚠️ ``_assert_array_value_is_not_transformed`` is bounded by the initializer
    expression, so ``appRoutes.push({...})`` on the next line was **invisible**
    (measured: 23 routes, no exception). The initializer is not the whole story
    about what a binding holds, and a completeness claim has to say so.

    ⚠️ **And the first repair only looked at the bare name.** An independent
    adversarial review put a boundary-less route with an unresolvable ``titleKey``
    onto the REAL ``app.tsx`` three further ways, all green:
    ``(appRoutes as AppRoute[]).push(…)`` · ``const r = appRoutes; r.push(…)`` ·
    ``appRoutes[appRoutes.length] = …``. So the receiver is resolved, not matched.

    ⚠️ The same review found the opposite failure in the same guard:
    ``registry.appRoutes.push('x')`` — a member of a **completely unrelated
    object** — raised. A gate that refuses code it has no business judging is a
    gate somebody switches off, so the receiver must not be preceded by a dot.
    """
    masked = mask_ts_noncode(source)
    for alias in sorted(_binding_aliases(masked, name)):
        quoted = re.escape(alias)
        # `(?<![.\w$])` is the whole false-positive fix: `registry.appRoutes` is a
        # different binding that merely ends with the same word.
        for mutator in _ARRAY_MUTATORS:
            # ⚠️ Four spellings of the same call, all measured on the real tree:
            # `x.push(`, `(x as T).push(`, `x.push?.(` (optional call) and
            # `x['push'](` (computed member). Matching only the first is matching a
            # spelling, which is the defect class this whole wave exists to remove.
            spellings = (
                rf"(?<![.\w$]){quoted}\s*(?:as\s+[^;)]*)?\)?\s*\.\s*{mutator}\s*(?:\?\.)?\s*\(",
                rf"\(\s*{quoted}\s+as\s+[^)]*\)\s*\.\s*{mutator}\s*(?:\?\.)?\s*\(",
            )
            call = next(
                (
                    found
                    for found in (
                        _search_module_scope(masked, re.compile(pattern))
                        for pattern in spellings
                    )
                    if found is not None
                ),
                None,
            )
            if call is not None:
                raise UnresolvedRouteElementError(
                    f"{module}: `{alias}.{mutator}(` changes the route array after its "
                    "declaration — this walk reads the initializer, so whatever that call "
                    "adds or removes is invisible to every assertion built on it"
                )
        index_write = _search_module_scope(
            masked, re.compile(rf"(?<![.\w$]){quoted}\s*\[[^\]\n]*\]\s*=(?!=)")
        ) or _search_module_scope(
            # `x.length = 0` empties the array without any mutator name.
            masked, re.compile(rf"(?<![.\w$]){quoted}\s*\.\s*length\s*=(?!=)")
        )
        # A computed member call — `x['push'](…)`. ⚠️ The KEY lives in a string, and
        # the mask blanks string content, so it has to be read back from the
        # ORIGINAL at the same offsets. Matching the masked text here would look
        # right and match nothing (measured).
        computed = _search_module_scope(
            masked, re.compile(rf"(?<![.\w$]){quoted}\s*\[\s*['\"`][^\]\n]*['\"`]\s*\]\s*\(")
        )
        if computed is not None:
            key = source[computed.start() : computed.end()]
            if any(mutator in key for mutator in _ARRAY_MUTATORS):
                raise UnresolvedRouteElementError(
                    f"{module}: `{key.strip()}` mutates the route array through a computed "
                    "member — the initializer no longer says what the binding holds"
                )
        # ⚠️ **The headline of round 3.** A module-scope call that RECEIVES the array
        # can do anything to it, and the walk has already finished reading the
        # initializer by then. An independent review registered a route with a
        # `titleKey` in neither locale through
        # `registerExtraRoutes(appRoutes as AppRoute[])` — 571 passed, `tsc` clean.
        # Enumerating mutator names can never reach this: the mutation is not
        # spelled here at all. What IS spelled here is that the binding escaped.
        escaped_into_call = _search_module_scope(
            masked, re.compile(rf"[A-Za-z_$][\w$.]*\s*\(\s*{quoted}\b(?!\s*\.)")
        )
        if escaped_into_call is not None:
            raise UnresolvedRouteElementError(
                f"{module}: `{alias}` is passed to a module-scope call "
                f"(`{source[escaped_into_call.start():escaped_into_call.end()].strip()}`) — "
                "whatever that function does to the array happens after this walk read the "
                "initializer, so the registered set cannot be derived"
            )
        if index_write is not None:
            raise UnresolvedRouteElementError(
                f"{module}: `{alias}[…] =` writes into the route array after its declaration "
                "— same reason as a mutator call: the initializer no longer says what the "
                "binding holds"
            )


def _module_scope_declarations(masked: str, name: str) -> "list[re.Match[str]]":
    """Declarations of ``name`` at **bracket depth 0** — i.e. module scope.

    ⚠️ This used to be ``_declaration_re(name).search(masked)``: the FIRST textual
    match, with no scope analysis. An independent adversarial review defeated the
    whole module with that one line — an ordinary-looking helper

    ``function noExtraRoutes() { const hiddenRoutes = []; return hiddenRoutes; }``

    placed ABOVE the real ``const hiddenRoutes = [ …one route with a titleKey that
    resolves in neither locale… ]`` won the search, so the walk read the empty
    array, reported 23 routes, and the entire lane stayed green while a screen
    shipped with a raw dotted key in its title slot. ``tsc`` had no opinion — the
    counterexample is production-legal. That is exactly the defect this module
    claims to be the only thing able to see.

    A spread in a module-scope array can only bind to a module-scope declaration,
    so depth is the whole judgement. Two of them is a redeclaration this walk must
    not silently pick a winner for.
    """
    matches: list[re.Match[str]] = []
    depth = 0
    pattern = _declaration_re(name)
    index = 0
    while index < len(masked):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "<" and _TYPE_ARGUMENT_OPEN_RE.match(masked, index):
            # `Foo<A, appRoutes>` is a type argument list, not a declarator list.
            depth += 1
        elif char == ">" and depth > 0 and masked[index - 1 : index] not in ("=", ">"):
            depth -= 1
        elif depth == 0:
            found = pattern.match(masked, index)
            if found is not None:
                matches.append(found)
                index = found.end()
                continue
        index += 1
    return matches


def _initializer_span(source: str, name: str, module: str) -> tuple[int, int]:
    """``[start, end)`` of the initializer expression bound to ``name``."""
    masked = mask_ts_noncode(source)
    declarations = _module_scope_declarations(masked, name)
    if not declarations:
        raise UnresolvedRouteElementError(
            f"{module}: no module-scope `const/let/var {name}` declaration — a spread named "
            "it, so either it moved, it is nested inside a function, or it is not a "
            "module-local binding. (A binding this walk cannot see is a route set it "
            "cannot see.)"
        )
    if len(declarations) > 1:
        raise UnresolvedRouteElementError(
            f"{module}: `{name}` is declared {len(declarations)} times at module scope — "
            "picking one silently is how a shadowing declaration hides a whole route array"
        )
    # The comma form of the pattern consumes the `=` itself (that is what makes it
    # a declarator rather than a type argument), so the assignment is already
    # located and searching forward would find the NEXT statement's — or nothing.
    match = declarations[0]
    assign = (
        match.end() - 1
        if masked[match.end() - 1 : match.end()] == "="
        else _assignment_index(masked, match.end())
    )
    if assign is None:
        raise UnresolvedRouteElementError(f"{module}: `{name}` is declared without an initializer")
    stop = _statement_end(masked, assign + 1)
    return assign + 1, stop


def _assignment_index(masked: str, start: int) -> int | None:
    """Index of the ``=`` that binds the declaration, skipping type annotations."""
    depth = 0
    for index in range(start, len(masked)):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return None
            depth -= 1
        elif char == ";" and depth == 0:
            return None
        elif char == "=" and depth == 0:
            following = masked[index + 1 : index + 2]
            preceding = masked[index - 1 : index]
            if following not in ("=", ">") and preceding not in ("=", "!", "<", ">"):
                return index
    return None


#: Statement keywords that can only begin a NEW top-level statement. Used as the
#: fallback boundary for an initializer that ends by ASI rather than by ``;`` —
#: without it a semicolon-free declaration swallows the rest of the file, and the
#: arrays of the next declaration are read as if they were this one's.
_STATEMENT_START_RE = re.compile(r"^(?:export|import|const|let|var|function|class|type)\b", re.M)


def _statement_end(masked: str, start: int) -> int:
    depth = 0
    for index in range(start, len(masked)):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == ";" and depth == 0:
            return index
        elif char == "\n" and depth == 0:
            following = _STATEMENT_START_RE.match(masked, index + 1)
            if following is not None:
                return index
    return len(masked)


#: Operators whose operands are VALUES of the whole expression, not conditions.
#: ``?:`` contributes its two arms; ``??`` and ``||`` contribute both sides.
_VALUE_OPERATORS = ("??", "||")


def _value_positions(masked: str, begin: int, end: int) -> "list[tuple[int, int]]":
    """Sub-spans of ``[begin, end)`` whose value can BECOME the binding's value.

    ⚠️ The previous shape — *"collect every outermost array literal in the
    initializer"* — silently lost every non-array arm. An independent adversarial
    review demonstrated five: ``f ? [lit] : IDENT``, ``IDENT : [lit]``,
    ``children: f ? [lit] : IDENT``, ``f ? [lit] : makeRoutes()``, and
    ``IDENT ?? [lit]``. The asymmetry was the tell — ``[lit] ?? IDENT`` raised
    loudly while ``IDENT ?? [lit]`` did not, because the guard only looked at what
    FOLLOWED a ``]``. Collecting the arrays it can see and ignoring the rest is
    exactly *"counting what it could not read as nothing"*.

    So the expression is decomposed instead: a conditional yields its two arms, a
    nullish/or chain yields both sides, and anything else is one position. Every
    position then has to BE an array literal — the caller raises otherwise, so an
    arm that is an identifier or a call is named rather than dropped.
    """
    span = _trim(masked, begin, end)
    if span is None:
        return []
    start, stop = span
    question = _top_level_conditional(masked, start, stop)
    if question is not None:
        colon = _matching_colon(masked, question + 1, stop)
        if colon is not None:
            return _value_positions(masked, question + 1, colon) + _value_positions(
                masked, colon + 1, stop
            )
    for operator in _VALUE_OPERATORS:
        at = _top_level_operator(masked, start, stop, operator)
        if at is not None:
            return _value_positions(masked, start, at) + _value_positions(
                masked, at + len(operator), stop
            )
    return [(start, stop)]


def _trim(masked: str, begin: int, end: int) -> "tuple[int, int] | None":
    while begin < end and masked[begin].isspace():
        begin += 1
    while end > begin and masked[end - 1].isspace():
        end -= 1
    return (begin, end) if begin < end else None


def _top_level_conditional(masked: str, begin: int, end: int) -> int | None:
    """Index of the ``?`` of a conditional at depth 0, skipping ``??`` and ``?.``."""
    depth = 0
    index = begin
    while index < end:
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "?" and depth == 0:
            following = masked[index + 1 : index + 2]
            if following == "?":
                index += 2
                continue
            if following != ".":
                return index
        index += 1
    return None


def _matching_colon(masked: str, begin: int, end: int) -> int | None:
    """The ``:`` that closes the conditional opened before ``begin``."""
    depth = 0
    nested = 0
    for index in range(begin, end):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 0 and char == "?" and masked[index + 1 : index + 2] not in ("?", "."):
            nested += 1
        elif depth == 0 and char == ":":
            if nested == 0:
                return index
            nested -= 1
    return None


def _top_level_operator(masked: str, begin: int, end: int, operator: str) -> int | None:
    depth = 0
    index = begin
    while index < end:
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 0 and masked.startswith(operator, index):
            return index
        index += 1
    return None


def _outermost_arrays(source: str, begin: int, end: int) -> "list[tuple[int, int]]":
    """The array literal spans this expression can evaluate to.

    Every value position must BE an array literal (a trailing ``as``/``satisfies``
    type assertion is erased first — it cannot change a member). A position that is
    anything else returns ``None`` for its span, and the caller turns that into a
    loud failure rather than a shorter answer.
    """
    masked = mask_ts_noncode(source)
    pairs = match_brackets(masked)
    spans: list[tuple[int, int]] = []
    for start, stop in _value_positions(masked, begin, end):
        closes = pairs.get(start)
        if masked[start] != "[" or closes is None or closes > stop:
            return []
        remainder = masked[closes:stop]
        if remainder.strip() and _TYPE_SUFFIX_RE.match(remainder) is None:
            return []
        spans.append((start, closes))
    return spans


#: What may legally follow the ``]`` of a route array literal. Anything else means
#: the array is an OPERAND of something — ``.concat(more)``, a call, an index —
#: and then the value bound to the name is not the array this walk just read.
_ARRAY_VALUE_TERMINATORS = frozenset(";,:)]}")

#: ``as const`` / ``satisfies <Type>`` immediately after the array. Erased before
#: the value exists, so it changes no member.
_TYPE_SUFFIX_RE = re.compile(r"^\s*(?:as|satisfies)\s+[A-Za-z_$][\w$.<>\[\]\s,|]*")


def _assert_array_value_is_not_transformed(
    module: str, source: str, array_end: int, expression_end: int
) -> None:
    """Raise when the array literal is consumed by an expression rather than bound.

    ⚠️ Without this the walk is defeated by ONE method call:
    ``export const appRoutes = [...visible].concat(hidden);`` reads perfectly to
    an "outermost array literals" scan, contributes every route in ``visible``,
    and drops ``hidden`` **silently** — the exact failure mode (a completeness
    claim that counts what it could not read as nothing) this module exists to
    remove. Naming the shape here costs one predicate; discovering it later costs
    another adversarial review.

    Bounded by ``expression_end`` rather than by end-of-file: the *next*
    statement is not this array's consumer, and a check that read it as one would
    reject the last declaration in a file that ends without a semicolon.
    """
    masked = mask_ts_noncode(source)
    tail = masked[array_end : min(expression_end, len(masked))]
    # `] as const` / `] satisfies readonly AppRoute[]` are TYPE assertions: they
    # cannot add or remove a member, so rejecting them would make this walk refuse
    # an idiom TypeScript users reach for constantly — and a gate that refuses
    # legitimate code is a gate that gets deleted. Everything the assertion names
    # is erased before the value exists, so it is skipped, not followed.
    tail = _TYPE_SUFFIX_RE.sub(" ", tail, count=1)
    for index, char in enumerate(tail):
        if char.isspace():
            continue
        if char in _ARRAY_VALUE_TERMINATORS:
            return
        raise UnresolvedRouteElementError(
            f"{module}: the route array is consumed by "
            f"`{source[array_end:array_end + 30].strip()}` rather than bound directly — "
            "whatever that expression adds or removes is invisible to this walk, so the "
            "registered set cannot be derived"
        )


def _collect_array(
    src_root: Path,
    module: str,
    source: str,
    start: int,
    end: int,
    parents: "tuple[str, ...]",
    out: "list[RouteEntry]",
    seen: "set[tuple[str, str, tuple[str, ...]]]",
) -> None:
    masked = mask_ts_noncode(source)
    for member_start, member_stop in split_top_level(masked, start, end):
        text = source[member_start:member_stop].strip()
        masked_text = masked[member_start:member_stop].strip()
        if masked_text.startswith("..."):
            identifier = masked_text[3:].strip()
            if not identifier.isidentifier():
                raise UnresolvedRouteElementError(
                    f"{module}: spread of a non-identifier expression {text!r} — this "
                    "walk cannot follow it, so the route set below it is unknown"
                )
            _resolve_spread(src_root, module, source, identifier, parents, out, seen)
            continue
        if not masked_text.startswith("{"):
            raise UnresolvedRouteElementError(
                f"{module}: route array member {text[:80]!r} is neither an object "
                "literal nor a spread — the registered set cannot be derived from it"
            )
        literal_start = member_start + masked[member_start:member_stop].index("{")
        literal_end = match_brackets(masked).get(literal_start)
        if literal_end is None:
            raise UnresolvedRouteElementError(f"{module}: unbalanced route object at {literal_start}")
        _collect_object(
            src_root, module, source, literal_start, literal_end, parents, out, seen
        )


def _collect_object(
    src_root: Path,
    module: str,
    source: str,
    start: int,
    end: int,
    parents: "tuple[str, ...]",
    out: "list[RouteEntry]",
    seen: "set[tuple[str, str, tuple[str, ...]]]",
) -> None:
    parsed = parse_ts_literal_members(source, start, end)
    # ⚠️ A member REMEMBERS WHICH FILE IT CAME FROM.
    #
    # A spread merges another module's members into this object, and their
    # ``start``/``end``/``value_start`` offsets are offsets into THAT module. The
    # first version of this merge kept only the ``TsEntry`` and then sliced
    # ``children`` out of the local source with foreign offsets — an unhandled
    # ``IndexError`` in the lucky case, and *reading another file's bytes as this
    # route's children* in the unlucky one. Both violate the contract this module
    # is built on ("unfollowable is loud"), so provenance travels with the member.
    members: dict[str, _OwnedMember] = {}
    # Source order, so a spread that FOLLOWS a key overwrites it — that is
    # JavaScript's rule, and the first version had own-keys-always-win, which
    # answers `/own` where the runtime answers `/spreadwins`.
    for member in parsed:
        if member.kind == "entry" and member.key is not None:
            members[member.key] = _OwnedMember(module, source, member)
        elif member.kind == "spread":
            members.update(_spread_members(src_root, module, source, member, seen))
    path = None
    if "path" in members:
        owned = members["path"]
        path = _string_value(owned.entry, owned.module, "path")
    index = (
        "index" in members
        and _TRUE_RE.match(members["index"].entry.value.strip()) is not None
    )
    if path is None and not index:
        raise UnresolvedRouteElementError(
            f"{module}: a route object declares neither a literal `path` nor `index: true` "
            f"— {source[start:start + 80]!r}"
        )
    boundary = members.get("errorElement")
    handle = members.get("handle")
    out.append(
        RouteEntry(
            module=module,
            path=path,
            index=index,
            # ⚠️ Presence is not a boundary. `errorElement: undefined` declares the
            # key and renders nothing, so the value has to be there too.
            has_error_element=boundary is not None and bool(boundary.entry.value.strip())
            and boundary.entry.value.strip() != "undefined",
            title_key=(
                _title_key(handle.entry, handle.module) if handle is not None else None
            ),
            parents=parents,
            # ⚠️ Comments stripped: `/* <RouteAnnouncer> */` inside the element
            # satisfied the announcer seal (measured). A seal that reads prose is a
            # seal that can be written to.
            element=(
                strip_ts_comments(members["element"].entry.value).strip()
                if "element" in members
                else ""
            ),
        )
    )
    children = members.get("children")
    if children is None:
        return
    # ⚠️ The child arrays are located with the SAME helper the top-level walk
    # uses, not with `.index("[")`. The shortcut took only the first array, so
    # `children: flag ? [a] : [b]` would have contributed one arm and dropped the
    # other **without saying so** — the identical silent-partial shape this module
    # rejects everywhere else. A `children` that is not an array literal at all
    # (an identifier, a call) has no arrays and is therefore loud.
    child_module, child_source, child_entry = children
    child_arrays = _outermost_arrays(child_source, child_entry.value_start, child_entry.end)
    if not child_arrays:
        raise UnresolvedRouteElementError(
            f"{child_module}: `children` is {child_entry.value.strip()[:60]!r}, not an array "
            "literal — the routes nested under it cannot be derived"
        )
    for array_start, array_end in child_arrays:
        _assert_array_value_is_not_transformed(
            child_module, child_source, array_end, child_entry.end
        )
        _collect_array(
            src_root,
            child_module,
            child_source,
            array_start,
            array_end,
            (*parents, path or ""),
            out,
            seen,
        )


def _spread_members(
    src_root: Path,
    module: str,
    source: str,
    spread: TsEntry,
    seen: "set[tuple[str, str, tuple[str, ...]]]",
    depth: int = 0,
) -> "dict[str, _OwnedMember]":
    """The keys an object spread inside a route object contributes.

    Resolved the same way a route-array spread is — module-scope declaration here,
    or the module an ``import``/re-export names. Unfollowable is loud, because the
    alternative is answering about a route object whose properties are unknown.

    ⚠️ **Nested spreads are followed too.** The first version filtered the
    resolved object's members with ``kind == "entry"`` — the identical filter it
    had just removed one level up — so a two-level shared-defaults arrangement
    (``{ ...CHILD_DEFAULTS }`` where ``CHILD_DEFAULTS`` spreads ``DEFAULTS``) lost
    everything the inner spread supplied and the walk accused a working route of
    having no boundary. Both failure modes this repair names are reproduced by the
    repair itself, one level down, unless it recurses.
    """
    if depth > _MAX_SPREAD_DEPTH:
        raise UnresolvedRouteElementError(
            f"{module}: object spreads nest more than {_MAX_SPREAD_DEPTH} deep — refusing to "
            "keep following rather than looping"
        )
    identifier = (spread.key or "").strip()
    if not identifier.isidentifier():
        raise UnresolvedRouteElementError(
            f"{module}: a route object spreads `...{identifier or spread.value.strip()[:40]}`, "
            "which is not a plain binding — this walk cannot tell which properties it supplies"
        )
    home_module, home_source = module, source
    if not _module_scope_declarations(mask_ts_noncode(source), identifier):
        target = _re_exported_from(source, identifier) or _imported_from(source, identifier)
        if target is None:
            raise UnresolvedRouteElementError(
                f"{module}: `...{identifier}` in a route object is neither declared here nor "
                "imported — the properties it supplies are unknown, and guessing them would "
                "report the wrong answer about a route rather than no answer"
            )
        specifier, exported_name = target
        home_module = _resolve_module(src_root, module, specifier)
        home_source = _read_module(src_root, home_module)
        identifier = exported_name
    begin, end = _initializer_span(home_source, identifier, home_module)
    masked = mask_ts_noncode(home_source)
    span = _trim(masked, begin, end)
    if span is None or masked[span[0]] != "{":
        raise UnresolvedRouteElementError(
            f"{home_module}: `{identifier}` is not an object literal, so the route object "
            "spreading it cannot be read"
        )
    close = match_brackets(masked).get(span[0])
    if close is None:
        raise UnresolvedRouteElementError(f"{home_module}: unbalanced object at {span[0]}")
    resolved: dict[str, _OwnedMember] = {}
    for member in parse_ts_literal_members(home_source, span[0], close):
        if member.kind == "entry" and member.key is not None:
            resolved[member.key] = _OwnedMember(home_module, home_source, member)
        elif member.kind == "spread":
            resolved.update(
                _spread_members(src_root, home_module, home_source, member, seen, depth + 1)
            )
    return resolved


def _string_value(member: TsEntry, module: str, label: str) -> str:
    match = _STRING_LITERAL_RE.match(member.value.strip())
    if match is None:
        raise UnresolvedRouteElementError(
            f"{module}: `{label}` is {member.value.strip()[:60]!r}, not a string literal — "
            "the address it registers cannot be derived"
        )
    text = match.group("text")
    return text if text is not None else match.group("tpl")


def _title_key(handle: TsEntry, module: str) -> str | None:
    text = handle.value.strip()
    masked = mask_ts_noncode(text)
    # The object span is located, not assumed. `handle: {…} as AppRouteHandle`
    # starts with `{` but does not END with `}`, and slicing to `len(text)` there
    # walks past the literal — the kind of off-by-a-suffix that reads as a parse
    # success and answers about the wrong bytes.
    span = match_brackets(masked).get(0) if masked.startswith("{") else None
    if span is None:
        raise UnresolvedRouteElementError(
            f"{module}: `handle` is {text[:60]!r}, not an object literal — the titleKey "
            "it declares cannot be checked against the locales"
        )
    for member in parse_ts_literal_members(text, 0, span):
        if member.kind == "entry" and member.key == "titleKey":
            value = _string_value(member, module, "titleKey")
            # ⚠️ `titleKey: ''` declares the key and names nothing. `is None` admits
            # it and a truthy filter drops it, so it passed BOTH title axes and both
            # locale halves at once (measured on `/auth/callback`). An empty name is
            # a missing name.
            if not value.strip():
                raise UnresolvedRouteElementError(
                    f"{module}: `titleKey` is empty — the screen has no name, and an "
                    "empty string satisfies every check that asks whether one is declared"
                )
            return value
    return None


def _resolve_spread(
    src_root: Path,
    module: str,
    source: str,
    identifier: str,
    parents: "tuple[str, ...]",
    out: "list[RouteEntry]",
    seen: "set[tuple[str, str, tuple[str, ...]]]",
) -> None:
    masked = mask_ts_noncode(source)
    # Depth-aware, like every other declaration question in this module: a bare
    # `search` would read `import { a, extra }` as a declaration of `extra`
    # now that the pattern also accepts a multi-declarator comma.
    if _module_scope_declarations(masked, identifier):
        _collect_declaration(src_root, module, identifier, parents, out, seen)
        return
    target = _imported_from(source, identifier)
    if target is None:
        raise UnresolvedRouteElementError(
            f"{module}: `...{identifier}` is neither declared here nor imported — this walk "
            "cannot follow it, so the routes it spreads in are invisible to every "
            "assertion built on this derivation"
        )
    specifier, exported_name = target
    resolved = _resolve_module(src_root, module, specifier)
    _collect_declaration(src_root, resolved, exported_name, parents, out, seen)


def _re_exported_from(source: str, identifier: str) -> "tuple[str, str] | None":
    """``export { A as B } from './x'`` — a barrel module forwarding a binding.

    ⚠️ Without this a barrel ``index.ts`` (a completely ordinary way to organise
    ``apps/web/src``) makes the walk raise, and a walk that refuses ordinary code
    is a walk somebody switches off. It is a *resolution* step, not a widening —
    the routes still have to be found, in the module the re-export names.
    """
    masked = mask_ts_noncode(source)
    for match in _RE_EXPORT_RE.finditer(masked):
        specifier = source[match.start("spec") : match.end("spec")]
        for binding in match.group("names").split(","):
            parsed = _NAMED_BINDING_RE.search(binding)
            if parsed is None:
                continue
            local = parsed.group("local") or parsed.group("source")
            if local == identifier:
                return specifier, parsed.group("source")
    return None


def _imported_from(source: str, identifier: str) -> tuple[str, str] | None:
    """``(specifier, name_in_that_module)`` for a local binding, else ``None``."""
    masked = mask_ts_noncode(source)
    for match in _IMPORT_RE.finditer(masked):
        clause = match.group("clause")
        # The specifier is string CONTENT, blanked in the mask — read it back
        # from the original at the same offsets.
        specifier = source[match.start("spec") : match.end("spec")]
        braced = re.search(r"\{(?P<names>[^}]*)\}", clause)
        if braced is not None:
            for binding in braced.group("names").split(","):
                parsed = _NAMED_BINDING_RE.search(binding)
                if parsed is None:
                    continue
                local = parsed.group("local") or parsed.group("source")
                if local == identifier:
                    return specifier, parsed.group("source")
        default = re.match(r"^\s*(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?:,|$)", clause)
        if default is not None and default.group("name") == identifier:
            return specifier, identifier
    return None


def _resolve_module(src_root: Path, importer: str, specifier: str) -> str:
    if specifier.startswith(_ALIAS_PREFIX):
        base = specifier[len(_ALIAS_PREFIX) :]
    elif specifier.startswith("."):
        # Normalised textually, never through the filesystem: `resolve()` would
        # follow symlinks and escape a synthetic tree, and this walk must give
        # the same answer for a real tree and a temporary one.
        base = _normalise((Path(importer).parent / specifier).as_posix())
    else:
        raise UnresolvedRouteElementError(
            f"{importer}: `{specifier}` is a package specifier — route objects are "
            "first-party source, so following it would leave the tree under audit"
        )
    for suffix in _MODULE_CANDIDATES:
        candidate = f"{base}{suffix}"
        if (src_root / candidate).is_file():
            return candidate
    raise UnresolvedRouteElementError(
        f"{importer}: cannot resolve `{specifier}` to a file under {src_root} "
        f"(tried {', '.join(base + suffix for suffix in _MODULE_CANDIDATES)})"
    )


def _normalise(path: str) -> str:
    """Collapse ``.``/``..`` segments without touching the filesystem."""
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)
