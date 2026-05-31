#!/usr/bin/env bash
#
# Parrot smoke test — proves the three-process architecture wires up.
#
# Builds the Svelte frontend, boots the Python sidecar, and exercises the IPC
# contract end to end against the real uvicorn process: liveness, engine status,
# the first-run setup gate, and a full voice-profile CRUD round-trip (which hits
# the SQLite DB + on-disk file writes). A *real* generation needs the ML engine
# extra + downloaded weights (GPU/CPU model), so it is out of this headless
# smoke test — the model boundary is covered by the mocked pytest suite instead.
#
# Usage:  bash scripts/smoke-test.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PARROT_PORT:-3922}"   # off-default so it won't clash with a running dev sidecar
LOG="$(mktemp 2>/dev/null || echo /tmp/parrot_smoke.log)"
DATA="$(mktemp -d 2>/dev/null || echo /tmp/parrot_smoke_data)"

echo "==> Parrot smoke test (sidecar port $PORT, data dir $DATA)"

# 1. Frontend builds (Svelte SPA + Tailwind v4 tokens + Montserrat).
echo "==> Building frontend…"
( cd "$ROOT/frontend" && bun install --silent && bun run build >/dev/null )
echo "    frontend build OK"

# 2. Sidecar venv. --no-dev: the smoke test exercises the runtime app only, and
#    the shipped/first-run venv must not carry pytest/httpx (dev-group) weight.
echo "==> Syncing sidecar venv…"
( cd "$ROOT/sidecar" && uv sync --frozen --no-dev -q )

# 3. Boot the sidecar against a throwaway data dir.
echo "==> Booting sidecar…"
cd "$ROOT/sidecar"
PARROT_PORT="$PORT" PARROT_DATA_DIR="$DATA" uv run python main.py >"$LOG" 2>&1 &
SVPID=$!
cd "$ROOT"
cleanup() {
  taskkill //F //T //PID "$SVPID" >/dev/null 2>&1 || kill "$SVPID" 2>/dev/null || true
  rm -rf "$DATA" 2>/dev/null || true
  rm -f "$ROOT/${REF:-_smoke_ref.wav}" 2>/dev/null || true
}
trap cleanup EXIT

base="http://127.0.0.1:$PORT"

fail() { echo "    FAIL: $1"; echo "--- sidecar log ---"; cat "$LOG"; exit 1; }
assert_contains() { case "$2" in *"$1"*) ;; *) fail "$3 (got: $2)";; esac; }

# 4. Liveness — wait for the server, then assert the contract.
echo -n "==> GET /healthz … "
HEALTH="$(curl -s --retry 30 --retry-connrefused --retry-delay 1 "$base/healthz")"
echo "$HEALTH"
assert_contains '"status":"ok"' "$HEALTH" "unexpected /healthz body"

# 5. Engine status stub (loopback-gated; curl is on 127.0.0.1).
echo -n "==> GET /engine/status … "
ENGINE="$(curl -s "$base/engine/status")"
echo "$ENGINE"
assert_contains '"active":"omnivoice"' "$ENGINE" "unexpected /engine/status body"

# 6. First-run setup gate (fresh data dir → model not downloaded yet).
echo -n "==> GET /setup/status … "
SETUP="$(curl -s "$base/setup/status")"
echo "$SETUP"
assert_contains '"models_ready"' "$SETUP" "unexpected /setup/status body"

# 7. Voice-profile CRUD round-trip (DB + on-disk file writes; no model needed).
#    Upload a tiny file by RELATIVE name from the repo root — native (Windows)
#    curl can't open a Git-Bash POSIX path like /tmp/... (curl error 26).
REF="_smoke_ref.wav"
printf 'RIFFxxxxWAVEfmt smoke-test-reference-bytes' > "$ROOT/$REF"
cd "$ROOT"
echo -n "==> POST /profiles (create) … "
PROF="$(curl -s -F "name=Smoke Test Voice" -F "ref_audio=@$REF;type=audio/wav;filename=ref.wav" "$base/profiles")"
echo "$PROF"
assert_contains '"id"' "$PROF" "profile create failed"
PID="$(printf '%s' "$PROF" | sed -E 's/.*"id"[ :]*"([^"]+)".*/\1/')"

echo -n "==> GET /profiles (list) … "
LIST="$(curl -s "$base/profiles")"
assert_contains "$PID" "$LIST" "created profile not in list"
echo "contains $PID"

echo -n "==> DELETE /profiles/$PID … "
DEL="$(curl -s -X DELETE "$base/profiles/$PID")"
echo "$DEL"
assert_contains '"deleted"' "$DEL" "profile delete failed"

echo -n "==> GET /history (empty) … "
HIST="$(curl -s "$base/history")"
assert_contains '[]' "$HIST" "expected empty history"
echo "[]"

echo "==> SMOKE TEST PASSED — frontend builds; sidecar serves health, engine,"
echo "    setup gate, and a full profile CRUD round-trip over the IPC contract."
