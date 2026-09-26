# DevCrew

A local, single-user, role-based multi-agent software development team. Five LangGraph agents
(Planner, Architect, Developer ×N, Reviewer, QA) plan, design, implement, review and test a
greenfield project (Python/FastAPI, Java/Spring Boot, or Angular) in Docker sandboxes, with human
approvals for the plan, the design and the final result, then open a GitHub PR.

All LLM calls go to a **local Ollama**. There are no cloud LLM calls anywhere.

> Status: **Phase 8**: GitHub delivery (repo creation, push, pull request) after final
> approval, on top of the Angular UI, the HTTP API (SSE replay, resume/cancel, restart recovery)
> and the backbone graph (five roles, parallel developers in git worktrees, LLM Coordinator,
> agent Q&A, Docker sandboxes, starter templates, per-run RAG). The benchmark (9) comes next.
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
| `backend/app/services/run_manager.py` | Background drives per run, cancel, restart recovery (used by the API). |
| `backend/app/api/` | FastAPI routers: runs (+ SSE), workspace files/diff, health. |
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

## Workflow view and requirements documents (Phase 10)

**Requirements input.** The New run page takes a feature request or a requirements document:
- Type or paste into the editor; the Preview toggle renders the Markdown.
- Or upload `.md`/`.txt` files by drag-and-drop or the file picker. Several files are joined,
  each under a `# <file name>` heading.
- A counter shows the size against `MAX_REQUEST_CHARS` (default 20,000 characters, served by
  `GET /config`). The Planner and Architect always receive the whole text, so the limit must
  fit their context window. To accept longer documents, raise `MAX_REQUEST_CHARS` together
  with `num_ctx` in `models.yaml`.

**Workflow graph.** The run page opens on an n8n-style graph drawn with
[ngx-vflow](https://www.ngx-vflow.org/):
- The main flow runs Requirements → Planner → Plan approval → Architect → Design approval →
  Scaffold, then one node per plan task (wired by dependencies, parallel tasks side by side),
  then Integration tests → Final approval → GitHub PR.
- Each node shows a robot for the agent at work, its status and elapsed time. Task nodes
  show dev → review → QA progress, the iteration count and Coordinator actions.
- Node colours:

  | Colour | Meaning |
  |---|---|
  | Cyan pulse | Working |
  | Amber pulse, with a badge | Needs you |
  | Teal | Done |
  | Red | Failed |
  | Striped | Skipped or blocked |

- Edges animate while work flows into a node. Rejections show as amber loop edges
  ("rejected 1x").
- **Follow** (the default) zooms to the active work and moves along with it. **Overview** shows
  the whole flow; finished runs always open in overview.

**Side panel.** Clicking a node opens a panel with its output and actions:
- **Requirements:** the document.
- **Planner:** the plan.
- **Architect:** the design, including the Architect's **plan assessment** (concerns,
  assumptions and suggested plan changes). The assessment is advisory; the plan only changes
  if you reject or edit.
- **Task:** review, tests and diff.
- **Integration:** test results.
- **PR:** the link.
- **Every node:** its live activity feed.

Approvals and questions are answered right in the panel. When something newly needs you, a
"Waiting for you" banner appears and the panel opens on that node. A panel opened this way
moves on with the work; a node you pick stays selected.

The graph comes from `GET /runs/{id}/workflow`, which is computed from the run status,
checkpointed state, pending interrupts and the event log. It looks the same after a reload or
a backend restart, and the UI re-fetches it (batched) as events stream in. The Timeline, Plan,
Design, Q&A and Files tabs are still below the graph.

## Benchmark (Phase 9)

`benchmarks/` holds 12 tasks: 4 per stack (Python/FastAPI, Java/Spring Boot, Angular),
from a single CRUD resource (difficulty 1) to a multi-module service (difficulty 4). Each task
has a precise request in `benchmarks/tasks/<stack>.yaml` and a **hidden** acceptance suite in
`benchmarks/hidden_tests/<task_id>/` that the agents never see (93 tests in total). See
[`benchmarks/README.md`](benchmarks/README.md) for the conventions.

```bash
cd devcrew/backend && . .venv/bin/activate
docker compose up -d chromadb            # Postgres is not needed (in-memory checkpoints)
export OLLAMA_BASE_URL=http://localhost:11434 CHROMA_HOST=localhost
python ../scripts/run_benchmark.py --list                     # tasks and hidden-test counts
python ../scripts/run_benchmark.py --stack python             # or --task py-todo-crud ...
python ../scripts/run_benchmark.py \
    --models-config config/models.yaml --models-config /path/to/other-models.yaml   # compare
```

Runs are headless and fully automatic:
- Every approval is approved.
- Every agent question is answered with "Use your best judgment and document the assumption."
- Escalations are retried with that same guidance up to `--max-escalations` times (default 2),
  then given up, so a stuck run always ends.
- `--task-timeout-min` (default 120) caps a single run.
- Nothing is pushed to GitHub, whatever `GITHUB_DELIVERY_ENABLED` says.

Scoring: after a run, its integration branch is exported (`git archive`) to
`$WORKSPACES_DIR/<run_id>/hidden-eval`, the hidden tests are copied in, and the suite runs in
the sandbox like any other test command (dependency install with network, tests offline).
Tests that never ran (for example a compile error or a missing component) count as failures
against the suite size.

Per task, `benchmarks/results/<timestamp>.json` and `.md` record:
- run status and wall time;
- hidden tests passed/total and pass rate;
- dev iterations (including the attempts before an escalation retry reset the counter);
- escalations, and questions to the human and to other agents;
- Coordinator calls, and planned/merged/failed tasks;
- whether the project's own tests passed at integration;
- token counts (total and per role).

A summary per models config is included, plus a comparison table when several configs are
given. Results are rewritten after each task, so an interrupted benchmark keeps what it has.

### End-to-end demo

With the stack up (`docker compose up`, Ollama on the host, `GITHUB_TOKEN` set):

1. Open http://localhost:4200 and click **New run**. Enter
   "Build a FastAPI TODO API with CRUD and pytest tests" and your `owner/repo`; tick "Create the
   repository if it is missing" if it does not exist yet.
2. Review and approve the plan, then the design (or reject with feedback, or edit the JSON).
3. Watch the task lanes, answer agent questions in the action panel, and inspect task diffs.
4. Approve the final result. The integration branch is pushed and the PR link appears next to
   the run status.

## GitHub delivery (Phase 8)

`backend/app/github/`. Delivery runs only after you approve the final result: the approval gate
sets `final_approved`, and the delivery refuses to push without it.

1. **Repository.** If `owner/repo` is missing and the run was started with "create repo if
   missing", DevCrew creates it **private and empty** (`POST /user/repos` for your own account,
   `POST /orgs/{org}/repos` for an organization). Otherwise delivery fails with an actionable
   message.
2. **`main`.** It is pushed **only if the remote repository is empty**, and then only the
   template scaffold commit. A non-empty repository whose `main` is not this run's scaffold is
   refused: DevCrew builds greenfield projects and never pushes to, or forces, an existing
   `main`.
3. **The run's branch.** `devcrew/<run_id>-<slug>` is pushed. It is the integration branch,
   with one commit per task. Task branches stay local.
4. **Pull request.** A PR is opened against `main`, or the existing open PR for the branch is
   reused, so re-delivery is idempotent. Its body contains the request, plan summary and user
   stories, the design document, the task list with statuses, attempts and test results, the
   integration test results (with failing output), and the Q&A log. `pr_url` is stored in the
   graph state and the runs table, and shown in the UI.

If delivery fails (bad token, missing permission, network, non-empty repo), the run asks you
to **retry delivery** or **finish without a PR**. The token is sent to git as an HTTP header
through environment-based git config, scoped to `GITHUB_GIT_URL`. It never appears in remote
URLs, `.git/config` or process arguments, and is scrubbed from error messages.

Token permissions: classic `repo` scope, or fine-grained **Contents** and **Pull requests**
read/write, plus **Administration** write if DevCrew should create repositories.

## Web UI (Phase 7)

`docker compose up` serves the UI at **http://localhost:4200** (nginx, static bundle). It calls
the backend at `http://localhost:8080`, set in `frontend/src/environments/`.

- **Runs list**: status, repository, PR link; refreshes every 5s.
- **New run**: feature request, `owner/repo`, "create repo if missing" (typed reactive form
  with validation).
- **Run detail**:
  - **Action panel**, one per pending interrupt. Approvals offer approve / reject with
    feedback / edit (the plan or design as JSON). Questions get an answer box. Escalations
    offer retry / retry with guidance / give up. Only the actions the backend allows are shown.
  - **Timeline**: live events over SSE (toggle to include tool calls).
  - **Tasks**: the DAG as **parallel lanes**, one column per wave with tasks in their lanes,
    then the planned layers. Selecting a task shows its review issues (with rule refs),
    test results and logs, developer feedback, and its **diff**.
  - **Plan** and **Design** viewers (the design document is rendered from markdown and
    sanitized), **Q&A** log, and a **Files** explorer for the integration branch, `main` or
    any task branch.
  - PR link, cancel button, and the event stream's connection state.
- **SSE client** (`core/run-events.service.ts`):
  - Exposes events, connection state and the last event id as signals, de-duplicated by id.
  - The browser's `EventSource` reconnects with `Last-Event-ID`. If the connection is
    closed, the client reconnects with exponential backoff and `?last_event_id=`.
  - The stream closes itself once the run is finished.
- Stack: Angular 19, standalone components, signals, `@if`/`@for`, OnPush, Angular Material,
  no web fonts (system UI font; works offline).

Development:
```bash
cd devcrew/frontend
npm ci
npx ng serve                      # http://localhost:4200, backend on :8080
npx ng lint
npx ng test --watch=false --browsers=ChromeHeadless
```

## HTTP API (Phase 6)

The backend runs on `http://localhost:8080`. docker-compose publishes every port on
`127.0.0.1` only, because DevCrew has no authentication by design. Interactive docs are at
`/docs`.

| Method & path | Purpose |
|---|---|
| `POST /runs` `{request, repo_target, create_repo}` | Create a run and start it in the background → `201` run summary |
| `GET /runs` | List runs (newest first) |
| `GET /runs/{id}` | Run detail: status, plan, design, tasks (with `wave`/`lane`), Q&A log, integration results, errors, and **`pending`** (every interrupt waiting for input: `interrupt_id`, `kind`, `title`, `artifact`, `allowed_actions`, `data`, `error`) |
| `GET /runs/{id}/events` | **SSE** stream of run events. Replays everything after `Last-Event-ID` (header, sent automatically by `EventSource` on reconnect) or `?last_event_id=`, then streams live. Sends keepalives every 15s and closes after the run reaches a terminal status. |
| `POST /runs/{id}/resume` `{action, feedback?, artifact?, answer?, interrupt_id?}` | Answer the pending interrupt → `202`. `interrupt_id` is required when several questions are pending (parallel tasks). `409` if the run is busy, finished or not waiting; `422` if the action is not allowed for that interrupt or required fields are missing. |
| `POST /runs/{id}/cancel` | Stop the run, mark it `cancelled`, and remove its containers, per-run volumes, worktrees and Chroma collection |
| `GET /runs/{id}/files?ref=` | Files tracked on `integration` (default), `main` or `task:<id>` |
| `GET /runs/{id}/files/{path}?ref=` | File content from git (binary-safe, size-capped, no path traversal, refs limited to the run's own branches) |
| `GET /runs/{id}/diff?task_id=` | A task's changes (its branch vs. where it forked from integration), or the whole run's changes (integration vs. the scaffold on `main`) |
| `GET /health` | DB, Chroma, Ollama, models, Docker, sandbox images, GitHub token |

**Execution model.** `RunManager` runs at most one background drive per run (start, resume
or continue); the API returns immediately and progress arrives over SSE.

**Restarts.** The graph is checkpointed in Postgres. On startup the backend continues every
run that was mid-flight (statuses `planning` … `delivering`) from its last checkpoint. Runs
waiting for human input keep waiting, and their pending questions are still listed by
`GET /runs/{id}`. Clients reconnect to `/events` with `Last-Event-ID` to replay what they
missed. This has been tested by `kill -9` of the backend mid-run.

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

The UI is served at http://localhost:4200.

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
