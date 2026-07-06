# Running the App

This repo hosts one app, StoryForge AI (Python/FastAPI + Angular): an SDD-to-
user-stories assessment pipeline, a unified code+manuals ingestion pipeline
(one ChromaDB corpus), and two standing Q&A pages (Ask Technical/Ask
Business) grounded in that same corpus.

## Authentication (read this first)

The FastAPI backend issues a JWT on `POST /auth/login` and every route checks
it. There's no user management UI or endpoint yet: the first time the
backend starts with no `users.json`, it seeds a default `admin` / `admin`
account (logged loudly to the console when it happens), and that's the only
account that exists until you add more. To add a real account (and/or retire
this one), the only way today is calling `user_store.create_user(username,
password, role)` directly (e.g. from a `python -c` one-liner run inside the
backend's venv, with `JOBS_DIR` pointed at the same directory the running
server uses) — don't expose this beyond local testing while `admin`/`admin`
is still the only login.

`JWT_SECRET` signs that token. Leave it unset for local dev and the backend
auto-generates and persists one under `JOBS_DIR/.jwt_secret`, so restarting
doesn't invalidate your login. Set it explicitly (e.g.
`export JWT_SECRET=$(openssl rand -hex 32)`) before exposing the app beyond
local testing, or when running via Docker Compose (see "Unified deployment"
below).

## Running locally

**Requirements:** Python 3.11+, Node.js 18+, Ollama running locally with
`nomic-embed-text` (embeddings) and `qwen2.5:14b` (StoryForge's
clarify/generate nodes, and optionally ingestion's LLM-summary enrichment
tier if you enable Ollama there — see below) pulled. `ANTHROPIC_API_KEY` is
required for ingestion's default (Claude) enrichment agent and Ask
Technical/Business (Claude is the default there too); StoryForge's own
assessment pipeline doesn't call Claude anywhere.

**Fastest path:** run `./dev-up.sh` from the repo root (after the one-time
setup below). Ctrl+C stops both processes.

### 1. Backend

```bash
cd usp-ai-ba/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in ANTHROPIC_API_KEY if you want ingestion's LLM-summary enrichment
# tier and Ask Technical/Business (Claude is the default agent for both).
# StoryForge's own SDD-to-stories pipeline doesn't need it -- see the note
# in .env.example.

ollama pull nomic-embed-text
ollama pull qwen2.5:14b
ollama serve                # if not already running

uvicorn api.main:app --reload --port 8000
```

> ⚠️ `backend/.env.example` currently has a real-looking `NOTION_API_KEY` value checked in — rotate that token and treat it as compromised; don't reuse it.

### 2. Frontend (separate terminal)

```bash
cd usp-ai-ba/frontend/storyforge-ui
npm install
npm start                   # ng serve -> http://localhost:4200
```

Open **http://localhost:4200**. The landing page has four cards:

- **AI Business Analyst** — turns an SDD PDF into an Epic → User Story →
  Dev/Unit-Test Task hierarchy (local Ollama model + RAG over ChromaDB),
  exported as `.docx`/ADO/Notion. Run the one-time ingestion below before
  your first assessment.
- **Ingestion** — index a code repository and/or user manuals into the
  shared corpus (`/ingestion`).
- **Ask Technical** / **Ask Business** — standing Q&A pages over that same
  corpus, for the dev team and business team respectively.

### 3. One-time ingestion (before first assessment)

```bash
curl -X POST http://localhost:8000/api/ingest/pdfs -H "Content-Type: application/json" \
  -d '{"folder_path": "/path/to/user-manuals"}'

curl -X POST http://localhost:8000/api/ingest/code -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/monorepo"}'
```

---

## Ingestion/Ask configuration notes

Ingestion's enrichment agent and Ask's model are both configurable from the
**Settings** page in the Angular shell, or via environment variables
documented in `.env.example`. A few things worth calling out:

- Ingestion's LLM-summary enrichment defaults to Claude only. Set
  `INGEST_OLLAMA_ENABLED=true` (or the equivalent Settings toggle) to add
  Ollama as a second agent — work is then split **round-robin** between the
  two, not primary/fallback.
- Ask Technical/Business's model is controlled separately via `ASK_QA_MODEL`
  (`claude` or `ollama`).
- All RAG retrieval (ingestion + StoryForge's own pipeline + Ask) uses one
  shared embedding model (`nomic-embed-text` by default, via
  `OLLAMA_EMBED_MODEL`) — there's no per-feature embedding toggle.

## Ollama server tuning (matters more now that one process drives all load)

Since StoryForge's own RAG/generation and ingestion's enrichment fan-out
share one Ollama server, a few server-level env vars (set before `ollama
serve`) are worth tuning for throughput:

- `OLLAMA_NUM_PARALLEL` — concurrent requests per loaded model. Ingestion's
  per-run `max_concurrency` (defaults to 8) should stay at or below
  `OLLAMA_NUM_PARALLEL × (number of active agents)` to avoid requests
  queuing up on the Ollama side.
- `OLLAMA_MAX_LOADED_MODELS` — keep both the generation model (`qwen2.5:14b`)
  and the embedding model (`nomic-embed-text`) resident at once, if you use
  both, to avoid reload latency swapping between them.
- `OLLAMA_KEEP_ALIVE` — how long an idle model stays loaded; raise it if you
  see reload latency between bursts of activity.
- `OLLAMA_MAX_QUEUE` — caps how many requests queue once `OLLAMA_NUM_PARALLEL`
  is saturated, rather than piling up unbounded.
- GPU layer offload and quantization level are configured per-model (see
  Ollama's own docs) — worth revisiting if extraction throughput is CPU-bound.

---

## Unified deployment (Docker Compose)

`storyforge-ui`'s landing page ("/") has the same cards described above. In
production this is served through a single nginx gateway in front of one
backend container:

- `/` → Angular shell (all pages)
- `/api/*` → the FastAPI backend

Run everything with Docker Compose from the repo root:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export JWT_SECRET=$(openssl rand -hex 32)   # see "Authentication" above
docker compose up --build
```

Open **http://localhost/** — pick a card to enter a flow. Only the
`gateway` service publishes a host port (`80`); `storyforge-backend` is
reachable only inside the compose network.

Ollama is expected to run on the host, not in a container. It's used by
StoryForge's `clarify_node`/`generate_node` (`qwen2.5:14b`) and by
ingestion's optional Ollama enrichment agent (Claude by default) — make sure
both `nomic-embed-text` and `qwen2.5:14b` are pulled if you enable the
Ollama enrichment agent. `OLLAMA_BASE_URL` defaults to
`http://host.docker.internal:11434`, mapped via `extra_hosts: host-gateway`
in `docker-compose.yml` so it resolves on Linux too — make sure `ollama
serve` is running on the host before starting the compose stack.

Data persistence: ChromaDB, the job registries, uploads/exports, and
ingestion's enrichment manifests are all written under `/data` inside the
container, backed by the `storyforge-data` named Docker volume — it
survives `docker compose down` (but not `docker compose down -v`).
