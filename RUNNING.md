# Running the Apps

This repo has two independent apps, plus a unified "umbrella" deployment that
serves both under one origin behind an nginx gateway (see the last section).
For separate local dev, each app has its own section below.

## Authentication (read this first)

Both apps sit behind a shared login: StoryForge's FastAPI backend issues a JWT
on `POST /auth/login`, and CodeMind only ever verifies that same token — it
never issues its own. That means **both backends must be started with the
exact same `JWT_SECRET` value**, or CodeMind will reject every request (either
with `{"error":"Server has no JWT_SECRET configured"}` if it's unset entirely,
or "Invalid or expired token" if the two values don't match).

- **Fastest path for standalone dev:** run `./dev-up.sh` from the repo root
  (after the one-time `venv`/`npm install` setup in the sections below). It
  generates one shared secret (persisted to `.dev-jwt-secret`, gitignored, so
  restarting doesn't invalidate your login), and starts StoryForge's backend,
  CodeMind, and the Angular shell together with it. Ctrl+C stops all three.
- **Manual standalone dev:** export the same `JWT_SECRET` value in every
  terminal before starting either backend, e.g.
  `export JWT_SECRET=$(openssl rand -hex 32)` in one shell, then start each
  app from a terminal that inherits it (or pass it inline:
  `JWT_SECRET=$JWT_SECRET uvicorn ...` / `JWT_SECRET=$JWT_SECRET ./mvnw spring-boot:run`).
  Leaving it unset for StoryForge alone "works" in the sense that it
  auto-generates and persists its own random one (`backend/jobs/.jwt_secret`)
  — but CodeMind has no equivalent fallback, so that alone won't fix SSO.
- **Docker Compose:** see the "Unified deployment" section below —
  `docker-compose.yml` passes `JWT_SECRET` through to both containers from
  your shell's environment.

There's no user management UI or endpoint yet: the first time StoryForge's
backend starts with no `users.json`, it seeds a default `admin` / `admin`
account (logged loudly to the console when it happens), and that's the only
account that exists until you add more. To add a real account (and/or retire
this one), the only way today is calling `user_store.create_user(username,
password, role)` directly (e.g. from a `python -c` one-liner run inside the
backend's venv, with `JOBS_DIR` pointed at the same directory the running
server uses) — don't expose either app beyond local testing while `admin`/
`admin` is still the only login.

## code-mind-app (CodeMind — Java/Spring Boot)

Scans a repo and extracts business logic; browse results / ask questions in a web UI.

**Requirements:** Java 17+, `ANTHROPIC_API_KEY`, Ollama running locally with `qwen2.5:14b` pulled.

Run with the local `qwen2.5:14b` model handling extraction and Q&A, and Claude kept
configured as the backup:

```bash
cd code-mind-app
ollama pull qwen2.5:14b
ollama serve                              # if not already running

export ANTHROPIC_API_KEY=sk-ant-...       # still required — see note below
export JSPROCESSOR_OLLAMA_ENABLED=true
export OLLAMA_MODEL=qwen2.5:14b           # already the default
export JSPROCESSOR_QA_MODEL=ollama
export JSPROCESSOR_QA_OLLAMA_MODEL=qwen2.5:14b   # already the default

./mvnw spring-boot:run
```

> **Note on "Claude as backup":** CodeMind doesn't have a "try local first, fall back to
> Claude on failure" mode. With `JSPROCESSOR_OLLAMA_ENABLED=true`, extraction work is
> split between Claude and Ollama via **round-robin** (each handles roughly half the
> files), not primary/fallback. `ANTHROPIC_API_KEY` stays required regardless of the
> settings above, because **BATCH mode always uses the Anthropic Batches API** (no
> Ollama equivalent — don't set `JSPROCESSOR_EXECUTION_MODE=BATCH` if you want files to
> stay on the local model), and both Q&A and vector search silently fall back to
> keyword search / whatever's reachable if Ollama is down. Vector search embeddings use
> a separate embedding model (`nomic-embed-text`, not `qwen2.5:14b`) — add
> `JSPROCESSOR_EMBEDDING_ENABLED=true` and `ollama pull nomic-embed-text` if you also
> want real vector search instead of the keyword-overlap fallback.

Open **http://localhost:8085/ui/jobs**, enter a repository path, click **Start Extraction**.

Run tests: `./mvnw test`

---

## usp-ai-ba (StoryForge AI — Python/FastAPI + Angular)

Turns an SDD PDF into an Epic → User Story → Dev/Unit-Test Task hierarchy (local Ollama
model + RAG over ChromaDB), exported as `.docx`/ADO/Notion.

**Requirements:** Python 3.11+, Node.js 18+, Ollama running locally with both
`nomic-embed-text` (embeddings) and `qwen2.5:14b` (clarify/generate) pulled.

> **Correction from an earlier version of this doc:** StoryForge's `clarify_node` and
> `generate_node` (the analysis/generation phases) already run entirely on
> `OLLAMA_LLM_MODEL` (default `qwen2.5:14b`) via `ChatOllama` — this was true from the
> very first commit in this repo, it's not something that changed recently. Claude is
> **not** used anywhere in this pipeline; `ANTHROPIC_API_KEY`/`CLAUDE_MODEL` are
> configured in `config.py` but never called. Both nodes now run at `temperature=0` with
> a fixed seed, so the same SDD produces the same clarifications and the same generated
> stories on every run, and both retry (with a nudged seed) on transient Ollama failures
> or malformed JSON before giving up — see `backend/pipeline/nodes/llm_retry.py`.

### 1. Backend

```bash
cd usp-ai-ba/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # ANTHROPIC_API_KEY isn't actually used by this pipeline
                             # today (see note above) — no need to fill it in to run

ollama pull nomic-embed-text
ollama pull qwen2.5:14b
ollama serve                # if not already running

uvicorn api.main:app --reload --port 8000
```

### 2. Frontend (separate terminal)

```bash
cd usp-ai-ba/frontend/storyforge-ui
npm install
npm start                   # ng serve -> http://localhost:4200
```

### 3. One-time ingestion (before first assessment)

```bash
curl -X POST http://localhost:8000/api/ingest/pdfs -H "Content-Type: application/json" \
  -d '{"folder_path": "/path/to/user-manuals"}'

curl -X POST http://localhost:8000/api/ingest/code -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/monorepo"}'
```

Then open **http://localhost:4200** and submit an SDD PDF via the **Assess** page.

> ⚠️ `backend/.env.example` currently has a real-looking `NOTION_API_KEY` value checked in — rotate that token and treat it as compromised; don't reuse it.

---

## Unified deployment (both apps under one origin)

`storyforge-ui` now includes a landing page ("/") with two cards — "CodeMind" and "AI
Business Analyst" — that route to each app. The CodeMind card leads to a page that embeds
CodeMind's existing UI in an iframe. In production this is served through a single nginx
gateway so both apps share one origin:

- `/` → Angular shell landing page + all StoryForge pages (`/ai-ba`, `/assess`, etc.)
- `/api/*` → StoryForge FastAPI backend
- `/codemind-app/*` → CodeMind (Spring Boot, running with `server.servlet.context-path=/codemind-app`)

Run everything with Docker Compose from the repo root:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export JWT_SECRET=$(openssl rand -hex 32)   # shared by both containers -- see "Authentication" above
docker compose up --build
```

Open **http://localhost/** — pick a card to enter the AI Business Analyst flow or
CodeMind's job UI. Only the `gateway` service publishes a host port (`80`); `codemind`
and `storyforge-backend` are reachable only inside the compose network.

Ollama is expected to run on the host, not in a container. It's used by both apps for
embeddings, and by StoryForge for `clarify_node`/`generate_node` too (`qwen2.5:14b`) —
make sure both `nomic-embed-text` and `qwen2.5:14b` are pulled. `OLLAMA_BASE_URL`
defaults to `http://host.docker.internal:11434`, mapped via `extra_hosts: host-gateway`
in `docker-compose.yml` so it resolves on Linux too — make sure `ollama serve` is
running on the host before starting the compose stack.

> `docker-compose.yml`'s `codemind` service doesn't currently pass through
> `JSPROCESSOR_OLLAMA_ENABLED`/`OLLAMA_MODEL`/`JSPROCESSOR_QA_MODEL`/`JSPROCESSOR_QA_OLLAMA_MODEL`
> (see the CodeMind section above) — only `ANTHROPIC_API_KEY` and `OLLAMA_BASE_URL` are
> wired into the container's environment today. The `qwen2.5:14b` configuration above
> only takes effect when running CodeMind standalone (`./mvnw spring-boot:run`); add
> those entries to the `codemind` service's `environment:` block yourself if you want
> the same behavior through `docker compose up`.

Data persistence: CodeMind's job store/output and StoryForge's ChromaDB/jobs/uploads/exports
are all backed by named Docker volumes (`codemind-jobs`, `codemind-output`, `storyforge-data`),
so they survive `docker compose down` (but not `docker compose down -v`).
