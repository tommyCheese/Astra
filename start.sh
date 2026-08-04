#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"

log() {
  printf '[Astra] %s\n' "$1"
}

fail() {
  printf '[Astra] Error: %s\n' "$1" >&2
  exit 1
}

command -v python3 >/dev/null 2>&1 || fail "python3 is required (Python 3.10+)."
command -v npm >/dev/null 2>&1 || fail "npm is required. Install Node.js and try again."

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
  || fail "Python 3.10+ is required."

if [[ ! -x "$PYTHON_BIN" ]]; then
  log "Creating the backend virtual environment..."
  python3 -m venv "$VENV_DIR"
fi

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
  || fail "The backend virtual environment must use Python 3.10+. Remove backend/.venv and retry."

if ! "$PYTHON_BIN" -c 'import alembic, fastapi, uvicorn' >/dev/null 2>&1; then
  log "Installing backend dependencies..."
  "$PYTHON_BIN" -m pip install -e "$BACKEND_DIR"
fi

if [[ ! -x "$FRONTEND_DIR/node_modules/.bin/vite" ]]; then
  log "Installing frontend dependencies..."
  (cd "$FRONTEND_DIR" && npm ci)
fi

if [[ ! -f "$BACKEND_DIR/.env" ]]; then
  log "Creating backend/.env with the local mock model..."
  printf '%s\n' \
    'DATABASE_URL=sqlite+aiosqlite:///./astra-dev.db' \
    'MODEL_PROVIDER=mock' \
    'MODEL_NAME=mock' >"$BACKEND_DIR/.env"
fi

log "Applying database migrations..."
(cd "$BACKEND_DIR" && "$PYTHON_BIN" -m alembic upgrade head)

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  trap - EXIT INT TERM
  log "Stopping services..."
  [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  [[ -n "$BACKEND_PID" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  [[ -n "$FRONTEND_PID" ]] && wait "$FRONTEND_PID" 2>/dev/null || true
  [[ -n "$BACKEND_PID" ]] && wait "$BACKEND_PID" 2>/dev/null || true
}

trap cleanup EXIT
trap 'exit 130' INT TERM

log "Starting backend at http://127.0.0.1:8000 ..."
(
  cd "$BACKEND_DIR"
  exec "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
) &
BACKEND_PID=$!

log "Starting frontend at http://127.0.0.1:5173 ..."
(
  cd "$FRONTEND_DIR"
  exec "$FRONTEND_DIR/node_modules/.bin/vite" --host 127.0.0.1 --port 5173 --strictPort
) &
FRONTEND_PID=$!

log "Astra is running. Press Ctrl+C to stop both services."

job_is_running() {
  jobs -pr | grep -qx "$1"
}

set +e
while job_is_running "$BACKEND_PID" && job_is_running "$FRONTEND_PID"; do
  sleep 0.5
done

EXIT_CODE=0
if ! job_is_running "$BACKEND_PID"; then
  wait "$BACKEND_PID" || EXIT_CODE=$?
else
  wait "$FRONTEND_PID" || EXIT_CODE=$?
fi
set -e

if [[ $EXIT_CODE -ne 0 ]]; then
  log "A service exited with status $EXIT_CODE."
fi
exit "$EXIT_CODE"
