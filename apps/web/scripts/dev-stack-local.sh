#!/usr/bin/env bash
# Local dev:stack launcher — loads per-machine env, optionally clears zombie
# listeners on the dev ports (--fresh), then runs `npm run dev:stack`.
#
# Why this exists:
#   * dev-stack.mjs passes process.env straight through to the 3 backends but
#     does NOT auto-load any env file, and crashes the WHOLE stack if any
#     backend's required env (FCC_SESSION_EXCEL_PATH / FCC_HEADLESS_DB_PATH /
#     FCC_CENTRAL_DB_URL) is missing. We source `.env.dev-stack.local`
#     (gitignored) so those values live in one place per developer.
#   * A killed/Ctrl-C'd run can leave the Vite gateway (5173) or a uvicorn
#     backend orphaned, so the next start dies with "Port already in use".
#     `--fresh` reaps those first — mirrors equipment_management_system's
#     `pnpm dev:fresh` intent (PGID-scoped SIGTERM → SIGKILL).
#
# Usage:
#   apps/web/scripts/dev-stack-local.sh            # source env + start
#   apps/web/scripts/dev-stack-local.sh --fresh    # reap dev-port zombies first
#   apps/web/scripts/dev-stack-local.sh --fresh --dry-run --no-start
# Or via npm:  npm run dev:stack:local  /  npm run dev:stack:fresh
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${APP_ROOT}/../.." && pwd)"
ENV_FILE="${APP_ROOT}/.env.dev-stack.local"
CONFIG_FILE="${APP_ROOT}/dev-stack.config.json"

FRESH=0
DRY_RUN=0
START=1
for arg in "$@"; do
  case "$arg" in
    --fresh)   FRESH=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --no-start) START=0 ;;
    *) echo "[dev-stack-local] unknown flag: $arg" >&2; exit 2 ;;
  esac
done

# ── env ──────────────────────────────────────────────────────────────────────
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[dev-stack-local] missing ${ENV_FILE}" >&2
  echo "[dev-stack-local] create it:  cp ${APP_ROOT}/.env.dev-stack.local.example ${ENV_FILE}" >&2
  echo "[dev-stack-local] then set FCC_SESSION_EXCEL_PATH + platform OIDC env (see the template)." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

# ── dev ports (SSOT-derived: backends from dev-stack.config.json + Vite) ──────
# Vite dev port = VITE_DEV_PORT || 5173 (vite.config.ts). No port literals beyond
# that single Vite default, which the config file does not carry.
mapfile -t DEV_PORTS < <(
  node -e '
    const fs = require("node:fs");
    const cfg = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
    const vite = Number(process.env.VITE_DEV_PORT) || 5173;
    const ports = [vite, ...cfg.surfaces.map((s) => s.port)];
    console.log([...new Set(ports)].join("\n"));
  ' "${CONFIG_FILE}"
)

# ── fresh: reap dev-stack processes (PGID-scoped) ─────────────────────────────
# Two passes, because a crashed run can leave a backend that no longer LISTENS on
# its port yet still holds an EXCLUSIVE SQLite lock (e.g. the notification outbox
# journal) — a port-only sweep misses it and the next session boot dies with
# "database is locked". So we also match by command signature, scoped to THIS
# repo so we never touch a developer's unrelated vite/uvicorn.
DEV_PROC_PATTERNS=(
  "uvicorn --factory session_api_app:create_app"
  "uvicorn --factory headless_api_app:create_app"
  "uvicorn --factory platform_api_app:create_app"
  "${APP_ROOT}/scripts/dev-stack.mjs"
  "${APP_ROOT}/node_modules/.bin/vite"
)
reap_dev_ports() {
  local pids=() pgids=() port pid pgid pat
  # Pass 1: port listeners.
  for port in "${DEV_PORTS[@]}"; do
    while read -r pid; do
      [[ -n "$pid" ]] && pids+=("$pid")
    done < <(ss -ltnHp "sport = :${port}" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u)
  done
  # Pass 2: repo-scoped command signatures (catches lock-holding orphans).
  for pat in "${DEV_PROC_PATTERNS[@]}"; do
    while read -r pid; do
      [[ -n "$pid" ]] && pids+=("$pid")
    done < <(pgrep -f -- "$pat" 2>/dev/null || true)
  done
  if [[ ${#pids[@]} -eq 0 ]]; then
    echo "[dev-stack-local] fresh: no dev-stack processes/listeners — already clean."
    return 0
  fi
  # Resolve PGIDs so we kill whole process trees, not just the listener leaf.
  for pid in $(printf '%s\n' "${pids[@]}" | sort -u); do
    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')"
    [[ -n "$pgid" ]] && pgids+=("$pgid")
  done
  mapfile -t pgids < <(printf '%s\n' "${pgids[@]}" | sort -u)
  echo "[dev-stack-local] fresh: reaping PGIDs ${pgids[*]} (ports: ${DEV_PORTS[*]})"
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "[dev-stack-local] [dry-run] would SIGTERM→SIGKILL the above."
    return 0
  fi
  for pgid in "${pgids[@]}"; do kill -TERM -- "-${pgid}" 2>/dev/null || true; done
  sleep 3
  for pgid in "${pgids[@]}"; do kill -KILL -- "-${pgid}" 2>/dev/null || true; done
  sleep 1
}

if [[ ${FRESH} -eq 1 ]]; then
  reap_dev_ports
else
  # Not fresh: fail fast with a clear hint instead of a cryptic EADDRINUSE.
  busy=()
  for port in "${DEV_PORTS[@]}"; do
    if ss -ltnH "sport = :${port}" 2>/dev/null | grep -q .; then busy+=("$port"); fi
  done
  if [[ ${#busy[@]} -gt 0 ]]; then
    echo "[dev-stack-local] ports already in use: ${busy[*]}" >&2
    echo "[dev-stack-local] run with --fresh (or: npm run dev:stack:fresh) to reap them." >&2
    exit 1
  fi
fi

# ── pre-flight: the two most common boot blockers on a fresh WSL clone ────────
if [[ ! -x "${REPO_ROOT}/fcc_test_env/bin/python" && -z "${FCC_PYTHON:-}" ]]; then
  echo "[dev-stack-local] WARN: no fcc_test_env/bin/python and FCC_PYTHON unset — backends will fail to spawn." >&2
fi
if [[ -n "${FCC_SESSION_EXCEL_PATH:-}" && ! -f "${FCC_SESSION_EXCEL_PATH}" ]]; then
  echo "[dev-stack-local] WARN: FCC_SESSION_EXCEL_PATH not found: ${FCC_SESSION_EXCEL_PATH}" >&2
fi
mkdir -p "$(dirname "${FCC_HEADLESS_DB_PATH:-${REPO_ROOT}/.dev/headless.db}")"

# ── session node runtime dir ──────────────────────────────────────────────────
# SessionApiConfig.resolved_runtime_dir falls back to Path.cwd(), and its own
# docstring says the operator env template is meant to supply "an explicit
# machine-local directory outside the immutable package". Neither this launcher
# nor the template did, so a node booted from the repository root wrote
# uploads/workbooks/<sha256>.xlsx INTO THE SOURCE TREE — and the leftovers make
# two repository-hygiene tests fail for a reason that has nothing to do with the
# change under test.
#
# Derived, not required: a dev launcher that refuses to start over a path
# preference gets switched off, and a switched-off launcher enforces nothing.
# `.dev/` is already this checkout's gitignored state directory (the headless DB
# and the seed manifest live there), so nothing new is invented here.
#
# ⚠️ The emptiness test strips whitespace. `[[ -z "  " ]]` is false, so a value
# of spaces would skip the derivation and then `mkdir -p "  "` would create a
# directory literally named "  " in the launcher's working directory — i.e. the
# source tree, which is the pollution this block exists to prevent. That is not
# hypothetical: it happened during review of this very change.
if [[ -z "${FCC_SESSION_RUNTIME_DIR:-}" || -z "${FCC_SESSION_RUNTIME_DIR// /}" ]]; then
  export FCC_SESSION_RUNTIME_DIR="${REPO_ROOT}/.dev/session-runtime"
  echo "[dev-stack-local] FCC_SESSION_RUNTIME_DIR unset — using ${FCC_SESSION_RUNTIME_DIR}"
  echo "[dev-stack-local] (set it in .env.dev-stack.local to choose your own)"
fi
mkdir -p "${FCC_SESSION_RUNTIME_DIR}"

if [[ ${START} -eq 0 ]]; then
  echo "[dev-stack-local] --no-start: cleanup/preflight only."
  exit 0
fi

cd "${APP_ROOT}"
exec npm run dev:stack
