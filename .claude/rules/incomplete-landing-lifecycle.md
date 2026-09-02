---
paths:
  - ".claude/**/*"
---

# Incomplete landing lifecycle

This policy separates an independently reviewed FAIL handoff from release
readiness. A failed review may be integrated only as
`INTEGRATED_NOT_RELEASE_READY` when the safe continuation conditions below
hold. Integration never changes the review verdict to PASS or authorizes a
release.

`FAIL` is the independent review verdict. `INTEGRATED_NOT_RELEASE_READY` is
the auditable lifecycle state for an exact FAIL SHA integrated into a named,
non-production continuation branch. `RELEASE_READY` is a separate state that
requires every MUST to pass at one immutable final SHA and a fresh,
read-only Sol review to PASS after at least 1,800 seconds of reviewer
availability.

## State machine

| Review/lifecycle state | Continuation integration | `main`/production merge | Plan/archive and claim close | `production_cutover` |
|---|---|---|---|---|
| `FAIL` | blocked unless the safe continuation checklist is satisfied | blocked | blocked | `NOT_READY` |
| `INTEGRATED_NOT_RELEASE_READY` | continuation repairs only | blocked | blocked; plan remains `active`, claim remains `review` | `NOT_READY` |
| `RELEASE_READY` | no longer an incomplete handoff | separately gated by the release workflow | allowed only after the release gate | still requires production gates |

`INTEGRATED_NOT_RELEASE_READY` and `RELEASE_READY` are distinct and must not
be treated as aliases. `INTEGRATED_NOT_RELEASE_READY` is not a PASS,
approval, archive/completed move, claim closure, main/production merge, or
release authorization.

## Safe continuation integration

An exact reviewer-FAIL SHA may be integrated only into a named
non-production continuation branch after all of these are recorded in the
continuation plan, claim, and integration commit:

1. The exact candidate SHA, evaluation path, failed MUST identifiers, and
   exact reproduction commands.
2. Python import/compile and the applicable language build/typecheck pass.
3. The failure is not a security or authorization bypass, data loss,
   destructive migration, false-PASS, protected-domain breach, or compile
   failure.
4. The merge-readiness guard passes for a current-base, non-squash,
   non-rebase, three-way merge.
5. Existing dirty files, reviewer artifacts, claims, and other worktrees are
   preserved; the explicit path manifest contains only owned paths.
6. The first repair action is recorded and the plan and claim remain active.

The handoff must retain the FAIL verdict, including any already passing and
failing MUSTs. A continuation integration does not erase R4 PASS or turn the
R7/R8 FAIL handoff into PASS.

## Release boundary

Until every contract MUST passes at the same immutable final SHA **and** a
fresh independent, read-only Sol review maps every MUST to evidence, returns
PASS, and satisfies the minimum 1,800-second reviewer wait, all of the
following are blocked:

- merge to `main` or production;
- moving the active plan to `completed/` or `archive/`;
- closing the claim or changing it from its active `review` handoff;
- declaring `RELEASE_READY`; and
- changing the literal `production_cutover=NOT_READY`.

No skipped live lane, prior review, later-SHA result, or generic handoff can
satisfy this release boundary.
