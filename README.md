# USP AI Apps

This repository hosts **StoryForge AI**, a single Python/FastAPI + Angular app that
covers two AI-powered developer workflows behind one login, one origin:

| | CodeMind | AI Business Analyst |
|---|---|---|
| Purpose | Reverse-engineers business logic out of an existing codebase | Forward-generates a full Azure DevOps work item hierarchy from a design doc |
| Model | Claude by default (+ optional local Ollama agent, round-robin) | Local Ollama model (`qwen2.5:14b`, deterministic — `temperature=0` + fixed seed) for analysis/generation and RAG embeddings |
| Landing page card | "CodeMind" (native Angular pages) | "AI Business Analyst" (native Angular pages) |

Both flows share one FastAPI backend process, one job-persistence layer, one
settings screen, and one error-monitoring feed — see
[`usp-ai-ba/backend/codemind/`](usp-ai-ba/backend/codemind/) for CodeMind's
module layout within that backend.

## Quick start (unified platform)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

Open **http://localhost/** — the "AI BA" card is the StoryForge flow, the "CodeMind"
card is CodeMind's job UI, both served from one origin. See
[RUNNING.md](RUNNING.md) for standalone run instructions, local-LLM configuration,
and troubleshooting notes.

## Architecture

```
                          ┌─────────────────────────────────────────┐
                          │        Angular shell (storyforge-ui)     │
                          │   landing cards:  [ AI BA ]  [ CodeMind ]│
                          └───────────────────┬───────────────────┘
                                              │
                        nginx gateway (deploy/nginx.conf), one origin
                                              │
                                     `/` (Angular build)
                                     SPA fallback
                                              │
                                          `/api/*`
                          StoryForge FastAPI backend (LangGraph pipeline,
                          ChromaDB, and CodeMind's extraction/Ask endpoints)
```

See [`docker-compose.yml`](docker-compose.yml) and
[`deploy/nginx.conf`](deploy/nginx.conf) for the exact wiring.

## CodeMind

Scans a repository, sends each source file to Claude (or a local Ollama model) with a
structured prompt, and stores the extracted rules, summaries, and dependencies as JSON.
Native Angular pages let you watch jobs run in real time, browse extracted results, and
ask natural-language questions about the code once extraction is complete.

**Highlights**
- Multi-language extraction — JS/TS, Python, Java, Kotlin, Go, C#, Ruby, Rust, PHP
- Two execution modes: **SYNC** (per-file, low latency, `asyncio`-bounded concurrency) or
  **BATCH** (Anthropic Message Batches API, flat 50% token discount, built for large repos)
- Hybrid agent setup — Claude plus an optional local Ollama model, round-robin load balanced
- Large-file chunking, incremental re-runs (manifest-tracked), per-file fault isolation
- Ask Agent (per-job) and Ask All (cross-job) natural-language Q&A over extracted results,
  with vector search (Ollama embeddings) falling back to keyword search
- Directory watcher — drop a path into a watched folder and a job starts automatically
  (off by default)
- Live progress UI: stepper, stats, real-time file feed, viewer modal, cancel/export

**Key REST endpoints:** `POST /api/v1/extraction-jobs`, `GET /api/v1/extraction-jobs/{id}`,
`POST .../cancel`, `GET .../export`, `POST .../qa/stream`, `POST /api/v1/ask/stream`.
**UI routes:** `/codemind`, `/codemind/:jobId`, `/codemind/:jobId/ask`, `/codemind/ask`.

## StoryForge AI

Turns a Solution Design Document (SDD) PDF into a fully-detailed, ready-to-create Azure
DevOps work item hierarchy — **Epic → User Story → Dev Tasks + Unit Test Tasks** — using
a local Ollama model (`qwen2.5:14b`) for analysis/generation, RAG over your
codebase/user manuals/JPA entities (ChromaDB + Ollama embeddings), and a human-in-the-loop
review/clarification workflow before anything is written downstream. Analysis/generation
run at `temperature=0` with a fixed seed, so the same SDD produces the same output on
every run, with automatic retry (nudged seed) on transient failures or malformed JSON.

**Pipeline** (LangGraph state machine, checkpointed per job): `analyze → clarify →
generate → review → export_document | create_ado | create_notion`. The graph interrupts
before generation and before the export step so a human can answer clarifying questions
or edit/approve stories; any node failure routes straight to `END` instead of letting
downstream nodes run on incomplete state.

**Output modes** (`OUTPUT_MODE`): `document` (default, `.docx` via python-docx), `ado`
(Azure DevOps via a local Node MCP server), `notion` (one Epic page per story via
`notion-client`).

**Tech stack:** Python/FastAPI · LangGraph · Angular 17+ standalone components · ChromaDB · Ollama

**Key REST endpoints:** `POST /api/assess`, `GET /api/assess/status/{job_id}`,
`POST /api/clarify/answer/{job_id}`, `POST /api/review/approve/{job_id}`,
`GET /api/export/document/{job_id}`, `POST /api/ingest/pdfs`, `POST /api/ingest/code`.
**Frontend routes:** `/ai-ba` (dashboard), `/assess`, `/clarify/:jobId`, `/review/:jobId`, `/status/:jobId`.

Full details, configuration reference, and the complete API/schema documentation:
[`usp-ai-ba/README.md`](usp-ai-ba/README.md).

## Repository layout

```
usp-ai-ba/
  backend/
    codemind/           CodeMind's extraction/QA/orchestration/batch/watch modules
    pipeline/           StoryForge's LangGraph nodes
    api/routers/        FastAPI routes for both flows (codemind_jobs.py, codemind_ask.py, assess.py, ...)
  frontend/storyforge-ui/   Angular SPA (unified shell — both apps' pages live here)
deploy/
  nginx.conf           Gateway routing config (/, /api/*)
  gateway.Dockerfile    Builds the Angular app and serves it via nginx
docker-compose.yml      Wires storyforge-backend + gateway together
RUNNING.md              How to run the app standalone, the unified platform, and local-LLM setup
```

## Notes

- `ANTHROPIC_API_KEY` is required for CodeMind (Claude is the default agent there, and
  BATCH mode always uses it regardless of any Ollama setting). StoryForge AI's
  `clarify_node`/`generate_node` run entirely on a local Ollama model instead;
  `ANTHROPIC_API_KEY`/`CLAUDE_MODEL` are configured in `config.py` but not called
  anywhere in that pipeline. See [RUNNING.md](RUNNING.md) for the full breakdown.
- Ollama is expected to run on the host, not in a container.
