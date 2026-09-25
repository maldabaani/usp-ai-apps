# DevCrew

A local, single-user, role-based multi-agent software development team. Five LangGraph agents
(Planner, Architect, Developer ×N, Reviewer, QA) plan, design, implement, review and test a
greenfield project (Python/FastAPI, Java/Spring Boot, or Angular) in Docker sandboxes, with human
approvals for the plan, the design and the final result, then open a GitHub PR.

All LLM calls go to a **local Ollama**. There are no cloud LLM calls anywhere.

> Status: **Phase 2**: sequential backbone graph, all five roles, approvals, checkpointer,
> `scripts/run_local.py`. Sandbox execution (Phase 3), RAG (4), parallelism (5), API (6),
> UI (7) and GitHub delivery (8) come next.

## Architecture

| Module | Purpose |
|---|---|
| `backend/app/config.py` | All settings from env / `.env` (documented in `.env.example`). |
| `backend/config/models.yaml` | Per-role model config. One shared chat model by default (no VRAM swapping). |
| `backend/app/llm/client.py` | `LLMGateway`: ChatOllama/OllamaEmbeddings factory, a **global asyncio semaphore** (`MAX_PARALLEL_DEVS`) around every chat call, per-role token accounting. |
| `backend/app/health.py` | DB, Chroma, Ollama, pulled models, Docker, GitHub token checks with actionable messages. Used by `GET /health` and by startup fail-fast. |
| `backend/app/db/` | SQLAlchemy 2.0 async models: `runs` (run index) and `run_events` (append-only event log). Alembic migrations in `backend/alembic/`. |
| `backend/app/events/` | In-process asyncio pub/sub. Events are persisted **before** fan-out. Subscribers replay from Postgres after a `Last-Event-ID`, then switch to live with no gaps or duplicates. Slow subscribers resync from the store instead of blocking publishers. |

| `backend/app/graph/backbone.py` | Backbone graph (see below). |
| `backend/app/graph/task_subgraph.py` | Per-task subgraph dispatched with the Send API: developer → reviewer → QA → merge. |
| `backend/app/graph/coordinator.py` | Exception handler. Phase 2 escalates to the human deterministically; Phase 5 adds LLM decisions. |
| `backend/app/graph/context_builder.py` | Scoped, token-budgeted context per role (never the run history, never raw full files). |
| `backend/app/graph/runner.py` | `RunDriver`: start / resume / continue a run, mirror status to DB + events. |
| `backend/app/llm/structured.py` | Structured-output validator: JSON-schema decoding, 2 retries with the Pydantic error fed back, then JSON extraction from raw text. |
| `backend/app/llm/agent.py` | Tool-calling loop: one corrective retry for malformed calls, then error → Coordinator; transcript compaction to fit `num_ctx`. |
| `backend/prompts/*.md` | One prompt file per role. |
| `rules/*.md`, `templates/*/template.yaml` | Per-stack standards (rule ids like `PY-003`) and the starter-template catalog. |

### Graph

```
planner ─▶ approve_plan ─▶ architect ─▶ approve_design ─▶ scaffold ─▶ schedule ─┐
   ▲  │        │ reject        ▲  │           │ reject                          │ Send(task)
   │  └▶ ask_human ◀───────────┘  └▶ coordinator (errors)                       ▼
   └───────────┘                                         task_worker: prepare ─▶ developer ⇄ ask_human
                                                                     reviewer ─(changes)─▶ developer
                                                                     qa ─(fail)─▶ developer
 done ◀─ github_delivery ◀─ approve_final ◀─ integration ◀─ schedule ◀─ merge (1 squashed commit/task)
                               │ reject → follow-up task → schedule        coordinator ◀─ iteration limit
```

Human input points (interrupts): plan, design and final approvals; every `ask_human`; every
Coordinator escalation. Resume payloads: `approve`, `reject {feedback}`, `edit {artifact}`,
`answer {text}`. Invalid payloads re-interrupt with an `error` message.

Design notes:
- **All graph state is JSON** (artifacts via `model_dump`, messages via `messages_to_dict`), so
  checkpoints survive restarts and library upgrades.
- **`ask_human` is a graph node, not an interrupt inside the agent loop.** LangGraph re-runs a
  node from the top on resume, and re-running LLM calls is not deterministic. The agent saves
  its transcript to state, the `ask_human` node interrupts, and the agent continues the same
  conversation with the answer.
- The Reviewer's `rule_ref`s are validated against the rule ids in `rules/<stack>.md`, and the
  Architect's `template_id` against the template catalog, so the validator's retry loop
  corrects bad references.
- QA's pass/fail comes from the sandbox exit code, not from the model. The model only
  summarizes failures for the Developer.
- **Until Phase 3 there is no sandbox runner.** QA writes tests, but they are not executed:
  results show `ran: false` and the task proceeds.

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

## Run from the terminal (Phase 2)

```bash
cd devcrew/backend && . .venv/bin/activate
docker compose up -d postgres            # or use --memory
alembic upgrade head
export OLLAMA_BASE_URL=http://localhost:11434 DATABASE_URL=postgresql+asyncpg://devcrew:<pw>@localhost:5432/devcrew
python ../scripts/run_local.py "Build a FastAPI TODO API with CRUD and pytest tests"
# Ctrl-C / crash at any point, then:
python ../scripts/run_local.py --resume <run_id>
```

At each interrupt you can approve, reject (with feedback), edit (opens `$EDITOR` on the
artifact JSON) or answer. `--auto` approves everything and answers questions with "Use your
best judgment and document the assumption." The generated project lives in
`$WORKSPACES_DIR/<run_id>/repo` on branch `devcrew/<run_id>-<slug>`.

## Configuration

Every variable is documented in [`.env.example`](.env.example). Model settings (per-role
overrides, `num_ctx`, `temperature`, `num_predict`) live in
[`backend/config/models.yaml`](backend/config/models.yaml).
