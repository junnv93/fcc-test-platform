"""Census of *numeric threshold* comparisons in a pytest/unittest source file.

Why this exists (ledger ``[2026-08-19 headless-helper] P3``)
-----------------------------------------------------------
A conformance gate exists to report defects. When it instead reports a
**premise that moved**, it costs more than it saves: somebody has to run a
pristine-base diff to learn that the red is not a regression, and the cheapest
way to make that red go away is to widen the gate.

Two shapes produce that failure, and this repository has paid for both:

* ``consumers >= 10`` — the ``10`` was *the number of route modules that each
  built their own failure*. When the transport was consolidated into the
  clients the number legitimately fell to 6, and the gate reported the
  **improvement as a regression**.
* ``\\bstatus\\b`` inside a 200-character window — true in the old tree not
  because the predicate was right but because there was more code between the
  two lines. Removing the transport moved them closer and a correct guard
  clause was reported as an offender.

Both were repaired one at a time. The *shape* was left behind, so this module
makes the shape visible: it enumerates every numeric threshold in a file so a
gate can require each one to be **declared**, and so the default for a new one
is rejection rather than silence.

Two questions, and both of them had to be inverted
--------------------------------------------------
⚠️ **Two independent adversarial reviews defeated two successive drafts, each
time through a deny-list wearing an allow-list's docstring.** The record is
kept here because the same mistake is available again.

*The walk* was the first. Draft one visited comparisons in three enumerated
"syntactic homes" and gated on ``attr.startswith("assert")``; a bare ``assert``
under a ``BoolOp``, a comparison returned by a helper, a comparison in a
keyword argument and ``failUnlessEqual`` all escaped. The walk now visits
**every** ``ast.Compare`` in the module, wherever it lives.

*The operand* was the second, and the review's phrase for it was exact: the
diagnosis had been applied to only one of the module's two deny-lists. Draft
two asked *"can I resolve this operand to an integer?"* and exempted whatever
it could not read — so ``16.0`` (one character), a dict subscript, an ``Enum``
member, an imported constant, ``int('16')``, a helper returning the number, and
a name assigned twice were all silently *not thresholds*. Every one of those is
a hand-picked number.

The total question is the opposite one, and it is what this module now asks:

    A comparison is a threshold when exactly one side **measures the tree**
    and the other does not. The side that does not measure the tree is the
    threshold — whatever it is spelled as, and whether or not its value can
    be read.

*Measuring the tree* is ``len(...)`` / ``sum(...)`` / ``.count(...)`` / a name
in which *count* is a word / a local bound to one of those. Nothing else is,
and an operand this module cannot read is **reported**, never exempted:
*"I cannot read this number"* must not become *"this is not a number"*.

What stays exempt, and it is a short list
-----------------------------------------
1. the **floor** — a comparison that asserts only emptiness, non-emptiness or
   sign (``> 0``, ``>= 1``, ``== 0``, ``!= 0``, ``< 1``, ``<= 0``, ``< 0``,
   ``>= 0``). ``0`` and ``1`` there are not numbers anybody chose; they are the
   boundary of the number line. ⚠️ The floor is judged on the *bare*
   comparison — a number folded into the measurement by arithmetic
   (``len(xs) - 16 >= 0``) is reported separately, because that shape hid in
   the one place this module promised never to look.
2. a comparison where **both** sides measure the tree — a derived comparison,
   which is the destination this axis recommends.
3. a comparison where **neither** side measures the tree — that is a value
   comparison (``assertEqual(armed, tabled)``), not a magnitude claim.

``unittest`` assertion *methods* carry an implicit comparison with no
``Compare`` node, so those need a table — but it is **derived from the
``TestCase`` class itself** by identity of the underlying function, so every
alias the runtime actually binds is in it without anybody maintaining a list.

dependency-free: standard library only, so a conformance test can import it
without dragging ``src/`` or third-party packages into its process.
"""

from __future__ import annotations

import ast
import re
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


__all__ = [
    "ASSERTION_CENSUS_LIMITATION",
    "ThresholdSite",
    "assertion_comparison_methods",
    "census_numeric_thresholds",
    "census_from_source",
    "SYNTACTIC_HOME_FIXTURES",
    "EXEMPT_SHAPE_FIXTURES",
    "ARITHMETIC_OPERATOR",
]


#: Announced by the gate that consumes this census. The partial-mechanism
#: discipline this repository already uses (``hook_bypass_guard
#: .GUARDRAIL_LIMITATION`` / ``supervisor_run.WRAPPER_LIMITATION`` /
#: ``self_audit_message.VALUE_AXIS_LIMITATION``): a mechanism that covers part
#: of an axis says so in three places — a module constant, its own docstring,
#: and the rule prose.
#:
#: ⚠️ Draft one said "magnitude claims only", which was a wider claim than the
#: code delivered. This text states the boundary the code actually draws.
ASSERTION_CENSUS_LIMITATION = (
    "assertion-threshold census: a comparison is read as a threshold only when "
    "exactly one side is a SYNTACTICALLY RECOGNISABLE measurement of the tree "
    "(a len/sum call, a .count() call, a *_count name, or a local bound to "
    "one). A count reached through a helper this module cannot see is "
    "therefore invisible to it, and a comparison between two operands it "
    "cannot recognise as a measurement is out of scope by design — such a "
    "comparison is an identity, not an arrangement, and cannot report a "
    "refactor as a regression. TWO named blind spots follow from that: a size "
    "or latency BUDGET (`assertLess(headroom, 36414)`) compares two values "
    "neither of which is a recognisable count, so it is not read; and scope is "
    "ONE file, so a threshold moved to another module is not seen."
)


def assertion_comparison_methods(
    case_class: type = unittest.TestCase,
) -> "dict[str, str]":
    """Assertion methods on ``case_class`` that carry an implicit comparison.

    ⚠️ **Derived, not listed** — a hand list is a deny-list of spellings, and
    ``failUnlessEqual`` escaped exactly such a list. ``case_class`` is a
    parameter so the derivation can be exercised against a class that *does*
    bind aliases: on Python 3.12 ``unittest`` no longer ships them, so a seal
    that only looked at today's runtime would certify nothing.
    """
    canonical = {
        "assertGreater": ">",
        "assertGreaterEqual": ">=",
        "assertLess": "<",
        "assertLessEqual": "<=",
        "assertEqual": "==",
        "assertNotEqual": "!=",
    }
    table: "dict[str, str]" = {}
    for name, operator in canonical.items():
        target = getattr(case_class, name, None)
        if target is None:  # pragma: no cover - defensive
            continue
        for attribute in dir(case_class):
            if getattr(case_class, attribute, None) is target:
                table[attribute] = operator
    return table


_ASSERTION_METHODS = assertion_comparison_methods()

#: Comparisons that assert only *"not vacuous"*, *"empty"* or *"negative"*.
_FLOOR_COMPARISONS = frozenset(
    {
        (">", 0),
        (">=", 1),
        (">=", 0),
        ("<", 1),
        ("<", 0),
        ("<=", 0),
        ("==", 0),
        ("!=", 0),
    }
)

_COMPARE_OPS = {
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Eq: "==",
    ast.NotEq: "!=",
}

#: Reversing operand order reverses the operator: ``0 < len(xs)`` and
#: ``len(xs) > 0`` are the same claim and must classify identically.
_MIRRORED_OP = {">": "<", "<": ">", ">=": "<=", "<=": ">=", "==": "==", "!=": "!="}

_COUNTING_CALLS = frozenset({"len", "sum"})
_COUNTING_METHODS = frozenset({"count"})

#: ⚠️ A name is a count when *count* is a **word** in it — not when the letters
#: happen to occur. ``discount`` ends with ``count`` and is a price. Same shape
#: as ``autoDownload`` containing ``toDownload``.
_COUNT_NAME_RE = re.compile(r"(?:^|_)count(?:_|$)|[a-z0-9]Count$")

#: The keyword names ``unittest`` gives the two compared operands. A caller may
#: legally write ``assertGreaterEqual(len(x), b=16)``.
_OPERAND_KEYWORDS = ("first", "second", "a", "b")

#: Marks a threshold that arithmetic folded into the measured side.
ARITHMETIC_OPERATOR = "arith"

#: ``assertEqual(offenders, [])`` is an **emptiness** assertion — the canonical
#: offenders-list form this repository writes everywhere — and the empty
#: container carries no chosen number. It is part of the floor, in the same
#: sense ``== 0`` is.
_EMPTY_CONTAINER_TYPES = (list, dict, set, tuple, frozenset, str)


@dataclass(frozen=True)
class ThresholdSite:
    """One numeric threshold, attributed to the function that owns it."""

    owner: str
    """``Class.function`` — where the threshold lives.

    Deliberately not the line number: line numbers drift under unrelated edits,
    so a registry keyed by them would need touching by every neighbouring
    change. A renamed function *should* make a gate red, and it does.
    """

    lineno: int
    operator: str
    value: str
    """The threshold as an identity string — its number when this module can
    read one, otherwise the operand's source text.

    ⚠️ **Never dropped when unreadable.** An operand this module cannot resolve
    is still a threshold; exempting it is exactly how ``16.0`` disappeared.
    """

    spelling: str

    @property
    def key(self) -> str:
        """Registry identity: owner **and value**.

        ⚠️ Draft two keyed a declaration by ``owner`` alone, which made every
        declared function a blanket exemption — an adversarial review added a
        literal ``4711`` to an already-declared function and the gate stayed
        green while its own docstring promised the opposite.
        """
        return f"{self.owner} {self.operator}{self.value}"

    def describe(self) -> str:
        return f"{self.key} (L{self.lineno}, {self.spelling})"


def _measurement_nodes(node: ast.AST) -> "Iterator[ast.AST]":
    """Walk ``node``, but never descend into a subscript's index.

    ⚠️ ``text[index]`` is an **element**, not a count, even when ``index`` is a
    walk position. Letting the index leak upward made ``char = text[index]``
    read as a measurement and turned ``char == '\\'`` — a character test — into
    a reported threshold. Elements and counts are different things and the
    slice is where they part.
    """
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(node, ast.Subscript) and child is node.slice:
            continue
        yield from _measurement_nodes(child)


def _is_derivation(node: ast.AST, count_names: "frozenset[str]") -> bool:
    """Does this expression *measure the tree under test*?"""
    for inner in _measurement_nodes(node):
        if isinstance(inner, ast.Call):
            func = inner.func
            if isinstance(func, ast.Name) and func.id in _COUNTING_CALLS:
                return True
            if isinstance(func, ast.Attribute) and func.attr in _COUNTING_METHODS:
                return True
        elif isinstance(inner, ast.Name):
            if _COUNT_NAME_RE.search(inner.id) or inner.id in count_names:
                return True
        elif isinstance(inner, ast.Attribute):
            if _COUNT_NAME_RE.search(inner.attr):
                return True
    return False


def _is_empty_container(node: ast.AST) -> bool:
    """``[]`` / ``{}`` / ``()`` / ``''`` / ``set()`` — an emptiness claim.

    ``assertEqual(offenders, [])`` is the canonical offenders-list form this
    repository writes everywhere, and the empty container carries no chosen
    number. It belongs to the floor in the same sense ``== 0`` does.
    """
    if isinstance(node, (ast.List, ast.Tuple)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value == ""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in {
            kind.__name__ for kind in _EMPTY_CONTAINER_TYPES
        } and not (node.args or node.keywords)
    return False


def _fixed_number(node: ast.AST) -> "int | float | None":
    """The number this expression denotes, when that is knowable.

    Used **only** for the floor comparison and to make the identity readable —
    never to decide whether something is a threshold.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            return None
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _fixed_number(node.operand)
        if inner is None:
            return None
        return -inner if isinstance(node.op, ast.USub) else inner
    return None


def _identity_of(node: ast.AST) -> str:
    number = _fixed_number(node)
    if number is not None:
        return repr(number)
    # Source text, whitespace-normalised, so a reflow does not churn the key.
    return re.sub(r"\s+", " ", ast.unparse(node)).strip()


def _local_count_names(scope: ast.AST, count_names: "frozenset[str]") -> "frozenset[str]":
    """Names bound to a measurement inside one function body.

    ``n = len(rows)`` … ``assertEqual(n, 17)`` is an exact-count claim wearing a
    local variable, and it escaped the first draft entirely.
    """
    found = set(count_names)
    # A name that accumulates (`scanned += 1`) is measuring the tree as much as
    # `len(...)` does — and `index < len(text)` then becomes measurement vs
    # measurement, which is exempt for the right reason rather than by a
    # special case.
    for node in ast.walk(scope):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            found.add(node.target.id)
    for node in ast.walk(scope):
        values: "list[tuple[ast.AST, ast.AST]]" = []
        if isinstance(node, ast.Assign):
            values = [(target, node.value) for target in node.targets]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            values = [(node.target, node.value)]
        elif isinstance(node, ast.NamedExpr):
            values = [(node.target, node.value)]
        for target, value in values:
            if isinstance(target, ast.Name) and _is_derivation(value, frozenset(found)):
                found.add(target.id)
    return frozenset(found)


def _arithmetic_thresholds(node: ast.AST) -> "Iterator[ast.AST]":
    """Fixed numbers folded into a measurement by arithmetic.

    ⚠️ ``assertTrue(len(xs) - 16 >= 0)`` is algebraically ``len(xs) >= 16`` and
    it hid in the **floor** — the one place this module promises never to look.
    A number arithmetically attached to a measurement is a threshold wherever
    the comparison operator happens to put it.
    """
    for inner in ast.walk(node):
        if not isinstance(inner, ast.BinOp):
            continue
        for side in (inner.left, inner.right):
            number = _fixed_number(side)
            if number is not None and number not in (0, 1):
                yield side


def _comparison_sites(
    *,
    left: ast.AST,
    right: ast.AST,
    operator: str,
    owner: str,
    lineno: int,
    spelling: str,
    count_names: "frozenset[str]",
) -> "Iterator[ThresholdSite]":
    left_measures = _is_derivation(left, count_names)
    right_measures = _is_derivation(right, count_names)

    if left_measures == right_measures:
        # Both measure the tree (a derived comparison — the destination), or
        # neither does (a value comparison, not a magnitude claim).
        return

    measured, threshold, effective = (
        (left, right, operator)
        if left_measures
        else (right, left, _MIRRORED_OP[operator])
    )

    number = _fixed_number(threshold)
    empty = effective in {"==", "!="} and _is_empty_container(threshold)
    if not empty and (
        number is None or (effective, number) not in _FLOOR_COMPARISONS
    ):
        yield ThresholdSite(
            owner=owner,
            lineno=lineno,
            operator=effective,
            value=_identity_of(threshold),
            spelling=spelling,
        )

    # …and any number arithmetic has hidden inside the measured side.
    for folded in _arithmetic_thresholds(measured):
        yield ThresholdSite(
            owner=owner,
            lineno=lineno,
            operator=ARITHMETIC_OPERATOR,
            value=_identity_of(folded),
            spelling=spelling,
        )


def _assertion_operands(node: ast.Call) -> "tuple[ast.AST, ast.AST] | None":
    """The two compared operands, positional or by keyword."""
    operands = list(node.args[:2])
    if len(operands) < 2:
        for keyword in node.keywords:
            if keyword.arg in _OPERAND_KEYWORDS:
                operands.append(keyword.value)
    if len(operands) != 2:
        return None
    return operands[0], operands[1]


def census_from_source(source: str, *, filename: str = "<census>") -> "list[ThresholdSite]":
    """Enumerate every numeric threshold in ``source``."""
    tree = ast.parse(source, filename=filename)
    found: "list[ThresholdSite]" = []
    seen: "set[tuple[int, int, str]]" = set()

    def record(site: ThresholdSite, col: int) -> None:
        identity = (site.lineno, col, f"{site.operator}{site.value}")
        if identity in seen:
            return
        seen.add(identity)
        found.append(site)

    def walk(
        node: ast.AST,
        stack: "list[str]",
        count_names: "frozenset[str]",
        spelling: str,
    ) -> None:
        # A bare ``assert`` names itself in the failure message; the hint rides
        # down the subtree because the comparison may sit under a ``BoolOp``.
        if isinstance(node, ast.Assert):
            spelling = "assert"
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            stack = [*stack, node.name]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                count_names = _local_count_names(node, count_names)
        owner = ".".join(stack) if stack else "<module>"

        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for index, op in enumerate(node.ops):
                operator = _COMPARE_OPS.get(type(op))
                if operator is None:
                    continue
                for site in _comparison_sites(
                    left=operands[index],
                    right=operands[index + 1],
                    operator=operator,
                    owner=owner,
                    lineno=node.lineno,
                    spelling=spelling,
                    count_names=count_names,
                ):
                    record(site, node.col_offset)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            operator = _ASSERTION_METHODS.get(node.func.attr)
            if operator is not None:
                pair = _assertion_operands(node)
                if pair is not None:
                    for site in _comparison_sites(
                        left=pair[0],
                        right=pair[1],
                        operator=operator,
                        owner=owner,
                        lineno=node.lineno,
                        spelling=node.func.attr,
                        count_names=count_names,
                        ):
                        record(site, node.col_offset)

        for child in ast.iter_child_nodes(node):
            walk(child, stack, count_names, spelling)

    walk(tree, [], frozenset(), "compare")
    found.sort(key=lambda site: (site.lineno, site.operator, site.value))
    return found


def census_numeric_thresholds(path: Path) -> "list[ThresholdSite]":
    """``census_from_source`` for a file on disk."""
    return census_from_source(path.read_text(encoding="utf-8"), filename=str(path))


#: One battery of *"this shape must be seen"* fixtures, shared by every seal.
#:
#: ⚠️ **Two copies of this table already drifted once** — 13 rows in the
#: conformance gate against 16 in the primitive's own seals, neither a superset
#: of the other, on the very commit that created them. A forced duplicate needs
#: a parity gate or a single owner; this is the single owner.
SYNTACTIC_HOME_FIXTURES: "dict[str, str]" = {
    "ordering assertion method": "self.assertGreaterEqual(len(xs), 16)",
    "compare nested in assertTrue": "self.assertTrue(len(xs) >= 16)",
    "compare nested in assertFalse": "self.assertFalse(len(xs) >= 16)",
    "compare under a BoolOp": "self.assertTrue(ok and len(xs) >= 16)",
    "exact count": "self.assertEqual(len(xs), 17)",
    "exact count on a method": "self.assertEqual(src.count('x'), 17)",
    "exact count on a *_count name": "self.assertEqual(row_count, 17)",
    "exact count on a camelCase count": "self.assertEqual(rowCount, 17)",
    "reversed operands": "self.assertGreaterEqual(16, len(xs))",
    "reversed operands in a compare": "self.assertTrue(16 <= len(xs))",
    "less-than form": "self.assertLess(len(xs), 99)",
    "inside a keyword argument": "self.assertTrue(ok, msg=(len(xs) >= 16))",
    "as the operand keyword": "self.assertGreaterEqual(len(xs), b=16)",
    "inside a generator expression": "self.assertTrue(all(len(g) >= 16 for g in gs))",
    "inside a comprehension filter": "self.assertTrue([g for g in gs if len(g) >= 16])",
    "inside a lambda": "self.assertTrue(any(map(lambda g: len(g) >= 16, gs)))",
    "inside a ternary": "self.assertTrue(True if len(xs) >= 16 else False)",
    "a float threshold": "self.assertGreaterEqual(len(xs), 16.0)",
    "a subscripted constant": "self.assertGreaterEqual(len(xs), LIMITS['routes'])",
    "an attribute constant": "self.assertGreaterEqual(len(xs), Limits.MIN.value)",
    "a converted string": "self.assertGreaterEqual(len(xs), int('16'))",
    "a shifted literal": "self.assertGreaterEqual(len(xs), 2 << 3)",
    "a helper returning the number": "self.assertGreaterEqual(len(xs), _floor())",
    "arithmetic folded into the measurement": "self.assertTrue(len(xs) - 16 >= 0)",
}

#: Shapes that must **not** be reported. The destination of every repayment is
#: in here, and a gate that reports its own advice gets deleted.
EXEMPT_SHAPE_FIXTURES: "dict[str, str]" = {
    "greater than zero": "self.assertGreater(len(xs), 0)",
    "at least one": "self.assertGreaterEqual(len(xs), 1)",
    "empty": "self.assertEqual(len(xs), 0)",
    "not empty": "self.assertNotEqual(len(xs), 0)",
    "less than one": "self.assertLess(len(xs), 1)",
    "at most zero": "self.assertLessEqual(len(xs), 0)",
    "reversed floor": "self.assertTrue(0 < len(xs))",
    "no offenders": "self.assertEqual(offenders_of(len(xs)), [])",
    "a derived comparison": "self.assertEqual(len(a), len(b))",
    "a value comparison": "self.assertEqual(armed, tabled)",
    "an http status": "self.assertEqual(resp.status, 404)",
    "a price that ends in count": "self.assertEqual(discount, 5)",
}
