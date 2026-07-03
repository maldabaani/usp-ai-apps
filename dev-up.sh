#!/usr/bin/env bash
# Starts StoryForge's backend, CodeMind, and the Angular shell for local dev,
# all sharing one JWT_SECRET -- without this, each backend that isn't given an
# explicit JWT_SECRET falls back to its own value (StoryForge auto-generates
# and persists one under usp-ai-ba/backend/jobs/.jwt_secret; CodeMind has no
# such fallback and just refuses every request), so tokens issued by one
# never verify against the other. Re-running this script reuses the same
# secret (persisted to .dev-jwt-secret, gitignored) so existing browser
# sessions/logins survive a restart.
#
# Usage: ./dev-up.sh
# Stop everything with Ctrl+C.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

SECRET_FILE="$ROOT_DIR/.dev-jwt-secret"
LOG_DIR="$ROOT_DIR/.dev-logs"
mkdir -p "$LOG_DIR"

if [ ! -f "$SECRET_FILE" ]; then
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32 > "$SECRET_FILE"
  else
    # Fallback for machines without openssl on PATH.
    python3 -c "import secrets; print(secrets.token_hex(32))" > "$SECRET_FILE"
  fi
  echo "Generated a new shared JWT_SECRET at $SECRET_FILE"
fi
export JWT_SECRET
JWT_SECRET="$(cat "$SECRET_FILE")"

BACKEND_DIR="$ROOT_DIR/usp-ai-ba/backend"
FRONTEND_DIR="$ROOT_DIR/usp-ai-ba/frontend/storyforge-ui"
CODEMIND_DIR="$ROOT_DIR/code-mind-app"

if [ ! -d "$BACKEND_DIR/venv" ]; then
  echo "Missing $BACKEND_DIR/venv -- set it up first (see RUNNING.md's backend section: python3 -m venv venv && pip install -r requirements.txt)." >&2
  exit 1
fi
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "Missing $FRONTEND_DIR/node_modules -- run 'npm install' there first (see RUNNING.md)." >&2
  exit 1
fi

PIDS=()
CLEANED_UP=0

cleanup() {
  if [ "$CLEANED_UP" -eq 1 ]; then
    return
  fi
  CLEANED_UP=1
  echo ""
  echo "Stopping backend + CodeMind..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  # `./mvnw spring-boot:run` forks a separate child JVM to actually run the
  # app -- killing the mvnw wrapper process above doesn't reliably take that
  # child down with it, which would otherwise leave a CodeMind process
  # holding :8085 after Ctrl+C. Belt-and-suspenders: also kill by the app's
  # own main class, scoped tightly enough not to catch anything unrelated.
  pkill -f "com.jslogicextractor.JsLogicExtractorApplication" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting StoryForge backend on :8000 (log: $LOG_DIR/backend.log)..."
(
  cd "$BACKEND_DIR"
  # shellcheck disable=SC1091
  source venv/bin/activate
  JWT_SECRET="$JWT_SECRET" exec uvicorn api.main:app --reload --port 8000
) > "$LOG_DIR/backend.log" 2>&1 &
PIDS+=($!)

echo "Starting CodeMind on :8085 (log: $LOG_DIR/codemind.log)..."
(
  cd "$CODEMIND_DIR"
  JWT_SECRET="$JWT_SECRET" exec ./mvnw -q spring-boot:run
) > "$LOG_DIR/codemind.log" 2>&1 &
PIDS+=($!)

echo ""
echo "Both backends are starting in the background -- give CodeMind ~10-15s to boot."
echo "Tail either log with: tail -f $LOG_DIR/backend.log   or   tail -f $LOG_DIR/codemind.log"
echo ""
echo "Starting the Angular shell on :4200 in the foreground -- Ctrl+C stops everything."
echo ""

cd "$FRONTEND_DIR"
npm start
