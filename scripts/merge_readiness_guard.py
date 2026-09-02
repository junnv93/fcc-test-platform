"""Refuse a merge that would land a branch cut from a base that has moved on.

## The incident this exists for

On 2026-08-07 pull request #126 was **squash**-merged from a branch whose base
predated pull request #127. A squash replays the branch's entire diff against
*its* base, so replaying it onto today's ``main`` reverted #127 in full — 13
files. Nothing failed. The only visible symptom was a missing self-audit block
in the landed commit, because the squash also replaced the authored body with
the pull request description. Recovery needed a separate restore pull request
(#133); the clobbering commit ``0cf7acee`` is immutable history.

Two conditions multiplied to produce it:

1. the merge method replayed a diff instead of performing a three-way merge, and
2. the branch's base was stale.

``CLAUDE.md``'s No-squash rule addressed condition 1 — in prose. It existed
before the incident and did not prevent it, for the same reason
``scripts/work_claim_branch_guard.py`` was written: **a rule that nothing
enforces is a rule that can be skipped.** This module enforces both conditions.

Condition 2 matters even with a real merge commit. A three-way merge cannot
revert #127, but a branch that is green is green *on the base it was cut from*,
and it lands on ``main`` as it is now. Textual mergeability is not semantic
agreement.

## The judgement

Freshness is decided by exactly one expression, and it is not
``merge-base --is-ancestor``::

    git log <base> --not <head> --no-merges

Zero lines means every non-merge commit reachable from the base ref is also
reachable from the branch: nothing has landed that the branch has not seen.
``--is-ancestor`` answers a different question and disagrees with this one on a
branch that merged the base back in, which is the ordinary way of getting fresh.
``--no-merges`` is what keeps integration merge commits from reading as work the
branch is missing.

## Failure posture

Like its sibling guard, this **fails open and says so**: an unresolved base ref,
an unreachable remote, a detached HEAD, or a git invocation that did not succeed
all allow the operation with a warning on stderr. A hygiene gate that produces
false alarms gets switched off, and a switched-off gate protects nothing. It
blocks only the two shapes it can be certain about — a squash request, and a
base ref carrying non-merge commits the branch does not have.

## The end state this is standing in for

GitHub's own answer to condition 2 is branch protection's *"Require branches to
be up to date before merging"* — the "strict" flavour of required status checks
(<https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches>).
That is server-side and unskippable, and it is where this rule belongs. It is
**not** enabled today because it is a sub-option of *"Require status checks to
pass before merging"*, and this repository's Actions runs are blocked at the
billing layer, so no check can report. Turning it on now would wedge every
merge. This module is the local stand-in until that block clears; see
``.claude/exec-plans/tech-debt-tracker.md``.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import subprocess
import sys
from typing import Sequence

#: Escape hatches, one per axis, because the two conditions are independent and
#: collapsing them would let a squash waiver silently also waive staleness.
#: Loud on purpose: a silent override is how a gate rots.
STALE_OVERRIDE_ENV = 'FCC_ALLOW_STALE_MERGE_BASE'
SQUASH_OVERRIDE_ENV = 'FCC_ALLOW_SQUASH_MERGE'

#: The repository merges into this, and every guidance string below assumes it.
DEFAULT_BASE_REF = 'origin/main'

#: The `pre-push` hook runs this on every push, so the remote round-trip has to
#: be bounded. Without a limit a slow or half-open remote hangs the push behind a
#: check that is only ever going to print a warning — which is how a hygiene gate
#: earns the reputation that gets it deleted. A timeout is not a failure: it
#: lands on the `freshness-unknown` fail-open, loudly.
FETCH_TIMEOUT_SECONDS = 15

#: `gh pr merge` spellings. `merge` is the only one this repository permits;
#: the other two replay a diff rather than recording both parents.
MERGE_METHOD_MERGE = 'merge'
MERGE_METHOD_SQUASH = 'squash'
MERGE_METHOD_REBASE = 'rebase'
DIFF_REPLAYING_METHODS = (MERGE_METHOD_SQUASH, MERGE_METHOD_REBASE)

ALLOW = 'allow'
BLOCK = 'block'

#: The incident, as data rather than as a story. A later session that wonders
#: whether this gate is worth its weight can resolve these objects.
INCIDENT_RECORD: dict[str, str] = {
    'clobbering_squash_commit': '0cf7acee1d211fdabea723d1435e1d91f0513a2c',
    'clobbering_pull_request': '126',
    'clobbered_pull_request': '127',
    'restore_pull_request': '133',
    'date': '2026-08-07',
}


@dataclass(frozen=True)
class Decision:
    verdict: str
    reason: str
    detail: str = ''

    @property
    def blocked(self) -> bool:
        return self.verdict == BLOCK


def evaluate(
    *,
    merge_method: str,
    head_ref: str | None,
    base_ref: str | None,
    base_only_commits: Sequence[str] | None,
    stale_override: bool = False,
    squash_override: bool = False,
) -> Decision:
    """Pure decision. Everything above this is I/O.

    ``head_ref`` is ``None`` for a detached HEAD, ``base_ref`` is ``None`` when
    the base could not be resolved, and ``base_only_commits`` is ``None`` when
    the comparison could not be run at all — which is distinct from the empty
    sequence, meaning it ran and found nothing.
    """
    # The method axis is decided first because it needs no refs at all. A
    # waiver here must NOT return early: the two axes are independent, and the
    # incident was their product, so releasing one while the other still holds
    # would let exactly that product through on a single override.
    method_waived = False
    if merge_method in DIFF_REPLAYING_METHODS:
        if not squash_override:
            return Decision(
                BLOCK,
                'diff-replaying-merge',
                f'Merge method "{merge_method}" replays the branch diff against '
                f'its own base. That is how pull request '
                f'#{INCIDENT_RECORD["clobbering_pull_request"]} reverted '
                f'#{INCIDENT_RECORD["clobbered_pull_request"]} in full.',
            )
        method_waived = True

    if head_ref is None:
        return Decision(
            ALLOW,
            'detached-head',
            'HEAD is detached, so there is no branch whose freshness could be '
            'judged. Rebase and cherry-pick run this way.',
        )

    if not base_ref:
        return Decision(
            ALLOW,
            'base-unresolved',
            f'No base ref resolved (expected something like {DEFAULT_BASE_REF}). '
            'Freshness cannot be judged without one.',
        )

    if base_only_commits is None:
        return Decision(
            ALLOW,
            'freshness-unknown',
            f'Could not compare "{head_ref}" against "{base_ref}" — the remote '
            'may be unreachable or the refs may not both exist locally. '
            'Freshness is unknown, which is not the same as fresh.',
        )

    if not base_only_commits:
        if method_waived:
            return Decision(
                ALLOW,
                'method-override',
                f'{SQUASH_OVERRIDE_ENV} is set — merging with "{merge_method}", '
                f'which replays a diff instead of recording both parents. The '
                f'base is fresh, so the replay reproduces today\'s "{base_ref}".',
            )
        return Decision(
            ALLOW,
            'fresh',
            f'"{base_ref}" holds no work that "{head_ref}" has not already seen.',
        )

    if stale_override:
        return Decision(
            ALLOW,
            'stale-override',
            f'{STALE_OVERRIDE_ENV} is set — merging "{head_ref}" while '
            f'{len(base_only_commits)} commit(s) on "{base_ref}" are missing '
            f'from it.',
        )

    return Decision(
        BLOCK,
        'stale-base',
        f'{len(base_only_commits)} commit(s) on "{base_ref}" are not reachable '
        f'from "{head_ref}".',
    )


# ------------------------------------------------------------------ I/O layer


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ['git', *args],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    )


def _git_out(*args: str) -> str | None:
    result = _git(*args)
    return result.stdout.strip() if result.returncode == 0 else None


def head_branch() -> str | None:
    return _git_out('symbolic-ref', '--quiet', '--short', 'HEAD') or None


def resolve_ref(ref: str) -> str | None:
    """Return ``ref`` when git can resolve it to an object, else ``None``."""
    return ref if _git_out('rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}') else None


def fetch_base(base_ref: str) -> bool:
    """Refresh the base ref. Freshness measured against a stale copy is fiction.

    Returns whether the fetch succeeded; a failure is reported, not fatal, so
    that an offline session still gets a (loudly caveated) answer.
    """
    remote, _, branch = base_ref.partition('/')
    if not branch:
        return False
    try:
        return subprocess.run(
            ['git', 'fetch', '--quiet', remote, branch],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            check=False, timeout=FETCH_TIMEOUT_SECONDS,
        ).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def base_only_commits(base_ref: str, head_ref: str) -> list[str] | None:
    """THE freshness expression. One call site, deliberately.

    Non-merge commits reachable from ``base_ref`` but not from ``head_ref``.
    ``None`` means the comparison could not be made — distinct from ``[]``.
    """
    result = _git(
        'log', base_ref, '--not', head_ref, '--no-merges', '--format=%h %s',
    )
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def pull_request_head(number: str) -> tuple[str | None, str | None]:
    """Fetch a pull request head into a local object; return (ref, branch name)."""
    view = subprocess.run(
        ['gh', 'pr', 'view', number, '--json', 'headRefName,baseRefName'],
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=False,
    )
    branch = None
    if view.returncode == 0:
        import json
        try:
            branch = (json.loads(view.stdout) or {}).get('headRefName')
        except (ValueError, AttributeError):
            branch = None
    if _git('fetch', '--quiet', 'origin', f'refs/pull/{number}/head').returncode != 0:
        return None, branch
    return _git_out('rev-parse', 'FETCH_HEAD'), branch


def decide(args: argparse.Namespace) -> Decision:
    """Gather the facts the pure decision needs, then step aside."""
    if args.pr:
        head, branch = pull_request_head(args.pr)
        head_name = branch or (f'PR #{args.pr}' if head else None)
    else:
        head = resolve_ref(args.head) if args.head else resolve_ref('HEAD')
        head_name = args.head or head_branch()

    base = args.base
    if not args.no_fetch and not fetch_base(base):
        print(
            f'merge guard: could not refresh "{base}" — judging against the '
            f'local copy, which may itself be stale.',
            file=sys.stderr,
        )
    base = resolve_ref(base)

    commits = (
        base_only_commits(base, head) if (base and head) else None
    )
    return evaluate(
        merge_method=args.method,
        head_ref=head_name,
        base_ref=base,
        base_only_commits=commits,
        stale_override=bool(os.environ.get(STALE_OVERRIDE_ENV)),
        squash_override=bool(os.environ.get(SQUASH_OVERRIDE_ENV)),
    )


# --------------------------------------------------------------- subcommands


def _blocked_report(decision: Decision, args: argparse.Namespace) -> str:
    if decision.reason == 'diff-replaying-merge':
        recovery = [
            'Merge with a merge commit instead:',
            '     gh pr merge <number> --merge',
            '',
            'A squash also replaces your commit body with the pull request',
            'description, which drops the 17-item self-audit that lives in it.',
            'If a squash is genuinely unavoidable, put those 17 lines in the',
            'pull request body first.',
            '',
            f'  Emergency override:  {SQUASH_OVERRIDE_ENV}=1 ...',
        ]
    else:
        recovery = [
            'Bring the base in first, in order of preference:',
            f'  1. gh pr update-branch <number>          (merge {args.base} into the PR)',
            f'  2. git merge {args.base}',
            '  3. python3 scripts/merge_readiness_guard.py merge <number> --update',
            '',
            'Then re-run the branch\'s gates. A branch that was green was green',
            'on the base it was cut from, not on the one it is landing on.',
            '',
            f'  Emergency override:  {STALE_OVERRIDE_ENV}=1 ...',
        ]
    return '\n'.join([
        '',
        'BLOCKED — this merge is not ready to land.',
        '',
        f'  {decision.detail}',
        '',
        *recovery,
        '',
    ])


def cmd_check(args: argparse.Namespace) -> int:
    decision = decide(args)
    if not decision.blocked:
        if decision.reason not in ('fresh',):
            print(f'merge guard: {decision.detail}', file=sys.stderr)
        return 0
    print(_blocked_report(decision, args), file=sys.stderr)
    return 1


def cmd_advise(args: argparse.Namespace) -> int:
    """Say the same thing, never block. The `pre-push` hook's entry point.

    Pushing a stale branch is legitimate — that is what a work-in-progress push
    is — so the hook must not refuse it. But a push is the last local moment
    before a server-side merge, which makes it the only place a warning can
    still be acted on cheaply.
    """
    decision = decide(args)
    if decision.blocked:
        print(
            '\n'.join([
                '',
                f'merge guard (advisory): {decision.detail}',
                '',
                f'  Merging as-is is what `check` would refuse. Bring {args.base} in',
                '  before merging, and re-run the branch gates afterwards.',
                '',
            ]),
            file=sys.stderr,
        )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    decision = decide(args)
    print(f'base   : {args.base}')
    print(f'head   : {args.pr and ("PR #" + args.pr) or (args.head or head_branch() or "(detached)")}')
    print(f'method : {args.method}')
    print(f'verdict: {decision.verdict} [{decision.reason}]')
    print(f'         {decision.detail}')
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """Gate, optionally repair, then merge — the whole safe sequence, once.

    This is NOT shorter than `gh pr merge <n> --merge` — nothing invoked through
    a script path could be. What it replaces is the three-step safe sequence
    (judge freshness, bring the base in, merge), which is what the one-line
    unsafe command actually competes with. Collapsing those three into one is
    the whole ergonomic claim; brevity against the unsafe form is not available
    and is not claimed.
    """
    decision = decide(args)
    if decision.blocked and decision.reason == 'stale-base' and args.update:
        print(f'merge guard: {decision.detail} — updating the branch first.', file=sys.stderr)
        updated = subprocess.run(
            ['gh', 'pr', 'update-branch', args.pr], check=False,
        )
        if updated.returncode != 0:
            print('merge guard: `gh pr update-branch` failed; not merging.', file=sys.stderr)
            return 1
        decision = decide(args)
    if decision.blocked:
        print(_blocked_report(decision, args), file=sys.stderr)
        return 1
    if decision.reason != 'fresh':
        print(f'merge guard: {decision.detail}', file=sys.stderr)
    return subprocess.run(
        ['gh', 'pr', 'merge', args.pr, f'--{args.method}'], check=False,
    ).returncode


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--base', default=DEFAULT_BASE_REF, help=f'default {DEFAULT_BASE_REF}')
    parser.add_argument('--head', default=None, help='default: current HEAD')
    parser.add_argument('--pr', default=None, help='judge a pull request head instead of HEAD')
    parser.add_argument(
        '--method', default=MERGE_METHOD_MERGE,
        choices=[MERGE_METHOD_MERGE, MERGE_METHOD_SQUASH, MERGE_METHOD_REBASE],
    )
    parser.add_argument(
        '--no-fetch', action='store_true',
        help='do not refresh the base ref first (offline; answers may be stale)',
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)

    check = sub.add_parser('check', help='non-zero when the merge must not proceed')
    _add_common(check)
    check.set_defaults(func=cmd_check)

    advise = sub.add_parser('advise', help='warn but never block; hook entry point')
    _add_common(advise)
    advise.set_defaults(func=cmd_advise)

    status = sub.add_parser('status', help='show the verdict; always exits 0')
    _add_common(status)
    status.set_defaults(func=cmd_status)

    merge = sub.add_parser('merge', help='gate, then `gh pr merge --merge`')
    _add_common(merge)
    merge.add_argument('pr_positional', metavar='PR', nargs='?')
    merge.add_argument(
        '--update', action='store_true',
        help='when the base has moved, merge it into the branch and re-judge',
    )
    merge.set_defaults(func=cmd_merge)

    args = parser.parse_args(argv)
    if getattr(args, 'pr_positional', None):
        args.pr = args.pr_positional
    if args.func is cmd_merge and not args.pr:
        parser.error('merge needs a pull request number')
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
