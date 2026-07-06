# USP AI Apps

This repository hosts **StoryForge AI**, a single Python/FastAPI + Angular app covering
three AI-powered workflows behind one login, one origin, and one shared knowledge base:

| | Ask Technical / Ask Business | AI Business Analyst (StoryForge) |
|---|---|---|
| Purpose | Standing Q&A over an ingested code repo + manuals, for dev and business audiences respectively | Forward-generates a full Azure DevOps work item hierarchy from a design doc |
| Model | Claude by default (+ optional local Ollama, per `ASK_QA_MODEL`) | Local Ollama model (`qwen2.5:14b`, deterministic — `temperature=0` + fixed seed) for analysis/generation and RAG embeddings |
| Landing page card | "Ask Technical Questions" / "Ask Business Questions" | "AI Business Analyst" |

All three flows share one FastAPI backend process, one ingestion pipeline (one ChromaDB
corpus behind Ask and StoryForge's own RAG retrieval), one job-persistence layer, one
settings screen, and one error-monitoring feed.

## Quick start (unified platform)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

Open **http://localhost/** — pick a card from the landing page. See
[RUNNING.md](RUNNING.md) for standalone run instructions, local-LLM configuration,
and troubleshooting notes.

## Architecture

```
                          ┌─────────────────────────────────────────┐
                          │        Angular shell (storyforge-ui)     │
                          │  landing cards: [AI BA] [Ingestion]      │
                          │            [Ask Technical] [Ask Business]│
                          └───────────────────┬───────────────────┘
                                              │
                        nginx gateway (deploy/nginx.conf), one origin
                                              │
                                     `/` (Angular build)
                                     SPA fallback
                                              │
                                          `/api/*`
                          StoryForge FastAPI backend (LangGraph pipeline,
                          ingestion pipeline, ChromaDB, Ask endpoints)
```

See [`docker-compose.yml`](docker-compose.yml) and
[`deploy/nginx.conf`](deploy/nginx.conf) for the exact wiring.

## Ingestion + Ask Technical / Ask Business

A code repository and its user manuals get indexed once into a shared ChromaDB corpus
(mechanical structural chunking across 16 languages, plus an optional per-file
LLM-summary enrichment tier). Two standing pages then query that same corpus:

- **Ask Technical** — for the development team; cites the full relative path of every
  source file an answer draws from.
- **Ask Business** — for the business team; plain-language capability/impact framing,
  no file paths or code identifiers.

Both stream answers back over SSE, using Claude by default (`ASK_QA_MODEL`).

**Key REST endpoints:** `POST /api/ingest/documents`, `POST /api/ingest/code`,
`GET /api/ingest/status/{job_id}`, `POST /api/ask/technical`, `POST /api/ask/business`,
`GET /api/ask/status`.
**UI routes:** `/ingestion`, `/ask/technical`, `/ask/business`.

## StoryForge AI

Turns a Solution Design Document (SDD) PDF into a fully-detailed, ready-to-create Azure
DevOps work item hierarchy — **Epic → User Story → Dev Tasks + Unit Test Tasks** — using
a local Ollama model (`qwen2.5:14b`) for analysis/generation, RAG over the same ingested
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
`GET /api/export/document/{job_id}`.
**Frontend routes:** `/ai-ba` (dashboard), `/assess`, `/clarify/:jobId`, `/review/:jobId`, `/status/:jobId`.

Full details, configuration reference, and the complete API/schema documentation:
[`usp-ai-ba/README.md`](usp-ai-ba/README.md).

## Repository layout

```
usp-ai-ba/
  backend/
    ingestion/           Code/PDF chunking + embedding, optional LLM-summary enrichment tier
    pipeline/           StoryForge's LangGraph nodes
    api/routers/        FastAPI routes (ask.py, ingest.py, assess.py, ...)
  frontend/storyforge-ui/   Angular SPA (unified shell)
deploy/
  nginx.conf           Gateway routing config (/, /api/*)
  gateway.Dockerfile    Builds the Angular app and serves it via nginx
docker-compose.yml      Wires storyforge-backend + gateway together
RUNNING.md              How to run the app standalone, the unified platform, and local-LLM setup
```

## Notes

- `ANTHROPIC_API_KEY` is required for ingestion's default (Claude) LLM-summary enrichment
  agent and for Ask Technical/Business's default model. StoryForge AI's
  `clarify_node`/`generate_node` run entirely on a local Ollama model instead;
  `ANTHROPIC_API_KEY`/`CLAUDE_MODEL` are configured in `config.py` but not called
  anywhere in that pipeline. See [RUNNING.md](RUNNING.md) for the full breakdown.
- Ollama is expected to run on the host, not in a container.
