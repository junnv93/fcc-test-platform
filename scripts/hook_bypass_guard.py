"""Judge whether a Bash tool command bypasses this repository's commit gates.

Why this exists
----------------
Three commit-gate layers already exist — the 17-item self-audit
(``scripts/self_audit_message.py``), the work-claim branch guard
(``scripts/work_claim_branch_guard.py``), and the merge-freshness guard
(``scripts/merge_readiness_guard.py``) — and all three are *git hooks*.
``core.hooksPath`` is repository-global, so a single ``--no-verify`` (or a
``-c core.hooksPath=/dev/null`` prefix) disables all three at once. This
module is the judgement for a fourth layer that runs one step earlier, at the
point an agent issues the Bash tool call that would run that command.

This is a guardrail, not a security boundary
---------------------------------------------
The layer this module feeds reads only the text of a Bash tool call. It
cannot see, and cannot stop, ``bash -c '...'`` with the flag hidden inside
the quoted string, a wrapper script that runs the flagged command internally,
or a shell variable that expands to the flag at run time. Anyone who reaches
a real shell can still bypass the hooks. What this catches is the *typed*
form — the common, undisguised case — the same posture this repository's
other hygiene hooks already take (fail-open, loud about what it cannot see).

Two axes, because ``-n`` does not mean one thing
-------------------------------------------------
``--no-verify`` means "skip hooks" everywhere git defines it, and nowhere
git does not define it does the flag do anything else — so an exact token
match is sufficient regardless of subcommand. ``-n`` is not like that: it is
``--no-verify`` for ``commit``/``am``, but ``--dry-run`` for ``push``,
``--no-commit`` for ``cherry-pick``, and ``--no-stat`` for ``merge``/
``rebase``. Treating every bare ``-n`` as a bypass would block those
legitimate uses and teach people to reach for ``--no-verify`` (this module's
other, unambiguous case) or turn the hook off outright — the failure mode
this repository has already paid for twice (work-claim guard, merge-
freshness guard): a rule that produces false positives gets deleted, and a
deleted rule protects nothing.

False positives, structurally excluded
----------------------------------------
Regex substring matching cannot tell a real flag from the same text sitting
inside a quoted commit message (``git commit -m "block --no-verify"``) or
after ``--`` (``git commit -- --no-verify``, a file named that). Both are
common, both are legitimate, and this module's own commits use the first
one. So parsing goes through ``shlex`` (structural tokenizing, not text
search), and the token stream is walked with git's own option grammar: `--`
ends flag parsing, known value-taking options consume the token that
follows them, and short-option clusters are scanned left to right, stopping
at the first letter that takes a value (the remainder of that token is that
value, never a further flag — this is what makes ``-nm msg`` a bypass but
``-mn msg`` message text starting with ``n``).

Reason codes are part of the design, not incidental
-----------------------------------------------------
Every verdict carries a reason. Some reasons mean "the parser understood
this and it is not a bypass" (judged-allow); others mean "the parser could
not make sense of this at all" (fail-open). The two must stay distinguishable
— a case that is fail-open only because nobody exercised the judged-allow
path it actually deserves is a gap in the parser wearing a passing grade.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import re
import shlex
import sys

# --------------------------------------------------------------- limitation

#: Repeated verbatim in the block message and (once the wrapper hook exists)
#: in its stderr — the fact that this is a guardrail, not a boundary, has to
#: travel with every refusal it issues, or the next reader assumes it is more
#: than it is.
GUARDRAIL_LIMITATION = (
    'guardrail, not a security boundary — reads only the Bash tool command '
    'string; bash -c, script files and shell variables pass through unseen.'
)

# --------------------------------------------------------------- the flag

#: The one and only spelling of the literal this module exists to catch.
#: Every other reference in this file uses this name, never the string
#: again, so the string cannot silently drift between two spots that were
#: supposed to agree.
NO_VERIFY_FLAG = '--no-verify'

# ------------------------------------------------------------- reason codes

#: Judged-allow: the parser understood the command and concluded, on
#: specific structural grounds, that it does not bypass a hook.
CLEAN = 'clean'
N_MEANS_SOMETHING_ELSE = 'n-means-something-else'
AFTER_DOUBLE_DASH = 'after-double-dash'
FLAG_IS_AN_OPTION_VALUE = 'flag-is-an-option-value'
HOOKS_PATH_SUBCOMMAND_DOES_NOT_RUN_HOOKS = 'hooks-path-subcommand-does-not-run-hooks'
NOT_A_GIT_INVOCATION = 'not-a-git-invocation'

#: Judged-block: the parser found a specific, named bypass.
LONG_NO_VERIFY = 'long-no-verify-flag'
SHORT_N_NO_VERIFY = 'short-n-flag-means-no-verify'
HOOKS_PATH_OVERRIDE = 'hooks-path-override'

#: Fail-open: the parser could not judge, so it allows — loudly.
UNTOKENIZABLE = 'untokenizable'
NO_COMMAND = 'no-command'

#: The set a case must *not* land in in order to count as an actual
#: judgement rather than an admission the parser gave up. Used by this
#: module's own test suite to keep "judged" and "gave up" from blurring.
#: Only reasons a real code path actually returns belong here — a member
#: no branch ever produces is a fail-open reason wearing a passing grade
#: on a path that does not exist (this module's own docstring calls that
#: out: "a gap in the parser wearing a passing grade").
FAIL_OPEN_REASONS = frozenset({UNTOKENIZABLE, NO_COMMAND})

BLOCK = 'block'
ALLOW = 'allow'


@dataclass(frozen=True)
class Decision:
    verdict: str
    reason: str
    detail: str = ''
    clause: str = ''

    @property
    def blocked(self) -> bool:
        return self.verdict == BLOCK


# ---------------------------------------------------------- the -n meaning

#: What a bare ``-n`` means for each subcommand this module knows about.
#: Values are meaning tokens, not booleans, because "not no-verify" is not
#: one fact — ``push -n`` and ``merge -n`` fail for different reasons if
#: someone tries to use them as `--no-verify`, and a case that reaches
#: N_MEANS_SOMETHING_ELSE should be able to say what it actually means.
#: The no-verify meaning reuses ``NO_VERIFY_FLAG`` itself rather than a
#: second hand-written spelling — a copy is exactly the kind of second
#: definition that drifts the first time someone edits one but not the
#: other.
_MEANING_NO_STAT = 'no-stat'
_MEANING_NO_COMMIT = 'no-commit'
_MEANING_DRY_RUN = 'dry-run'

SHORT_N_MEANING: dict[str, str] = {
    'commit': NO_VERIFY_FLAG,
    'am': NO_VERIFY_FLAG,
    'merge': _MEANING_NO_STAT,
    'rebase': _MEANING_NO_STAT,
    'cherry-pick': _MEANING_NO_COMMIT,
    'revert': _MEANING_NO_COMMIT,
    'push': _MEANING_DRY_RUN,
}

#: Derived, never hand-written — a second hand-written set is exactly the
#: kind of second copy that drifts the first time this table grows a row.
SHORT_N_MEANS_NO_VERIFY: frozenset[str] = frozenset(
    subcommand for subcommand, meaning in SHORT_N_MEANING.items()
    if meaning == NO_VERIFY_FLAG
)

# ------------------------------------------------------- hooks-running set

#: Subcommands that run git hooks locally on an ordinary invocation. Values
#: are the membership reason, not decoration — a subcommand not on this list
#: (``status``, ``clone``: cloning does not run the *new* repo's local
#: commit-time hooks, and overriding hooksPath before cloning an untrusted
#: repo is itself a legitimate safety measure) means overriding
#: ``core.hooksPath`` ahead of it has nothing to disable.
HOOK_RUNNING_SUBCOMMANDS: dict[str, str] = {
    'commit': 'creates a commit — runs pre-commit/commit-msg/post-commit',
    'merge': 'can create a merge commit — runs pre-merge-commit/commit-msg/post-merge',
    'am': 'applies patches as commits — runs applypatch-msg/pre-applypatch/post-applypatch',
    'rebase': 'replays commits — runs pre-rebase and, per replayed commit, commit-msg',
    'push': 'runs pre-push before contacting the remote',
    'cherry-pick': 'creates a commit from an existing one — runs pre-commit/commit-msg',
    'revert': 'creates a commit undoing another — runs pre-commit/commit-msg',
}

# --------------------------------------------------------- option grammar

#: Long options that always consume the *next* token as a separate value
#: (``--message foo``), as opposed to the ``--message=foo`` form, which
#: cannot be mistaken for anything else because the value is inside the same
#: token. Not exhaustive — a guardrail only needs the options common enough
#: to otherwise generate false positives.
VALUE_TAKING_LONG_OPTIONS = frozenset({
    '--message', '--file', '--author', '--date', '--template',
    '--reedit-message', '--reuse-message',
})

#: Short-option letters that consume a value — either the remainder of the
#: same token (``-mFOO``) or, if nothing follows within the token, the next
#: separate token (``-m FOO``). Shared across subcommands deliberately: this
#: is a guardrail, and treating an ambiguous letter as "probably takes a
#: value" is the direction of error that avoids mis-flagging ``-n`` sitting
#: after it in the same cluster (``-mn`` — see module docstring).
SHORT_VALUE_FLAGS = frozenset({'m', 'F', 'C', 'c', 't'})

#: Operators that end one clause and start the next, for splitting a single
#: line's token stream into independently-judged git invocations.
CLAUSE_SEPARATORS = frozenset({'&&', '||', ';', '|', '&', '(', ')'})

#: Global options that take a value as a *separate* token.
_GLOBAL_SEPARATE_VALUE_OPTIONS = frozenset({
    '-C', '--git-dir', '--work-tree', '--namespace', '--exec-path',
})

#: Global long options whose value is always inline (``--git-dir=...``).
_GLOBAL_INLINE_VALUE_PREFIXES = (
    '--git-dir=', '--work-tree=', '--namespace=', '--exec-path=', '--config-env=',
)

_ENV_ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=.*$')


# -------------------------------------------------------------- segmenting


class _SegmentError(ValueError):
    """Raised when the command cannot be confidently split into segments."""


def _segment(command: str) -> list[str]:
    """Split a possibly multi-line command into independent logical lines.

    A bare, unquoted newline is a real command boundary in bash — the same
    as ``;`` — so each one starts a fresh segment that is judged on its own.
    Heredoc bodies are dropped entirely: they are data, not commands, and a
    parser that flattened a heredoc body into the same token stream as
    surrounding commands would be exactly the version of this guard that
    flags its own changelist (a commit message assembled via heredoc is this
    repository's standard form). Open quotes and a trailing backslash both
    suppress the newline that would otherwise end a segment, matching what
    the shell itself does. An unquoted ``#`` that starts a word begins a
    comment running to end of line — quotes and heredoc markers *inside*
    that comment are text, not syntax, and must not be allowed to open a
    quote or heredoc that swallows the next, real line (``echo ok # <<EOF``
    followed by a real command on the next line is exactly the shape that
    would otherwise vanish into a heredoc body that never closes, or a
    quote that never closes, and get judged as unparsable rather than as
    the command it actually is).

    Best-effort, not a full shell grammar — see the module docstring.
    """
    in_squote = False
    in_dquote = False
    heredoc_queue: list[tuple[str, bool]] = []
    segments: list[str] = []
    buf_lines: list[str] = []

    raw_lines = command.split('\n')
    idx = 0
    while idx < len(raw_lines):
        line = raw_lines[idx]
        idx += 1

        if heredoc_queue:
            delim, strip_tabs = heredoc_queue[0]
            probe = line.lstrip('\t') if strip_tabs else line
            if probe == delim:
                heredoc_queue.pop(0)
            continue  # heredoc body lines are data — never scanned, never kept

        heredoc_starts: list[tuple[str, bool]] = []
        saw_comment = False
        j = 0
        n = len(line)
        while j < n:
            ch = line[j]
            if in_squote:
                if ch == "'":
                    in_squote = False
                j += 1
                continue
            if in_dquote:
                if ch == '\\' and j + 1 < n:
                    j += 2
                    continue
                if ch == '"':
                    in_dquote = False
                j += 1
                continue
            if ch == '\\' and j + 1 < n:
                j += 2  # unquoted backslash escapes the next char
                continue
            if ch == "'":
                in_squote = True
                j += 1
                continue
            if ch == '"':
                in_dquote = True
                j += 1
                continue
            if ch == '#' and (j == 0 or line[j - 1] in ' \t;&|()<>'):
                # Unquoted comment starting a word — everything after this
                # point on the line is text, not syntax. Stop scanning so a
                # quote or heredoc marker written *inside* the comment
                # cannot suppress the newline that ends this segment.
                # A word starts not only after whitespace but also right
                # after a shell control operator (';', '&', '|', '(', ')',
                # '<', '>') — bash treats '#' as a comment marker there too
                # (`echo ok;# <<EOF` really is a comment, not a heredoc
                # start), so the same set of characters must be honoured
                # here or that shape falls through as unparsable/untokenizable
                # instead of being judged.
                saw_comment = True
                break
            if line[j:j + 2] == '<<':
                k = j + 2
                strip_tabs = False
                if k < n and line[k] == '-':
                    strip_tabs = True
                    k += 1
                while k < n and line[k] in ' \t':
                    k += 1
                delim = ''
                if k < n and line[k] in '\'"':
                    quote = line[k]
                    k += 1
                    start = k
                    while k < n and line[k] != quote:
                        k += 1
                    delim = line[start:k]
                    if k < n:
                        k += 1  # consume closing quote
                else:
                    start = k
                    while k < n and not line[k].isspace() and line[k] not in '&|;()<>':
                        k += 1
                    delim = line[start:k]
                if delim:
                    heredoc_starts.append((delim, strip_tabs))
                j = k
                continue
            j += 1

        buf_lines.append(line)

        if in_squote or in_dquote:
            continue  # unterminated quote — this newline is data, keep accumulating

        joined = '\n'.join(buf_lines)
        # A trailing backslash inside a comment does not continue the line
        # in bash — comments always end at the newline — hence saw_comment.
        if not saw_comment and joined.endswith('\\') and not joined.endswith('\\\\'):
            buf_lines[-1] = buf_lines[-1][:-1]
            continue  # line continuation — this newline is not a boundary

        segments.append('\n'.join(buf_lines))
        buf_lines = []
        heredoc_queue.extend(heredoc_starts)

    if buf_lines:
        raise _SegmentError('unterminated quote or line continuation at end of command')
    if heredoc_queue:
        raise _SegmentError(f'unterminated heredoc (missing delimiter {heredoc_queue[0][0]!r})')

    return segments


def _tokenize(segment: str) -> list[str]:
    lexer = shlex.shlex(segment, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _split_clauses(tokens: list[str]) -> list[list[str]]:
    clauses: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in CLAUSE_SEPARATORS:
            if current:
                clauses.append(current)
                current = []
            continue
        current.append(tok)
    if current:
        clauses.append(current)
    return clauses


# ------------------------------------------------------------ git argv


def _is_git_executable(token: str) -> bool:
    name = os.path.basename(token)
    if name.lower().endswith('.exe'):
        name = name[:-4]
    return name == 'git'


def _is_env_executable(token: str) -> bool:
    name = os.path.basename(token)
    if name.lower().endswith('.exe'):
        name = name[:-4]
    return name == 'env'


#: sudo's own short options that take a value as a *separate* token
#: (``sudo -u root ...``, ``sudo -C 3 ...``). Consuming only the flag and
#: leaving its value in place is what turned ``sudo -u root git commit
#: --no-verify`` into "not a git invocation" — the value token ("root")
#: is not a flag, so the flag-skipping loop below it would otherwise stop
#: on it and never reach ``git``.
_SUDO_VALUE_TAKING_OPTIONS = frozenset({'-u', '-g', '-h', '-p', '-C', '-D', '-R', '-T', '-U'})


def _skip_wrapper_prefix(tokens: list[str]) -> int:
    """Advance past leading ``VAR=val`` assignments, a ``sudo`` wrapper, and
    an ``env`` (or path-form ``/usr/bin/env``) wrapper, in any order, until
    none apply — returning the index of what should be the actual command.

    Best-effort, matching the same posture as the rest of this module: it
    recognises the common, undisguised forms (``FOO=1 git ...``, ``sudo -u
    root git ...``, ``env FOO=1 git ...``) without trying to be a full
    shell-invocation grammar.
    """
    i = 0
    n = len(tokens)
    while True:
        progressed = False

        while i < n and _ENV_ASSIGNMENT.match(tokens[i]):
            i += 1
            progressed = True

        if i < n and tokens[i] == 'sudo':
            i += 1
            while i < n and tokens[i].startswith('-') and tokens[i] != '--':
                tok = tokens[i]
                i += 2 if tok in _SUDO_VALUE_TAKING_OPTIONS else 1
            if i < n and tokens[i] == '--':
                i += 1
            progressed = True
            continue

        if i < n and _is_env_executable(tokens[i]):
            i += 1
            progressed = True
            continue

        if not progressed:
            break

    return i


def _parse_global_options(tokens: list[str]) -> tuple[int, bool]:
    """Split git's global options off the front. Returns (subcommand index,
    whether ``core.hooksPath`` was overridden in this global-options region).

    Deliberately does not look past the subcommand — ``git commit -c
    HEAD~1`` uses commit's own ``-c``/``--reedit-message``, a different
    option that happens to share a spelling, and only the *global* ``-c``
    can override hooksPath.
    """
    hooks_override = False
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if not tok.startswith('-'):
            return i, hooks_override
        if tok == '-c':
            if i + 1 < n:
                value = tokens[i + 1]
                key = value.split('=', 1)[0].strip().lower()
                if key == 'core.hookspath':
                    hooks_override = True
                i += 2
                continue
            i += 1
            continue
        if tok in _GLOBAL_SEPARATE_VALUE_OPTIONS:
            i += 2
            continue
        if any(tok.startswith(prefix) for prefix in _GLOBAL_INLINE_VALUE_PREFIXES):
            i += 1
            continue
        # Unknown global flag — consume just itself; conservative, but this
        # is a guardrail and an unrecognised global option is rare.
        i += 1
    return n, hooks_override


def _scan_short_cluster(token: str, subcommand: str) -> tuple[bool, bool, bool]:
    """Walk a bundled or standalone short-option token left to right.

    Returns ``(blocks, consumes_next_token, saw_benign_n)``. A value-taking
    letter ends the scan: the remainder of the token, if any, is that
    letter's inline value, never a further flag — this is what makes
    ``-nm msg`` a bypass but ``-mn`` message text starting with ``n``. An
    unrecognised letter does not consume anything and scanning continues.
    """
    chars = token[1:]
    saw_benign_n = False
    for idx, ch in enumerate(chars):
        if ch == 'n':
            if subcommand in SHORT_N_MEANS_NO_VERIFY:
                return True, False, saw_benign_n
            saw_benign_n = True
            continue
        if ch in SHORT_VALUE_FLAGS:
            remainder = chars[idx + 1:]
            return False, remainder == '', saw_benign_n
    return False, False, saw_benign_n


def _judge_git_argv(
    subcommand: str, rest_tokens: list[str], *, hooks_path_override: bool, clause: str,
) -> Decision:
    if hooks_path_override and subcommand in HOOK_RUNNING_SUBCOMMANDS:
        return Decision(
            BLOCK, HOOKS_PATH_OVERRIDE,
            f'core.hooksPath was overridden ahead of "git {subcommand}" '
            f'({HOOK_RUNNING_SUBCOMMANDS[subcommand]}).',
            clause=clause,
        )

    if hooks_path_override:
        reason, detail = HOOKS_PATH_SUBCOMMAND_DOES_NOT_RUN_HOOKS, (
            f'core.hooksPath was overridden, but "git {subcommand}" does not '
            'run hooks locally.'
        )
    else:
        reason, detail = CLEAN, 'no clause bypasses a hook.'

    after_double_dash = False
    i = 0
    n = len(rest_tokens)
    while i < n:
        tok = rest_tokens[i]

        if after_double_dash:
            if tok == NO_VERIFY_FLAG:
                reason, detail = AFTER_DOUBLE_DASH, (
                    f'"{tok}" appears after "--", where git treats it as a '
                    'path, not a flag.'
                )
            i += 1
            continue

        if tok == '--':
            after_double_dash = True
            i += 1
            continue

        if tok == NO_VERIFY_FLAG:
            return Decision(
                BLOCK, LONG_NO_VERIFY,
                f'"{NO_VERIFY_FLAG}" is defined the same way everywhere git '
                'accepts it: skip hooks.',
                clause=clause,
            )

        if tok in VALUE_TAKING_LONG_OPTIONS:
            if i + 1 < n and rest_tokens[i + 1] == NO_VERIFY_FLAG:
                reason, detail = FLAG_IS_AN_OPTION_VALUE, (
                    f'"{rest_tokens[i + 1]}" is the value of "{tok}", not a flag.'
                )
            i += 2
            continue

        if tok.startswith('--'):
            # Unrecognised long option — consumes only itself. Skipping the
            # *next* token here is exactly how a real bypass hides behind an
            # unrelated flag (e.g. "--amend --no-verify").
            i += 1
            continue

        if len(tok) >= 2 and tok[0] == '-':
            blocks, consumes_next, saw_benign_n = _scan_short_cluster(tok, subcommand)
            if blocks:
                return Decision(
                    BLOCK, SHORT_N_NO_VERIFY,
                    f'"-n" means {NO_VERIFY_FLAG!r} for "git {subcommand}" '
                    f'(from "{tok}").',
                    clause=clause,
                )
            if saw_benign_n and reason == CLEAN:
                meaning = SHORT_N_MEANING.get(subcommand, 'a subcommand-specific option')
                reason, detail = N_MEANS_SOMETHING_ELSE, (
                    f'"-n" means {meaning!r} for "git {subcommand}", not '
                    f'"{NO_VERIFY_FLAG}" (from "{tok}").'
                )
            if consumes_next and i + 1 < n and rest_tokens[i + 1] == NO_VERIFY_FLAG:
                reason, detail = FLAG_IS_AN_OPTION_VALUE, (
                    f'"{rest_tokens[i + 1]}" is the value of "{tok}", not a flag.'
                )
            i += 2 if consumes_next else 1
            continue

        i += 1

    return Decision(ALLOW, reason, detail, clause=clause)


def _judge_clause(tokens: list[str]) -> Decision:
    clause_text = ' '.join(tokens)
    n = len(tokens)
    i = _skip_wrapper_prefix(tokens)

    if i >= n or not _is_git_executable(tokens[i]):
        return Decision(ALLOW, NOT_A_GIT_INVOCATION, 'not a git invocation.', clause=clause_text)

    git_argv = tokens[i + 1:]
    subcommand_index, hooks_override = _parse_global_options(git_argv)
    if subcommand_index >= len(git_argv):
        return Decision(
            ALLOW, CLEAN, 'git invocation has no subcommand.', clause=clause_text,
        )

    subcommand = git_argv[subcommand_index]
    rest = git_argv[subcommand_index + 1:]
    return _judge_git_argv(
        subcommand, rest, hooks_path_override=hooks_override, clause=clause_text,
    )


# --------------------------------------------------------------- entry points


def judge_command(command: str | None) -> Decision:
    """Pure entry point: does this Bash command bypass a commit gate?

    Judges every clause of every logical line and blocks on the first one
    that does. When nothing blocks, the returned reason reflects the last
    clause examined — informational only, since the verdict is ALLOW either
    way.
    """
    if command is None or not command.strip():
        return Decision(ALLOW, NO_COMMAND, 'no command text to judge.')

    try:
        segments = _segment(command)
    except _SegmentError as exc:
        return Decision(ALLOW, UNTOKENIZABLE, f'could not segment command: {exc}')

    last: Decision | None = None
    for segment in segments:
        if not segment.strip():
            continue
        try:
            tokens = _tokenize(segment)
        except ValueError as exc:
            return Decision(
                ALLOW, UNTOKENIZABLE, f'could not tokenize a clause: {exc}', clause=segment,
            )
        for clause_tokens in _split_clauses(tokens):
            if not clause_tokens:
                continue
            decision = _judge_clause(clause_tokens)
            if decision.blocked:
                return decision
            last = decision

    if last is None:
        return Decision(ALLOW, NOT_A_GIT_INVOCATION, 'no git invocation found.')
    return last


#: Escape hatch. Loud on purpose, matching the other three gates this layer
#: backs up — an easy but visible bypass beats a hook people delete. Set in
#: the shell environment that runs the ``claude`` process — never honoured
#: as a prefix inside the *judged* command string, since a command that can
#: turn its own gate off is not a gate. This is a deliberate, final decision,
#: not a placeholder: an agent cannot set this itself (it has no channel to
#: the process environment the hook reads), so hitting this block means
#: asking the operator, one of three ways —
#:   1. the operator runs the git command directly in their own terminal;
#:   2. the operator adds ``"env": {"FCC_ALLOW_HOOK_BYPASS": "1"}`` to
#:      ``.claude/settings.local.json`` and restarts the session (env is
#:      read once at hook invocation, not re-read mid-session); or
#:   3. the operator restarts with ``FCC_ALLOW_HOOK_BYPASS=1 claude``.
#: An agent that hits this block must not remove the PreToolUse registration
#: from ``.claude/settings.json`` to get past it — that deletes the gate for
#: every future command, not just this one.
BYPASS_ENV = 'FCC_ALLOW_HOOK_BYPASS'


def check_payload(raw_payload: str) -> tuple[int, str]:
    """Exit code plus the message to print, from a PreToolUse hook payload.

    Fails open wherever it is unsure: not JSON, not an object, not the Bash
    tool, or missing a command all allow silently (or with a soft warning)
    rather than block a tool call this layer cannot make sense of.
    """
    if os.environ.get(BYPASS_ENV, '').strip() not in ('', '0'):
        return 0, f'hook-bypass guard: bypassed via {BYPASS_ENV}.'

    text = raw_payload.strip()
    if not text:
        return 0, ''

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return 0, f'hook-bypass guard: could not parse hook payload ({exc}) — skipped.'

    if not isinstance(payload, dict):
        return 0, 'hook-bypass guard: hook payload was not a JSON object — skipped.'

    tool_name = payload.get('tool_name') or payload.get('tool')
    if tool_name != 'Bash':
        return 0, ''

    tool_input = payload.get('tool_input')
    command = tool_input.get('command') if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        return 0, ''

    decision = judge_command(command)
    if not decision.blocked:
        if decision.reason == UNTOKENIZABLE:
            return 0, (
                f'hook-bypass guard: could not parse this command well enough to '
                f'judge it — allowed without judgement ({decision.detail}).'
            )
        return 0, ''

    return 2, (
        'hook-bypass guard: BLOCKED — this Bash command would skip a commit gate.\n'
        f'  {decision.detail}\n'
        f'  clause: {decision.clause}\n'
        f'  ({GUARDRAIL_LIMITATION})\n'
        f'  This requires the operator — an agent cannot set {BYPASS_ENV} on '
        'itself. Ask the operator to either (1) run the git command directly '
        'in their own terminal, (2) add it to .claude/settings.local.json '
        '"env" and restart the session, or (3) restart with '
        f'{BYPASS_ENV}=1 claude. Do not remove the PreToolUse hook '
        'registration to get past this.'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    raw_payload = sys.stdin.read()
    code, message = check_payload(raw_payload)
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
