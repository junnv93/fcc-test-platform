#!/usr/bin/env bash
# Provision a frozen, read-only review copy in which the browser lanes ACTUALLY RUN.
#
# Usage:
#   bash scripts/provision_review_worktree.sh <destination-dir> [<sha-or-ref>]
#
# ⚠️ **Why this exists, measured rather than assumed.** Five consecutive independent
# reviewers of one wave each wrote some version of:
#
#     "not verified: the frozen copy has no apps/web/node_modules, so the
#      browser / Vitest / lint / build lanes cannot run here"
#
# and each time the frontend half of the contract was judged from committed
# receipts instead of from execution. The wall was never structural. `git worktree
# add --detach` simply does not carry untracked, gitignored artifacts:
# `apps/web/node_modules`, the codegen output under `apps/web/src/api/generated/`,
# and `apps/web/dist`. All three are reproducible in about ten seconds.
#
# Measured on 2026-08-27 in a copy provisioned by this script:
#   node_modules hardlink copy   1.1 s      (402 MB, ~0 bytes of new disk)
#   npm run codegen              ~2 s       (3 generated .ts modules)
#   npm run build                4.2 s
#   playwright tests/e2e/a11y.spec.ts   40 passed in 14.7 s
#
# ⚠️ **Hardlinked, not copied, and that is a decision with a failure mode.**
# `cp -al` costs no disk and no time, but a hardlink is the SAME inode: a tool
# that rewrites a file in place would corrupt the source worktree's copy too.
# `node_modules` is treated as immutable by npm's own design (packages are
# replaced, not edited), and the two directories that are NOT — `.vite` and
# `.cache` — are removed after the copy so they are regenerated locally. Do not
# run `npm install` inside a copy provisioned this way.
#
# The copy is detached and must stay read-only. Nothing here writes to the repo.

set -euo pipefail

PRINT_STEPS=0
if [ "${1:-}" = "--print-steps" ]; then
  PRINT_STEPS=1
  shift
fi

DEST=${1:-}
REF=${2:-HEAD}

if [ "$PRINT_STEPS" = 1 ]; then
  # `--print-steps` exists so a seal can assert what this script ACTUALLY runs.
  # ⚠️ The first version of that seal grepped this file for `npm run codegen`,
  # and deleting the invocation left the phrase in an `echo` — so the mutation
  # survived. Steps are now emitted from the same `_step` call sites that execute
  # them: removing a call removes a token, and there is no way to have one
  # without the other.
  DEST=${DEST:-/dev/null}
fi

if [ -z "$DEST" ]; then
  echo "usage: bash scripts/provision_review_worktree.sh [--print-steps] <destination-dir> [<sha-or-ref>]" >&2
  exit 2
fi

#: Run one provisioning step, or name it under --print-steps. The token is the
#: contract; the command after it is the implementation.
_step() {
  local _token=$1
  shift
  if [ "$PRINT_STEPS" = 1 ]; then
    printf 'step: %s\n' "$_token"
    return 0
  fi
  "$@"
}

repo_root=$(git rev-parse --show-toplevel)
sha=$(git -C "$repo_root" rev-parse "$REF")
web_src="$repo_root/apps/web"

if [ "$PRINT_STEPS" = 0 ] && [ -e "$DEST" ]; then
  echo "provision: $DEST already exists — refusing to overwrite a review copy." >&2
  echo "  Remove it with: git worktree remove --force '$DEST'" >&2
  exit 2
fi

echo "provision: freezing $sha into $DEST"
_step "worktree add --detach" git -C "$repo_root" worktree add --detach "$DEST" "$sha"

# ── The frontend lanes, in dependency order ───────────────────────────────────
# Absent node_modules, every one of typecheck / lint / test / build / playwright
# fails at module resolution, which reads like "the lane is unrunnable here"
# rather than "a directory is missing".
if [ "$PRINT_STEPS" = 1 ] || [ -d "$web_src/node_modules" ]; then
  echo "provision: hardlinking node_modules (no disk cost, same inodes)"
  _step node_modules cp -al "$web_src/node_modules" "$DEST/apps/web/node_modules"
  _step "strip .vite/.cache" rm -rf "$DEST/apps/web/node_modules/.vite" "$DEST/apps/web/node_modules/.cache"
else
  echo "provision: WARNING — $web_src/node_modules is absent in the source worktree." >&2
  echo "  Run 'npm ci' there first, or the review copy will hit the same wall." >&2
fi

# ⚠️ The box default Node is routinely not the pinned one, and the lanes fail
# under the wrong major in ways that look like code defects.
#
# ⚠️ The question here is NOT "is fnm installed?". That is a *proxy*, and it is
# wrong on any box carrying a different version manager -- observed 2026-08-30 on
# a box with nvm and no fnm, where the old code warned once to stderr and then ran
# every lane under Node 24 while the operator believed the pin was honoured. The
# question actually asked below is "will the lanes run under a Node that satisfies
# engines?", and it is answered by *observation*: each candidate runner is probed
# with the module that already owns the derivation, scripts/frontend_build_gate.py.
# The version literal lives in apps/web/package.json and nowhere else.
node_runner=(env)
node_runner_label='the box default Node'
# The prefix the reviewer will actually copy. It is DERIVED from the runner that
# was selected -- printing a fixed 'fnm exec --using=22' here is the same defect
# as selecting on a proxy, one step further downstream and far more visible: it
# is the text a reviewer pastes, so a stale prefix sends every lane to the wrong
# runtime even when selection got it right.
lane_prefix=''

if [ "$PRINT_STEPS" = 1 ]; then
  # A dry run selects nothing, so it must not print a prefix that looks selected.
  node_runner_label='<the runtime chosen at provision time>'
  _step "select a Node runtime satisfying engines" true
else
  gate_py=''
  required_major=''
  # Prefer the reviewed tree's own gate: it answers for the engines *that tree*
  # declares. Fall back to this checkout's copy when the reviewed sha predates
  # these flags, so provisioning an older sha degrades instead of refusing.
  for _gate in "$DEST/scripts/frontend_build_gate.py" \
               "$repo_root/scripts/frontend_build_gate.py"; do
    [ -f "$_gate" ] || continue
    if required_major=$(python3 "$_gate" --print-node-major 2>/dev/null) \
       && [ -n "$required_major" ]; then
      gate_py="$_gate"
      break
    fi
    required_major=''
  done

  if [ -z "$gate_py" ]; then
    # "cannot ask" and "asked, and the answer is no" are different states, and
    # this repository fail-opens only on the first one.
    echo "provision: WARNING — could not derive engines.node (no usable python3 or gate)." >&2
    echo "  Frontend lanes will run under $node_runner_label, unverified." >&2
  elif node_bin=$(python3 "$gate_py" --print-satisfying-node-bin 2>/dev/null) \
       && [ -n "$node_bin" ]; then
    # Selection is NOT reimplemented here.  The module that owns the engines
    # derivation also owns the search across fnm / nvm / PATH and verifies each
    # candidate by running it; this script only consumes the answer.  A second
    # search written in shell would be a second policy, and the two would agree
    # until the day a box carries a manager only one of them knows about.
    node_runner=(env "PATH=$node_bin:$PATH")
    node_runner_label="Node $required_major from $node_bin"
    lane_prefix="PATH=$node_bin:\$PATH "
    _step "node runtime: $node_runner_label" true
    echo "provision: frontend lanes will run under $node_runner_label"
  else
    echo "provision: REFUSING — no available Node satisfies apps/web engines.node." >&2
    python3 "$gate_py" --print-satisfying-node-bin >&2 || true
    echo "  Install Node $required_major (nvm install $required_major / fnm install $required_major) and rerun." >&2
    echo "  This refuses rather than warns on purpose: lanes under the wrong major" >&2
    echo "  fail like code defects, which is how five consecutive reviewers lost the" >&2
    echo "  frontend half of a contract before this script existed." >&2
    exit 1
  fi
fi

if [ "$PRINT_STEPS" = 1 ] || [ -d "$DEST/apps/web/node_modules" ]; then
  # Generated API types are gitignored, so a fresh worktree has none and every
  # `@/api/generated/*` import fails to resolve — which presents as ~40 unrelated
  # TypeScript errors rather than as one missing step.
  echo "provision: npm run codegen"
  _step "npm run codegen" env -C "$DEST/apps/web" "${node_runner[@]}" npm run codegen
  # `vite preview` serves dist/, and Playwright's webServer waits on it — without
  # a build the browser lane dies as a 60 s webServer timeout, which is the least
  # informative possible symptom.
  echo "provision: npm run build"
  _step "npm run build" env -C "$DEST/apps/web" "${node_runner[@]}" npm run build
fi

cat <<EOF

provision: ready.

  frozen SHA : $sha
  location   : $DEST

Python lanes (from $DEST):
  python3 -m pytest tests/ -q -m "invariant and not hardware and not gui and not bench"
  python3 scripts/supervisor_preflight.py .claude/work-claims/<slug>.json

Frontend lanes (from $DEST/apps/web) — run under $node_runner_label:
  ${lane_prefix}npm run typecheck
  ${lane_prefix}npm run lint
  ${lane_prefix}npm run test
  ${lane_prefix}npx playwright test tests/e2e/a11y.spec.ts --reporter=line
  ${lane_prefix}npm run test:e2e:visual

⚠️ Never run 'npm run test:e2e:visual:update' in a review copy. Re-baselining a
   snapshot hides the differential the review exists to judge.
⚠️ Treat the copy as read-only. Write findings outside the tree — appending to
   .claude/ while another worktree runs a lane makes that lane report regressions
   that do not exist.

Tear down with:
  git worktree remove --force $DEST
EOF
