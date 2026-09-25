# DevCrew

A local, single-user, role-based multi-agent software development team. Five LangGraph agents
(Planner, Architect, Developer ×N, Reviewer, QA) plan, design, implement, review and test a
greenfield project (Python/FastAPI, Java/Spring Boot, or Angular) in Docker sandboxes, with human
approvals for the plan, the design and the final result, then open a GitHub PR.

All LLM calls go to a **local Ollama**. There are no cloud LLM calls anywhere.

> Status: **Phase 5**: backbone graph with all five roles, parallel developers in git
> worktrees, LLM Coordinator, agent Q&A, Docker sandboxes, runnable starter templates, per-run
> RAG, `scripts/run_local.py`. API (6), UI (7) and GitHub delivery (8) come next.
> Open issues and deferred work: [BACKLOG.md](BACKLOG.md).

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
| `backend/app/graph/coordinator.py` | Exception handler: LLM decisions (retry / replan / split / escalate), deterministic conflict resolution, human escalation. |
| `backend/app/graph/replan.py` | Applies Coordinator plan changes (replan / split) and re-validates the DAG. |
| `backend/app/tools/agents.py` | `ask_agent`: one-shot questions to the Architect / Planner, routed to the human when needed. |
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
- With `SANDBOX_ENABLED=false`, QA writes tests but they are not executed: results show
  `ran: false` and the task proceeds.

## Parallel execution, Coordinator and agent Q&A (Phase 5)

**Scheduling in waves.** The scheduler dispatches every task whose dependencies are merged, up
to `MAX_PARALLEL_DEVS` at once, as parallel `task_worker` subgraphs (LangGraph `Send`). When
the wave finishes, it applies Coordinator plan changes and dispatches the next wave. Each task
records its `wave` and `lane` for the UI's parallel lanes. LLM calls are bounded by the same
semaphore. Because a wave ends only when all of its tasks end, a task waiting on a human
question delays the next wave (BL-050).

**Worktrees.** Each task works in `WORKSPACES_DIR/<run_id>/worktrees/<task_id>` on its own
branch `devcrew/<run>/task-<id>`, with its own sandbox container. The main repository
(`<run_id>/repo`) stays on the integration branch. Worktree add/remove, merges and
re-indexing are serialized with a per-repository lock. A worktree is removed after its task
merges; the branch is kept.

**Merges and conflicts.** Each task is squash-merged, so the integration branch gets one
commit per task. On a conflict the merge is aborted, and the Coordinator merges the
integration branch into the task's worktree and assigns the Developer to resolve the conflict
there. The Developer is told which files conflict; commits containing conflict markers are
refused. The resolved change then goes through review and QA again before re-merging. After
`MAX_CONFLICT_ROUNDS` the Coordinator escalates to the human.

**Coordinator.** It is not a persona, and it uses the LLM only for exceptions, through a
validated `CoordinatorDecision`:

| Exception | Allowed actions |
|---|---|
| Task hit `MAX_DEV_ITERATIONS` | `replan` (rewrite the task, restart from integration), `split` (2–4 subtasks; dependents are rewired to all subtasks), `escalate` |
| Agent error (malformed tool calls after one retry, invalid structured output) | `retry` with guidance, `replan`, `escalate` |
| Planner/Architect failure | `retry` with guidance, `escalate` |
| Merge conflict | deterministic: a Developer resolves it (see above) |

- Plan changes flow to the scheduler through an append-only `plan_changes` channel, so
  parallel tasks never write the plan concurrently. The resulting plan is re-validated as a
  DAG.
- After `MAX_COORDINATOR_ACTIONS` automatic decisions, or when no valid decision can be
  obtained, it escalates to the human.
- The decision and the human interrupt are separate nodes (`coordinator` / `escalate`), so
  the LLM decision never re-runs on resume.

**Agent Q&A.**
- `ask_agent(architect|planner, question)` (Developer) is a scoped one-shot structured call.
  It sends the target role's prompt without its output section, the question, and only the
  relevant artifacts (design contracts and doc for the Architect; stories and tasks for the
  Planner). It does not run the target's node.
- If the target answers `needs_human`, the question is routed to the human and the answer
  resumes the Developer.
- `ask_agent` and `ask_human` share `MAX_QUESTIONS_PER_TASK`. Every Q&A is logged to
  `qa_log` and emitted as `question`/`answer` events.
- Parallel tasks can have questions pending at the same time; each is resumed independently
  with its interrupt id (`RunDriver.resume(..., interrupt_id)`), including after a restart.

## Sandbox (Phase 3)

Generated code only ever runs inside Docker, never on the host.

| Image | Contents | Used for |
|---|---|---|
| `devcrew-sandbox-python` | Python 3.12, pytest, ruff | `python` projects |
| `devcrew-sandbox-java` | Temurin 21, Maven 3.9 | `java` projects |
| `devcrew-sandbox-node` | Node 20, Angular CLI 19, Chromium | `angular` projects |
| `devcrew-sandbox-mixed` | all of the above | `mixed` designs (only needed for those) |

```bash
scripts/build_sandbox_images.sh                 # all four
scripts/build_sandbox_images.sh python java     # a subset
# base images are build args (PYTHON_IMAGE, MAVEN_IMAGE, NODE_IMAGE), e.g. to use a mirror:
scripts/build_sandbox_images.sh -- --build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:3.12-slim
```

How commands run (`backend/app/sandbox/`):
- **One container per task worktree**, created on first use and removed after the task
  merges or fails. All of a run's containers and per-run volumes are removed when the run
  completes. The workspace is bind-mounted at `/workspace`.
- **The network is off** (`network_mode=none`) for every agent command and test run. The only
  networked step is **dependency install**: the template's fixed `install_cmd` (never an
  agent-chosen command) runs in a short-lived container on the bridge network. It runs at
  scaffold time and again whenever a manifest changes (`pyproject.toml`, `pom.xml`,
  `package.json`).
- **Dependencies live in volumes**: a per-run Python venv and `node_modules`, plus shared
  Maven (`devcrew-m2`) and npm caches. Maven runs in offline mode (`-o`) outside the
  install step.
- **Limits and hardening**: a per-command timeout (`timeout -s KILL` inside the container
  plus an outer watchdog), CPU/memory/pids limits, all capabilities dropped,
  `no-new-privileges`, and a read-only root filesystem. Commands run as the backend's
  uid:gid, which is root inside the compose backend container.
- **Policy** (`policy.py`): denies `rm -rf /` (and `~`, `--no-preserve-root`), `curl|sh`-style
  download-and-execute, `docker`, `sudo`/`su`, including when nested in `sh -c`, `$(...)`,
  backticks, `env`/`timeout` wrappers. Working directories must be inside `WORKSPACES_DIR`
  (allowlist), and `cwd` must stay inside the project.
- Tools: the Developer and QA get `run_command` (sandboxed). QA's pass/fail is the test
  command's exit code.

## Code retrieval / RAG (Phase 4)

`backend/app/rag/`: one ChromaDB collection per run, `run_<run_id>`.

- **What is indexed**: the tracked files of the integration branch (template, `docs/design.md`,
  every merged task), plus `rules/<stack>.md` for the stacks in the design. Lockfiles,
  binaries, empty files and files over `RAG_MAX_FILE_BYTES` are skipped.
- **Code-aware chunking**:
  - Python is split with `ast` (functions and classes, decorators included; large classes
    are split into their methods).
  - Java, TypeScript and JavaScript are split by brace matching that ignores strings and
    comments. Units are classes, methods, functions, consts, and Jasmine `describe`/`it`
    blocks, with multi-line decorators such as `@Component({...})` kept with their class.
  - Markdown is split by heading.
  - Anything else, and any unit still longer than `RAG_CHUNK_MAX_LINES`, becomes line
    windows with overlap.
  - Metadata per chunk: `path`, `language`, line range, `symbol`, `commit_sha`,
    `file_hash`, `source`.
- **Incremental**: indexing runs at scaffold and again after every merge into the
  integration branch. Only files whose content hash changed are re-embedded, and chunks of
  deleted files are removed.
- **Embeddings**: `nomic-embed-text` via Ollama, with its `search_document:` /
  `search_query:` prefixes.
- **`search_codebase(query, k, path_filter?)`** (Developer, Reviewer, QA): returns chunks with
  path and line ranges. `path_filter` is a directory prefix (`app/`) or a glob
  (`*.spec.ts`).
- **Context builders** add retrieved chunks as a budgeted section, never whole files:
  related code for the Developer and Reviewer, and existing tests (to copy fixtures and
  style) for QA.
- **No cross-run memory**: the collection is deleted when the run completes, fails or is
  cancelled, together with the sandbox containers and volumes.

### Starter templates (`templates/<stack>/`)

| id | Stack | Sample test | Test command |
|---|---|---|---|
| `python-fastapi` | FastAPI + pydantic-settings, routers/schemas/services | `GET /health` via TestClient | `pytest -q` |
| `java-spring-boot` | Spring Boot 3.5, Java 21, controller/service/repository/dto | `@WebMvcTest` MockMvc | `mvn -q test` |
| `angular-standalone` | Angular 19 standalone, OnPush + signals, router | TestBed component spec | `npx ng test --watch=false --browsers=ChromeHeadless` |

Each `template.yaml` has `id`, `stack`, `description`, `install_cmd`, `build_cmd`, `test_cmd`.
Multi-stack projects use `stack: mixed` with `components` (one template per sub-directory,
e.g. `backend/` + `frontend/`). Each stack's commands run in its own sub-directory, in the
`mixed` image.

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
../scripts/build_sandbox_images.sh       # once
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
