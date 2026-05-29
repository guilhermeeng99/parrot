#!/usr/bin/env bash
#
# Parrot smoke test — proves the three-process architecture wires up.
#
# Phase 1 scope (no ML yet): build the Svelte frontend, boot the Python sidecar,
# and assert the IPC contract (/healthz + /engine/status). The full GUI path
# (`bun run tauri dev` opening a window that reads the sidecar) needs a real
# desktop session and is not exercised here.
#
# Usage:  bash scripts/smoke-test.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PARROT_PORT:-3922}"   # off-default so it won't clash with a running dev sidecar
LOG="$(mktemp 2>/dev/null || echo /tmp/parrot_smoke.log)"

echo "==> Parrot smoke test (sidecar port $PORT)"

# 1. Frontend builds (Svelte SPA + Tailwind v4 tokens + Montserrat).
echo "==> Building frontend…"
( cd "$ROOT/frontend" && bun install --silent && bun run build >/dev/null )
echo "    frontend build OK"

# 2. Sidecar venv.
echo "==> Syncing sidecar venv…"
( cd "$ROOT/sidecar" && uv sync -q )

# 3. Boot the sidecar.
echo "==> Booting sidecar…"
cd "$ROOT/sidecar"
PARROT_PORT="$PORT" uv run python main.py >"$LOG" 2>&1 &
SVPID=$!
cd "$ROOT"
cleanup() {
  taskkill //F //T //PID "$SVPID" >/dev/null 2>&1 || kill "$SVPID" 2>/dev/null || true
}
trap cleanup EXIT

# 4. Liveness — wait for the server, then assert the contract.
echo -n "==> GET /healthz … "
HEALTH="$(curl -s --retry 30 --retry-connrefused --retry-delay 1 "http://127.0.0.1:$PORT/healthz")"
echo "$HEALTH"
case "$HEALTH" in
  *'"status":"ok"'*) ;;
  *) echo "    FAIL: unexpected /healthz body"; echo "--- sidecar log ---"; cat "$LOG"; exit 1 ;;
esac

# 5. Engine status stub.
echo -n "==> GET /engine/status … "
ENGINE="$(curl -s "http://127.0.0.1:$PORT/engine/status")"
echo "$ENGINE"
case "$ENGINE" in
  *'"active":"omnivoice"'*) ;;
  *) echo "    FAIL: unexpected /engine/status body"; exit 1 ;;
esac

echo "==> SMOKE TEST PASSED — frontend builds, sidecar serves the IPC contract."
