# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend (`usp-ai-ba/backend/`)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in ANTHROPIC_API_KEY etc.

uvicorn api.main:app --reload --port 8000    # dev server

pytest -q                                     # full suite
pytest tests/test_ask_router.py -q            # one file
pytest tests/test_ask_router.py::test_ask_technical_returns_sse_sources_and_chunks -q  # one test
```

No test makes a real call to Anthropic, Ollama, or ChromaDB beyond a scratch path — every LLM/embedding client is hand-mocked. When adding a test that touches one, copy the existing fake-client shape rather than reaching for a mocking framework (e.g. `tests/test_ask_router.py`'s `_FakeChat`, `tests/ingestion/enrichment/test_enrich.py`'s `_FakeVectorStore`).

### Frontend (`usp-ai-ba/frontend/storyforge-ui/`)

```bash
npm install
npm start                              # ng serve, http://localhost:4200
ng build --configuration=production    # the standard "did this break anything" check
```

There is no Karma/Jasmine unit test suite here — frontend verification is a clean production build plus manually exercising the changed page in a browser (or Playwright), not `ng test`.

### Both at once

`./dev-up.sh` from the repo root starts backend + frontend together (fails fast if `venv`/`node_modules` aren't set up yet). Ctrl+C stops both.

### Local runtime requirement

Ollama running locally (`ollama serve`) with `nomic-embed-text` (all embeddings) and `qwen2.5:14b` (StoryForge's `clarify_node`/`generate_node`) pulled. `ANTHROPIC_API_KEY` is needed for ingestion's optional LLM-summary enrichment tier and for Ask Technical/Business's default model — StoryForge's own assessment pipeline never calls Claude.

## Architecture

One FastAPI backend (`usp-ai-ba/backend/`) + one Angular 17+ standalone-component SPA (`usp-ai-ba/frontend/storyforge-ui/`), covering three workflows behind one login:

1. **Ingestion** (`ingestion/`) indexes a code repo + user manual PDFs into three persistent ChromaDB collections (`sf_codebase`, `sf_jpa_entities`, `sf_user_manuals`). Two tiers: mechanical structural chunking (always runs — per-language chunkers for Java/TS/JS, whole-file fallback for 13 more languages) and an optional per-file LLM-summary enrichment tier (`ingestion/enrichment/`, Claude/Ollama agents gated by `ANTHROPIC_API_KEY`/`INGEST_OLLAMA_ENABLED`). Re-ingesting is idempotent, not additive: deterministic sha256-derived chunk IDs mean unchanged content upserts to the same ID, and `ingestion/chroma_client.py`'s `delete_by_source*` helpers purge a file's stale chunks before re-adding. Enrichment's own incremental-skip manifest (`ingestion/enrichment/manifest.py`) only ever records a file's content hash after a *genuinely successful* summarization (or an already-known-good skip) — never on a failed attempt — so a transient failure (e.g. exhausted API credits) gets retried on the next run instead of being silently treated as done forever (see `enrich.py`'s `process_one()`).

2. **Ask Technical / Ask Business** (`api/routers/ask.py`, `prompts/ask_prompts.py`) are two standing SSE-streamed Q&A endpoints querying that same corpus via `ingestion/retrieval.py`'s `retrieve_all_collections()` — shared with StoryForge's own `analyze_node` below, so there's exactly one retrieval implementation, not two. The two Ask endpoints differ only in system-prompt framing (Technical cites full file paths; Business explicitly doesn't), never in retrieval scope. SSE contract: one `event: sources` frame (JSON array of source paths) then `event: chunk` frames (JSON-encoded string) per streamed piece of the answer — this exact shape is relied on by `frontend/.../services/sse.util.ts` and must not change without updating both sides.

3. **StoryForge assessment** (`pipeline/`) turns an uploaded SDD PDF into an Epic → User Story → Dev/Unit-Test-Task hierarchy via a LangGraph `StateGraph` (`pipeline/graph.py`): `analyze → clarify → generate → review → (export_document | create_ado | create_notion)`, with the output branch chosen by each *job's own* `output_mode` (stamped onto the job at submission time, not read live from the global settings default). The graph is checkpointed to SQLite (`JOBS_DIR/checkpoints.sqlite`, keyed by `job_id`) and **interrupts** before `generate_node` and before whichever output node is reachable; `pipeline/runner.py`'s `_drive` loop then either auto-resumes immediately (no ambiguity found / `review_mode` off) or leaves the job genuinely paused for human input. Every node-to-node edge is conditional on `status`: a node that sets `status == "error"` routes straight to `END`, so a failure can never be silently masked by a downstream node running against incomplete state.

### Settings hot-reload

`config.py`'s `Settings` is a plain singleton, mutated in place by `PUT /api/settings` (`Settings.apply_updates()`, which also writes to `.env` via `config_store.py` and bumps `settings_generation`). Any module that builds an LLM/embeddings client needs to check `settings_generation` before reusing a cached client, or a live settings-screen change silently has no effect until a process restart — see the established pattern in `pipeline/nodes/generate.py::_get_llm()`, `ingestion/chroma_client.py::get_embeddings()`, or `api/routers/ask.py::_get_ask_chat()` before adding a new LLM-backed feature.

### Job registries

Three independent registries share the same shape (in-memory dict + JSON-file persistence, reloaded at startup) but are separate implementations, not shared code: `api/job_registry.py` (StoryForge assessment jobs), and `api/ingest_jobs.py` + `ingestion/ingest_job_registry.py` (ingestion jobs, cancellable via `ingestion/runner.py`'s asyncio-Task-tracking `run_tracked`/`cancel_job`). Don't assume a fix in one applies to the other.

### Auth

Every route except `/auth/login` and `/health` requires a JWT (`api/deps.py`'s `require_auth`/`require_admin` FastAPI dependencies), read from either the `Authorization: Bearer` header or a `?token=` query param (for contexts that can't set a header, e.g. a direct download link). A default `admin`/`admin` account is seeded on first run if `users.json` doesn't exist yet (`api/user_store.py::ensure_default_admin()`) — change it before exposing the app beyond local testing.
