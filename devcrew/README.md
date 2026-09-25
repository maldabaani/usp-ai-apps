# DevCrew

A local, single-user, role-based multi-agent software development team. Five LangGraph agents
(Planner, Architect, Developer ×N, Reviewer, QA) plan, design, implement, review and test a
greenfield project (Python/FastAPI, Java/Spring Boot, or Angular) in Docker sandboxes, with human
approvals for the plan, the design and the final result, then open a GitHub PR.

All LLM calls go to a **local Ollama**. There are no cloud LLM calls anywhere.

> Status: **Phase 1** (scaffold, config, Ollama gateway + health check, DB + Alembic, event bus).

## Architecture (Phase 1 pieces)

| Module | Purpose |
|---|---|
| `backend/app/config.py` | All settings from env / `.env` (documented in `.env.example`). |
| `backend/config/models.yaml` | Per-role model config. One shared chat model by default (no VRAM swapping). |
| `backend/app/llm/client.py` | `LLMGateway`: ChatOllama/OllamaEmbeddings factory, a **global asyncio semaphore** (`MAX_PARALLEL_DEVS`) around every chat call, per-role token accounting. |
| `backend/app/health.py` | DB, Chroma, Ollama, pulled models, Docker, GitHub token checks with actionable messages. Used by `GET /health` and by startup fail-fast. |
| `backend/app/db/` | SQLAlchemy 2.0 async models: `runs` (run index) and `run_events` (append-only event log). Alembic migrations in `backend/alembic/`. |
| `backend/app/events/` | In-process asyncio pub/sub. Events are persisted **before** fan-out. Subscribers replay from Postgres after a `Last-Event-ID`, then switch to live with no gaps or duplicates. Slow subscribers resync from the store instead of blocking publishers. |

The LangGraph Postgres checkpointer creates its own tables (Phase 2, `AsyncPostgresSaver.setup()`).

## Prerequisites

- Docker + docker compose
- [Ollama](https://ollama.com) on the host, with the models pulled:
  ```bash
  ollama pull qwen2.5-coder:14b
  ollama pull nomic-embed-text
  ```
- **`OLLAMA_NUM_PARALLEL` must be at least `MAX_PARALLEL_DEVS`** (default 2), otherwise parallel
  developers queue inside Ollama. Ollama must also listen on an address the backend container
  can reach:
  ```bash
  OLLAMA_NUM_PARALLEL=2 OLLAMA_HOST=0.0.0.0 ollama serve
  ```
  With a 16–24GB GPU, keep one chat model for all roles. Each parallel slot multiplies the
  KV-cache for `num_ctx`, so lower `num_ctx` in `models.yaml` if you raise parallelism.

## Run

```bash
cd devcrew
cp .env.example .env          # set POSTGRES_PASSWORD, DATABASE_URL, WORKSPACES_DIR, GITHUB_TOKEN
mkdir -p "$(grep ^WORKSPACES_DIR .env | cut -d= -f2)"
docker compose up --build
curl -s localhost:8080/health | jq
```

The backend container runs `alembic upgrade head` on start. With `STARTUP_HEALTH_STRICT=true`
(default) it refuses to start when a critical dependency is down and logs how to fix it.
`GITHUB_TOKEN` is reported but is not critical: it is only needed for PR delivery.

The UI (`frontend`, Phase 7) is behind a compose profile: `docker compose --profile ui up`.

Note that `WORKSPACES_DIR` is mounted at the **same absolute path** in the backend container.
Sandbox containers are started through the host Docker socket, so their bind mounts must be
host paths.

## Backend development (on the host)

```bash
cd devcrew/backend
uv venv -p 3.12 && uv pip install -e ".[dev]"     # or: python3.12 -m venv .venv && pip install -e ".[dev]"
docker compose up -d postgres chromadb
alembic upgrade head
uvicorn app.main:app --reload --port 8080

# quality gates
ruff check . && ruff format --check . && mypy app tests alembic/env.py
pytest                                               # Ollama is always mocked
TEST_DATABASE_URL=postgresql+asyncpg://devcrew:<pw>@localhost:5432/devcrew_test pytest   # + Postgres tests
```

## Configuration

Every variable is documented in [`.env.example`](.env.example). Model settings (per-role
overrides, `num_ctx`, `temperature`, `num_predict`) live in
[`backend/config/models.yaml`](backend/config/models.yaml).
