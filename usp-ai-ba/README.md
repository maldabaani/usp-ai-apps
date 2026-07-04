# StoryForge AI

StoryForge AI turns a Solution Design Document (SDD) PDF into a fully-detailed, ready-to-create Azure DevOps work item hierarchy — **Epic → User Story → Dev Tasks + Unit Test Tasks** — using a local Ollama model (`qwen2.5:14b` by default) for analysis/generation, the same Ollama server for embeddings + ChromaDB for retrieval-augmented context (RAG) over your existing codebase, user manuals, and JPA entities, and a human-in-the-loop review/clarification workflow before anything is written to ADO.

This same backend and Angular app also serve **CodeMind** — a per-repository business-logic
extraction and Q&A tool, merged in from a formerly-standalone Java service (see the
[CodeMind](#codemind) section below). The two flows share one FastAPI process, one
job-persistence layer under `JOBS_DIR`, one `/settings` screen, and one error-monitoring
feed, but otherwise don't interact — this doc's SDD-to-stories content below is unaffected
by CodeMind's presence in the same app.

> `clarify_node` and `generate_node` (StoryForge's own pipeline, below) run at
> `temperature=0` with a fixed seed, so the same SDD produces the same clarification
> questions and the same generated stories on every run. Both nodes retry (with a nudged
> seed) on transient failures or malformed JSON before giving up — see
> [`backend/pipeline/nodes/llm_retry.py`](backend/pipeline/nodes/llm_retry.py).
> `ANTHROPIC_API_KEY`/`CLAUDE_MODEL` are configured in `config.py` but are not called
> anywhere in *this pipeline* — Claude is genuinely used elsewhere in this same backend,
> by CodeMind's extraction/Ask agents (see [CodeMind](#codemind) below).

It is a two-part application:

- **Backend** — Python / FastAPI. StoryForge's own flow is orchestrated by a
  [LangGraph](https://github.com/langchain-ai/langgraph) state machine; CodeMind's flow
  (`codemind/` package) is a simpler per-file fan-out job model, `asyncio`-based
- **Frontend** — Angular 17+ standalone-component SPA, one shell for both flows

## Table of contents

- [How it works](#how-it-works)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
  - [Backend](#backend-setup)
  - [Frontend](#frontend-setup)
  - [Ollama](#ollama-setup)
  - [Azure DevOps MCP server](#azure-devops-mcp-server)
  - [Notion setup](#notion-setup)
- [Configuration reference](#configuration-reference)
- [Running the app](#running-the-app)
- [One-time ingestion](#one-time-ingestion)
- [API reference](#api-reference)
- [Generated story JSON schema](#generated-story-json-schema)
- [Pipeline state machine](#pipeline-state-machine)
- [Frontend pages](#frontend-pages)
- [CodeMind](#codemind)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

## How it works

1. **Ingest once** — index your User Manual PDFs and your Maven multi-module monorepo (Spring Boot services + Angular + legacy JS/jQuery) into ChromaDB. This only needs to be re-run when the source manuals/codebase change meaningfully.
2. **Assess** — submit an SDD PDF along with a PPM number/name and system name. The pipeline extracts the SDD text, retrieves relevant context from the three ChromaDB collections, and (optionally) pauses to ask clarifying questions if it detects ambiguity in four specific categories (undefined status/error codes, missing API contracts, unspecified middleware topics, unconfirmed DB changes).
3. **Generate** — the local model produces one User Story (with 7-section Dev Tasks and 5-section Unit Test Tasks, 1:1 mapped) per distinct feature found in the SDD, grounded in the retrieved context.
4. **Review** (optional, `review_mode`) — a human can edit the generated stories before anything is created downstream.
5. **Export** — depending on `OUTPUT_MODE`, the approved Epic → User Story → Dev Tasks + Unit Test Tasks hierarchy is either written to a `.docx` (`document`, default), pushed to Azure DevOps as an Epic/User Story/Task hierarchy via a local Node.js MCP server (`ado`), or pushed to a Notion database as one Epic page per story with the tasks rendered as nested blocks (`notion`) — with results (file path, work item IDs + URLs, or Notion page URLs) reported back to the UI.

## Architecture

```
                     ┌─────────────────────────┐
PDFs / Codebase ───▶ │   Ingestion (one-time)  │ ───▶ ChromaDB (3 collections)
                     └─────────────────────────┘        - sf_user_manuals
                                                         - sf_codebase
                                                         - sf_jpa_entities
                                                              │
                                                              ▼
SDD PDF ───▶ POST /api/assess ───▶  LangGraph pipeline (pipeline/graph.py)
                                  analyze ─▶ clarify ─▶ generate ─▶ review ─▶ export_document | create_ado | create_notion
                                     │           │          │          │              │             │             │
                              extract text  ambiguity   local model human edit      .docx file    ADO MCP      Notion API
                              + RAG fetch    detection  generation  gate       (OUTPUT_MODE=    (Node.js,    (notion-client,
                                                                                 document,        stdio,      OUTPUT_MODE=
                                                                                 default)      OUTPUT_MODE=ado)   notion)
                                                              │
                                                              ▼
                                                   Angular SPA (poll /api/assess/status)
```

The graph is checkpointed (`AsyncSqliteSaver`, persisted to `JOBS_DIR/checkpoints.sqlite`, keyed by `job_id`) and interrupts before `generate_node` and before whichever of `export_document_node` / `create_ado_node` / `create_notion_node` is selected by `OUTPUT_MODE`, so jobs can pause for human clarification/review and resume later via dedicated endpoints. Because the checkpoint is on disk rather than only in process memory, a job's full state — including one paused mid-review or one that failed and hasn't been retried yet — survives a backend restart. Every node-to-node edge is conditional on `status`: if any node fails and sets `status == "error"`, the graph routes straight to `END` instead of letting downstream nodes run against incomplete state.

## Project structure

```
backend/
  api/
    main.py                 FastAPI app factory, CORS, lifespan (job registry reload, file watcher), router registration (all under /api), /health
    job_registry.py         In-memory registry for /api/assess jobs (list + metadata)
    ingest_jobs.py           In-memory registry for /api/ingest jobs (progress + status)
    routers/
      assess.py              POST /api/assess, POST /api/assess/rerun/{job_id}, POST /api/assess/retry/{job_id}, GET /api/assess/jobs, GET /api/assess/status/{job_id}
      clarify.py              POST /api/clarify/answer/{job_id}
      review.py               POST /api/review/approve/{job_id}
      ado.py                  GET /api/ado/status/{job_id}
      export.py                GET /api/export/document/{job_id}
      ingest.py                POST /api/ingest/pdfs, POST /api/ingest/code, GET /api/ingest/status/{job_id}
      settings.py              GET/PUT /api/settings -- both StoryForge's and CodeMind's configuration
      monitoring.py             GET /api/monitoring/errors -- captures ERROR+ logs from every module in this process, StoryForge's and CodeMind's alike
      codemind_jobs.py          CodeMind's /api/v1/extraction-jobs* (see CodeMind section below)
      codemind_ask.py            CodeMind's SSE /api/v1/.../qa/stream and /api/v1/ask/stream
  scripts/
    setup_notion_database.py  One-off script: creates the Notion "StoryForge Epics" database, prints NOTION_DATABASE_ID
  pipeline/
    state.py                 StoryForgeState TypedDict + new_state() factory
    graph.py                 LangGraph StateGraph wiring + conditional error-routing
    runner.py                 start_job / resume_after_clarification / resume_after_review / get_job_state
    nodes/
      analyze.py              Node 1: PDF text extraction + parallel RAG retrieval
      clarify.py               Node 2: ambiguity detection via a local Ollama model (temperature=0, fixed seed), pauses graph if needed
      generate.py              Node 3: story/task generation via a local Ollama model (temperature=0, fixed seed)
      llm_retry.py              Shared retry helper for clarify.py/generate.py: retries with a nudged seed on transient failures or malformed JSON
      review.py                Node 4: human review pass-through gate
      create_ado.py            Node 5 (OUTPUT_MODE=ado): creates the Epic/Story/Task hierarchy via MCP
      export_document.py       Node 5 (OUTPUT_MODE=document, default): writes the same hierarchy to a .docx
      create_notion.py         Node 5 (OUTPUT_MODE=notion): creates one Epic page per story in a Notion database
  ingestion/
    chroma_client.py          ChromaDB + Ollama embeddings singletons, 3 collections
    ingest_pdfs.py             PDF chunking + embedding into sf_user_manuals
    ingest_code.py             Java/TypeScript/JavaScript "smart chunking" + embedding
  ado_mcp/
    ado_client.py              MultiServerMCPClient wrapper for the ADO MCP server
  notion_export/
    client.py                  notion-client AsyncClient wrapper: page/block creation, 100-block batching, rich_text chunking
  prompts/
    system_prompt.py           Full generate_node system prompt + JSON output schema
  codemind/                  CodeMind's own package -- see CodeMind section below for a per-module breakdown
  config.py                   Settings loaded from environment / .env (StoryForge's and CodeMind's alike)
  requirements.txt
  .env.example

frontend/storyforge-ui/
  src/app/
    pages/
      landing/                 "/" -- app picker (StoryForge / CodeMind cards)
      dashboard/               "/ai-ba" -- job list (PPM number/name, status, story count)
      assess/                  New assessment submission form (PDF upload)
      clarify/                 Answer clarification questions
      review/                  Edit/approve generated stories before document export / ADO creation
      status/                  Poll job status, stepper, ADO results table (OUTPUT_MODE=ado) or Notion pages table (OUTPUT_MODE=notion), and (once done) a read-only stories/tasks text panel with copy + document-download buttons
      settings/                 "/settings" -- one page for both StoryForge's and CodeMind's configuration
      monitoring/                "/monitoring" -- merged error feed for both flows
      codemind/
        jobs-list/               "/codemind" -- start-job form + jobs table
        job-detail/               "/codemind/:jobId" -- progress stepper, stats, file feed, viewer modal
        job-ask/                   "/codemind/:jobId/ask" -- per-job SSE Q&A chat
        ask-all/                   "/codemind/ask" -- cross-job SSE Q&A chat
    services/
      storyforge.service.ts    HTTP client for StoryForge's own backend API
      codemind.service.ts       HTTP + SSE client for CodeMind's endpoints
      settings.service.ts        Unified settings GET/PUT
      monitoring.service.ts      Unified error feed
    app.routes.ts              SPA route table
```

## Prerequisites

- Python 3.11+
- Node.js 18+ and npm (for the Angular frontend)
- Node.js (separately) for the Azure DevOps MCP server process the backend spawns over stdio
- [Ollama](https://ollama.com) running locally with an embedding model pulled (default: `nomic-embed-text`) and a chat model pulled for clarify/generate (default: `qwen2.5:14b`)
- Azure DevOps organization/project + a PAT-backed MCP server binary that implements `create_epic` / `create_user_story` / `create_task` (required only when `OUTPUT_MODE=ado`)
- A Notion account with an internal integration token and a parent page shared with that integration (required only when `OUTPUT_MODE=notion`)

## Setup

### Backend setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env — see Configuration reference below
```

### Frontend setup

```bash
cd frontend/storyforge-ui
npm install
```

### Ollama setup

```bash
ollama pull nomic-embed-text
ollama serve   # if not already running
```

### Azure DevOps MCP server

The backend connects to a local Node.js MCP server over stdio (spawned as `node <MCP_SERVER_PATH>`) that must expose three tools mirroring the ADO work item hierarchy:

| Tool | Args | Returns |
|---|---|---|
| `create_epic` | `title, description, tags` | `{"id": ..., "url": ...}` |
| `create_user_story` | `parent_id, title, description, tags` | `{"id": ..., "url": ...}` |
| `create_task` | `parent_id, title, description, tags, activity` | `{"id": ..., "url": ...}` |

Point `MCP_SERVER_PATH` at this server's entry script, and set `ADO_ORGANIZATION` / `ADO_PROJECT` (passed to the server process as environment variables).

### Notion setup

Only needed when `OUTPUT_MODE=notion`:

1. Create a [Notion internal integration](https://www.notion.so/my-integrations) and copy its secret into `NOTION_API_KEY`.
2. Share a parent page in your Notion workspace with that integration (the page's `•••` menu → "Connections" → add the integration), and set its page ID as `NOTION_PARENT_PAGE_ID`.
3. Run the one-off setup script to create the "StoryForge Epics" database under that parent page:
   ```bash
   cd backend
   python -m scripts.setup_notion_database
   ```
4. Copy the printed database ID into `NOTION_DATABASE_ID`.

`create_notion_node` then creates one Epic page per generated story in that database on every job, with Dev Tasks and Unit Test Tasks rendered as nested heading/paragraph/bulleted-list blocks in the page body (mirroring the `.docx` export's structure).

## Configuration reference

All backend configuration is environment-variable driven (`backend/.env`, loaded via `python-dotenv`). See `backend/config.py` for defaults. Everything below can also be edited from the `/settings` screen in the UI (one screen covers both StoryForge's and CodeMind's fields), which writes back to `.env` and applies the change to the running backend immediately — no restart needed for most fields (see [`config.py`](backend/config.py)'s `Settings.apply_updates` and [`config_store.py`](backend/config_store.py); a few CodeMind fields still require a restart, flagged as `restart_required_fields` in the `GET /api/settings` response).

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(none)_ | Not called anywhere in *this* pipeline (`clarify_node`/`generate_node` use `OLLAMA_LLM_MODEL` instead) — but it **is** required for CodeMind's default (Claude) extraction/Ask agent, see [CodeMind](#codemind) below |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Not used by this pipeline; used by CodeMind as the Claude model for extraction/Ask (exposed as `anthropic_model` on the settings screen) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL, shared by StoryForge's embeddings/clarify/generate and CodeMind's optional Ollama agent/QA/embeddings — one physical server for both flows |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model name, shared by StoryForge's RAG and CodeMind's optional vector search |
| `OLLAMA_LLM_MODEL` | `qwen2.5:14b` | Chat model used by `clarify_node` and `generate_node`, both run at `temperature=0` with a fixed seed for reproducible output across runs of the same SDD |
| `CHROMA_PERSIST_PATH` | `./chroma_db` | On-disk path for the persistent ChromaDB store |
| `MCP_SERVER_PATH` | _(empty)_ | Path to the ADO MCP server's Node.js entry script |
| `ADO_ORGANIZATION` | _(empty)_ | Azure DevOps organization name, passed to the MCP server |
| `ADO_PROJECT` | _(empty)_ | Azure DevOps project name, passed to the MCP server |
| `NOTION_API_KEY` | _(empty)_ | Notion internal integration secret, used when `OUTPUT_MODE=notion` |
| `NOTION_DATABASE_ID` | _(empty)_ | ID of the "StoryForge Epics" database `create_notion_node` writes pages into — created once via `python -m scripts.setup_notion_database`. `notion_export/client.py` resolves and caches the database's *data source* ID from this at runtime (Notion API 2025-09-03+ requires pages to be parented by a data source, not a database, directly) — you only ever configure the database ID here |
| `NOTION_PARENT_PAGE_ID` | _(empty)_ | ID of the Notion page (shared with the integration) under which `scripts/setup_notion_database.py` creates the database; only needed to run that script |
| `CORS_ORIGINS` | `http://localhost:4200` | Comma-separated list of allowed CORS origins |
| `JWT_SECRET` | _(auto-generated)_ | Signs/verifies the login JWT used by every route in this app (StoryForge's and CodeMind's alike). Leave unset for local dev — a random value is generated and persisted to `JOBS_DIR/.jwt_secret` so restarts don't invalidate logins. Set explicitly before exposing the app beyond local testing |
| `JOBS_DIR` | `./jobs` | Holds `checkpoints.sqlite` (LangGraph's persisted pipeline state), `assess_jobs.json` (the dashboard's job list), `users.json`, `.jwt_secret`, and CodeMind's `codemind_jobs/` (one JSON file per extraction job) — all survive a backend restart |
| `UPLOADS_DIR` | `./uploads` | Directory uploaded SDD PDFs are saved to (`{job_id}.pdf`) |
| `EXPORTS_DIR` | `./exports` | Directory generated `.docx` files are saved to (`{ppm_number}_{ppm_name}_{system_name}_V_{n}.docx`, sanitized and versioned per project), used when `OUTPUT_MODE=document` |
| `PROMPT_VARIANT` | `production` | `production` uses `prompts/system_prompt.py`. `selftest` swaps in `prompts/system_prompt_selftest.py`, a variant tuned for assessing SDDs about StoryForge AI's own codebase (Python/FastAPI/LangGraph/Angular) instead of the default telecom/Spring Boot domain assumptions. |
| `OUTPUT_MODE` | `document` | The **default** task-management system for new assessments — `document` writes approved stories to a `.docx` via `export_document_node`, downloadable from `GET /export/document/{job_id}`; `ado` pushes to Azure DevOps via `create_ado_node`; `notion` pushes to a Notion database via `create_notion_node`. All three nodes are registered unconditionally; only the routing picks one. Each job can override this default via the dropdown on the Assess form — the choice is stamped onto the job as `state["output_mode"]` at submission time, so changing this default later doesn't affect already-submitted jobs |

CodeMind's own `CODEMIND_*` variables (extraction agents, execution mode, Ask model, vector
search, the directory watcher, and its output directory) are documented in
[`.env.example`](backend/.env.example) and the [CodeMind](#codemind) section below, rather
than duplicated in this table.

## Running the app

**Backend** (from `backend/`, with the virtualenv active):

```bash
uvicorn api.main:app --reload --port 8000
```

**Frontend** (from `frontend/storyforge-ui/`):

```bash
npm start              # ng serve, http://localhost:4200
```

Production build:

```bash
ng build
npx serve -s dist/storyforge-ui/browser -l 4300   # -s enables SPA history-mode fallback
```

> Plain static file servers without SPA fallback (e.g. `http-server` without `-s`) will 404 on direct navigation to client-side routes like `/assess` or `/status/:jobId`.

## One-time ingestion

Before running assessments, index your manuals and codebase:

```bash
curl -X POST http://localhost:8000/api/ingest/pdfs -H "Content-Type: application/json" \
  -d '{"folder_path": "/path/to/user-manuals"}'

curl -X POST http://localhost:8000/api/ingest/code -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/monorepo"}'
```

Both return `{"job_id": ..., "status": "pending"}` immediately; poll `GET /api/ingest/status/{job_id}` for progress/completion. Re-running an ingestion job does not deduplicate against previous runs — chunks are simply re-added.

**Code chunking rules** (`backend/ingestion/ingest_code.py`):
- **Java** — chunked by whole class AND by individual method. Files annotated `@Entity` are indexed into both `sf_codebase` and `sf_jpa_entities`. Layer is inferred from `@RestController`/`@Service`/`@Repository`/`@Entity`/`@Configuration` annotations. `*Test.java` and `*IT.java` files are excluded.
- **TypeScript/Angular** — chunked by `@Component`/`@Injectable`/`@NgModule`/`@Directive`/`@Pipe` decorated class. Files with no decorator are chunked whole as a service/utility file. `*.spec.ts` files are excluded.
- **JavaScript/jQuery** — chunked by function (`function foo(){}`, arrow function assignments, and `$.fn.x = function(){}` jQuery plugins). Falls back to whole-file chunking if no functions are detected.
- **HTML** is never indexed. `node_modules/`, `target/`, `dist/`, and `.git/` directories are always skipped.
- Any chunk exceeding ~1500 tokens (~6000 chars) is further split with `RecursiveCharacterTextSplitter` (150-token overlap).

## API reference

All endpoints are served under the FastAPI app created in `backend/api/main.py`, with all routers registered under an `/api` prefix (so a reverse proxy can route by path prefix). `/health` is also exposed unprefixed for direct container healthchecks.

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` (also `/api/health`) | Liveness check → `{"status": "ok"}` |
| `POST` | `/api/auth/login` | Body: `{"username", "password"}` → `{"access_token", "username", "role"}`. This one JWT is trusted by every route in this app, StoryForge's and CodeMind's alike |
| `POST` | `/api/auth/logout` | Stateless — nothing to invalidate server-side; the client just drops the token |
| `GET` | `/api/auth/me` | → the decoded `{"username", "role"}` for the caller's current token |
| `POST` | `/api/ingest/pdfs` | Body: `{"folder_path": str}`. Starts background PDF ingestion → `{"job_id", "status": "pending"}` |
| `POST` | `/api/ingest/code` | Body: `{"repo_path": str}`. Starts background code ingestion → `{"job_id", "status": "pending"}` |
| `GET` | `/api/ingest/status/{job_id}` | → `{"status", "progress", "errors", "result"}` (`result` is `null` until the job finishes, then holds `files_processed`/`chunks_indexed`/etc.). 404 if unknown |
| `POST` | `/api/assess` | Multipart form: `file` (PDF), `ppm_number`, `ppm_name`, `system_name`, `review_mode` (bool, default `false`), `output_mode` (optional — `document`/`ado`/`notion`; defaults to `settings.OUTPUT_MODE` if omitted). Starts the pipeline in the background → `{"job_id"}`. `output_mode` is stamped onto the job (`state["output_mode"]`) at submission time, so each job keeps using the system it was created with even if the global default in Settings changes later |
| `GET` | `/api/assess/jobs` | List all submitted assessment jobs with live `status` + `story_count` + `output_mode` |
| `GET` | `/api/assess/status/{job_id}` | Full `StoryForgeState` for the job (including `output_mode`). 404 if unknown |
| `POST` | `/api/assess/retry/{job_id}` | Re-runs just the pipeline step that failed (`generate_node`, or whichever `create_ado_node`/`export_document_node`/`create_notion_node` the job's `output_mode` selects) without redoing earlier work (SDD parsing, RAG retrieval, clarification, generation, or review that already succeeded). Distinct from `llm_retry.py`'s per-LLM-call retries inside a single node — this is a user-triggered retry of an entire node after it already exhausted those and left the job in `status: "error"`. 404 if unknown, 409 if the job isn't in an `error` status or the failure isn't resumable (e.g. it failed inside `analyze_node`, before the first checkpoint — submit a new assessment instead). See [`pipeline/runner.py`](backend/pipeline/runner.py)'s `retry_failed_step` |
| `POST` | `/api/assess/recreate/{job_id}` | Pushes a **completed** job's approved stories to Notion/ADO again from scratch — shown on the status page as "Re-create tasks in Notion/ADO" once a job with `output_mode` `notion`/`ado` reaches `done`. Notion: archives the job's previously-created pages first, then creates fresh ones. ADO: no delete capability exists (the external MCP server only exposes `create_*` tools), so this just creates a fresh Epic/Story/Tasks hierarchy alongside whatever was created before. 404 if unknown, 409 if the job isn't `done` yet or its `output_mode` is `document` (nothing to "recreate" — it's just a file). See [`pipeline/runner.py`](backend/pipeline/runner.py)'s `recreate_tasks` |
| `POST` | `/api/assess/update/{job_id}` | Updates a **completed** Notion job's existing pages in place (position-matched against the approved stories/tasks) instead of archiving and recreating them — shown as "Update tasks" alongside "Re-create tasks" for `output_mode=notion` jobs only (ADO has no update/delete tool in its MCP wrapper). 404 if unknown, 409 if the job isn't `done` or isn't Notion. See [`pipeline/runner.py`](backend/pipeline/runner.py)'s `update_tasks` |
| `POST` | `/api/clarify/answer/{job_id}` | Body: `{"answers": {question: answer}}`. 409 if job isn't awaiting clarification. Resumes the pipeline → `{"status": "generating"}` |
| `POST` | `/api/review/approve/{job_id}` | Body: `{"approved_stories": [...]}`. 409 if job wasn't run with `review_mode=true`. Resumes the pipeline → `{"status": "creating"}` |
| `GET` | `/api/ado/status/{job_id}` | → `{"ado_results", "errors"}`. 404 if unknown |
| `GET` | `/api/export/document/{job_id}` | Downloads the generated `.docx` (`output_mode=document`). 404 if the job is unknown or the document isn't generated yet. Linked from the status page via the "Download Document" button shown once the job reaches `done` |
| `GET` | `/api/settings` | Current configuration for **both** StoryForge and CodeMind, read from `backend/.env`. Secrets (`notion_api_key`, `anthropic_api_key`) are masked (last 4 chars only, e.g. `"…q5t9"`) — never returned in plaintext. Also returns `restart_required_fields`, the subset of CodeMind fields that need a process restart to take effect |
| `PUT` | `/api/settings` | Body: any subset of the fields returned by `GET /api/settings`, plus optionally `notion_api_key`/`anthropic_api_key` (a real new value — omitting it, or sending back the mask unchanged, leaves the current secret untouched). Writes changed fields to `backend/.env` and applies them to the running backend immediately for the fields that support hot-reload — see [`config.py`](backend/config.py)'s `Settings.apply_updates` and [`config_store.py`](backend/config_store.py). Returns the refreshed (masked) settings |
| `GET` | `/api/monitoring/errors` | Every `ERROR`+-level log record captured from this process since startup (StoryForge's and CodeMind's modules alike — see [`monitoring/log_capture.py`](backend/monitoring/log_capture.py)), ring-buffered to the most recent N. Backs the `/monitoring` page's combined error feed |

CodeMind's own endpoints (`/api/v1/extraction-jobs*`, `/api/v1/ask/stream`) are documented in
the [CodeMind](#codemind) section below, not duplicated in this table.

## Generated story JSON schema

`generate_node` (see `backend/prompts/system_prompt.py` for the full prompt) asks the local model to return a JSON array, one object per distinct feature in the SDD:

```jsonc
[
  {
    "epic_title": "string",
    "user_story": "As a [role], I want [goal], so that [benefit]",
    "acceptance_criteria": ["Given... When... Then..."],
    "dev_tasks": [
      {
        "title": "[N] Task title",
        "user_story": "As a...",
        "acceptance_criteria": ["..."],
        "technical_approach": ["Step 1: ...", "Step 2: ..."],
        "affected_components": { "frontend": "...", "backend": "...", "middleware": "...", "database": "..." },
        "api_contract": { "endpoint": "...", "request": {}, "response_success": {}, "response_error": {}, "status_codes": [] },
        "business_rules": ["Rule 1: ...", "..."],
        "error_handling": ["Scenario 1: ... -> ...", "..."]
      }
    ],
    "unit_test_tasks": [
      {
        "title": "[N] Unit Test - [matching dev task title]",
        "test_objective": "Verify that...",
        "test_scenarios": { "happy_path": ["TC-01: ..."], "negative": ["TC-02: ..."], "edge_cases": ["TC-03: ..."] },
        "test_data": { "valid": {}, "invalid": {} },
        "mock_setup": ["Mock [Service] to return [value] when called with [input]"],
        "assertions": ["Assert ..."]
      }
    ]
  }
]
```

Every `dev_tasks` entry has exactly one corresponding `unit_test_tasks` entry at the same array index. Affected layers that are genuinely untouched are marked `"N/A"` with justification rather than left ambiguous. The model is explicitly instructed never to fabricate file/class names — only logical roles.

## Pipeline state machine

`pipeline/graph.py` wires 7 nodes into a LangGraph `StateGraph`, checkpointed per `job_id` to `JOBS_DIR/checkpoints.sqlite` (survives a backend restart):

| Node | Sets `status` to | Notes |
|---|---|---|
| `analyze_node` | `analyzing` (or `error`) | Extracts SDD text via `pypdf`, retrieves top-10 chunks per collection in parallel |
| `clarify_node` | `clarifying` or `generating` | Asks the local model to flag ambiguities in 4 categories; retries on transient/parse failures ([`llm_retry.py`](backend/pipeline/nodes/llm_retry.py)), then fails open (proceeds without clarification) if all retries are exhausted |
| `generate_node` | `reviewing`/`creating` (or `error`) | Generates the full story/task JSON array; retries on transient/parse failures ([`llm_retry.py`](backend/pipeline/nodes/llm_retry.py)), then sets `error` if all retries are exhausted |
| `review_node` | `reviewing` or `creating` | Pass-through when `review_mode` is off; otherwise waits for human edits |
| `export_document_node` | `done` (or `error`) | Default (`output_mode=document`): renders the approved hierarchy to a `.docx` via `python-docx`, saved to `EXPORTS_DIR/{job_id}.docx` |
| `create_ado_node` | `done` (or `error`) | `output_mode=ado`: creates Epic → User Story → Tasks via the MCP client; one story failing doesn't abort the rest |
| `create_notion_node` | `done` (or `error`) | `output_mode=notion`: creates one Epic page per story in the Notion database (`NOTION_DATABASE_ID`) via `notion-client`, with Dev/Unit-Test tasks rendered as nested blocks; one story failing doesn't abort the rest |

After `review_node`, the graph branches on `state["output_mode"]` (falling back to `settings.OUTPUT_MODE` for checkpoints persisted before this per-job field existed) to reach `export_document_node`, `create_ado_node`, or `create_notion_node` — all three are registered unconditionally so the mode can be flipped at runtime without recompiling the graph. The graph **interrupts before** `generate_node` and before whichever of the three is reachable. `pipeline/runner.py`'s `_drive` loop auto-resumes past an interrupt when no human input is actually required (no ambiguities found / `review_mode=False`), and stops cleanly at a genuine human-in-the-loop pause otherwise. Every edge between nodes is conditional: a node that sets `status == "error"` routes straight to `END`, so a failure can never be silently overwritten back to `"done"` by a downstream node running on empty input.

Once a job reaches `done` with `output_mode` `notion` or `ado`, `POST /api/assess/recreate/{job_id}` (`pipeline/runner.py`'s `recreate_tasks`) can push the approved stories to that system again from scratch — for Notion it archives the job's previously-created pages first via `NotionExportClient.archive_page`; for ADO there's no delete capability available (the external MCP server only exposes `create_*` tools), so it just creates a fresh hierarchy. It reuses the same checkpoint-rewind mechanism as retry: `aupdate_state(..., as_node=NODE_REVIEW)` followed by `_drive`.

If `generate_node` or a create/export node fails outright (all its `llm_retry.py` attempts exhausted, or a non-LLM error like a bad `NOTION_DATABASE_ID`), `POST /api/assess/retry/{job_id}` rewinds the checkpoint to right before the failed node and re-runs just that node, reusing everything computed before it (see the API reference above). This isn't idempotent for the create/export nodes: they isolate failures per-item (one story failing doesn't abort the rest), so retrying after a *partial* failure re-creates every approved story/task from scratch, including ones that already succeeded on the first attempt — expect duplicate ADO work items / Notion pages / a re-generated document for those. A failure inside `analyze_node` has no earlier checkpoint to rewind to, so it isn't retryable this way — submit a new assessment instead.

`job_id` doubles as the LangGraph checkpoint `thread_id`, so `get_job_state(job_id)` can always retrieve the latest state for polling, and `resume_after_clarification` / `resume_after_review` patch state via `aupdate_state` before resuming.

## Frontend pages

| Route | Component | Purpose |
|---|---|---|
| `/login` | Login | Username/password form; stores the returned JWT and redirects to `/` |
| `/` | Landing | App picker — "AI Business Analyst" and "CodeMind" cards |
| `/ai-ba` | Dashboard | Lists all submitted jobs with live status + story count |
| `/assess` | Assess | Form to submit a new SDD PDF + PPM metadata + task-management-system dropdown (document/Notion/ADO, defaulting to the Settings screen's global default) + review mode toggle |
| `/clarify/:jobId` | Clarify | Displays clarification questions, submits answers to resume the pipeline |
| `/review/:jobId` | Review | Displays generated stories for human editing/approval before ADO creation |
| `/status/:jobId` | Status | Polls job status, shows a step progress indicator, the final ADO work item results table or Notion pages table (depending on the job's own `output_mode`), "Re-create tasks"/"Update tasks" buttons once a `notion`/`ado` job reaches `done`, and a read-only formatted stories/tasks panel with copy-to-clipboard and a Download Document button |
| `/settings` | Settings | One page for both StoryForge's configuration (Ollama base URL/model/embed model, prompt variant, task-management-system fields) and CodeMind's (Anthropic key/model, Ollama agent toggle, execution mode, Ask model, vector search toggle), persisted to `backend/.env` and applied to the running backend immediately for most fields |
| `/monitoring` | Monitoring | Combined error feed for both StoryForge and CodeMind, tagged by which app logged each entry |
| `/codemind`, `/codemind/:jobId`, `/codemind/:jobId/ask`, `/codemind/ask` | CodeMind pages | See [CodeMind](#codemind) below |
| `**` | — | Redirects to `/` |

## CodeMind

CodeMind scans a repository, sends each source file to an LLM with a structured
extraction prompt, and stores the resulting rules/summary/dependencies as JSON — one
result file per source file. It was originally a standalone Java/Spring Boot service
(`code-mind-app/`) and was ported into this same Python backend (see `git log` for the
merge history if you're curious) to get both apps onto one tech stack and one process.
Functionally nothing changed for end users: same REST/SSE contract, same job model, same
extraction behavior — the differences below are internal.

### How it works

1. **Start a job** — `POST /api/v1/extraction-jobs` with a `repositoryPath` (and optional
   `outputDirectory`/`maxConcurrency`/`executionMode`). The job is registered and its
   scan/extraction runs as a FastAPI background task, not on the request thread.
2. **Scan + filter** — `codemind/scanner.py` walks the tree (respecting included
   extensions/excluded directories), `codemind/chunker.py` splits any file over the
   size threshold at safe line boundaries into `part-NNNN` virtual files instead of
   skipping it, and `codemind/filter.py` skips non-substantive files (`.d.ts`,
   test/spec files, barrel/re-export-only files) before any LLM call.
3. **Extract** — `codemind/orchestrator.py` fans the eligible files out across the
   registered agents (`codemind/agents/`) with an `asyncio.Semaphore(max_concurrency)`
   bounding concurrent LLM calls. Each file's result (or per-file failure — one bad
   file never aborts the job) is written to `{outputDirectory}/{relativePath}.json` by
   `codemind/output.py`.
4. **Ask** — `codemind/qa.py` retrieves the extraction results most relevant to a
   question (real vector search via Ollama embeddings when enabled, otherwise
   keyword-overlap scoring) and asks an LLM to answer grounded in only that context,
   streamed back over SSE.
5. **Incremental re-runs** — `codemind/manifest.py` content-hashes source files per
   repository; a job started with `incremental=true` (auto-detected when a manifest
   already exists for that repo path) only re-processes changed/added files and removes
   output for deleted ones.

### Module layout (`backend/codemind/`)

| Module | Responsibility |
|---|---|
| `scanner.py`, `chunker.py` | Directory/file walking, extension/exclusion filtering, large-file line-boundary chunking |
| `filter.py` | Non-substantive-file pre-filter (test files, `.d.ts`, barrel files) |
| `manifest.py` | SHA-256 content-hash manifest for incremental re-runs |
| `prompts.py` | Extraction prompt builder (per-language hints + JSON output instructions) |
| `agents/base.py` | `ExtractionResult` (camelCase on-disk JSON shape) + the `LogicExtractionAgent` protocol |
| `agents/claude_agent.py`, `agents/ollama_agent.py` | Per-file extraction via `ChatAnthropic`/`ChatOllama`; single attempt, catches all exceptions into a failure result (per-file isolation) |
| `agents/selector.py` | Round-robins across whichever agents are configured (`build_agents()`/`get_agent_selector()`) — Claude registers iff `ANTHROPIC_API_KEY` is set, Ollama iff `CODEMIND_OLLAMA_ENABLED=true` |
| `qa.py` | Ask/Ask-All retrieval + answer generation (keyword or vector search) |
| `orchestrator.py` | `ExtractionJob`/`JobPhase` + the scan → filter → fan-out → complete pipeline for one job |
| `job_store.py`, `job_registry.py` | Per-job JSON persistence under `JOBS_DIR/codemind_jobs/`, and the in-memory registry reloaded at startup (non-terminal jobs found on disk are marked `FAILED` with `"Interrupted at server restart"`, matching a real restart's effect on any in-flight fan-out) |
| `batch.py` | `BATCH` execution mode via the raw `anthropic.AsyncAnthropic` SDK's Message Batches API (50% token discount, built for large repos; polls every 30s with a 26h timeout) |
| `watch.py` | Optional non-recursive directory watcher (`watchdog`) that auto-starts one job per dropped file, debounced |
| `output.py` | Reads/writes per-file result JSON + job summary JSON, and lists recent/failed output files for the UI |

### Execution modes

- **SYNC** (default) — per-file calls through the registered agents, bounded by
  `maxConcurrency` (per-job, defaults to 8). Keep this at or below
  `OLLAMA_NUM_PARALLEL × (agent count)` if using the Ollama agent, to avoid requests
  queuing on the Ollama side.
- **BATCH** — always uses the Anthropic Batches API (Claude only, regardless of
  `CODEMIND_OLLAMA_ENABLED`) for a flat 50% token discount, at the cost of higher
  latency (results arrive once the whole batch completes, not per-file).

### REST/SSE endpoints (`api/routers/codemind_jobs.py`, `api/routers/codemind_ask.py`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/extraction-jobs` | Start a job. Body: `{"repositoryPath", "outputDirectory"?, "maxConcurrency"?, "executionMode"?}` → 202 + the job |
| `GET` | `/api/v1/extraction-jobs` | List all jobs, newest first |
| `GET` | `/api/v1/extraction-jobs/{id}` | Job status/progress |
| `POST` | `/api/v1/extraction-jobs/{id}/cancel` | Soft-cancel — in-flight LLM calls finish, no new ones start |
| `DELETE` | `/api/v1/extraction-jobs/{id}` | Delete a job + its output files |
| `DELETE` | `/api/v1/extraction-jobs` | Clear every job + all output data |
| `GET` | `/api/v1/extraction-jobs/{id}/output-files` | Most-recently-modified output files (50 max) |
| `GET` | `/api/v1/extraction-jobs/{id}/output-file?relativePath=...` | Read one output file's raw JSON |
| `GET` | `/api/v1/extraction-jobs/{id}/failed-files` | Files where extraction failed, with the error |
| `GET` | `/api/v1/extraction-jobs/{id}/export` | Download every successful extraction for the job as one merged JSON file |
| `POST` | `/api/v1/extraction-jobs/{id}/qa` | One-shot (non-streaming) Ask for this job |
| `POST` | `/api/v1/extraction-jobs/{id}/qa/stream` | SSE-streamed Ask for this job (`event: sources` then `event: chunk`) |
| `POST` | `/api/v1/ask/stream` | SSE-streamed Ask across every completed job at once |

### Configuration

See [`.env.example`](backend/.env.example) for the full `CODEMIND_*` list
(`CODEMIND_OLLAMA_ENABLED`/`_MODEL`, `CODEMIND_EXECUTION_MODE`, `CODEMIND_QA_MODEL`,
`CODEMIND_EMBEDDING_ENABLED`, `CODEMIND_DEFAULT_OUTPUT_DIRECTORY`,
`CODEMIND_WATCH_ENABLED`/`_DIRECTORY`/`_QUIET_PERIOD_MILLIS`) — all editable from
`/settings` except the watcher, which is startup-only (see `api/main.py`'s `lifespan`).

## Testing

```bash
cd backend
source venv/bin/activate
pytest tests/ -q
```

Run against `fastapi.testclient.TestClient` for the routers, mocked chat/embedding clients
for LLM-touching logic, and the real LangGraph engine (with node functions monkeypatched)
for StoryForge's orchestration. `tests/codemind/` covers CodeMind's modules the same way —
mocked `ChatAnthropic`/`ChatOllama`/embeddings, an injectable `AsyncAnthropic` client for
Batch mode, and `run_job`/`get_agent_selector` monkeypatched in the file-watcher tests. No
test makes a real call to Anthropic, Ollama, ChromaDB persistence beyond a scratch path,
the ADO MCP server, the Notion API, or the Anthropic Batches API — all are mocked/stubbed.

Key things to verify after making changes:
- `backend/pipeline/graph.py` orchestration: clarification + review pause/resume across all 4 combinations of `clarification_needed` × `review_mode`
- Error propagation: a node failure (`status == "error"`) must stop the graph rather than being masked by a downstream node finishing on empty input
- `backend/ingestion/ingest_code.py` chunking rules against representative Java/TypeScript/JavaScript fixtures
- CodeMind's `orchestrator.py` concurrency (no more than `max_concurrency` extractions in flight) and per-file fault isolation (one failing file doesn't sink the job)
- All FastAPI routers' status codes (`404`/`409`/`422`) and response shapes
- Frontend build (`ng build`) and a smoke pass over all routes with the backend unreachable (should degrade gracefully, no console errors)

There is currently no automated test (nor a CI-run manual check) against the real
Anthropic Message Batches API — verify `CODEMIND_EXECUTION_MODE=BATCH` manually against a
real repo before relying on it in production.

## Troubleshooting

- **`ModuleNotFoundError` involving `mcp`** — the backend's own `ado_mcp/` package was deliberately renamed from `mcp/` because a directory literally named `mcp` on `PYTHONPATH` shadows the third-party `mcp` SDK package required by `langchain-mcp-adapters`. Make sure nothing reintroduces a local `mcp/` directory.
- **`ModuleNotFoundError` or attribute errors involving `notion`** — same pattern: the backend's own package is named `notion_export/`, not `notion/`, so it doesn't shadow the third-party `notion_client` package's import name. Make sure nothing reintroduces a local `notion/` directory.
- **`RuntimeError: NOTION_API_KEY / NOTION_DATABASE_ID is not set`** — `OUTPUT_MODE=notion` requires both; run through [Notion setup](#notion-setup) first, including `scripts/setup_notion_database.py` to obtain `NOTION_DATABASE_ID`.
- **`notion_client.errors.APIResponseError: Task is not a property that exists. Status is not a property that exists.`** — the target database wasn't actually created with the expected schema. Notion API 2025-09-03+ moved a database's property schema under its *data source*, so `databases.create()` needs `initial_data_source={"properties": ...}`, not a top-level `properties` kwarg (the SDK silently drops an unrecognized top-level kwarg instead of erroring) — re-run `scripts/setup_notion_database.py` on the current code to create a database with the right schema, and update `NOTION_DATABASE_ID` to the new one it prints.
- **After changing `.env`, the app still behaves like the old value** — confirm the *file* actually has the new value (`grep VARNAME .env`) before assuming it's a code/caching issue; a `sed`/editor edit that silently failed to save is a far more common cause than `load_dotenv()` or `@lru_cache` misbehaving. Also confirm you're in the activated `venv` (`which python3` should point inside `backend/venv/`) and that you fully restarted the process — `uvicorn --reload` watches `.py` files, not `.env`.
- **`ImportError: cannot import name 'RecursiveCharacterTextSplitter' from 'langchain.text_splitter'`** — that module was removed; both ingestion modules import from `langchain_text_splitters` instead (listed explicitly in `requirements.txt`).
- **`ChatAnthropic` raises `ValueError` on attribute assignment** — it's a Pydantic model with strict field validation; don't monkeypatch its methods directly in tests, replace the module-level `_llm` reference instead.
- **A job's `status` shows `done` with empty `generated_stories`/`ado_results`** — check `errors` in the job state; this indicates an upstream node failed. The graph now routes failures straight to `END`, so this should only surface for jobs run before the error-routing fix.
- **404 on direct navigation to a frontend route in a static build** — your static file server needs SPA history-mode fallback (e.g. `serve -s`).
- **A CodeMind extraction job completes with `failedFiles` > 0 and the file viewer shows `Error code: 401 - invalid x-api-key`** — `ANTHROPIC_API_KEY` is unset/invalid. This is a per-file failure (the job itself still reaches `COMPLETED`), so check the specific file's error in the viewer or `GET .../failed-files` rather than assuming a crash; set a valid key via `.env` or the `/settings` screen and re-run (`incremental` mode will only retry the files that failed, once a manifest exists).
- **A CodeMind job immediately shows `FAILED` with `"No LogicExtractionAgent beans configured"`** — neither extraction agent is configured (`ANTHROPIC_API_KEY` unset *and* `CODEMIND_OLLAMA_ENABLED=false`). Set `ANTHROPIC_API_KEY` and/or `CODEMIND_OLLAMA_ENABLED=true` (via `.env` or the `/settings` screen) and start a new job — the failed one won't recover on its own. The same message also appears on the `/monitoring` page. (Older builds of this port left the job stuck at `PENDING` forever instead of marking it `FAILED` in this case — fixed in `api/routers/codemind_jobs.py`'s and `codemind/watch.py`'s job-crash handlers.)
