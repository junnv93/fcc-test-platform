#!/usr/bin/env python3
"""Single definition of "does this commit message carry the 17-item self-audit".

Why this is a module and not two copies
---------------------------------------
The rule already exists in ``tests/test_meta_self_audit_invariant.py``, and it
already fires — after the fact, on a commit that is usually already pushed. Two
commits reached ``main`` red on 2026-08-07/09 and neither could be repaired,
because history rewriting is unavailable once another session builds on it. The
repository's own conclusion from the work-claim branch guard and the merge
freshness guard applies here: *a rule that enforces nothing is a rule that can
be skipped*.

Moving enforcement earlier only helps if the earlier check answers **the same
question**. A hook with its own copy of the predicate is worse than no hook: it
blocks messages the gate would accept, someone turns it off, and the gate is
back to being the only line of defence — now with a disabled hook in the tree
suggesting otherwise. So the predicate lives here, the test imports it, and a
seal asserts the test keeps no second copy.

Deliberately dependency-free (standard library only): a git hook runs in
whatever interpreter is on the machine, with no project virtualenv guaranteed.

Two entry points, one grammar
-----------------------------
``check_message_file`` answers *"does this commit message carry the audit"*.
``extract_implementer_audit`` answers *"where in this agent report is the part
of the audit its author can actually attest to"*. Different questions, but they
share the item list and the row regex, and splitting them across modules would
mean declaring that grammar twice — which is the failure this module was
written to end.

Three axes, not one widened gate
--------------------------------
The original rule is a **format** axis: seventeen rows exist, each once, plus a
trailer. It says nothing about whether a ``PASS`` is true, so writing ``PASS``
costs less than writing ``SKIP`` — and two consecutive sessions forged rows
through exactly that gap, the second after reading a handoff that warned about
it. Fixing the economics needs ``PASS`` to cost something, and the repository's
standing rule is to *split the axis rather than widen the gate*:

* **format** — unchanged. ``has_physical_full_audit`` still answers only the
  original question, so the post-hoc historical gate stays green.
* **reason** (:func:`reasonless_rows`) — a status with nothing after it is the
  word PASS, not an attestation. This is not a new invention: the sibling entry
  point below already refuses reasonless and placeholder rows, so one module was
  judging one grammar by two standards. Measured over the last 300 audited
  commits, 99 of them (33%) state all seventeen rows bare.
* **value** (:func:`unsupported_claims`) — for the two items whose ``PASS``
  names a fixed artifact, the claim must be backed by an observation.

The value axis deliberately covers **two** of seventeen items, so
:data:`VALUE_AXIS_LIMITATION` names the other fifteen wherever it runs. Partial
enforcement that stays quiet about its own edges reads as full verification.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path
import re
import subprocess
import sys

#: Checklist items, in the order the checklist declares them.
#: `.claude/contracts/sprint-self-audit-checklist.md` is the prose SSOT; this is
#: the machine-readable one, and the invariants assert the two agree.
SELF_AUDIT_ITEMS: tuple[str, ...] = (
    'A-1', 'A-2', 'A-3', 'A-4',
    'B-1', 'B-2', 'B-3', 'B-4', 'B-5',
    'C-1', 'C-2', 'C-3', 'C-4', 'C-5',
    'D-1', 'D-2', 'D-3',
)

#: The checklist section whose items are *commit-custody* facts — how this
#: particular commit was assembled. Only whoever makes the commit can answer
#: them, which is why they are split out rather than listed a second time.
CUSTODY_SECTION = 'D'

#: Derived, never hand-written. A second literal list would go stale the first
#: time the checklist grows an item, and the two halves would silently stop
#: covering it between them.
CUSTODY_AUDIT_ITEMS: tuple[str, ...] = tuple(
    item for item in SELF_AUDIT_ITEMS if item.startswith(f'{CUSTODY_SECTION}-')
)
IMPLEMENTER_AUDIT_ITEMS: tuple[str, ...] = tuple(
    item for item in SELF_AUDIT_ITEMS if item not in CUSTODY_AUDIT_ITEMS
)

#: One physical line per item. Uppercase only — the checklist's own example
#: once used ``Pass``, which this pattern rejects, and a commit that followed
#: the example landed red. Documentation that disagrees with the gate is how
#: an author gets blamed for reading the docs.
PHYSICAL_AUDIT_LINE = re.compile(
    r'^(?P<item>[A-D]-[1-5]):\s+(?P<result>PASS|SKIP|N/A)(?:\s|$)'
)

#: Conventional Commits §footer / ``git interpret-trailers`` token syntax.
TRAILER_TOKEN_PATTERN = re.compile(r'^([A-Za-z][A-Za-z0-9-]*): (.+)$')

#: The trailer that marks a message as carrying the full audit.
SELF_AUDIT_TRAILER_TOKEN = 'Self-Audit'
SELF_AUDIT_TRAILER_VALUE = '17-items'

#: Escape hatch. Loud on purpose — an easy but visible bypass beats a hook
#: people delete.
BYPASS_ENV = 'FCC_ALLOW_MISSING_SELF_AUDIT'

#: A hook that hangs on git is a hook that gets uninstalled. Every observation
#: this module makes is optional, so a timeout degrades to "unobservable".
_GIT_TIMEOUT_SECONDS = 15

#: Message prefixes git itself authors or replays. Blocking these wedges the
#: operation that produced them (a rebase cannot be answered by editing a
#: message that was written months ago).
_REPLAYED_SUBJECT_PREFIXES = ('fixup!', 'squash!', 'amend!', 'Revert "')

#: Files git creates while an operation is mid-flight. Their presence means the
#: message being validated is not one the author is composing right now.
_IN_FLIGHT_MARKERS = (
    'MERGE_HEAD',        # merge commit — the audit gate exempts merges too
    'CHERRY_PICK_HEAD',
    'REVERT_HEAD',
    'rebase-merge',
    'rebase-apply',
    'BISECT_LOG',
)


def parse_trailers(body: str) -> dict[str, list[str]]:
    """Extract the trailing ``Token: value`` block(s) from a commit body.

    Scans paragraphs from the end and takes every consecutive paragraph whose
    non-blank lines are all trailers, stopping at the first prose paragraph.
    Slightly more permissive than ``git interpret-trailers --parse`` because
    real messages here often separate ``Self-Audit:`` from ``Co-Authored-By:``
    with a blank line.
    """
    text = body.rstrip('\n')
    if not text:
        return {}

    def _is_pure_trailer_paragraph(para: str) -> bool:
        lines = [line for line in para.split('\n') if line.strip()]
        return bool(lines) and all(TRAILER_TOKEN_PATTERN.match(line) for line in lines)

    trailer_paragraphs: list[str] = []
    for para in reversed(text.split('\n\n')):
        if _is_pure_trailer_paragraph(para):
            trailer_paragraphs.append(para)
        else:
            break

    trailers: dict[str, list[str]] = {}
    for para in reversed(trailer_paragraphs):
        for line in para.split('\n'):
            match = TRAILER_TOKEN_PATTERN.match(line)
            if match:
                trailers.setdefault(match.group(1), []).append(match.group(2))
    return trailers


def missing_audit_items(body: str) -> list[str]:
    """Items lacking exactly one physical status line, in checklist order."""
    lines = body.splitlines()
    missing: list[str] = []
    for item in SELF_AUDIT_ITEMS:
        matches = [
            line for line in lines
            if (match := PHYSICAL_AUDIT_LINE.match(line)) and match.group('item') == item
        ]
        if len(matches) != 1:
            missing.append(item)
    return missing


def row_reason(row: str, match: 're.Match[str]') -> str:
    """The attestation text after a status, or ``''`` when the row states none.

    One definition because three callers need the same answer: the implementer
    extractor, the reason axis, and the value axis. Two of them would otherwise
    disagree about whether ``B-4: PASS -`` carries a reason.
    """
    return row[match.end():].strip(' \t-—–:')


def audit_rows(body: str) -> list[tuple[str, str, str]]:
    """Every well-formed status row as ``(item, status, reason)``, in order.

    Malformed lines are not rows; the format axis already reports those as
    missing items, and re-reporting them here would name one defect twice.
    """
    rows: list[tuple[str, str, str]] = []
    for line in body.splitlines():
        match = PHYSICAL_AUDIT_LINE.match(line)
        if match:
            rows.append(
                (match.group('item'), match.group('result'), row_reason(line, match))
            )
    return rows


def _is_a_reason(text: str) -> bool:
    """At least one character a reader can actually see.

    ``strip`` alone is not enough: ``B-4: PASS \\xa0`` survived it and is
    indistinguishable from a bare status on screen. Measured — ``isprintable()``
    is ``False`` for NBSP, zero-width space, ideographic space, word joiner and
    BOM, and ``True`` for ordinary punctuation, so the pair of tests admits a
    deliberate one-character reason and refuses an invisible one. The sibling
    ``extract_implementer_audit`` inherits the same rule through this function.
    """
    return any(char.isprintable() and not char.isspace() for char in text)


def reasonless_rows(body: str) -> list[str]:
    """Items that state a status with no reason, or with the worked placeholder.

    The reason axis. Deliberately *not* folded into
    :func:`has_physical_full_audit`: the post-hoc gate judges commits that
    predate this rule, and widening the format predicate would turn a third of
    recorded history red for a rule it was never offered.
    """
    return [
        item for item, _status, reason in audit_rows(body)
        if not _is_a_reason(reason) or reason in PLACEHOLDER_REASONS
    ]


def has_trailer(body: str) -> bool:
    return parse_trailers(body).get(SELF_AUDIT_TRAILER_TOKEN) == [SELF_AUDIT_TRAILER_VALUE]


def has_physical_full_audit(body: str) -> bool:
    """The whole rule: 17 physical lines, each exactly once, plus the trailer."""
    return not missing_audit_items(body) and has_trailer(body)


def strip_comments(raw: str) -> str:
    """Drop the lines git will drop before storing the message.

    The hook sees the file as the author left it, including the ``#`` help
    text git appends. Validating text git is about to remove would let a
    commented-out trailer look real.
    """
    return '\n'.join(line for line in raw.splitlines() if not line.startswith('#'))


def in_flight_operation(git_dir: Path) -> str:
    """Name the mid-flight git operation, or ``''`` when the author is composing."""
    for marker in _IN_FLIGHT_MARKERS:
        if (git_dir / marker).exists():
            return marker
    return ''


def replayed_subject(body: str) -> str:
    subject = next((line for line in body.splitlines() if line.strip()), '')
    for prefix in _REPLAYED_SUBJECT_PREFIXES:
        if subject.startswith(prefix):
            return prefix
    return ''


def explain(body: str) -> str:
    """Operator-facing reason a message fails, naming what to add."""
    missing = missing_audit_items(body)
    parts: list[str] = []
    if missing:
        duplicated = [
            item for item in missing
            if sum(
                1 for line in body.splitlines()
                if (m := PHYSICAL_AUDIT_LINE.match(line)) and m.group('item') == item
            ) > 1
        ]
        absent = [item for item in missing if item not in duplicated]
        if absent:
            parts.append(f'missing status lines: {", ".join(absent)}')
        if duplicated:
            parts.append(f'duplicated status lines: {", ".join(duplicated)}')
    if not has_trailer(body):
        parts.append(
            f'final trailer block must contain "{SELF_AUDIT_TRAILER_TOKEN}: '
            f'{SELF_AUDIT_TRAILER_VALUE}" verbatim'
        )
    return '; '.join(parts)


#: Checklist items whose ``PASS`` names a **fixed** artifact, and the repo-relative
#: glob that artifact matches. Kept deliberately small: an item belongs here only
#: when its checklist verb is a *production* verb (등재/추가/작성), so that a truthful
#: ``PASS`` cannot mean anything except "I produced this".
#:
#: Candidates measured against 556 audited commits and rejected, so the next wave
#: does not re-measure:
#:
#: * ``A-3`` — 85.5% of branch-level ``PASS`` ships no edit, because the item's own
#:   text licenses ``PASS`` = "검토 완료, 해당 항목 없음". Permanently unverifiable.
#: * ``B-1``/``B-2`` — 45.5%/48.9%. A new test file routed by an existing glob needs
#:   no edit, so ``PASS`` legitimately reads "already registered". Real false-positive
#:   channel, and a gate with those gets switched off.
#: * ``A-2`` — 20.0%, but the item says 등재 **검토**, which licenses the same reading.
SUBSTANTIATED_CLAIM_ARTIFACTS: dict[str, tuple[str, ...]] = {
    'B-3': ('.claude/exec-plans/tech-debt-tracker.md',),
    'B-4': ('.claude/evaluations/*.md',),
}

#: Derived, never hand-written. When the checklist grows an item it lands here
#: automatically, so a new claim is announced as unchecked rather than silently
#: inheriting the confidence of the two that are.
UNCHECKED_AUDIT_ITEMS: tuple[str, ...] = tuple(
    item for item in SELF_AUDIT_ITEMS if item not in SUBSTANTIATED_CLAIM_ARTIFACTS
)

#: Said wherever the value axis runs. Two of seventeen items are observed; the
#: other fifteen are self-statements nothing checks. A partial gate that stays
#: quiet about its own edges reads as full verification — the same reason
#: ``hook_bypass_guard.GUARDRAIL_LIMITATION`` and ``supervisor_run``'s wrapper
#: notice exist. Not advisory: the invariants assert this text reaches stderr.
VALUE_AXIS_LIMITATION = (
    'self-audit value axis: only {checked} are checked against what this commit '
    'contains.\n'
    '  Unverified self-statements (nothing checks these): {unchecked}.'
)

#: A ``PASS`` whose artifact is not in this commit may point at the commit that
#: does carry it. Abbreviated git object names are 7+ hex characters; anything
#: shorter is a word, not a reference.
#:
#: This is the whole reason the escape exists and the reason it is safe: a
#: **backward** reference is cheap to write truthfully, and a **forward** one is
#: impossible — the commit that will carry the artifact has no name yet. The
#: rule "그 커밋에 실제로 들어있어야 PASS" stops being prose and becomes arithmetic.
_HEX_OBJECT_REFERENCE = re.compile(r'\b[0-9a-f]{7,40}\b')


def _matches_artifact(path: str, patterns: tuple[str, ...]) -> bool:
    """Anchored, repo-relative glob match.

    :func:`fnmatch.fnmatchcase` matches the **whole** string, so
    ``vendor/.claude/evaluations/x.md`` cannot satisfy a claim about this
    repository's own directory, and its ``*`` crosses ``/``, so
    ``.claude/evaluations/2026/nested.md`` does count. ``PurePosixPath.match``
    got the first right and the second wrong — it anchors at the *right* and
    refuses to cross a separator, which refused honest commits.
    """
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def unsupported_claims(
    body: str,
    *,
    changed_paths: 'tuple[str, ...] | None',
    paths_of_ref: 'object',
) -> list[tuple[str, str]]:
    """``PASS`` rows on declared items that no observation supports.

    Returns ``(item, expectation)`` pairs. Empty when the axis is satisfied and
    also when it cannot run — see below.

    Why the observations are arguments
    ----------------------------------
    Nothing here opens a file or shells out to git. The expectation is the
    declaration above, the observation is whatever the caller saw, and the
    verdict is this function; three things that do not know about each other.
    That is the same separation ``artifact_custody_policy`` and
    ``workbook_upload_gc_policy`` are built on, and it is what makes the verdict
    testable without a repository.

    ``paths_of_ref`` is a callable ``(str) -> tuple[str, ...] | None`` answering
    *"which paths did this git object touch, if it exists and is reachable"*.

    Fails open, deliberately. With no observation the axis is skipped rather
    than assumed-violated: refusing a commit because the gate could not look is
    the failure mode that gets hooks deleted. Both observations are required
    together — accepting ``changed_paths`` while ``paths_of_ref`` is missing
    would turn any hex-looking word into an unverifiable pass.
    """
    if changed_paths is None or not callable(paths_of_ref):
        return []

    findings: list[tuple[str, str]] = []
    for item, status, reason in audit_rows(body):
        patterns = SUBSTANTIATED_CLAIM_ARTIFACTS.get(item)
        if patterns is None or status != 'PASS':
            continue
        if any(_matches_artifact(path, patterns) for path in changed_paths):
            continue
        if any(
            (referenced := paths_of_ref(token)) is not None
            and any(_matches_artifact(path, patterns) for path in referenced)
            for token in _HEX_OBJECT_REFERENCE.findall(reason)
        ):
            continue
        findings.append((item, ' or '.join(patterns)))
    return findings


def value_axis_notice() -> str:
    """The limitation, rendered. Derived on every call so it cannot go stale."""
    return VALUE_AXIS_LIMITATION.format(
        checked=', '.join(SUBSTANTIATED_CLAIM_ARTIFACTS),
        unchecked=', '.join(UNCHECKED_AUDIT_ITEMS),
    )


#: Markers the Implementer wraps its attestation in. Chosen as HTML comments so
#: the block survives a Markdown report unrendered, and so the delimiters cannot
#: be confused with the audit rows themselves.
IMPLEMENTER_BLOCK_BEGIN = '<!-- SELF-AUDIT-BEGIN -->'
IMPLEMENTER_BLOCK_END = '<!-- SELF-AUDIT-END -->'

#: A report is agent prose of unbounded length; the attestation is not. The cap
#: is a refusal, not a truncation — silently keeping the first N characters of a
#: block would be the one failure mode this extractor exists to prevent.
MAX_IMPLEMENTER_BLOCK_CHARS = 8000

#: The reason text the contract's worked example uses. The example has to show
#: all fourteen rows — an elided one taught the wrong shape, and a row indented
#: to sit inside a Markdown list taught a shape the parser refuses. But a
#: complete, valid example in a prompt is a thing an agent can copy, and then
#: fourteen placeholder rows become somebody's attestation. Naming the
#: placeholder makes copying it a **structural** refusal rather than something a
#: reviewer has to notice. Not a heuristic: nothing rejects a reason for looking
#: unconvincing, only for being this exact literal.
PLACEHOLDER_REASONS = frozenset({'<근거>', '<사유>'})

#: Everything below 0x20 except tab. A commit message assembled from these would
#: be stored by git and then read back differently by whatever renders it, so a
#: block carrying them is refused rather than sanitised.
_FORBIDDEN_CONTROL_CHARS = frozenset(chr(code) for code in range(0x20)) - {'\t'}


def _marker_lines(lines: list[str], marker: str) -> list[int]:
    return [index for index, line in enumerate(lines) if line.strip() == marker]


def extract_implementer_audit(report: str) -> tuple[str | None, str]:
    """Lift the Implementer's ``A-1``…``C-5`` attestation out of its report.

    Returns ``(block, '')`` on success — the rows **verbatim**, so the reasons
    the Implementer wrote survive into the commit — or ``(None, reason)``.

    Why this lives beside the verdict rather than in the caller
    -----------------------------------------------------------
    The item list and the row regex are already defined here. A caller that
    re-derived them would answer a slightly different question than the gate,
    and this repository has already paid for that once: a preflight rebuilt from
    the predicate's parts diverged from it in four places at once. Two entry
    points over one grammar beats two grammars.

    The grammar is **closed**, not merely "contains the required rows". Counting
    only valid rows would let ``A-1: FAIL``, ``A-5: PASS``, or a paragraph of
    prose ride along inside the block and reach the commit message, where the
    gate — which counts only well-formed rows — would wave it through. So every
    non-blank line between the markers must be the next expected row, in order.

    What this cannot do is judge whether a ``PASS`` is true. Nothing here reads
    the repository. The block is an **unverified self-statement**; the channel
    that doubts it is the Reviewer, and its approval is what gates the commit.
    """
    if not report or not report.strip():
        return None, 'the Implementer report is empty'

    lines = report.splitlines()
    begins = _marker_lines(lines, IMPLEMENTER_BLOCK_BEGIN)
    ends = _marker_lines(lines, IMPLEMENTER_BLOCK_END)
    if len(begins) != 1:
        return None, (
            f'expected exactly one {IMPLEMENTER_BLOCK_BEGIN} line, found {len(begins)}'
        )
    if len(ends) != 1:
        return None, (
            f'expected exactly one {IMPLEMENTER_BLOCK_END} line, found {len(ends)}'
        )
    if ends[0] < begins[0]:
        return None, (
            f'{IMPLEMENTER_BLOCK_END} appears before {IMPLEMENTER_BLOCK_BEGIN}'
        )

    inner = lines[begins[0] + 1:ends[0]]
    block_chars = sum(len(line) + 1 for line in inner)
    if block_chars > MAX_IMPLEMENTER_BLOCK_CHARS:
        return None, (
            f'the block is {block_chars} characters, over the '
            f'{MAX_IMPLEMENTER_BLOCK_CHARS} limit'
        )
    for line in inner:
        offending = _FORBIDDEN_CONTROL_CHARS.intersection(line)
        if offending:
            return None, (
                'the block contains control characters: '
                + ', '.join(sorted(hex(ord(char)) for char in offending))
            )

    rows = [line for line in inner if line.strip()]
    expected = IMPLEMENTER_AUDIT_ITEMS
    # Named before the count, because a custody row inside the block is a
    # specific authoring mistake with a specific fix, and reporting it as
    # "found 15 lines, expected 14" would send the author looking for a
    # duplicate that is not there.
    intruders = [
        match.group('item')
        for row in rows
        if (match := PHYSICAL_AUDIT_LINE.match(row))
        and match.group('item') in CUSTODY_AUDIT_ITEMS
    ]
    if intruders:
        return None, (
            f'{", ".join(intruders)} '
            f'{"are" if len(intruders) > 1 else "is"} commit-custody item'
            f'{"s" if len(intruders) > 1 else ""}, answered by whoever makes the '
            f'commit; remove {"/".join(CUSTODY_AUDIT_ITEMS)} from the block'
        )
    if len(rows) != len(expected):
        return None, (
            f'expected exactly {len(expected)} status lines '
            f'({expected[0]}…{expected[-1]}), found {len(rows)}'
        )
    for row, item in zip(rows, expected):
        match = PHYSICAL_AUDIT_LINE.match(row)
        if match is None:
            return None, (
                # Not stripped: a leading space is itself the defect on a row
                # that otherwise reads as correct, and the gate is just as
                # unforgiving about it.
                f'expected a "{item}: PASS|SKIP|N/A — reason" line at column 1, '
                f'found: {row[:80]!r}'
            )
        found = match.group('item')
        if found == item:
            # A status with nothing after it is not an attestation — it is the
            # word PASS. The *gate* accepts it (it counts rows, and loosening
            # the gate is out of this module's remit here), but a block that the
            # supervisor is about to carry into a commit as somebody's evidence
            # has to carry the evidence.
            reason_text = row_reason(row, match)
            if not _is_a_reason(reason_text):
                return None, (
                    f'{item} states a status with no reason; every row must read '
                    f'"{item}: PASS|SKIP|N/A — <reason>"'
                )
            if reason_text in PLACEHOLDER_REASONS:
                return None, (
                    f'{item} still carries the worked example\'s placeholder '
                    f'({reason_text}); write the actual reason'
                )
            continue
        if found in CUSTODY_AUDIT_ITEMS:
            return None, (
                f'{found} is a commit-custody item and belongs to whoever makes '
                'the commit; remove '
                f'{"/".join(CUSTODY_AUDIT_ITEMS)} from the block'
            )
        return None, f'expected {item} at this position, found {found}'

    return '\n'.join(row.rstrip() for row in rows), ''


def _axis_refusal(body: str) -> str:
    """The reason axis, worded for the author. ``''`` when satisfied."""
    bare = reasonless_rows(body)
    if not bare:
        return ''
    return (
        'self-audit gate: these rows state a status with no reason: '
        f'{", ".join(bare)}.\n'
        '  A status with nothing after it is the word PASS, not an attestation —\n'
        '  the same rule the Implementer attestation block already enforces.\n'
        f'  Format: "{bare[0]}: PASS|SKIP|N/A — <why>"\n'
        '  Checklist: .claude/contracts/sprint-self-audit-checklist.md\n'
        f'  Deliberate exception: {BYPASS_ENV}=1 git commit ...'
    )


def _value_refusal(findings: list[tuple[str, str]]) -> str:
    """The value axis, worded for the author. ``''`` when satisfied."""
    if not findings:
        return ''
    lines = [
        'self-audit gate: these rows claim PASS but this commit does not carry '
        'the artifact they name:',
    ]
    for item, expectation in findings:
        lines.append(f'  {item}: PASS — expected this commit to touch {expectation}')
    lines.extend([
        '  Two honest fixes: include the artifact in this commit, or write',
        '  "SKIP — <why it is not this commit\'s scope>". A PASS may also name the',
        '  commit that does carry it ("PASS — landed in 3f2a1b9"); a reference',
        '  forward, to a commit that does not exist yet, is what this refuses.',
        f'  Deliberate exception: {BYPASS_ENV}=1 git commit ...',
    ])
    return '\n'.join(lines)


def check_message_file(
    path: Path,
    *,
    git_dir: Path,
    changed_paths: 'tuple[str, ...] | None' = None,
    paths_of_ref: 'object' = None,
) -> tuple[int, str]:
    """Exit code plus the message to print. Fails open wherever it is unsure.

    ``changed_paths``/``paths_of_ref`` are the value axis's observations. Both
    default to absent, which makes this call byte-identical to the format-only
    behaviour every existing caller already relies on — the axis is added, not
    substituted.
    """
    if os.environ.get(BYPASS_ENV, '').strip() not in ('', '0'):
        return 0, f'self-audit gate bypassed via {BYPASS_ENV}.'

    operation = in_flight_operation(git_dir)
    if operation:
        return 0, f'self-audit gate skipped: {operation} in progress.'

    try:
        # Decoded with replacement rather than strictly: the audit rows are
        # ASCII, so one undecodable byte anywhere in the prose used to skip the
        # whole gate. That was a second bypass nobody declared, reachable
        # without the environment variable — measured by an adversarial review.
        raw = path.read_bytes().decode('utf-8', errors='replace')
    except OSError as exc:
        return 0, f'self-audit gate skipped: cannot read message ({exc}).'

    body = strip_comments(raw)
    if not body.strip():
        # An empty message aborts the commit anyway; refusing here would only
        # replace git's own clearer error.
        return 0, ''

    prefix = replayed_subject(body)
    if prefix:
        return 0, f'self-audit gate skipped: {prefix} message is replayed, not authored.'

    if not has_physical_full_audit(body):
        return 1, (
            'self-audit gate: this commit message does not carry the 17-item audit.\n'
            f'  {explain(body)}\n'
            '  Format: 17 physical lines "A-1: PASS — reason" … "D-3: PASS — reason"\n'
            '          (uppercase PASS/SKIP/N/A), then a final trailer block containing\n'
            f'          "{SELF_AUDIT_TRAILER_TOKEN}: {SELF_AUDIT_TRAILER_VALUE}".\n'
            '  Checklist: .claude/contracts/sprint-self-audit-checklist.md\n'
            f'  Deliberate exception: {BYPASS_ENV}=1 git commit ...'
        )

    # Reason axis before value axis: a bare `B-4: PASS` fails both, and naming
    # the missing artifact first would send the author to add a file when what
    # the row is missing is the sentence saying which file.
    refusal = _axis_refusal(body)
    if refusal:
        return 1, refusal

    if changed_paths is None or not callable(paths_of_ref):
        if changed_paths is None and paths_of_ref is None:
            # Neither observation offered: an in-process caller asking the
            # format question only (the supervisor preflight does this), not a
            # degradation. Saying something here would put a warning on a call
            # that never intended to run the axis.
            return 0, ''
        # One observation and not the other means the caller tried and could
        # not — announced rather than silent, because "the axis passed" and
        # "the axis did not run" must not look the same.
        return 0, (
            'self-audit value axis SKIPPED: this commit could not be observed '
            '(git unavailable, or not a repository).\n'
            f'  {", ".join(SUBSTANTIATED_CLAIM_ARTIFACTS)} were not checked here, '
            'on top of the items below.\n'
            f'  {value_axis_notice()}'
        )

    findings = unsupported_claims(
        body, changed_paths=changed_paths, paths_of_ref=paths_of_ref
    )
    if findings:
        return 1, _value_refusal(findings) + '\n' + value_axis_notice()

    return 0, value_axis_notice()


def _git(*args: str) -> 'str | None':
    """``git`` stdout, or ``None`` when git cannot answer. Never raises."""
    try:
        result = subprocess.run(
            ('git', *args), capture_output=True, text=True, timeout=_GIT_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


#: ``--numstat -z`` rows are ``added \t deleted \t path`` NUL-terminated. A
#: rename leaves the path field empty and follows with two more fields (from,
#: to); a binary file reports ``-`` for both counts.
_NUMSTAT_ROW = re.compile(r'^(?P<added>\d+|-)\t(?P<deleted>\d+|-)\t(?P<path>.*)$', re.S)


def _paths_that_gained_content(*git_args: str) -> 'list[str] | None':
    """Paths a diff **added lines to**, or ``None`` when git cannot answer.

    Not ``--name-only``. The declared items' checklist verbs are 등재/추가/작성 —
    *production* verbs — and a bare name list cannot tell production from its
    opposite. Measured against the real gate: a commit that **deletes** the
    ledger, and a commit that creates a **0-byte** evaluation, both satisfied
    ``--name-only`` and passed. ``touch`` twice was the cheapest forgery
    available, which is the exact economics this axis exists to change.

    ``-z`` is load-bearing: without it git C-quotes non-ASCII paths and an
    honest commit touching ``docs/education/한글….html`` is refused.
    """
    out = _git(*git_args, '--numstat', '-z')
    if out is None:
        return None
    fields = [field for field in out.split('\0') if field != '']
    paths: list[str] = []
    index = 0
    while index < len(fields):
        row = _NUMSTAT_ROW.match(fields[index])
        if row is None:
            index += 1
            continue
        path = row.group('path')
        if path == '':
            # Rename/copy: the two following fields are the old and new paths.
            path = fields[index + 2] if index + 2 < len(fields) else ''
            index += 3
        else:
            index += 1
        added = row.group('added')
        # A binary file reports '-'; it plainly carries content, and refusing
        # to judge it would be the gate declining to look.
        if path and (added == '-' or int(added) > 0):
            paths.append(path)
    return paths


def observe_changed_paths() -> 'tuple[str, ...] | None':
    """Paths this commit will contain, or ``None`` if unobservable.

    ``git diff --cached`` is right even for the repository's house style
    ``git commit -F <msg> -- <files>``: git builds a temporary index for a
    pathspec commit and exports it as ``GIT_INDEX_FILE``, so the diff reports
    the pathspec-limited set rather than everything a parallel session happens
    to have staged. Measured, not assumed — the invariants commit into a real
    repository with an unrelated file staged and assert it stays out.

    ``HEAD``'s own paths join it, and that is about ``--amend``
    -----------------------------------------------------------
    During an amend the staged diff is taken against ``HEAD``, which *is* the
    commit being replaced — so a commit that legitimately carries the artifact
    shows an empty diff and gets refused. Measured: refusing a truthful amend
    is exactly the false positive that gets hooks uninstalled.

    git tells a ``commit-msg`` hook **nothing** about amending — identical
    environment, identical ``.git`` markers, and ``prepare-commit-msg`` reports
    source ``message`` with no sha whenever ``-m``/``-F`` is used. Measured on
    this machine's git. So amend cannot be detected; the observation has to be
    right without knowing.

    Including ``HEAD``'s paths is *exact* for an amend and a **named,
    one-commit leniency** for an ordinary commit: the row after the one that
    landed the artifact may claim ``PASS`` without naming it. That is the same
    claim the axis already accepts when written explicitly (``PASS — <sha>``),
    so the escape is bounded to a reference the author could have written, and
    it errs open — the direction this module errs in everywhere else.
    """
    staged = _paths_that_gained_content('diff', '--cached')
    if staged is None:
        return None
    head = _paths_that_gained_content('show', '--format=', 'HEAD')
    return tuple(staged) + tuple(head or ())


#: Refs tried, in order, to find where this branch left the shared history.
#: ``@{upstream}`` first because it is what the author actually configured;
#: the rest are this repository's conventions.
_UPSTREAM_CANDIDATES = ('@{upstream}', 'origin/main', 'origin/HEAD')


#: ⚠️ Said wherever the wave window is explained, because the window's floor is
#: derived from **local refs an author can move**. ``git update-ref
#: refs/remotes/origin/main <root>`` widens the escape back to all of history,
#: silently and without the declared environment variable; so does simply not
#: fetching. Verifying the floor against the real remote would put a network
#: round-trip inside a commit hook, which is how hooks get uninstalled.
#:
#: This is a guardrail against forgery-by-habit, not a boundary against a
#: determined author — the same standing this repository already gives
#: ``hook_bypass_guard.GUARDRAIL_LIMITATION``. Stated in three places so the
#: next session does not build something on top of it that needs it to be more.
WAVE_WINDOW_LIMITATION = (
    'the wave window is derived from local refs (@{upstream}/origin/main/'
    'origin/HEAD); an author who moves them, or who has not fetched, widens it. '
    'Guardrail, not a security boundary.'
)


def wave_base() -> 'str | None':
    """Where this branch diverged from the shared history, if that is knowable.

    ``None`` when no upstream resolves — a throwaway repository with no remote
    has no "shared history", so *every* reachable commit is legitimately part of
    the current line of work and there is nothing to narrow to.
    """
    for ref in _UPSTREAM_CANDIDATES:
        base = _git('merge-base', 'HEAD', ref)
        if base is not None and base.strip():
            return base.strip()
    return None


def make_reference_resolver() -> 'object':
    """A ``(token) -> paths | None`` oracle over **this wave's** commits.

    Reachability from ``HEAD`` alone is not enough, and measuring said so
    loudly: of this repository's 677 ancestors, **470** touch the ledger or an
    evaluation file. A resolver that accepts any reachable ancestor turns the
    escape into *"write any of 470 hashes"*, and ``PASS`` costs nothing again —
    the precise failure this axis exists to end. An adversarial review found it
    by naming two unrelated 2026-08 commits; the unit tests missed it because
    they injected a fake resolver and never asked git.

    So the window is the wave: reachable from ``HEAD`` **and not** already in
    the shared history. That is also the semantically right window — the claim
    is *"this wave produced the artifact"*, and an artifact from a previous PR
    is exactly the case whose honest answer is ``SKIP``.
    """
    base = wave_base()
    cache: dict[str, 'tuple[str, ...] | None'] = {}

    def paths_of_ref(token: str) -> 'tuple[str, ...] | None':
        if token not in cache:
            cache[token] = resolve_reference(token, floor=base, tip='HEAD')
        return cache[token]

    return paths_of_ref


def resolve_reference(
    token: str, *, floor: 'str | None', tip: str,
) -> 'tuple[str, ...] | None':
    """Paths a referenced object added, if it is inside ``floor..tip``.

    ``tip`` and ``floor`` are parameters because the two enforcement points
    stand at different places and must not ask the question two different ways.
    The hook stands at ``HEAD`` with the wave base as its floor; the post-hoc
    gate stands at each commit it judges, with the axis baseline as its floor
    (a merged branch's divergence point is not recoverable afterwards, so the
    post-hoc floor is coarser — named, not accidental).

    An adversarial review found the two had drifted into separate grammars, one
    using ``--name-only`` and no floor at all, which left the whole defect open
    in the population the post-hoc gate exists for. This function is the answer
    to that: one grammar, two anchors.
    """
    commit = f'{token}^{{commit}}'
    resolved = _git('rev-parse', '--verify', commit)
    if resolved is None or not resolved.strip().startswith(token):
        # The token has to *be* the object's name, not merely resolve to one.
        # A tag or branch spelled in hex would otherwise satisfy a claim, which
        # is a name an author controls rather than a fact about history.
        return None
    if _git('merge-base', '--is-ancestor', commit, tip) is None:
        return None
    if floor is not None and _git('merge-base', '--is-ancestor', commit, floor) is not None:
        # Already below the floor: not this line of work's doing.
        return None
    return _paths_that_gained_content('show', '--format=', commit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('message_file', type=Path)
    parser.add_argument(
        '--git-dir', type=Path, default=None,
        help='directory holding in-flight operation markers (default: derived)',
    )
    args = parser.parse_args(argv)

    git_dir = args.git_dir
    if git_dir is None:
        # The message file lives in the worktree's git dir for every git
        # operation that runs this hook, so its parent is the right place to
        # look for MERGE_HEAD and friends — including linked worktrees, where
        # `.git` is a file and a hardcoded `<root>/.git` would be wrong.
        git_dir = args.message_file.resolve().parent

    code, message = check_message_file(
        args.message_file,
        git_dir=git_dir,
        changed_paths=observe_changed_paths(),
        paths_of_ref=make_reference_resolver(),
    )
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
