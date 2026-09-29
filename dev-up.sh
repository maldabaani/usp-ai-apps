#!/usr/bin/env bash
# Starts StoryForge's backend and the Angular shell for local dev.
#
# JWT_SECRET isn't set here -- config.py's _default_jwt_secret() already
# auto-generates one and persists it under usp-ai-ba/backend/jobs/.jwt_secret
# on first run, reusing it on every subsequent run, so logins survive a
# restart without any extra setup. That auto-generated value only matters
# for this one process now that there's a single backend -- no more
# cross-app secret coordination needed (see Phase E/F8 history in git log
# if you're wondering why this script used to be more involved).
#
# Since the DevCrew merge, the one backend also assembles DevCrew's own
# container (Postgres-checkpointed graph, Docker-sandboxed agents) at
# startup -- see api/main.py's lifespan. That assembly is wrapped so a
# missing Postgres/Docker/Ollama doesn't stop StoryForge's own routes from
# working, but DevCrew's routes (and "Send to DevCrew") won't either, so
# this script checks both up front and warns (not fails) if either is
# unreachable -- same "don't block StoryForge over DevCrew" spirit as that
# lifespan wrapper, just surfaced earlier where it's easier to notice.
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

DEVCREW_PG_PORT="${DEVCREW_PG_PORT:-5432}"
if command -v pg_isready >/dev/null 2>&1; then
  if ! pg_isready -q -p "$DEVCREW_PG_PORT" 2>/dev/null; then
    echo "Warning: no Postgres reachable on port $DEVCREW_PG_PORT -- DevCrew's routes and \"Send to DevCrew\" will be unavailable (StoryForge's own features are unaffected). Start Postgres or 'docker compose up devcrew-postgres'." >&2
  fi
elif command -v docker >/dev/null 2>&1; then
  if ! (echo > "/dev/tcp/127.0.0.1/$DEVCREW_PG_PORT") >/dev/null 2>&1; then
    echo "Warning: no Postgres reachable on port $DEVCREW_PG_PORT -- DevCrew's routes and \"Send to DevCrew\" will be unavailable (StoryForge's own features are unaffected). Start Postgres or 'docker compose up devcrew-postgres'." >&2
  fi
fi

if command -v docker >/dev/null 2>&1; then
  if ! docker info >/dev/null 2>&1; then
    echo "Warning: Docker daemon isn't reachable -- DevCrew's sandboxed agent tasks will fail (StoryForge's own features are unaffected). Start Docker (dockerd/Docker Desktop) before dispatching a DevCrew run." >&2
  fi
else
  echo "Warning: 'docker' CLI not found -- DevCrew's sandboxed agent tasks need a reachable Docker daemon. Install Docker before dispatching a DevCrew run." >&2
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
