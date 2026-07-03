#!/usr/bin/env bash
# Starts StoryForge's backend (which now also serves CodeMind's endpoints,
# see usp-ai-ba/backend/codemind/) and the Angular shell for local dev.
#
# JWT_SECRET isn't set here -- config.py's _default_jwt_secret() already
# auto-generates one and persists it under usp-ai-ba/backend/jobs/.jwt_secret
# on first run, reusing it on every subsequent run, so logins survive a
# restart without any extra setup. That auto-generated value only matters
# for this one process now that there's a single backend -- no more
# cross-app secret coordination needed (see Phase E/F8 history in git log
# if you're wondering why this script used to be more involved).
#
# Usage: ./dev-up.sh
# Stop everything with Ctrl+C.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/.dev-logs"
mkdir -p "$LOG_DIR"

BACKEND_DIR="$ROOT_DIR/usp-ai-ba/backend"
FRONTEND_DIR="$ROOT_DIR/usp-ai-ba/frontend/storyforge-ui"

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
  echo "Stopping backend..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting StoryForge backend on :8000 (log: $LOG_DIR/backend.log)..."
(
  cd "$BACKEND_DIR"
  # shellcheck disable=SC1091
  source venv/bin/activate
  exec uvicorn api.main:app --reload --port 8000
) > "$LOG_DIR/backend.log" 2>&1 &
PIDS+=($!)

echo ""
echo "Backend is starting in the background -- tail its log with: tail -f $LOG_DIR/backend.log"
echo ""
echo "Starting the Angular shell on :4200 in the foreground -- Ctrl+C stops everything."
echo ""

cd "$FRONTEND_DIR"
npm start
