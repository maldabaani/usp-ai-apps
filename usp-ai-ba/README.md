# StoryForge AI

StoryForge AI turns a Solution Design Document (SDD) PDF into a fully-detailed, ready-to-create Azure DevOps work item hierarchy — **Epic → User Story → Dev Tasks + Unit Test Tasks** — using a local Ollama model (`qwen2.5:14b` by default) for analysis/generation, the same Ollama server for embeddings + ChromaDB for retrieval-augmented context (RAG) over your existing codebase, user manuals, and JPA entities, and a human-in-the-loop review/clarification workflow before anything is written to ADO.

That same ChromaDB corpus — one code repository and its manuals, ingested once — also
answers standing questions from two other audiences: **Ask Technical** (development team,
cites source file paths) and **Ask Business** (business team, plain-language answers, no
code references). All three features share one ingestion pipeline, one FastAPI process,
one `/settings` screen, and one error-monitoring feed (see [Ask Technical / Ask
Business](#ask-technical--ask-business) below).

> `clarify_node` and `generate_node` (StoryForge's own pipeline, below) run at
> `temperature=0` with a fixed seed, so the same SDD produces the same clarification
> questions and the same generated stories on every run. Both nodes retry (with a nudged
> seed) on transient failures or malformed JSON before giving up — see
> [`backend/pipeline/nodes/llm_retry.py`](backend/pipeline/nodes/llm_retry.py).
> `ANTHROPIC_API_KEY`/`CLAUDE_MODEL` are configured in `config.py` but are not called
> anywhere in *this pipeline* — Claude is used elsewhere in this same backend, by the
> ingestion pipeline's optional LLM-summary enrichment tier and by Ask Technical/Business
> (see [Ask Technical / Ask Business](#ask-technical--ask-business) below).

It is a two-part application:

- **Backend** — Python / FastAPI. StoryForge's assessment flow is orchestrated by a
  [LangGraph](https://github.com/langchain-ai/langgraph) state machine; ingestion
  (`ingestion/` package) is a simpler per-file fan-out job model, `asyncio`-based
- **Frontend** — Angular 17+ standalone-component SPA

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
- [Ask Technical / Ask Business](#ask-technical--ask-business)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

## How it works

1. **Ingest once** — index your User Manual PDFs and codebase (16 recognized languages) into ChromaDB: mechanical structural chunking always runs, plus an optional per-file LLM-summary enrichment tier. Deterministic per-chunk IDs mean re-running ingestion updates in place rather than duplicating — safe to re-run whenever the source manuals/codebase change.
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
                                       ┌──────────────────────┼──────────────────────┐
                                       ▼                      ▼                      ▼
                     POST /api/ask/technical      POST /api/ask/business    SDD PDF ─▶ POST /api/assess
                     (dev team, cites file        (business team, plain          │
                      paths, SSE-streamed)         language, SSE-streamed)        ▼
                                                                       LangGraph pipeline (pipeline/graph.py)
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
    main.py                 FastAPI app factory, CORS, lifespan, router registration (all under /api), /health
    job_registry.py         In-memory registry for /api/assess jobs (list + metadata)
    ingest_jobs.py           In-memory registry for /api/ingest jobs (progress + status)
    routers/
      assess.py              POST /api/assess, POST /api/assess/rerun/{job_id}, POST /api/assess/retry/{job_id}, GET /api/assess/jobs, GET /api/assess/status/{job_id}
      clarify.py              POST /api/clarify/answer/{job_id}
      review.py               POST /api/review/approve/{job_id}
      ado.py                  GET /api/ado/status/{job_id}
      export.py                GET /api/export/document/{job_id}
      ingest.py                POST /api/ingest/documents, POST /api/ingest/code, GET /api/ingest/status/{job_id}
      ask.py                    POST /api/ask/technical, POST /api/ask/business, GET /api/ask/status (see Ask Technical / Ask Business below)
      settings.py              GET/PUT /api/settings
      monitoring.py             GET /api/monitoring/errors -- captures ERROR+ logs from every module in this process
      corpus.py                 GET /api/corpus/sources -- per-source chunk-count/LLM-summary/format metadata for the corpus browser
      watch.py                  GET/POST /api/watch/targets, PATCH/DELETE /api/watch/targets/{id} -- watched-path CRUD for auto re-ingestion
      prompts.py                 GET /api/prompts/ask, PUT /api/prompts/ask/{kind} -- Ask Technical/Business prompt customization
      conversations.py           GET/POST /api/conversations, GET/DELETE /api/conversations/{id} -- per-user conversation memory CRUD
    conversation_store.py       File-per-conversation persistence (<JOBS_DIR>/conversations/<owner>/<id>.json), scoped per-user by directory nesting
    ask_cache.py                In-memory exact-question-match answer cache for Ask Technical/Business, keyed by kind+ingestion-generation+prompt+conversation-context+question
  scripts/
    setup_notion_database.py  One-off script: creates the Notion "StoryForge Epics" database, prints NOTION_DATABASE_ID
  pipeline/
    state.py                 StoryForgeState TypedDict + new_state() factory
    graph.py                 LangGraph StateGraph wiring + conditional error-routing
    runner.py                 start_job / resume_after_clarification / resume_after_review / get_job_state
    nodes/
      analyze.py              Node 1: PDF text extraction + parallel RAG retrieval (via ingestion/retrieval.py)
      clarify.py               Node 2: ambiguity detection via a local Ollama model (temperature=0, fixed seed), pauses graph if needed
      generate.py              Node 3: story/task generation via a local Ollama model (temperature=0, fixed seed)
      llm_retry.py              Shared retry helper for clarify.py/generate.py: retries with a nudged seed on transient failures or malformed JSON
      review.py                Node 4: human review pass-through gate
      create_ado.py            Node 5 (OUTPUT_MODE=ado): creates the Epic/Story/Task hierarchy via MCP
      export_document.py       Node 5 (OUTPUT_MODE=document, default): writes the same hierarchy to a .docx
      create_notion.py         Node 5 (OUTPUT_MODE=notion): creates one Epic page per story in a Notion database
  ingestion/
    chroma_client.py          ChromaDB + Ollama embeddings singletons, 3 collections
    retrieval.py               Shared RAG retrieval over all 3 collections -- used by analyze_node and api/routers/ask.py
    ingest_documents.py        PDF/Word/Markdown/Confluence-export chunking + embedding into sf_user_manuals
    ingest_code.py             Mechanical structural chunking (16 languages) + embedding into sf_codebase/sf_jpa_entities
    enrichment/                Optional per-file LLM-summary enrichment tier (agents/, prompts.py, manifest.py for incremental skip)
    runner_jobs.py             Shared run_document_ingestion/run_code_ingestion wrappers used by both api/routers/ingest.py and the watcher
    watch_registry.py          Persisted watched-path targets ({id, path, kind, enabled}), same _load()/_save() idiom as api/job_registry.py
    watcher.py                 WatcherManager: one watchdog Observer per enabled target, per-target debounce, auto re-triggers ingestion on create/modify/delete
    ingestion_generation.py     In-memory-only counter bumped on every successful ingestion run, used by api/ask_cache.py to invalidate cached answers
  ado_mcp/
    ado_client.py              MultiServerMCPClient wrapper for the ADO MCP server
  notion_export/
    client.py                  notion-client AsyncClient wrapper: page/block creation, 100-block batching, rich_text chunking
  prompts/
    system_prompt.py           Full generate_node system prompt + JSON output schema
    ask_prompts.py              Ask Technical/Business system prompt templates (defaults)
  prompt_store.py              Persisted per-kind overrides of the Ask Technical/Business templates above, editable from /settings
  config.py                   Settings loaded from environment / .env
  requirements.txt
  .env.example

frontend/storyforge-ui/
  src/app/
    pages/
      landing/                 "/" -- app picker
      dashboard/               "/ai-ba" -- job list (PPM number/name, status, story count)
      assess/                  New assessment submission form (PDF upload)
      clarify/                 Answer clarification questions
      review/                  Edit/approve generated stories before document export / ADO creation
      status/                  Poll job status, stepper, ADO results table (OUTPUT_MODE=ado) or Notion pages table (OUTPUT_MODE=notion), and (once done) a read-only stories/tasks text panel with copy + document-download buttons
      ingestion/                "/ingestion" -- start code/PDF ingestion jobs, progress + history
      ask-technical/            "/ask/technical" -- SSE Q&A chat grounded in the ingested corpus, cites file paths
      ask-business/              "/ask/business" -- same corpus, plain-language answers
      settings/                 "/settings"
      monitoring/                "/monitoring" -- error feed
    services/
      storyforge.service.ts    HTTP client for StoryForge's own backend API
      ask.service.ts             HTTP + SSE client for Ask Technical/Business
      sse.util.ts                 Shared SSE frame-parsing client
      settings.service.ts        Unified settings GET/PUT
      monitoring.service.ts      Error feed
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

All backend configuration is environment-variable driven (`backend/.env`, loaded via `python-dotenv`). See `backend/config.py` for defaults. Everything below can also be edited from the `/settings` screen in the UI, which writes back to `.env` and applies the change to the running backend immediately — no restart needed for most fields (see [`config.py`](backend/config.py)'s `Settings.apply_updates` and [`config_store.py`](backend/config_store.py); a few fields still require a restart, flagged as `restart_required_fields` in the `GET /api/settings` response).

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(none)_ | Not called anywhere in *this* pipeline (`clarify_node`/`generate_node` use `OLLAMA_LLM_MODEL` instead) — but it **is** required for ingestion's default (Claude) LLM-summary enrichment agent and Ask Technical/Business's default model |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Not used by this pipeline; used by ingestion's enrichment tier and Ask Technical/Business (exposed as `anthropic_model` on the settings screen) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL, shared by every local model (embeddings, clarify/generate, ingestion's optional enrichment agent, Ask's optional Ollama mode) |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model name, used for all RAG retrieval |
| `OLLAMA_LLM_MODEL` | `qwen2.5:14b` | Chat model used by `clarify_node` and `generate_node`, both run at `temperature=0` with a fixed seed for reproducible output across runs of the same SDD |
| `INGEST_OLLAMA_ENABLED` | `false` | Enables Ollama as a second agent (round-robin with Claude) for ingestion's optional per-file LLM-summary enrichment tier |
| `INGEST_OLLAMA_MODEL` | `qwen2.5:14b` | Model name for the above |
| `INGEST_LLM_SUMMARY_ENABLED` | `true` | Whether ingestion runs its optional per-file LLM-summary enrichment tier at all (mechanical chunking always runs regardless) |
| `ASK_QA_MODEL` | `claude` | `claude` or `ollama` — which model answers Ask Technical/Business. Editable from `/settings` (hot-reloads, no restart needed) |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `300` | Shared HTTP request timeout for every LLM call in this app (Ask, StoryForge's `generate_node`/`clarify_node`, ingestion's LLM-summary enrichment agents) — previously three separate hardcoded 120s constants that could kill a slow local Ollama response before it finished. Editable from `/settings` (hot-reloads, no restart needed) |
| `CONVERSATION_HISTORY_CHAR_BUDGET` | `8000` | Character ceiling (not token — see `config.py`'s comment) for how much prior conversation history is folded into a follow-up Ask Technical/Business question; trimmed oldest-turn-first |
| `CHROMA_PERSIST_PATH` | `./chroma_db` | On-disk path for the persistent ChromaDB store |
| `MCP_SERVER_PATH` | _(empty)_ | Path to the ADO MCP server's Node.js entry script |
| `ADO_ORGANIZATION` | _(empty)_ | Azure DevOps organization name, passed to the MCP server |
| `ADO_PROJECT` | _(empty)_ | Azure DevOps project name, passed to the MCP server |
| `NOTION_API_KEY` | _(empty)_ | Notion internal integration secret, used when `OUTPUT_MODE=notion` |
| `NOTION_DATABASE_ID` | _(empty)_ | ID of the "StoryForge Epics" database `create_notion_node` writes pages into — created once via `python -m scripts.setup_notion_database`. `notion_export/client.py` resolves and caches the database's *data source* ID from this at runtime (Notion API 2025-09-03+ requires pages to be parented by a data source, not a database, directly) — you only ever configure the database ID here |
| `NOTION_PARENT_PAGE_ID` | _(empty)_ | ID of the Notion page (shared with the integration) under which `scripts/setup_notion_database.py` creates the database; only needed to run that script |
| `CORS_ORIGINS` | `http://localhost:4200` | Comma-separated list of allowed CORS origins |
| `JWT_SECRET` | _(auto-generated)_ | Signs/verifies the login JWT used by every route in this app. Leave unset for local dev — a random value is generated and persisted to `JOBS_DIR/.jwt_secret` so restarts don't invalidate logins. Set explicitly before exposing the app beyond local testing |
| `JOBS_DIR` | `./jobs` | Holds `checkpoints.sqlite` (LangGraph's persisted pipeline state), `assess_jobs.json` (the dashboard's job list), `users.json`, `.jwt_secret`, and `.enrichment-manifests/` (ingestion's incremental-skip manifests) — all survive a backend restart |
| `UPLOADS_DIR` | `./uploads` | Directory uploaded SDD PDFs are saved to (`{job_id}.pdf`) |
| `EXPORTS_DIR` | `./exports` | Directory generated `.docx` files are saved to (`{ppm_number}_{ppm_name}_{system_name}_V_{n}.docx`, sanitized and versioned per project), used when `OUTPUT_MODE=document` |
| `PROMPT_VARIANT` | `production` | `production` uses `prompts/system_prompt.py`. `selftest` swaps in `prompts/system_prompt_selftest.py`, a variant tuned for assessing SDDs about StoryForge AI's own codebase (Python/FastAPI/LangGraph/Angular) instead of the default telecom/Spring Boot domain assumptions. |
| `OUTPUT_MODE` | `document` | The **default** task-management system for new assessments — `document` writes approved stories to a `.docx` via `export_document_node`, downloadable from `GET /export/document/{job_id}`; `ado` pushes to Azure DevOps via `create_ado_node`; `notion` pushes to a Notion database via `create_notion_node`. All three nodes are registered unconditionally; only the routing picks one. Each job can override this default via the dropdown on the Assess form — the choice is stamped onto the job as `state["output_mode"]` at submission time, so changing this default later doesn't affect already-submitted jobs |

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
curl -X POST http://localhost:8000/api/ingest/documents -H "Content-Type: application/json" \
  -d '{"folder_path": "/path/to/user-manuals"}'

curl -X POST http://localhost:8000/api/ingest/code -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/monorepo"}'
```

Both return `{"job_id": ..., "status": "pending"}` immediately; poll `GET /api/ingest/status/{job_id}` for progress/completion, or use the `/ingestion` page. Deterministic per-chunk IDs mean re-running ingestion against the same repo/folder **updates in place** — unchanged chunks are untouched, changed files' chunks are replaced, and files deleted since the last run have their stale chunks purged.

**Auto re-ingestion via a filesystem watcher**: instead of re-running ingestion by hand, add a path under "Watched Paths" on the `/ingestion` page (or `POST /api/watch/targets`) and every create/modify/delete anywhere underneath it automatically triggers a full re-ingestion run of that path (debounced to one run per burst of changes, not one per touched file — see `backend/ingestion/watcher.py`). A watched path and a manually-started run of the same path can't race each other; whichever started first wins and the other is skipped until it finishes.

**Code chunking rules** (`backend/ingestion/ingest_code.py`):
- **Java** — chunked by whole class AND by individual method. Files annotated `@Entity` are indexed into both `sf_codebase` and `sf_jpa_entities`. Layer is inferred from `@RestController`/`@Service`/`@Repository`/`@Entity`/`@Configuration` annotations. `*Test.java` and `*IT.java` files are excluded.
- **TypeScript/Angular** — chunked by `@Component`/`@Injectable`/`@NgModule`/`@Directive`/`@Pipe` decorated class. Files with no decorator are chunked whole as a service/utility file. `*.spec.ts` files are excluded.
- **JavaScript/jQuery** — chunked by function (`function foo(){}`, arrow function assignments, and `$.fn.x = function(){}` jQuery plugins). Falls back to whole-file chunking if no functions are detected.
- **13 other languages** (Python, Go, C#, Ruby, Rust, PHP, Kotlin, and more) — chunked whole-file, since precise symbol-level chunking isn't implemented for these yet. The optional LLM-summary enrichment tier below is meant to compensate for the resulting precision gap.
- **HTML** is never indexed. `node_modules/`, `target/`, `dist/`, `.git/`, and similar build/dependency directories are always skipped.
- Any chunk exceeding ~1500 tokens (~6000 chars) is further split with `RecursiveCharacterTextSplitter` (150-token overlap).

**Optional LLM-summary enrichment** (`backend/ingestion/enrichment/`, `INGEST_LLM_SUMMARY_ENABLED`, on by default): after mechanical chunking, each eligible file is also sent to an LLM (Claude by default, optionally Ollama too) for a narrative business-logic summary, embedded into `sf_codebase` alongside the raw chunks. Incremental re-runs skip re-summarizing files whose content hasn't changed, via a per-repo content-hash manifest under `JOBS_DIR/.enrichment-manifests/`.

## API reference

All endpoints are served under the FastAPI app created in `backend/api/main.py`, with all routers registered under an `/api` prefix (so a reverse proxy can route by path prefix). `/health` is also exposed unprefixed for direct container healthchecks.

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` (also `/api/health`) | Liveness check → `{"status": "ok"}` |
| `POST` | `/api/auth/login` | Body: `{"username", "password"}` → `{"access_token", "username", "role"}`. This one JWT is trusted by every route in this app |
| `POST` | `/api/auth/logout` | Stateless — nothing to invalidate server-side; the client just drops the token |
| `GET` | `/api/auth/me` | → the decoded `{"username", "role"}` for the caller's current token |
| `POST` | `/api/ingest/documents` | Body: `{"folder_path": str}`. Starts background document ingestion (PDF/Word/Markdown/Confluence export) → `{"job_id", "status": "pending"}` |
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
| `GET` | `/api/settings` | Current configuration, read from `backend/.env`. Secrets (`notion_api_key`, `anthropic_api_key`) are masked (last 4 chars only, e.g. `"…q5t9"`) — never returned in plaintext. Also returns `restart_required_fields`, the subset of fields that need a process restart to take effect |
| `PUT` | `/api/settings` | Body: any subset of the fields returned by `GET /api/settings`, plus optionally `notion_api_key`/`anthropic_api_key` (a real new value — omitting it, or sending back the mask unchanged, leaves the current secret untouched). Writes changed fields to `backend/.env` and applies them to the running backend immediately for the fields that support hot-reload — see [`config.py`](backend/config.py)'s `Settings.apply_updates` and [`config_store.py`](backend/config_store.py). Returns the refreshed (masked) settings |
| `GET` | `/api/monitoring/errors` | Every `ERROR`+-level log record captured from this process since startup (see [`monitoring/log_capture.py`](backend/monitoring/log_capture.py)), ring-buffered to the most recent N. Backs the `/monitoring` page's error feed |
| `GET` | `/api/corpus/sources` | → `{"manuals": [...], "codebase": [...]}`, one row per distinct source file in each collection: `{"source", "chunk_count", "has_llm_summary", "format", "ingested_at"}`. Backs the `/corpus` corpus-browser page (file list + metadata only, no chunk-content drill-down); `entities` is intentionally excluded since it's a derived re-indexing of `@Entity` files already counted under `codebase` |
| `GET` | `/api/watch/targets` | List watched paths → `[{"id", "path", "kind", "enabled", "created_at"}, ...]` |
| `POST` | `/api/watch/targets` | Admin only. Body: `{"path", "kind": "documents"\|"code"}`. 400 if `path` isn't a directory. Starts watching immediately (no restart needed) |
| `PATCH` | `/api/watch/targets/{id}` | Admin only. Body: `{"enabled": bool}`. Starts/stops the live watch immediately. 404 if unknown |
| `DELETE` | `/api/watch/targets/{id}` | Admin only. Stops watching and removes the target. 404 if unknown |
| `GET` | `/api/prompts/ask` | → `{"technical": {"custom", "default", "effective"}, "business": {...}}` for the Ask Technical/Business system prompt templates |
| `PUT` | `/api/prompts/ask/{kind}` | Admin only. `{kind}` is `technical` or `business`. Body: `{"template": str \| null}` (`null` resets to the built-in default). 400 if the template is missing `{context}` or contains any other placeholder — takes effect on the very next `/api/ask/{kind}` request, no restart needed |
| `GET` | `/api/conversations` | List the caller's own conversations (summaries only, sorted newest-updated-first) |
| `POST` | `/api/conversations` | Body: `{"kind": "technical"\|"business", "title"?: str}`. Creates a new empty conversation (Ask Technical/Business auto-create one on the first question instead of calling this directly — see `AskRequest.conversation_id` below) |
| `GET` | `/api/conversations/{id}` | Full conversation including all messages. 404 if unknown or owned by another user |
| `DELETE` | `/api/conversations/{id}` | 404 if unknown or owned by another user |

Ask Technical/Business's endpoints (`/api/ask/technical`, `/api/ask/business`, `/api/ask/status`)
are documented in the [Ask Technical / Ask Business](#ask-technical--ask-business) section
below, not duplicated in this table.

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
| `/` | Landing | App picker |
| `/ai-ba` | Dashboard | Lists all submitted jobs with live status + story count |
| `/assess` | Assess | Form to submit a new SDD PDF + PPM metadata + task-management-system dropdown (document/Notion/ADO, defaulting to the Settings screen's global default) + review mode toggle |
| `/clarify/:jobId` | Clarify | Displays clarification questions, submits answers to resume the pipeline |
| `/review/:jobId` | Review | Displays generated stories for human editing/approval before ADO creation |
| `/status/:jobId` | Status | Polls job status, shows a step progress indicator, the final ADO work item results table or Notion pages table (depending on the job's own `output_mode`), "Re-create tasks"/"Update tasks" buttons once a `notion`/`ado` job reaches `done`, and a read-only formatted stories/tasks panel with copy-to-clipboard and a Download Document button |
| `/ingestion` | Ingestion | Start code/PDF ingestion jobs, progress bar, cancel, history |
| `/ask/technical` | Ask Technical | SSE Q&A chat grounded in the ingested corpus, cites source file paths |
| `/ask/business` | Ask Business | Same corpus, plain-language answers, no file paths |
| `/settings` | Settings | Ollama base URL/model/embed model, prompt variant, task-management-system fields, Anthropic key/model, ingestion's optional Ollama enrichment agent toggle — persisted to `backend/.env` and applied to the running backend immediately for most fields |
| `/monitoring` | Monitoring | Error feed |
| `**` | — | Redirects to `/` |

## Ask Technical / Ask Business

Two standing Q&A pages, always available once ingestion has run at least once, both
querying the exact same ChromaDB corpus (code + manuals + JPA entities) via
`ingestion/retrieval.py` — they differ only in prompt framing/depth, not retrieval scope:

- **Ask Technical** (`/ask/technical`, `POST /api/ask/technical`) — for the development
  team. Cites the full relative path of every source file the answer draws from.
- **Ask Business** (`/ask/business`, `POST /api/ask/business`) — for the business team.
  Plain-language capability/impact framing, no file paths or code identifiers.

Both retrieve the top 10 chunks per collection (`retrieve_all_collections()`) and stream
the answer back over SSE (`event: sources` with the source file list, then `event: chunk`
per text chunk) using the model selected by `ASK_QA_MODEL` (`prompts/ask_prompts.py` holds
the two system prompt templates, sharing one grounding-rules block that governs
same-basename-file disambiguation and cross-cutting-feature attribution — customizable
per-kind from `/settings`, see `prompt_store.py`). `ASK_QA_MODEL` itself is also editable
from `/settings` ("Ask Technical / Ask Business" card) and takes effect on the very next
question with no restart, since `_get_ask_chat()` already checks `settings_generation` on
every call. `GET
/api/ask/status` reports each collection's document count, so the frontend can show a
"run ingestion first" empty state.

**Conversation memory**: each page keeps a sidebar of the caller's own past conversations
(backend-persisted, one file per conversation under `<JOBS_DIR>/conversations/<username>/`,
see `api/conversation_store.py`). The first question of a new conversation auto-creates one
server-side — the frontend never calls `POST /api/conversations` directly for this path —
and its id comes back on the streamed response's `X-Conversation-Id` header (not a new SSE
event, to avoid touching the `sources`/`chunk` frame contract). A follow-up question passes
that same id as `AskRequest.conversation_id`; prior turns (trimmed to
`CONVERSATION_HISTORY_CHAR_BUDGET` characters, oldest first) are threaded into the actual
LangChain message list sent to the model — never string-concatenated into the `{context}`
RAG placeholder, keeping conversation memory orthogonal to prompt customization above.

**Answer caching**: an in-memory, exact-question-text cache (`api/ask_cache.py`, no TTL, no
size cap — resets on restart, a deliberate v1 tradeoff) skips retrieval and the chat call
entirely on a hit. The cache key folds in `kind`, `ingestion/ingestion_generation.py`'s counter
(bumped on every successful ingestion run — a repeat question after re-ingesting is always a
miss), the effective prompt template (a Settings-page prompt edit is never served stale), and
the trimmed conversation-context text (so a cached answer can never leak across unrelated
conversations asking the same question). The empty-corpus "run ingestion first" fallback is
never cached.

## Testing

```bash
cd backend
source venv/bin/activate
pytest tests/ -q
```

Run against `fastapi.testclient.TestClient` for the routers, mocked chat/embedding clients
for LLM-touching logic, and the real LangGraph engine (with node functions monkeypatched)
for StoryForge's orchestration. `tests/ingestion/` covers ingestion's chunking/dedup/
enrichment modules the same way — mocked `ChatAnthropic`/`ChatOllama`/embeddings and a fake
vector store standing in for Chroma. No test makes a real call to Anthropic, Ollama,
ChromaDB persistence beyond a scratch path, the ADO MCP server, or the Notion API — all are
mocked/stubbed.

Key things to verify after making changes:
- `backend/pipeline/graph.py` orchestration: clarification + review pause/resume across all 4 combinations of `clarification_needed` × `review_mode`
- Error propagation: a node failure (`status == "error"`) must stop the graph rather than being masked by a downstream node finishing on empty input
- `backend/ingestion/ingest_code.py` chunking rules against representative fixtures across languages, and dedup (re-ingesting doesn't grow chunk counts)
- `backend/ingestion/enrichment/enrich.py`'s incremental-skip manifest: only a genuinely successful summarization should be recorded as done, never a failed attempt
- All FastAPI routers' status codes (`404`/`409`/`422`) and response shapes
- Frontend build (`ng build`) and a smoke pass over all routes with the backend unreachable (should degrade gracefully, no console errors)

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
- **Ingestion completes but the LLM-summary enrichment tier silently did nothing** — check `GET /api/ingest/status/{job_id}`'s `result.errors` list, or the `/monitoring` page, for `"credit balance too low"`/`"invalid x-api-key"`/similar per-file failures — `ANTHROPIC_API_KEY` is unset/invalid (and `INGEST_OLLAMA_ENABLED` is off, so there's no fallback agent). Mechanical chunking (tier 1) still succeeds regardless; only the optional narrative summaries are missing. Set a valid key and re-run ingestion — a failed enrichment attempt is never recorded as done, so it will be retried automatically.
- **Ask Technical/Business or ingestion enrichment logs `"no agents configured"`** — neither `ANTHROPIC_API_KEY` nor `INGEST_OLLAMA_ENABLED`/Ollama is configured. Set one via `.env` or the `/settings` screen.
