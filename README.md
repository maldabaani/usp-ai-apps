# USP AI Apps

This repository hosts two independent AI-powered developer tools, served together as a
single **unified platform** — one Angular shell with a tab bar, one nginx gateway, one
origin. Each app also works completely standalone.

| | [CodeMind](code-mind-app/) | [StoryForge AI](usp-ai-ba/) |
|---|---|---|
| Purpose | Reverse-engineers business logic out of an existing codebase | Forward-generates a full Azure DevOps work item hierarchy from a design doc |
| Stack | Java 17, Spring Boot 3.5.0, Spring AI, Thymeleaf | Python/FastAPI + LangGraph backend, Angular 17+ SPA frontend |
| Model | Claude (+ optional local Ollama agent) | Claude (analysis/generation) + Ollama (RAG embeddings only) |
| Standalone port | `8085` | backend `8000` / frontend `4200` |
| Tab in unified shell | "CodeMind" (embedded via iframe) | "AI BA" (native) |

## Quick start (unified platform)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

Open **http://localhost/** — the "AI BA" tab is the StoryForge flow, the "CodeMind" tab
embeds CodeMind's job UI, both served from one origin. See [RUNNING.md](RUNNING.md) for
standalone run instructions per app, local-LLM configuration, and troubleshooting notes.

## Architecture

```
                          ┌─────────────────────────────────────────┐
                          │        Angular shell (storyforge-ui)     │
                          │   tab bar:  [ AI BA ]   [ CodeMind ]     │
                          └───────────────────┬───────────────────┘
                                              │
                        nginx gateway (deploy/nginx.conf), one origin
                                              │
              ┌───────────────────────────────┼───────────────────────────────┐
              │                               │                               │
        `/` (Angular build)              `/api/*`                    `/codemind-app/*`
        SPA fallback                StoryForge FastAPI backend      CodeMind (Spring Boot,
                                     (LangGraph pipeline, ChromaDB)  context-path=/codemind-app)
```

Both apps keep their own REST APIs, data stores, and business logic — the gateway only
adds routing. See [`docker-compose.yml`](docker-compose.yml) and
[`deploy/nginx.conf`](deploy/nginx.conf) for the exact wiring.

## CodeMind

Scans a repository, sends each source file to Claude (or a local Ollama model) with a
structured prompt, and stores the extracted rules, summaries, and dependencies as JSON.
A built-in web UI lets you watch jobs run in real time, browse extracted results, and
ask natural-language questions about the code once extraction is complete.

**Highlights**
- Multi-language extraction — JS/TS, Python, Java, Kotlin, Go, C#, Ruby, Rust, PHP
- Two execution modes: **SYNC** (per-file, low latency) or **BATCH** (Anthropic Message
  Batches API, flat 50% token discount, built for large repos)
- Hybrid agent setup — Claude plus an optional local Ollama model, round-robin load balanced
- Large-file chunking, incremental re-runs (manifest-tracked), per-file fault isolation
- Ask Agent (per-job) and Ask All (cross-job) natural-language Q&A over extracted results,
  with vector search (Ollama embeddings) falling back to keyword search
- Directory watcher — drop a path into a watched folder and a job starts automatically
- Live progress UI: stepper, stats, real-time file feed, viewer modal, cancel/export

**Tech stack:** Spring Boot 3.5.0 · Spring AI 1.1.7 · Anthropic Java SDK · Thymeleaf · Maven · Java 17

**Key REST endpoints:** `POST /api/v1/extraction-jobs`, `GET /api/v1/extraction-jobs/{id}`,
`POST .../cancel`, `GET .../export`, `POST .../qa/stream`, `POST /api/v1/ask/stream`.
**UI routes:** `/ui/jobs`, `/ui/jobs/{id}`, `/ui/jobs/{id}/ask`, `/ui/ask`.

Full details, configuration reference, and the complete API/REST table:
[`code-mind-app/README.md`](code-mind-app/README.md) ·
[`code-mind-app/CLAUDE.md`](code-mind-app/CLAUDE.md) (developer reference).

## StoryForge AI

Turns a Solution Design Document (SDD) PDF into a fully-detailed, ready-to-create Azure
DevOps work item hierarchy — **Epic → User Story → Dev Tasks + Unit Test Tasks** — using
Claude for analysis/generation, RAG over your codebase/user manuals/JPA entities
(ChromaDB + Ollama embeddings), and a human-in-the-loop review/clarification workflow
before anything is written downstream.

**Pipeline** (LangGraph state machine, checkpointed per job): `analyze → clarify →
generate → review → export_document | create_ado | create_notion`. The graph interrupts
before generation and before the export step so a human can answer clarifying questions
or edit/approve stories; any node failure routes straight to `END` instead of letting
downstream nodes run on incomplete state.

**Output modes** (`OUTPUT_MODE`): `document` (default, `.docx` via python-docx), `ado`
(Azure DevOps via a local Node MCP server), `notion` (one Epic page per story via
`notion-client`).

**Tech stack:** Python/FastAPI · LangGraph · Angular 17+ standalone components · ChromaDB · Ollama (embeddings)

**Key REST endpoints:** `POST /api/assess`, `GET /api/assess/status/{job_id}`,
`POST /api/clarify/answer/{job_id}`, `POST /api/review/approve/{job_id}`,
`GET /api/export/document/{job_id}`, `POST /api/ingest/pdfs`, `POST /api/ingest/code`.
**Frontend routes:** `/` (dashboard), `/assess`, `/clarify/:jobId`, `/review/:jobId`, `/status/:jobId`.

Full details, configuration reference, and the complete API/schema documentation:
[`usp-ai-ba/README.md`](usp-ai-ba/README.md).

## Repository layout

```
code-mind-app/        CodeMind (Java/Spring Boot)
usp-ai-ba/
  backend/            StoryForge FastAPI + LangGraph pipeline
  frontend/storyforge-ui/   Angular SPA (unified shell lives here — tab bar + both apps' pages)
deploy/
  nginx.conf           Gateway routing config (/, /api/*, /codemind-app/*)
  gateway.Dockerfile    Builds the Angular app and serves it via nginx
docker-compose.yml      Wires codemind + storyforge-backend + gateway together
RUNNING.md              How to run each app standalone, the unified platform, and local-LLM setup
```

## Notes

- Both apps require `ANTHROPIC_API_KEY`; Claude is not optional in either today (see
  [RUNNING.md](RUNNING.md) for exactly which phases can currently offload to a local
  Ollama model, and which can't).
- Ollama is expected to run on the host, not in a container, for both apps.
