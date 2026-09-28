#!/usr/bin/env bash
# DevCrew: one-click start.
#
#   macOS    double-click this file in Finder (or run ./start.command in a terminal)
#   Linux    ./start.command
#   Windows  inside WSL2 (Ubuntu) with Docker Desktop's WSL integration on: ./start.command
#
#   ./start.command          start everything and open the UI (the default)
#   ./start.command stop     stop DevCrew (data is kept)
#   ./start.command status   show the containers and the health checks
#   ./start.command logs     follow the backend log
#
# The first start creates .env (with a random database password), finds or starts Ollama,
# pulls the models from backend/config/models.yaml, builds the sandbox images and the app,
# and can take a while. Later starts only start the containers.
#
# Options (environment variables):
#   DEVCREW_OLLAMA=host|docker   force Ollama on this machine or in Docker (default: automatic)
#   DEVCREW_NO_BROWSER=1         do not open the browser
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
ENV_FILE="$ROOT/.env"
MODELS_FILE="$ROOT/backend/config/models.yaml"
UI_URL="http://localhost:4200"   # updated from API_PORT / UI_PORT in .env (see sync_urls)
API_URL="http://localhost:8080"

bold() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m    ! %s\033[0m\n' "$*"; }
fail() {
  printf '\n\033[31mDevCrew could not start: %s\033[0m\n' "$*" >&2
  # keep a double-clicked Terminal window open long enough to read the error
  if [[ -t 0 ]]; then read -r -p "Press Enter to close. " _ || true; fi
  exit 1
}

# ------------------------------------------------------------------------------ .env helpers
env_get() {  # env_get KEY -> value from .env (empty when missing)
  [[ -f "$ENV_FILE" ]] || return 0
  awk -F= -v k="$1" '$1 == k { sub(/^[^=]*=/, ""); v = $0 } END { print v }' "$ENV_FILE"
}

env_set() {  # env_set KEY VALUE: replace or append the line KEY=VALUE in .env
  local tmp
  tmp="$(mktemp)"
  awk -F= -v k="$1" -v v="$2" '
    $1 == k { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }' "$ENV_FILE" >"$tmp"
  cat "$tmp" >"$ENV_FILE"
  rm -f "$tmp"
}

random_password() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24 || true; }

# ------------------------------------------------------------------------------ compose
COMPOSE_FILES=(-f docker-compose.yml)
PROFILES=()

# ${arr[@]+"${arr[@]}"}: an empty array is an error under `set -u` in bash 3.2 (macOS)
compose() { docker compose "${COMPOSE_FILES[@]}" ${PROFILES[@]+"${PROFILES[@]}"} "$@"; }

use_docker_ollama() {
  PROFILES=(--profile ollama)
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1 \
    && docker info 2>/dev/null | grep -qi nvidia; then
    COMPOSE_FILES+=(-f docker-compose.gpu.yml)
    info "NVIDIA GPU found: Ollama in Docker uses it."
  else
    warn "No GPU for Ollama in Docker: models run on the CPU and runs are very slow"
    warn "(see docs/REAL-RUN.md). A host Ollama with a GPU (or Apple silicon) is much faster."
  fi
}

# ------------------------------------------------------------------------------ checks
check_docker() {
  bold "Checking Docker"
  command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install Docker Desktop (macOS/Windows) or Docker Engine (Linux): https://docs.docker.com/get-docker/"
  docker compose version >/dev/null 2>&1 || fail "'docker compose' is missing. Install the Docker Compose plugin."
  if ! docker info >/dev/null 2>&1; then
    if [[ "$(uname)" == "Darwin" ]]; then
      info "Starting Docker Desktop..."
      open -a Docker || true
      for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
    fi
    docker info >/dev/null 2>&1 || fail "Docker is not running. Start Docker Desktop / the Docker service and try again."
  fi
  command -v curl >/dev/null 2>&1 || fail "curl is not installed."
  info "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null) is running."
}

create_env() {
  [[ -f "$ENV_FILE" ]] && return 0
  bold "Creating .env (first start)"
  cp "$ROOT/.env.example" "$ENV_FILE"
  local password workspaces
  password="$(random_password)"
  workspaces="$HOME/devcrew-workspaces"
  env_set POSTGRES_PASSWORD "$password"
  env_set DATABASE_URL "postgresql+asyncpg://devcrew:${password}@localhost:5432/devcrew"
  env_set WORKSPACES_DIR "$workspaces"
  info "Database password: generated. Workspaces: $workspaces"
  if [[ -t 0 ]]; then
    echo
    info "A GitHub token lets DevCrew open pull requests (repo scope, or fine-grained Contents +"
    info "Pull requests read/write). Press Enter to skip: runs then finish without a PR, and"
    info "you can add GITHUB_TOKEN to .env later."
    local token=""
    read -r -s -p "    GitHub token: " token || true
    echo
    if [[ -n "$token" ]]; then
      env_set GITHUB_TOKEN "$token"
      info "Token saved in .env (never committed: .env is in .gitignore)."
    fi
  fi
}

check_env() {
  local workspaces
  workspaces="$(env_get WORKSPACES_DIR)"
  [[ -n "$(env_get POSTGRES_PASSWORD)" ]] || fail "POSTGRES_PASSWORD is empty in .env."
  [[ "$workspaces" == /* ]] || fail "WORKSPACES_DIR in .env must be an absolute path (it is '$workspaces')."
  mkdir -p "$workspaces"
  if [[ -z "$(env_get GITHUB_TOKEN)" ]]; then
    warn "No GITHUB_TOKEN in .env: runs finish without a pull request, and GitHub automation is off."
  fi
}

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

free_port() {  # free_port START: the first free port from START on
  local port="$1"
  while port_busy "$port"; do port=$((port + 1)); done
  echo "$port"
}

check_ports() {
  # only when DevCrew is not running (its own containers hold these ports then)
  if [[ -z "$(compose ps -q 2>/dev/null)" ]]; then
    # every published port moves to the next free one when another program holds it
    local entry key default current port
    for entry in API_PORT:8080 UI_PORT:4200 POSTGRES_PORT:5432 CHROMA_PORT:8000; do
      default="${entry#*:}"
      key="${entry%%:*}"
      current="$(env_get "$key")"
      current="${current:-$default}"
      if port_busy "$current"; then
        port="$(free_port "$((current + 1))")"
        env_set "$key" "$port"
        info "Port $current is in use by another program: $key is now $port."
      fi
    done
  fi
  sync_urls
}

sync_urls() {  # the UI must call the API port, and the API must accept the UI's origin
  local api ui
  api="$(env_get API_PORT)"
  ui="$(env_get UI_PORT)"
  API_URL="http://localhost:${api:-8080}"
  UI_URL="http://localhost:${ui:-4200}"
  env_set DEVCREW_API_URL "$API_URL"
  env_set CORS_ORIGINS "[\"$UI_URL\"]"
}

# ------------------------------------------------------------------------------ Ollama
host_ollama_reachable_from_docker() {
  # What the backend will do: reach the host's Ollama from a container. On Linux this needs
  # Ollama to listen on all interfaces (OLLAMA_HOST=0.0.0.0); Docker Desktop (macOS, Windows)
  # also reaches a host Ollama that listens on 127.0.0.1.
  local image="postgres:16-alpine"  # already needed by DevCrew; has busybox wget
  docker image inspect "$image" >/dev/null 2>&1 || docker pull -q "$image" >/dev/null 2>&1 || return 1
  docker run --rm --add-host host.docker.internal:host-gateway --entrypoint wget "$image" \
    -q -T 5 -O /dev/null http://host.docker.internal:11434/api/tags >/dev/null 2>&1
}

setup_ollama() {
  bold "Checking Ollama"
  local mode="${DEVCREW_OLLAMA:-auto}"
  if [[ "$mode" == "auto" ]]; then
    if curl -fsS -m 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
      if host_ollama_reachable_from_docker; then
        mode=host
      else
        warn "Ollama runs on this machine but containers cannot reach it (on Linux it listens on"
        warn "127.0.0.1 only)."
        warn "Restart it with OLLAMA_HOST=0.0.0.0 to use it; using Ollama in Docker instead."
        mode=docker
      fi
    else
      mode=docker
      info "No Ollama on this machine: using Ollama in Docker."
    fi
  fi
  case "$mode" in
    host)
      env_set OLLAMA_BASE_URL "http://host.docker.internal:11434"
      # an Ollama container from an earlier start is not needed any more
      docker compose --profile ollama stop ollama >/dev/null 2>&1 || true
      info "Using the Ollama on this machine."
      ;;
    docker)
      env_set OLLAMA_BASE_URL "http://ollama:11434"
      use_docker_ollama
      compose up -d ollama >/dev/null
      ;;
    *) fail "DEVCREW_OLLAMA must be host or docker (it is '$mode')." ;;
  esac
  OLLAMA_MODE="$mode"
}

ollama_list() {  # the installed models, wherever Ollama runs
  if [[ "$OLLAMA_MODE" == "host" ]]; then
    curl -fsS -m 10 http://localhost:11434/api/tags
  else
    compose exec -T ollama ollama list
  fi
}

ollama_pull() {  # ollama_pull MODEL
  if [[ "$OLLAMA_MODE" == "host" ]]; then
    curl -fsS -m 7200 -X POST http://localhost:11434/api/pull \
      -d "{\"model\":\"$1\",\"stream\":false}" >/dev/null
  else
    compose exec -T ollama ollama pull "$1"
  fi
}

pull_hint() {
  if [[ "$OLLAMA_MODE" == "host" ]]; then
    echo "ollama pull $1"
  else
    echo "docker compose --profile ollama exec ollama ollama pull $1"
  fi
}

pull_models() {
  bold "Checking models (backend/config/models.yaml)"
  local models installed model wanted
  models="$(grep -E '^[[:space:]]*model:' "$MODELS_FILE" | awk '{print $2}' | sort -u)"
  if [[ "$OLLAMA_MODE" == "docker" ]]; then
    for _ in $(seq 1 30); do compose exec -T ollama ollama list >/dev/null 2>&1 && break; sleep 2; done
  fi
  installed="$(ollama_list 2>/dev/null || true)"
  for model in $models; do
    wanted="$model"
    [[ "$wanted" == *:* ]] || wanted="$wanted:latest"
    if grep -qF -- "$wanted" <<<"$installed"; then
      info "$model: installed"
    else
      info "$model: downloading (several GB for chat models; this happens once)..."
      ollama_pull "$model" || fail "could not download $model. Check the internet connection, or run: $(pull_hint "$model")"
      info "$model: installed"
    fi
  done
}

# ------------------------------------------------------------------------------ images
build_sandbox_images() {
  bold "Checking sandbox images"
  local prefix missing=()
  prefix="$(env_get SANDBOX_IMAGE_PREFIX)"
  prefix="${prefix:-devcrew-sandbox}"
  for stack in python java node mixed; do
    docker image inspect "$prefix-$stack:latest" >/dev/null 2>&1 || missing+=("$stack")
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    info "All sandbox images are built."
    return
  fi
  info "Building: ${missing[*]} (first start only; this takes several minutes)..."
  SANDBOX_IMAGE_PREFIX="$prefix" "$ROOT/scripts/build_sandbox_images.sh" "${missing[@]}" \
    || fail "building the sandbox images failed (see the output above)."
}

# ------------------------------------------------------------------------------ start
wait_until_ready() {
  bold "Waiting for DevCrew"
  local code=""
  for _ in $(seq 1 180); do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$API_URL/health" || true)"
    [[ "$code" == "200" || "$code" == "503" ]] && break
    if [[ "$(compose ps --status exited --services 2>/dev/null)" == *backend* ]]; then
      compose logs --tail 40 backend
      fail "the backend stopped (see its log above)."
    fi
    sleep 2
  done
  [[ "$code" == "200" || "$code" == "503" ]] || fail "the backend did not answer on $API_URL (run: ./start.command logs)."
  for _ in $(seq 1 60); do
    curl -fsS -o /dev/null -m 5 "$UI_URL" && break
    sleep 2
  done
  show_health
}

show_health() {  # the backend's health checks, one line each (formatted by its own Python)
  compose exec -T backend python -c '
import json, urllib.request, urllib.error
try:
    body = urllib.request.urlopen("http://localhost:8080/health", timeout=10).read()
except urllib.error.HTTPError as exc:
    body = exc.read()
for c in json.loads(body)["checks"]:
    mark = "ok     " if c["ok"] else ("PROBLEM" if c["critical"] else "warning")
    print("    " + mark + " " + c["name"] + ("" if c["ok"] else ": " + c["detail"]))
' 2>/dev/null || warn "the backend is not running (./start.command logs shows why)."
}

open_browser() {
  [[ "${DEVCREW_NO_BROWSER:-0}" == "1" ]] && return
  if [[ "$(uname)" == "Darwin" ]]; then
    open "$UI_URL"
  elif grep -qi microsoft /proc/version 2>/dev/null; then
    cmd.exe /c start "$UI_URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$UI_URL" >/dev/null 2>&1 || true
  fi
}

start() {
  check_docker
  create_env
  check_env
  check_ports
  setup_ollama
  pull_models
  build_sandbox_images
  bold "Building and starting DevCrew (the first build takes a few minutes)"
  compose up -d --build || fail "docker compose could not start the stack (see the output above)."
  wait_until_ready
  bold "DevCrew is running"
  info "Web UI:   $UI_URL"
  info "API:      $API_URL/docs"
  info "Stop:     ./start.command stop      Status: ./start.command status      Log: ./start.command logs"
  open_browser
}

current_profiles() {  # stop/status/logs must include the Ollama service when it is in use
  [[ "$(env_get OLLAMA_BASE_URL)" == "http://ollama:11434" ]] && PROFILES=(--profile ollama)
  return 0
}

case "${1:-start}" in
  start) start ;;
  stop)
    current_profiles
    bold "Stopping DevCrew (data is kept; ./start.command starts it again)"
    compose stop
    ;;
  status)
    current_profiles
    sync_urls
    compose ps
    bold "Health"
    show_health
    ;;
  logs)
    current_profiles
    compose logs -f --tail 100 backend
    ;;
  *) fail "unknown command '$1' (use: start, stop, status or logs)" ;;
esac
