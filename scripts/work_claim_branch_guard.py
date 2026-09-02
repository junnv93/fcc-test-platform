"""Refuse a commit that lands on a branch the session did not claim.

Git's "current branch" belongs to the *working directory*, not to the process.
When two sessions share one checkout, either one's ``git switch`` silently
moves the other's HEAD, and the next ``git commit`` lands wherever HEAD
happens to point. On 2026-08-06 that produced two commits on another session's
branch, interleaved with that session's own commit, so extracting them would
have required rewriting somebody else's published history.

``.claude/rules/supervisor-workflow.md`` already mandates one worktree per
task. That rule was written before the incident and did not prevent it, because
a rule that nothing enforces is a rule that can be skipped. This is the gate.

## How it works

A session *binds* its worktree to its work-claim slug::

    python scripts/work_claim_branch_guard.py bind <slug>

The binding is stored in the worktree's own git directory
(``git rev-parse --git-path``), which is per-worktree state — exactly the
granularity the failure has. The ``pre-commit`` hook then refuses any commit
whose HEAD branch differs from the ``branch`` the bound claim declares.

## Failure posture

This is a hygiene guard, not a security control, so it **fails open** and says
so loudly: a missing interpreter, an unreadable claim, or a detached HEAD
allows the commit with a warning on stderr. Blocking every commit in the
repository because a JSON file is malformed would be a worse outcome than the
defect it prevents. The one case it blocks is the one it can be certain about:
a binding exists, the claim names a branch, and HEAD is a different branch.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys

#: Per-worktree binding file, resolved through ``git rev-parse --git-path`` so
#: linked worktrees each get their own rather than sharing the primary's.
BINDING_GIT_PATH = 'fcc-work-claim'

CLAIMS_DIR = Path('.claude') / 'work-claims'

#: Escape hatch. Loud on purpose: a silent override is how a gate rots.
OVERRIDE_ENV = 'FCC_ALLOW_BRANCH_MISMATCH'

ALLOW = 'allow'
BLOCK = 'block'


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
    binding: str | None,
    head_branch: str | None,
    claims: dict[str, dict],
    override: bool = False,
) -> Decision:
    """Pure decision. Everything above this is I/O.

    ``head_branch`` is ``None`` for a detached HEAD.
    ``claims`` maps slug to the parsed claim document.
    """
    if head_branch is None:
        return Decision(
            ALLOW,
            'detached-head',
            'HEAD is detached — rebase and cherry-pick run this way, and '
            'blocking them would break the very recovery this guard exists to '
            'make unnecessary.',
        )
    if not binding:
        return Decision(
            ALLOW,
            'unbound',
            'This worktree is not bound to a work claim. Bind it with '
            '`python scripts/work_claim_branch_guard.py bind <slug>` so a '
            'commit cannot land on another session\'s branch unnoticed.',
        )

    claim = claims.get(binding)
    if claim is None:
        return Decision(
            ALLOW,
            'stale-binding',
            f'Bound to "{binding}" but no such claim exists. If the task is '
            'finished, run `work_claim_branch_guard.py unbind`.',
        )

    declared = (claim.get('branch') or '').strip()
    if not declared:
        return Decision(
            ALLOW,
            'claim-declares-no-branch',
            f'Claim "{binding}" has no `branch` field, so there is nothing to '
            'check. Add one to make this worktree enforceable.',
        )

    if declared == head_branch:
        return Decision(ALLOW, 'match', f'HEAD is on the claimed branch "{declared}".')

    if override:
        return Decision(
            ALLOW,
            'override',
            f'{OVERRIDE_ENV} is set — committing to "{head_branch}" while '
            f'claim "{binding}" declares "{declared}".',
        )

    return Decision(
        BLOCK,
        'branch-mismatch',
        f'HEAD is on "{head_branch}" but claim "{binding}" declares '
        f'"{declared}".',
    )


# ------------------------------------------------------------------ I/O layer


def _git(*args: str) -> str:
    result = subprocess.run(
        ['git', *args], capture_output=True, text=True, encoding='utf-8', check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ''


def repo_root() -> Path:
    top = _git('rev-parse', '--show-toplevel')
    return Path(top) if top else Path.cwd()


def binding_path() -> Path:
    rel = _git('rev-parse', '--git-path', BINDING_GIT_PATH)
    if not rel:
        return repo_root() / '.git' / BINDING_GIT_PATH
    path = Path(rel)
    return path if path.is_absolute() else repo_root() / path


def read_binding() -> str | None:
    path = binding_path()
    if not path.is_file():
        return None
    return path.read_text(encoding='utf-8').strip() or None


def head_branch() -> str | None:
    branch = _git('symbolic-ref', '--quiet', '--short', 'HEAD')
    return branch or None


def load_claims(root: Path | None = None) -> dict[str, dict]:
    base = (root or repo_root()) / CLAIMS_DIR
    claims: dict[str, dict] = {}
    if not base.is_dir():
        return claims
    for path in sorted(base.glob('*.json')):
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(document, dict):
            claims[document.get('slug') or path.stem] = document
    return claims


# --------------------------------------------------------------- subcommands


def cmd_bind(args: argparse.Namespace) -> int:
    claims = load_claims()
    if args.slug not in claims and not args.force:
        print(
            f'no work claim named "{args.slug}" under {CLAIMS_DIR}/ — create it '
            'first, or pass --force if it is intentionally absent',
            file=sys.stderr,
        )
        return 1
    path = binding_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.slug + '\n', encoding='utf-8')
    print(f'bound this worktree to work claim "{args.slug}" ({path})')
    return 0


def cmd_unbind(_: argparse.Namespace) -> int:
    path = binding_path()
    if path.is_file():
        path.unlink()
        print(f'removed {path}')
    else:
        print('this worktree was not bound')
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    decision = evaluate(
        binding=read_binding(),
        head_branch=head_branch(),
        claims=load_claims(),
        override=bool(os.environ.get(OVERRIDE_ENV)),
    )
    print(f'branch : {head_branch() or "(detached)"}')
    print(f'claim  : {read_binding() or "(unbound)"}')
    print(f'verdict: {decision.verdict} [{decision.reason}]')
    print(f'         {decision.detail}')
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    decision = evaluate(
        binding=read_binding(),
        head_branch=head_branch(),
        claims=load_claims(),
        override=bool(os.environ.get(OVERRIDE_ENV)),
    )
    if not decision.blocked:
        if decision.reason not in ('match',):
            print(f'work-claim guard: {decision.detail}', file=sys.stderr)
        return 0
    print(
        '\n'.join([
            '',
            'BLOCKED — this commit would land on a branch you did not claim.',
            '',
            f'  {decision.detail}',
            '',
            'Git\'s current branch belongs to the working directory, not to your',
            'session: another session sharing this checkout can move HEAD under',
            'you. That is how two commits once landed on another session\'s branch,',
            'interleaved with theirs.',
            '',
            'Fix one of these, in order of preference:',
            '  1. Work in your own worktree:',
            '       git worktree add <path> <your-branch>',
            '  2. Switch back:  git switch <your-branch>',
            '  3. Re-bind if the claim genuinely changed:',
            '       python scripts/work_claim_branch_guard.py bind <slug>',
            '',
            f'  Emergency override:  {OVERRIDE_ENV}=1 git commit ...',
            '',
        ]),
        file=sys.stderr,
    )
    return 1


def cmd_install(_: argparse.Namespace) -> int:
    hooks_dir = 'scripts/githooks'
    subprocess.run(['git', 'config', 'core.hooksPath', hooks_dir], check=True)
    hook = repo_root() / hooks_dir / 'pre-commit'
    if hook.is_file():
        hook.chmod(hook.stat().st_mode | 0o111)
    print(f'core.hooksPath = {hooks_dir} (applies to every worktree of this repo)')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)

    bind = sub.add_parser('bind', help='bind this worktree to a work-claim slug')
    bind.add_argument('slug')
    bind.add_argument('--force', action='store_true')
    bind.set_defaults(func=cmd_bind)

    sub.add_parser('unbind', help='remove this worktree\'s binding').set_defaults(func=cmd_unbind)
    sub.add_parser('status', help='show the current verdict').set_defaults(func=cmd_status)
    sub.add_parser('check', help='hook entry point; non-zero blocks the commit').set_defaults(func=cmd_check)
    sub.add_parser('install', help='point core.hooksPath at the tracked hooks').set_defaults(func=cmd_install)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
