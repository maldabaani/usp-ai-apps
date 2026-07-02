# StoryForge AI

StoryForge AI turns a Solution Design Document (SDD) PDF into a fully-detailed, ready-to-create Azure DevOps work item hierarchy — **Epic → User Story → Dev Tasks + Unit Test Tasks** — using a local Ollama model (`qwen2.5:14b` by default) for analysis/generation, the same Ollama server for embeddings + ChromaDB for retrieval-augmented context (RAG) over your existing codebase, user manuals, and JPA entities, and a human-in-the-loop review/clarification workflow before anything is written to ADO.

> `clarify_node` and `generate_node` run at `temperature=0` with a fixed seed, so the
> same SDD produces the same clarification questions and the same generated stories on
> every run. Both nodes retry (with a nudged seed) on transient failures or malformed
> JSON before giving up — see [`backend/pipeline/nodes/llm_retry.py`](backend/pipeline/nodes/llm_retry.py).
> `ANTHROPIC_API_KEY`/`CLAUDE_MODEL` are configured in `config.py` but are not currently
> called anywhere in the pipeline — Claude is not used by this app today.

It is a two-part application:

- **Backend** — Python / FastAPI, orchestrated by a [LangGraph](https://github.com/langchain-ai/langgraph) state machine
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

The graph is checkpointed (`MemorySaver`, keyed by `job_id`) and interrupts before `generate_node` and before whichever of `export_document_node` / `create_ado_node` / `create_notion_node` is selected by `OUTPUT_MODE`, so jobs can pause for human clarification/review and resume later via dedicated endpoints. Every node-to-node edge is conditional on `status`: if any node fails and sets `status == "error"`, the graph routes straight to `END` instead of letting downstream nodes run against incomplete state.

## Project structure

```
backend/
  api/
    main.py                 FastAPI app factory, CORS, router registration (all under /api), /health
    job_registry.py         In-memory registry for /api/assess jobs (list + metadata)
    ingest_jobs.py           In-memory registry for /api/ingest jobs (progress + status)
    routers/
      assess.py              POST /api/assess, POST /api/assess/rerun/{job_id}, POST /api/assess/retry/{job_id}, GET /api/assess/jobs, GET /api/assess/status/{job_id}
      clarify.py              POST /api/clarify/answer/{job_id}
      review.py               POST /api/review/approve/{job_id}
      ado.py                  GET /api/ado/status/{job_id}
      export.py                GET /api/export/document/{job_id}
      ingest.py                POST /api/ingest/pdfs, POST /api/ingest/code, GET /api/ingest/status/{job_id}
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
  config.py                   Settings loaded from environment / .env
  requirements.txt
  .env.example

frontend/storyforge-ui/
  src/app/
    pages/
      dashboard/               Job list (PPM number/name, status, story count)
      assess/                  New assessment submission form (PDF upload)
      clarify/                 Answer clarification questions
      review/                  Edit/approve generated stories before document export / ADO creation
      status/                  Poll job status, stepper, ADO results table (OUTPUT_MODE=ado) or Notion pages table (OUTPUT_MODE=notion), and (once done) a read-only stories/tasks text panel with copy + document-download buttons
    services/
      storyforge.service.ts    HTTP client for the backend API
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

All backend configuration is environment-variable driven (`backend/.env`, loaded via `python-dotenv`). See `backend/config.py` for defaults.

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(unused)_ | Configured but not currently called anywhere in the pipeline — `clarify_node`/`generate_node` use `OLLAMA_LLM_MODEL` instead |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Configured but not currently used (see `ANTHROPIC_API_KEY` above) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL, used for both embeddings and clarify/generate |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model name |
| `OLLAMA_LLM_MODEL` | `qwen2.5:14b` | Chat model used by `clarify_node` and `generate_node`, both run at `temperature=0` with a fixed seed for reproducible output across runs of the same SDD |
| `CHROMA_PERSIST_PATH` | `./chroma_db` | On-disk path for the persistent ChromaDB store |
| `MCP_SERVER_PATH` | _(empty)_ | Path to the ADO MCP server's Node.js entry script |
| `ADO_ORGANIZATION` | _(empty)_ | Azure DevOps organization name, passed to the MCP server |
| `ADO_PROJECT` | _(empty)_ | Azure DevOps project name, passed to the MCP server |
| `NOTION_API_KEY` | _(empty)_ | Notion internal integration secret, used when `OUTPUT_MODE=notion` |
| `NOTION_DATABASE_ID` | _(empty)_ | ID of the "StoryForge Epics" database `create_notion_node` writes pages into — created once via `python -m scripts.setup_notion_database` |
| `NOTION_PARENT_PAGE_ID` | _(empty)_ | ID of the Notion page (shared with the integration) under which `scripts/setup_notion_database.py` creates the database; only needed to run that script |
| `CORS_ORIGINS` | `http://localhost:4200` | Comma-separated list of allowed CORS origins |
| `JOBS_DIR` | `./jobs` | Reserved directory for job-related persistence |
| `UPLOADS_DIR` | `./uploads` | Directory uploaded SDD PDFs are saved to (`{job_id}.pdf`) |
| `EXPORTS_DIR` | `./exports` | Directory generated `.docx` files are saved to (`{ppm_number}_{ppm_name}_{system_name}_V_{n}.docx`, sanitized and versioned per project), used when `OUTPUT_MODE=document` |
| `PROMPT_VARIANT` | `production` | `production` uses `prompts/system_prompt.py`. `selftest` swaps in `prompts/system_prompt_selftest.py`, a variant tuned for assessing SDDs about StoryForge AI's own codebase (Python/FastAPI/LangGraph/Angular) instead of the default telecom/Spring Boot domain assumptions. |
| `OUTPUT_MODE` | `document` | `document` (default) writes approved stories to a `.docx` via `export_document_node`, downloadable from `GET /export/document/{job_id}`. `ado` pushes to Azure DevOps via `create_ado_node`. `notion` pushes to a Notion database via `create_notion_node`. All three nodes are registered unconditionally; only the routing picks one. |

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
| `POST` | `/api/ingest/pdfs` | Body: `{"folder_path": str}`. Starts background PDF ingestion → `{"job_id", "status": "pending"}` |
| `POST` | `/api/ingest/code` | Body: `{"repo_path": str}`. Starts background code ingestion → `{"job_id", "status": "pending"}` |
| `GET` | `/api/ingest/status/{job_id}` | → `{"status", "progress", "errors", "result"}` (`result` is `null` until the job finishes, then holds `files_processed`/`chunks_indexed`/etc.). 404 if unknown |
| `POST` | `/api/assess` | Multipart form: `file` (PDF), `ppm_number`, `ppm_name`, `system_name`, `review_mode` (bool, default `false`). Starts the pipeline in the background → `{"job_id"}` |
| `GET` | `/api/assess/jobs` | List all submitted assessment jobs with live `status` + `story_count` |
| `GET` | `/api/assess/status/{job_id}` | Full `StoryForgeState` for the job. 404 if unknown |
| `POST` | `/api/assess/retry/{job_id}` | Re-runs just the pipeline step that failed (`generate_node`, or whichever `create_ado_node`/`export_document_node`/`create_notion_node` `OUTPUT_MODE` selects) without redoing earlier work (SDD parsing, RAG retrieval, clarification, generation, or review that already succeeded). Distinct from `llm_retry.py`'s per-LLM-call retries inside a single node — this is a user-triggered retry of an entire node after it already exhausted those and left the job in `status: "error"`. 404 if unknown, 409 if the job isn't in an `error` status or the failure isn't resumable (e.g. it failed inside `analyze_node`, before the first checkpoint — submit a new assessment instead). See [`pipeline/runner.py`](backend/pipeline/runner.py)'s `retry_failed_step` |
| `POST` | `/api/clarify/answer/{job_id}` | Body: `{"answers": {question: answer}}`. 409 if job isn't awaiting clarification. Resumes the pipeline → `{"status": "generating"}` |
| `POST` | `/api/review/approve/{job_id}` | Body: `{"approved_stories": [...]}`. 409 if job wasn't run with `review_mode=true`. Resumes the pipeline → `{"status": "creating"}` |
| `GET` | `/api/ado/status/{job_id}` | → `{"ado_results", "errors"}`. 404 if unknown |
| `GET` | `/api/export/document/{job_id}` | Downloads the generated `.docx` (`OUTPUT_MODE=document`). 404 if the job is unknown or the document isn't generated yet. Linked from the status page via the "Download Document" button shown once the job reaches `done` |

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

`pipeline/graph.py` wires 7 nodes into a LangGraph `StateGraph`, checkpointed per `job_id`:

| Node | Sets `status` to | Notes |
|---|---|---|
| `analyze_node` | `analyzing` (or `error`) | Extracts SDD text via `pypdf`, retrieves top-10 chunks per collection in parallel |
| `clarify_node` | `clarifying` or `generating` | Asks the local model to flag ambiguities in 4 categories; retries on transient/parse failures ([`llm_retry.py`](backend/pipeline/nodes/llm_retry.py)), then fails open (proceeds without clarification) if all retries are exhausted |
| `generate_node` | `reviewing`/`creating` (or `error`) | Generates the full story/task JSON array; retries on transient/parse failures ([`llm_retry.py`](backend/pipeline/nodes/llm_retry.py)), then sets `error` if all retries are exhausted |
| `review_node` | `reviewing` or `creating` | Pass-through when `review_mode` is off; otherwise waits for human edits |
| `export_document_node` | `done` (or `error`) | Default (`OUTPUT_MODE=document`): renders the approved hierarchy to a `.docx` via `python-docx`, saved to `EXPORTS_DIR/{job_id}.docx` |
| `create_ado_node` | `done` (or `error`) | `OUTPUT_MODE=ado`: creates Epic → User Story → Tasks via the MCP client; one story failing doesn't abort the rest |
| `create_notion_node` | `done` (or `error`) | `OUTPUT_MODE=notion`: creates one Epic page per story in the Notion database (`NOTION_DATABASE_ID`) via `notion-client`, with Dev/Unit-Test tasks rendered as nested blocks; one story failing doesn't abort the rest |

After `review_node`, the graph branches on `settings.OUTPUT_MODE` to reach `export_document_node`, `create_ado_node`, or `create_notion_node` — all three are registered unconditionally so the mode can be flipped at runtime without recompiling the graph. The graph **interrupts before** `generate_node` and before whichever of the three is reachable. `pipeline/runner.py`'s `_drive` loop auto-resumes past an interrupt when no human input is actually required (no ambiguities found / `review_mode=False`), and stops cleanly at a genuine human-in-the-loop pause otherwise. Every edge between nodes is conditional: a node that sets `status == "error"` routes straight to `END`, so a failure can never be silently overwritten back to `"done"` by a downstream node running on empty input.

If `generate_node` or a create/export node fails outright (all its `llm_retry.py` attempts exhausted, or a non-LLM error like a bad `NOTION_DATABASE_ID`), `POST /api/assess/retry/{job_id}` rewinds the checkpoint to right before the failed node and re-runs just that node, reusing everything computed before it (see the API reference above). This isn't idempotent for the create/export nodes: they isolate failures per-item (one story failing doesn't abort the rest), so retrying after a *partial* failure re-creates every approved story/task from scratch, including ones that already succeeded on the first attempt — expect duplicate ADO work items / Notion pages / a re-generated document for those. A failure inside `analyze_node` has no earlier checkpoint to rewind to, so it isn't retryable this way — submit a new assessment instead.

`job_id` doubles as the LangGraph checkpoint `thread_id`, so `get_job_state(job_id)` can always retrieve the latest state for polling, and `resume_after_clarification` / `resume_after_review` patch state via `aupdate_state` before resuming.

## Frontend pages

| Route | Component | Purpose |
|---|---|---|
| `/` | Dashboard | Lists all submitted jobs with live status + story count |
| `/assess` | Assess | Form to submit a new SDD PDF + PPM metadata + review mode toggle |
| `/clarify/:jobId` | Clarify | Displays clarification questions, submits answers to resume the pipeline |
| `/review/:jobId` | Review | Displays generated stories for human editing/approval before ADO creation |
| `/status/:jobId` | Status | Polls job status, shows a step progress indicator, the final ADO work item results table or Notion pages table (depending on `OUTPUT_MODE`), and a read-only formatted stories/tasks panel with copy-to-clipboard and a Download Document button |
| `**` | — | Redirects to `/` |

## Testing

Backend tests use `pytest`-style assertions (or can be run as plain scripts) against `fastapi.testclient.TestClient` for the routers and against the real LangGraph engine (with node functions monkeypatched) for orchestration logic. No tests make real calls to Anthropic, Ollama, ChromaDB persistence beyond a scratch path, the ADO MCP server, or the Notion API — all are mocked/stubbed.

Key things to verify after making changes:
- `backend/pipeline/graph.py` orchestration: clarification + review pause/resume across all 4 combinations of `clarification_needed` × `review_mode`
- Error propagation: a node failure (`status == "error"`) must stop the graph rather than being masked by a downstream node finishing on empty input
- `backend/ingestion/ingest_code.py` chunking rules against representative Java/TypeScript/JavaScript fixtures
- All 5 FastAPI routers' status codes (`404`/`409`/`422`) and response shapes
- Frontend build (`ng build`) and a smoke pass over all routes with the backend unreachable (should degrade gracefully, no console errors)

## Troubleshooting

- **`ModuleNotFoundError` involving `mcp`** — the backend's own `ado_mcp/` package was deliberately renamed from `mcp/` because a directory literally named `mcp` on `PYTHONPATH` shadows the third-party `mcp` SDK package required by `langchain-mcp-adapters`. Make sure nothing reintroduces a local `mcp/` directory.
- **`ModuleNotFoundError` or attribute errors involving `notion`** — same pattern: the backend's own package is named `notion_export/`, not `notion/`, so it doesn't shadow the third-party `notion_client` package's import name. Make sure nothing reintroduces a local `notion/` directory.
- **`RuntimeError: NOTION_API_KEY / NOTION_DATABASE_ID is not set`** — `OUTPUT_MODE=notion` requires both; run through [Notion setup](#notion-setup) first, including `scripts/setup_notion_database.py` to obtain `NOTION_DATABASE_ID`.
- **`ImportError: cannot import name 'RecursiveCharacterTextSplitter' from 'langchain.text_splitter'`** — that module was removed; both ingestion modules import from `langchain_text_splitters` instead (listed explicitly in `requirements.txt`).
- **`ChatAnthropic` raises `ValueError` on attribute assignment** — it's a Pydantic model with strict field validation; don't monkeypatch its methods directly in tests, replace the module-level `_llm` reference instead.
- **A job's `status` shows `done` with empty `generated_stories`/`ado_results`** — check `errors` in the job state; this indicates an upstream node failed. The graph now routes failures straight to `END`, so this should only surface for jobs run before the error-routing fix.
- **404 on direct navigation to a frontend route in a static build** — your static file server needs SPA history-mode fallback (e.g. `serve -s`).
